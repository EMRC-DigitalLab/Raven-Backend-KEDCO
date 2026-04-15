"""
energy_account/views/compare/ea_compare.py

Two dedicated structural EA comparison views.
These live under the Compare Engine because they answer specific
hierarchical questions in the EA billing system:

  1. station_vs_feeders  — Transformer meter (Stream A) vs each 33kV feeder's
                           technical energy (Stream B) for a single station.
                           "How does the transformer billing read compare against
                            the sum of its individual feeder contributions?"

  2. kv33_vs_kv11        — 33kV feeder energy total vs 11kV feeder energy total.
                           "How much energy is measured at the 33kV tier vs the
                            downstream 11kV tier?"
                           Scoped to a single station, or rolled up across all
                           stations (optionally filtered by state/district).

Both return the standard compare-engine response shape:
  { compare_type, period, scope, sides, methodology }

Query params (both endpoints):
    year     : int  (default: current year)
    month    : int  (default: current month)

station_vs_feeders also requires:
    <slug> — injection substation slug in the URL

kv33_vs_kv11 optional:
    station  : str  (filter to one station slug)
    state    : str  (filter by state slug)
    district : str  (filter by business district slug)
"""

from django.db.models import Avg, Count, Q, Sum
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from energy_account.permissions import HasEAAccess

from common.models import BusinessDistrict, InjectionSubstation
from energy_account.models import (
    EAFeederTechnicalEnergy,
    EAMonthlyReading,
    EAMonthlyReturn,
)
from energy_account.utils import metric, parse_ea_period


# ─── Alignment flag helpers ───────────────────────────────────────────────────

def _alignment_flag(pct, thresholds):
    """Classify a loss % into a status string using ordered threshold pairs."""
    if pct is None:
        return 'no_data'
    for max_val, label in thresholds:
        if pct <= max_val:
            return label
    return thresholds[-1][1]

# Transformer → 33kV metering gap bands
_METERING_GAP_BANDS = [
    (2.0,          'within_threshold'),   # billing rule NOT triggered
    (5.0,          'elevated'),           # watch, but billing rule may not apply
    (float('inf'), 'critical'),           # significant metering discrepancy
]
# 33kV → 11kV tier gap bands (larger gaps are expected in distribution)
_TIER_GAP_BANDS = [
    (10.0,         'normal'),
    (20.0,         'elevated'),
    (float('inf'), 'high'),
]
# Per-feeder: check meter (Stream A) vs Stream B deviation
_FEEDER_DEVIATION_BANDS = [
    (2.0,          'aligned'),
    (5.0,          'minor_divergence'),
    (float('inf'), 'divergent'),
]


# ─── 1. Station vs Feeders ────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([HasEAAccess])
def station_vs_feeders(request, slug):
    """
    GET /api/energy-account/compare/station-vs-feeders/<slug>/

    Compares the station-level transformer meter (Stream A — the transmission
    figure) against each 33kV feeder's technical energy (Stream B).

    This answers: "Does the transformer billing read match the sum of what
    flowed out through the individual 33kV feeders?"

    Response structure:
      - transmission_side: transformer meter (Stream A) → single figure
      - feeder_side: each 33kV feeder as a separate row (Stream B)
      - summary: sum of feeder side vs transformer side, deviation %
    """
    period = parse_ea_period(request)
    month, year = period['month'], period['year']

    station = get_object_or_404(InjectionSubstation, slug=slug)

    try:
        monthly_return = EAMonthlyReturn.objects.get(
            station=station, month=month, year=year
        )
    except EAMonthlyReturn.DoesNotExist:
        return Response({
            'compare_type': 'station_vs_feeders',
            'period':  {'year': year, 'month': month, 'label': period['label']},
            'station': {'slug': station.slug, 'name': station.name},
            'error':   f'No monthly return found for {station.slug} — {period["label"]}',
        }, status=404)

    # ── Transmission side: transformer meter reading (Stream A) ───────────────
    transformer_readings = (
        EAMonthlyReading.objects
        .filter(
            monthly_return=monthly_return,
            grid_meter__meter_owner_type='transformer',
        )
        .select_related('transformer', 'grid_meter')
    )

    transmission_rows = []
    total_stream_a = 0.0
    for r in transformer_readings:
        mwh = float(r.billing_mwh) if r.billing_mwh else float(r.energy_mwh)
        total_stream_a += mwh
        transmission_rows.append({
            'label':          r.transformer.name if r.transformer else r.grid_meter.meter_no,
            'meter_no':       r.grid_meter.meter_no,
            'energy_mwh':     metric(
                float(r.energy_mwh),
                unit='MWh',
                explanation='Raw transformer meter read: present_reading − previous_reading × multiplying_factor.',
            ),
            'billing_mwh':    metric(
                float(r.billing_mwh) if r.billing_mwh else None,
                unit='MWh',
                explanation='Final billing figure after applying billing rule (threshold / max_applied).',
            ),
            'billing_rule':   r.billing_rule_applied,
            'is_flagged':     r.is_flagged,
            'deviation_pct':  float(r.deviation_pct) if r.deviation_pct else None,
            'data_source':    r.data_source,
            'nbet_snapshot':  float(r.nbet_rate_snapshot) if r.nbet_rate_snapshot else None,
        })

    # ── Feeder side: each 33kV feeder Stream B entry ──────────────────────────
    feeder_tech_rows = (
        EAFeederTechnicalEnergy.objects
        .filter(monthly_return=monthly_return, feeder_type='33KV')
        .select_related('feeder', 'feeder__business_district')
        .order_by('feeder__slug')
    )

    feeder_rows = []
    total_stream_b_33kv = 0.0
    for fte in feeder_tech_rows:
        mwh = float(fte.energy_mwh) if fte.energy_mwh else 0.0
        total_stream_b_33kv += mwh

        # Check if this feeder also has a check meter reading (Stream A feeder read)
        feeder_check_read = (
            EAMonthlyReading.objects
            .filter(
                monthly_return=monthly_return,
                feeder=fte.feeder,
                grid_meter__meter_owner_type='feeder',
            )
            .first()
        )
        check_mwh = float(feeder_check_read.energy_mwh) if feeder_check_read else None

        feeder_rows.append({
            'feeder': {
                'slug':     fte.feeder.slug,
                'name':     fte.feeder.name,
                'district': fte.feeder.business_district.slug if fte.feeder.business_district else None,
            },
            'stream_b_mwh': metric(
                mwh,
                unit='MWh',
                explanation='Stream B: feeder technical energy from the monitoring system for this 33kV feeder.',
            ),
            'check_meter_mwh': metric(
                check_mwh,
                unit='MWh',
                explanation=(
                    'Feeder check meter read (Stream A feeder side), if a grid meter is registered '
                    'for this feeder. Null if no feeder check meter exists.'
                ),
            ),
            'data_completeness_pct': metric(
                float(fte.data_completeness_pct),
                unit='%',
                explanation='days_with_data / total_days_in_period × 100.',
            ),
            'has_gaps':   fte.has_gaps,
            'data_source': fte.data_source,
        })

    # ── Summary: transmission vs feeder sum ───────────────────────────────────
    deviation_pct = None
    if total_stream_a and total_stream_b_33kv:
        deviation_pct = round(
            abs(total_stream_a - total_stream_b_33kv) / total_stream_b_33kv * 100, 4
        )

    # Feeder contributions as % of transformer total
    for row in feeder_rows:
        v = row['stream_b_mwh']['value']
        row['pct_of_transformer'] = metric(
            round(v / total_stream_a * 100, 2) if total_stream_a else None,
            unit='%',
            explanation='This feeder\'s Stream B as a percentage of the total transformer billing read.',
        )

    return Response({
        'compare_type': 'station_vs_feeders',
        'period': {
            'year':  year,
            'month': month,
            'label': period['label'],
        },
        'station': {
            'slug':   station.slug,
            'name':   station.name,
            'state':  station.state.slug if station.state else None,
            'status': monthly_return.status,
            'is_late': monthly_return.is_late,
        },

        'methodology': {
            'transmission_side': metric(
                None,
                explanation=(
                    'Stream A — Physical transformer meter readings submitted by the station DSO. '
                    'This is the "transmission figure": what the transformer meter says left the '
                    'high-voltage side. The authoritative billing source when within 2% of feeder sum.'
                ),
            ),
            'feeder_side': metric(
                None,
                explanation=(
                    'Stream B — Feeder technical energy from the monitoring system, one row per 33kV feeder. '
                    'Sum of all feeder Stream B values should approximate the transformer meter read. '
                    'Deviation >2% triggers the max_applied billing rule.'
                ),
            ),
        },

        'summary': {
            'transmission_total_mwh': metric(
                round(total_stream_a, 4),
                unit='MWh',
                explanation='Total Stream A billing MWh across all transformer meters at this station.',
            ),
            'feeder_sum_33kv_mwh': metric(
                round(total_stream_b_33kv, 4),
                unit='MWh',
                explanation='Sum of Stream B energy across all 33kV feeders at this station.',
            ),
            'deviation_pct': metric(
                deviation_pct,
                unit='%',
                explanation=(
                    '|Transformer total − Feeder sum| / Feeder sum × 100. '
                    'If this exceeds 2%, the higher figure is used for billing (max_applied rule).'
                ),
            ),
            'billing_rule_triggered': metric(
                'max_applied' if (deviation_pct is not None and deviation_pct > 2.0) else 'within_threshold',
                explanation=(
                    'within_threshold: transformer read used directly. '
                    'max_applied: deviation >2% — higher of transformer vs feeder sum used.'
                ),
            ),
            'station_billing_mwh': metric(
                float(monthly_return.station_billing_mwh) if monthly_return.station_billing_mwh else None,
                unit='MWh',
                explanation='Final station billing figure from EAMonthlyReturn — used for NBET invoicing.',
            ),
            'station_billing_naira': metric(
                float(monthly_return.station_billing_naira) if monthly_return.station_billing_naira else None,
                unit='NGN',
                explanation='Final billed Naira from EAMonthlyReturn.',
            ),
        },

        'transmission_side': transmission_rows,
        'feeder_side':        feeder_rows,
    })


# ─── 2. 33kV vs 11kV ─────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([HasEAAccess])
def kv33_vs_kv11(request):
    """
    GET /api/energy-account/compare/kv33-vs-kv11/

    Compares 33kV feeder energy against 11kV feeder energy (Stream B) for a
    billing period. This shows energy measured at each distribution tier.

    "How much energy is measured at the 33kV level vs the downstream 11kV level?"

    A gap between the two tiers indicates distribution losses between
    the 33kV and 11kV networks.

    Optional filters:
        ?station=<slug>  — scope to a single injection substation
        ?state=<slug>    — scope to all stations in a state
        ?district=<slug> — scope to all stations in a business district

    Response structure:
      - summary: 33kV total vs 11kV total, tier gap, tier gap %
      - by_station: per-station breakdown with 33kV and 11kV splits
      - methodology: explanation of what each tier means
    """
    period = parse_ea_period(request)
    month, year   = period['month'], period['year']
    station_slug  = request.GET.get('station',  '').strip()
    state_slug    = request.GET.get('state',    '').strip()
    district_slug = request.GET.get('district', '').strip()

    # ── Scope: filter returns ─────────────────────────────────────────────────
    returns_qs = EAMonthlyReturn.objects.filter(month=month, year=year)

    if station_slug:
        returns_qs = returns_qs.filter(station__slug=station_slug)
    elif state_slug:
        returns_qs = returns_qs.filter(station__state__slug=state_slug)
    elif district_slug:
        station_ids = list(
            InjectionSubstation.objects
            .filter(feeders__business_district__slug=district_slug)
            .values_list('id', flat=True)
            .distinct()
        )
        returns_qs = returns_qs.filter(station_id__in=station_ids)

    # ── Overall 33kV vs 11kV totals ───────────────────────────────────────────
    tech_qs = EAFeederTechnicalEnergy.objects.filter(monthly_return__in=returns_qs)

    tier_agg = tech_qs.aggregate(
        total_33kv=Sum('energy_mwh', filter=Q(feeder_type='33KV')),
        total_11kv=Sum('energy_mwh', filter=Q(feeder_type='11KV')),
        feeders_33kv=Count('id', filter=Q(feeder_type='33KV')),
        feeders_11kv=Count('id', filter=Q(feeder_type='11KV')),
        avg_completeness_33kv=Avg('data_completeness_pct', filter=Q(feeder_type='33KV')),
        avg_completeness_11kv=Avg('data_completeness_pct', filter=Q(feeder_type='11KV')),
        gaps_33kv=Count('id', filter=Q(feeder_type='33KV', has_gaps=True)),
        gaps_11kv=Count('id', filter=Q(feeder_type='11KV', has_gaps=True)),
    )

    total_33kv = float(tier_agg['total_33kv'] or 0)
    total_11kv = float(tier_agg['total_11kv'] or 0)

    # Tier gap: energy at 33kV level that doesn't appear at 11kV level
    tier_gap_mwh = round(total_33kv - total_11kv, 4)
    tier_gap_pct = round(tier_gap_mwh / total_33kv * 100, 4) if total_33kv else None

    # ── Per-station breakdown ─────────────────────────────────────────────────
    station_breakdown = []

    # Bulk aggregation per station — avoids N+1
    per_station = {
        row['monthly_return__station_id']: row
        for row in (
            tech_qs
            .values('monthly_return__station_id',
                    'monthly_return__station__slug',
                    'monthly_return__station__name')
            .annotate(
                mwh_33kv=Sum('energy_mwh', filter=Q(feeder_type='33KV')),
                mwh_11kv=Sum('energy_mwh', filter=Q(feeder_type='11KV')),
                cnt_33kv=Count('id', filter=Q(feeder_type='33KV')),
                cnt_11kv=Count('id', filter=Q(feeder_type='11KV')),
                avg_comp_33kv=Avg('data_completeness_pct', filter=Q(feeder_type='33KV')),
                avg_comp_11kv=Avg('data_completeness_pct', filter=Q(feeder_type='11KV')),
            )
            .order_by('monthly_return__station__name')
        )
    }

    for station_id, row in per_station.items():
        s_33kv = float(row['mwh_33kv'] or 0)
        s_11kv = float(row['mwh_11kv'] or 0)
        s_gap  = round(s_33kv - s_11kv, 4)
        s_gap_pct = round(s_gap / s_33kv * 100, 4) if s_33kv else None

        station_breakdown.append({
            'station': {
                'slug': row['monthly_return__station__slug'],
                'name': row['monthly_return__station__name'],
            },
            'kv33': {
                'energy_mwh': metric(
                    round(s_33kv, 4),
                    unit='MWh',
                    explanation='Sum of Stream B energy from all 33kV feeders at this station.',
                ),
                'feeder_count': metric(
                    row['cnt_33kv'],
                    explanation='Number of 33kV feeders with a Stream B record this period.',
                ),
                'avg_data_completeness': metric(
                    round(float(row['avg_comp_33kv']), 2) if row['avg_comp_33kv'] else 0.0,
                    unit='%',
                    explanation='Average Stream B data completeness % for 33kV feeders.',
                ),
            },
            'kv11': {
                'energy_mwh': metric(
                    round(s_11kv, 4),
                    unit='MWh',
                    explanation='Sum of Stream B energy from all 11kV feeders at this station.',
                ),
                'feeder_count': metric(
                    row['cnt_11kv'],
                    explanation='Number of 11kV feeders with a Stream B record this period.',
                ),
                'avg_data_completeness': metric(
                    round(float(row['avg_comp_11kv']), 2) if row['avg_comp_11kv'] else 0.0,
                    unit='%',
                    explanation='Average Stream B data completeness % for 11kV feeders.',
                ),
            },
            'tier_gap': {
                'gap_mwh': metric(
                    s_gap,
                    unit='MWh',
                    explanation=(
                        '33kV total − 11kV total for this station. '
                        'Positive gap indicates energy measured at 33kV that is not captured at 11kV — '
                        'reflects distribution losses and any unmeasured 11kV feeders.'
                    ),
                ),
                'gap_pct': metric(
                    s_gap_pct,
                    unit='%',
                    explanation='Tier gap as a percentage of 33kV energy — gap_mwh / kv33_mwh × 100.',
                ),
            },
        })

    return Response({
        'compare_type': 'kv33_vs_kv11',
        'period': {
            'year':  year,
            'month': month,
            'label': period['label'],
        },
        'scope': {
            'total_stations': metric(
                returns_qs.values('station_id').distinct().count(),
                explanation='Injection substations included in this comparison.',
            ),
            'total_33kv_feeders': metric(
                tier_agg['feeders_33kv'] or 0,
                explanation='33kV feeder Stream B records in scope.',
            ),
            'total_11kv_feeders': metric(
                tier_agg['feeders_11kv'] or 0,
                explanation='11kV feeder Stream B records in scope.',
            ),
        },

        'methodology': {
            'kv33_side': metric(
                None,
                explanation=(
                    '33kV feeder Stream B energy — measured directly at the 33kV bus. '
                    'These feeders connect the injection substation transformer to the '
                    'distribution network. This is the closest measurement to the '
                    'transmission input point.'
                ),
            ),
            'kv11_side': metric(
                None,
                explanation=(
                    '11kV feeder Stream B energy — measured at the 11kV downstream level. '
                    '11kV feeders serve the last-mile distribution to customers. '
                    'Energy at this tier is typically lower than 33kV due to transformer '
                    'losses and unmeasured network sections.'
                ),
            ),
            'tier_gap': metric(
                None,
                explanation=(
                    'The difference between 33kV and 11kV measured energy. '
                    'This gap represents: (1) transformer losses between 33kV and 11kV, '
                    '(2) feeders without monitoring coverage, '
                    '(3) meter accuracy differences between tiers. '
                    'A large tier gap warrants investigation of data completeness and meter health.'
                ),
            ),
        },

        'summary': {
            'total_33kv_mwh': metric(
                round(total_33kv, 4),
                unit='MWh',
                explanation='Sum of all 33kV feeder Stream B energy across all stations in scope.',
            ),
            'total_11kv_mwh': metric(
                round(total_11kv, 4),
                unit='MWh',
                explanation='Sum of all 11kV feeder Stream B energy across all stations in scope.',
            ),
            'tier_gap_mwh': metric(
                tier_gap_mwh,
                unit='MWh',
                explanation='33kV total − 11kV total across all stations in scope.',
            ),
            'tier_gap_pct': metric(
                tier_gap_pct,
                unit='%',
                explanation='tier_gap_mwh / total_33kv_mwh × 100.',
            ),
            'avg_completeness_33kv': metric(
                round(float(tier_agg['avg_completeness_33kv']), 2)
                if tier_agg['avg_completeness_33kv'] else 0.0,
                unit='%',
                explanation='Average Stream B data completeness % across all 33kV feeder records.',
            ),
            'avg_completeness_11kv': metric(
                round(float(tier_agg['avg_completeness_11kv']), 2)
                if tier_agg['avg_completeness_11kv'] else 0.0,
                unit='%',
                explanation='Average Stream B data completeness % across all 11kV feeder records.',
            ),
            'feeders_with_gaps_33kv': metric(
                tier_agg['gaps_33kv'] or 0,
                explanation='33kV feeders with incomplete Stream B data this period.',
            ),
            'feeders_with_gaps_11kv': metric(
                tier_agg['gaps_11kv'] or 0,
                explanation='11kV feeders with incomplete Stream B data this period.',
            ),
        },

        'by_station': station_breakdown,
    })


# ─── 3. Full Alignment Cascade ────────────────────────────────────────────────

def _station_alignment_payload(station, monthly_return):
    """
    Build the full Transformer → 33kV → 11kV alignment cascade for one station.

    Three layers:
      transmission_layer  — transformer meter readings (Stream A)
      kv33_layer          — each 33kV feeder Stream B + check meter alignment
      kv11_layer          — each 11kV feeder Stream B

    Loss cascade:
      metering_gap        — transformer billing − 33kV Stream B total
      tier_gap            — 33kV Stream B total − 11kV Stream B total
      total_cascade_loss  — transformer billing − 11kV Stream B total
    """
    rid = monthly_return.id

    # ── Transmission layer: transformer meters ────────────────────────────────
    transformer_rows = []
    total_stream_a_billing = 0.0
    total_stream_a_energy  = 0.0

    for r in (
        EAMonthlyReading.objects
        .filter(monthly_return_id=rid, grid_meter__meter_owner_type='transformer')
        .select_related('grid_meter', 'transformer')
        .order_by('grid_meter__meter_no')
    ):
        billing = float(r.billing_mwh) if r.billing_mwh else float(r.energy_mwh)
        energy  = float(r.energy_mwh)
        total_stream_a_billing += billing
        total_stream_a_energy  += energy
        transformer_rows.append({
            'label':         r.transformer.name if r.transformer else r.grid_meter.meter_no,
            'meter_no':      r.grid_meter.meter_no,
            'energy_mwh':    metric(round(energy,  4), unit='MWh',
                                    explanation='Raw transformer meter read: present − previous × multiplier.'),
            'billing_mwh':   metric(round(billing, 4), unit='MWh',
                                    explanation='Billing figure after applying billing rule (threshold/max_applied).'),
            'billing_rule':  r.billing_rule_applied,
            'deviation_pct': float(r.deviation_pct)      if r.deviation_pct      else None,
            'is_flagged':    r.is_flagged,
            'data_source':   r.data_source,
            'nbet_snapshot': float(r.nbet_rate_snapshot) if r.nbet_rate_snapshot else None,
        })

    # ── 33kV layer: feeder technical energy + check meter alignment ───────────
    # Bulk-fetch 33kV feeder check meter readings for this return in one query
    check_33 = {
        row['feeder_id']: row
        for row in (
            EAMonthlyReading.objects
            .filter(
                monthly_return_id=rid,
                grid_meter__meter_owner_type='feeder',
                feeder__voltage_level='33kv',
            )
            .values('feeder_id')
            .annotate(check_mwh=Sum('energy_mwh'), billing_mwh=Sum('billing_mwh'))
        )
    }

    kv33_rows = []
    total_kv33 = 0.0

    for fte in (
        EAFeederTechnicalEnergy.objects
        .filter(monthly_return_id=rid, feeder_type='33KV')
        .select_related('feeder', 'feeder__business_district')
        .order_by('feeder__slug')
    ):
        b_mwh = float(fte.energy_mwh) if fte.energy_mwh else 0.0
        total_kv33 += b_mwh

        chk     = check_33.get(fte.feeder_id, {})
        a_mwh   = float(chk.get('check_mwh') or 0) or None

        # Check meter vs Stream B deviation for this feeder
        a_vs_b_pct    = None
        feeder_status = 'no_check_meter'
        if a_mwh and b_mwh:
            a_vs_b_pct    = round(abs(a_mwh - b_mwh) / b_mwh * 100, 4)
            feeder_status = _alignment_flag(a_vs_b_pct, _FEEDER_DEVIATION_BANDS)

        kv33_rows.append({
            'feeder': {
                'slug':     fte.feeder.slug,
                'name':     fte.feeder.name,
                'district': fte.feeder.business_district.slug if fte.feeder.business_district else None,
            },
            'stream_b_mwh': metric(
                b_mwh, unit='MWh',
                explanation='Stream B: feeder technical energy from the SCADA/monitoring system.',
            ),
            'check_meter_mwh': metric(
                a_mwh, unit='MWh',
                explanation=(
                    'Stream A feeder check meter read for this 33kV feeder. '
                    'Only present if a grid meter is registered for this feeder.'
                ),
            ),
            'check_vs_stream_b_pct': metric(
                a_vs_b_pct, unit='%',
                explanation='|Check meter − Stream B| / Stream B × 100.',
            ),
            'feeder_alignment_status': feeder_status,
            'data_completeness_pct':  metric(float(fte.data_completeness_pct), unit='%',
                                             explanation='days_with_data / total_days × 100 for Stream B.'),
            'has_gaps':   fte.has_gaps,
            'data_source': fte.data_source,
            'pct_of_transformer': None,  # filled below once total_kv33 known — placeholder
        })

    # Attach transformer share to each 33kV feeder row
    for row in kv33_rows:
        v = row['stream_b_mwh']['value']
        row['pct_of_transformer'] = metric(
            round(v / total_stream_a_billing * 100, 4) if total_stream_a_billing else None,
            unit='%',
            explanation="This feeder's Stream B as % of the station transformer billing read.",
        )

    # ── 11kV layer: feeder technical energy ───────────────────────────────────
    kv11_rows = []
    total_kv11 = 0.0

    for fte in (
        EAFeederTechnicalEnergy.objects
        .filter(monthly_return_id=rid, feeder_type='11KV')
        .select_related('feeder', 'feeder__business_district')
        .order_by('feeder__slug')
    ):
        b_mwh = float(fte.energy_mwh) if fte.energy_mwh else 0.0
        total_kv11 += b_mwh
        kv11_rows.append({
            'feeder': {
                'slug':     fte.feeder.slug,
                'name':     fte.feeder.name,
                'district': fte.feeder.business_district.slug if fte.feeder.business_district else None,
            },
            'stream_b_mwh': metric(
                b_mwh, unit='MWh',
                explanation='Stream B: 11kV feeder technical energy from the monitoring system.',
            ),
            'pct_of_kv33_total': None,  # filled below
            'data_completeness_pct': metric(float(fte.data_completeness_pct), unit='%'),
            'has_gaps':   fte.has_gaps,
            'data_source': fte.data_source,
        })

    for row in kv11_rows:
        v = row['stream_b_mwh']['value']
        row['pct_of_kv33_total'] = metric(
            round(v / total_kv33 * 100, 4) if total_kv33 else None,
            unit='%',
            explanation="This feeder's Stream B as % of the station's total 33kV Stream B.",
        )

    # ── Loss cascade ──────────────────────────────────────────────────────────
    def _gap(a, b):
        return round(a - b, 4) if a else None

    def _gap_pct(gap, base):
        return round(gap / base * 100, 4) if (base and gap is not None) else None

    metering_gap_mwh     = _gap(total_stream_a_billing, total_kv33)
    metering_gap_pct     = _gap_pct(metering_gap_mwh, total_stream_a_billing)
    tier_gap_mwh         = _gap(total_kv33, total_kv11)
    tier_gap_pct         = _gap_pct(tier_gap_mwh, total_kv33)
    cascade_loss_mwh     = _gap(total_stream_a_billing, total_kv11)
    cascade_loss_pct     = _gap_pct(cascade_loss_mwh, total_stream_a_billing)

    # ── Alignment flags ───────────────────────────────────────────────────────
    metering_status  = _alignment_flag(abs(metering_gap_pct) if metering_gap_pct is not None else None, _METERING_GAP_BANDS)
    tier_status      = _alignment_flag(tier_gap_pct,  _TIER_GAP_BANDS)

    misaligned_33       = sum(1 for r in kv33_rows if r['feeder_alignment_status'] == 'divergent')
    gaps_33             = sum(1 for r in kv33_rows if r['has_gaps'])
    gaps_11             = sum(1 for r in kv11_rows if r['has_gaps'])

    return {
        'station': {
            'slug':   station.slug,
            'name':   station.name,
            'state':  station.state.slug if station.state else None,
            'type':   station.station_type,
            'status': station.status,
        },
        'return_status': monthly_return.status,
        'is_late':       monthly_return.is_late,

        # ── Layer 1: Transformer ──────────────────────────────────────────────
        'transmission_layer': {
            'total_billing_mwh': metric(
                round(total_stream_a_billing, 4), unit='MWh',
                explanation='Stream A total — transformer billing reads. The TCN-agreed input energy for this station.',
            ),
            'total_energy_mwh': metric(
                round(total_stream_a_energy, 4), unit='MWh',
                explanation='Raw transformer meter energy before billing rule adjustments.',
            ),
            'meter_count': metric(len(transformer_rows),
                                  explanation='Number of transformer meters at this station.'),
            'meters': transformer_rows,
        },

        # ── Layer 2: 33kV feeders ─────────────────────────────────────────────
        'kv33_layer': {
            'total_mwh': metric(
                round(total_kv33, 4), unit='MWh',
                explanation=(
                    'Stream B total for 33kV feeders — sum of feeder technical energy '
                    'from the monitoring system across all 33kV feeders at this station.'
                ),
            ),
            'feeder_count':      metric(len(kv33_rows),   explanation='33kV feeders with Stream B records this period.'),
            'feeders_with_gaps': metric(gaps_33,           explanation='33kV feeders with incomplete SCADA data this period.'),
            'misaligned_feeders': metric(misaligned_33,
                                         explanation='33kV feeders where check meter vs Stream B deviation exceeds 5%.'),
            'feeders': kv33_rows,
        },

        # ── Layer 3: 11kV feeders ─────────────────────────────────────────────
        'kv11_layer': {
            'total_mwh': metric(
                round(total_kv11, 4), unit='MWh',
                explanation=(
                    'Stream B total for 11kV feeders — energy measured at the downstream '
                    '11kV distribution level. Represents the last-mile network output.'
                ),
            ),
            'feeder_count':      metric(len(kv11_rows), explanation='11kV feeders with Stream B records this period.'),
            'feeders_with_gaps': metric(gaps_11,         explanation='11kV feeders with incomplete SCADA data this period.'),
            'feeders': kv11_rows,
        },

        # ── Loss cascade ──────────────────────────────────────────────────────
        'loss_cascade': {
            'metering_gap': {
                'label': 'Transformer → 33kV (Metering Gap)',
                'mwh': metric(
                    metering_gap_mwh, unit='MWh',
                    explanation=(
                        'Transformer billing read − sum of 33kV Stream B. '
                        'Energy unaccounted for between the TCN-metered transformer and the 33kV '
                        'distribution bus. Includes: transformer iron losses, substation auxiliary '
                        'loads, and accuracy differences between the TCN meter and SCADA monitors.'
                    ),
                ),
                'pct': metric(metering_gap_pct, unit='%',
                              explanation='metering_gap_mwh / transformer_billing_mwh × 100.'),
                'status': metering_status,
            },
            'tier_gap': {
                'label': '33kV → 11kV (Distribution Tier Gap)',
                'mwh': metric(
                    tier_gap_mwh, unit='MWh',
                    explanation=(
                        'Total 33kV Stream B − total 11kV Stream B. '
                        'Energy measured at the 33kV bus that does not appear at the 11kV level. '
                        'Represents: 33/11kV distribution transformer losses, conductor losses, '
                        'unmeasured network sections, and SCADA data coverage gaps.'
                    ),
                ),
                'pct': metric(tier_gap_pct, unit='%',
                              explanation='tier_gap_mwh / kv33_total_mwh × 100.'),
                'status': tier_status,
            },
            'total_cascade_loss': {
                'label': 'Transformer → 11kV (Full Cascade)',
                'mwh': metric(
                    cascade_loss_mwh, unit='MWh',
                    explanation=(
                        'Transformer billing read − total 11kV Stream B. '
                        'The complete energy gap from what the TCN meter recorded at the '
                        'transformer to what is measured flowing into the 11kV network. '
                        'Equals metering_gap + tier_gap combined.'
                    ),
                ),
                'pct': metric(cascade_loss_pct, unit='%',
                              explanation='total_cascade_loss_mwh / transformer_billing_mwh × 100.'),
            },
        },

        # ── Alignment summary ─────────────────────────────────────────────────
        'alignment': {
            'metering_gap_status':   metering_status,
            'tier_gap_status':       tier_status,
            'billing_rule_triggered': (
                'max_applied' if (metering_gap_pct is not None and abs(metering_gap_pct) > 2.0)
                else 'within_threshold'
            ),
            'station_billing_mwh': metric(
                float(monthly_return.station_billing_mwh) if monthly_return.station_billing_mwh else None,
                unit='MWh',
                explanation='Final station billing figure used for NBET invoicing.',
            ),
            'station_billing_naira': metric(
                float(monthly_return.station_billing_naira) if monthly_return.station_billing_naira else None,
                unit='NGN',
                explanation='Billed Naira from EAMonthlyReturn.',
            ),
        },
    }


@api_view(['GET'])
@permission_classes([HasEAAccess])
def full_alignment(request):
    """
    GET /api/energy-account/compare/full-alignment/

    Comprehensive energy cascade alignment across injection substations.

    Shows the complete Transformer → 33kV → 11kV energy chain for every
    station in scope, with all loss layers and alignment flags made fully
    visible:

      - Transformer billing (Stream A) per meter
      - Each 33kV feeder's Stream B + check meter alignment where available
      - Each 11kV feeder's Stream B
      - Metering gap  (Transformer → 33kV)
      - Tier gap      (33kV → 11kV)
      - Total cascade (Transformer → 11kV)
      - Alignment status flags at every level

    Optional filters:
        ?station=<slug>  — single station
        ?state=<slug>    — all stations in a state
        ?district=<slug> — all stations in a business district
    """
    period = parse_ea_period(request)
    month, year   = period['month'], period['year']
    station_slug  = request.GET.get('station',  '').strip()
    state_slug    = request.GET.get('state',    '').strip()
    district_slug = request.GET.get('district', '').strip()

    # ── Scope stations ────────────────────────────────────────────────────────
    stations_qs = InjectionSubstation.objects.select_related('state').order_by('state__name', 'name')
    if station_slug:
        stations_qs = stations_qs.filter(slug=station_slug)
    elif state_slug:
        stations_qs = stations_qs.filter(state__slug=state_slug)
    elif district_slug:
        district = get_object_or_404(BusinessDistrict, slug=district_slug)
        d_ids = list(
            InjectionSubstation.objects
            .filter(feeders__business_district=district)
            .values_list('id', flat=True)
            .distinct()
        )
        stations_qs = stations_qs.filter(id__in=d_ids)

    station_ids = list(stations_qs.values_list('id', flat=True))

    # Bulk-fetch all monthly returns for the scope in one query
    returns_map = {
        r.station_id: r
        for r in EAMonthlyReturn.objects.filter(
            station_id__in=station_ids, month=month, year=year
        )
    }

    if not returns_map:
        return Response({
            'compare_type': 'full_alignment',
            'period':   {'year': year, 'month': month, 'label': period['label']},
            'summary':  None,
            'stations': [],
            'note': f'No monthly returns found for {period["label"]}.',
        })

    # ── Build per-station payloads ────────────────────────────────────────────
    station_payloads = []
    for station in stations_qs:
        monthly_return = returns_map.get(station.id)
        if not monthly_return:
            continue
        station_payloads.append(
            _station_alignment_payload(station, monthly_return)
        )

    # ── System-wide summary ───────────────────────────────────────────────────
    total_transformer = sum(
        p['transmission_layer']['total_billing_mwh']['value'] or 0
        for p in station_payloads
    )
    total_kv33 = sum(
        p['kv33_layer']['total_mwh']['value'] or 0
        for p in station_payloads
    )
    total_kv11 = sum(
        p['kv11_layer']['total_mwh']['value'] or 0
        for p in station_payloads
    )

    def _sum_gap(a, b):
        v = round(a - b, 4)
        return v, (round(v / a * 100, 4) if a else None)

    net_metering_gap_mwh, net_metering_gap_pct = _sum_gap(total_transformer, total_kv33)
    net_tier_gap_mwh,     net_tier_gap_pct     = _sum_gap(total_kv33, total_kv11)
    net_cascade_mwh,      net_cascade_pct      = _sum_gap(total_transformer, total_kv11)

    stations_critical_metering  = sum(
        1 for p in station_payloads
        if p['alignment']['metering_gap_status'] == 'critical'
    )
    stations_high_tier_gap = sum(
        1 for p in station_payloads
        if p['alignment']['tier_gap_status'] == 'high'
    )
    total_misaligned_33 = sum(
        p['kv33_layer']['misaligned_feeders']['value'] or 0
        for p in station_payloads
    )
    total_gaps_33 = sum(
        p['kv33_layer']['feeders_with_gaps']['value'] or 0
        for p in station_payloads
    )
    total_gaps_11 = sum(
        p['kv11_layer']['feeders_with_gaps']['value'] or 0
        for p in station_payloads
    )

    summary = {
        'station_count': metric(len(station_payloads),
                                explanation='Injection substations included in this alignment report.'),

        'transformer_total_mwh': metric(
            round(total_transformer, 4), unit='MWh',
            explanation='Sum of all transformer billing reads (Stream A) across stations in scope.',
        ),
        'kv33_total_mwh': metric(
            round(total_kv33, 4), unit='MWh',
            explanation='Sum of all 33kV feeder Stream B across stations in scope.',
        ),
        'kv11_total_mwh': metric(
            round(total_kv11, 4), unit='MWh',
            explanation='Sum of all 11kV feeder Stream B across stations in scope.',
        ),

        'metering_gap': {
            'mwh': metric(net_metering_gap_mwh, unit='MWh',
                          explanation='Transformer total − 33kV total across all stations.'),
            'pct': metric(net_metering_gap_pct, unit='%'),
            'status': _alignment_flag(
                abs(net_metering_gap_pct) if net_metering_gap_pct is not None else None,
                _METERING_GAP_BANDS,
            ),
        },
        'tier_gap': {
            'mwh': metric(net_tier_gap_mwh, unit='MWh',
                          explanation='33kV total − 11kV total across all stations.'),
            'pct': metric(net_tier_gap_pct, unit='%'),
            'status': _alignment_flag(net_tier_gap_pct, _TIER_GAP_BANDS),
        },
        'total_cascade_loss': {
            'mwh': metric(net_cascade_mwh, unit='MWh',
                          explanation='Transformer total − 11kV total. Full Transformer → 11kV gap.'),
            'pct': metric(net_cascade_pct, unit='%'),
        },

        'attention_flags': {
            'stations_critical_metering_gap': metric(
                stations_critical_metering,
                explanation='Stations where transformer vs 33kV deviation exceeds 5%.',
            ),
            'stations_high_tier_gap': metric(
                stations_high_tier_gap,
                explanation='Stations where 33kV → 11kV tier gap exceeds 20%.',
            ),
            'misaligned_33kv_feeders': metric(
                total_misaligned_33,
                explanation='33kV feeders (across all stations) where check meter vs Stream B deviation exceeds 5%.',
            ),
            'kv33_feeders_with_data_gaps': metric(
                total_gaps_33,
                explanation='33kV feeders with incomplete SCADA monitoring data this period.',
            ),
            'kv11_feeders_with_data_gaps': metric(
                total_gaps_11,
                explanation='11kV feeders with incomplete SCADA monitoring data this period.',
            ),
        },
    }

    return Response({
        'compare_type': 'full_alignment',
        'period': {'year': year, 'month': month, 'label': period['label']},
        'summary': summary,
        'stations': station_payloads,
    })
