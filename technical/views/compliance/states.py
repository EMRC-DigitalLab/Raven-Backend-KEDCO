# technical/views/compliance/states.py
"""
GET /api/technical/compliance/states/
GET /api/technical/compliance/states/?voltage_level=11kv

GET /api/technical/compliance/states/<slug>/
GET /api/technical/compliance/states/KN/?mode=monthly&year=2026&month=1

Each state returns the same summary + by_band shape as the overview,
scoped to feeders in that state.
"""
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from common.models import Feeder, State
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


def _build_state_block(state, feeders, daily_map, period_dates):
    summary, by_band = aggregate_compliance(feeders, daily_map, period_dates)
    return {
        'state': {'slug': state.slug, 'name': state.name},
        'summary': summary,
        'by_band': by_band,
    }


@api_view(['GET'])
def compliance_states_list(request):
    try:
        from_date, to_date, mode = _parse_request_dates(request)
    except (ValueError, TypeError) as e:
        return Response({'error': str(e)}, status=400)

    to_date = _cap_to_today(to_date)
    period_dates = _date_range(from_date, to_date)
    voltage_level = _get_voltage(request)

    feeders_qs = (
        Feeder.objects
        .filter(is_onboarded=True)
        .select_related('band', 'business_district__state')
        .exclude(band__isnull=True)
        .exclude(business_district__isnull=True)
    )
    if voltage_level:
        feeders_qs = feeders_qs.filter(voltage_level=voltage_level)

    feeders = list(feeders_qs)

    # Group feeders by state
    state_feeders: dict = {}
    state_objs: dict = {}
    for feeder in feeders:
        state = feeder.business_district.state
        sid = state.id
        if sid not in state_feeders:
            state_feeders[sid] = []
            state_objs[sid] = state
        state_feeders[sid].append(feeder)

    if not state_feeders:
        return Response({
            'period': build_period_object(from_date, to_date, mode),
            'filters': {'voltage_level': voltage_level or 'all'},
            'count': 0,
            'states': [],
        })

    all_feeder_ids = [f.id for f in feeders]
    daily_map = bulk_daily_hours(all_feeder_ids, from_date, to_date)

    states_out = []
    for sid in sorted(state_objs, key=lambda s: state_objs[s].name):
        state = state_objs[sid]
        s_feeders = state_feeders[sid]
        states_out.append(_build_state_block(state, s_feeders, daily_map, period_dates))

    return Response({
        'period': build_period_object(from_date, to_date, mode),
        'filters': {'voltage_level': voltage_level or 'all'},
        'count': len(states_out),
        'states': states_out,
    })


@api_view(['GET'])
def compliance_state_detail(request, slug):
    state = get_object_or_404(State, slug__iexact=slug)

    try:
        from_date, to_date, mode = _parse_request_dates(request)
    except (ValueError, TypeError) as e:
        return Response({'error': str(e)}, status=400)

    to_date = _cap_to_today(to_date)
    period_dates = _date_range(from_date, to_date)
    voltage_level = _get_voltage(request)

    feeders_qs = (
        Feeder.objects
        .filter(is_onboarded=True, business_district__state=state)
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
            'state': {'slug': state.slug, 'name': state.name},
            'summary': summary,
            'by_band': [],
        })

    daily_map = bulk_daily_hours([f.id for f in feeders], from_date, to_date)
    summary, by_band = aggregate_compliance(feeders, daily_map, period_dates)

    return Response({
        'period': build_period_object(from_date, to_date, mode),
        'filters': {'voltage_level': voltage_level or 'all'},
        'state': {'slug': state.slug, 'name': state.name},
        'summary': summary,
        'by_band': by_band,
    })
