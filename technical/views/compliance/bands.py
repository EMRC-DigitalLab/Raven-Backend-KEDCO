# technical/views/compliance/bands.py
"""
GET /api/technical/compliance/bands/
GET /api/technical/compliance/bands/?voltage_level=11kv
GET /api/technical/compliance/bands/?state=KN
GET /api/technical/compliance/bands/?district=KN-IDU

GET /api/technical/compliance/bands/<slug>/
GET /api/technical/compliance/bands/a/?state=KN
GET /api/technical/compliance/bands/a/?district=KN-IDU&voltage_level=11kv

Bands list  → each band: compliance summary (total feeders, compliant, at_risk, critical, upgrade_eligible)
Single band → same summary + full feeder list with per-feeder compliance (ordered A→E within band = all same band, so ordered by name)

Filters: mode, year, month, from_date, voltage_level, state, district
"""
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from common.models import Band, Feeder
from technical.utils.compliance_utils import (
    BAND_NEXT_LEVEL_HOURS,
    BAND_ORDER,
    BAND_TARGET_HOURS,
    _cap_to_today,
    _date_range,
    aggregate_compliance,
    bulk_daily_hours,
    build_period_object,
    compute_compliance,
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


def _get_voltage(request):
    v = request.GET.get('voltage_level') or request.GET.get('feeder_type')
    return v if v in ('11kv', '33kv') else None


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


@api_view(['GET'])
def compliance_bands_list(request):
    """
    All bands — each with compliance summary.
    Ordered A → E always.
    """
    try:
        from_date, to_date, mode = _parse_request_dates(request)
    except (ValueError, TypeError) as e:
        return Response({'error': str(e)}, status=400)

    to_date = _cap_to_today(to_date)
    period_dates = _date_range(from_date, to_date)
    voltage_level = _get_voltage(request)
    state_slug = request.GET.get('state')
    district_name = request.GET.get('district')

    feeders_qs = (
        Feeder.objects
        .filter(is_onboarded=True)
        .select_related('band', 'business_district__state')
        .exclude(band__isnull=True)
    )
    if voltage_level:
        feeders_qs = feeders_qs.filter(voltage_level=voltage_level)
    if district_name:
        feeders_qs = feeders_qs.filter(business_district__name__iexact=district_name)
    elif state_slug:
        feeders_qs = feeders_qs.filter(business_district__state__slug__iexact=state_slug)

    feeders = list(feeders_qs)

    # Group feeders by band
    band_feeders: dict = {}
    band_objs: dict = {}
    for feeder in feeders:
        slug = feeder.band.slug
        if slug not in band_feeders:
            band_feeders[slug] = []
            band_objs[slug] = feeder.band
        band_feeders[slug].append(feeder)

    if not band_feeders:
        return Response({
            'period': build_period_object(from_date, to_date, mode),
            'filters': {'voltage_level': voltage_level or 'all', 'state': state_slug, 'district': district_name},
            'count': 0,
            'bands': [],
        })

    daily_map = bulk_daily_hours([f.id for f in feeders], from_date, to_date)

    bands_out = []
    for slug in sorted(band_feeders, key=lambda s: BAND_ORDER.get(s, 99)):
        band = band_objs[slug]
        b_feeders = band_feeders[slug]
        summary, _ = aggregate_compliance(b_feeders, daily_map, period_dates)
        bands_out.append({
            'band': {
                'slug': band.slug,
                'name': band.name,
                'target_hours_per_day': float(band.target_hours_per_day),
                'upgrade_target_hours': BAND_NEXT_LEVEL_HOURS.get(slug),
            },
            'summary': summary,
        })

    return Response({
        'period': build_period_object(from_date, to_date, mode),
        'filters': {'voltage_level': voltage_level or 'all', 'state': state_slug, 'district': district_name},
        'count': len(bands_out),
        'bands': bands_out,
    })


@api_view(['GET'])
def compliance_band_detail(request, slug):
    """
    Single band — compliance summary + every feeder in that band with its compliance.
    Feeder list ordered alphabetically (all same band so no band-ordering needed).
    Supports: state, district, voltage_level filters to scope which feeders appear.
    """
    band = get_object_or_404(Band, slug__iexact=slug)

    try:
        from_date, to_date, mode = _parse_request_dates(request)
    except (ValueError, TypeError) as e:
        return Response({'error': str(e)}, status=400)

    to_date = _cap_to_today(to_date)
    period_dates = _date_range(from_date, to_date)
    voltage_level = _get_voltage(request)
    state_slug = request.GET.get('state')
    district_name = request.GET.get('district')

    feeders_qs = (
        Feeder.objects
        .filter(is_onboarded=True, band=band)
        .select_related('band', 'business_district__state')
    )
    if voltage_level:
        feeders_qs = feeders_qs.filter(voltage_level=voltage_level)
    if district_name:
        feeders_qs = feeders_qs.filter(business_district__name__iexact=district_name)
    elif state_slug:
        feeders_qs = feeders_qs.filter(business_district__state__slug__iexact=state_slug)

    feeders = list(feeders_qs.order_by('name'))

    target = BAND_TARGET_HOURS.get(slug, float(band.target_hours_per_day))
    next_level = BAND_NEXT_LEVEL_HOURS.get(slug)

    if not feeders:
        empty_summary = {'total_feeders': 0, 'compliant': 0, 'at_risk': 0, 'critical': 0, 'upgrade_eligible': 0}
        return Response({
            'period': build_period_object(from_date, to_date, mode),
            'filters': {'voltage_level': voltage_level or 'all', 'state': state_slug, 'district': district_name},
            'band': {
                'slug': band.slug,
                'name': band.name,
                'target_hours_per_day': float(band.target_hours_per_day),
                'upgrade_target_hours': next_level,
            },
            'summary': empty_summary,
            'feeders': [],
        })

    daily_map = bulk_daily_hours([f.id for f in feeders], from_date, to_date)
    summary, _ = aggregate_compliance(feeders, daily_map, period_dates)

    feeders_out = []
    for feeder in feeders:
        c = compute_compliance(daily_map.get(feeder.id, {}), slug, target, period_dates)
        feeders_out.append({
            'feeder': _feeder_info(feeder),
            'compliance': c,
        })

    return Response({
        'period': build_period_object(from_date, to_date, mode),
        'filters': {'voltage_level': voltage_level or 'all', 'state': state_slug, 'district': district_name},
        'band': {
            'slug': band.slug,
            'name': band.name,
            'target_hours_per_day': float(band.target_hours_per_day),
            'upgrade_target_hours': next_level,
        },
        'summary': summary,
        'feeders': feeders_out,
    })
