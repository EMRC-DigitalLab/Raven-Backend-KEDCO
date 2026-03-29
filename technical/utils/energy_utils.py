# technical/utils/energy_utils.py
"""
Shared technical metric calculations.

All functions accept feeder_ids as a parameter so they work with any scope
(network-wide, per state, per district, per report filter set, etc.).
Callers are responsible for scoping feeder IDs before calling.

Energy delivered:
  PRIMARY  (meter)  — EnergyDelivered.energy_mwh when records exist and the
                      MAX single-day value is within the balloon limit.
  FALLBACK (system) — avg_load_mw × supply_hours from HourlyLoad.

Usage:
    from technical.utils.energy_utils import (
        calculate_energy_delivered,
        calculate_hours_of_supply,
        calculate_average_load,
    )
"""

from django.db import connection
from django.db.models import Avg, Count, Max, Sum
from django.utils import timezone

from technical.models import EnergyDelivered, HourlyLoad

# Max acceptable daily energy per feeder (MWh).
# If ANY single day's reading exceeds this, treat the whole feeder's meter
# data as suspect (wrong units, cumulative meter not reset, etc.) and replace
# all of it with the system estimate derived from HourlyLoad.
#
# WHY MAX instead of AVG:
#   Using avg (total/days) masked outlier days when queried over a long period.
#   E.g., one 600 MWh day + 27 × 20 MWh days → avg = 40 MWh → passed the check
#   → monthly total included the 600 MWh spike.  But the same day queried alone
#   (600/1 > 500) → rejected → fell back to ~20 MWh.  This caused monthly total
#   to be ~3× larger than the sum of daily queries.  Using MAX ensures the same
#   feeder classification regardless of the date range queried.
DAILY_BALLOON_LIMIT = 500.0


def calculate_energy_delivered(feeder_ids, from_date, to_date):
    """
    Smart per-feeder energy delivered calculation.

    For each feeder in feeder_ids:
      PRIMARY  (meter)  — EnergyDelivered.energy_mwh, if records exist AND
                          the MAX single-day value ≤ DAILY_BALLOON_LIMIT (500 MWh).
      FALLBACK (system) — avg_load_mw × supply_hours from HourlyLoad.

    Using MAX (not AVG) for the outlier check guarantees that the result for
    any date range equals the sum of results for each individual day within
    that range — i.e., monthly total = Σ daily totals.

    Args:
        feeder_ids : list of feeder UUIDs (already filtered by scope/voltage)
        from_date  : date
        to_date    : date

    Returns:
        dict:
            total_mwh      — Total energy delivered (MWh), float
            meter_feeders  — Number of feeders that used real meter data, int
            system_feeders — Number of feeders that used system estimate, int
    """
    if not feeder_ids:
        return {'total_mwh': 0.0, 'meter_feeders': 0, 'system_feeders': 0}

    # ── Step 1: Collect valid EnergyDelivered totals per feeder ──────────────
    # Use MAX daily value for the balloon check so that a single bad day
    # rejects the feeder regardless of how long the date range is.
    ed_by_feeder = {}
    ed_records = (
        EnergyDelivered.objects
        .filter(feeder_id__in=feeder_ids, date__gte=from_date, date__lte=to_date)
        .values('feeder_id')
        .annotate(total=Sum('energy_mwh'), max_daily=Max('energy_mwh'), days=Count('id'))
    )
    for row in ed_records:
        fid = row['feeder_id']
        total = float(row['total'] or 0)
        max_daily = float(row['max_daily'] or 0)
        days = int(row['days'] or 1)
        # ✅ FIX: reject if ANY single day exceeds the balloon limit
        # (previously used avg which masked outlier days over long ranges)
        if days > 0 and 0 < max_daily <= DAILY_BALLOON_LIMIT:
            ed_by_feeder[fid] = total

    # ── Step 2: System estimate for feeders with no valid meter data ──────────
    feeders_needing_system = [fid for fid in feeder_ids if fid not in ed_by_feeder]

    system_by_feeder = {}
    if feeders_needing_system:
        hl_records = (
            HourlyLoad.objects
            .filter(
                feeder_id__in=feeders_needing_system,
                date__gte=from_date,
                date__lte=to_date,
                load_mw__gt=0,
            )
            .values('feeder_id')
            .annotate(avg_load=Avg('load_mw'), supply_hours=Count('id'))
        )
        for row in hl_records:
            fid = row['feeder_id']
            avg_load = float(row['avg_load'] or 0)
            supply_hours = int(row['supply_hours'] or 0)
            system_by_feeder[fid] = avg_load * supply_hours

    total_mwh = sum(ed_by_feeder.values()) + sum(system_by_feeder.values())

    return {
        'total_mwh': round(total_mwh, 2),
        'meter_feeders': len(ed_by_feeder),
        'system_feeders': len(feeders_needing_system),
    }


def calculate_energy_delivered_per_feeder(feeder_ids, from_date, to_date):
    """
    Same hybrid logic as calculate_energy_delivered but returns a per-feeder dict.

    Returns:
        {feeder_id: {'mwh': float, 'source': 'meter' | 'system'}}

    Feeders with zero data in both sources are not included in the result
    (caller should treat missing key as 0 / no_data).
    """
    if not feeder_ids:
        return {}

    # ── Step 1: valid meter data per feeder ───────────────────────────────────
    ed_by_feeder = {}
    ed_records = (
        EnergyDelivered.objects
        .filter(feeder_id__in=feeder_ids, date__gte=from_date, date__lte=to_date)
        .values('feeder_id')
        .annotate(total=Sum('energy_mwh'), max_daily=Max('energy_mwh'), days=Count('id'))
    )
    for row in ed_records:
        fid = row['feeder_id']
        total = float(row['total'] or 0)
        max_daily = float(row['max_daily'] or 0)
        days = int(row['days'] or 1)
        if days > 0 and 0 < max_daily <= DAILY_BALLOON_LIMIT:
            ed_by_feeder[fid] = total

    # ── Step 2: system estimate for feeders with no valid meter data ──────────
    feeders_needing_system = [fid for fid in feeder_ids if fid not in ed_by_feeder]
    system_by_feeder = {}
    if feeders_needing_system:
        hl_records = (
            HourlyLoad.objects
            .filter(
                feeder_id__in=feeders_needing_system,
                date__gte=from_date,
                date__lte=to_date,
                load_mw__gt=0,
            )
            .values('feeder_id')
            .annotate(avg_load=Avg('load_mw'), supply_hours=Count('id'))
        )
        for row in hl_records:
            fid = row['feeder_id']
            system_by_feeder[fid] = float(row['avg_load'] or 0) * int(row['supply_hours'] or 0)

    result = {}
    for fid, mwh in ed_by_feeder.items():
        result[fid] = {'mwh': round(mwh, 2), 'source': 'meter'}
    for fid, mwh in system_by_feeder.items():
        if mwh > 0:
            result[fid] = {'mwh': round(mwh, 2), 'source': 'system'}

    return result


def calculate_hours_of_supply(feeder_ids, from_date, to_date):
    """
    Average hours of supply per day for the given feeder IDs.

    Single-day  → avg hours per feeder for that day   (max 24)
    Multi-day   → avg hours per day per feeder         (max 24)

    Uses the same SQL used by calculate_hours_of_supply_network() in
    overview_views — one implementation, many callers.

    Args:
        feeder_ids : list of feeder UUIDs already scoped by the caller
        from_date  : date
        to_date    : date

    Returns: float (hours, 0–24)
    """
    if not feeder_ids:
        return 0.0

    placeholders = ','.join(['%s'] * len(feeder_ids))
    query = f"""
        SELECT COUNT(DISTINCT CONCAT(feeder_id, '-', date, '-', hour))
        FROM technical_hourlyload
        WHERE date BETWEEN %s AND %s
          AND load_mw > 0
          AND feeder_id IN ({placeholders})
    """
    with connection.cursor() as cursor:
        cursor.execute(query, [from_date, to_date] + list(feeder_ids))
        row = cursor.fetchone()
        total_hours = row[0] if row and row[0] else 0

    total_feeders = len(feeder_ids)
    if from_date == to_date:
        avg = total_hours / total_feeders if total_feeders else 0.0
    else:
        period_days = (to_date - from_date).days + 1
        avg = total_hours / (total_feeders * period_days) if (total_feeders * period_days) else 0.0

    return round(min(avg, 24.0), 2)


def calculate_average_load(feeder_ids, from_date, to_date):
    """
    Average load per feeder per hour for the given feeder IDs.

    Formula: total_load_mw / (total_feeders × period_hours)
    For current-day periods, period_hours uses actual elapsed hours so the
    average is not diluted by future hours that haven't happened yet.

    Args:
        feeder_ids : list of feeder UUIDs already scoped by the caller
        from_date  : date
        to_date    : date

    Returns: float (MW)
    """
    if not feeder_ids:
        return 0.0

    today = timezone.now().date()
    now = timezone.now()

    if to_date == today:
        full_days = (to_date - from_date).days
        hours_elapsed = now.hour + (now.minute / 60.0)
        period_hours = (full_days * 24) + hours_elapsed or 1.0
    else:
        period_hours = ((to_date - from_date).days + 1) * 24

    result = HourlyLoad.objects.filter(
        feeder_id__in=feeder_ids,
        date__range=(from_date, to_date),
    ).aggregate(total_load=Sum('load_mw'), peak_load=Max('load_mw'))

    total_load = float(result['total_load'] or 0)
    peak_load = float(result['peak_load'] or 0)
    total_feeders = len(feeder_ids)

    avg = total_load / (total_feeders * period_hours) if (total_feeders * period_hours) else 0.0
    return round(avg, 2), round(peak_load, 2)
