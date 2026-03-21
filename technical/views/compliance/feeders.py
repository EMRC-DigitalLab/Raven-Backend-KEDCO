# technical/views/compliance/feeders.py
"""
GET /api/technical/compliance/feeders/
GET /api/technical/compliance/feeders/?band=a
GET /api/technical/compliance/feeders/?band=a&state=KN&voltage_level=11kv

GET /api/technical/compliance/feeders/<slug>/
  → single feeder: compliance summary + day-by-day breakdown

Feeders are always returned ordered Band A → B → C → D → E,
then alphabetically within each band.

Filters (all optional):
  band           — a | b | c | d | e
  voltage_level  — 11kv | 33kv   (also accepts feeder_type for consistency)
  state          — state slug e.g. KN
  district       — business district name
  mode / year / month / from_date / to_date  — period
"""
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from common.models import Feeder
from technical.utils.compliance_utils import (
    BAND_NEXT_LEVEL_HOURS,
    BAND_ORDER,
    BAND_TARGET_HOURS,
    _cap_to_today,
    _date_range,
    bulk_daily_hours,
    build_period_object,
    compute_compliance,
    compute_daily_breakdown,
)


def _parse_request_dates(request):
    mode = request.GET.get('mode', 'monthly')
    today = datetime.now().date()

    if mode == 'yearly':
        year = int(request.GET.get('year', today.year))
        return datetime(year, 1, 1).date(), datetime(year, 12, 31).date(), 'yearly'

    if mode in ('daily', 'weekly', 'custom', 'range'):
        from_str = request.GET.get('from_date')
        to_str = request.GET.get('to_date', from_str)
        if not from_str:
            raise ValueError('from_date is required for this mode')
        from_date = datetime.strptime(from_str[:10], '%Y-%m-%d').date()
        to_date = datetime.strptime(to_str[:10], '%Y-%m-%d').date()
        return from_date, to_date, mode

    year = int(request.GET.get('year', today.year))
    month = int(request.GET.get('month', today.month))
    from_date = datetime(year, month, 1).date()
    to_date = (datetime(year, month, 1) + relativedelta(months=1) - timedelta(days=1)).date()
    return from_date, to_date, 'monthly'


def _feeder_info(feeder):
    district = feeder.business_district
    state = district.state if district else None
    return {
        'slug': feeder.slug,
        'name': feeder.name,
        'voltage_level': feeder.voltage_level,
        'district': {'slug': district.slug, 'name': district.name} if district else None,
        'state': {'slug': state.slug, 'name': state.name} if state else None,
    }


def _band_info(band):
    return {
        'slug': band.slug,
        'name': band.name,
        'target_hours_per_day': float(band.target_hours_per_day),
    }


@api_view(['GET'])
def compliance_feeders_list(request):
    try:
        from_date, to_date, mode = _parse_request_dates(request)
    except (ValueError, TypeError) as e:
        return Response({'error': str(e)}, status=400)

    to_date = _cap_to_today(to_date)
    period_dates = _date_range(from_date, to_date)

    # ── Filters ──────────────────────────────────────────────────────────────
    band_slug = request.GET.get('band', '').lower() or None
    voltage_level = request.GET.get('voltage_level') or request.GET.get('feeder_type')
    if voltage_level and voltage_level not in ('11kv', '33kv'):
        voltage_level = None
    state = request.GET.get('state')
    district = request.GET.get('district')

    feeders_qs = (
        Feeder.objects
        .filter(is_onboarded=True)
        .select_related('band', 'business_district__state')
        .exclude(band__isnull=True)
    )
    if band_slug:
        feeders_qs = feeders_qs.filter(band__slug__iexact=band_slug)
    if voltage_level:
        feeders_qs = feeders_qs.filter(voltage_level=voltage_level)
    if district:
        feeders_qs = feeders_qs.filter(business_district__name__iexact=district)
    elif state:
        feeders_qs = feeders_qs.filter(business_district__state__slug__iexact=state)

    feeders = list(feeders_qs)
    if not feeders:
        return Response({
            'period': build_period_object(from_date, to_date, mode),
            'filters': {
                'band': band_slug or 'all',
                'voltage_level': voltage_level or 'all',
                'state': state,
                'district': district,
            },
            'count': 0,
            'feeders': [],
        })

    # ── Bulk supply hours (1 query) ───────────────────────────────────────────
    feeder_ids = [f.id for f in feeders]
    daily_map = bulk_daily_hours(feeder_ids, from_date, to_date)

    # ── Build per-feeder compliance + sort A → E then by name ────────────────
    rows = []
    for feeder in feeders:
        slug = feeder.band.slug
        target = BAND_TARGET_HOURS.get(slug, float(feeder.band.target_hours_per_day))
        c = compute_compliance(daily_map.get(feeder.id, {}), slug, target, period_dates)

        rows.append({
            '_band_order': BAND_ORDER.get(slug, 99),
            '_name': feeder.name,
            'feeder': _feeder_info(feeder),
            'band': _band_info(feeder.band),
            'compliance': c,
        })

    rows.sort(key=lambda r: (r['_band_order'], r['_name']))

    # Strip internal sort keys
    feeders_out = [{k: v for k, v in r.items() if not k.startswith('_')} for r in rows]

    return Response({
        'period': build_period_object(from_date, to_date, mode),
        'filters': {
            'band': band_slug or 'all',
            'voltage_level': voltage_level or 'all',
            'state': state,
            'district': district,
        },
        'count': len(feeders_out),
        'feeders': feeders_out,
    })


@api_view(['GET'])
def compliance_feeder_detail(request, slug):
    """
    Single feeder compliance detail — summary + day-by-day breakdown.
    """
    feeder = get_object_or_404(
        Feeder.objects.select_related('band', 'business_district__state'),
        slug=slug,
        is_onboarded=True,
    )

    if not feeder.band:
        return Response({'error': 'This feeder has no band assigned.'}, status=400)

    try:
        from_date, to_date, mode = _parse_request_dates(request)
    except (ValueError, TypeError) as e:
        return Response({'error': str(e)}, status=400)

    to_date = _cap_to_today(to_date)
    period_dates = _date_range(from_date, to_date)

    band_slug = feeder.band.slug
    target = BAND_TARGET_HOURS.get(band_slug, float(feeder.band.target_hours_per_day))
    next_level = BAND_NEXT_LEVEL_HOURS.get(band_slug)

    # Single feeder daily hours
    daily_map = bulk_daily_hours([feeder.id], from_date, to_date)
    feeder_daily = daily_map.get(feeder.id, {})

    compliance = compute_compliance(feeder_daily, band_slug, target, period_dates)
    daily_breakdown = compute_daily_breakdown(feeder_daily, band_slug, target, next_level, period_dates)

    return Response({
        'period': build_period_object(from_date, to_date, mode),
        'feeder': _feeder_info(feeder),
        'band': _band_info(feeder.band),
        'compliance': compliance,
        'daily': daily_breakdown,
    })
