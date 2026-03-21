# technical/views/compliance/overview.py
"""
GET /api/technical/compliance/overview/

Returns total + per-band compliance summary.
Filters: mode, year, month, from_date, voltage_level (11kv|33kv), state, district
"""
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from rest_framework.decorators import api_view
from rest_framework.response import Response

from common.models import Feeder
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


def _empty_response(from_date, to_date, mode, voltage_level, state, district):
    return {
        'period': build_period_object(from_date, to_date, mode),
        'filters': {'voltage_level': voltage_level or 'all', 'state': state, 'district': district},
        'summary': {'total_feeders': 0, 'compliant': 0, 'at_risk': 0, 'critical': 0, 'upgrade_eligible': 0},
        'by_band': [],
    }


@api_view(['GET'])
def compliance_overview(request):
    try:
        from_date, to_date, mode = _parse_request_dates(request)
    except (ValueError, TypeError) as e:
        return Response({'error': str(e)}, status=400)

    to_date = _cap_to_today(to_date)
    period_dates = _date_range(from_date, to_date)

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
    if voltage_level:
        feeders_qs = feeders_qs.filter(voltage_level=voltage_level)
    if district:
        feeders_qs = feeders_qs.filter(business_district__name__iexact=district)
    elif state:
        feeders_qs = feeders_qs.filter(business_district__state__slug__iexact=state)

    feeders = list(feeders_qs)
    if not feeders:
        return Response(_empty_response(from_date, to_date, mode, voltage_level, state, district))

    daily_map = bulk_daily_hours([f.id for f in feeders], from_date, to_date)
    summary, by_band = aggregate_compliance(feeders, daily_map, period_dates)

    return Response({
        'period': build_period_object(from_date, to_date, mode),
        'filters': {'voltage_level': voltage_level or 'all', 'state': state, 'district': district},
        'summary': summary,
        'by_band': by_band,
    })
