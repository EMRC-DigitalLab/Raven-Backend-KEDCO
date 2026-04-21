"""
analytics/views/grid_lens/stations.py

GET /api/analytics/grid-lens/stations/
GET /api/analytics/grid-lens/stations/<slug>/

GridLens loss decomposition at injection substation level.

At station level, the metering gap is the most granular loss signal:
  EA Received (transformer meter — TCN agreed)
      ↓  metering gap
  Feeder Distributed (sum of all feeder Stream B at this station)

Query params:
    year     : int  (default: current year)
    month    : int  (default: current month)
    state    : str  (optional — filter by state slug)
    district : str  (optional — filter by business district slug)
"""

from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from analytics.permissions import HasGridLensAccess
from common.models import BusinessDistrict, InjectionSubstation
from energy_account.models import EAFeederTechnicalEnergy, EAMonthlyReturn
from energy_account.utils import metric, parse_ea_period

from ._helpers import (
    build_two_layer_response,
    compute_losses,
    fetch_ea_layer,
    fetch_stream_b_layer,
)


def _station_payload(station, month, year, period):
    ea       = fetch_ea_layer(month, year, station_ids=[station.id])
    stream_b = fetch_stream_b_layer(month, year, station_ids=[station.id])
    losses   = compute_losses(ea['total_mwh'], stream_b['total_mwh'])

    layers = build_two_layer_response(ea, stream_b, losses, scope_label='station')

    # Per-feeder Stream B breakdown for this station
    feeder_breakdown = []
    fte_qs = (
        EAFeederTechnicalEnergy.objects
        .filter(
            monthly_return__station=station,
            monthly_return__month=month,
            monthly_return__year=year,
        )
        .select_related('feeder')
        .order_by('feeder_type', 'feeder__slug')
    )
    total_b = stream_b['total_mwh'] or 0
    for fte in fte_qs:
        mwh = float(fte.energy_mwh) if fte.energy_mwh else 0.0
        feeder_breakdown.append({
            'feeder': {
                'slug':        fte.feeder.slug,
                'name':        fte.feeder.name,
                'feeder_type': fte.feeder_type,
            },
            'stream_b_mwh': metric(
                mwh,
                unit='MWh',
                explanation='Stream B feeder technical energy for this feeder.',
            ),
            'pct_of_station_stream_b': metric(
                round(mwh / total_b * 100, 4) if total_b else None,
                unit='%',
                explanation=(
                    'This feeder\'s Stream B as a share of the station\'s total '
                    'feeder distributed energy.'
                ),
            ),
            'data_completeness_pct': metric(float(fte.data_completeness_pct), unit='%'),
            'has_gaps':  fte.has_gaps,
            'data_source': fte.data_source,
        })

    return {
        'period': {
            'year':  period['year'],
            'month': period['month'],
            'label': period['label'],
        },
        'station': {
            'slug':   station.slug,
            'name':   station.name,
            'state':  station.state.slug if station.state else None,
            'type':   station.station_type,
            'status': station.status,
        },
        **layers,
        'breakdown': {'by_feeder': feeder_breakdown},
    }


@api_view(['GET'])
@permission_classes([HasGridLensAccess])
def all_stations(request):
    """
    GET /api/analytics/grid-lens/stations/

    GridLens loss decomposition for every injection substation.
    Optional: ?state=<slug> or ?district=<slug>
    """
    period = parse_ea_period(request)
    month, year   = period['month'], period['year']
    state_slug    = request.GET.get('state',    '').strip()
    district_slug = request.GET.get('district', '').strip()

    stations_qs = InjectionSubstation.objects.select_related('state').order_by('state__name', 'name')
    if state_slug:
        stations_qs = stations_qs.filter(state__slug=state_slug)
    if district_slug:
        district = get_object_or_404(BusinessDistrict, slug=district_slug)
        district_station_ids = list(
            InjectionSubstation.objects
            .filter(feeders__business_district=district)
            .values_list('id', flat=True)
            .distinct()
        )
        stations_qs = stations_qs.filter(id__in=district_station_ids)

    results = []
    for station in stations_qs:
        ea       = fetch_ea_layer(month, year, station_ids=[station.id])
        stream_b = fetch_stream_b_layer(month, year, station_ids=[station.id])

        if not ea['return_count']:
            continue

        losses = compute_losses(ea['total_mwh'], stream_b['total_mwh'])

        results.append({
            'station': {
                'slug':  station.slug,
                'name':  station.name,
                'state': station.state.slug if station.state else None,
                'type':  station.station_type,
            },
            'ea_received_mwh':         metric(round(ea['total_mwh'], 4),       unit='MWh'),
            'feeder_distributed_mwh':  metric(round(stream_b['total_mwh'], 4), unit='MWh'),
            'metering_gap_mwh':        metric(losses['metering_gap_mwh'],      unit='MWh'),
            'metering_gap_pct':        metric(losses['metering_gap_pct'],       unit='%'),
            'transmission_efficiency': metric(losses['transmission_eff'],       unit='%'),
            'kv33_mwh':                metric(round(stream_b['kv33_mwh'], 4),  unit='MWh'),
            'kv11_mwh':                metric(round(stream_b['kv11_mwh'], 4),  unit='MWh'),
            'avg_stream_b_completeness': metric(stream_b['avg_completeness'],   unit='%'),
            'feeders_with_gaps':       metric(stream_b['feeders_with_gaps']),
        })

    return Response({
        'period': {'year': year, 'month': month, 'label': period['label']},
        'module': 'GridLens',
        'stations': results,
    })


@api_view(['GET'])
@permission_classes([HasGridLensAccess])
def single_station(request, slug):
    """
    GET /api/analytics/grid-lens/stations/<slug>/

    GridLens loss decomposition for a single injection substation
    with per-feeder Stream B breakdown.
    """
    period = parse_ea_period(request)
    month, year = period['month'], period['year']

    station = get_object_or_404(InjectionSubstation, slug=slug)
    return Response(_station_payload(station, month, year, period))
