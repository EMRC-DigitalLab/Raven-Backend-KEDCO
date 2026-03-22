# technical/utils/compliance_utils.py
"""
Shared compliance utilities used by feeder list, overview, states, districts,
and service-bands endpoints.

Band targets (NERC/MYTO):
  A = 20 hrs/day   B = 16   C = 12   D = 8   E = 4

Compliance status (per feeder, for selected period):
  no_data    — zero HourlyLoad records submitted for this feeder in the period
  compliant  — avg hours supplied >= band target
  at_risk    — avg hours supplied < target but >= 50% of target
  critical   — avg hours supplied < 50% of target

Ongoing interruption:
  Any unresolved FeederInterruption (restored_at IS NULL).
  breaching = True when duration > band's allowed downtime (24 - target_hours).
"""
from django.db import connection
from django.utils import timezone

BAND_TARGET_HOURS = {
    'a': 20.0,
    'b': 16.0,
    'c': 12.0,
    'd': 8.0,
    'e': 4.0,
}

BAND_ORDER = {'a': 0, 'b': 1, 'c': 2, 'd': 3, 'e': 4}


def compliance_status(avg_hours, in_supply_map, target_hours):
    """
    Determine compliance status for a single feeder.

    Args:
        avg_hours:      float — avg hours/day from supply_map (0 if not in map)
        in_supply_map:  bool  — True if feeder had ANY HourlyLoad record in period
        target_hours:   float — band's NERC minimum

    Returns: 'no_data' | 'compliant' | 'at_risk' | 'critical'
    """
    if not in_supply_map:
        return 'no_data'
    if avg_hours >= target_hours:
        return 'compliant'
    if avg_hours >= target_hours * 0.5:
        return 'at_risk'
    return 'critical'


def bulk_ongoing_interruptions(feeder_ids):
    """
    One query — returns the earliest unresolved interruption per feeder.
    Result: {feeder_id: {'type': str, 'occurred_at': datetime}}
    """
    if not feeder_ids:
        return {}

    placeholders = ','.join(['%s'] * len(feeder_ids))
    query = f"""
        SELECT DISTINCT ON (feeder_id)
            feeder_id,
            interruption_type,
            occurred_at
        FROM technical_feederinterruption
        WHERE feeder_id IN ({placeholders})
            AND restored_at IS NULL
        ORDER BY feeder_id, occurred_at ASC
    """
    result = {}
    with connection.cursor() as cursor:
        cursor.execute(query, list(feeder_ids))
        for feeder_id, itype, occurred_at in cursor.fetchall():
            result[feeder_id] = {'type': itype, 'occurred_at': occurred_at}
    return result


def build_ongoing_interruption(feeder_id, interruption_map, target_hours):
    """
    Build the ongoing_interruption block for a single feeder.
    band_allowance_hours = 24 - target_hours (max downtime the band permits per day).
    breaching = True when the fault has already exceeded that allowance.
    """
    if feeder_id not in interruption_map:
        return {'has_interruption': False}

    rec = interruption_map[feeder_id]
    now = timezone.now()
    occurred_at = rec['occurred_at']

    # Make timezone-aware if naive
    if occurred_at.tzinfo is None:
        from django.utils.timezone import make_aware
        occurred_at = make_aware(occurred_at)

    duration_hours = round((now - occurred_at).total_seconds() / 3600, 2)
    band_allowance = round(24.0 - target_hours, 1)

    return {
        'has_interruption': True,
        'type': rec['type'],
        'duration_hours': duration_hours,
        'band_allowance_hours': band_allowance,
        'breaching': duration_hours > band_allowance,
    }


def get_compliance_summary(from_date, to_date, state=None, district=None, voltage_level=None):
    """
    Self-contained compliance summary for any scope.
    Called directly from overview / states / districts / service-bands views.
    Runs its own supply query — no dependency on the calling view's data.

    Returns the compliance block:
    {
        "total_feeders": N,
        "compliant": N,
        "non_compliant": N,
        "no_data": N,
        "by_band": [...]
    }
    """
    from common.models import Feeder

    feeders_qs = (
        Feeder.objects
        .filter(is_onboarded=True)
        .select_related('band', 'business_district__state')
        .exclude(band__isnull=True)
    )
    if voltage_level:
        feeders_qs = feeders_qs.filter(voltage_level=voltage_level)
    if district:
        feeders_qs = feeders_qs.filter(business_district__name__iexact=district)
    elif state:
        # state can be a slug or a name — try slug first
        feeders_qs = feeders_qs.filter(
            business_district__state__slug__iexact=state
        ) | feeders_qs.filter(
            business_district__state__name__iexact=state
        )

    feeders = list(feeders_qs)
    if not feeders:
        return {'total_feeders': 0, 'compliant': 0, 'non_compliant': 0, 'no_data': 0, 'by_band': []}

    feeder_ids = [f.id for f in feeders]
    supply_map = _bulk_supply_hours(feeder_ids, from_date, to_date)
    return build_compliance_summary(feeders, supply_map)


def _bulk_supply_hours(feeder_ids, from_date, to_date):
    """Avg hours/day per feeder from HourlyLoad. Feeders not in result = no data."""
    if not feeder_ids:
        return {}

    from django.db import connection as _conn
    today = __import__('django.utils.timezone', fromlist=['now']).now().date()
    if from_date > today:
        return {}

    placeholders = ','.join(['%s'] * len(feeder_ids))
    is_single = (from_date == to_date)
    period_days = 1 if is_single else (to_date - from_date).days + 1

    query = f"""
        SELECT feeder_id, COUNT(DISTINCT hour) AS total_hours
        FROM technical_hourlyload
        WHERE feeder_id IN ({placeholders})
            AND date BETWEEN %s AND %s
            AND load_mw > 0
        GROUP BY feeder_id
    """
    result = {}
    with _conn.cursor() as cursor:
        cursor.execute(query, list(feeder_ids) + [from_date, to_date])
        for feeder_id, total_hours in cursor.fetchall():
            avg = float(total_hours) if is_single else total_hours / period_days
            result[feeder_id] = round(min(avg, 24.0), 2)
    return result


def build_compliance_summary(feeders, supply_map):
    """
    Build the compliance summary block added to overview/states/districts/service-bands.

    Args:
        feeders:    list of Feeder ORM objects (must have .band selected)
        supply_map: {feeder_id: avg_hours} from _bulk_supply_hours()
                    — feeders NOT in this dict have no data

    Returns dict:
    {
        "total_feeders": N,
        "compliant": N,
        "non_compliant": N,
        "no_data": N,
        "by_band": [
            {"slug": "a", "name": "A", "compliant": N, "non_compliant": N, "no_data": N},
            ...
        ]
    }
    """
    band_counts = {}

    for feeder in feeders:
        if not feeder.band:
            continue
        slug = feeder.band.slug
        name = feeder.band.name
        if slug not in band_counts:
            band_counts[slug] = {'slug': slug, 'name': name, 'compliant': 0, 'non_compliant': 0, 'no_data': 0}

        target = BAND_TARGET_HOURS.get(slug, 0.0)
        in_map = feeder.id in supply_map
        avg = supply_map.get(feeder.id, 0.0)
        status = compliance_status(avg, in_map, target)

        if status == 'compliant':
            band_counts[slug]['compliant'] += 1
        elif status == 'no_data':
            band_counts[slug]['no_data'] += 1
        else:
            band_counts[slug]['non_compliant'] += 1

    by_band = [band_counts[s] for s in sorted(band_counts, key=lambda x: BAND_ORDER.get(x, 99))]

    total = len([f for f in feeders if f.band])
    compliant = sum(b['compliant'] for b in by_band)
    non_compliant = sum(b['non_compliant'] for b in by_band)
    no_data = sum(b['no_data'] for b in by_band)

    return {
        'total_feeders': total,
        'compliant': compliant,
        'non_compliant': non_compliant,
        'no_data': no_data,
        'by_band': by_band,
    }
