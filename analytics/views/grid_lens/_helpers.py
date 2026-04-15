"""
analytics/views/grid_lens/_helpers.py

Shared computation helpers for GridLens loss decomposition.

Energy chain:
    EA Received (TCN-agreed)
        ↓ metering gap
    Feeder Distributed (Stream B — monitoring system)
        ↓ distribution loss
    Commercially Billed (customer billing)
        ↓ collection loss
    Commercially Collected (cash received)

The commercial layer (billed / collected) is only available at system
level from MonthlyOverviewSummary. State / district / station views
expose the two EA-side layers only (EA received + Stream B).
"""

from datetime import date

from django.db.models import Avg, Count, Q, Sum

from analytics.models import MonthlyOverviewSummary
from energy_account.models import EAFeederTechnicalEnergy, EAMonthlyReturn
from energy_account.utils import metric


# ─── Data fetchers ────────────────────────────────────────────────────────────

def fetch_ea_layer(month, year, station_ids=None):
    """
    Return EA received MWh from EAMonthlyReturn for the billing period.
    Optionally scoped to a list of station PKs.
    """
    qs = EAMonthlyReturn.objects.filter(month=month, year=year)
    if station_ids is not None:
        qs = qs.filter(station_id__in=station_ids)
    agg = qs.aggregate(
        total_mwh=Sum('station_billing_mwh'),
        total_naira=Sum('station_billing_naira'),
        return_count=Count('id'),
        late_count=Count('id', filter=Q(is_late=True)),
    )
    return {
        'total_mwh':    float(agg['total_mwh']   or 0),
        'total_naira':  float(agg['total_naira']  or 0),
        'return_count': agg['return_count'] or 0,
        'late_count':   agg['late_count']   or 0,
    }


def fetch_stream_b_layer(month, year, station_ids=None):
    """
    Return Stream B (feeder distributed) MWh from EAFeederTechnicalEnergy.
    Includes 33KV / 11KV splits and data quality metrics.
    """
    qs = EAFeederTechnicalEnergy.objects.filter(
        monthly_return__month=month,
        monthly_return__year=year,
    )
    if station_ids is not None:
        qs = qs.filter(monthly_return__station_id__in=station_ids)
    agg = qs.aggregate(
        total_mwh=Sum('energy_mwh'),
        kv33_mwh=Sum('energy_mwh', filter=Q(feeder_type='33KV')),
        kv11_mwh=Sum('energy_mwh', filter=Q(feeder_type='11KV')),
        avg_completeness=Avg('data_completeness_pct'),
        feeders_with_gaps=Count('id', filter=Q(has_gaps=True)),
        total_feeders=Count('id'),
    )
    return {
        'total_mwh':         float(agg['total_mwh']          or 0),
        'kv33_mwh':          float(agg['kv33_mwh']           or 0),
        'kv11_mwh':          float(agg['kv11_mwh']           or 0),
        'avg_completeness':  round(float(agg['avg_completeness'] or 0), 2),
        'feeders_with_gaps': agg['feeders_with_gaps'] or 0,
        'total_feeders':     agg['total_feeders']     or 0,
    }


def fetch_commercial_layer(month, year):
    """
    Return commercially billed / collected MWh from MonthlyOverviewSummary.
    System-level only — MonthlyOverviewSummary has no geographic FK.
    Aggregates across all feeder_type rows for the period.
    """
    period_date = date(year, month, 1)
    agg = MonthlyOverviewSummary.objects.filter(month=period_date).aggregate(
        billed=Sum('energy_billed'),
        collected=Sum('energy_collected'),
    )
    return {
        'billed':    float(agg['billed']    or 0),
        'collected': float(agg['collected'] or 0),
    }


# ─── Loss computation ─────────────────────────────────────────────────────────

def compute_losses(ea_mwh, stream_b_mwh, billed_mwh=None, collected_mwh=None):
    """
    Compute all loss layers from the energy chain figures.

    Returns a dict of raw numeric values (no metric() wrapping — callers
    apply metric() themselves to add explanations appropriate to the context).
    """
    def _pct(numerator, denominator):
        if denominator and numerator is not None:
            return round(numerator / denominator * 100, 4)
        return None

    # Layer 1 — Metering gap (EA received vs Stream B)
    metering_gap_mwh = round(ea_mwh - stream_b_mwh, 4) if ea_mwh is not None else None
    metering_gap_pct = _pct(metering_gap_mwh, ea_mwh)

    # Layer 2 — Distribution loss (Stream B vs commercially billed)
    dist_loss_mwh = (
        round(stream_b_mwh - billed_mwh, 4)
        if stream_b_mwh is not None and billed_mwh is not None
        else None
    )
    dist_loss_pct = _pct(dist_loss_mwh, stream_b_mwh)

    # Layer 3 — Collection loss (billed vs collected)
    coll_loss_mwh = (
        round(billed_mwh - collected_mwh, 4)
        if billed_mwh is not None and collected_mwh is not None
        else None
    )
    coll_loss_pct = _pct(coll_loss_mwh, billed_mwh)

    # Total ATC&C loss (EA received vs collected)
    total_loss_mwh = (
        round(ea_mwh - collected_mwh, 4)
        if ea_mwh is not None and collected_mwh is not None
        else None
    )
    total_loss_pct = _pct(total_loss_mwh, ea_mwh)

    # Efficiency metrics
    transmission_eff = _pct(stream_b_mwh, ea_mwh)
    billing_eff      = _pct(billed_mwh,   stream_b_mwh)
    collection_eff   = _pct(collected_mwh, billed_mwh)
    overall_eff      = _pct(collected_mwh, ea_mwh)

    return {
        'metering_gap_mwh':  metering_gap_mwh,
        'metering_gap_pct':  metering_gap_pct,
        'dist_loss_mwh':     dist_loss_mwh,
        'dist_loss_pct':     dist_loss_pct,
        'coll_loss_mwh':     coll_loss_mwh,
        'coll_loss_pct':     coll_loss_pct,
        'total_loss_mwh':    total_loss_mwh,
        'total_loss_pct':    total_loss_pct,
        'transmission_eff':  transmission_eff,
        'billing_eff':       billing_eff,
        'collection_eff':    collection_eff,
        'overall_eff':       overall_eff,
    }


# ─── Response builders ────────────────────────────────────────────────────────

def build_two_layer_response(ea, stream_b, losses, scope_label):
    """
    Build the loss_decomposition + efficiency + data_context blocks
    for endpoints that only have EA + Stream B (no commercial layer).

    scope_label: e.g. 'state', 'district', 'station' — used in explanation text.
    """
    return {
        'energy_chain': {
            'ea_received_mwh': metric(
                round(ea['total_mwh'], 4),
                unit='MWh',
                explanation=(
                    'EA billing MWh from EAMonthlyReturn — the TCN-agreed figure for energy '
                    'received at this injection substation (or substations in this scope).'
                ),
            ),
            'feeder_distributed_mwh': metric(
                round(stream_b['total_mwh'], 4),
                unit='MWh',
                explanation=(
                    'Stream B: sum of feeder technical energy from the monitoring system. '
                    'Represents energy measured as it left the substation into distribution feeders.'
                ),
            ),
            'commercially_billed_mwh': metric(
                None,
                explanation=(
                    f'Commercial billed energy is not disaggregated by {scope_label} in the '
                    'pre-calculated summaries. Available at system level only — see /grid-lens/.'
                ),
            ),
            'commercially_collected_mwh': metric(
                None,
                explanation=f'See commercially_billed_mwh note above.',
            ),
        },
        'loss_decomposition': {
            'metering_gap': {
                'mwh': metric(
                    losses['metering_gap_mwh'],
                    unit='MWh',
                    explanation=(
                        'EA received − feeder distributed. Difference between the transformer '
                        'meter (TCN-agreed) and the sum of individual feeder technical energies. '
                        'Includes transformer losses, substation auxiliary consumption, and '
                        'meter accuracy differences between the transmission meter and feeder monitors.'
                    ),
                ),
                'pct': metric(
                    losses['metering_gap_pct'],
                    unit='%',
                    explanation='metering_gap_mwh / ea_received_mwh × 100.',
                ),
            },
            'distribution_loss':  None,
            'collection_loss':    None,
            'total_atcc_loss':    None,
        },
        'efficiency': {
            'transmission_efficiency': metric(
                losses['transmission_eff'],
                unit='%',
                explanation=(
                    'feeder_distributed / ea_received × 100. '
                    'Proportion of TCN-received energy that reaches the feeder distribution level.'
                ),
            ),
            'billing_efficiency':    metric(None, explanation='Requires commercial data — available at system level.'),
            'collection_efficiency': metric(None, explanation='Requires commercial data — available at system level.'),
            'overall_efficiency':    metric(None, explanation='Requires commercial data — available at system level.'),
        },
        'data_context': {
            'ea_return_count': metric(
                ea['return_count'],
                explanation='Monthly EA returns included in this analysis.',
            ),
            'late_returns': metric(
                ea['late_count'],
                explanation='EA returns submitted after the monthly deadline.',
            ),
            'total_feeder_records': metric(
                stream_b['total_feeders'],
                explanation='Feeder Stream B records in scope for this period.',
            ),
            'kv33_mwh': metric(
                round(stream_b['kv33_mwh'], 4),
                unit='MWh',
                explanation='Stream B energy from 33kV feeders.',
            ),
            'kv11_mwh': metric(
                round(stream_b['kv11_mwh'], 4),
                unit='MWh',
                explanation='Stream B energy from 11kV feeders.',
            ),
            'avg_stream_b_completeness': metric(
                stream_b['avg_completeness'],
                unit='%',
                explanation='Average data completeness % across feeder Stream B records.',
            ),
            'feeders_with_gaps': metric(
                stream_b['feeders_with_gaps'],
                explanation='Feeders with incomplete monitoring data for this period.',
            ),
        },
    }
