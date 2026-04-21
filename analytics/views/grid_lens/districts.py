"""
analytics/views/grid_lens/districts.py

GET /api/analytics/grid-lens/districts/
GET /api/analytics/grid-lens/districts/<slug>/

GridLens loss decomposition at business district level.

Station → district linkage:
    InjectionSubstation has no direct district FK.
    Resolved via: Feeder.business_district → Feeder.substation

Two layers available at this scope:
  - EA Received  (EAMonthlyReturn)
  - Feeder Distributed  (EAFeederTechnicalEnergy)

Query params:
    year  : int  (default: current year)
    month : int  (default: current month)
    state : str  (optional — filter by state slug)
"""

from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from analytics.permissions import HasGridLensAccess
from common.models import BusinessDistrict, InjectionSubstation
from energy_account.utils import metric, parse_ea_period

from ._helpers import (
    build_two_layer_response,
    compute_losses,
    fetch_ea_layer,
    fetch_stream_b_layer,
)


def _station_ids_in_district(district):
    return list(
        InjectionSubstation.objects
        .filter(feeders__business_district=district)
        .values_list('id', flat=True)
        .distinct()
    )


def _district_payload(district, month, year, period):
    station_ids = _station_ids_in_district(district)

    ea       = fetch_ea_layer(month, year, station_ids=station_ids)
    stream_b = fetch_stream_b_layer(month, year, station_ids=station_ids)
    losses   = compute_losses(ea['total_mwh'], stream_b['total_mwh'])

    layers = build_two_layer_response(ea, stream_b, losses, scope_label='district')

    # Station breakdown within this district
    station_breakdown = []
    for station in InjectionSubstation.objects.filter(id__in=station_ids).order_by('name'):
        s_ea       = fetch_ea_layer(month, year, station_ids=[station.id])
        s_stream_b = fetch_stream_b_layer(month, year, station_ids=[station.id])
        if not s_ea['return_count']:
            continue
        s_losses = compute_losses(s_ea['total_mwh'], s_stream_b['total_mwh'])
        station_breakdown.append({
            'station': {
                'slug':  station.slug,
                'name':  station.name,
                'state': station.state.slug if station.state else None,
            },
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
        'district': {
            'slug':  district.slug,
            'name':  district.name,
            'state': district.state.slug if district.state else None,
        },
        **layers,
        'breakdown': {'by_station': station_breakdown},
    }


@api_view(['GET'])
@permission_classes([HasGridLensAccess])
def all_districts(request):
    """
    GET /api/analytics/grid-lens/districts/

    GridLens loss decomposition summary for every business district.
    Optional: ?state=<slug> to filter by state.
    """
    period = parse_ea_period(request)
    month, year = period['month'], period['year']
    state_slug  = request.GET.get('state', '').strip()

    districts_qs = BusinessDistrict.objects.select_related('state').order_by('state__name', 'name')
    if state_slug:
        districts_qs = districts_qs.filter(state__slug=state_slug)

    results = []
    for district in districts_qs:
        station_ids = _station_ids_in_district(district)
        if not station_ids:
            continue

        ea       = fetch_ea_layer(month, year, station_ids=station_ids)
        stream_b = fetch_stream_b_layer(month, year, station_ids=station_ids)

        if not ea['return_count']:
            continue

        losses = compute_losses(ea['total_mwh'], stream_b['total_mwh'])

        results.append({
            'district': {
                'slug':  district.slug,
                'name':  district.name,
                'state': district.state.slug if district.state else None,
            },
            'ea_received_mwh':         metric(round(ea['total_mwh'], 4),       unit='MWh'),
            'feeder_distributed_mwh':  metric(round(stream_b['total_mwh'], 4), unit='MWh'),
            'metering_gap_mwh':        metric(losses['metering_gap_mwh'],      unit='MWh'),
            'metering_gap_pct':        metric(losses['metering_gap_pct'],       unit='%'),
            'transmission_efficiency': metric(losses['transmission_eff'],       unit='%'),
            'return_count':            metric(ea['return_count']),
            'avg_stream_b_completeness': metric(stream_b['avg_completeness'],   unit='%'),
        })

    return Response({
        'period': {'year': year, 'month': month, 'label': period['label']},
        'module': 'GridLens',
        'districts': results,
    })


@api_view(['GET'])
@permission_classes([HasGridLensAccess])
def single_district(request, slug):
    """
    GET /api/analytics/grid-lens/districts/<slug>/

    GridLens loss decomposition for a single district with station breakdown.
    """
    period = parse_ea_period(request)
    month, year = period['month'], period['year']

    district = get_object_or_404(BusinessDistrict, slug=slug)
    return Response(_district_payload(district, month, year, period))
