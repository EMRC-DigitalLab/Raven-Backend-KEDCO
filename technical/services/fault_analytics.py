# technical/services/fault_analytics.py
"""
CTO dashboard fault-analytics engine: FRI (Feeder Risk Index) computation
and related aggregates, built on FeederInterruption(source='tcn') -- the
same data backfilled/synced by technical/sync/tcn_interruptions.py.

FRI formula reverse-engineered and verified against TCN's own LIVE sheet
formula (not their prose docs, which contradict both themselves and the
actual working formula) -- pulled directly via value_render_option='FORMULA'
from the 'January FRI Rankings' tab and cross-checked against real computed
scores to 2 decimal places:

    FRI = (Outage_Frequency / MAX(Outage_Frequency) * 100 * 0.3)
        + (Total_Duration_Hrs  / MAX(Total_Duration_Hrs)  * 100 * 0.3)
        + (Max_Load_MW         / MAX(Max_Load_MW)         * 100 * 0.3)
        + (Energy_Not_Supplied / MAX(ENS_MWh)              * 100 * 0.1)

Risk tiers (also verified against real data, not the contradictory prose):
    CRITICAL >= 60, HIGH 40-59.99, MEDIUM 30-39.99, LOW 15-29.99, MINIMAL < 15

IMPORTANT quirk, also verified against real data (not assumed from the
"Load magnitude x Duration = Energy impact" prose, which is wrong): TCN's
own 'Energy_Not_Supplied_MWh' column is NOT load x duration. Its formula
is `=SUMIF('Jan 2026'!E:E, B2, 'Jan 2026'!O:O)` -- a plain SUM of the "Last
Load Recorded (MW)" reading across that feeder's events for the month.
Dimensionally that's just an MW total, not true MWh energy-not-supplied --
but it's what TCN's own penalty figures are actually built on (confirmed:
DANGOTE avg load 12.9MW x 74 events = 954.6, matches their stated ENS of
955.7). Replicated as-is here since the whole point is matching TCN's
real numbers for penalty-accountability credibility, not computing a
"more correct" energy figure they don't actually use.

MAX() denominators use the TRUE population max across all scored feeders --
TCN's own sheet hardcodes a stale row range ($2:$32) that stopped growing
when the feeder list did; verified it doesn't change any real result for
the datasets checked, but there's no reason to copy a bug.

33kV only, matching TCN's own scope exactly (DSO doesn't report 33kV
faults at all, so there's nothing to extend this to for 11kV anyway).
"""
from calendar import monthrange
from collections import defaultdict
from datetime import date

from django.db.models import Max
from django.utils import timezone

from common.models import Feeder
from technical.models import FeederInterruption, HourlyLoad

CRITICAL, HIGH, MEDIUM, LOW, MINIMAL = 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'MINIMAL'


def _risk_category(fri):
    if fri >= 60:
        return CRITICAL
    if fri >= 40:
        return HIGH
    if fri >= 30:
        return MEDIUM
    if fri >= 15:
        return LOW
    return MINIMAL


def _feeder_identity(feeder):
    """Common id/name/band/segment fields, so every endpoint in this file returns the same shape."""
    return {
        'feeder_id': str(feeder.id),
        'feeder_name': feeder.name,
        'feeder_slug': feeder.slug,
        'band': feeder.band.name if feeder.band else None,
        'segment': feeder.pl_segment,
    }


def _feeder_raw_metrics(from_date, to_date):
    """
    Per-33kV-feeder raw metrics for [from_date, to_date] (inclusive), from
    FeederInterruption(source='tcn'). Returns {feeder_id: {...}}.
    Computed in Python (not DB-side duration arithmetic) -- row counts per
    month are in the low thousands, not worth the cross-backend duration
    handling complexity for this volume.
    """
    feeders = {
        f.id: f for f in
        Feeder.objects.filter(voltage_level='33kv', is_onboarded=True).select_related('band')
    }

    rows = FeederInterruption.objects.filter(
        source='tcn',
        feeder_id__in=feeders.keys(),
        occurred_at__date__gte=from_date,
        occurred_at__date__lte=to_date,
    ).values('feeder_id', 'occurred_at', 'restored_at', 'load_at_fault_mw', 'party_responsible')

    metrics = {
        fid: {
            'feeder': f,
            'outage_frequency': 0,
            'total_duration_hrs': 0.0,
            'max_load_mw': 0.0,
            'ens_mwh': 0.0,
            'party_counts': defaultdict(int),
        }
        for fid, f in feeders.items()
    }

    for row in rows:
        m = metrics[row['feeder_id']]
        m['outage_frequency'] += 1
        if row['party_responsible']:
            m['party_counts'][row['party_responsible']] += 1

        duration_hrs = None
        if row['restored_at'] and row['occurred_at']:
            duration_hrs = (row['restored_at'] - row['occurred_at']).total_seconds() / 3600.0
            m['total_duration_hrs'] += duration_hrs

        load_mw = float(row['load_at_fault_mw']) if row['load_at_fault_mw'] is not None else None
        if load_mw is not None:
            m['max_load_mw'] = max(m['max_load_mw'], load_mw)
            # SUM of raw load readings, NOT load x duration -- see module
            # docstring, this replicates TCN's own (mislabeled) formula.
            m['ens_mwh'] += load_mw

    return metrics


def compute_fri_rankings(from_date, to_date):
    """
    Per-feeder FRI for the period, ranked descending. Feeders with zero
    outages in the period are included (matches TCN's own sheet, which
    lists every feeder every month even at FRI=0) so counts stay comparable
    across periods.
    """
    metrics = _feeder_raw_metrics(from_date, to_date)
    if not metrics:
        return []

    max_freq = max((m['outage_frequency'] for m in metrics.values()), default=0) or 1
    max_duration = max((m['total_duration_hrs'] for m in metrics.values()), default=0) or 1
    max_load = max((m['max_load_mw'] for m in metrics.values()), default=0) or 1
    max_ens = max((m['ens_mwh'] for m in metrics.values()), default=0) or 1

    results = []
    for m in metrics.values():
        frequency_risk = m['outage_frequency'] / max_freq * 100
        duration_risk = m['total_duration_hrs'] / max_duration * 100
        severity_risk = m['max_load_mw'] / max_load * 100
        impact_risk = m['ens_mwh'] / max_ens * 100

        fri = (frequency_risk * 0.3) + (duration_risk * 0.3) + (severity_risk * 0.3) + (impact_risk * 0.1)

        results.append({
            **_feeder_identity(m['feeder']),
            'outage_frequency': m['outage_frequency'],
            'total_duration_hrs': round(m['total_duration_hrs'], 2),
            'max_load_mw': round(m['max_load_mw'], 2),
            'ens_mwh': round(m['ens_mwh'], 2),
            'fri_score': round(fri, 2),
            'risk_category': _risk_category(fri),
            'party_breakdown': dict(m['party_counts']),
        })

    results.sort(key=lambda r: r['fri_score'], reverse=True)
    for i, r in enumerate(results, start=1):
        r['rank'] = i
    return results


def compute_risk_distribution(from_date, to_date):
    """Feeder counts / outages / ENS grouped by risk category for the period."""
    rankings = compute_fri_rankings(from_date, to_date)
    total_ens = sum(r['ens_mwh'] for r in rankings) or 1

    buckets = {cat: {'feeder_count': 0, 'total_outages': 0, 'total_ens_mwh': 0.0, 'fri_sum': 0.0}
               for cat in (CRITICAL, HIGH, MEDIUM, LOW, MINIMAL)}
    for r in rankings:
        b = buckets[r['risk_category']]
        b['feeder_count'] += 1
        b['total_outages'] += r['outage_frequency']
        b['total_ens_mwh'] += r['ens_mwh']
        b['fri_sum'] += r['fri_score']

    out = []
    for cat in (CRITICAL, HIGH, MEDIUM, LOW, MINIMAL):
        b = buckets[cat]
        out.append({
            'risk_category': cat,
            'feeder_count': b['feeder_count'],
            'total_outages': b['total_outages'],
            'total_ens_mwh': round(b['total_ens_mwh'], 2),
            'avg_fri': round(b['fri_sum'] / b['feeder_count'], 2) if b['feeder_count'] else 0.0,
            'pct_ens': round(b['total_ens_mwh'] / total_ens * 100, 1),
        })
    return out


def compute_penalty_drivers(from_date, to_date, top_pct=20):
    """Top N% of feeders by FRI descending (Pareto principle, matches TCN's own '16 of 79' framing)."""
    rankings = compute_fri_rankings(from_date, to_date)
    n = max(1, round(len(rankings) * top_pct / 100)) if rankings else 0
    return rankings[:n]


def compute_monthly_summary(year, up_to_month=None):
    """
    One row per month (Jan..up_to_month, default = current month for the
    current year, else Dec for a past year), for the YTD Monthly Cumulative
    Summary table. Plus a YTD_TOTAL row computed across the full
    Jan-up_to_month range (not a naive average of the per-month rows'
    FRI values, since FRI must be recomputed against the full-period
    population max, not averaged after the fact).
    """
    today = timezone.now().date()
    if up_to_month is None:
        up_to_month = today.month if year == today.year else 12

    months = []
    for month in range(1, up_to_month + 1):
        month_start = date(year, month, 1)
        if month_start > today:
            break
        month_end = min(date(year, month, monthrange(year, month)[1]), today)

        rankings = compute_fri_rankings(month_start, month_end)
        months.append({
            'year': year,
            'month': month,
            'total_outages': sum(r['outage_frequency'] for r in rankings),
            'total_duration_hrs': round(sum(r['total_duration_hrs'] for r in rankings), 2),
            'total_ens_mwh': round(sum(r['ens_mwh'] for r in rankings), 2),
            'avg_fri': round(sum(r['fri_score'] for r in rankings) / len(rankings), 2) if rankings else 0.0,
            'critical_feeders': sum(1 for r in rankings if r['risk_category'] == CRITICAL),
            'high_risk_feeders': sum(1 for r in rankings if r['risk_category'] == HIGH),
            'medium_risk_feeders': sum(1 for r in rankings if r['risk_category'] == MEDIUM),
        })

    ytd_end_date = min(date(year, up_to_month, monthrange(year, up_to_month)[1]), today)
    ytd_rankings = compute_fri_rankings(date(year, 1, 1), ytd_end_date)
    ytd_total = {
        'year': year,
        'month': None,
        'total_outages': sum(r['outage_frequency'] for r in ytd_rankings),
        'total_duration_hrs': round(sum(r['total_duration_hrs'] for r in ytd_rankings), 2),
        'total_ens_mwh': round(sum(r['ens_mwh'] for r in ytd_rankings), 2),
        'avg_fri': round(sum(r['fri_score'] for r in ytd_rankings) / len(ytd_rankings), 2) if ytd_rankings else 0.0,
        'critical_feeders': sum(1 for r in ytd_rankings if r['risk_category'] == CRITICAL),
        'high_risk_feeders': sum(1 for r in ytd_rankings if r['risk_category'] == HIGH),
        'medium_risk_feeders': sum(1 for r in ytd_rankings if r['risk_category'] == MEDIUM),
    }

    return {'months': months, 'ytd_total': ytd_total}


def compute_chronic_fault_feeders(year, up_to_month=None):
    """
    Feeders that fault repeatedly month after month, not just once --
    "always going out on fault" means recurring across the YEAR, which a
    single-period FRI snapshot can't show (a feeder could hit CRITICAL once
    off a single bad week and never show up again). This recomputes FRI
    rankings PER MONTH (Jan..up_to_month) and tracks, per feeder, how many
    of those months it landed in CRITICAL/HIGH risk or in the top-20%
    penalty-driver list -- then ranks by recurrence, not by any single
    month's score.
    """
    today = timezone.now().date()
    if up_to_month is None:
        up_to_month = today.month if year == today.year else 12

    tally = {}  # feeder_id -> accumulator
    months_scanned = 0

    for month in range(1, up_to_month + 1):
        month_start = date(year, month, 1)
        if month_start > today:
            break
        month_end = min(date(year, month, monthrange(year, month)[1]), today)
        months_scanned += 1

        rankings = compute_fri_rankings(month_start, month_end)
        penalty_ids = {r['feeder_id'] for r in compute_penalty_drivers(month_start, month_end)}

        for r in rankings:
            fid = r['feeder_id']
            acc = tally.setdefault(fid, {
                'feeder_id': fid,
                'feeder_name': r['feeder_name'],
                'feeder_slug': r['feeder_slug'],
                'band': r['band'],
                'segment': r['segment'],
                'months_critical': 0,
                'months_high': 0,
                'months_critical_or_high': 0,
                'months_as_penalty_driver': 0,
                'total_outages_ytd': 0,
                'total_ens_mwh_ytd': 0.0,
                'fri_by_month': {},
            })
            acc['fri_by_month'][month] = {'fri_score': r['fri_score'], 'risk_category': r['risk_category']}
            acc['total_outages_ytd'] += r['outage_frequency']
            acc['total_ens_mwh_ytd'] += r['ens_mwh']
            if r['risk_category'] == CRITICAL:
                acc['months_critical'] += 1
                acc['months_critical_or_high'] += 1
            elif r['risk_category'] == HIGH:
                acc['months_high'] += 1
                acc['months_critical_or_high'] += 1
            if fid in penalty_ids:
                acc['months_as_penalty_driver'] += 1

    results = []
    for acc in tally.values():
        acc['total_ens_mwh_ytd'] = round(acc['total_ens_mwh_ytd'], 2)
        acc['months_scanned'] = months_scanned
        acc['pct_months_critical_or_high'] = (
            round(acc['months_critical_or_high'] / months_scanned * 100, 1) if months_scanned else 0.0
        )
        results.append(acc)

    # Recurring risk first (most months at CRITICAL/HIGH), tie-broken by
    # how often it was an actual penalty driver, then by raw outage volume.
    results.sort(key=lambda r: (r['months_critical_or_high'], r['months_as_penalty_driver'], r['total_outages_ytd']),
                 reverse=True)
    for i, r in enumerate(results, start=1):
        r['rank'] = i
    return results


def compute_tcn_interruptions_by_feeder(from_date, to_date):
    """TCN Induced Interruption chart: per-feeder count split by party_responsible."""
    metrics = _feeder_raw_metrics(from_date, to_date)
    out = []
    for m in metrics.values():
        if m['outage_frequency'] == 0:
            continue
        out.append({
            **_feeder_identity(m['feeder']),
            'total_interruptions': m['outage_frequency'],
            'tcn_forced_outage': m['party_counts'].get('TCN', 0),
            'kedco_responsibility': m['party_counts'].get('DISCO', 0),
            'genco_responsibility': m['party_counts'].get('GENCO', 0),
        })
    out.sort(key=lambda r: r['feeder_name'])
    return out


def compute_feeder_compliance(from_date, to_date):
    """33KV Feeder Compliance Status: total interruption count per feeder, descending."""
    metrics = _feeder_raw_metrics(from_date, to_date)
    out = [
        {
            **_feeder_identity(m['feeder']),
            'total_interruptions': m['outage_frequency'],
        }
        for m in metrics.values() if m['outage_frequency'] > 0
    ]
    out.sort(key=lambda r: r['total_interruptions'], reverse=True)
    return out


def _segment_tariffs(year, month):
    """
    ₦/kWh per P&L segment for (year, month), same source and same
    current-month-missing fallback as TMOService.get_gcr() (most recent
    available month if the target for this exact month isn't set yet).
    """
    from tmo.models import TMOMonthlySegmentTarget

    targets = {t.segment: float(t.average_tariff_per_kwh)
               for t in TMOMonthlySegmentTarget.objects.filter(year=year, month=month)}
    if not targets:
        latest = (
            TMOMonthlySegmentTarget.objects
            .order_by('-year', '-month')
            .values_list('year', 'month')
            .first()
        )
        if latest:
            targets = {t.segment: float(t.average_tariff_per_kwh)
                       for t in TMOMonthlySegmentTarget.objects.filter(year=latest[0], month=latest[1])}
    return targets


def compute_fault_financial_exposure(from_date, to_date):
    """
    First-pass estimate of monetary exposure per responsible party for the
    period -- "what KEDCO would be paying" / "what TCN would be paying" for
    that month's faults. NOT an official penalty figure (no TCN/NERC penalty
    formula exists anywhere in this codebase to verify against) -- this is
    Raven's own estimate built from data already trusted elsewhere:

        exposure = (load_at_fault_mw x duration_hrs) x 1000 x segment_tariff_per_kwh

    i.e. true energy not supplied for that specific outage (not TCN's
    SUM(load) quirk used in the FRI engine -- that's the right basis for
    matching TCN's own risk scores, but the wrong basis for a real Naira
    estimate) x that feeder's P&L segment tariff, attributed to whichever
    party (DISCO/TCN/GENCO) the sheet's own Party Responsible column names
    for that event.

    Rows missing either restored_at or load_at_fault_mw can't be priced
    (no duration or no load reading) and are counted separately, not
    silently dropped or guessed at.
    """
    tariffs = _segment_tariffs(from_date.year, from_date.month)

    rows = FeederInterruption.objects.filter(
        source='tcn',
        feeder__voltage_level='33kv',
        occurred_at__date__gte=from_date,
        occurred_at__date__lte=to_date,
    ).values('occurred_at', 'restored_at', 'load_at_fault_mw', 'party_responsible', 'feeder__pl_segment')

    faults_by_party = {'DISCO': 0, 'TCN': 0, 'GENCO': 0, 'UNSPECIFIED': 0}
    exposure_by_party = {'DISCO': 0.0, 'TCN': 0.0, 'GENCO': 0.0, 'UNSPECIFIED': 0.0}
    total_faults = 0
    unpriceable_faults = 0

    for row in rows:
        total_faults += 1
        party = row['party_responsible'] or 'UNSPECIFIED'
        if party not in faults_by_party:
            party = 'UNSPECIFIED'
        faults_by_party[party] += 1

        if not row['restored_at'] or row['load_at_fault_mw'] is None:
            unpriceable_faults += 1
            continue

        duration_hrs = (row['restored_at'] - row['occurred_at']).total_seconds() / 3600.0
        energy_mwh = float(row['load_at_fault_mw']) * duration_hrs
        tariff = tariffs.get(row['feeder__pl_segment'], 0.0)
        exposure_by_party[party] += energy_mwh * 1000 * tariff

    return {
        'period': {'from': str(from_date), 'to': str(to_date)},
        'tariffs_used': tariffs,
        'total_faults': total_faults,
        'unpriceable_faults': unpriceable_faults,
        'faults_by_party': faults_by_party,
        'estimated_exposure_naira_by_party': {k: round(v, 2) for k, v in exposure_by_party.items()},
        'kedco_estimated_exposure_naira': round(exposure_by_party['DISCO'], 2),
        'tcn_estimated_exposure_naira': round(exposure_by_party['TCN'], 2),
        'genco_estimated_exposure_naira': round(exposure_by_party['GENCO'], 2),
    }


def compute_peak_load_ranking(from_date, to_date):
    """
    Highest peak load per 33kV feeder for any selected period (not
    hardcoded to a single month -- from_date/to_date come from the same
    global-filter resolve_date_params() used everywhere else in the app).
    """
    peaks = (
        HourlyLoad.objects
        .filter(feeder__voltage_level='33kv', feeder__is_onboarded=True, date__gte=from_date, date__lte=to_date)
        .values('feeder_id', 'feeder__name', 'feeder__slug', 'feeder__band__name', 'feeder__pl_segment')
        .annotate(highest_peak_load_mw=Max('load_mw'))
        .order_by('feeder__name')
    )
    return [
        {
            'feeder_id': str(p['feeder_id']),
            'feeder_name': p['feeder__name'],
            'feeder_slug': p['feeder__slug'],
            'band': p['feeder__band__name'],
            'segment': p['feeder__pl_segment'],
            'highest_peak_load_mw': float(p['highest_peak_load_mw']) if p['highest_peak_load_mw'] is not None else 0.0,
        }
        for p in peaks
    ]
