# technical/utils/energy_utils.py
"""
Shared smart energy delivered calculation.

Per-feeder logic:
  PRIMARY  (meter)  — EnergyDelivered.energy_mwh when records exist and the
                      average daily value is within the balloon limit.
  FALLBACK (system) — avg_load_mw × supply_hours from HourlyLoad when
                      EnergyDelivered is missing or contains outliers.

Usage:
    from technical.utils.energy_utils import calculate_energy_delivered

    result = calculate_energy_delivered(feeder_ids, from_date, to_date)
    total  = result['total_mwh']          # float, MWh
    meter  = result['meter_feeders']      # int
    system = result['system_feeders']     # int
"""

from django.db.models import Sum, Avg, Count, Max
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
