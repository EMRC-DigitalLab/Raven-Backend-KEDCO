"""
technical/views/sync_status.py

API endpoint: DataNest ↔ Raven data reconciliation for the Technical module.

GET /api/technical/sync-status/

For each data type, compares actual row counts between DataNest and Raven
for the same date window to tell you if they are truly in sync.
Requires authentication.
"""

from datetime import timedelta

from django.db import connections
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from technical.models import (
    CumulativeMeterReading,
    DataSyncLog,
    FeederInterruption,
    HourlyLoad,
)

# Reconciliation window — compare last N days of data
RECONCILE_DAYS = 7


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def technical_sync_status(request):
    """
    Returns DataNest vs Raven reconciliation for all three Technical data types.

    Response shape:
    {
      "overall_status": "synced" | "partial" | "out_of_sync" | "error",
      "reconciliation_window_days": 7,
      "last_checked": "<ISO>",
      "sources": {
        "hourly_load":    { datanest_count, raven_count, diff, status, ... },
        "meter_readings": { ... },
        "interruptions":  { ... },
      }
    }
    """
    now = timezone.now()
    window_start = (now - timedelta(days=RECONCILE_DAYS)).date()
    window_end = now.date()

    sources = {}
    statuses = []

    # ── Hourly Load ───────────────────────────────────────────────────────────
    try:
        dn_hourly = _datanest_count(
            "SELECT COUNT(*) FROM Technicalhourlydata WHERE Date >= %s AND Date <= %s",
            [window_start, window_end]
        )
        rv_hourly = HourlyLoad.objects.filter(
            date__gte=window_start, date__lte=window_end
        ).count()
        sources['hourly_load'] = _reconcile_entry(
            label='Hourly Load',
            datanest_count=dn_hourly,
            raven_count=rv_hourly,
            data_type='technical_hourly_load',
            window_start=window_start,
            window_end=window_end,
        )
        statuses.append(sources['hourly_load']['status'])
    except Exception as exc:
        sources['hourly_load'] = _error_entry('Hourly Load', str(exc))
        statuses.append('error')

    # ── Meter Readings ────────────────────────────────────────────────────────
    try:
        dn_readings = _datanest_count(
            "SELECT COUNT(*) FROM techicalenergyreadingdailydta WHERE Date >= %s AND Date <= %s",
            [window_start, window_end]
        )
        rv_readings = CumulativeMeterReading.objects.filter(
            reading_date__gte=window_start, reading_date__lte=window_end
        ).count()
        sources['meter_readings'] = _reconcile_entry(
            label='Meter Readings',
            datanest_count=dn_readings,
            raven_count=rv_readings,
            data_type='technical_meter_readings',
            window_start=window_start,
            window_end=window_end,
        )
        statuses.append(sources['meter_readings']['status'])
    except Exception as exc:
        sources['meter_readings'] = _error_entry('Meter Readings', str(exc))
        statuses.append('error')

    # ── Interruptions ─────────────────────────────────────────────────────────
    try:
        dn_faults = _datanest_count(
            "SELECT COUNT(*) FROM technicalenergyfault WHERE time_of_occurrence >= %s AND time_of_occurrence <= %s",
            [window_start, window_end]
        )
        rv_faults = FeederInterruption.objects.filter(
            occurred_at__date__gte=window_start,
            occurred_at__date__lte=window_end,
        ).count()
        sources['interruptions'] = _reconcile_entry(
            label='Interruptions / Faults',
            datanest_count=dn_faults,
            raven_count=rv_faults,
            data_type='technical_interruptions',
            window_start=window_start,
            window_end=window_end,
        )
        statuses.append(sources['interruptions']['status'])
    except Exception as exc:
        sources['interruptions'] = _error_entry('Interruptions / Faults', str(exc))
        statuses.append('error')

    # ── Overall ───────────────────────────────────────────────────────────────
    if 'error' in statuses:
        overall = 'error'
    elif 'out_of_sync' in statuses:
        overall = 'out_of_sync'
    elif 'partial' in statuses:
        overall = 'partial'
    else:
        overall = 'synced'

    return Response({
        'overall_status': overall,
        'reconciliation_window_days': RECONCILE_DAYS,
        'window_start': window_start,
        'window_end': window_end,
        'last_checked': now,
        'module': 'technical',
        'sources': sources,
    })


# ── helpers ───────────────────────────────────────────────────────────────────

def _datanest_count(sql: str, params: list) -> int:
    conn = connections['external']
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return row[0] if row else 0


def _reconcile_entry(label, datanest_count, raven_count, data_type,
                     window_start, window_end) -> dict:
    diff = datanest_count - raven_count
    pct_synced = round((raven_count / datanest_count * 100), 1) if datanest_count > 0 else 100.0

    if diff == 0:
        status = 'synced'
    elif abs(diff) <= max(5, datanest_count * 0.01):
        # Within 1% or 5 records — acceptable lag, partial
        status = 'partial'
    else:
        status = 'out_of_sync'

    # Last sync metadata
    last_log = (
        DataSyncLog.objects
        .filter(data_type=data_type)
        .order_by('-started_at')
        .first()
    )

    return {
        'label': label,
        'status': status,
        'datanest_count': datanest_count,
        'raven_count': raven_count,
        'diff': diff,                         # positive = DataNest has more (not yet in Raven)
        'pct_synced': pct_synced,
        'window_start': window_start,
        'window_end': window_end,
        'last_sync_at': last_log.started_at if last_log else None,
        'last_sync_status': last_log.status if last_log else None,
        'last_sync_created': last_log.records_created if last_log else 0,
        'last_sync_updated': last_log.records_updated if last_log else 0,
        'last_sync_deleted': last_log.records_deleted if last_log else 0,
        'last_sync_error': last_log.error_message or None if last_log else None,
    }


def _error_entry(label: str, error: str) -> dict:
    return {
        'label': label,
        'status': 'error',
        'datanest_count': None,
        'raven_count': None,
        'diff': None,
        'pct_synced': None,
        'error': error,
    }
