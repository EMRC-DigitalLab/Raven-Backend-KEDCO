"""
energy_account/utils.py

Shared helpers for all Energy Account analytics views.
Mirrors commercial/analytics_utils.py — every KPI goes through metric(),
every view uses parse_ea_period() for the billing period filter.
"""

import calendar
from datetime import date

from django.db.models import Avg, Count, Q, Sum

from energy_account.models import (
    EAFeederTechnicalEnergy,
    EAMOReconciliation,
    EAMonthlyReading,
    EAMonthlyReturn,
    EATCNReconciliation,
)


# ── Period helper ─────────────────────────────────────────────────────────────

def parse_ea_period(request):
    """
    Parse month/year query params → {year, month, label}.
    EA date filter is always a billing period (month + year), not a date range.

    Query params:
        year  : int (default: current year)
        month : int (default: current month)
    """
    today = date.today()
    year  = int(request.GET.get('year',  today.year))
    month = int(request.GET.get('month', today.month))
    label = f"{calendar.month_name[month]} {year}"
    return {'year': year, 'month': month, 'label': label}


# ── Standard metric wrapper ───────────────────────────────────────────────────

def metric(value, unit='', mode='actual', explanation=''):
    """
    Every metric returned by the EA API uses this structure.
    Mirrors commercial/analytics_utils.metric().

    Args:
        value       : the computed value (int, float, dict, None)
        unit        : display unit string (e.g. 'MWh', 'NGN', '%')
        mode        : 'actual' | 'estimated' | 'derived'
        explanation : plain-English description of what this metric means
                      and how it is calculated
    """
    return {
        'value':       value,
        'unit':        unit,
        'mode':        mode,
        'explanation': explanation,
    }


# ── ALL_STATUSES constants ────────────────────────────────────────────────────

RETURN_STATUSES = [
    'draft', 'submitted', 'tcn_submitted', 'tcn_agreed', 'tcn_disputed',
    'mo_submitted', 'mo_agreed', 'mo_disputed', 'settled', 'reconciled',
]

TCN_STATUSES = ['pending', 'sent', 'tcn_submitted', 'under_review', 'agreed', 'disputed', 'resolved']

MO_STATUSES  = ['pending', 'agreed', 'disputed']

BILLING_RULES = ['within_threshold', 'max_applied', 'coupling_adjusted', 'no_feeder_meters']

DATA_SOURCES  = ['field_read', 'technical_derived', 'not_available']


# ── Core EA aggregation ───────────────────────────────────────────────────────

def calc_ea_metrics(returns_qs):
    """
    Compute all EA KPIs for a given EAMonthlyReturn queryset.
    Returns a flat dict of computed values — views build metric() wrappers on top.

    Covers:
      - Billing totals from monthly returns (station_billing_mwh / naira)
      - Return status breakdown and late submission tracking
      - Stream A: transformer meter readings (physical billing reads)
      - Stream B: feeder technical energy (monitoring system)
      - 33KV vs 11KV feeder type energy split
      - Stream A vs B deviation %
      - Data quality: flagged readings, completeness, gaps
      - Billing rule and data source breakdown
      - TCN reconciliation status breakdown and agreed rate
      - MO reconciliation status breakdown and agreed rate
    """

    # ── Billing totals from monthly returns ───────────────────────────────────
    billing_agg = returns_qs.aggregate(
        total_mwh=Sum('station_billing_mwh'),
        total_naira=Sum('station_billing_naira'),
    )
    total_billing_mwh   = float(billing_agg['total_mwh']   or 0)
    total_billing_naira = float(billing_agg['total_naira'] or 0)

    # ── Return counts / status breakdown ──────────────────────────────────────
    total_returns = returns_qs.count()
    late_count    = returns_qs.filter(is_late=True).count()
    late_rate     = round(late_count / total_returns * 100, 2) if total_returns else 0

    status_counts = {
        row['status']: row['count']
        for row in returns_qs.values('status').annotate(count=Count('id'))
    }

    # ── Stream A: transformer meter readings (billing reads) ──────────────────
    readings_qs          = EAMonthlyReading.objects.filter(monthly_return__in=returns_qs)
    transformer_readings = readings_qs.filter(grid_meter__meter_owner_type='transformer')

    stream_a_agg   = transformer_readings.aggregate(mwh=Sum('billing_mwh'), naira=Sum('billing_naira'))
    stream_a_mwh   = float(stream_a_agg['mwh']   or 0)
    stream_a_naira = float(stream_a_agg['naira'] or 0)

    # ── Stream B: feeder technical energy (monitoring system) ─────────────────
    tech_qs      = EAFeederTechnicalEnergy.objects.filter(monthly_return__in=returns_qs)
    stream_b_mwh = float(tech_qs.aggregate(mwh=Sum('energy_mwh'))['mwh'] or 0)

    kv33_mwh = float(
        tech_qs.filter(feeder_type='33KV').aggregate(mwh=Sum('energy_mwh'))['mwh'] or 0
    )
    kv11_mwh = float(
        tech_qs.filter(feeder_type='11KV').aggregate(mwh=Sum('energy_mwh'))['mwh'] or 0
    )

    # ── Stream A vs B deviation ───────────────────────────────────────────────
    if stream_b_mwh:
        stream_deviation_pct = round(abs(stream_a_mwh - stream_b_mwh) / stream_b_mwh * 100, 4)
    else:
        stream_deviation_pct = None

    # ── Data quality ──────────────────────────────────────────────────────────
    total_readings = readings_qs.count()
    flagged_count  = readings_qs.filter(is_flagged=True).count()

    avg_completeness_raw = tech_qs.aggregate(avg=Avg('data_completeness_pct'))['avg']
    avg_completeness     = round(float(avg_completeness_raw), 2) if avg_completeness_raw else 0
    feeders_with_gaps    = tech_qs.filter(has_gaps=True).count()

    rule_counts = {
        row['billing_rule_applied']: row['count']
        for row in readings_qs.values('billing_rule_applied').annotate(count=Count('id'))
    }
    source_counts = {
        row['data_source']: row['count']
        for row in readings_qs.values('data_source').annotate(count=Count('id'))
    }

    # ── TCN reconciliation ────────────────────────────────────────────────────
    tcn_qs = EATCNReconciliation.objects.filter(monthly_return__in=returns_qs)
    total_tcn = tcn_qs.count()
    tcn_status_counts = {
        row['status']: row['count']
        for row in tcn_qs.values('status').annotate(count=Count('id'))
    }
    tcn_agreed     = tcn_status_counts.get('agreed', 0) + tcn_status_counts.get('resolved', 0)
    tcn_agreed_rate = round(tcn_agreed / total_tcn * 100, 2) if total_tcn else 0

    # ── MO reconciliation ─────────────────────────────────────────────────────
    mo_qs = EAMOReconciliation.objects.filter(monthly_return__in=returns_qs)
    total_mo = mo_qs.count()
    mo_status_counts = {
        row['status']: row['count']
        for row in mo_qs.values('status').annotate(count=Count('id'))
    }
    mo_agreed     = mo_status_counts.get('agreed', 0)
    mo_agreed_rate = round(mo_agreed / total_mo * 100, 2) if total_mo else 0

    return {
        # billing
        'total_billing_mwh':    total_billing_mwh,
        'total_billing_naira':  total_billing_naira,
        # returns
        'total_returns':  total_returns,
        'late_count':     late_count,
        'late_rate':      late_rate,
        'status_counts':  status_counts,
        # stream A
        'stream_a_mwh':   stream_a_mwh,
        'stream_a_naira': stream_a_naira,
        # stream B
        'stream_b_mwh':          stream_b_mwh,
        'kv33_mwh':              kv33_mwh,
        'kv11_mwh':              kv11_mwh,
        'stream_deviation_pct':  stream_deviation_pct,
        # data quality
        'total_readings':    total_readings,
        'flagged_count':     flagged_count,
        'avg_completeness':  avg_completeness,
        'feeders_with_gaps': feeders_with_gaps,
        'rule_counts':       rule_counts,
        'source_counts':     source_counts,
        # reconciliation
        'total_tcn':         total_tcn,
        'tcn_status_counts': tcn_status_counts,
        'tcn_agreed_rate':   tcn_agreed_rate,
        'total_mo':          total_mo,
        'mo_status_counts':  mo_status_counts,
        'mo_agreed_rate':    mo_agreed_rate,
    }


# ── Standard EA response builder ──────────────────────────────────────────────

def build_ea_response(m):
    """
    Build the standardised EA analytics response block from calc_ea_metrics() output.
    Every KPI is wrapped with metric().

    Used by all hierarchy levels: overview, state, district, station, feeder.
    Callers add their own 'period' and 'scope' blocks around this output.
    """

    # Return status breakdown
    status_metrics = {
        s: metric(
            m['status_counts'].get(s, 0),
            explanation=f"Monthly returns with status '{s}'.",
        )
        for s in RETURN_STATUSES
    }

    # TCN reconciliation status breakdown
    tcn_status_metrics = {
        s: metric(
            m['tcn_status_counts'].get(s, 0),
            explanation=f"TCN reconciliations with status '{s}'.",
        )
        for s in TCN_STATUSES
    }

    # MO reconciliation status breakdown
    mo_status_metrics = {
        s: metric(
            m['mo_status_counts'].get(s, 0),
            explanation=f"MO reconciliations with status '{s}'.",
        )
        for s in MO_STATUSES
    }

    # Billing rule breakdown
    rule_metrics = {
        r: metric(
            m['rule_counts'].get(r, 0),
            explanation=f"Meter readings where billing rule '{r}' was applied.",
        )
        for r in BILLING_RULES
    }

    # Data source breakdown
    source_metrics = {
        s: metric(
            m['source_counts'].get(s, 0),
            explanation=f"Readings with data source '{s}'.",
        )
        for s in DATA_SOURCES
    }

    return {
        'returns': {
            'total': metric(
                m['total_returns'],
                explanation=(
                    'Total monthly billing returns for this period across selected stations. '
                    'Each injection station produces one return per month.'
                ),
            ),
            'by_status': status_metrics,
            'late_submissions': metric(
                m['late_count'],
                explanation=(
                    'Returns submitted after the cutoff day (day 5 of the following month). '
                    'Late submissions can affect invoicing and reconciliation timelines.'
                ),
            ),
            'late_rate': metric(
                m['late_rate'],
                unit='%',
                explanation='Percentage of returns submitted late — late_count / total_returns × 100.',
            ),
        },

        'energy': {
            'total_billing_mwh': metric(
                m['total_billing_mwh'],
                unit='MWh',
                explanation=(
                    'Total energy billed for this period from EAMonthlyReturn.station_billing_mwh. '
                    'This is the authoritative figure used for NBET invoicing.'
                ),
            ),
            'stream_a_mwh': metric(
                m['stream_a_mwh'],
                unit='MWh',
                explanation=(
                    'Stream A energy — sum of billing_mwh from transformer meter readings. '
                    'Physical field reads submitted by station DSOs each month. '
                    'The authoritative billing figure when within 2% deviation from feeder meters.'
                ),
            ),
            'stream_b_mwh': metric(
                m['stream_b_mwh'],
                unit='MWh',
                explanation=(
                    'Stream B energy — sum of EAFeederTechnicalEnergy.energy_mwh. '
                    'Auto-computed from the technical monitoring system (SCADA/hourly load). '
                    'Used as cross-check against physical reads. '
                    'When Stream A and Stream B differ by >2%, the higher figure is used for billing.'
                ),
            ),
            'stream_a_vs_b_deviation': metric(
                m['stream_deviation_pct'],
                unit='%',
                mode='actual' if m['stream_deviation_pct'] is not None else 'estimated',
                explanation=(
                    'Deviation between Stream A (physical reads) and Stream B (technical system): '
                    '|A − B| / B × 100. '
                    'If this exceeds the deviation threshold (default 2%), the higher of the two '
                    'figures is used for billing (billing_rule = max_applied).'
                ),
            ),
            'by_feeder_type': {
                '33kv_mwh': metric(
                    m['kv33_mwh'],
                    unit='MWh',
                    explanation=(
                        'Stream B energy from 33kV feeders only. '
                        '33kV feeders connect directly to the injection substation transformer '
                        'and are the primary measurement point.'
                    ),
                ),
                '11kv_mwh': metric(
                    m['kv11_mwh'],
                    unit='MWh',
                    explanation=(
                        'Stream B energy from 11kV feeders only. '
                        '11kV feeders are downstream of the 33kV network and provide the '
                        'last-mile distribution energy measurement.'
                    ),
                ),
            },
        },

        'revenue': {
            'total_billing_naira': metric(
                m['total_billing_naira'],
                unit='NGN',
                explanation=(
                    'Total billed Naira for this period — sum of station_billing_naira from monthly returns. '
                    'Calculated as billing_mwh × NBET rate (NGN/MWh) at time of submission.'
                ),
            ),
            'stream_a_naira': metric(
                m['stream_a_naira'],
                unit='NGN',
                explanation=(
                    'Naira value derived from Stream A transformer meter readings, '
                    'using the NBET rate snapshot captured at time of reading.'
                ),
            ),
        },

        'reconciliation': {
            'tcn': {
                'total': metric(
                    m['total_tcn'],
                    explanation=(
                        'Monthly returns that have an associated TCN reconciliation record. '
                        'TCN submits their energy figure via a PIN-protected portal for cross-check.'
                    ),
                ),
                'by_status': tcn_status_metrics,
                'agreed_rate': metric(
                    m['tcn_agreed_rate'],
                    unit='%',
                    explanation=(
                        'Percentage of TCN reconciliations reaching agreed or resolved status: '
                        '(agreed + resolved) / total × 100.'
                    ),
                ),
            },
            'mo': {
                'total': metric(
                    m['total_mo'],
                    explanation=(
                        'Monthly returns with a Market Operator reconciliation record. '
                        'MO compares their invoice figure against KEDCO\'s submitted figure.'
                    ),
                ),
                'by_status': mo_status_metrics,
                'agreed_rate': metric(
                    m['mo_agreed_rate'],
                    unit='%',
                    explanation='Percentage of MO reconciliations in agreed status — agreed / total × 100.',
                ),
            },
        },

        'data_quality': {
            'total_readings': metric(
                m['total_readings'],
                explanation=(
                    'Total individual grid meter readings submitted for this period '
                    '(transformer + feeder meter readings combined).'
                ),
            ),
            'flagged_readings': metric(
                m['flagged_count'],
                explanation=(
                    'Readings flagged for anomalies — deviation exceeded the 2% threshold, '
                    'or other data integrity issues detected. '
                    'Flagged readings require review before billing is finalised.'
                ),
            ),
            'avg_data_completeness': metric(
                m['avg_completeness'],
                unit='%',
                explanation=(
                    'Average data completeness % across all feeder technical energy records: '
                    'days_with_data / total_days_in_period × 100. '
                    'Low completeness indicates gaps in the monitoring system data.'
                ),
            ),
            'feeders_with_gaps': metric(
                m['feeders_with_gaps'],
                explanation=(
                    'Feeders where Stream B technical energy has data gaps '
                    '— monitoring data was incomplete for one or more days in the period.'
                ),
            ),
            'by_billing_rule': rule_metrics,
            'by_data_source':  source_metrics,
        },
    }
