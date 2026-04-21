"""
energy_account/views/feeders/all_feeders.py

GET /api/energy-account/feeders/
GET /api/energy-account/feeders/<slug>/

EA analytics at the individual feeder level.

Query params:
    year     : int  (default: current year)
    month    : int  (default: current month)
    station  : str  (optional — filter by station slug)
    district : str  (optional — filter by business district slug)
    state    : str  (optional — filter by state slug)
    type     : 33KV | 11KV  (optional — filter by feeder voltage)

At feeder level the primary data source is EAFeederTechnicalEnergy (Stream B).
Stream A meter readings are linked via EAMonthlyReading.feeder FK (for feeders
that have their own grid meter — typically 33KV feeder check meters).

Methodology splits on every KPI:
  - Stream A (feeder meter read, if exists) vs Stream B (technical energy)
  - Feeder type: 33KV vs 11KV
  - Data completeness, gaps, anomalies
  - Weekly reading trend (last 4 weeks)
"""

from django.db.models import Avg, Count, Q, Sum
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from energy_account.permissions import HasEAAccess

from common.models import BusinessDistrict, Feeder, InjectionSubstation
from energy_account.models import (
    EAFeederTechnicalEnergy,
    EAMonthlyReading,
    EAMonthlyReturn,
    EAWeeklyReading,
)
from energy_account.utils import metric, parse_ea_period


def _feeder_metrics(feeder, month, year):
    """
    Compute EA metrics for a single feeder for a given billing period.
    Reused by both all_feeders (list) and single_feeder (detail).
    """
    # Stream B: feeder technical energy record for this period
    try:
        fte = EAFeederTechnicalEnergy.objects.select_related(
            'monthly_return', 'monthly_return__station'
        ).get(feeder=feeder, monthly_return__month=month, monthly_return__year=year)
        stream_b_mwh        = float(fte.energy_mwh) if fte.energy_mwh else None
        data_completeness   = float(fte.data_completeness_pct)
        days_with_data      = fte.days_with_data
        total_days          = fte.total_days_in_period
        has_gaps            = fte.has_gaps
        gap_notes           = fte.gap_notes
        data_source         = fte.data_source
        station_slug        = fte.monthly_return.station.slug
        return_status       = fte.monthly_return.status
    except EAFeederTechnicalEnergy.DoesNotExist:
        stream_b_mwh = data_completeness = None
        days_with_data = total_days = 0
        has_gaps = False
        gap_notes = data_source = ''
        station_slug = feeder.substation.slug if feeder.substation_id else None
        return_status = None

    # Stream A: feeder meter reading for this period (only for feeders with their own grid meter)
    feeder_reading = (
        EAMonthlyReading.objects
        .filter(
            feeder=feeder,
            monthly_return__month=month,
            monthly_return__year=year,
            grid_meter__meter_owner_type='feeder',
        )
        .first()
    )
    stream_a_mwh   = float(feeder_reading.energy_mwh)  if feeder_reading else None
    billing_mwh    = float(feeder_reading.billing_mwh) if feeder_reading and feeder_reading.billing_mwh else None
    billing_naira  = float(feeder_reading.billing_naira) if feeder_reading and feeder_reading.billing_naira else None
    billing_rule   = feeder_reading.billing_rule_applied if feeder_reading else None
    is_flagged     = feeder_reading.is_flagged if feeder_reading else None
    deviation_pct  = float(feeder_reading.deviation_pct) if feeder_reading and feeder_reading.deviation_pct else None
    nbet_snapshot  = float(feeder_reading.nbet_rate_snapshot) if feeder_reading and feeder_reading.nbet_rate_snapshot else None

    # Stream A vs B deviation (only when both exist)
    if stream_a_mwh is not None and stream_b_mwh:
        a_vs_b_pct = round(abs(stream_a_mwh - stream_b_mwh) / stream_b_mwh * 100, 4)
    else:
        a_vs_b_pct = None

    return {
        'station_slug':      station_slug,
        'return_status':     return_status,
        'stream_a_mwh':      stream_a_mwh,
        'stream_b_mwh':      stream_b_mwh,
        'billing_mwh':       billing_mwh,
        'billing_naira':     billing_naira,
        'billing_rule':      billing_rule,
        'is_flagged':        is_flagged,
        'deviation_pct':     deviation_pct,
        'nbet_snapshot':     nbet_snapshot,
        'a_vs_b_pct':        a_vs_b_pct,
        'data_completeness': data_completeness,
        'days_with_data':    days_with_data,
        'total_days':        total_days,
        'has_gaps':          has_gaps,
        'gap_notes':         gap_notes,
        'data_source':       data_source,
    }


@api_view(['GET'])
@permission_classes([HasEAAccess])
def all_feeders(request):
    """
    GET /api/energy-account/feeders/

    EA analytics for every feeder, for the given billing period.
    Each row contains Stream A, Stream B, completeness, billing info
    and the full methodology split.

    Optional filters: ?station=<slug>, ?district=<slug>, ?state=<slug>, ?type=33KV|11KV
    """
    period = parse_ea_period(request)
    month, year   = period['month'], period['year']
    station_slug  = request.GET.get('station',  '').strip()
    district_slug = request.GET.get('district', '').strip()
    state_slug    = request.GET.get('state',    '').strip()
    feeder_type   = request.GET.get('type',     '').strip().upper()

    # ── Bulk aggregation: feeder technical energy for the period ──────────────
    fte_qs = EAFeederTechnicalEnergy.objects.filter(
        monthly_return__month=month,
        monthly_return__year=year,
    ).select_related('feeder', 'feeder__substation', 'feeder__business_district',
                     'monthly_return__station', 'monthly_return__station__state')

    if station_slug:
        fte_qs = fte_qs.filter(monthly_return__station__slug=station_slug)
    if district_slug:
        fte_qs = fte_qs.filter(feeder__business_district__slug=district_slug)
    if state_slug:
        fte_qs = fte_qs.filter(monthly_return__station__state__slug=state_slug)
    if feeder_type in ('33KV', '11KV'):
        fte_qs = fte_qs.filter(feeder_type=feeder_type)

    # Bulk fetch Stream A readings keyed by feeder_id
    feeder_ids = list(fte_qs.values_list('feeder_id', flat=True).distinct())
    stream_a_map = {
        row['feeder_id']: row
        for row in (
            EAMonthlyReading.objects
            .filter(
                feeder_id__in=feeder_ids,
                monthly_return__month=month,
                monthly_return__year=year,
                grid_meter__meter_owner_type='feeder',
            )
            .values('feeder_id')
            .annotate(
                stream_a_mwh=Sum('energy_mwh'),
                billing_mwh=Sum('billing_mwh'),
                billing_naira=Sum('billing_naira'),
                flagged_count=Count('id', filter=Q(is_flagged=True)),
            )
        )
    }

    results = []
    for fte in fte_qs.order_by(
        'monthly_return__station__state__name',
        'monthly_return__station__name',
        'feeder_type',
        'feeder__slug',
    ):
        feeder = fte.feeder
        a      = stream_a_map.get(feeder.id, {})

        stream_b = float(fte.energy_mwh) if fte.energy_mwh else None
        stream_a = float(a.get('stream_a_mwh') or 0) or None
        a_vs_b   = None
        if stream_a is not None and stream_b:
            a_vs_b = round(abs(stream_a - stream_b) / stream_b * 100, 4)

        results.append({
            'feeder': {
                'slug':          feeder.slug,
                'name':          feeder.name,
                'voltage_level': feeder.voltage_level,
                'feeder_type':   fte.feeder_type,
                'station': {
                    'slug': fte.monthly_return.station.slug,
                    'name': fte.monthly_return.station.name,
                },
                'state': {
                    'slug': fte.monthly_return.station.state.slug,
                    'name': fte.monthly_return.station.state.name,
                } if fte.monthly_return.station.state else None,
            },
            'return_status': fte.monthly_return.status,

            'energy': {
                'stream_b_mwh': metric(
                    stream_b,
                    unit='MWh',
                    explanation='Stream B: feeder technical energy from the monitoring system.',
                ),
                'stream_a_mwh': metric(
                    stream_a,
                    unit='MWh',
                    explanation=(
                        'Stream A: feeder check meter reading for this feeder. '
                        'Only present if a feeder grid meter is registered for this feeder.'
                    ),
                ),
                'stream_a_vs_b_deviation': metric(
                    a_vs_b,
                    unit='%',
                    explanation='|Stream A − Stream B| / Stream B × 100.',
                ),
                'billing_mwh': metric(
                    float(a.get('billing_mwh') or 0) or None,
                    unit='MWh',
                    explanation='Final billing figure after applying the billing rule (threshold / max / coupling).',
                ),
                'billing_naira': metric(
                    float(a.get('billing_naira') or 0) or None,
                    unit='NGN',
                    explanation='Naira value of billing_mwh at the NBET rate snapshot.',
                ),
            },

            'data_quality': {
                'data_completeness_pct': metric(
                    float(fte.data_completeness_pct),
                    unit='%',
                    explanation='days_with_data / total_days_in_period × 100 for Stream B.',
                ),
                'days_with_data': metric(
                    fte.days_with_data,
                    explanation='Number of days in the period with valid Stream B monitoring data.',
                ),
                'total_days_in_period': metric(
                    fte.total_days_in_period,
                    explanation='Total days in the billing period.',
                ),
                'has_gaps': metric(
                    fte.has_gaps,
                    explanation='True if Stream B data is missing for one or more days.',
                ),
                'flagged_readings': metric(
                    a.get('flagged_count', 0),
                    explanation='Stream A feeder meter readings flagged for anomalies.',
                ),
                'data_source': metric(
                    fte.data_source,
                    explanation='Source of Stream B data: auto_technical or manual_entry.',
                ),
            },
        })

    # ── Summary totals ────────────────────────────────────────────────────────
    summary_agg = fte_qs.aggregate(
        total_stream_b=Sum('energy_mwh'),
        kv33=Sum('energy_mwh', filter=Q(feeder_type='33KV')),
        kv11=Sum('energy_mwh', filter=Q(feeder_type='11KV')),
        avg_completeness=Avg('data_completeness_pct'),
        feeders_with_gaps=Count('id', filter=Q(has_gaps=True)),
        total_feeders=Count('id'),
    )

    return Response({
        'period': {
            'year':  year,
            'month': month,
            'label': period['label'],
        },
        'summary': {
            'total_feeders': metric(
                summary_agg['total_feeders'] or 0,
                explanation='Total feeders with a Stream B record for this period.',
            ),
            'total_stream_b_mwh': metric(
                float(summary_agg['total_stream_b'] or 0),
                unit='MWh',
                explanation='Sum of all feeder Stream B energy for this period.',
            ),
            'kv33_mwh': metric(
                float(summary_agg['kv33'] or 0),
                unit='MWh',
                explanation='Stream B total for 33KV feeders.',
            ),
            'kv11_mwh': metric(
                float(summary_agg['kv11'] or 0),
                unit='MWh',
                explanation='Stream B total for 11KV feeders.',
            ),
            'avg_data_completeness': metric(
                round(float(summary_agg['avg_completeness'] or 0), 2),
                unit='%',
                explanation='Average data completeness % across all feeder records.',
            ),
            'feeders_with_gaps': metric(
                summary_agg['feeders_with_gaps'] or 0,
                explanation='Feeders with incomplete Stream B data.',
            ),
        },
        'feeders': results,
    })


@api_view(['GET'])
@permission_classes([HasEAAccess])
def single_feeder(request, slug):
    """
    GET /api/energy-account/feeders/<slug>/

    Full EA analytics for a single feeder.
    Includes Stream A/B detail, weekly readings trend (last 8 weeks),
    and billing breakdown.
    """
    period = parse_ea_period(request)
    month, year = period['month'], period['year']

    feeder = get_object_or_404(Feeder, slug=slug)
    fm     = _feeder_metrics(feeder, month, year)

    # ── Weekly readings trend (last 8 weeks from period) ─────────────────────
    weekly_trend = []
    for wr in (
        EAWeeklyReading.objects
        .filter(feeder=feeder)
        .order_by('-week_start_date')[:8]
    ):
        weekly_trend.append({
            'week_start_date':      str(wr.week_start_date),
            'week_end_date':        str(wr.week_end_date),
            'energy_mwh':           float(wr.energy_mwh) if wr.energy_mwh else None,
            'prior_week_energy_mwh': float(wr.prior_week_energy_mwh) if wr.prior_week_energy_mwh else None,
            'delta_pct':            float(wr.delta_pct) if wr.delta_pct else None,
            'is_anomaly':           wr.is_anomaly,
            'data_source':          wr.data_source,
            'notes':                wr.notes,
        })
    weekly_trend.reverse()  # chronological order

    # ── Feeder substation context ─────────────────────────────────────────────
    station = feeder.substation

    return Response({
        'period': {
            'year':  year,
            'month': month,
            'label': period['label'],
        },
        'feeder': {
            'slug':            feeder.slug,
            'name':            feeder.name,
            'voltage_level':   feeder.voltage_level,
            'station': {
                'slug':  station.slug,
                'name':  station.name,
                'state': {'slug': station.state.slug, 'name': station.state.name} if station.state else None,
            } if station else None,
            'business_district': {
                'slug': feeder.business_district.slug,
                'name': feeder.business_district.name,
            } if feeder.business_district else None,
            'status':  feeder.status,
        },
        'return_status': fm['return_status'],

        'energy': {
            'stream_b_mwh': metric(
                fm['stream_b_mwh'],
                unit='MWh',
                explanation=(
                    'Stream B: feeder technical energy from the monitoring system for this period. '
                    'Auto-computed from SCADA/hourly load data.'
                ),
            ),
            'stream_a_mwh': metric(
                fm['stream_a_mwh'],
                unit='MWh',
                explanation=(
                    'Stream A: feeder check meter physical reading for this period. '
                    'Only present if this feeder has a registered grid meter.'
                ),
            ),
            'stream_a_vs_b_deviation': metric(
                fm['a_vs_b_pct'],
                unit='%',
                explanation=(
                    '|Stream A − Stream B| / Stream B × 100. '
                    'If >2%, the higher figure is used for billing (max_applied rule).'
                ),
            ),
            'billing_mwh': metric(
                fm['billing_mwh'],
                unit='MWh',
                explanation=(
                    'Final energy figure used for billing after applying the billing rule. '
                    'billing_rule_applied indicates which rule was used.'
                ),
            ),
            'billing_rule_applied': metric(
                fm['billing_rule'],
                explanation=(
                    'Billing rule applied: '
                    'within_threshold = Stream A used as-is; '
                    'max_applied = deviation >2% so higher figure used; '
                    'coupling_adjusted = transformer fault period adjustment; '
                    'no_feeder_meters = no check meter, transformer meter only.'
                ),
            ),
        },

        'revenue': {
            'billing_naira': metric(
                fm['billing_naira'],
                unit='NGN',
                explanation='Naira value of billing_mwh at the NBET rate snapshot captured at reading time.',
            ),
            'nbet_rate_snapshot': metric(
                fm['nbet_snapshot'],
                unit='NGN/MWh',
                explanation='NBET rate (NGN per MWh) that was active when this reading was submitted.',
            ),
        },

        'data_quality': {
            'data_completeness_pct': metric(
                fm['data_completeness'],
                unit='%',
                explanation='days_with_data / total_days_in_period × 100 for Stream B.',
            ),
            'days_with_data': metric(
                fm['days_with_data'],
                explanation='Days in the billing period with valid monitoring system data.',
            ),
            'total_days_in_period': metric(
                fm['total_days'],
                explanation='Total calendar days in the billing period.',
            ),
            'has_gaps': metric(
                fm['has_gaps'],
                explanation='True when Stream B monitoring data is missing for one or more days.',
            ),
            'gap_notes': metric(
                fm['gap_notes'],
                explanation='Notes explaining the cause of any data gaps in Stream B.',
            ),
            'is_flagged': metric(
                fm['is_flagged'],
                explanation='True if the feeder check meter reading was flagged for deviation or data issues.',
            ),
            'deviation_pct': metric(
                fm['deviation_pct'],
                unit='%',
                explanation='Deviation % recorded on the feeder check meter reading row.',
            ),
            'data_source': metric(
                fm['data_source'],
                explanation='Source of Stream B: auto_technical (SCADA) or manual_entry.',
            ),
        },

        'weekly_trend': weekly_trend,
    })
