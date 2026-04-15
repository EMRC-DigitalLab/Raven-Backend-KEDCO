"""
energy_account/views/states/all_states.py

GET /api/energy-account/states/
GET /api/energy-account/states/<slug>/

EA analytics broken down by state.

Query params:
    year  : int  (default: current year)
    month : int  (default: current month)

Geographic hierarchy:
    State → BusinessDistrict → InjectionSubstation → Feeder

Methodology splits on every KPI:
  - Stream A (transformer reads) vs Stream B (technical energy)
  - 33KV vs 11KV feeder type
  - Return status, billing rule, data source
  - TCN/MO reconciliation status
"""

from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from energy_account.permissions import HasEAAccess

from common.models import BusinessDistrict, InjectionSubstation, State
from energy_account.models import EAFeederTechnicalEnergy, EAMonthlyReturn
from energy_account.utils import (
    build_ea_response,
    calc_ea_metrics,
    metric,
    parse_ea_period,
)


def _state_payload(state, returns_qs, period):
    """Compute and return the full EA analytics payload for a single state."""
    m  = calc_ea_metrics(returns_qs)
    ea = build_ea_response(m)

    # District breakdown within the state
    district_breakdown = []
    for district in BusinessDistrict.objects.filter(state=state).order_by('name'):
        station_ids = list(
            InjectionSubstation.objects
            .filter(feeders__business_district=district)
            .values_list('id', flat=True)
            .distinct()
        )
        d_returns = returns_qs.filter(station_id__in=station_ids)
        if not d_returns.exists():
            continue
        d_agg = d_returns.aggregate(
            mwh=Sum('station_billing_mwh'),
            naira=Sum('station_billing_naira'),
        )
        d_tech = EAFeederTechnicalEnergy.objects.filter(monthly_return__in=d_returns)
        district_breakdown.append({
            'district': {'slug': district.slug, 'name': district.name},
            'total_returns':       d_returns.count(),
            'total_billing_mwh':   float(d_agg['mwh']   or 0),
            'total_billing_naira': float(d_agg['naira'] or 0),
            'stream_b_mwh': float(
                d_tech.aggregate(mwh=Sum('energy_mwh'))['mwh'] or 0
            ),
            'late_count': d_returns.filter(is_late=True).count(),
        })

    # Station list within this state
    station_breakdown = []
    for station in InjectionSubstation.objects.filter(state=state).order_by('name'):
        s_returns = returns_qs.filter(station=station)
        if not s_returns.exists():
            continue
        s_agg = s_returns.aggregate(
            mwh=Sum('station_billing_mwh'),
            naira=Sum('station_billing_naira'),
        )
        s_tech = EAFeederTechnicalEnergy.objects.filter(monthly_return__in=s_returns)
        station_breakdown.append({
            'station': {'slug': station.slug, 'name': station.name},
            'total_billing_mwh':   float(s_agg['mwh']   or 0),
            'total_billing_naira': float(s_agg['naira'] or 0),
            'stream_b_mwh': float(
                s_tech.aggregate(mwh=Sum('energy_mwh'))['mwh'] or 0
            ),
            'return_status': s_returns.values_list('status', flat=True).first(),
            'is_late':       s_returns.filter(is_late=True).exists(),
        })

    return {
        'period': {
            'year':  period['year'],
            'month': period['month'],
            'label': period['label'],
        },
        'state': {
            'slug': state.slug,
            'name': state.name,
        },
        'scope': {
            'total_stations': metric(
                returns_qs.values('station_id').distinct().count(),
                explanation='Injection substations in this state with a billing return for this period.',
            ),
            'total_districts': metric(
                len(district_breakdown),
                explanation='Business districts in this state with at least one active station.',
            ),
        },
        'returns':        ea['returns'],
        'energy':         ea['energy'],
        'revenue':        ea['revenue'],
        'reconciliation': ea['reconciliation'],
        'data_quality':   ea['data_quality'],
        'breakdown': {
            'by_district': district_breakdown,
            'by_station':  station_breakdown,
        },
    }


@api_view(['GET'])
@permission_classes([HasEAAccess])
def all_states(request):
    """
    GET /api/energy-account/states/

    EA analytics for every state, for the given billing period.
    Each state entry carries the full methodology split breakdown.
    """
    period = parse_ea_period(request)
    month, year = period['month'], period['year']

    returns_all = EAMonthlyReturn.objects.filter(month=month, year=year)

    results = []
    for state in State.objects.order_by('name'):
        state_returns = returns_all.filter(station__state=state)
        if not state_returns.exists():
            continue

        m  = calc_ea_metrics(state_returns)
        ea = build_ea_response(m)

        results.append({
            'state': {'slug': state.slug, 'name': state.name},
            'returns':        ea['returns'],
            'energy':         ea['energy'],
            'revenue':        ea['revenue'],
            'reconciliation': ea['reconciliation'],
            'data_quality':   ea['data_quality'],
        })

    return Response({
        'period': {
            'year':  year,
            'month': month,
            'label': period['label'],
        },
        'states': results,
    })


@api_view(['GET'])
@permission_classes([HasEAAccess])
def single_state(request, slug):
    """
    GET /api/energy-account/states/<slug>/

    Full EA analytics for a single state with district and station breakdown.
    """
    period = parse_ea_period(request)
    month, year = period['month'], period['year']

    state      = get_object_or_404(State, slug=slug)
    returns_qs = EAMonthlyReturn.objects.filter(
        month=month, year=year, station__state=state
    )

    return Response(_state_payload(state, returns_qs, period))
