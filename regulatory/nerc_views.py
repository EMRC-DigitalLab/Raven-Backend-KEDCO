"""
regulatory/nerc_views.py

Computed NERC regulatory metrics — derived live from Raven DB.

GET /api/regulatory/nerc/capping-estimated-bills/
GET /api/regulatory/nerc/revenue-recovery/
GET /api/regulatory/nerc/api-feeder-streaming/

All endpoints support the same period + state params as every other commercial view:
  ?mode=monthly&year=2026&month=7
  ?mode=daily&from_date=2026-07-01
  ?state=<slug>

Response envelope per metric:
  { value, delta, history: [{period, value}], target, unit }
"""

from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from django.db.models import Avg, Count, Max, Q, Sum

from rest_framework.decorators import api_view
from rest_framework.response import Response

from commercial.analytics_utils import parse_date_range
from commercial.models import MeterReading, TariffRate
from common.models import Feeder
from technical.models import EnergyDelivered


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _prev_periods(date_range, count=3):
    """Return `count` periods immediately before date_range, oldest first."""
    mode  = date_range['mode']
    start = date_range['start_date']
    periods = []
    for i in range(count, 0, -1):
        if mode == 'daily':
            s = start - timedelta(days=i)
            e = s
            label = s.strftime('%d %b')
        elif mode == 'weekly':
            s = start - timedelta(weeks=i)
            e = s + timedelta(days=6)
            label = f"{s.strftime('%d %b')} – {e.strftime('%d %b')}"
        elif mode == 'yearly':
            s = date(start.year - i, 1, 1)
            e = date(start.year - i, 12, 31)
            label = str(start.year - i)
        else:
            ref = start - relativedelta(months=i)
            s   = date(ref.year, ref.month, 1)
            e   = s + relativedelta(months=1) - timedelta(days=1)
            label = s.strftime('%b %Y')
        periods.append({
            'start_date': s, 'end_date': e,
            'label': label, 'days': (e - s).days + 1,
        })
    return periods


def _metric(value, delta=None, history=None, target=None, unit=None):
    return {'value': value, 'delta': delta, 'history': history or [], 'target': target, 'unit': unit}


def _delta(current, previous):
    if previous is None or previous == 0:
        return None
    try:
        return round((current - previous) / abs(previous) * 100, 2)
    except Exception:
        return None


def _history(period_data, key):
    return [{'period': label, 'value': d[key]} for label, d in period_data]


def _base_readings_qs(p_start, p_end, state_slug):
    qs = MeterReading.objects.filter(
        customer__feeder__commercial_is_onboarded=True,
        reading_date__gte=p_start,
        reading_date__lte=p_end,
        billed_consumption__isnull=False,
    )
    if state_slug:
        qs = qs.filter(customer__feeder__business_district__state__slug=state_slug)
    return qs


# ── 1. Capping of Estimated Bills ─────────────────────────────────────────────

def _capping_for_period(p_start, p_end, state_slug):
    r_qs = _base_readings_qs(p_start, p_end, state_slug)

    agg = r_qs.aggregate(
        total_kwh=Sum('billed_consumption'),
        estimated_kwh=Sum('billed_consumption', filter=~Q(estimation_method='')),
        total_count=Count('id'),
        estimated_count=Count('id', filter=~Q(estimation_method='')),
    )

    total_count      = agg['total_count'] or 0
    estimated_count  = agg['estimated_count'] or 0
    actual_count     = total_count - estimated_count
    total_kwh        = float(agg['total_kwh'] or 0)
    estimated_kwh    = float(agg['estimated_kwh'] or 0)

    billing_eff      = round(actual_count / total_count * 100, 2) if total_count else 0
    gross_overbilled = round(estimated_kwh / total_kwh * 100, 2) if total_kwh else 0

    total_read  = r_qs.values('customer_id').distinct().count()
    actual_read = r_qs.filter(estimation_method='').values('customer_id').distinct().count()
    within_cap  = round(actual_read / total_read * 100, 2) if total_read else 0

    return {
        'estimated_billing_efficiency': billing_eff,
        'customers_billed_within_cap':  within_cap,
        'gross_energy_overbilled':      gross_overbilled,
    }


@api_view(['GET'])
def capping_estimated_bills(request):
    date_range = parse_date_range(request)
    state_slug = request.GET.get('state', '').strip() or None

    all_periods = _prev_periods(date_range, count=3) + [date_range]
    period_data = [
        (p['label'], _capping_for_period(p['start_date'], p['end_date'], state_slug))
        for p in all_periods
    ]

    current  = period_data[-1][1]
    previous = period_data[-2][1] if len(period_data) >= 2 else None
    prev_val = lambda key: previous[key] if previous else None

    return Response({
        'period': {
            'mode': date_range['mode'], 'start_date': str(date_range['start_date']),
            'end_date': str(date_range['end_date']), 'label': date_range['label'],
            'days': date_range['days'],
        },
        'estimated_billing_efficiency': _metric(
            current['estimated_billing_efficiency'],
            delta=_delta(current['estimated_billing_efficiency'], prev_val('estimated_billing_efficiency')),
            history=_history(period_data, 'estimated_billing_efficiency'),
            unit='%',
        ),
        'customers_billed_within_cap': _metric(
            current['customers_billed_within_cap'],
            delta=_delta(current['customers_billed_within_cap'], prev_val('customers_billed_within_cap')),
            history=_history(period_data, 'customers_billed_within_cap'),
            target=100, unit='%',
        ),
        'gross_energy_overbilled': _metric(
            current['gross_energy_overbilled'],
            delta=_delta(current['gross_energy_overbilled'], prev_val('gross_energy_overbilled')),
            history=_history(period_data, 'gross_energy_overbilled'),
            target=0, unit='%',
        ),
    })


# ── 2. Revenue Recovery ────────────────────────────────────────────────────────

def _allowed_tariff_for_period(p_start):
    """Most recent active TariffRate on or before period start. Falls back to None."""
    obj = (
        TariffRate.objects
        .filter(is_active=True, effective_from__lte=p_start)
        .order_by('-effective_from')
        .first()
    )
    return float(obj.rate_per_kwh) if obj else None


def _revenue_recovery_for_period(p_start, p_end, state_slug):
    r_qs = _base_readings_qs(p_start, p_end, state_slug).filter(
        tariff_rate__isnull=False,
        tariff_rate__gt=0,
        estimation_method='',   # actual readings only
    )

    allowed_tariff = _allowed_tariff_for_period(p_start)
    if allowed_tariff is None:
        avg = r_qs.aggregate(avg=Avg('tariff_rate'))['avg']
        allowed_tariff = round(float(avg), 4) if avg else 0

    avg_billed_rate = r_qs.aggregate(avg=Avg('tariff_rate'))['avg']
    revenue_recovered = round(float(avg_billed_rate), 4) if avg_billed_rate else 0

    recovery_rate = round(revenue_recovered / allowed_tariff * 100, 2) if allowed_tariff else 0

    return {
        'allowed_tariff':        round(allowed_tariff, 4),
        'revenue_recovered':     revenue_recovered,
        'revenue_recovery_rate': recovery_rate,
    }


@api_view(['GET'])
def revenue_recovery(request):
    date_range = parse_date_range(request)
    state_slug = request.GET.get('state', '').strip() or None

    all_periods = _prev_periods(date_range, count=3) + [date_range]
    period_data = [
        (p['label'], _revenue_recovery_for_period(p['start_date'], p['end_date'], state_slug))
        for p in all_periods
    ]

    current  = period_data[-1][1]
    previous = period_data[-2][1] if len(period_data) >= 2 else None
    prev_val = lambda key: previous[key] if previous else None

    return Response({
        'period': {
            'mode': date_range['mode'], 'start_date': str(date_range['start_date']),
            'end_date': str(date_range['end_date']), 'label': date_range['label'],
            'days': date_range['days'],
        },
        'allowed_tariff': _metric(
            current['allowed_tariff'],
            delta=_delta(current['allowed_tariff'], prev_val('allowed_tariff')),
            history=_history(period_data, 'allowed_tariff'),
            unit='₦/kWh',
        ),
        'revenue_recovered': _metric(
            current['revenue_recovered'],
            delta=_delta(current['revenue_recovered'], prev_val('revenue_recovered')),
            history=_history(period_data, 'revenue_recovered'),
            unit='₦/kWh',
        ),
        'revenue_recovery_rate': _metric(
            current['revenue_recovery_rate'],
            delta=_delta(current['revenue_recovery_rate'], prev_val('revenue_recovery_rate')),
            history=_history(period_data, 'revenue_recovery_rate'),
            target=100, unit='%',
        ),
    })


# ── 3. API Feeder Streaming Rate ───────────────────────────────────────────────

_BALLOON = 500.0  # same outlier threshold as bulk_analytics


def _streaming_for_period(p_start, p_end, state_slug):
    feeders_qs = Feeder.objects.filter(is_onboarded=True)
    if state_slug:
        feeders_qs = feeders_qs.filter(business_district__state__slug=state_slug)

    feeder_ids = list(feeders_qs.values_list('id', flat=True))
    if not feeder_ids:
        return {'total_metered': 0, 'total_unmetered': 0, 'streaming_rate': 0}

    metered_ids = set(
        EnergyDelivered.objects
        .filter(feeder_id__in=feeder_ids, date__gte=p_start, date__lte=p_end)
        .values('feeder_id')
        .annotate(max_daily=Max('energy_mwh'))
        .filter(max_daily__gt=0, max_daily__lte=_BALLOON)
        .values_list('feeder_id', flat=True)
    )

    total           = len(feeder_ids)
    total_metered   = len(metered_ids)
    total_unmetered = total - total_metered
    streaming_rate  = round(total_metered / total * 100, 2) if total else 0

    return {
        'total_metered':   total_metered,
        'total_unmetered': total_unmetered,
        'streaming_rate':  streaming_rate,
    }


@api_view(['GET'])
def api_feeder_streaming(request):
    date_range = parse_date_range(request)
    state_slug = request.GET.get('state', '').strip() or None

    all_periods = _prev_periods(date_range, count=3) + [date_range]
    period_data = [
        (p['label'], _streaming_for_period(p['start_date'], p['end_date'], state_slug))
        for p in all_periods
    ]

    current  = period_data[-1][1]
    previous = period_data[-2][1] if len(period_data) >= 2 else None
    prev_val = lambda key: previous[key] if previous else None

    return Response({
        'period': {
            'mode': date_range['mode'], 'start_date': str(date_range['start_date']),
            'end_date': str(date_range['end_date']), 'label': date_range['label'],
            'days': date_range['days'],
        },
        'total_metered': _metric(
            current['total_metered'],
            delta=_delta(current['total_metered'], prev_val('total_metered')),
            history=_history(period_data, 'total_metered'),
        ),
        'total_unmetered': _metric(
            current['total_unmetered'],
            delta=_delta(current['total_unmetered'], prev_val('total_unmetered')),
            history=_history(period_data, 'total_unmetered'),
        ),
        'streaming_rate': _metric(
            current['streaming_rate'],
            delta=_delta(current['streaming_rate'], prev_val('streaming_rate')),
            history=_history(period_data, 'streaming_rate'),
            target=100, unit='%',
        ),
    })
