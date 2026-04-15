"""
energy_account/views/overview/overview.py

GET /api/energy-account/overview/

System-wide EA analytics for a billing period.
Aggregates all monthly returns across all injection substations.

Query params:
    year  : int  (default: current year)
    month : int  (default: current month)

Methodology splits alongside every KPI:
  - Stream A (transformer meter reads) vs Stream B (feeder technical energy)
  - 33KV vs 11KV feeder type energy split
  - Return status breakdown (draft → reconciled)
  - Billing rule applied breakdown
  - Data source breakdown
  - TCN and MO reconciliation status
"""

from django.db.models import Count, Q, Sum
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from energy_account.permissions import HasEAAccess

from common.models import BusinessDistrict, InjectionSubstation, State
from energy_account.models import (
    EAFeederTechnicalEnergy,
    EAMonthlyReturn,
)
from energy_account.utils import (
    build_ea_response,
    calc_ea_metrics,
    metric,
    parse_ea_period,
)


@api_view(['GET'])
@permission_classes([HasEAAccess])
def ea_overview(request):
    """
    GET /api/energy-account/overview/

    Full EA analytics for a billing period — all stations, all states.
    """
    period = parse_ea_period(request)
    month, year = period['month'], period['year']

    # ── All returns for this billing period ───────────────────────────────────
    returns_qs = EAMonthlyReturn.objects.filter(month=month, year=year)

    # ── Core EA metrics ───────────────────────────────────────────────────────
    m = calc_ea_metrics(returns_qs)

    # ── Station and state coverage ────────────────────────────────────────────
    total_stations = returns_qs.values('station_id').distinct().count()
    total_states   = (
        returns_qs
        .values('station__state_id')
        .exclude(station__state_id=None)
        .distinct()
        .count()
    )
    total_districts = (
        InjectionSubstation.objects
        .filter(monthly_returns__month=month, monthly_returns__year=year)
        .values('feeders__business_district_id')
        .exclude(feeders__business_district_id=None)
        .distinct()
        .count()
    )

    # ── Energy breakdown by state ─────────────────────────────────────────────
    state_breakdown = []
    for state in State.objects.order_by('name'):
        state_returns = returns_qs.filter(station__state=state)
        if not state_returns.exists():
            continue
        s_agg = state_returns.aggregate(
            mwh=Sum('station_billing_mwh'),
            naira=Sum('station_billing_naira'),
        )
        s_tech = EAFeederTechnicalEnergy.objects.filter(
            monthly_return__in=state_returns
        )
        state_breakdown.append({
            'state': {'slug': state.slug, 'name': state.name},
            'total_returns':        state_returns.count(),
            'total_billing_mwh':    float(s_agg['mwh']   or 0),
            'total_billing_naira':  float(s_agg['naira'] or 0),
            'stream_b_mwh': float(
                s_tech.aggregate(mwh=Sum('energy_mwh'))['mwh'] or 0
            ),
            'late_count': state_returns.filter(is_late=True).count(),
        })

    # ── Energy breakdown by district ──────────────────────────────────────────
    district_breakdown = []
    for district in BusinessDistrict.objects.select_related('state').order_by('name'):
        # Stations in this district via feeders
        station_ids = list(
            InjectionSubstation.objects
            .filter(feeders__business_district=district)
            .values_list('id', flat=True)
            .distinct()
        )
        if not station_ids:
            continue
        d_returns = returns_qs.filter(station_id__in=station_ids)
        if not d_returns.exists():
            continue
        d_agg = d_returns.aggregate(
            mwh=Sum('station_billing_mwh'),
            naira=Sum('station_billing_naira'),
        )
        district_breakdown.append({
            'district': {
                'slug':  district.slug,
                'name':  district.name,
                'state': district.state.slug if district.state else None,
            },
            'total_returns':       d_returns.count(),
            'total_billing_mwh':   float(d_agg['mwh']   or 0),
            'total_billing_naira': float(d_agg['naira'] or 0),
            'late_count':          d_returns.filter(is_late=True).count(),
        })

    # ── Build standardised EA response ────────────────────────────────────────
    ea = build_ea_response(m)

    return Response({
        'period': {
            'year':  year,
            'month': month,
            'label': period['label'],
        },

        'scope': {
            'total_stations': metric(
                total_stations,
                explanation='Number of injection substations that have a monthly return for this period.',
            ),
            'total_states': metric(
                total_states,
                explanation='Number of states with at least one billing return in this period.',
            ),
            'total_districts': metric(
                total_districts,
                explanation='Number of business districts with at least one station in this period.',
            ),
        },

        'returns':       ea['returns'],
        'energy':        ea['energy'],
        'revenue':       ea['revenue'],
        'reconciliation': ea['reconciliation'],
        'data_quality':  ea['data_quality'],

        'breakdown': {
            'by_state':    state_breakdown,
            'by_district': district_breakdown,
        },
    })
