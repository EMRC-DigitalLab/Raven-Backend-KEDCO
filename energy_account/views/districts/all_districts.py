"""
energy_account/views/districts/all_districts.py

GET /api/energy-account/districts/
GET /api/energy-account/districts/<slug>/

EA analytics broken down by business district.

Query params:
    year  : int  (default: current year)
    month : int  (default: current month)
    state : str  (optional — filter by state slug)

District → Station linkage:
    InjectionSubstation has no direct district FK.
    We resolve via: Feeder.business_district → Feeder.substation
    i.e. a station belongs to a district if any of its feeders belong to that district.

Methodology splits on every KPI:
  - Stream A vs Stream B
  - 33KV vs 11KV feeder type
  - Return status, billing rule, data source
  - TCN/MO reconciliation
"""

from django.db.models import Sum
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from energy_account.permissions import HasEAAccess

from common.models import BusinessDistrict, InjectionSubstation
from energy_account.models import EAFeederTechnicalEnergy, EAMonthlyReturn
from energy_account.utils import (
    build_ea_response,
    calc_ea_metrics,
    metric,
    parse_ea_period,
)


def _station_ids_in_district(district):
    """Return a list of InjectionSubstation PKs that have at least one feeder in this district."""
    return list(
        InjectionSubstation.objects
        .filter(feeders__business_district=district)
        .values_list('id', flat=True)
        .distinct()
    )


def _district_payload(district, returns_qs, period):
    """Compute the full EA analytics payload for a single district."""
    m  = calc_ea_metrics(returns_qs)
    ea = build_ea_response(m)

    # Station breakdown within this district
    station_breakdown = []
    station_ids = _station_ids_in_district(district)
    for station in InjectionSubstation.objects.filter(id__in=station_ids).order_by('name'):
        s_returns = returns_qs.filter(station=station)
        if not s_returns.exists():
            continue
        s_agg = s_returns.aggregate(
            mwh=Sum('station_billing_mwh'),
            naira=Sum('station_billing_naira'),
        )
        s_tech = EAFeederTechnicalEnergy.objects.filter(monthly_return__in=s_returns)
        station_breakdown.append({
            'station': {
                'slug':  station.slug,
                'name':  station.name,
                'state': station.state.slug if station.state else None,
            },
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
        'district': {
            'slug':  district.slug,
            'name':  district.name,
            'state': district.state.slug if district.state else None,
        },
        'scope': {
            'total_stations': metric(
                returns_qs.values('station_id').distinct().count(),
                explanation='Injection substations in this district with a billing return for this period.',
            ),
        },
        'returns':        ea['returns'],
        'energy':         ea['energy'],
        'revenue':        ea['revenue'],
        'reconciliation': ea['reconciliation'],
        'data_quality':   ea['data_quality'],
        'breakdown': {
            'by_station': station_breakdown,
        },
    }


@api_view(['GET'])
@permission_classes([HasEAAccess])
def all_districts(request):
    """
    GET /api/energy-account/districts/

    EA analytics for every business district, for the given billing period.
    Optional: ?state=<slug> to filter by state.
    """
    period = parse_ea_period(request)
    month, year = period['month'], period['year']
    state_slug  = request.GET.get('state', '').strip()

    returns_all = EAMonthlyReturn.objects.filter(month=month, year=year)

    districts_qs = BusinessDistrict.objects.select_related('state').order_by('state__name', 'name')
    if state_slug:
        districts_qs = districts_qs.filter(state__slug=state_slug)

    results = []
    for district in districts_qs:
        station_ids = _station_ids_in_district(district)
        if not station_ids:
            continue
        d_returns = returns_all.filter(station_id__in=station_ids)
        if not d_returns.exists():
            continue

        m  = calc_ea_metrics(d_returns)
        ea = build_ea_response(m)

        results.append({
            'district': {
                'slug':  district.slug,
                'name':  district.name,
                'state': district.state.slug if district.state else None,
            },
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
        'districts': results,
    })


@api_view(['GET'])
@permission_classes([HasEAAccess])
def single_district(request, slug):
    """
    GET /api/energy-account/districts/<slug>/

    Full EA analytics for a single business district with station breakdown.
    """
    period = parse_ea_period(request)
    month, year = period['month'], period['year']

    district    = get_object_or_404(BusinessDistrict, slug=slug)
    station_ids = _station_ids_in_district(district)
    returns_qs  = EAMonthlyReturn.objects.filter(
        month=month, year=year, station_id__in=station_ids
    )

    return Response(_district_payload(district, returns_qs, period))
