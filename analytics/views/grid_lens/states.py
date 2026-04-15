"""
analytics/views/grid_lens/states.py

GET /api/analytics/grid-lens/states/
GET /api/analytics/grid-lens/states/<slug>/

GridLens loss decomposition at state level.

Two layers available at this scope:
  - EA Received  (EAMonthlyReturn, scoped to stations in the state)
  - Feeder Distributed  (EAFeederTechnicalEnergy, scoped to same stations)

Commercial billed / collected is not available at state level — see /grid-lens/.

Query params:
    year  : int  (default: current year)
    month : int  (default: current month)
"""

from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from analytics.permissions import HasGridLensAccess
from common.models import InjectionSubstation, State
from energy_account.utils import metric, parse_ea_period

from ._helpers import (
    build_two_layer_response,
    compute_losses,
    fetch_ea_layer,
    fetch_stream_b_layer,
)


def _station_ids_in_state(state):
    return list(
        InjectionSubstation.objects
        .filter(state=state)
        .values_list('id', flat=True)
    )


def _state_payload(state, month, year, period):
    station_ids = _station_ids_in_state(state)

    ea       = fetch_ea_layer(month, year, station_ids=station_ids)
    stream_b = fetch_stream_b_layer(month, year, station_ids=station_ids)
    losses   = compute_losses(ea['total_mwh'], stream_b['total_mwh'])

    layers = build_two_layer_response(ea, stream_b, losses, scope_label='state')

    # Station breakdown within this state
    station_breakdown = []
    for station in InjectionSubstation.objects.filter(state=state).order_by('name'):
        s_ea       = fetch_ea_layer(month, year, station_ids=[station.id])
        s_stream_b = fetch_stream_b_layer(month, year, station_ids=[station.id])
        if not s_ea['return_count']:
            continue
        s_losses = compute_losses(s_ea['total_mwh'], s_stream_b['total_mwh'])
        station_breakdown.append({
            'station': {'slug': station.slug, 'name': station.name},
            'ea_received_mwh':         metric(round(s_ea['total_mwh'], 4),       unit='MWh'),
            'feeder_distributed_mwh':  metric(round(s_stream_b['total_mwh'], 4), unit='MWh'),
            'metering_gap_mwh':        metric(s_losses['metering_gap_mwh'],      unit='MWh'),
            'metering_gap_pct':        metric(s_losses['metering_gap_pct'],       unit='%'),
            'transmission_efficiency': metric(s_losses['transmission_eff'],       unit='%'),
        })

    return {
        'period': {
            'year':  period['year'],
            'month': period['month'],
            'label': period['label'],
        },
        'state': {'slug': state.slug, 'name': state.name},
        **layers,
        'breakdown': {'by_station': station_breakdown},
    }


@api_view(['GET'])
@permission_classes([HasGridLensAccess])
def all_states(request):
    """
    GET /api/analytics/grid-lens/states/

    GridLens loss decomposition summary for every state.
    """
    period = parse_ea_period(request)
    month, year = period['month'], period['year']

    results = []
    for state in State.objects.order_by('name'):
        station_ids = _station_ids_in_state(state)
        if not station_ids:
            continue

        ea       = fetch_ea_layer(month, year, station_ids=station_ids)
        stream_b = fetch_stream_b_layer(month, year, station_ids=station_ids)

        if not ea['return_count']:
            continue

        losses = compute_losses(ea['total_mwh'], stream_b['total_mwh'])

        results.append({
            'state': {'slug': state.slug, 'name': state.name},
            'ea_received_mwh':         metric(round(ea['total_mwh'], 4),       unit='MWh'),
            'feeder_distributed_mwh':  metric(round(stream_b['total_mwh'], 4), unit='MWh'),
            'metering_gap_mwh':        metric(losses['metering_gap_mwh'],      unit='MWh'),
            'metering_gap_pct':        metric(losses['metering_gap_pct'],       unit='%'),
            'transmission_efficiency': metric(losses['transmission_eff'],       unit='%'),
            'return_count':            metric(ea['return_count']),
            'late_returns':            metric(ea['late_count']),
            'avg_stream_b_completeness': metric(stream_b['avg_completeness'],   unit='%'),
        })

    return Response({
        'period': {'year': year, 'month': month, 'label': period['label']},
        'module': 'GridLens',
        'states': results,
    })


@api_view(['GET'])
@permission_classes([HasGridLensAccess])
def single_state(request, slug):
    """
    GET /api/analytics/grid-lens/states/<slug>/

    GridLens loss decomposition for a single state with station breakdown.
    """
    period = parse_ea_period(request)
    month, year = period['month'], period['year']

    state = get_object_or_404(State, slug=slug)
    return Response(_state_payload(state, month, year, period))
