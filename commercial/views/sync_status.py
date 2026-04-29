"""
commercial/views/sync_status.py

GET /api/commercial/sync-status/

Reconciles DataNest vs Raven for all four Commercial data types.
Mirrors the pattern used in technical/views/sync_status.py.

Response shape:
{
  "overall_status": "synced" | "partial" | "out_of_sync" | "error",
  "module": "commercial",
  "reconciliation_window_days": 30,
  "window_start": "...",
  "window_end": "...",
  "last_checked": "...",
  "sources": {
    "meter_readings": { datanest_count, raven_count, diff, status, ... },
    "customers":      { ... },
    "managers":       { ... },
    "tariff_rates":   { ... },
  }
}
"""

from datetime import timedelta

from django.db import connections
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from commercial.models import CommercialCustomer, MeterManager, MeterReading, TariffRate
from technical.models import DataSyncLog

READING_WINDOW_DAYS = 30   # readings are weekly — 30 days = ~4 cycles


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def commercial_sync_status(request):
    now = timezone.now()
    window_start = (now - timedelta(days=READING_WINDOW_DAYS)).date()
    window_end = now.date()

    sources = {}
    statuses = []

    # ── Meter Readings ────────────────────────────────────────────────────────
    try:
        dn_readings = _datanest_count(
            "SELECT COUNT(*) FROM meter_readings WHERE created_at >= %s AND created_at <= %s",
            [window_start, window_end],
        )
        rv_readings = MeterReading.objects.filter(
            reading_date__gte=window_start,
            reading_date__lte=window_end,
        ).count()
        sources['meter_readings'] = _reconcile_entry(
            label='Meter Readings',
            datanest_count=dn_readings,
            raven_count=rv_readings,
            data_type='commercial_readings',
            window_start=window_start,
            window_end=window_end,
            note=f'Last {READING_WINDOW_DAYS} days — readings are submitted weekly.',
        )
        statuses.append(sources['meter_readings']['status'])
    except Exception as exc:
        sources['meter_readings'] = _error_entry('Meter Readings', str(exc))
        statuses.append('error')

    # ── Customers ─────────────────────────────────────────────────────────────
    try:
        dn_customers = _datanest_count("SELECT COUNT(*) FROM customer_information", [])
        rv_customers = CommercialCustomer.objects.count()
        sources['customers'] = _reconcile_entry(
            label='Customers',
            datanest_count=dn_customers,
            raven_count=rv_customers,
            data_type='commercial_customers',
            window_start=None,
            window_end=None,
        )
        statuses.append(sources['customers']['status'])
    except Exception as exc:
        sources['customers'] = _error_entry('Customers', str(exc))
        statuses.append('error')

    # ── Managers ──────────────────────────────────────────────────────────────
    try:
        dn_managers = _datanest_count("SELECT COUNT(*) FROM feeder_managers", [])
        rv_managers = MeterManager.objects.count()
        sources['managers'] = _reconcile_entry(
            label='Feeder Managers',
            datanest_count=dn_managers,
            raven_count=rv_managers,
            data_type='commercial_managers',
            window_start=None,
            window_end=None,
        )
        statuses.append(sources['managers']['status'])
    except Exception as exc:
        sources['managers'] = _error_entry('Feeder Managers', str(exc))
        statuses.append('error')

    # ── Tariff Rates ──────────────────────────────────────────────────────────
    try:
        dn_tariffs = _datanest_count(
            "SELECT COUNT(*) FROM tariff_rates WHERE customer_type IN ('MD1','Non-MD')", []
        )
        rv_tariffs = TariffRate.objects.count()
        sources['tariff_rates'] = _reconcile_entry(
            label='Tariff Rates',
            datanest_count=dn_tariffs,
            raven_count=rv_tariffs,
            data_type='commercial_tariff_rates',
            window_start=None,
            window_end=None,
            note='MD2 rates are intentionally excluded from Raven.',
        )
        statuses.append(sources['tariff_rates']['status'])
    except Exception as exc:
        sources['tariff_rates'] = _error_entry('Tariff Rates', str(exc))
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
        'module': 'commercial',
        'reconciliation_window_days': READING_WINDOW_DAYS,
        'window_start': window_start,
        'window_end': window_end,
        'last_checked': now,
        'sources': sources,
    })


# ── helpers ───────────────────────────────────────────────────────────────────

def _datanest_count(sql: str, params: list) -> int:
    with connections['external'].cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return row[0] if row else 0


def _reconcile_entry(label, datanest_count, raven_count, data_type,
                     window_start, window_end, note=None) -> dict:
    diff = datanest_count - raven_count
    pct_synced = (
        round(raven_count / datanest_count * 100, 1)
        if datanest_count > 0 else 100.0
    )

    if diff == 0:
        status = 'synced'
    elif abs(diff) <= max(5, datanest_count * 0.01):
        status = 'partial'
    else:
        status = 'out_of_sync'

    last_log = (
        DataSyncLog.objects
        .filter(data_type=data_type)
        .order_by('-started_at')
        .first()
    )

    entry = {
        'label': label,
        'status': status,
        'datanest_count': datanest_count,
        'raven_count': raven_count,
        'diff': diff,
        'pct_synced': pct_synced,
        'window_start': window_start,
        'window_end': window_end,
        'last_sync_at': last_log.started_at if last_log else None,
        'last_sync_status': last_log.status if last_log else None,
        'last_sync_created': last_log.records_created if last_log else 0,
        'last_sync_updated': last_log.records_updated if last_log else 0,
        'last_sync_errored': last_log.records_errored if last_log else 0,
        'last_sync_error': last_log.error_message or None if last_log else None,
    }
    if note:
        entry['note'] = note
    return entry


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
