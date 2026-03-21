# technical/utils/compliance_utils.py
"""
NERC/MYTO Service Level Compliance utilities.

Band targets (hours/day):
  A = 20  B = 16  C = 12  D = 8  E = 4

Status rules:
  upgrade_eligible  — consecutive days at NEXT band's level >= 7
  critical          — consecutive days BELOW target >= 7  (NERC auto-downgrade trigger)
  at_risk           — consecutive days BELOW target >= 2  (NERC must-publish-explanation)
  compliant         — everything else
"""
from datetime import date, timedelta

from django.db import connection
from django.utils import timezone

# Slug → target hours mapping (MYTO order)
BAND_TARGET_HOURS = {
    'a': 20.0,
    'b': 16.0,
    'c': 12.0,
    'd': 8.0,
    'e': 4.0,
}

# What the NEXT band up requires (for upgrade eligibility)
BAND_NEXT_LEVEL_HOURS = {
    'b': 20.0,   # B → A requires hitting 20 hrs for 7 days
    'c': 16.0,   # C → B
    'd': 12.0,   # D → C
    'e': 8.0,    # E → D
    # 'a' omitted — already highest band
}

# Canonical band order for sorting (A first)
BAND_ORDER = {'a': 0, 'b': 1, 'c': 2, 'd': 3, 'e': 4}


def _date_range(from_date, to_date):
    """All dates from from_date to to_date inclusive."""
    days = (to_date - from_date).days + 1
    return [from_date + timedelta(days=i) for i in range(days)]


def _cap_to_today(to_date):
    today = timezone.now().date()
    return min(to_date, today)


def bulk_daily_hours(feeder_ids, from_date, to_date):
    """
    One query: returns per-feeder per-day hours supplied.
    Result: {feeder_id: {date: hours_supplied}}
    Days with no HourlyLoad rows default to 0 (handled by caller).
    """
    if not feeder_ids:
        return {}

    placeholders = ','.join(['%s'] * len(feeder_ids))
    query = f"""
        SELECT
            feeder_id,
            date,
            COUNT(DISTINCT hour) AS hours_supplied
        FROM technical_hourlyload
        WHERE feeder_id IN ({placeholders})
            AND date BETWEEN %s AND %s
            AND load_mw > 0
        GROUP BY feeder_id, date
    """
    params = list(feeder_ids) + [from_date, to_date]

    result = {fid: {} for fid in feeder_ids}
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        for feeder_id, day, hours in cursor.fetchall():
            result[feeder_id][day] = float(hours)

    return result


def compute_compliance(daily_hours_map, band_slug, target_hours, period_dates):
    """
    Compute compliance metrics for a single feeder.

    Args:
        daily_hours_map: {date: hours_supplied} — only dates with data; missing = 0
        band_slug:       e.g. 'a', 'b', 'c', 'd', 'e'
        target_hours:    float — band's NERC minimum hrs/day
        period_dates:    list of date objects (oldest → newest) for the period

    Returns dict with all compliance fields.
    """
    if not period_dates:
        return _empty_compliance(target_hours)

    days_in_period = len(period_dates)
    hours_per_day = [daily_hours_map.get(d, 0.0) for d in period_dates]
    total_hours = sum(hours_per_day)
    avg_hours = total_hours / days_in_period

    days_compliant = sum(1 for h in hours_per_day if h >= target_hours)
    days_failed = days_in_period - days_compliant

    # Consecutive days BELOW target — counted from the END of the period backward
    consecutive_failed = 0
    for h in reversed(hours_per_day):
        if h < target_hours:
            consecutive_failed += 1
        else:
            break

    # Consecutive days AT OR ABOVE next band's level — from end backward
    next_level = BAND_NEXT_LEVEL_HOURS.get(band_slug)
    consecutive_next_level = 0
    if next_level is not None:
        for h in reversed(hours_per_day):
            if h >= next_level:
                consecutive_next_level += 1
            else:
                break

    upgrade_eligible = (next_level is not None) and (consecutive_next_level >= 7)

    if upgrade_eligible:
        status = 'upgrade_eligible'
    elif consecutive_failed >= 7:
        status = 'critical'
    elif consecutive_failed >= 2:
        status = 'at_risk'
    else:
        status = 'compliant'

    compliance_pct = round((avg_hours / target_hours) * 100, 1) if target_hours > 0 else 0.0
    has_data = any(h > 0 for h in hours_per_day)

    return {
        'status': status,
        'avg_hours_supplied': round(avg_hours, 2),
        'target_hours_per_day': target_hours,
        'compliance_pct': compliance_pct,
        'days_in_period': days_in_period,
        'days_compliant': days_compliant,
        'days_failed': days_failed,
        'consecutive_days_failed': consecutive_failed,
        'consecutive_days_at_next_level': consecutive_next_level,
        'upgrade_eligible': upgrade_eligible,
        'has_data': has_data,
    }


def compute_daily_breakdown(daily_hours_map, band_slug, target_hours, next_level_hours, period_dates):
    """
    Per-day compliance detail for the single-feeder detail endpoint.
    Returns list of daily records oldest → newest.
    """
    rows = []
    for d in period_dates:
        hours = daily_hours_map.get(d, 0.0)
        rows.append({
            'date': d.isoformat(),
            'hours_supplied': round(hours, 2),
            'target_hours': target_hours,
            'compliant': hours >= target_hours,
            'at_next_level': (next_level_hours is not None) and (hours >= next_level_hours),
        })
    return rows


def _empty_compliance(target_hours):
    return {
        'status': 'no_data',
        'avg_hours_supplied': 0.0,
        'target_hours_per_day': target_hours,
        'compliance_pct': 0.0,
        'days_in_period': 0,
        'days_compliant': 0,
        'days_failed': 0,
        'consecutive_days_failed': 0,
        'consecutive_days_at_next_level': 0,
        'upgrade_eligible': False,
        'has_data': False,
    }


def build_period_object(from_date, to_date, mode):
    """Consistent period metadata block."""
    return {
        'mode': mode,
        'start_date': from_date.isoformat(),
        'end_date': to_date.isoformat(),
        'days': (to_date - from_date).days + 1,
    }


def aggregate_compliance(feeders, daily_map, period_dates):
    """
    Shared aggregation used by overview, states, and districts.

    Args:
        feeders:      list of Feeder ORM objects (must have .band selected)
        daily_map:    {feeder_id: {date: hours}} from bulk_daily_hours()
        period_dates: list of date objects for the period

    Returns:
        (summary dict, by_band list ordered A→E)
    """
    bands_meta = {}

    for feeder in feeders:
        slug = feeder.band.slug
        if slug not in bands_meta:
            bands_meta[slug] = {
                'band': {
                    'slug': slug,
                    'name': feeder.band.name,
                    'target_hours_per_day': float(feeder.band.target_hours_per_day),
                },
                'total': 0,
                'compliant': 0,
                'at_risk': 0,
                'critical': 0,
                'upgrade_eligible': 0,
                'total_compliance_pct': 0.0,
            }

        target = BAND_TARGET_HOURS.get(slug, float(feeder.band.target_hours_per_day))
        c = compute_compliance(daily_map.get(feeder.id, {}), slug, target, period_dates)

        bands_meta[slug]['total'] += 1
        bands_meta[slug]['total_compliance_pct'] += c['compliance_pct']

        status = c['status']
        if status == 'upgrade_eligible':
            bands_meta[slug]['upgrade_eligible'] += 1
        elif status == 'critical':
            bands_meta[slug]['critical'] += 1
        elif status == 'at_risk':
            bands_meta[slug]['at_risk'] += 1
        else:
            bands_meta[slug]['compliant'] += 1

    by_band = []
    for slug in sorted(bands_meta, key=lambda s: BAND_ORDER.get(s, 99)):
        b = bands_meta[slug]
        total = b['total']
        avg_pct = round(b['total_compliance_pct'] / total, 1) if total else 0.0
        by_band.append({
            'band': b['band'],
            'total': total,
            'compliant': b['compliant'],
            'at_risk': b['at_risk'],
            'critical': b['critical'],
            'upgrade_eligible': b['upgrade_eligible'],
            'avg_compliance_pct': avg_pct,
        })

    summary = {
        'total_feeders': sum(b['total'] for b in by_band),
        'compliant': sum(b['compliant'] for b in by_band),
        'at_risk': sum(b['at_risk'] for b in by_band),
        'critical': sum(b['critical'] for b in by_band),
        'upgrade_eligible': sum(b['upgrade_eligible'] for b in by_band),
    }

    return summary, by_band
