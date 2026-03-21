# technical/views/compliance/districts.py
"""
GET /api/technical/compliance/districts/
GET /api/technical/compliance/districts/?state=KN
GET /api/technical/compliance/districts/?state=KN&voltage_level=11kv

GET /api/technical/compliance/districts/<slug>/
GET /api/technical/compliance/districts/KN-IDU/?mode=monthly&year=2026&month=1

Each district returns summary + by_band, same shape as overview/states.
"""
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from common.models import BusinessDistrict, Feeder
from technical.utils.compliance_utils import (
    _cap_to_today,
    _date_range,
    aggregate_compliance,
    bulk_daily_hours,
    build_period_object,
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


def _district_info(district):
    return {
        'slug': district.slug,
        'name': district.name,
        'state': {'slug': district.state.slug, 'name': district.state.name},
    }


@api_view(['GET'])
def compliance_districts_list(request):
    try:
        from_date, to_date, mode = _parse_request_dates(request)
    except (ValueError, TypeError) as e:
        return Response({'error': str(e)}, status=400)

    to_date = _cap_to_today(to_date)
    period_dates = _date_range(from_date, to_date)
    voltage_level = _get_voltage(request)
    state_slug = request.GET.get('state')

    feeders_qs = (
        Feeder.objects
        .filter(is_onboarded=True)
        .select_related('band', 'business_district__state')
        .exclude(band__isnull=True)
        .exclude(business_district__isnull=True)
    )
    if voltage_level:
        feeders_qs = feeders_qs.filter(voltage_level=voltage_level)
    if state_slug:
        feeders_qs = feeders_qs.filter(business_district__state__slug__iexact=state_slug)

    feeders = list(feeders_qs)

    # Group by district
    district_feeders: dict = {}
    district_objs: dict = {}
    for feeder in feeders:
        district = feeder.business_district
        did = district.id
        if did not in district_feeders:
            district_feeders[did] = []
            district_objs[did] = district
        district_feeders[did].append(feeder)

    if not district_feeders:
        return Response({
            'period': build_period_object(from_date, to_date, mode),
            'filters': {'voltage_level': voltage_level or 'all', 'state': state_slug},
            'count': 0,
            'districts': [],
        })

    daily_map = bulk_daily_hours([f.id for f in feeders], from_date, to_date)

    districts_out = []
    for did in sorted(district_objs, key=lambda d: district_objs[d].name):
        district = district_objs[did]
        d_feeders = district_feeders[did]
        summary, by_band = aggregate_compliance(d_feeders, daily_map, period_dates)
        districts_out.append({
            'district': _district_info(district),
            'summary': summary,
            'by_band': by_band,
        })

    return Response({
        'period': build_period_object(from_date, to_date, mode),
        'filters': {'voltage_level': voltage_level or 'all', 'state': state_slug},
        'count': len(districts_out),
        'districts': districts_out,
    })


@api_view(['GET'])
def compliance_district_detail(request, slug):
    district = get_object_or_404(
        BusinessDistrict.objects.select_related('state'),
        slug__iexact=slug,
    )

    try:
        from_date, to_date, mode = _parse_request_dates(request)
    except (ValueError, TypeError) as e:
        return Response({'error': str(e)}, status=400)

    to_date = _cap_to_today(to_date)
    period_dates = _date_range(from_date, to_date)
    voltage_level = _get_voltage(request)

    feeders_qs = (
        Feeder.objects
        .filter(is_onboarded=True, business_district=district)
        .select_related('band', 'business_district__state')
        .exclude(band__isnull=True)
    )
    if voltage_level:
        feeders_qs = feeders_qs.filter(voltage_level=voltage_level)

    feeders = list(feeders_qs)
    if not feeders:
        summary = {'total_feeders': 0, 'compliant': 0, 'at_risk': 0, 'critical': 0, 'upgrade_eligible': 0}
        return Response({
            'period': build_period_object(from_date, to_date, mode),
            'filters': {'voltage_level': voltage_level or 'all'},
            'district': _district_info(district),
            'summary': summary,
            'by_band': [],
        })

    daily_map = bulk_daily_hours([f.id for f in feeders], from_date, to_date)
    summary, by_band = aggregate_compliance(feeders, daily_map, period_dates)

    return Response({
        'period': build_period_object(from_date, to_date, mode),
        'filters': {'voltage_level': voltage_level or 'all'},
        'district': _district_info(district),
        'summary': summary,
        'by_band': by_band,
    })
