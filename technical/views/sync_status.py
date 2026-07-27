"""
technical/views/sync_status.py

API endpoint: unified data source health for the Technical module.

GET /api/technical/sync-status/

Covers ALL data pipelines feeding the technical module:
  - DataNest sources: HourlyLoad, Meter Readings, Interruptions
  - Google Sheet sources: 33KV Load Flow, 11KV Load Flow (+ energy accounting when wired)

Requires authentication.
"""

from datetime import date, timedelta

from django.db import connections
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.models import Feeder
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

    # ── Google Sheet sources ──────────────────────────────────────────────────
    today = now.date()
    sheet_feeds = [
        ('33kv_load_flow',         '33KV Load Flow',         '33kv'),
        ('11kv_load_flow',         '11KV Load Flow',         '11kv'),
        ('33kv_energy_accounting', '33KV Energy Accounting', '33kv'),
        ('11kv_energy_accounting', '11KV Energy Accounting', '11kv'),
    ]
    sources['google_sheets'] = []
    for feed_type, label, voltage in sheet_feeds:
        try:
            entry = _google_sheet_entry(feed_type, label, voltage, today)
            sources['google_sheets'].append(entry)
            statuses.append(entry['status'])
        except Exception as exc:
            entry = _error_entry(label, str(exc))
            entry['source'] = 'google_sheet'
            entry['feed_type'] = feed_type
            sources['google_sheets'].append(entry)
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


def _google_sheet_entry(feed_type: str, label: str, voltage: str, today: date) -> dict:
    """
    Builds a sync health entry for a Google Sheet feed.
    Compares expected feeder coverage vs what's actually in HourlyLoad for the current month.
    """
    from tmo.models import GoogleSheetFeed

    year, month = today.year, today.month

    feed = GoogleSheetFeed.objects.filter(
        feed_type=feed_type, year=year, month=month, is_active=True
    ).first()

    # Expected feeder count for this voltage level
    expected_feeders = Feeder.objects.filter(voltage_level=voltage).count()
    # Threshold for "complete" day (70% of expected)
    complete_threshold = max(1, int(expected_feeders * 0.70))

    # Days elapsed so far this month (up to and including today)
    days_elapsed = today.day

    # Per-day breakdown
    day_rows = []
    complete_days = 0
    partial_days  = 0
    missing_days  = 0

    # Aggregate feeder counts per date for this month
    from django.db.models import Count
    daily_counts = {
        row['date']: row['feeder_count']
        for row in HourlyLoad.objects
            .filter(
                date__year=year, date__month=month,
                date__lte=today,
                feeder__voltage_level=voltage,
                submission_type='admin_override',
            )
            .values('date')
            .annotate(feeder_count=Count('feeder_id', distinct=True))
    }

    for day in range(1, days_elapsed + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            break
        feeders = daily_counts.get(d, 0)
        if feeders >= complete_threshold:
            day_status = 'complete'
            complete_days += 1
        elif feeders > 0:
            day_status = 'partial'
            partial_days += 1
        else:
            day_status = 'missing'
            missing_days += 1

        day_rows.append({
            'date':    d.isoformat(),
            'status':  day_status,
            'feeders': feeders,
            'expected_feeders': expected_feeders,
        })

    # Overall status for this feed
    if not feed:
        status = 'no_feed'
    elif missing_days > days_elapsed * 0.5:
        status = 'out_of_sync'
    elif partial_days > 0 or missing_days > 0:
        status = 'partial'
    else:
        status = 'synced'

    return {
        'source':            'google_sheet',
        'feed_type':         feed_type,
        'label':             label,
        'status':            status,
        'feed_registered':   feed is not None,
        'last_synced_at':    feed.last_synced_at.isoformat() if feed and feed.last_synced_at else None,
        'last_sync_log':     feed.last_sync_log if feed else None,
        'year':              year,
        'month':             month,
        'expected_feeders':  expected_feeders,
        'days_elapsed':      days_elapsed,
        'complete_days':     complete_days,
        'partial_days':      partial_days,
        'missing_days':      missing_days,
        'days':              day_rows,
    }
