"""
analytics/views/grid_lens/overview.py

GET /api/analytics/grid-lens/

GridLens system-wide loss decomposition for a billing period.

This is the only GridLens endpoint that provides the full 4-layer energy
chain because MonthlyOverviewSummary (commercial billed / collected) is
a system-wide pre-calculated table with no geographic breakdown.

Energy chain:
    EA Received  →  Feeder Distributed  →  Commercially Billed  →  Collected

Query params:
    year  : int  (default: current year)
    month : int  (default: current month)
"""

from django.db.models import Q, Sum

from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from analytics.permissions import HasGridLensAccess
from common.models import InjectionSubstation, State
from energy_account.models import EAFeederTechnicalEnergy, EAMonthlyReturn
from energy_account.utils import metric, parse_ea_period

from ._helpers import (
    build_two_layer_response,
    compute_losses,
    fetch_commercial_layer,
    fetch_ea_layer,
    fetch_stream_b_layer,
)


@api_view(['GET'])
@permission_classes([HasGridLensAccess])
def grid_lens_overview(request):
    """
    GET /api/analytics/grid-lens/

    Full 4-layer GridLens energy chain for the entire KEDCO network.
    Includes system-wide metering gap, distribution loss, collection loss,
    total ATC&C loss, and all efficiency metrics.

    Also includes a by-state breakdown (EA + Stream B only per state).
    """
    period = parse_ea_period(request)
    month, year = period['month'], period['year']

    # ── Fetch all three layers ────────────────────────────────────────────────
    ea         = fetch_ea_layer(month, year)
    stream_b   = fetch_stream_b_layer(month, year)
    commercial = fetch_commercial_layer(month, year)

    ea_mwh        = ea['total_mwh']
    stream_b_mwh  = stream_b['total_mwh']
    billed_mwh    = commercial['billed']
    collected_mwh = commercial['collected']

    losses = compute_losses(ea_mwh, stream_b_mwh, billed_mwh, collected_mwh)

    # ── By-state breakdown (EA + Stream B) ────────────────────────────────────
    state_breakdown = []
    for state in State.objects.order_by('name'):
        station_ids = list(
            InjectionSubstation.objects
            .filter(state=state)
            .values_list('id', flat=True)
        )
        if not station_ids:
            continue

        s_ea       = fetch_ea_layer(month, year, station_ids=station_ids)
        s_stream_b = fetch_stream_b_layer(month, year, station_ids=station_ids)

        if not s_ea['return_count']:
            continue

        s_losses = compute_losses(s_ea['total_mwh'], s_stream_b['total_mwh'])

        state_breakdown.append({
            'state': {'slug': state.slug, 'name': state.name},
            'ea_received_mwh':        metric(round(s_ea['total_mwh'], 4),       unit='MWh'),
            'feeder_distributed_mwh': metric(round(s_stream_b['total_mwh'], 4), unit='MWh'),
            'metering_gap_mwh':       metric(s_losses['metering_gap_mwh'],      unit='MWh'),
            'metering_gap_pct':       metric(s_losses['metering_gap_pct'],       unit='%'),
            'transmission_efficiency': metric(s_losses['transmission_eff'],      unit='%'),
            'return_count':           metric(s_ea['return_count']),
            'late_returns':           metric(s_ea['late_count']),
        })

    return Response({
        'period': {
            'year':  year,
            'month': month,
            'label': period['label'],
        },
        'module': 'GridLens',

        'energy_chain': {
            'ea_received_mwh': metric(
                round(ea_mwh, 4),
                unit='MWh',
                explanation=(
                    'Total EA billing MWh from all EAMonthlyReturn records for this period. '
                    'This is the TCN-agreed energy figure — what KEDCO officially received '
                    'from the national grid at every injection substation.'
                ),
            ),
            'feeder_distributed_mwh': metric(
                round(stream_b_mwh, 4),
                unit='MWh',
                explanation=(
                    'Stream B: total feeder technical energy from the monitoring system. '
                    'Sum of EAFeederTechnicalEnergy.energy_mwh across all feeders. '
                    'Represents energy measured as it flowed from substations into the '
                    'distribution network (33kV and 11kV feeders combined).'
                ),
            ),
            'commercially_billed_mwh': metric(
                round(billed_mwh, 4),
                unit='MWh',
                explanation=(
                    'Total energy billed to customers — from MonthlyOverviewSummary.energy_billed. '
                    'Energy for which KEDCO raised invoices to end-use customers in this period.'
                ),
            ),
            'commercially_collected_mwh': metric(
                round(collected_mwh, 4),
                unit='MWh',
                explanation=(
                    'Energy equivalent of revenue actually collected from customers — '
                    'from MonthlyOverviewSummary.energy_collected. '
                    'Represents effective energy recovery after billing disputes and bad debt.'
                ),
            ),
        },

        'loss_decomposition': {
            'metering_gap': {
                'mwh': metric(
                    losses['metering_gap_mwh'],
                    unit='MWh',
                    explanation=(
                        'EA received − feeder distributed. '
                        'Gap between the station transformer meter (TCN-agreed) and the sum of '
                        'individual feeder technical energies at the substation. '
                        'Attributable to: transformer iron losses, substation auxiliary loads, '
                        'and measurement accuracy differences between the TCN transmission meter '
                        'and the SCADA feeder monitors.'
                    ),
                ),
                'pct': metric(
                    losses['metering_gap_pct'],
                    unit='%',
                    explanation='metering_gap_mwh / ea_received_mwh × 100.',
                ),
            },
            'distribution_loss': {
                'mwh': metric(
                    losses['dist_loss_mwh'],
                    unit='MWh',
                    explanation=(
                        'Feeder distributed − commercially billed. '
                        'Energy that passed through the feeder network but was not billed to customers. '
                        'Comprises: technical network losses (conductor resistance, transformer losses), '
                        'unmetered connections, meter bypass, and monitoring-to-billing alignment gaps.'
                    ),
                ),
                'pct': metric(
                    losses['dist_loss_pct'],
                    unit='%',
                    explanation='distribution_loss_mwh / feeder_distributed_mwh × 100.',
                ),
            },
            'collection_loss': {
                'mwh': metric(
                    losses['coll_loss_mwh'],
                    unit='MWh',
                    explanation=(
                        'Commercially billed − commercially collected. '
                        'Energy billed to customers for which payment was not recovered. '
                        'Pure commercial loss — billing disputes, bad debt, collection inefficiency, '
                        'disconnected accounts with outstanding balances.'
                    ),
                ),
                'pct': metric(
                    losses['coll_loss_pct'],
                    unit='%',
                    explanation='collection_loss_mwh / commercially_billed_mwh × 100.',
                ),
            },
            'total_atcc_loss': {
                'mwh': metric(
                    losses['total_loss_mwh'],
                    unit='MWh',
                    explanation=(
                        'EA received − commercially collected. '
                        'Total Aggregate Technical & Commercial (ATC&C) loss — the complete gap '
                        'between what KEDCO received from TCN and what was effectively recovered '
                        'from customers. Combines metering gap + distribution loss + collection loss.'
                    ),
                ),
                'pct': metric(
                    losses['total_loss_pct'],
                    unit='%',
                    explanation='total_atcc_loss_mwh / ea_received_mwh × 100.',
                ),
            },
        },

        'efficiency': {
            'transmission_efficiency': metric(
                losses['transmission_eff'],
                unit='%',
                explanation=(
                    'feeder_distributed / ea_received × 100. '
                    'Share of TCN-received energy that reaches the feeder distribution level. '
                    'High value = low metering/transformer gap at substation level.'
                ),
            ),
            'billing_efficiency': metric(
                losses['billing_eff'],
                unit='%',
                explanation=(
                    'commercially_billed / feeder_distributed × 100. '
                    'Share of distributed energy that is captured in customer billing. '
                    'Gap = technical network losses + unmetered consumption.'
                ),
            ),
            'collection_efficiency': metric(
                losses['collection_eff'],
                unit='%',
                explanation=(
                    'commercially_collected / commercially_billed × 100. '
                    'Share of billed energy for which payment was collected. '
                    'Gap = commercial losses (bad debt, collection shortfall).'
                ),
            ),
            'overall_efficiency': metric(
                losses['overall_eff'],
                unit='%',
                explanation=(
                    'commercially_collected / ea_received × 100. '
                    'End-to-end energy recovery rate from TCN grid input to customer cash collection. '
                    'This is KEDCO\'s effective energy utilisation rate.'
                ),
            ),
        },

        'data_context': {
            'ea_return_count': metric(
                ea['return_count'],
                explanation='EA monthly returns included in the EA layer for this period.',
            ),
            'late_returns': metric(
                ea['late_count'],
                explanation='EA returns submitted after the monthly deadline.',
            ),
            'total_feeder_records': metric(
                stream_b['total_feeders'],
                explanation='Feeder Stream B records contributing to the feeder distributed figure.',
            ),
            'kv33_mwh': metric(
                round(stream_b['kv33_mwh'], 4),
                unit='MWh',
                explanation='Stream B component from 33kV feeders.',
            ),
            'kv11_mwh': metric(
                round(stream_b['kv11_mwh'], 4),
                unit='MWh',
                explanation='Stream B component from 11kV feeders.',
            ),
            'avg_stream_b_completeness': metric(
                stream_b['avg_completeness'],
                unit='%',
                explanation=(
                    'Average data completeness % across all feeder Stream B records. '
                    'Low completeness may inflate the metering gap figure.'
                ),
            ),
            'feeders_with_gaps': metric(
                stream_b['feeders_with_gaps'],
                explanation='Feeders with incomplete SCADA monitoring data for this period.',
            ),
            'commercial_data_note': metric(
                'MonthlyOverviewSummary',
                explanation=(
                    'Commercial billed / collected figures sourced from the pre-calculated '
                    'MonthlyOverviewSummary table. Available at system level only — not '
                    'disaggregated by state, district, or station in GridLens sub-views.'
                ),
            ),
        },

        'breakdown': {
            'by_state': state_breakdown,
        },
    })
