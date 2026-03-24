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


def build_ongoing_interruption(feeder_id, interruption_map, target_hours, from_date=None, to_date=None):
    """
    Build the ongoing_interruption block for a single feeder.

    duration_hours is scoped to the selected period:
      - daily  (1 day):  capped at 24 hrs
      - weekly (7 days): capped at 168 hrs
      - monthly (N days): capped at N × 24 hrs

    band_allowance_hours is also scaled to the period:
      daily allowance × period_days  (e.g. Band A = 4 hrs/day → 92 hrs for 23-day month)

    breaching = duration_hours > band_allowance_hours
    """
    if feeder_id not in interruption_map:
        return {'has_interruption': False}

    rec = interruption_map[feeder_id]
    now = timezone.now()
    occurred_at = rec['occurred_at']

    from django.utils.timezone import make_aware
    if occurred_at.tzinfo is None:
        occurred_at = make_aware(occurred_at)

    # Period boundaries
    if from_date and to_date:
        from datetime import datetime as _dt
        period_days = (to_date - from_date).days + 1
        period_start = make_aware(_dt.combine(from_date, _dt.min.time()))
        period_end = now  # always count up to now (not future end of period)

        # Fault might have started before the period — only count within period
        effective_start = max(occurred_at, period_start)
        duration_hours = round(max((period_end - effective_start).total_seconds() / 3600, 0.0), 2)
    else:
        # Fallback: raw duration from occurrence to now
        period_days = 1
        duration_hours = round((now - occurred_at).total_seconds() / 3600, 2)

    daily_allowance = 24.0 - target_hours
    band_allowance = round(daily_allowance * period_days, 1)

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


# Module-level in-process cache (avoids Redis dependency for this tiny dataset)
_category_codes_cache = None
_category_codes_ts = 0.0


def _get_category_codes():
    """
    Returns (load_shedding_codes, transmission_codes) as sets, read from DB.
    'disco' = every code NOT in either set.
    Results are cached in-process for 60 s (no Redis dependency).
    """
    import time
    global _category_codes_cache, _category_codes_ts

    if _category_codes_cache is not None and (time.time() - _category_codes_ts) < 60:
        return _category_codes_cache

    from technical.models import FaultTypeCategory
    ls_codes = set(
        FaultTypeCategory.objects.filter(category='ls').values_list('code', flat=True)
    )
    tx_codes = set(
        FaultTypeCategory.objects.filter(category='tcn').values_list('code', flat=True)
    )
    _category_codes_cache = (ls_codes, tx_codes)
    _category_codes_ts = time.time()
    return _category_codes_cache


def _classify_interruption_type(itype, ls_codes, tx_codes):
    itype = itype or 'Unknown'
    if itype in ls_codes:
        return 'ls'
    if itype in tx_codes:
        return 'tcn'
    return 'disco'


def get_interruption_category_breakdown(start_date, end_date, period_days, period_offset=0, voltage_level=None):
    """
    Returns interruption counts split into ls / tcn / disco for ONE period slot.
    Uses the same date-offset logic as get_interruption_breakdown_network so it
    can be called in a list-of-4 loop.

    Per category:
        interruption_count          — total interruptions that started in the period
        feeders_affected            — distinct feeder count
        mean_time_to_restore_hours  — avg restore time for resolved interruptions
    """
    from datetime import datetime as dt, timedelta
    from dateutil.relativedelta import relativedelta

    # ── Resolve target date range for this period slot ────────────────────────
    if period_days == 1:
        target_start = start_date - timedelta(days=period_offset)
        target_end = target_start
    elif period_days == 7:
        target_start = start_date - timedelta(weeks=period_offset)
        target_end = target_start + timedelta(days=6)
    elif 28 <= period_days <= 31:
        temp = start_date - relativedelta(months=period_offset)
        from technical.views.overview.overview_views import get_month_range
        target_start, target_end = get_month_range(temp.year, temp.month)
    else:
        target_start = start_date - timedelta(days=period_days * period_offset)
        target_end = target_start + timedelta(days=period_days - 1)

    ls_codes, tx_codes = _get_category_codes()

    voltage_clause = ""
    params = [target_start, target_end]
    if voltage_level:
        voltage_clause = "AND cf.voltage_level = %s"
        params.append(voltage_level)

    query = f"""
        SELECT
            fi.feeder_id,
            fi.interruption_type,
            fi.occurred_at,
            fi.restored_at
        FROM technical_feederinterruption fi
        JOIN common_feeder cf ON cf.id = fi.feeder_id
        WHERE cf.is_onboarded = TRUE
            AND (fi.occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
            {voltage_clause}
    """

    with connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()

    buckets = {
        'ls':    {'feeder_ids': set(), 'count': 0, 'restore_hours': []},
        'tcn':   {'feeder_ids': set(), 'count': 0, 'restore_hours': []},
        'disco': {'feeder_ids': set(), 'count': 0, 'restore_hours': []},
    }

    for feeder_id, itype, occurred_at, restored_at in rows:
        cat = _classify_interruption_type(itype, ls_codes, tx_codes)
        buckets[cat]['count'] += 1
        buckets[cat]['feeder_ids'].add(feeder_id)

        if restored_at is not None:
            if occurred_at.tzinfo is None:
                from django.utils.timezone import make_aware
                occurred_at = make_aware(occurred_at)
            if restored_at.tzinfo is None:
                from django.utils.timezone import make_aware
                restored_at = make_aware(restored_at)
            hrs = (restored_at - occurred_at).total_seconds() / 3600
            buckets[cat]['restore_hours'].append(hrs)

    result = {}
    for cat, data in buckets.items():
        rlist = data['restore_hours']
        result[cat] = {
            'interruption_count': data['count'],
            'feeders_affected': len(data['feeder_ids']),
            'mean_time_to_restore_hours': round(sum(rlist) / len(rlist), 2) if rlist else 0.0,
        }
    return result


def get_feeder_ids_by_interruption_category(from_date, to_date, category, voltage_level=None):
    """
    Returns feeder IDs (list) that had at least one interruption of the given
    category in the period.  Used by feeders/all/ ?interruption_type= filter.

    category: 'load_shedding' | 'transmission' | 'disco'
    """
    ls_codes, tx_codes = _get_category_codes()

    if category == 'ls':
        type_list = list(ls_codes)
        use_exclude = False
    elif category == 'tcn':
        type_list = list(tx_codes)
        use_exclude = False
    else:  # disco = everything not in ls or tcn
        type_list = list(ls_codes | tx_codes)
        use_exclude = True

    if not type_list and not use_exclude:
        return []

    voltage_clause = ""
    params = [from_date, to_date]

    if type_list:
        placeholders = ','.join(['%s'] * len(type_list))
        type_clause = (
            f"AND fi.interruption_type NOT IN ({placeholders})"
            if use_exclude
            else f"AND fi.interruption_type IN ({placeholders})"
        )
        params += type_list
    else:
        type_clause = ""  # disco with empty exclusion list = all types

    if voltage_level:
        voltage_clause = "AND cf.voltage_level = %s"
        params.append(voltage_level)

    query = f"""
        SELECT DISTINCT fi.feeder_id
        FROM technical_feederinterruption fi
        JOIN common_feeder cf ON cf.id = fi.feeder_id
        WHERE cf.is_onboarded = TRUE
            AND (fi.occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
            {type_clause}
            {voltage_clause}
    """

    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return [row[0] for row in cursor.fetchall()]


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
