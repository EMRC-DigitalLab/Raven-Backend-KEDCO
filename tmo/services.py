# tmo/services.py
import calendar
from collections import defaultdict
from datetime import date, timedelta

from django.db.models import Avg, Count, Max, Q, Sum

from commercial.models import (
    TMOBillingEfficiency,
    TMOCollectionTarget,
    TMOFeederTarget,
)
from commercial.models import CommercialCustomer
from common.models import Band, Feeder, FeederCouplingEvent, FeederSupplyRelationship
from technical.models import DailyHoursOfSupply, EnergyDelivered, FeederInterruption, HourlyLoad
from technical.utils.energy_utils import (
    DAILY_BALLOON_LIMIT,
    calculate_energy_delivered,
    calculate_energy_delivered_per_feeder,
)
from tmo.constants import STANDARD_DAILY_FORECAST_GWH
from tmo.models import TMODailyAllocation, TMOFeederSupplyTarget, TMOIncident, TMOMonthlySegmentTarget, TMONetworkConfig, TMONetworkDispatch, TMOSupplyHoursTarget


# Confirmed phantom duplicate feeder records (same physical feeder re-onboarded
# under a second substation with no real meter history). Excluded here rather
# than deleted because each has real HourlyLoad/billing/interruption history
# attached that a straight delete would destroy — proper merge is a separate,
# larger data-cleanup effort.
DUPLICATE_FEEDER_SLUGS = ['KN-DAK-RAN', 'KN-DAK-GEZ', 'KN-NAI-DAW']

# Feeders TCN's own "Daily Energy Allocation" accounting counts as a separate
# line item even though they are physically downstream of another 33KV bulk
# feeder. Reverse-engineered by testing TCN's per-feeder formula against their
# own source sheets (Aug 2026, 6 days, 210 feeders): every other downstream
# child's energy is subtracted from its 33KV parent's raw meter reading before
# the child's own row is added back in (a wash for the grand total — see
# _bulk_feeder_ids), but Dundu and Nuhu Sunusi are the sole confirmed
# exceptions TCN's own formula does NOT subtract from NNPC/Dutse respectively.
# Matching TCN's total requires adding them on top of the bulk population
# rather than excluding them as an ordinary downstream child would be.
# METRO is here for a different reason: Raven's FeederSupplyRelationship
# tags it as downstream of Tamburawa Water Works, but TCN's own sheet tags
# its Associated 33KV Feeder as "OUT OF SUPPLY" (not a real parent) — so it
# is a true orphan, not a subtracted child, and must not be excluded by the
# downstream-of-bulk-parent rule in _bulk_feeder_ids.
# NAKOWA / FUNTUA TEXTILE MILL / FUNTUA WATER WORKS / MAI RUWA / TOWN /
# DUTSEN REME / JABIRI / INDUSTRIAL / BCGA / GALADIMA / BICHI TOWN are here for
# a third reason, found 2026-08-08 via a full "which of TCN's 210 ground-truth
# rows never gets touched by any Raven bulk feeder or its children" audit:
# each one's nominal 33KV parent (TEXTILE, KATSINA ROAD, MAMMAN NASIR, HON.
# ABUBAKAR) either has ZERO EnergyDelivered rows in Raven at all (no meter
# reading ever synced) or — for HON. ABUBAKAR specifically — its own row
# already matches TCN exactly WITHOUT Bichi Town, proving Bichi Town's energy
# is not embedded in it. Either way, the normal "child energy is embedded in
# the parent's gross reading" assumption fails for these, so their real,
# well-metered consumption (confirmed row-by-row against TCN's own sheet) was
# being silently dropped from the bulk total entirely. Add them directly.
# AJASA / IBRAHIM TAIWO / KOFAR NASSARAWA are here for the same reason as
# DAN'AGUNDI 1's own never-subtract exception above: confirmed DAN'AGUNDI 1's
# raw reading alone already matches TCN's reported figure with no children
# added, meaning these three are never embedded in it and must be counted as
# their own separate line items, same as TCN does.
STANDALONE_BULK_EQUIVALENT_SLUGS = [
    'KN-JOG-DUN', 'KN-DUT-NUH', 'KN-TAM2-MET',
    'KN-TEX-NAK', 'KN-TEX-FUN', 'KN-TEX-FUN2', 'KN-TEX-MAI',
    'KN-KAT-TOW', 'KN-KAT-DUT', 'KN-KAT-JAB', 'KN-KAT-IND',
    'KN-DAN-AJA', 'KN-DAN-IBR', 'KN-DAN-KOF',
    'KN-MAL2-BCG', 'KN-MAL2-GAL',
    'KN-BIC-BIC',
]

# Feeders TCN's own formula GENUINELY subtracts from a parent's raw reading —
# confirmed 2026-08-08 by reading the actual SUMIF formula text (not just the
# "Associated 33KV Feeder" tag) for every one of the 210 rows in TCN's ground-
# truth workbook. This matters because of a quirk in how that sheet is built:
# each parent's SUMIF only searches a specific row range for children to
# subtract — most parents search rows 15-145 (the 11KV retail rows) and can
# NEVER reach a child sitting in rows ~146-227 (where the 33KV-tagged rows
# live), no matter what that child's own "Associated 33KV Feeder" tag says.
# Only a handful of parents (Small Scale, Tamburawa Water Works, Wudil) use an
# extended range that actually reaches that far, so only children whose
# parent is one of those are truly subtracted.
#
# Previously this was inferred from Raven's own FeederSupplyRelationship data
# (physical supply topology) — reasonable-sounding, but WRONG for 3 of the 7
# candidates it flagged, because physical topology has nothing to do with
# which row range a spreadsheet formula happens to search:
#   - Dawaki: Raven's topology says downstream of Zaria Road, but TCN's own
#     tag for Dawaki's row is literally "OUT OF SUPPLY" — TCN treats it as a
#     fully independent orphan, not anyone's child, full stop.
#   - Dawanau (KN-DAN2-DAW / KN-BOK-DAW — NOT "Dawanau Industrial", a
#     different feeder): tagged as Kurna's child, but Kurna's SUMIF only
#     searches rows 15-145 and Dawanau sits at row 159 — never reached.
#   - Badume: tagged as "Hon. Abubakar"'s child, but no row in the sheet
#     exactly matches that name (only "Hon. Abubakar Kabir" exists, a
#     different row) — the SUMIF has no target to hit, so nothing is ever
#     subtracted.
# All three were being wrongly excluded from Raven's bulk total as a result.
# Confirmed correct (searched a wide-enough range in TCN's own formula):
# Rangaza/Gezawa (Small Scale), Dr Jamil Gwamna (Tamburawa Water Works),
# Gaya (Wudil).
CONFIRMED_SUBTRACTED_CHILD_SLUGS = [
    'KN-SMA-RAN',   # Rangaza <- Small Scale
    'KN-SMA-GEZ',   # Gezawa <- Small Scale
    'KN-TAM2-DRJ', 'KN-TAM-DRJ',  # Dr Jamil Gwamna <- Tamburawa Water Works
    'KN-WUD3-GAY',  # Gaya <- Wudil
]

# 33KV parents individually verified (2026-08-08, against TCN's own ground-
# truth workbook) to subtract NONE of their topology-listed children at all
# — their own raw reading already equals TCN's reported figure with nothing
# removed. Same pattern as DAN'AGUNDI 1: Gano (37.30=37.30), Flour Mills
# (123=123), Hon. Abubakar (26.84=26.84, verified against Bichi Town).
PARENT_NEVER_SUBTRACTS_SLUGS = {'KN-DAN-DAN', 'KN-WUD2-GAN', 'KN-DAK-FLO', 'KS-KAN-HON'}

# Ignore negative-net dips smaller than this — floating-point/rounding-scale
# noise, not a real broken-children-list signal worth surfacing to TMO.
NEGATIVE_NET_MIN_ABS_MWH = 0.5

# Children individually verified to never be subtracted from ANY parent,
# regardless of what FeederSupplyRelationship's topology claims. Excluding
# Dundu and Asian Plastic gives an EXACT match for NNPC (50.30=50.30);
# excluding Nuhu Sunusi gives an EXACT match for Dutse (48.55=48.55).
CHILD_NEVER_SUBTRACTED_SLUGS = {'KN-JOG-DUN', 'KN-DUT-NUH', 'asian-plastic'}

# Children confirmed to have NO real energy at all — TCN's own row for these
# reads exactly 0 on every single day checked (2026-08-01 through 09), not
# just an occasional gap. Different fact from CHILD_NEVER_SUBTRACTED_SLUGS
# above: Dundu and Nuhu Sunusi genuinely deliver real energy and are only
# exempted from being subtracted from a parent, but still correctly counted
# at their own 11KV value. Asian Plastic has no real value to count in the
# first place, so it's excluded from the 11KV addition entirely, not just
# the subtraction step.
CHILD_ZERO_ENERGY_SLUGS = {'asian-plastic'}

# 33KV bulk parents TCN's own sheet tags 'Data unavailable'/'Unclassified'
# in the Segment column — confirmed 2026-08-10 against the "11KV + 33KV
# Combined" sheet. TCN excludes these from MDI/MDNI/Regions entirely (their
# own contribution, not their children — the children remain individually
# and correctly classified). Their children keep being subtracted from them
# as normal (that part is independently verified correct); only the
# parent's own NET value is kept out of every segment total here, matching
# TCN's own classification gap instead of forcing it into a segment TCN
# itself doesn't put it in.
PARENT_UNCLASSIFIED_BY_TCN_SLUGS = {'KN-KUM-SHA'}  # Sharada Bata

# Feeders that have NO real EnergyDelivered data of their own (never synced —
# not "broken meter", genuinely nothing to sync) AND whose own row in TCN's
# ground-truth workbook is blank/zero, confirmed by a full row-by-row audit
# 2026-08-08. Excluded here because _classify_feeders() falls back to a
# HourlyLoad-based balloon estimate for ANY onboarded feeder with zero
# EnergyDelivered rows — which fabricates a nonzero contribution these
# feeders don't actually have (confirmed: Katsina Road's balloon estimate was
# 61 MWh, Textile's 60.4 MWh, Malumfashi's 25.7 MWh, on a day TCN's own sheet
# shows blank/near-zero for all of them). This directly caused Raven's total
# to run ~150 MWh/day (4-6%) over TCN's real figure.
#
# Katsina Road, Textile, and Mamman Nasir specifically are also each other's
# special case: their real energy isn't zero at all — it's fully present in
# TCN's sheet, just filed under their downstream 11KV children's own rows
# instead of a parent rollup (TCN's sheet has no parent row value for any of
# these three). Those children are now counted directly via
# STANDALONE_BULK_EQUIVALENT_SLUGS above, so excluding the empty parent here
# avoids adding a second, fabricated number on top of the real one.
#
# Superseded 2026-08-08: an earlier version of this list was tested empirically
# and found to make the total worse — but that test predates both the
# Textile/Katsina Road/Mamman Nasir standalone-children fix above and the
# discovery that the parent's contribution wasn't 0, it was a fabricated
# balloon estimate. Re-tested with both those fixes in place: excluding these
# now closes the gap rather than widening it. If re-litigating this, re-run
# the balloon-estimate check in _bulk_classification() first — a parent with
# no EnergyDelivered rows will always get a nonzero estimate as long as it
# has any HourlyLoad data, whether or not that estimate reflects reality.
NOT_TRACKED_BY_TCN_SLUGS = [
    'KS-FUN-KAT',  # Katsina Road — real energy now counted via its children
    'KS-FUN-TEX',  # Textile — real energy now counted via its children
    'KS-FUN-MAM',  # Mamman Nasir — real energy now counted via its children
    'KS-FUN-MAL',  # Malumfashi — no real data, TCN's own row is ~0
    'KS-FUN-DAN',  # Dandume — no real data, TCN's own row is ~0
    'KS-FUN-FAS',  # Faskari — no real data, TCN's own row is ~0
    'KN-DAN4-NBC',  # NB Ceramic — no real data, TCN's own row is blank
    # Majiya and Danzabuwa are different from the six above: both have real,
    # solid meter_difference data in Raven (confirmed genuine, not a broken
    # meter) but do not appear ANYWHERE in TCN's own 210-row ground-truth
    # workbook under any name or spelling — checked exhaustively 2026-08-08,
    # unlike every other apparent "unmatched" case this session (Rumawa,
    # Mai Adua, Ajiwa, Tamburawa, Hon. Abubakar, Rice Mills, Polytechnic —
    # all turned out to be spelling/naming variants of a real TCN row).
    # Excluded here to match TCN's own reported total exactly, per this
    # reconciliation's standing rule (align with TCN's own workbook even
    # where Raven's other data disagrees — same precedent used for the 16
    # FeederSupplyRelationship overrides above). This is a real, verified
    # trade-off, not a data-quality fix: excluding them means Raven's Daily
    # Energy Allocation total will run ~25-100 MWh/day BELOW the true total
    # system energy on any day these two have supply, in exchange for
    # matching TCN's reported figure exactly. If TCN ever adds these to
    # their own file, remove the exclusion.
    'majiya',
    'danzabuwa',
]

# Feeders confirmed against TCN's own ground-truth workbook to have a
# genuine, real reading of exactly 0 on at least some days — Musawa, Rijiyar
# Zaki, and Spanish 1 report 0 every day; Dan'Agundi 1 alternates 0/1 MWh,
# both real values, not a broken meter. Trusted here so the meter-vs-balloon
# classifier never substitutes a fabricated HourlyLoad estimate for one of
# these on a day their real reading happens to be 0, no matter what
# calculation_method that day's row carries.
#
# This exists because calculation_method-based trust (sheet_variance/
# manual_entry, see _classify_feeders below) is NOT durable: a `manual_entry`
# override written directly to the database gets silently reverted back to
# `meter_difference` the next time an automated `force=True` sync runs and
# recomputes that same (feeder, date) from the live sheet — confirmed to
# happen TWICE this session for Musawa/Rijiyar Zaki/Spanish 1 specifically.
# Re-writing the override each time doesn't fix it; it just gets undone again
# on the next sync cycle. Trusting by SLUG here instead is permanent and
# survives any number of future resyncs, since it doesn't depend on which
# calculation_method the row happens to carry today.
CONFIRMED_ZERO_TRUSTED_SLUGS = ['KS-KAN-MUS', 'KN-KUM-RIJ', 'KN-KUM-SPA', 'KN-DAN-DAN']


# ── Methodology tooltips ─────────────────────────────────────────────────────
# Shown on hover in the frontend so users can see exactly how a number was
# derived, without reading code or asking an engineer. These spell out the
# real calculation — data source, what's included/excluded, and why — in
# plain sentences rather than code terms, but nothing is glossed over. Each
# TMOService method that feeds a chart includes 'methodology' in its
# response, pulled from here — edit the text in ONE place, not scattered
# across every method.
METHODOLOGY = {
    'daily_energy': (
        "Each day's total is calculated from real meter readings at every major "
        "(33KV) supply feeder, comparing the reading at the start and end of the "
        "day. Feeders that are physically downstream of another feeder already "
        "counted are left out, so the same electricity is never counted twice. "
        "Today is never shown as a final number, since a day's total can only be "
        "calculated once tomorrow's reading exists. The target line is the "
        "month's agreed allocation split across the days of the month."
    ),
    'daily_allocation': (
        "Comes directly from TCN's own hour-by-hour figures for KEDCO. Expected "
        "is what TCN allocated that hour, Actual is what KEDCO's network drew, "
        "and the gap between them is the variance. Today isn't shown until the "
        "day is complete."
    ),
    'supply_compliance': (
        "For each feeder, we calculate its average hours of power per day over "
        "the period and compare it to its target hours. The target used is the "
        "most specific one available: a target set for that exact feeder, then a "
        "target set for its customer category, then a default minimum for its "
        "band. Feeders are ranked from worst to best."
    ),
    'compliance_summary': (
        "The same supply-hours-versus-target calculation as Feeder Compliance, "
        "grouped by customer category instead of listed feeder by feeder, "
        "showing how many feeders fall into each performance level."
    ),
    'minigrids': (
        "Haske Solar's own metered energy for the period, compared against its "
        "energy target for the same period."
    ),
    'pear': (
        "Every feeder is tagged as a major paying customer (MD) or general "
        "customer (Non-MD). MD share is that group's metered energy divided by "
        "total metered energy for the same period — not scaled to any other "
        "total. Yesterday uses that day's numbers directly; month-to-date is "
        "the average of each individual day's MD share, not one ratio over the "
        "whole month, since a straight monthly ratio can be skewed by a single "
        "high- or low-volume day. Compared against the target mix set for the "
        "month."
    ),
    'volatility': (
        "Splits energy three ways: MDI, MDNI, and Regions. Each category's "
        "share is that category's own metered energy divided by the sum of "
        "all three — this total is the classified energy only, and can run "
        "slightly below the full Daily Energy Allocation total when some "
        "feeders aren't yet tagged to a category. The Volatility Index "
        "compares yesterday's share for each category to its month-to-date "
        "average, and flags a meaningful shift."
    ),
    'energy_by_voltage': (
        "For each customer category and day, every 33KV feeder's own reading "
        "has its downstream 11KV feeders' energy subtracted out, then each of "
        "those 11KV feeders is counted separately at its own full value — "
        "nothing is scaled to force a match elsewhere. Voltage level comes "
        "from each feeder's own record, not from its name. This total can "
        "differ slightly from the Daily Energy Allocation total, since not "
        "every 33KV feeder's downstream links are confirmed with full "
        "certainty yet — that's expected, not an error."
    ),
    'incidents': (
        "Every logged feeder fault in the period, with financial loss calculated "
        "from the feeder's average load, how long the fault lasted, and the "
        "tariff rate for its customer category. Only genuine KEDCO faults are "
        "counted. Load shedding, TCN transmission faults, maintenance, and "
        "permit outages are excluded, since they're planned or outside KEDCO's "
        "own network."
    ),
    'energy_by_segment': (
        "Uses the same method as PEAR: each customer category's share of energy "
        "is calculated, then applied to the network's real total, and compared "
        "against that category's target for the month. The gap shown is target "
        "minus actual."
    ),
    'gcr': (
        "Uses the same actual energy figures as P&L Target Realization Deficit, "
        "and multiplies the energy gap by each category's average tariff rate to "
        "estimate the billing value lost."
    ),
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _classify_feeders(feeder_ids, from_date, to_date):
    """
    Split feeder_ids into (meter_ids, balloon_ids) for the given period.
    Mirrors the logic in calculate_energy_delivered_per_feeder:
      meter  — feeder has readings AND max daily value <= DAILY_BALLOON_LIMIT
      balloon — any day exceeds the limit, or feeder has no readings at all
                → system estimate (avg_load × supply_hours) will be used

    The max_daily > 0 requirement exists to catch a broken/disconnected
    meter that always diffs to exactly 0 (a real bug found 2026-08-05 —
    see fix #7 in project memory) — those should fall back to the load
    estimate rather than being trusted. But it has a false-positive: a
    feeder whose value is a genuine, explicitly-sourced 0 (calculation_
    method='sheet_variance' — TCN's own sheet says zero that day, not a
    Raven-computed diff) is NOT the same failure mode, and was wrongly
    getting overridden by a fabricated non-zero estimate (confirmed
    2026-08-08: Dawaki/Dawanau/Badume genuinely report 0 energy across
    all 6 days in TCN's own ground truth, but Raven's estimate fallback
    invented ~54, ~3, and ~2.8 MWh for them respectively). A feeder with
    at least one directly-sourced sheet_variance row is trusted even at
    max_daily == 0; an all meter_difference feeder stuck at 0 still falls
    back to the estimate, preserving the original fix's protection.

    A feeder in CONFIRMED_ZERO_TRUSTED_SLUGS is trusted regardless of
    calculation_method — see that constant's comment for why trusting by
    slug, not by calculation_method, is required for those specific feeders
    to survive future automated resyncs.
    """
    always_trusted_ids = set(
        Feeder.objects.filter(slug__in=CONFIRMED_ZERO_TRUSTED_SLUGS, id__in=feeder_ids)
        .values_list('id', flat=True)
    )
    stats = (
        EnergyDelivered.objects
        .filter(feeder_id__in=feeder_ids, date__gte=from_date, date__lte=to_date)
        .values('feeder_id')
        .annotate(
            max_daily=Max('energy_mwh'),
            cnt=Count('id'),
            trusted_zero_cnt=Count('id', filter=Q(calculation_method__in=['sheet_variance', 'manual_entry'])),
        )
    )
    meter_ids = set()
    for row in stats:
        max_daily = float(row['max_daily'] or 0)
        cnt = int(row['cnt'] or 0)
        trusted = int(row['trusted_zero_cnt'] or 0) > 0 or row['feeder_id'] in always_trusted_ids
        if cnt > 0 and max_daily <= DAILY_BALLOON_LIMIT and (max_daily > 0 or trusted):
            meter_ids.add(row['feeder_id'])
    balloon_ids = set(feeder_ids) - meter_ids
    return meter_ids, balloon_ids


def _per_feeder_daily_map(feeder_ids, from_date, to_date):
    """
    {feeder_id: {date_str: mwh}} using the same meter+balloon classification
    as _bulk_daily_map/_daily_energy_breakdown, but keeping each feeder's
    contribution separate instead of summing them. Needed wherever energy
    must be attributed to a specific feeder — e.g. subtracting a 33KV
    parent's own downstream 11KV children — rather than just totalled.
    """
    if not feeder_ids:
        return {}
    meter_ids, balloon_ids = _classify_feeders(feeder_ids, from_date, to_date)
    result = defaultdict(dict)
    if meter_ids:
        for row in (
            EnergyDelivered.objects
            .filter(feeder_id__in=meter_ids, date__gte=from_date, date__lte=to_date)
        ):
            result[row.feeder_id][str(row.date)] = float(row.energy_mwh)
    if balloon_ids:
        for row in (
            HourlyLoad.objects
            .filter(feeder_id__in=balloon_ids, date__gte=from_date, date__lte=to_date, load_mw__gt=0)
            .values('feeder_id', 'date').annotate(avg_load=Avg('load_mw'), supply_hours=Count('hour'))
        ):
            result[row['feeder_id']][str(row['date'])] = float(row['avg_load'] or 0) * int(row['supply_hours'] or 0)
    return dict(result)


OUTLIER_BASELINE_DAYS = 60
OUTLIER_MIN_BASELINE_POINTS = 10
OUTLIER_Z_THRESHOLD = 3.5
OUTLIER_MIN_ABS_DEVIATION_MWH = 1.0
OUTLIER_FLAT_BASELINE_Z = 99.0  # stand-in "z-score" used when MAD == 0, so callers never see infinity


def _median(values):
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _outlier_candidates(feeder_ids, from_date, to_date, baseline_days=OUTLIER_BASELINE_DAYS):
    """
    Per-feeder relative outlier check: flags feeder-days in [from_date, to_date]
    whose RAW EnergyDelivered reading falls far outside that SAME feeder's own
    historical normal range. Unlike the fixed DAILY_BALLOON_LIMIT=500 safety
    net (a single global absolute threshold), this catches smaller-magnitude
    anomalies scaled to each feeder's own normal size — e.g. a feeder whose
    typical range is 1-2 MWh suddenly reading 82.

    Baseline = that feeder's own raw daily readings over `baseline_days`
    immediately BEFORE from_date — never including any day being evaluated,
    so a bad day can't hide inside its own baseline. Uses median + MAD
    (median absolute deviation) rather than mean/stddev, since a single
    already-known-corrupted historical day (several found this session)
    would otherwise blow out a mean-based baseline and mask future real
    outliers; median/MAD is far less sensitive to a handful of bad points.

    Reads RAW values directly, deliberately bypassing the meter/balloon
    classification _per_feeder_daily_map applies — that classification
    already replaces the biggest anomalies with an estimate before this
    check would ever see them, which would defeat the purpose here.

    Returns a list of dicts: feeder_id, date, observed_mwh,
    baseline_median_mwh, baseline_mad_mwh, modified_z_score, baseline_points.
    """
    if not feeder_ids:
        return []

    baseline_start = from_date - timedelta(days=baseline_days)
    baseline_end = from_date - timedelta(days=1)

    baseline_values = defaultdict(list)
    for row in EnergyDelivered.objects.filter(
        feeder_id__in=feeder_ids, date__gte=baseline_start, date__lte=baseline_end
    ).values('feeder_id', 'energy_mwh'):
        baseline_values[row['feeder_id']].append(float(row['energy_mwh']))

    review_rows = defaultdict(dict)
    for row in EnergyDelivered.objects.filter(
        feeder_id__in=feeder_ids, date__gte=from_date, date__lte=to_date
    ).values('feeder_id', 'date', 'energy_mwh'):
        review_rows[row['feeder_id']][row['date']] = float(row['energy_mwh'])

    candidates = []
    for fid, by_date in review_rows.items():
        baseline = baseline_values.get(fid, [])
        if len(baseline) < OUTLIER_MIN_BASELINE_POINTS:
            continue
        median = _median(baseline)
        mad = _median([abs(v - median) for v in baseline])

        for d, value in by_date.items():
            deviation = value - median
            if mad > 0:
                mod_z = 0.6745 * deviation / mad
            elif median > 0 and abs(deviation) >= OUTLIER_MIN_ABS_DEVIATION_MWH:
                # flat but non-zero baseline (e.g. a steady 24.0/day for 60 days)
                # -- any real change from an established norm is worth surfacing
                mod_z = OUTLIER_FLAT_BASELINE_Z if deviation > 0 else -OUTLIER_FLAT_BASELINE_Z
            else:
                # median == 0: the feeder was dormant/unreported for its whole
                # baseline window, so there's no real "normal" to compare
                # against yet -- a feeder simply coming online isn't an outlier.
                mod_z = 0.0

            if abs(mod_z) >= OUTLIER_Z_THRESHOLD and abs(deviation) >= OUTLIER_MIN_ABS_DEVIATION_MWH:
                candidates.append({
                    'feeder_id': fid,
                    'date': d,
                    'observed_mwh': round(value, 2),
                    'baseline_median_mwh': round(median, 2),
                    'baseline_mad_mwh': round(mad, 2),
                    'modified_z_score': round(mod_z, 2),
                    'baseline_points': len(baseline),
                })

    return candidates


def _energy_feeder_ids(feeder_ids):
    """
    Remove 33KV feeders that actively supply downstream 11KV feeders.
    Those are upstream/bulk meters — counting them alongside their downstream
    11KV feeders would double-count the same energy.
    Only 33KV feeders with direct customer connections are kept.
    """
    upstream_ids = set(
        FeederSupplyRelationship.objects
        .filter(supplier_feeder_id__in=feeder_ids, status='active')
        .values_list('supplier_feeder_id', flat=True)
        .distinct()
    )
    return [fid for fid in feeder_ids if fid not in upstream_ids]


def _daily_energy_breakdown(feeder_ids, from_date, to_date):
    """
    Returns {date_str: total_mwh} for the set of feeders using the same
    balloon + system-estimate logic as calculate_energy_delivered_per_feeder:
      - meter feeders  → sum actual EnergyDelivered per day
      - balloon feeders → avg_load × supply_hours from HourlyLoad per day
    """
    if not feeder_ids:
        return {}

    meter_ids, balloon_ids = _classify_feeders(feeder_ids, from_date, to_date)
    daily = defaultdict(float)

    if meter_ids:
        for row in (
            EnergyDelivered.objects
            .filter(feeder_id__in=meter_ids, date__gte=from_date, date__lte=to_date)
            .values('date').annotate(total=Sum('energy_mwh'))
        ):
            daily[str(row['date'])] += float(row['total'] or 0)

    if balloon_ids:
        for row in (
            HourlyLoad.objects
            .filter(feeder_id__in=balloon_ids, date__gte=from_date, date__lte=to_date, load_mw__gt=0)
            .values('feeder_id', 'date')
            .annotate(avg_load=Avg('load_mw'), supply_hours=Count('hour'))
        ):
            daily[str(row['date'])] += float(row['avg_load'] or 0) * int(row['supply_hours'] or 0)

    return dict(daily)


def _feeder_energy_by_day(feeder_id, from_date, to_date):
    """
    Returns {date_str: mwh} for a single feeder with balloon+system logic.
    See _classify_feeders() for why a genuine, directly-sourced zero
    (calculation_method='sheet_variance') is trusted even though
    max_daily == 0 — that's not the same as a broken meter stuck at 0. Also
    see CONFIRMED_ZERO_TRUSTED_SLUGS for the slug-based trust override.
    """
    stats = EnergyDelivered.objects.filter(
        feeder_id=feeder_id, date__gte=from_date, date__lte=to_date
    ).aggregate(
        max_daily=Max('energy_mwh'),
        cnt=Count('id'),
        trusted_zero_cnt=Count('id', filter=Q(calculation_method__in=['sheet_variance', 'manual_entry'])),
    )

    always_trusted = Feeder.objects.filter(
        id=feeder_id, slug__in=CONFIRMED_ZERO_TRUSTED_SLUGS
    ).exists()

    max_daily = float(stats['max_daily'] or 0)
    use_meter = (
        int(stats['cnt'] or 0) > 0 and
        max_daily <= DAILY_BALLOON_LIMIT and
        (max_daily > 0 or int(stats['trusted_zero_cnt'] or 0) > 0 or always_trusted)
    )

    if use_meter:
        return {
            str(r.date): float(r.energy_mwh)
            for r in EnergyDelivered.objects.filter(
                feeder_id=feeder_id, date__gte=from_date, date__lte=to_date
            )
        }

    return {
        str(row['date']): float(row['avg_load'] or 0) * int(row['supply_hours'] or 0)
        for row in (
            HourlyLoad.objects
            .filter(feeder_id=feeder_id, date__gte=from_date, date__lte=to_date, load_mw__gt=0)
            .values('date').annotate(avg_load=Avg('load_mw'), supply_hours=Count('hour'))
        )
    }

def _pct(numerator, denominator):
    try:
        return round(float(numerator) / float(denominator) * 100, 2)
    except (ZeroDivisionError, TypeError, ValueError):
        return 0.0


def _var_pct(actual, target):
    try:
        return round((float(actual) - float(target)) / float(target) * 100, 2)
    except (ZeroDivisionError, TypeError, ValueError):
        return 0.0


def _compliance(pct):
    if pct > 105:
        return 'exceeding'
    if pct >= 95:
        return 'on_target'
    if pct >= 85:
        return 'below_target'
    if pct >= 75:
        return 'poor'
    return 'critical'


def resolve_date_params(request):
    """
    Parse query params → (from_date, to_date).
    Priority: from_date+to_date > month > date > T-1 (yesterday).

    to_date is ALWAYS clamped to yesterday, no matter which branch produced
    it. This is the single point every TMOService date range passes through,
    so it's the one place this rule has to hold — individual methods have
    been re-implementing their own "today isn't final yet" clamp for months
    (get_daily_energy, get_volatility, get_pear, ...), which works until a
    new method forgets to. Confirmed 2026-08-08: a request for the current
    month (?month=2026-08) returned to_date = Aug 31 unclamped — the entire
    rest of the month treated as already-happened data — which is what
    produced the 98.9%-to-one-segment P&L donut bug. Clamping here means
    every TMOService method is protected automatically, not just the ones
    that remembered to guard themselves.
    """
    p = request.query_params
    yesterday = date.today() - timedelta(days=1)

    if p.get('from_date') and p.get('to_date'):
        from_date = date.fromisoformat(p['from_date'])
        to_date   = min(date.fromisoformat(p['to_date']), yesterday)
        return from_date, to_date

    if p.get('month'):
        year, month = map(int, p['month'].split('-'))
        from_date = date(year, month, 1)
        if month == 12:
            month_end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = date(year, month + 1, 1) - timedelta(days=1)
        to_date = min(month_end, yesterday)
        return from_date, to_date

    if p.get('date'):
        d = min(date.fromisoformat(p['date']), yesterday)
        return d, d

    return yesterday, yesterday


def _get_segment_feeder_ids():
    """
    MDI / MDNI feeder sets derived from the pl_segment field (authoritative).
    Falls back to CommercialCustomer lookup for feeders without pl_segment set.
    Returns (mdi_ids set, mdni_ids set).
    """
    mdi_ids  = set()
    mdni_ids = set()

    # Primary: use pl_segment field (set by import_feeder_segmentation command)
    for fid, seg in (
        Feeder.objects
        .filter(is_onboarded=True, pl_segment__in=['MDI', 'MDNI'])
        .values_list('id', 'pl_segment')
    ):
        if seg == 'MDI':
            mdi_ids.add(fid)
        else:
            mdni_ids.add(fid)

    # Fallback: any onboarded feeder without pl_segment still gets segmented via
    # CommercialCustomer so legacy data continues to work.
    #
    # already_segmented must include feeders explicitly tagged pl_segment=
    # 'Regions' too, not just MDI/MDNI — confirmed 2026-08-08 this was
    # missing, so a feeder explicitly tagged Regions with an unrelated
    # CommercialCustomer record (customer_type='MDNI') was getting silently
    # reclassified as MDNI anyway, overriding its own explicit tag. Found via
    # Apex/Lambisa/Nuhu Sunusi/Yusuf Road — all pl_segment='Regions' but
    # showing up in mdni_ids, worth 61.58 MWh on a single day alone. pl_
    # segment is authoritative whenever it's set to ANY value; the fallback
    # exists only for feeders where it's genuinely unset (None/blank).
    already_segmented = set(
        Feeder.objects.filter(is_onboarded=True).exclude(pl_segment__isnull=True).exclude(pl_segment='')
        .values_list('id', flat=True)
    )
    fallback_mdi = set(
        CommercialCustomer.objects
        .filter(customer_type='MDI')
        .exclude(feeder_id__in=already_segmented)
        .values_list('feeder_id', flat=True)
        .distinct()
    )
    fallback_mdni = set(
        CommercialCustomer.objects
        .filter(customer_type='MDNI')
        .exclude(feeder_id__in=already_segmented | fallback_mdi)
        .values_list('feeder_id', flat=True)
        .distinct()
    )
    mdi_ids  |= fallback_mdi
    mdni_ids |= fallback_mdni

    return mdi_ids, mdni_ids


# ── Service class ─────────────────────────────────────────────────────────────

class TMOService:
    """
    Central service for the TMO live dashboard.
    Always operates over (from_date, to_date); T-1 is the default when
    resolve_date_params finds no explicit param.

    filters = {
        'segment'  : 'MDI' | 'MDNI' | 'MINIGRID',
        'state'    : slug,
        'district' : slug,
        'band'     : slug,
        'voltage'  : '11kv' | '33kv',
        'feeder'   : slug,
    }
    """

    def __init__(self, from_date, to_date, filters=None):
        self.from_date   = from_date
        self.to_date     = to_date
        self.filters     = filters or {}
        self._mdi_ids    = None
        self._mdni_ids   = None
        self._upstream_ids_cache = None
        self._bulk_classification_cache = None
        self._regions_ids_cache = None
        self._segment_topology_cache = None

    def _energy_ids(self, feeder_ids):
        """Strip upstream 33KV feeders (those with active downstream 11KV feeders)."""
        return _energy_feeder_ids(feeder_ids)

    # Lazy-load segment IDs once per service instance
    @property
    def mdi_ids(self):
        if self._mdi_ids is None:
            self._mdi_ids, self._mdni_ids = _get_segment_feeder_ids()
        return self._mdi_ids

    @property
    def mdni_ids(self):
        if self._mdni_ids is None:
            self._mdi_ids, self._mdni_ids = _get_segment_feeder_ids()
        return self._mdni_ids

    def _base_feeder_qs(self):
        qs = Feeder.objects.filter(is_onboarded=True).select_related(
            'band', 'substation', 'substation__state', 'business_district'
        )
        f = self.filters
        if f.get('state'):
            qs = qs.filter(substation__state__slug=f['state'])
        if f.get('district'):
            qs = qs.filter(business_district__slug=f['district'])
        if f.get('band'):
            qs = qs.filter(band__slug=f['band'])
        if f.get('voltage'):
            qs = qs.filter(voltage_level=f['voltage'])
        if f.get('feeder'):
            qs = qs.filter(slug=f['feeder'])
        if f.get('segment'):
            seg = f['segment'].upper()
            if seg == 'MDI':
                qs = qs.filter(id__in=self.mdi_ids)
            elif seg == 'MDNI':
                qs = qs.filter(id__in=self.mdni_ids)
            elif seg in ('REGIONS', 'REGIONAL'):
                qs = qs.exclude(id__in=self.mdi_ids).exclude(id__in=self.mdni_ids)
            elif seg == 'MINIGRID':
                qs = qs.filter(is_minigrid=True)
        return qs

    def _bulk_feeder_ids(self):
        """
        The 33KV bulk anchor population feeding get_daily_energy() and every
        segment/voltage/PEAR breakdown that scales to it (see class docstring
        / TMO_Methodology cross-cutting rule #1).

        = onboarded 33KV feeders (excluding minigrids and confirmed duplicate
          records)
          MINUS the explicit, formula-verified list of feeders TCN's own
          SUMIF genuinely subtracts from a parent's raw reading (see
          CONFIRMED_SUBTRACTED_CHILD_SLUGS above) — their consumption is
          already embedded in that supplying feeder's own raw meter reading,
          so counting both would double-count them.
          PLUS the confirmed standalone-equivalent exceptions TCN's own
          formula counts separately regardless (see STANDALONE_BULK_
          EQUIVALENT_SLUGS above).

        IMPORTANT (2026-08-08): this used to derive the "downstream child"
        exclusion from Raven's own FeederSupplyRelationship data (physical
        supply topology) instead of an explicit list. That was wrong for 3 of
        the 7 feeders it flagged — physical topology has nothing to do with
        which row-range TCN's spreadsheet formula happens to search (see the
        long comment on CONFIRMED_SUBTRACTED_CHILD_SLUGS for the full
        explanation). Dawaki, Dawanau, and Badume were being wrongly excluded
        as a result — none of the three are actually subtracted by anyone in
        TCN's real formula, so they were missing from Raven's total for no
        good reason. Don't switch this back to a topology-derived heuristic —
        physical "who feeds whom" and "which row range does TCN's SUMIF
        search" are unrelated facts that happen to overlap only sometimes.
        """
        bulk_ids = set(
            self._base_feeder_qs()
            .filter(voltage_level='33kv')
            .exclude(slug__in=DUPLICATE_FEEDER_SLUGS)
            .exclude(slug__in=NOT_TRACKED_BY_TCN_SLUGS)
            .exclude(slug__in=CONFIRMED_SUBTRACTED_CHILD_SLUGS)
            .exclude(is_minigrid=True)
            .values_list('id', flat=True)
        )
        bulk_ids |= set(
            self._base_feeder_qs()
            .filter(slug__in=STANDALONE_BULK_EQUIVALENT_SLUGS)
            .values_list('id', flat=True)
        )
        return bulk_ids

    def _bulk_classification(self):
        """
        (bulk_ids, (meter_ids, balloon_ids)) for the bulk 33KV population,
        computed ONCE per service instance and shared by every bulk-total
        helper below (_bulk_daily_map / _bulk_total_mwh) and by
        get_daily_energy() itself.

        Why this exists: _classify_feeders() decides meter-vs-balloon per
        feeder based on the date range it's given — so asking "what's the
        bulk total for just day 1?" can classify a feeder differently than
        asking "what's the bulk total across the whole week?", even for the
        exact same feeder on the exact same day, purely because the range
        used to check for bad/missing data differs. That caused get_daily_
        energy() and get_energy_by_voltage() to disagree by ~0.02-0.08 GWh
        on the same day (confirmed 2026-08-07: day 1's bulk total was
        3922.79 MWh classified over just day 1, vs 3898.16 MWh classified
        over the whole week — a 24.6 MWh drift from classification alone,
        nothing to do with the underlying data).

        Fix: classify once, over this service instance's own requested period
        (self.from_date to self.to_date — covers single-day and MTD calls,
        which are what everything reconciles against), and have every caller
        share that same classification. Only which DAYS get summed varies;
        whether a given feeder is trusted to use its real reading never does.

        Deliberately NOT widened to the previous month, even though a couple
        of callers (get_energy_by_voltage, get_daily_energy_by_segment) also
        need a previous-month total for their comparison panel — tried that
        first, but it pulled in that other month's own data-quality issues
        and pushed feeders that were fine in the requested month into the
        cruder estimate anyway, moving the total further from TCN's real
        figure (28.20 → 27.73 GWh, tested 2026-08-07). The previous-month
        panel is a standalone bar, never summed against the current period,
        so it doesn't need to share this classification for correctness —
        _bulk_daily_map()/_bulk_total_mwh() still work for a previous-month
        date range, they just use the current period's classification to
        decide meter-vs-balloon for it too, which is an acceptable trade-off
        given feeder meter reliability doesn't typically change month to
        month.
        """
        if self._bulk_classification_cache is None:
            bulk_ids = self._bulk_feeder_ids()
            # Never let today (always incomplete — see the "today excluded"
            # rule used everywhere else in this file) into the classification
            # window. A caller can legitimately pass to_date=today (that's
            # the normal default), but classifying meter-vs-balloon using a
            # partial/missing day can corrupt the decision for feeders that
            # are otherwise fine — confirmed 2026-08-08: extending the range
            # by one day to include today turned a healthy MTD split (44/10/46%)
            # into a broken one (63/1/-0%) with no other change.
            classify_end = min(self.to_date, date.today() - timedelta(days=1))
            classify_start = min(self.from_date, classify_end)
            self._bulk_classification_cache = (
                bulk_ids,
                _classify_feeders(bulk_ids, classify_start, classify_end),
            )
        return self._bulk_classification_cache

    def _bulk_daily_map(self, from_date, to_date):
        """{date_str: mwh} for the bulk 33KV population over [from_date, to_date],
        using the single shared classification from _bulk_classification().

        NOTE (2026-08-08): tried dropping the avg-load × hours estimate
        entirely for balloon-classified feeders, on the theory that TCN's
        own methodology never estimates from average MW so Raven shouldn't
        either. That made every single day WORSE (day 1 alone moved from
        +0.0035 GWh to -0.1510 GWh) — proving most balloon-classified
        feeders DO have real signal in their HourlyLoad data worth keeping,
        even if it's not as precise as a real meter reading. The correct
        fix for "TCN never estimates from MW" isn't to blank out these
        feeders — it's to find and fix the sync gap that's stopping their
        REAL meter-based value from being captured in the first place (the
        Ahmadu Bello orphaned-CMR bug is exactly this kind of gap, and was
        worth ~80 recovered feeders on day 1 alone). Go feeder by feeder
        checking the live sheets before ever removing this fallback again.
        """
        _, (meter_ids, balloon_ids) = self._bulk_classification()
        daily = defaultdict(float)
        if meter_ids:
            for row in (
                EnergyDelivered.objects
                .filter(feeder_id__in=meter_ids, date__gte=from_date, date__lte=to_date)
                .values('date').annotate(total=Sum('energy_mwh'))
            ):
                daily[str(row['date'])] += float(row['total'] or 0)
        if balloon_ids:
            for row in (
                HourlyLoad.objects
                .filter(feeder_id__in=balloon_ids, date__gte=from_date, date__lte=to_date, load_mw__gt=0)
                .values('feeder_id', 'date').annotate(avg_load=Avg('load_mw'), supply_hours=Count('hour'))
            ):
                daily[str(row['date'])] += float(row['avg_load'] or 0) * int(row['supply_hours'] or 0)
        return dict(daily)

    def _bulk_total_mwh(self, from_date, to_date):
        return sum(self._bulk_daily_map(from_date, to_date).values())

    def _segment_label(self, feeder_id):
        if feeder_id in self.mdi_ids:
            return 'MDI'
        if feeder_id in self.mdni_ids:
            return 'MDNI'
        return 'Regional'

    # ── 1. Overview ──────────────────────────────────────────────────────────

    def get_overview(self):
        feeder_qs  = self._base_feeder_qs()
        feeder_ids = list(feeder_qs.values_list('id', flat=True))

        energy_ids   = self._energy_ids(feeder_ids)
        total_actual = calculate_energy_delivered(energy_ids, self.from_date, self.to_date)['total_mwh']

        target_agg = TMOFeederTarget.objects.filter(
            feeder_id__in=feeder_ids,
            target_date__gte=self.from_date,
            target_date__lte=self.to_date,
        ).aggregate(total=Sum('target_mwh'))
        total_target = float(target_agg['total'] or 0)

        dispatch_ach = _pct(total_actual, total_target)

        band_a   = Band.objects.filter(slug='a').first()
        min_hrs  = float(band_a.minimum_hours) if band_a else 20.0

        supply_rows = list(
            DailyHoursOfSupply.objects.filter(
                feeder_id__in=feeder_ids,
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).values('feeder_id').annotate(avg_hours=Avg('hours_supplied'))
        )
        compliant     = sum(1 for r in supply_rows if float(r['avg_hours'] or 0) >= min_hrs)
        total_tracked = len(supply_rows)

        return {
            'period': {'from': str(self.from_date), 'to': str(self.to_date)},
            'total_feeders': feeder_qs.count(),
            'energy_dispatch': {
                'target_mwh':       round(total_target, 2),
                'actual_mwh':       round(total_actual, 2),
                'variance_mwh':     round(total_actual - total_target, 2),
                'achievement_pct':  round(dispatch_ach, 1),
                'status':           _compliance(dispatch_ach),
            },
            'supply_compliance': {
                'compliant_feeders': compliant,
                'total_feeders':     total_tracked,
                'compliance_pct':    round(_pct(compliant, total_tracked), 1),
            },
        }

    # ── 2. Feeder Dispatch ───────────────────────────────────────────────────

    def get_feeder_dispatch(self):
        feeder_qs  = self._base_feeder_qs()
        feeder_ids = list(feeder_qs.values_list('id', flat=True))

        energy_map = calculate_energy_delivered_per_feeder(self._energy_ids(feeder_ids), self.from_date, self.to_date)
        actuals = {fid: d['mwh'] for fid, d in energy_map.items()}

        targets = {
            row['feeder_id']: float(row['total'] or 0)
            for row in TMOFeederTarget.objects.filter(
                feeder_id__in=feeder_ids,
                target_date__gte=self.from_date,
                target_date__lte=self.to_date,
            ).values('feeder_id').annotate(total=Sum('target_mwh'))
        }

        feeders = {f.id: f for f in feeder_qs}
        all_ids = set(feeder_ids) | set(actuals) | set(targets)

        rows = []
        for fid in all_ids:
            feeder = feeders.get(fid)
            actual = actuals.get(fid, 0.0)
            target = targets.get(fid, 0.0)
            ach    = _pct(actual, target)
            rows.append({
                'feeder_id':          str(fid),
                'feeder_name':        feeder.name if feeder else str(fid),
                'feeder_slug':        feeder.slug if feeder else '',
                'segment':            self._segment_label(fid),
                'band':               feeder.band.name if feeder and feeder.band else '',
                'state':              (feeder.substation.state.name
                                      if feeder and feeder.substation and feeder.substation.state else ''),
                'district':           feeder.business_district.name if feeder and feeder.business_district else '',
                'is_minigrid':        feeder.is_minigrid if feeder else False,
                'target_mwh':         round(target, 4),
                'actual_mwh':         round(actual, 4),
                'variance_mwh':       round(actual - target, 4),
                'variance_pct':       _var_pct(actual, target),
                'achievement_pct':    round(ach, 1),
                'status':             _compliance(ach),
            })

        rows.sort(key=lambda r: r['achievement_pct'])

        total_target = sum(r['target_mwh'] for r in rows)
        total_actual = sum(r['actual_mwh'] for r in rows)
        ov_ach = _pct(total_actual, total_target)

        return {
            'period': {'from': str(self.from_date), 'to': str(self.to_date)},
            'feeders': rows,
            'summary': {
                'total_target_mwh':        round(total_target, 2),
                'total_actual_mwh':        round(total_actual, 2),
                'variance_mwh':            round(total_actual - total_target, 2),
                'overall_achievement_pct': round(ov_ach, 1),
                'overall_status':          _compliance(ov_ach),
            },
        }

    # ── 3. Energy by Segment ─────────────────────────────────────────────────

    def get_energy_by_segment(self):
        # Clamp to yesterday — today's cross-day diff is really yesterday's
        # energy filed under today's date (see get_daily_energy()), so this
        # total must not include it either or it silently diverges from
        # get_volatility()'s MTD total for the same period.
        to_date = min(self.to_date, date.today() - timedelta(days=1))

        feeder_qs  = self._base_feeder_qs()
        all_ids    = set(feeder_qs.values_list('id', flat=True))

        # Exclude 33KV feeders that supply downstream 11KV feeders — their energy
        # is already captured at the 11KV meter level; including both = double-count
        upstream_33kv = set(
            FeederSupplyRelationship.objects
            .filter(supplier_feeder__voltage_level='33kv', status='active')
            .values_list('supplier_feeder_id', flat=True)
        )
        feeder_ids = all_ids - upstream_33kv

        mdi_ids    = self.mdi_ids  & feeder_ids
        mdni_ids   = self.mdni_ids & feeder_ids
        # Regions = every feeder that is neither MDI nor MDNI (includes minigrids)
        region_ids = feeder_ids - mdi_ids - mdni_ids

        # feeder_count/target still use this (per-feeder, topology-agnostic)
        # population — only the *energy* figure below needed the fix.
        buckets = {
            'MDI':     mdi_ids,
            'MDNI':    mdni_ids,
            'Regions': region_ids,
        }

        def _target(ids):
            if not ids:
                return 0.0
            return float(
                TMOFeederTarget.objects.filter(
                    feeder_id__in=ids,
                    target_date__gte=self.from_date,
                    target_date__lte=to_date,
                ).aggregate(t=Sum('target_mwh'))['t'] or 0
            )

        # Each segment's own classified energy, from the shared NET-33KV +
        # all-11KV logic (see _segment_totals() above) — NOT the old _energy_
        # ids() population, which undercounted badly (confirmed 2026-08-08:
        # 27.77 GWh vs the validated 33.88 GWh anchor for the same period, an
        # 18% gap — most 33KV bulk parents were being dropped outright
        # instead of NET-subtracted). Also not scaled to the bulk 33KV total:
        # confirmed against the source Excel that TCN's own P&L breakdown
        # doesn't force MDI+MDNI+Regions to equal the Daily Energy Allocation
        # total either. This also fixed a real, user-visible inconsistency:
        # MDNI showed 2.71 GWh here (and on GCR, which reuses actual_mwh from
        # here) vs 2.22 GWh on the donut for the identical MTD window.
        raw_actual = self._segment_totals(self.from_date, to_date)
        raw_actual = {'MDI': raw_actual.get('MDI', 0.0), 'MDNI': raw_actual.get('MDNI', 0.0),
                      'Regions': raw_actual.get('Regional', 0.0)}
        raw_total  = sum(raw_actual.values())

        segments = []
        totals   = {'actual': 0.0, 'target': 0.0}
        for name, ids in buckets.items():
            actual = raw_actual[name]
            target = _target(ids)
            ach    = _pct(actual, target)
            totals['actual'] += actual
            totals['target'] += target
            segments.append({
                'segment':         name,
                'feeder_count':    len(ids),
                'target_mwh':      round(target, 2),
                'actual_mwh':      round(actual, 2),
                'actual_gwh':      round(actual / 1000, 4),
                'variance_mwh':    round(actual - target, 2),
                'achievement_pct': round(ach, 1),
                'status':          _compliance(ach),
                'share_pct':       0.0,   # filled after total is known
            })

        # Fill share_pct now that total is known
        total_actual = totals['actual']
        for seg in segments:
            seg['share_pct'] = round(_pct(seg['actual_mwh'], total_actual), 1)

        return {
            'period':          {'from': str(self.from_date), 'to': str(self.to_date)},
            'total_actual_mwh': round(total_actual, 2),
            'total_actual_gwh': round(total_actual / 1000, 4),
            'segments':         segments,
            'methodology':      METHODOLOGY['energy_by_segment'],
        }

    # ── 4. Supply Compliance ─────────────────────────────────────────────────

    def get_supply_compliance(self):
        feeder_qs  = self._base_feeder_qs()
        feeder_ids = list(feeder_qs.values_list('id', flat=True))

        supply_map = {
            row['feeder_id']: row
            for row in DailyHoursOfSupply.objects.filter(
                feeder_id__in=feeder_ids,
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).values('feeder_id').annotate(
                avg_hours=Avg('hours_supplied'),
                total_hours=Sum('hours_supplied'),
                days=Count('id'),
            )
        }

        feeders = {f.id: f for f in feeder_qs}

        # Admin-set segment targets for the period's month (use from_date month)
        segment_target_map = {
            row.segment: float(row.target_hours)
            for row in TMOSupplyHoursTarget.objects.filter(
                year=self.from_date.year,
                month=self.from_date.month,
            )
        }

        # Per-feeder targets — current month first, then most-recent upload as base default
        month_targets = {
            str(row.feeder_id): float(row.target_hours)
            for row in TMOFeederSupplyTarget.objects.filter(
                year=self.from_date.year,
                month=self.from_date.month,
                feeder_id__in=feeder_ids,
            )
        }
        missing_ids = [fid for fid in feeder_ids if str(fid) not in month_targets]
        base_targets = {}
        if missing_ids:
            # PostgreSQL DISTINCT ON: picks the latest (year DESC, month DESC) row per feeder
            for row in (
                TMOFeederSupplyTarget.objects
                .filter(feeder_id__in=missing_ids)
                .order_by('feeder_id', '-year', '-month')
                .distinct('feeder_id')
                .values('feeder_id', 'target_hours')
            ):
                base_targets[str(row['feeder_id'])] = float(row['target_hours'])
        feeder_target_map = {**base_targets, **month_targets}

        def _dm_status(fid, feeder):
            if fid in self.mdi_ids:
                return 'MDI'
            is_band_a = feeder and feeder.band and feeder.band.slug == 'a'
            is_33kv   = feeder and feeder.voltage_level == '33kv'
            if is_band_a or is_33kv:
                return 'Non-MDI Band A'
            return 'Non-MDI, Non-Band A'

        rows = []
        for fid in feeder_ids:
            feeder  = feeders.get(fid)
            s       = supply_map.get(fid, {})
            avg_h   = float(s.get('avg_hours') or 0)
            dm_seg  = _dm_status(fid, feeder)
            # Priority: per-feeder upload > segment admin target > band minimum
            if str(fid) in feeder_target_map:
                min_h = feeder_target_map[str(fid)]
            elif dm_seg in segment_target_map:
                min_h = segment_target_map[dm_seg]
            else:
                min_h = float(feeder.band.minimum_hours) if feeder and feeder.band else 0.0
            c_pct  = _pct(avg_h, min_h) if min_h else 0.0
            rows.append({
                'feeder_id':          str(fid),
                'feeder_name':        feeder.name if feeder else str(fid),
                'feeder_slug':        feeder.slug if feeder else '',
                'voltage_level':      feeder.voltage_level if feeder else '',
                'segment':            self._segment_label(fid),
                'dm_status':          _dm_status(fid, feeder),
                'band':               feeder.band.name if feeder and feeder.band else '',
                'band_minimum_hours': min_h,
                'avg_daily_hours':    round(avg_h, 2),
                'gap_hours':          round(avg_h - min_h, 2),
                'total_hours':        round(float(s.get('total_hours') or 0), 2),
                'days_recorded':      s.get('days', 0),
                'compliance_pct':     round(c_pct, 1),
                'status':             _compliance(c_pct),
                'bucket':             _compliance(c_pct),   # alias used by frontend RAG coloring
                'is_minigrid':        feeder.is_minigrid if feeder else False,
            })

        # Disambiguate duplicate feeder names by appending the slug
        from collections import Counter
        name_counts = Counter(r['feeder_name'] for r in rows)
        for r in rows:
            if name_counts[r['feeder_name']] > 1 and r['feeder_slug']:
                r['feeder_name'] = f"{r['feeder_name']} ({r['feeder_slug']})"

        rows.sort(key=lambda r: (r['avg_daily_hours'] == 0, -r['compliance_pct'] if r['avg_daily_hours'] == 0 else r['compliance_pct']))
        compliant = sum(1 for r in rows if r['compliance_pct'] >= 100)
        total     = len(rows)

        return {
            'period':  {'from': str(self.from_date), 'to': str(self.to_date)},
            'feeders': rows,
            'summary': {
                'compliant_feeders':  compliant,
                'total_feeders':      total,
                'compliance_rate_pct': round(_pct(compliant, total), 1),
            },
            'methodology': METHODOLOGY['supply_compliance'],
        }

    # ── 5. Collection ────────────────────────────────────────────────────────

    def get_collection(self):
        qs = TMOCollectionTarget.objects.filter(
            period_month__gte=self.from_date,
            period_month__lte=self.to_date,
        ).order_by('period_month', 'segment_code')

        if self.filters.get('segment'):
            qs = qs.filter(segment_code=self.filters['segment'].upper())

        rows = []
        for obj in qs:
            target = float(obj.target_amount)
            actual = float(obj.actual_amount)
            ach    = _pct(actual, target)
            rows.append({
                'segment_code':    obj.segment_code,
                'sub_segment':     obj.sub_segment,
                'period_month':    str(obj.period_month),
                'target_amount':   round(target, 2),
                'actual_amount':   round(actual, 2),
                'variance':        round(actual - target, 2),
                'achievement_pct': round(ach, 1),
                'status':          _compliance(ach),
            })

        total_target = sum(r['target_amount'] for r in rows)
        total_actual = sum(r['actual_amount'] for r in rows)
        ov_ach       = _pct(total_actual, total_target)

        return {
            'period': {'from': str(self.from_date), 'to': str(self.to_date)},
            'rows':   rows,
            'summary': {
                'total_target':            round(total_target, 2),
                'total_actual':            round(total_actual, 2),
                'variance':                round(total_actual - total_target, 2),
                'overall_achievement_pct': round(ov_ach, 1),
                'overall_status':          _compliance(ov_ach),
            },
        }

    # ── 6. Billing Efficiency ────────────────────────────────────────────────

    def get_billing_efficiency(self):
        qs = TMOBillingEfficiency.objects.filter(
            period_month__gte=self.from_date,
            period_month__lte=self.to_date,
        ).order_by('scope_type', 'scope_label', 'period_month')

        rows = []
        for obj in qs:
            del_gwh = float(obj.energy_delivered_gwh)
            bil_gwh = float(obj.energy_billed_gwh)
            t_rev   = float(obj.target_revenue_amount)
            b_rev   = float(obj.billed_amount)
            be_pct  = _pct(bil_gwh, del_gwh)
            rr_pct  = _pct(b_rev, t_rev)
            rows.append({
                'scope_type':             obj.scope_type,
                'scope_code':             obj.scope_code,
                'scope_label':            obj.scope_label or obj.scope_code,
                'period_month':           str(obj.period_month),
                'energy_delivered_gwh':   del_gwh,
                'energy_billed_gwh':      bil_gwh,
                'billing_efficiency_pct': round(be_pct, 1),
                'target_revenue':         round(t_rev, 2),
                'billed_amount':          round(b_rev, 2),
                'revenue_efficiency_pct': round(rr_pct, 1),
                'be_status':              _compliance(be_pct),
                'rr_status':              _compliance(rr_pct),
            })

        total_del  = sum(r['energy_delivered_gwh'] for r in rows)
        total_bil  = sum(r['energy_billed_gwh']    for r in rows)
        total_trev = sum(r['target_revenue']        for r in rows)
        total_brev = sum(r['billed_amount']         for r in rows)

        return {
            'period': {'from': str(self.from_date), 'to': str(self.to_date)},
            'rows':   rows,
            'summary': {
                'total_energy_delivered_gwh': round(total_del, 4),
                'total_energy_billed_gwh':    round(total_bil, 4),
                'overall_billing_eff_pct':    round(_pct(total_bil, total_del), 1),
                'total_target_revenue':       round(total_trev, 2),
                'total_billed_amount':        round(total_brev, 2),
                'overall_revenue_eff_pct':    round(_pct(total_brev, total_trev), 1),
            },
        }

    # ── 7. P&L Segment Targets ───────────────────────────────────────────────

    def get_pnl_targets(self):
        """Energy actuals vs TMOMonthlySegmentTarget for MDI and MDNI."""
        target_qs = TMOMonthlySegmentTarget.objects.filter(
            year=self.from_date.year,
            month=self.from_date.month,
        )
        targets_by_seg = {t.segment: t for t in target_qs}

        def _energy_actual(ids):
            if not ids:
                return 0.0
            return calculate_energy_delivered(self._energy_ids(list(ids)), self.from_date, self.to_date)['total_mwh']

        segments = []
        for seg_name, ids in [('MDI', self.mdi_ids), ('MDNI', self.mdni_ids)]:
            actual = _energy_actual(ids)
            t      = targets_by_seg.get(seg_name)
            t_mwh  = float(t.target_energy_mwh)     if t else 0.0
            t_rev  = float(t.target_revenue_ngn)    if t else 0.0
            t_col  = float(t.target_collection_ngn) if t else 0.0
            ach    = _pct(actual, t_mwh)
            segments.append({
                'segment':               seg_name,
                'feeder_count':          len(ids),
                'target_energy_mwh':     round(t_mwh, 2),
                'actual_energy_mwh':     round(actual, 2),
                'energy_achievement_pct': round(ach, 1),
                'energy_status':         _compliance(ach),
                'target_revenue_ngn':    round(t_rev, 2),
                'target_collection_ngn': round(t_col, 2),
            })

        return {
            'period':   {'from': str(self.from_date), 'to': str(self.to_date)},
            'segments': segments,
        }

    # ── 8. Minigrids ─────────────────────────────────────────────────────────

    def get_minigrids(self):
        feeder_qs  = self._base_feeder_qs().filter(is_minigrid=True)
        feeder_ids = list(feeder_qs.values_list('id', flat=True))

        energy_map = calculate_energy_delivered_per_feeder(self._energy_ids(feeder_ids), self.from_date, self.to_date)
        actuals = {fid: d['mwh'] for fid, d in energy_map.items()}
        supply = {
            row['feeder_id']: float(row['avg'] or 0)
            for row in DailyHoursOfSupply.objects.filter(
                feeder_id__in=feeder_ids,
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).values('feeder_id').annotate(avg=Avg('hours_supplied'))
        }
        targets = {
            row['feeder_id']: float(row['total'] or 0)
            for row in TMOFeederTarget.objects.filter(
                feeder_id__in=feeder_ids,
                target_date__gte=self.from_date,
                target_date__lte=self.to_date,
            ).values('feeder_id').annotate(total=Sum('target_mwh'))
        }

        rows = []
        for feeder in feeder_qs:
            fid    = feeder.id
            actual = actuals.get(fid, 0.0)
            target = targets.get(fid, 0.0)
            avg_h  = supply.get(fid, 0.0)
            ach    = _pct(actual, target)
            rows.append({
                'feeder_id':       str(fid),
                'feeder_name':     feeder.name,
                'state':           (feeder.substation.state.name
                                   if feeder.substation and feeder.substation.state else ''),
                'target_mwh':      round(target, 4),
                'actual_mwh':      round(actual, 4),
                'variance_mwh':    round(actual - target, 4),
                'achievement_pct': round(ach, 1),
                'avg_daily_hours': round(avg_h, 2),
                'status':          _compliance(ach),
            })

        return {
            'period':    {'from': str(self.from_date), 'to': str(self.to_date)},
            'minigrids': rows,
            'count':     len(rows),
            'methodology': METHODOLOGY['minigrids'],
        }

    # ── 9. All Feeders ───────────────────────────────────────────────────────

    def get_feeders(self):
        feeder_qs  = self._base_feeder_qs()

        # When filtered to voltage=33kv, this is the population the frontend's
        # "Log coupling event" form builds its faulted_feeder/coupled_to_feeder
        # pickers from (see TMO_Settings_Frontend_Spec.md §1.4). Coupling means
        # "this feeder's downstream 11kV network was rerouted elsewhere" — only
        # meaningful for a 33kV parent that actually HAS real 11kV children.
        # Narrowed here (not in _base_feeder_qs, which other methods like
        # get_overview/get_feeder_dispatch also rely on for the full, real
        # 33kV population) so this dropdown-facing endpoint is the only one
        # affected.
        if self.filters.get('voltage') == '33kv':
            # Use a filter-free service instance to build the topology check —
            # self._segment_topology() reads self._base_feeder_qs(), which
            # already carries this same voltage=33kv filter; calling it on
            # self here would filter its own 11kV-children lookup down to
            # nothing before it could find any children at all.
            _, true_33kv_ids, _, children_by_parent = TMOService(self.from_date, self.to_date)._segment_topology()
            eligible_ids = {fid for fid in true_33kv_ids if children_by_parent.get(fid)}
            feeder_qs = feeder_qs.filter(id__in=eligible_ids)

        feeder_ids = list(feeder_qs.values_list('id', flat=True))

        energy_map = calculate_energy_delivered_per_feeder(self._energy_ids(feeder_ids), self.from_date, self.to_date)
        actuals = {fid: d['mwh'] for fid, d in energy_map.items()}
        supply = {
            row['feeder_id']: float(row['avg'] or 0)
            for row in DailyHoursOfSupply.objects.filter(
                feeder_id__in=feeder_ids,
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).values('feeder_id').annotate(avg=Avg('hours_supplied'))
        }
        targets = {
            row['feeder_id']: float(row['total'] or 0)
            for row in TMOFeederTarget.objects.filter(
                feeder_id__in=feeder_ids,
                target_date__gte=self.from_date,
                target_date__lte=self.to_date,
            ).values('feeder_id').annotate(total=Sum('target_mwh'))
        }

        rows = []
        for feeder in feeder_qs:
            fid    = feeder.id
            actual = actuals.get(fid, 0.0)
            target = targets.get(fid, 0.0)
            avg_h  = supply.get(fid, 0.0)
            min_h  = float(feeder.band.minimum_hours) if feeder.band else 0.0
            ach    = _pct(actual, target)
            h_c    = _pct(avg_h, min_h) if min_h else 0.0
            rows.append({
                'feeder_id':              str(fid),
                'feeder_name':            feeder.name,
                'feeder_slug':            feeder.slug,
                'segment':                self._segment_label(fid),
                'band':                   feeder.band.name if feeder.band else '',
                'voltage_level':          feeder.voltage_level,
                'state':                  (feeder.substation.state.name
                                          if feeder.substation and feeder.substation.state else ''),
                'district':               feeder.business_district.name if feeder.business_district else '',
                'is_minigrid':            feeder.is_minigrid,
                'target_mwh':             round(target, 4),
                'actual_mwh':             round(actual, 4),
                'variance_mwh':           round(actual - target, 4),
                'energy_achievement_pct': round(ach, 1),
                'energy_status':          _compliance(ach),
                'avg_daily_hours':        round(avg_h, 2),
                'band_minimum_hours':     min_h,
                'hours_compliance_pct':   round(h_c, 1),
                'hours_status':           _compliance(h_c),
            })

        rows.sort(key=lambda r: r['energy_achievement_pct'])

        return {
            'period':  {'from': str(self.from_date), 'to': str(self.to_date)},
            'feeders': rows,
            'count':   len(rows),
        }

    # ── 10. P&L Mix Volatility Index ────────────────────────────────────────

    def get_volatility(self):
        """
        P&L Mix Volatility Index.
        Compares each segment's share of total energy for:
          - the selected day (T-1 by default)
          - month-to-date (start of that month → selected day)
        Flags when high-ROI segment share is declining vs MTD average.
        """
        # Day = to_date (T-1 or user-selected date), clamped to never reach today —
        # today can never have a real, finished number (see get_daily_energy()), so
        # "yesterday" and "month-to-date" must never include it either.
        day = min(self.to_date, date.today() - timedelta(days=1))
        mtd_start = day.replace(day=1)

        def _share(part, total):
            return round(_pct(part, total), 1) if total else 0.0

        def _remark(seg, diff):
            if seg in ('MDI', 'MDNI'):
                if diff < -1:
                    return 'Decline –'
                if diff > 1:
                    return 'Growth +'
                return 'Stable'
            else:  # Regions (includes minigrids)
                if diff > 1:
                    return 'High – Daily spike is sustained'
                if diff < -1:
                    return 'Declining –'
                return 'Stable'

        # Segment energy comes from the shared NET-33KV + all-11KV logic (see
        # _segment_totals() above), used directly — not scaled to the bulk
        # 33KV total. Confirmed 2026-08-08 against the source Excel's own P&L
        # doughnut logic: each segment's share is that segment's classified
        # energy divided by the sum of the three classified segments — the
        # doughnut's own total is that sum, not the Daily Energy Allocation
        # total. TCN's own dashboard does NOT reconcile these two numbers to
        # match each other.
        day_totals = self._segment_totals(day, day)
        day_mdi  = day_totals.get('MDI', 0.0)
        day_mdni = day_totals.get('MDNI', 0.0)
        day_reg  = day_totals.get('Regional', 0.0)
        day_total = day_mdi + day_mdni + day_reg

        mtd_totals = self._segment_totals(mtd_start, day)
        mtd_mdi  = mtd_totals.get('MDI', 0.0)
        mtd_mdni = mtd_totals.get('MDNI', 0.0)
        mtd_reg  = mtd_totals.get('Regional', 0.0)
        mtd_total = mtd_mdi + mtd_mdni + mtd_reg

        segments = []
        for seg, d_val, m_val in [
            ('MDI',     day_mdi,  mtd_mdi),
            ('MDNI',    day_mdni, mtd_mdni),
            ('Regions', day_reg,  mtd_reg),
        ]:
            d_share = _share(d_val, day_total)
            m_share = _share(m_val, mtd_total)
            diff    = round(d_share - m_share, 1)
            segments.append({
                'segment':          seg,
                # Precise absolute values — the frontend should display these
                # directly, not re-derive GWh by multiplying the *_share_pct
                # fields below by the total. Those percentages are rounded to
                # 1dp for display and don't sum to exactly 100%, so computing
                # GWh from them compounds the rounding error (confirmed
                # 2026-08-08: 44.6+11.0+44.3=99.9% displayed a 140.67 GWh sum
                # against the real 140.81 GWh total).
                'yesterday_mwh':    round(d_val, 2),
                'yesterday_gwh':    round(d_val / 1000, 4),
                'mtd_mwh':          round(m_val, 2),
                'mtd_gwh':          round(m_val / 1000, 4),
                'yesterday_share_pct': d_share,
                'mtd_share_pct':    m_share,
                'difference_pct':   diff,
                'remark':           _remark(seg, diff),
            })

        return {
            'day':      str(day),
            'mtd_from': str(mtd_start),
            'day_total_mwh': round(day_total, 2),
            'mtd_total_mwh': round(mtd_total, 2),
            'segments': segments,
            'methodology': METHODOLOGY['volatility'],
        }

    # ── 11. Daily Network Energy (Forecast vs Actual) ────────────────────────

    def get_daily_energy(self):
        """
        Daily total energy across the network for the selected period.
        Compares against daily target derived from monthly GWh target in TMONetworkConfig.
        Covers Slides 2 & 3 (Daily Energy Forecast / Daily Energy Allocation).
        """
        # _bulk_feeder_ids() already strips downstream-of-bulk-parent feeders,
        # so no separate _energy_ids() double-count pass is needed here.
        # _bulk_daily_map() shares one classification with every other bulk-
        # total call in this service instance (see _bulk_classification) —
        # this IS the anchor everything else reconciles against.
        daily_map = self._bulk_daily_map(self.from_date, self.to_date)
        daily = [{'date': date.fromisoformat(d), 'total_mwh': v} for d, v in sorted(daily_map.items())]

        config = TMONetworkConfig.objects.filter(
            year=self.from_date.year,
            month=self.from_date.month,
        ).first()

        monthly_target_gwh = float(config.monthly_energy_target_gwh) if config else 0.0

        days_in_month = calendar.monthrange(self.from_date.year, self.from_date.month)[1]
        flat_daily_target_gwh = monthly_target_gwh / days_in_month if days_in_month else 0.0

        # Per-day targets: admin override from TMODailyAllocation takes priority.
        # Falls back to the standard day-of-month pattern (STANDARD_DAILY_FORECAST_GWH),
        # then to flat monthly ÷ days as the last resort when no monthly target is set.
        alloc_map = {
            str(a.date): float(a.expected_mw) * 24.0 / 1000.0
            for a in TMODailyAllocation.objects.filter(
                date__gte=self.from_date,
                date__lte=self.to_date,
            )
        }

        days = []
        for row in daily:
            # The cumulative-meter diff for "today" is really yesterday's energy
            # (today's reading − yesterday's reading = yesterday's full 24h span),
            # filed under today's date. There's no way to compute a real, final
            # number for today until today is over — so don't show one.
            if row['date'] == date.today():
                continue
            standard_gwh = STANDARD_DAILY_FORECAST_GWH.get(row['date'].day, flat_daily_target_gwh)
            daily_target_gwh = alloc_map.get(str(row['date']), standard_gwh)
            actual_gwh = float(row['total_mwh'] or 0) / 1000
            ach = _pct(actual_gwh, daily_target_gwh)
            days.append({
                'date':          str(row['date']),
                'day':           row['date'].day,
                'target_gwh':    round(daily_target_gwh, 4),
                'actual_gwh':    round(actual_gwh, 4),
                'variance_gwh':  round(actual_gwh - daily_target_gwh, 4),
                'achievement_pct': round(ach, 1),
                'status':        _compliance(ach),
            })

        total_actual_gwh = sum(d['actual_gwh'] for d in days)
        mtd_ach = _pct(total_actual_gwh, monthly_target_gwh)

        return {
            'period': {'from': str(self.from_date), 'to': str(self.to_date)},
            'monthly_target_gwh':  round(monthly_target_gwh, 4),
            'total_actual_gwh':    round(total_actual_gwh, 4),
            'mtd_achievement_pct': round(mtd_ach, 1),
            'mtd_status':          _compliance(mtd_ach),
            'days':                days,
            'methodology':         METHODOLOGY['daily_energy'],
        }

    # ── 11b. Daily Energy Forecast by Segment ───────────────────────────────────

    def get_daily_energy_by_segment(self):
        """
        Per-segment daily energy broken down by voltage level (33KV + 11KV).
        Segments: MDI | MDNI | Regions (from pl_segment field).
        Includes previous-month comparison for the same day numbers.
        Daily forecast = TMOMonthlySegmentTarget.target_energy_mwh / days_in_month.
        Actual uses balloon+system fallback via _daily_energy_breakdown.
        """
        # Current and previous-month period breakdowns, from the shared NET-
        # 33KV + all-11KV logic (see _segment_voltage_daily_map() above).
        # Rewritten 2026-08-08 — this used to build its own population via
        # _daily_energy_breakdown()/_safe_33kv() (drop any 33KV feeder with an
        # active downstream 11KV relationship, never add its energy back),
        # the same undercounting bug fixed in get_volatility()/get_pear()/
        # get_energy_by_segment(): confirmed this endpoint's MTD total sat at
        # 27-28 GWh vs the validated 33.88 GWh anchor for the same period.
        prev_month_last  = self.from_date.replace(day=1) - timedelta(days=1)
        prev_month_first = prev_month_last.replace(day=1)

        def _transpose(by_day):
            """{date_str: {segment: {'33kv':mwh,'11kv':mwh}}} ->
            {segment: {'33kv': {date_str:mwh}, '11kv': {date_str:mwh}}}"""
            out = {seg: {'33kv': {}, '11kv': {}} for seg in ('MDI', 'MDNI', 'Regional')}
            for d_str, segs in by_day.items():
                for seg, v in segs.items():
                    out[seg]['33kv'][d_str] = v['33kv']
                    out[seg]['11kv'][d_str] = v['11kv']
            # External field name for this endpoint has always been 'Regions'
            out['Regions'] = out.pop('Regional')
            return out

        curr = _transpose(self._segment_voltage_daily_map(self.from_date, self.to_date))
        prev = _transpose(self._segment_voltage_daily_map(prev_month_first, prev_month_last))

        # Feeder counts per segment, for the monthly_targets summary below.
        feeders_by_id, true_33kv_ids, all_11kv_ids, _ = self._segment_topology()
        seg_voltage = {seg: {'33kv': [], '11kv': []} for seg in ('MDI', 'MDNI', 'Regions')}
        for fid in true_33kv_ids:
            seg = 'Regions' if self._segment_label(fid) == 'Regional' else self._segment_label(fid)
            seg_voltage[seg]['33kv'].append(fid)
        for fid in all_11kv_ids:
            seg = 'Regions' if self._segment_label(fid) == 'Regional' else self._segment_label(fid)
            seg_voltage[seg]['11kv'].append(fid)

        # Index previous-month data by day number (1-31) for easy lookup
        def _by_day_number(voltage_daily: dict) -> dict[int, float]:
            out: dict[int, float] = {}
            for d_str, mwh in voltage_daily.items():
                day_num = int(d_str.split('-')[2])
                out[day_num] = out.get(day_num, 0.0) + mwh
            return out

        prev_by_day: dict[str, dict[str, dict[int, float]]] = {
            seg: {
                '33kv': _by_day_number(prev[seg]['33kv']),
                '11kv': _by_day_number(prev[seg]['11kv']),
            }
            for seg in seg_voltage
        }

        # Monthly targets
        days_in_month = calendar.monthrange(self.from_date.year, self.from_date.month)[1]
        target_qs = TMOMonthlySegmentTarget.objects.filter(
            year=self.from_date.year, month=self.from_date.month,
        )
        targets = {t.segment: float(t.target_energy_mwh) for t in target_qs}

        total_monthly_target = sum(targets.get(s, 0.0) for s in ('MDI', 'MDNI', 'Regions'))
        monthly_targets = {
            seg: {
                'target_mwh':         round(targets.get(seg, 0.0), 2),
                'daily_forecast_mwh': round(targets.get(seg, 0.0) / days_in_month, 4) if days_in_month else 0.0,
                'feeder_count_33kv':  len(seg_voltage[seg]['33kv']),
                'feeder_count_11kv':  len(seg_voltage[seg]['11kv']),
            }
            for seg in ('MDI', 'MDNI', 'Regions')
        }
        monthly_targets['Total'] = {
            'target_mwh':         round(total_monthly_target, 2),
            'daily_forecast_mwh': round(total_monthly_target / days_in_month, 4) if days_in_month else 0.0,
        }

        # Build day-by-day rows
        all_dates = []
        cur = self.from_date
        while cur <= self.to_date:
            all_dates.append(str(cur))
            cur += timedelta(days=1)

        days_out = []
        for d_str in sorted(all_dates):
            day_num = int(d_str.split('-')[2])
            segs_out: dict[str, dict] = {}
            tot_fore   = 0.0
            tot_actual = 0.0

            for seg in ('MDI', 'MDNI', 'Regions'):
                target     = targets.get(seg, 0.0)
                daily_fore = target / days_in_month if days_in_month else 0.0
                e_33 = curr[seg]['33kv'].get(d_str, 0.0)
                e_11 = curr[seg]['11kv'].get(d_str, 0.0)
                actual = e_33 + e_11
                ach    = _pct(actual, daily_fore)
                tot_fore   += daily_fore
                tot_actual += actual

                prev_33 = prev_by_day[seg]['33kv'].get(day_num, 0.0)
                prev_11 = prev_by_day[seg]['11kv'].get(day_num, 0.0)
                segs_out[seg] = {
                    'energy_33kv_mwh':      round(e_33, 4),
                    'energy_11kv_mwh':      round(e_11, 4),
                    'forecast_mwh':         round(daily_fore, 4),
                    'actual_mwh':           round(actual, 4),
                    'variance_mwh':         round(actual - daily_fore, 4),
                    'achievement_pct':      round(ach, 1),
                    'status':               _compliance(ach),
                    'prev_month': {
                        'energy_33kv_mwh': round(prev_33, 4),
                        'energy_11kv_mwh': round(prev_11, 4),
                        'total_mwh':       round(prev_33 + prev_11, 4),
                    },
                }

            tot_ach = _pct(tot_actual, tot_fore)
            days_out.append({
                'date':     d_str,
                'day':      day_num,
                'segments': segs_out,
                'total': {
                    'forecast_mwh':    round(tot_fore, 4),
                    'actual_mwh':      round(tot_actual, 4),
                    'variance_mwh':    round(tot_actual - tot_fore, 4),
                    'achievement_pct': round(tot_ach, 1),
                    'status':          _compliance(tot_ach),
                },
            })

        total_actual_mwh = sum(d['total']['actual_mwh'] for d in days_out)
        mtd_ach = _pct(total_actual_mwh, total_monthly_target)

        # Per-segment MTD summary — target (full month), actual (MTD so far),
        # and gap (target minus actual, floored at 0). Added 2026-08-08
        # because nothing in this response previously gave a per-segment MTD
        # actual figure (monthly_targets above is target-only; mtd_actual_mwh
        # above is a single combined total) — without this, a frontend chart
        # has to reconstruct "actual so far" for each segment itself, which is
        # exactly what led to the target/gap/actual bar being stacked wrong
        # (target added a THIRD time on top of actual+gap, when actual+gap
        # already equals target by definition — target is a reference line or
        # label, never a third stacked segment).
        segment_mtd = {}
        for seg in ('MDI', 'MDNI', 'Regions'):
            target = targets.get(seg, 0.0)
            actual = sum(d['segments'][seg]['actual_mwh'] for d in days_out)
            gap    = max(target - actual, 0.0)
            segment_mtd[seg] = {
                'target_mwh':      round(target, 2),
                'target_gwh':      round(target / 1000, 4),
                'actual_mwh':      round(actual, 2),
                'actual_gwh':      round(actual / 1000, 4),
                'gap_mwh':         round(gap, 2),
                'gap_gwh':         round(gap / 1000, 4),
                'achievement_pct': round(_pct(actual, target), 1),
            }
        segment_mtd['Total'] = {
            'target_mwh': round(total_monthly_target, 2),
            'target_gwh': round(total_monthly_target / 1000, 4),
            'actual_mwh': round(total_actual_mwh, 2),
            'actual_gwh': round(total_actual_mwh / 1000, 4),
            'gap_mwh':    round(max(total_monthly_target - total_actual_mwh, 0.0), 2),
            'gap_gwh':    round(max(total_monthly_target - total_actual_mwh, 0.0) / 1000, 4),
            'achievement_pct': round(mtd_ach, 1),
        }

        return {
            'period':              {'from': str(self.from_date), 'to': str(self.to_date)},
            'prev_month_period':   {'from': str(prev_month_first), 'to': str(prev_month_last)},
            'monthly_targets':     monthly_targets,
            'segment_mtd':         segment_mtd,
            'mtd_actual_mwh':      round(total_actual_mwh, 2),
            'mtd_achievement_pct': round(mtd_ach, 1),
            'mtd_status':          _compliance(mtd_ach),
            'days':                days_out,
        }

    # ── 12. PEAR (Premium Energy Allocation Ratio) ────────────────────────────

    def get_pear(self):
        """
        PEAR: MD (MDI+MDNI) vs NMD share of total energy.
        Compares yesterday vs MTD average, against the configured target mix.
        Covers Slide 10.
        """
        # Clamp to yesterday — same rule as get_daily_energy() and everywhere
        # else: today's cross-day diff is really yesterday's energy.
        day       = min(self.to_date, date.today() - timedelta(days=1))
        mtd_start = day.replace(day=1)

        config = TMONetworkConfig.objects.filter(year=day.year, month=day.month).first()
        # Default 60/40 confirmed 2026-08-08 against the source Excel's own
        # target-mix cells (Indicator 2 - Seg disp!E38:E39) — the previous
        # 65/35 default didn't match that workbook.
        target_md_pct  = float(config.target_md_share_pct)  if config else 60.0
        target_nmd_pct = round(100.0 - target_md_pct, 2)

        def _md_nmd(fd, td):
            totals = self._segment_totals(fd, td)
            md  = totals.get('MDI', 0.0) + totals.get('MDNI', 0.0)
            nmd = totals.get('Regional', 0.0)
            return md, nmd

        def _day_md_pct(d):
            """A single day's MD share, used both for 'yesterday' and to
            average into the MTD figure below."""
            md, nmd = _md_nmd(d, d)
            t = md + nmd
            return _pct(md, t) if t else None

        # Segment energy comes from the shared NET-33KV + all-11KV logic (see
        # _segment_totals() above), used directly — not scaled to the bulk
        # 33KV total. Confirmed 2026-08-08 against the source Excel's own
        # PEAR sheet: MD = MDI + MDNI energy, NMD = Regions energy, share =
        # each ÷ (MD + NMD), with no scaling step.
        day_md, day_nmd = _md_nmd(day, day)
        day_total = day_md + day_nmd

        mtd_md, mtd_nmd = _md_nmd(mtd_start, day)
        mtd_total = mtd_md + mtd_nmd

        # MTD share is the AVERAGE of each day's own MD%, not MTD-MD-energy ÷
        # MTD-total-energy — confirmed against the source Excel's literal
        # formula (`AVERAGE(H38:M38)` across each day's column). A weighted
        # ratio and an average of daily ratios diverge whenever daily volumes
        # vary, which they do here — matching the Excel means matching its
        # arithmetic, not just its inputs.
        daily_pcts = []
        d = mtd_start
        while d <= day:
            p = _day_md_pct(d)
            if p is not None:
                daily_pcts.append(p)
            d += timedelta(days=1)
        mtd_md_share_pct = round(sum(daily_pcts) / len(daily_pcts), 1) if daily_pcts else 0.0
        mtd_nmd_share_pct = round(100.0 - mtd_md_share_pct, 1) if daily_pcts else 0.0

        return {
            'day':      str(day),
            'mtd_from': str(mtd_start),
            'target_mix': {
                'md_pct':  target_md_pct,
                'nmd_pct': target_nmd_pct,
            },
            'yesterday': {
                'total_mwh':    round(day_total, 2),
                'md_mwh':       round(day_md, 2),
                'nmd_mwh':      round(day_nmd, 2),
                'md_share_pct': round(_pct(day_md, day_total), 1),
                'nmd_share_pct': round(_pct(day_nmd, day_total), 1),
            },
            'mtd': {
                'total_mwh':    round(mtd_total, 2),
                'md_mwh':       round(mtd_md, 2),
                'nmd_mwh':      round(mtd_nmd, 2),
                'md_share_pct': mtd_md_share_pct,
                'nmd_share_pct': mtd_nmd_share_pct,
            },
            'methodology': METHODOLOGY['pear'],
        }

    # ── 13. Compliance Summary by Segment ────────────────────────────────────

    def get_compliance_summary(self):
        """
        Feeder count bucketed by supply-hours compliance status per DM segment.
        actual = avg daily hours of supply (DailyHoursOfSupply)
        target = admin-set TMOSupplyHoursTarget first, fallback to feeder.band.minimum_hours
        Segments: MDI | Non-MDI Band A | Non-MDI Non-Band A.
        Covers Slide 6 / Overall view1 chart.
        """
        feeder_qs  = self._base_feeder_qs()
        feeder_ids = list(feeder_qs.values_list('id', flat=True))
        feeders    = {f.id: f for f in feeder_qs}

        # Avg daily hours of supply per feeder over the period
        supply_map = {
            row['feeder_id']: float(row['avg_hours'] or 0)
            for row in DailyHoursOfSupply.objects.filter(
                feeder_id__in=feeder_ids,
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).values('feeder_id').annotate(avg_hours=Avg('hours_supplied'))
        }

        # Admin-set segment targets for the period's month
        segment_target_map = {
            row.segment: float(row.target_hours)
            for row in TMOSupplyHoursTarget.objects.filter(
                year=self.from_date.year,
                month=self.from_date.month,
            )
        }

        # Per-feeder targets — current month first, then most-recent upload as base default
        month_targets = {
            str(row.feeder_id): float(row.target_hours)
            for row in TMOFeederSupplyTarget.objects.filter(
                year=self.from_date.year,
                month=self.from_date.month,
                feeder_id__in=feeder_ids,
            )
        }
        missing_ids = [fid for fid in feeder_ids if str(fid) not in month_targets]
        base_targets = {}
        if missing_ids:
            for row in (
                TMOFeederSupplyTarget.objects
                .filter(feeder_id__in=missing_ids)
                .order_by('feeder_id', '-year', '-month')
                .distinct('feeder_id')
                .values('feeder_id', 'target_hours')
            ):
                base_targets[str(row['feeder_id'])] = float(row['target_hours'])
        feeder_target_map = {**base_targets, **month_targets}

        def _dm_status(fid, feeder):
            if fid in self.mdi_ids:
                return 'MDI'
            is_band_a = feeder and feeder.band and feeder.band.slug == 'a'
            is_33kv   = feeder and feeder.voltage_level == '33kv'
            if is_band_a or is_33kv:
                return 'Non-MDI Band A'
            return 'Non-MDI, Non-Band A'

        BUCKETS = [
            ('exceeding',    lambda p: p > 105),
            ('on_target',    lambda p: 95 <= p <= 105),
            ('below_target', lambda p: 85 <= p < 95),
            ('poor',         lambda p: 75 <= p < 85),
            ('critical',     lambda p: p < 75),
        ]

        segments = {
            'MDI':                 {b: 0 for b, _ in BUCKETS},
            'Non-MDI Band A':      {b: 0 for b, _ in BUCKETS},
            'Non-MDI, Non-Band A': {b: 0 for b, _ in BUCKETS},
        }
        seg_totals = {k: 0 for k in segments}

        for fid in feeder_ids:
            feeder  = feeders.get(fid)
            avg_h   = supply_map.get(fid, 0.0)
            seg     = _dm_status(fid, feeder)
            # Priority: per-feeder upload > segment admin target > band minimum
            if str(fid) in feeder_target_map:
                min_h = feeder_target_map[str(fid)]
            elif seg in segment_target_map:
                min_h = segment_target_map[seg]
            else:
                min_h = float(feeder.band.minimum_hours) if feeder and feeder.band else 0.0
            pct = _pct(avg_h, min_h) if min_h else 0.0
            seg_totals[seg] += 1
            for bucket_name, test in BUCKETS:
                if test(pct):
                    segments[seg][bucket_name] += 1
                    break

        result = []
        for seg_name, counts in segments.items():
            total = seg_totals[seg_name]
            result.append({
                'segment':       seg_name,
                'total_feeders': total,
                'buckets': {
                    name: {
                        'count': counts[name],
                        'pct':   round(_pct(counts[name], total), 1) if total else 0.0,
                    }
                    for name, _ in BUCKETS
                },
            })

        return {
            'period':   {'from': str(self.from_date), 'to': str(self.to_date)},
            'segments': result,
            'methodology': METHODOLOGY['compliance_summary'],
        }

    # ── Shared segment/voltage helpers ────────────────────────────────────────
    # The validated NET-33KV + all-11KV logic (see get_energy_by_voltage()'s
    # docstring for the full derivation), promoted to shared methods 2026-08-08
    # so every segment-total endpoint uses the SAME real numbers. Before this,
    # get_volatility()/get_pear()/get_energy_by_segment()/get_daily_energy_by_
    # segment() each built their own feeder population via _energy_ids()
    # (strip any 33KV feeder with an active downstream 11KV relationship) —
    # confirmed 2026-08-08 this population undercounts badly: for Aug 1-7 it
    # summed to 27.77 GWh total regardless of segment, vs the validated
    # get_daily_energy() anchor of 33.88 GWh — an 18% gap, far too large to be
    # "some feeders unclassified" (TCN's own Excel gap for that reason is
    # ~1.4%). The real cause: _energy_ids() drops most 33KV bulk parents
    # outright (they almost all have SOME downstream 11KV relationship) and
    # never adds their energy back, instead of doing genuine per-parent NET
    # subtraction. This shared logic reconciles to within ~2.8% of the
    # validated anchor — the remaining gap is the same disclosed topology
    # uncertainty documented in get_energy_by_voltage()'s NET-flooring comment,
    # not a population hole.
    def _segment_topology(self):
        """(true_33kv_ids, all_11kv_ids, children_by_parent), cached per
        instance — the feeder population and subtraction map shared by every
        segment/voltage total in this service."""
        if self._segment_topology_cache is None:
            feeders_by_id = {f.id: f for f in self._base_feeder_qs()}
            bulk_ids = self._bulk_feeder_ids()
            # CONFIRMED_SUBTRACTED_CHILD_SLUGS (Rangaza, Gezawa, Dr Jamil
            # Gwamna, Gaya) are tagged voltage_level='33kv' in Raven but
            # TCN's own workbook sources their value from the 11KV Load Flow
            # sheet, not a 33KV day-tab meter reading — confirmed 2026-08-08
            # against the Combined sheet directly.
            true_33kv_ids = {
                fid for fid in bulk_ids
                if feeders_by_id.get(fid)
                and feeders_by_id[fid].voltage_level == '33kv'
                and feeders_by_id[fid].slug not in CONFIRMED_SUBTRACTED_CHILD_SLUGS
            }
            all_11kv_ids = {
                fid for fid, f in feeders_by_id.items()
                if (f.voltage_level == '11kv' or f.slug in CONFIRMED_SUBTRACTED_CHILD_SLUGS)
                and f.slug not in CHILD_ZERO_ENERGY_SLUGS
            }
            children_by_parent = defaultdict(list)
            for supplier_id, supplied_id in (
                FeederSupplyRelationship.objects
                .filter(supplier_feeder_id__in=true_33kv_ids, status='active', supplied_feeder_id__in=all_11kv_ids)
                .values_list('supplier_feeder_id', 'supplied_feeder_id')
            ):
                if feeders_by_id[supplied_id].slug in CHILD_NEVER_SUBTRACTED_SLUGS:
                    continue
                children_by_parent[supplier_id].append(supplied_id)
            self._segment_topology_cache = (feeders_by_id, true_33kv_ids, all_11kv_ids, children_by_parent)
        return self._segment_topology_cache

    def _coupling_adjustments(self, from_date, to_date):
        """{date_str: {feeder_id: (add_ids, remove_ids)}} — per-day children-
        list adjustments from any FeederCouplingEvent active during the
        period. On a day a coupling is active: the faulted feeder has the
        coupled feeder(s) REMOVED from its own subtraction list (they
        weren't really feeding from it that day), and the feeder it was
        coupled to has those same feeder(s) ADDED to its subtraction list
        (its gross reading now includes their load, so it must net them out
        instead — otherwise they'd double-count). Empty dict, and therefore
        a no-op, whenever no coupling events overlap the range."""
        events = list(
            FeederCouplingEvent.objects
            .filter(start_date__lte=to_date)
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=from_date))
            .prefetch_related('selected_feeders')
        )
        if not events:
            return {}

        adjustments = defaultdict(lambda: defaultdict(lambda: (set(), set())))
        d = from_date
        while d <= to_date:
            d_str = str(d)
            for event in events:
                if not event.is_active_on(d):
                    continue
                affected = event.affected_feeder_ids()
                if not affected:
                    continue
                adjustments[d_str][event.faulted_feeder_id][1].update(affected)
                adjustments[d_str][event.coupled_to_feeder_id][0].update(affected)
            d += timedelta(days=1)
        return adjustments

    def _segment_voltage_daily_map(self, from_date, to_date):
        """{date_str: {segment: {'33kv': mwh, '11kv': mwh}}} — NET 33KV
        (gross minus verified-subtracted children, floored at 0) plus every
        11KV feeder at full value, attributed by segment. Children lists are
        adjusted per day for any active feeder coupling — see
        _coupling_adjustments().

        CONFIRMED_SUBTRACTED_CHILD_SLUGS members (Rangaza, Gezawa, Dr Jamil
        Gwamna, Gaya) are still correctly subtracted from their true 33KV
        parent below — that part is unchanged and independently verified
        against TCN's own numbers. But their OWN value is attributed to the
        33KV bucket here, not 11KV, even though _segment_topology() classes
        them into all_11kv_ids for subtraction purposes (their reading is
        sourced from TCN's 11KV Load Flow sheet). Confirmed 2026-08-10
        against TCN's own "11KV + 33KV Combined" sheet: their rows are
        literally named "33KV RANGAZA" / "33KV DR JAMIL GWAMNA" etc, and
        TCN's own voltage-level totals bucket them as 33KV. Putting them in
        11KV here (as before) was the entire cause of MDI's 33KV/11KV split
        being wrong by ~1.7 GWh (33KV too low, 11KV too high) while the
        segment TOTAL was already correct — confirmed by simulation this
        never changes any total, only which bucket a value lands in."""
        feeders_by_id, true_33kv_ids, all_11kv_ids, children_by_parent = self._segment_topology()
        all_ids = true_33kv_ids | all_11kv_ids
        per_feeder = _per_feeder_daily_map(all_ids, from_date, to_date)
        coupling = self._coupling_adjustments(from_date, to_date)
        by_day = defaultdict(lambda: defaultdict(lambda: {'33kv': 0.0, '11kv': 0.0}))

        for fid in true_33kv_ids:
            seg = self._segment_label(fid)
            feeder_slug = feeders_by_id[fid].slug
            if feeder_slug in PARENT_UNCLASSIFIED_BY_TCN_SLUGS:
                continue
            base_children = set() if feeder_slug in PARENT_NEVER_SUBTRACTS_SLUGS else set(children_by_parent.get(fid, []))
            for d_str, gross in per_feeder.get(fid, {}).items():
                add_ids, remove_ids = coupling.get(d_str, {}).get(fid, (None, None))
                children = (base_children | add_ids) - remove_ids if add_ids is not None else base_children
                children_sum = sum(per_feeder.get(cid, {}).get(d_str, 0.0) for cid in children)
                # No floor at zero here — confirmed 2026-08-11 against TCN's own
                # "Energy Delivered (MWh)" column: TCN publishes the raw gross-minus-
                # children result directly, negative or not (e.g. ATM's own row read
                # -13.05, -30.99 MWh on several days this period). Flooring here made
                # Raven's own per-parent net silently disagree with TCN's number on
                # every day a parent's children outweighed its gross — a ~420 MWh
                # chunk of the segment/voltage total gap traced directly to this.
                by_day[d_str][seg]['33kv'] += gross - children_sum

        for fid in all_11kv_ids:
            seg = self._segment_label(fid)
            bucket = '33kv' if feeders_by_id[fid].slug in CONFIRMED_SUBTRACTED_CHILD_SLUGS else '11kv'
            for d_str, v in per_feeder.get(fid, {}).items():
                by_day[d_str][seg][bucket] += v

        return by_day

    def get_negative_net_candidates(self):
        """
        Scans every true 33kV bulk parent over [self.from_date, self.to_date]
        for days where gross minus its (coupling-adjusted) children sum goes
        negative BEFORE the max(gross - children_sum, 0) floor in
        _segment_voltage_daily_map hides it — an early warning that
        something's wrong with that parent's children list (a permanent
        topology mistake, a one-off corrupted reading, or an unaccounted-for
        coupling event), surfaced automatically instead of only being found
        by someone manually reconciling against TCN's own numbers.

        Deliberately reuses the exact same topology + coupling-adjusted
        children resolution as _segment_voltage_daily_map (not a separate
        computation), so a day already explained by a logged
        FeederCouplingEvent stops appearing here on its own — this list is
        meant to shrink toward genuinely-unresolved cases as coupling gets
        logged, not stay static.

        Returns a list of dicts: feeder_id, date (str), gross_mwh,
        children_sum_mwh, net_mwh (always negative for anything returned).
        """
        feeders_by_id, true_33kv_ids, all_11kv_ids, children_by_parent = self._segment_topology()
        all_ids = true_33kv_ids | all_11kv_ids
        per_feeder = _per_feeder_daily_map(all_ids, self.from_date, self.to_date)
        coupling = self._coupling_adjustments(self.from_date, self.to_date)

        candidates = []
        for fid in true_33kv_ids:
            feeder_slug = feeders_by_id[fid].slug
            base_children = set() if feeder_slug in PARENT_NEVER_SUBTRACTS_SLUGS else set(children_by_parent.get(fid, []))
            for d_str, gross in per_feeder.get(fid, {}).items():
                add_ids, remove_ids = coupling.get(d_str, {}).get(fid, (None, None))
                children = (base_children | add_ids) - remove_ids if add_ids is not None else base_children
                children_sum = sum(per_feeder.get(cid, {}).get(d_str, 0.0) for cid in children)
                net = gross - children_sum
                if net < -NEGATIVE_NET_MIN_ABS_MWH:
                    candidates.append({
                        'feeder_id':        fid,
                        'date':             d_str,
                        'gross_mwh':        round(gross, 2),
                        'children_sum_mwh': round(children_sum, 2),
                        'net_mwh':          round(net, 2),
                    })
        return candidates

    def _segment_totals(self, from_date, to_date):
        """{segment: total_mwh} — voltage-agnostic sum (33KV NET + 11KV) over
        a period. Used by get_volatility/get_pear/get_energy_by_segment."""
        by_day = self._segment_voltage_daily_map(from_date, to_date)
        totals = defaultdict(float)
        for segs in by_day.values():
            for seg, v in segs.items():
                totals[seg] += v['33kv'] + v['11kv']
        return dict(totals)

    # ── 14. Energy by Voltage (33KV vs 11KV per segment) ─────────────────────

    def get_energy_by_voltage(self):
        """
        Per-segment daily energy split by voltage level (33KV vs 11KV),
        plus month-vs-previous-month totals.
        Covers Slides 13, 14, 15.

        Rewritten 2026-08-08 to match TCN's own workbook logic exactly
        (confirmed against the source Excel's "11KV + 33KV Combined" sheet):
        each 33KV bulk feeder's own value must be genuinely NET (gross meter
        movement minus its actual downstream 11KV children's own energy,
        subtracted explicitly, feeder by feeder), and EVERY 11KV feeder is
        then counted at its own full value on top. This is different from
        get_daily_energy()'s approach (sum 33KV gross readings only, relying
        on the reading already physically including its children) — that
        shortcut is correct for a single grand total, but wrong for a
        by-voltage split, since it never counts 11KV energy on its own line
        at all. Voltage level is read from the feeder's own voltage_level
        field, never inferred from its name (confirmed necessary: feeders
        like Sarkin Yaki and Dawanau Industrial don't have a "33KV" prefix
        despite being 33KV, and some "33KV"-prefixed names are administered
        through the 11KV side of TCN's workbook).
        """
        from datetime import date as date_type

        # Clamp to yesterday — today's cross-day diff is really yesterday's
        # energy filed under today's date (see get_daily_energy()), so the
        # month-comparison total must not include it either.
        to_date = min(self.to_date, date.today() - timedelta(days=1))

        by_day = self._segment_voltage_daily_map(self.from_date, to_date)

        days = []
        for d_str in sorted(by_day):
            if d_str == str(date.today()):
                continue
            entry = {'date': d_str, 'day': int(d_str.split('-')[2]), 'segments': {}}
            for seg in ('MDI', 'MDNI', 'Regional'):
                v      = by_day[d_str].get(seg, {'33kv': 0.0, '11kv': 0.0})
                mwh_33 = round(v['33kv'], 4)
                mwh_11 = round(v['11kv'], 4)
                entry['segments'][seg] = {
                    'energy_33kv_mwh': mwh_33,
                    'energy_11kv_mwh': mwh_11,
                    'total_mwh':       round(mwh_33 + mwh_11, 4),
                    'energy_33kv_gwh': round(mwh_33 / 1000, 4),
                    'energy_11kv_gwh': round(mwh_11 / 1000, 4),
                    'total_gwh':       round((mwh_33 + mwh_11) / 1000, 4),
                }
            days.append(entry)

        # Previous month totals — same NET-33KV + all-11KV logic, just summed
        # over the whole month instead of per day. No scaling anywhere: every
        # feeder gets a segment (see _segment_label()'s Regional catch-all),
        # so MDI+MDNI+Regional already equals the true period total exactly.
        y, m = self.from_date.year, self.from_date.month
        if m == 1:
            prev_y, prev_m = y - 1, 12
        else:
            prev_y, prev_m = y, m - 1
        prev_start = date_type(prev_y, prev_m, 1)
        prev_end   = date_type(prev_y, prev_m, calendar.monthrange(prev_y, prev_m)[1])

        def _period_totals(fd, td):
            by_day_period = self._segment_voltage_daily_map(fd, td)
            totals = defaultdict(lambda: {'33kv': 0.0, '11kv': 0.0})
            for d_str, segs in by_day_period.items():
                for seg, v in segs.items():
                    totals[seg]['33kv'] += v['33kv']
                    totals[seg]['11kv'] += v['11kv']
            return totals

        curr_totals = _period_totals(self.from_date, to_date)
        prev_totals = _period_totals(prev_start, prev_end)

        month_comparison = {}
        for seg in ('MDI', 'MDNI', 'Regional'):
            c = curr_totals.get(seg, {'33kv': 0.0, '11kv': 0.0})
            p = prev_totals.get(seg, {'33kv': 0.0, '11kv': 0.0})

            def _vol(v33, v11):
                return {
                    'energy_33kv_mwh': round(v33, 2),
                    'energy_11kv_mwh': round(v11, 2),
                    'total_mwh':       round(v33 + v11, 2),
                    'energy_33kv_gwh': round(v33 / 1000, 4),
                    'energy_11kv_gwh': round(v11 / 1000, 4),
                    'total_gwh':       round((v33 + v11) / 1000, 4),
                }

            month_comparison[seg] = {
                'current_month':  _vol(c['33kv'], c['11kv']),
                'previous_month': _vol(p['33kv'], p['11kv']),
            }

        return {
            'period':           {'from': str(self.from_date), 'to': str(self.to_date)},
            'days':             days,
            'month_comparison': month_comparison,
            'methodology':      METHODOLOGY['energy_by_voltage'],
        }

    # ── 15. Techno-Commercial Incidents ──────────────────────────────────────

    def get_incidents(self):
        """
        Weekly Techno-Commercial Incidence report driven by FeederInterruption.
        Financial loss is calculated automatically:
            Loss (₦) = avg_load_mw × duration_hours × 1,000 × tariff_per_kwh
        Where:
            avg_load_mw   = feeder's 30-day average MW (non-zero hours from HourlyLoad)
            duration_hours = occurred_at → restored_at (or now if still lingering)
            tariff_per_kwh = from TMOMonthlySegmentTarget for feeder's pl_segment
        Covers Slide 16.
        """
        from django.utils import timezone as tz

        qs = (
            FeederInterruption.objects
            .filter(
                occurred_at__date__gte=self.from_date,
                occurred_at__date__lte=self.to_date,
            )
            # Only genuine DISCO (11KV/33KV distribution-side) faults belong in a
            # techno-commercial FAULT incidence report — exclude everything that
            # isn't a real DISCO fault:
            #   - Load shedding (L/S, L/S GS, 330KV L/S, T/LS) — planned, generation-
            #     driven, not a feeder fault
            #   - TCN/transmission-side faults (132KV/330KV-prefixed types, "tcn") —
            #     TCN's network, not KEDCO's
            #   - Maintenance (MTCE, MTNC, 132KV MTCE) — planned work, not a fault
            #   - Administrative/status codes (permit, NO RI, N/A, OFF) — not faults
            .exclude(interruption_type__icontains='L/S')
            .exclude(interruption_type__icontains='LS')
            .exclude(interruption_type__istartswith='132KV')
            .exclude(interruption_type__istartswith='330KV')
            .exclude(interruption_type='tcn')
            .exclude(interruption_type__in=['MTCE', 'MTNC', 'permit', 'NO RI', 'N/A', 'OFF'])
            .select_related(
                'feeder', 'feeder__band',
                'feeder__substation', 'feeder__substation__state',
                'feeder__business_district',
            )
        )

        f = self.filters
        if f.get('state'):
            qs = qs.filter(feeder__substation__state__slug=f['state'])
        if f.get('district'):
            qs = qs.filter(feeder__business_district__slug=f['district'])
        if f.get('feeder'):
            qs = qs.filter(feeder__slug=f['feeder'])
        if f.get('segment'):
            seg = f['segment'].upper()
            if seg == 'MDI':
                qs = qs.filter(feeder_id__in=self.mdi_ids)
            elif seg == 'MDNI':
                qs = qs.filter(feeder_id__in=self.mdni_ids)
        if f.get('status'):
            s = f['status'].lower()
            if s == 'rectified':
                qs = qs.exclude(restored_at=None)
            elif s == 'lingering':
                qs = qs.filter(restored_at=None)

        # Build tariff lookup: pl_segment → ₦/kWh
        tariff_map = {
            t.segment: float(t.average_tariff_per_kwh)
            for t in TMOMonthlySegmentTarget.objects.filter(
                year=self.from_date.year, month=self.from_date.month,
            )
        }

        # Build feeder avg-load lookup over past 30 days (non-zero hours only)
        feeder_ids = list(qs.values_list('feeder_id', flat=True).distinct())
        avg_load_30d: dict = {}
        if feeder_ids:
            lookback = self.to_date - timedelta(days=30)
            for row in (
                HourlyLoad.objects
                .filter(feeder_id__in=feeder_ids, date__gte=lookback,
                        date__lte=self.to_date, load_mw__gt=0)
                .values('feeder_id')
                .annotate(avg_mw=Avg('load_mw'))
            ):
                avg_load_30d[row['feeder_id']] = float(row['avg_mw'] or 0)

        now = tz.now()
        rows = []
        total_loss = 0.0
        rectified  = 0
        lingering  = 0

        for obj in qs:
            feeder  = obj.feeder
            is_done = obj.restored_at is not None
            status  = 'rectified' if is_done else 'lingering'

            # Duration
            end_time  = obj.restored_at if is_done else now
            occurred  = obj.occurred_at if tz.is_aware(obj.occurred_at) else tz.make_aware(obj.occurred_at)
            end_aware = end_time if tz.is_aware(end_time) else tz.make_aware(end_time)
            duration_hrs = max((end_aware - occurred).total_seconds() / 3600, 0)

            # Average load (MW) — fallback to 0 if no HourlyLoad data
            avg_mw = avg_load_30d.get(feeder.id, 0.0)

            # Tariff — use feeder's pl_segment, fall back to band order
            seg = feeder.pl_segment or 'Regions'
            tariff = tariff_map.get(seg, tariff_map.get('Regions', 0.0))

            # Financial loss = energy not served × tariff
            energy_not_served_mwh = avg_mw * duration_hrs
            loss = energy_not_served_mwh * 1_000 * tariff

            total_loss += loss
            if is_done:
                rectified += 1
            else:
                lingering += 1

            rows.append({
                'id':                   str(obj.id),
                'feeder_name':          feeder.name,
                'feeder_slug':          feeder.slug,
                'voltage':              feeder.voltage_level,
                'pl_segment':           seg,
                'coordinate':           feeder.substation.state.name if feeder.substation and feeder.substation.state else '',
                'region':               feeder.business_district.name if feeder.business_district else '',
                'nature_of_fault':      obj.get_interruption_type_display() + (f' — {obj.description}' if obj.description else ''),
                'interruption_type':    obj.interruption_type,
                'status':               status,
                'occurred_at':          obj.occurred_at.isoformat(),
                'restored_at':          obj.restored_at.isoformat() if obj.restored_at else None,
                'duration_hours':       round(duration_hrs, 2),
                'avg_load_mw':          round(avg_mw, 4),
                'energy_not_served_mwh': round(energy_not_served_mwh, 4),
                'tariff_per_kwh':       tariff,
                'financial_loss_ngn':   round(loss, 2),
            })

        total = rectified + lingering
        return {
            'period': {'from': str(self.from_date), 'to': str(self.to_date)},
            'summary': {
                'total_incidents':          total,
                'rectified':                rectified,
                'lingering':                lingering,
                'rectification_rate_pct':   round(_pct(rectified, total), 1),
                'total_financial_loss_ngn': round(total_loss, 2),
            },
            'incidents': rows,
            'methodology': METHODOLOGY['incidents'],
        }

    # ── 16. GCR (Energy Gap-to-Cost Ratio) ───────────────────────────────────

    def get_gcr(self):
        """
        P&L Target vs Billing Value Realization — Energy Gap-to-Cost Ratio.
        Uses TMOMonthlySegmentTarget.average_tariff_per_kwh to compute bill values.
        Covers Slide 18.
        """
        feeder_ids = set(self._base_feeder_qs().values_list('id', flat=True))

        target_qs = TMOMonthlySegmentTarget.objects.filter(
            year=self.from_date.year,
            month=self.from_date.month,
        )
        targets = {t.segment: t for t in target_qs}

        # Fallback: if current month has no targets, use the most recent available month
        if not targets:
            latest = (
                TMOMonthlySegmentTarget.objects
                .order_by('-year', '-month')
                .values_list('year', 'month')
                .first()
            )
            if latest:
                targets = {
                    t.segment: t
                    for t in TMOMonthlySegmentTarget.objects.filter(
                        year=latest[0], month=latest[1],
                    )
                }

        # Reuse get_energy_by_segment()'s actual_mwh — it's the single source of truth
        # for "actual energy consumed per segment" (retail-level classification, scaled
        # onto the bulk network total). Previously this method recomputed its own 33KV-
        # only, unstripped total here, which double-counted district bulk feeders (e.g.
        # BRISCOE, ZARIA ROAD) that also supply metered downstream 11KV feeders — that
        # produced a "consumed_gwh" wildly inconsistent with the actual-consumption
        # slide for the same segment/period.
        seg_actual_mwh = {s['segment']: s['actual_mwh'] for s in self.get_energy_by_segment()['segments']}

        def _actual(seg_name):
            return seg_actual_mwh.get(seg_name, 0.0)

        seg_data = [
            ('MDI',     self.mdi_ids & feeder_ids),
            ('MDNI',    self.mdni_ids & feeder_ids),
            ('Regions', feeder_ids - self.mdi_ids - self.mdni_ids),
        ]

        rows = []
        grand_target_mwh   = 0.0
        grand_actual_mwh   = 0.0
        grand_exp_bill     = 0.0
        grand_mtd_bill     = 0.0

        for seg_name, ids in seg_data:
            t = targets.get(seg_name)
            target_mwh  = float(t.target_energy_mwh) if t else 0.0
            tariff      = float(t.average_tariff_per_kwh) if t else 0.0
            actual_mwh  = _actual(seg_name)
            gap_mwh     = target_mwh - actual_mwh
            # tariff is ₦/kWh; convert MWh → kWh before multiplying
            exp_bill    = target_mwh * 1_000 * tariff
            mtd_bill    = actual_mwh * 1_000 * tariff
            gap_bill    = exp_bill - mtd_bill
            mtd_ach     = _pct(actual_mwh, target_mwh)

            grand_target_mwh += target_mwh
            grand_actual_mwh += actual_mwh
            grand_exp_bill   += exp_bill
            grand_mtd_bill   += mtd_bill

            rows.append({
                'segment':              seg_name,
                'target_gwh':           round(target_mwh / 1000, 4),
                'consumed_gwh':         round(actual_mwh / 1000, 4),
                'gap_gwh':              round(gap_mwh / 1000, 4),
                'expected_bill_value':  round(exp_bill, 2),
                'mtd_bill_value':       round(mtd_bill, 2),
                'gap_bill_value':       round(gap_bill, 2),
                'mtd_achievement_pct':  round(mtd_ach, 1),
                'gap_pct':              round(100 - mtd_ach, 1),
                'average_tariff_per_kwh': tariff,
            })

        grand_ach = _pct(grand_actual_mwh, grand_target_mwh)
        rows.append({
            'segment':              'Total',
            'target_gwh':           round(grand_target_mwh / 1000, 4),
            'consumed_gwh':         round(grand_actual_mwh / 1000, 4),
            'gap_gwh':              round((grand_target_mwh - grand_actual_mwh) / 1000, 4),
            'expected_bill_value':  round(grand_exp_bill, 2),
            'mtd_bill_value':       round(grand_mtd_bill, 2),
            'gap_bill_value':       round(grand_exp_bill - grand_mtd_bill, 2),
            'mtd_achievement_pct':  round(grand_ach, 1),
            'gap_pct':              round(100 - grand_ach, 1),
            'average_tariff_per_kwh': None,
        })

        return {
            'period':   {'from': str(self.from_date), 'to': str(self.to_date)},
            'rows':     rows,
            'segments': rows,   # alias used by report renderers and GCR slide
            'methodology': METHODOLOGY['gcr'],
        }

    # ── 17. Monitored New Feeders — daily energy from commissioning date ─────

    def get_monitored_feeders(self):
        """
        New feeders under active monitoring (Feeder.monitoring_end_date >= today).
        Each feeder returns daily MWh from its onboarded_at date to today,
        so the TMO dashboard can show the "Dawanau feeder: Daily Energy Allocation" style chart.
        """
        today = date.today()
        qs = (
            Feeder.objects
            .filter(is_onboarded=True, monitoring_end_date__gte=today)
            .select_related('band', 'substation', 'substation__state', 'business_district')
        )

        feeders_out = []
        for feeder in qs:
            # Monitor from onboarding date (or self.from_date if later)
            monitor_start = (
                feeder.onboarded_at.date() if feeder.onboarded_at else self.from_date
            )
            from_d = max(monitor_start, self.from_date)
            to_d   = self.to_date

            day_map   = _feeder_energy_by_day(feeder.id, from_d, to_d)
            total_mwh = sum(day_map.values())

            # Build day-by-day array covering full monitoring window
            days = []
            cur  = from_d
            while cur <= to_d:
                d_str = str(cur)
                days.append({
                    'date': d_str,
                    'day':  cur.day,
                    'mwh':  round(day_map.get(d_str, 0.0), 2),
                })
                cur += timedelta(days=1)

            feeders_out.append({
                'feeder_id':          str(feeder.id),
                'feeder_name':        feeder.name,
                'feeder_slug':        feeder.slug,
                'voltage_level':      feeder.voltage_level,
                'band':               feeder.band.name if feeder.band else '',
                'state':              (feeder.substation.state.name
                                      if feeder.substation and feeder.substation.state else ''),
                'district':           feeder.business_district.name if feeder.business_district else '',
                'onboarded_at':       str(feeder.onboarded_at.date()) if feeder.onboarded_at else None,
                'monitoring_end_date': str(feeder.monitoring_end_date),
                'monitoring_from':    str(from_d),
                'total_mwh':          round(total_mwh, 2),
                'days':               days,
            })

        return {
            'as_of':          str(today),
            'feeder_count':   len(feeders_out),
            'feeders':        feeders_out,
        }

    # ── 18. Minigrids Daily (SSF — per-feeder daily MWh + summary table) ────

    def get_minigrids_daily(self, feeder_slug=None, q=None, band=None, segment=None):
        """
        Per-feeder daily MWh chart.
        If feeder_slug / q / band / segment provided → any matching feeder.
        Default (no params): minigrid feeders only (original SSF behaviour).
        """
        # Clamp to yesterday — today's cross-day diff is really yesterday's energy
        # filed under today's date (see get_daily_energy()), so it can never be a
        # real, finished number until today is over.
        to_date = min(self.to_date, date.today() - timedelta(days=1))

        feeder_qs = self._base_feeder_qs()
        if feeder_slug:
            feeder_qs = feeder_qs.filter(slug=feeder_slug)
        elif q or band or segment:
            if q:
                feeder_qs = feeder_qs.filter(name__icontains=q)
            if band:
                feeder_qs = feeder_qs.filter(band__slug__iexact=band)
            if segment:
                feeder_qs = feeder_qs.filter(pl_segment__iexact=segment)
        else:
            feeder_qs = feeder_qs.filter(is_minigrid=True)
        feeder_ids = list(feeder_qs.values_list('id', flat=True))

        if not feeder_ids:
            return {
                'period':   {'from': str(self.from_date), 'to': str(to_date)},
                'feeders':  [],
                'summary':  {'total_mwh': 0.0, 'days': []},
            }

        # All dates in the period
        all_dates = []
        cur = self.from_date
        while cur <= to_date:
            all_dates.append(str(cur))
            cur += timedelta(days=1)

        # Per-feeder daily energy (balloon+system fallback)
        feeders_out = []
        summary_by_date = defaultdict(float)

        for feeder in feeder_qs:
            day_map   = _feeder_energy_by_day(feeder.id, self.from_date, to_date)
            total_mwh = sum(day_map.values())

            days = []
            for d_str in all_dates:
                mwh = day_map.get(d_str, 0.0)
                summary_by_date[d_str] += mwh
                days.append({
                    'date': d_str,
                    'day':  int(d_str.split('-')[2]),
                    'mwh':  round(mwh, 2),
                })

            feeders_out.append({
                'feeder_id':   str(feeder.id),
                'feeder_name': feeder.name,
                'feeder_slug': feeder.slug,
                'state':       (feeder.substation.state.name
                                if feeder.substation and feeder.substation.state else ''),
                'total_mwh':   round(total_mwh, 2),
                'days':        days,
            })

        # Sort by total MWh descending
        feeders_out.sort(key=lambda f: f['total_mwh'], reverse=True)

        # Summary table — all minigrids combined per day
        summary_days = [
            {
                'date': d_str,
                'day':  int(d_str.split('-')[2]),
                'mwh':  round(summary_by_date[d_str], 2),
            }
            for d_str in all_dates
        ]
        grand_total = round(sum(summary_by_date.values()), 2)

        return {
            'period':  {'from': str(self.from_date), 'to': str(to_date)},
            'feeders': feeders_out,          # one entry per minigrid → individual bar charts
            'summary': {                     # aggregated → combined table
                'total_mwh': grand_total,
                'days':      summary_days,
            },
        }

    # ── 18. Daily Real-Time Allocation (TCN vs Actual Consumption) ───────────

    def get_daily_allocation(self):
        """
        Per-day comparison of KEDCO allocation vs DISCO offtake from TMONetworkDispatch.
        Uses the same data source as the live dashboard (tmo_tmonetworkdispatch table).
        kedco_allocation_mw → expected_mw
        disco_offtake_mw    → actual_mw
        variance_mw         → unpicked_mw (positive = underpicked, negative = overrun)
        """
        dispatch_map = {
            str(obj.date): obj
            for obj in TMONetworkDispatch.objects.filter(
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).order_by('date')
        }

        all_dates = set(dispatch_map.keys())
        cur = self.from_date
        while cur <= self.to_date:
            all_dates.add(str(cur))
            cur += timedelta(days=1)

        days = []
        for d_str in sorted(all_dates):
            # Today's row is only ever a partial-day average of whatever hours have
            # been entered in the sheet so far — not a finished day. Don't show it
            # as if it were final, same rule as get_daily_energy().
            if d_str == str(date.today()):
                continue
            obj      = dispatch_map.get(d_str)
            expected = float(obj.kedco_allocation_mw or 0) if obj else 0.0
            actual   = float(obj.disco_offtake_mw    or 0) if obj else 0.0
            unpicked = float(obj.variance_mw         or 0) if obj else 0.0
            days.append({
                'date':        d_str,
                'day':         int(d_str.split('-')[2]),
                'expected_mw': round(expected, 2),
                'actual_mw':   round(actual,   2),
                'unpicked_mw': round(unpicked,  2),
            })

        total_expected = sum(d['expected_mw'] for d in days)
        total_actual   = sum(d['actual_mw']   for d in days)
        total_unpicked = total_expected - total_actual

        return {
            'period': {'from': str(self.from_date), 'to': str(self.to_date)},
            'summary': {
                'total_expected_mw':  round(total_expected, 2),
                'total_actual_mw':    round(total_actual,   2),
                'total_unpicked_mw':  round(total_unpicked, 2),
            },
            'days': days,
            'methodology': METHODOLOGY['daily_allocation'],
        }

    # ── 18. Single Feeder Detail ─────────────────────────────────────────────

    def get_feeder_detail(self, feeder_slug):
        feeder = Feeder.objects.select_related(
            'band', 'substation', 'substation__state', 'business_district'
        ).get(slug=feeder_slug, is_onboarded=True)

        energy_by_day = _feeder_energy_by_day(feeder.id, self.from_date, self.to_date)

        hours_map = {
            str(row['date']): float(row['hours_supplied'] or 0)
            for row in DailyHoursOfSupply.objects.filter(
                feeder=feeder,
                date__gte=self.from_date,
                date__lte=self.to_date,
            ).values('date', 'hours_supplied')
        }

        target_map = {
            str(row['target_date']): float(row['target_mwh'] or 0)
            for row in TMOFeederTarget.objects.filter(
                feeder=feeder,
                target_date__gte=self.from_date,
                target_date__lte=self.to_date,
            ).values('target_date', 'target_mwh')
        }

        days = []
        for d_str in sorted(energy_by_day):
            actual = energy_by_day[d_str]
            target = target_map.get(d_str, 0.0)
            hours  = hours_map.get(d_str, 0.0)
            ach    = _pct(actual, target)
            days.append({
                'date':           d_str,
                'target_mwh':     round(target, 4),
                'actual_mwh':     round(actual, 4),
                'variance_mwh':   round(actual - target, 4),
                'achievement_pct': round(ach, 1),
                'hours_supplied': round(hours, 2),
                'status':         _compliance(ach),
            })

        total_actual = sum(d['actual_mwh'] for d in days)
        total_target = sum(d['target_mwh'] for d in days)
        ov_ach       = _pct(total_actual, total_target)

        return {
            'feeder': {
                'id':            str(feeder.id),
                'name':          feeder.name,
                'slug':          feeder.slug,
                'segment':       self._segment_label(feeder.id),
                'band':          feeder.band.name if feeder.band else '',
                'voltage_level': feeder.voltage_level,
                'state':         (feeder.substation.state.name
                                 if feeder.substation and feeder.substation.state else ''),
                'district':      feeder.business_district.name if feeder.business_district else '',
                'is_minigrid':   feeder.is_minigrid,
            },
            'period': {'from': str(self.from_date), 'to': str(self.to_date)},
            'days':   days,
            'summary': {
                'total_target_mwh':        round(total_target, 2),
                'total_actual_mwh':        round(total_actual, 2),
                'variance_mwh':            round(total_actual - total_target, 2),
                'overall_achievement_pct': round(ov_ach, 1),
                'overall_status':          _compliance(ov_ach),
            },
        }

    def get_feeder_scoped_summary(self, feeder_ids):
        """
        Per-feeder summary for an arbitrary, user-selected subset of feeders —
        the data behind report sections scoped to specific feeders (e.g. "build
        me a report for just these 5 feeders"). Deliberately built by calling
        get_feeder_detail() once per feeder rather than narrowing the shared
        bulk/topology population to this subset: _bulk_feeder_ids()/
        _segment_topology() need to see the FULL network to net a 33kV
        parent's children correctly, so filtering that population down to an
        arbitrary subset first would silently corrupt any NET-of-children
        figure (same class of bug fixed earlier for the coupling-event
        dropdown, where narrowing the population broke topology lookups). By
        reusing get_feeder_detail() per feeder, every number here is computed
        against the correct, full network exactly as everywhere else in the
        dashboard, then only the requested feeders' own rows are surfaced.
        """
        feeders_by_id, true_33kv_ids, _, children_by_parent = self._segment_topology()

        rows = []
        for fid in feeder_ids:
            f = feeders_by_id.get(fid)
            if f is None:
                continue
            detail = self.get_feeder_detail(f.slug)
            row = {
                'feeder':  detail['feeder'],
                'summary': detail['summary'],
                'days':    detail['days'],
            }
            if fid in true_33kv_ids and children_by_parent.get(fid):
                children = [
                    {'name': feeders_by_id[cid].name, 'slug': feeders_by_id[cid].slug}
                    for cid in children_by_parent[fid] if cid in feeders_by_id
                ]
                row['children'] = children
                row['has_downstream_network'] = True
            else:
                row['children'] = []
                row['has_downstream_network'] = False
            rows.append(row)

        total_target = sum(r['summary']['total_target_mwh'] for r in rows)
        total_actual = sum(r['summary']['total_actual_mwh'] for r in rows)
        ov_ach = _pct(total_actual, total_target)

        return {
            'period':  {'from': str(self.from_date), 'to': str(self.to_date)},
            'feeders': rows,
            'summary': {
                'feeder_count':            len(rows),
                'total_target_mwh':        round(total_target, 2),
                'total_actual_mwh':        round(total_actual, 2),
                'total_target_gwh':        round(total_target / 1000, 4),
                'total_actual_gwh':        round(total_actual / 1000, 4),
                'variance_mwh':            round(total_actual - total_target, 2),
                'overall_achievement_pct': round(ov_ach, 1),
                'overall_status':          _compliance(ov_ach),
            },
        }
