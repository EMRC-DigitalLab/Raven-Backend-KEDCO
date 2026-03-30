"""
analytics/services/compare_service.py

Core Compare Engine — data layer.

Handles:
  - Role-based module access gating (UserSectionAccess + TemporaryAccess)
  - Entity resolution: state / district / feeder / band
  - Per-entity metric fetching: technical / commercial / financial / hr
  - Time bucketing for trend data: daily / weekly / monthly / yearly
  - Entity comparison (many entities, one period + trend)
  - Period comparison (one entity, many time windows)

All SQL follows the same patterns as technical/views/districts/all_districts.py
and technical/views/states/all_states.py.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from dateutil.relativedelta import relativedelta
from django.db import connection
from django.db.models import (
    ExpressionWrapper, F, Sum,
    DecimalField as DField,
)
from django.utils import timezone

from analytics.compare_metrics import METRICS_REGISTRY, METRIC_MODULE_MAP
from commercial.models import CommercialCustomer, MeterReading
from common.models import Band, BusinessDistrict, Feeder, State
from financial.models import Opex, SalaryPayment
from hr.metrics import get_active_staff, get_hr_overview, get_staff_metrics, get_wage_bill
from technical.constants import TURNAROUND_EXCLUSIONS
from technical.utils.energy_utils import calculate_energy_delivered
from users.models import TemporaryAccess, UserSectionAccess

logger = logging.getLogger(__name__)

ENTITY_MODELS = {
    'state':    State,
    'district': BusinessDistrict,
    'feeder':   Feeder,
    'band':     Band,
}


# ─── Access helpers ────────────────────────────────────────────────────────────

def get_user_accessible_modules(user) -> set:
    """Return the set of module/section names this user has access to."""
    if user.role in ('super_admin', 'admin'):
        return {'technical', 'commercial', 'financial', 'hr', 'overview'}

    now = timezone.now()
    accessible: set = set()

    for access in (
        UserSectionAccess.objects
        .filter(user=user, is_active=True)
        .select_related('section')
    ):
        accessible.add(access.section.name)

    for access in (
        TemporaryAccess.objects
        .filter(user=user, is_active=True, expires_at__gt=now)
        .select_related('section')
    ):
        accessible.add(access.section.name)

    return accessible


# ─── Time-bucket helpers ───────────────────────────────────────────────────────

def generate_time_buckets(from_date: date, to_date: date, granularity: str):
    """
    Yield (bucket_from, bucket_to, label) tuples for the chosen granularity.
    Caps at today so we never query future data.
    Granularity: daily | weekly | monthly | yearly | custom
    """
    today   = date.today()
    to_date = min(to_date, today)

    if granularity == 'daily':
        current = from_date
        while current <= to_date:
            yield current, current, current.isoformat()
            current += timedelta(days=1)

    elif granularity == 'weekly':
        # Align to the Monday of the week containing from_date
        start = from_date - timedelta(days=from_date.weekday())
        while start <= to_date:
            end = min(start + timedelta(days=6), to_date)
            if end >= from_date:
                label = f"Wk {start.strftime('%d %b %Y')}"
                yield max(start, from_date), end, label
            start += timedelta(weeks=1)

    elif granularity == 'monthly':
        current = from_date.replace(day=1)
        while current <= to_date:
            month_end = current + relativedelta(months=1) - timedelta(days=1)
            month_end = min(month_end, to_date)
            yield current, month_end, current.strftime('%Y-%m')
            current = current + relativedelta(months=1)

    elif granularity == 'yearly':
        year = from_date.year
        while year <= to_date.year:
            y_start = max(date(year, 1, 1), from_date)
            y_end   = min(date(year, 12, 31), to_date)
            yield y_start, y_end, str(year)
            year += 1

    else:
        # custom / range — single bucket
        yield from_date, to_date, f"{from_date} → {to_date}"


# ─── Entity resolution ─────────────────────────────────────────────────────────

def resolve_entities(entity_type: str, entity_ids: list) -> list:
    """
    Resolve entity identifiers to model objects, preserving input order.
    Accepts UUIDs or name strings — whichever the caller provides.
    """
    import uuid as _uuid

    Model = ENTITY_MODELS.get(entity_type)
    if not Model:
        raise ValueError(f"Unknown entity_type: {entity_type!r}")

    uuid_ids, name_ids = [], []
    for eid in entity_ids:
        try:
            _uuid.UUID(str(eid))
            uuid_ids.append(str(eid))
        except (ValueError, AttributeError):
            name_ids.append(str(eid))

    from django.db.models import Q
    q = Q()
    if uuid_ids:
        q |= Q(id__in=uuid_ids)
    if name_ids:
        q |= Q(name__in=name_ids)

    if not q:
        return []

    objs = list(Model.objects.filter(q))

    # Preserve input order
    by_id   = {str(o.id): o for o in objs}
    by_name = {o.name: o for o in objs}
    result  = []
    seen    = set()
    for eid in entity_ids:
        obj = by_id.get(str(eid)) or by_name.get(str(eid))
        if obj and str(obj.id) not in seen:
            result.append(obj)
            seen.add(str(obj.id))
    return result


def entity_name(entity_type: str, entity) -> str:
    return getattr(entity, 'name', str(entity.id))


# ─── SQL helpers ───────────────────────────────────────────────────────────────

def _sql_one(query: str, params: list):
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchone()


def _feeder_scope(entity_type: str, entity_id):
    """
    Returns (where_clause, params, extra_join) to filter feeder rows
    by entity scope. Assumes the feeder table is aliased as `f`.
    """
    if entity_type == 'district':
        return "f.business_district_id = %s", [entity_id], ""
    elif entity_type == 'state':
        join = "INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id"
        return "bd.state_id = %s", [entity_id], join
    elif entity_type == 'feeder':
        return "f.id = %s", [entity_id], ""
    elif entity_type == 'band':
        return "f.band_id = %s", [entity_id], ""
    raise ValueError(f"Unknown entity_type: {entity_type!r}")


# ─── Technical fetchers ────────────────────────────────────────────────────────

def _tech_feeder_count(entity_type: str, entity_id, voltage_level=None) -> int:
    where, params, join = _feeder_scope(entity_type, entity_id)
    v_clause = "AND f.voltage_level = %s" if voltage_level else ""
    v_params = [voltage_level] if voltage_level else []
    row = _sql_one(f"""
        SELECT COUNT(DISTINCT f.id)
        FROM common_feeder f {join}
        WHERE f.is_onboarded = TRUE AND {where} {v_clause}
    """, params + v_params)
    return int(row[0] or 0) if row else 0


def _tech_hours_of_supply(
    entity_type: str, entity_id,
    from_date: date, to_date: date, voltage_level=None,
) -> float:
    where, params, join = _feeder_scope(entity_type, entity_id)
    v_clause = "AND f.voltage_level = %s" if voltage_level else ""
    v_params = [voltage_level] if voltage_level else []

    total_feeders = _tech_feeder_count(entity_type, entity_id, voltage_level)
    if total_feeders == 0:
        return 0.0

    row = _sql_one(f"""
        SELECT COUNT(DISTINCT CONCAT(hl.feeder_id, '-', hl.date, '-', hl.hour))
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id {join}
        WHERE f.is_onboarded = TRUE AND {where} {v_clause}
            AND hl.date BETWEEN %s AND %s
            AND hl.load_mw > 0
    """, params + v_params + [from_date, to_date])

    total_hours = int(row[0] or 0) if row else 0
    period_days = (to_date - from_date).days + 1

    if from_date == to_date:
        avg = total_hours / total_feeders
    else:
        avg = total_hours / (total_feeders * period_days)

    return round(min(avg, 24.0), 2)


def _tech_peak_load(
    entity_type: str, entity_id,
    from_date: date, to_date: date, voltage_level=None,
) -> float:
    where, params, join = _feeder_scope(entity_type, entity_id)
    v_clause = "AND f.voltage_level = %s" if voltage_level else ""
    v_params = [voltage_level] if voltage_level else []
    row = _sql_one(f"""
        SELECT MAX(hl.load_mw)
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id {join}
        WHERE f.is_onboarded = TRUE AND {where} {v_clause}
            AND hl.date BETWEEN %s AND %s
    """, params + v_params + [from_date, to_date])
    return round(float(row[0] or 0), 2) if row else 0.0


def _tech_interruptions(
    entity_type: str, entity_id,
    from_date: date, to_date: date, voltage_level=None,
    exclude_types=None,
) -> tuple:
    """
    Returns (avg_interruption_hrs_per_day, total_count).
    Mirrors the logic in calculate_district_interruption_metrics_sql.
    """
    now   = timezone.now()
    today = now.date()
    if from_date > today:
        return 0.0, 0

    where, params, join = _feeder_scope(entity_type, entity_id)
    v_clause = "AND f.voltage_level = %s" if voltage_level else ""
    v_params = [voltage_level] if voltage_level else []

    total_feeders = _tech_feeder_count(entity_type, entity_id, voltage_level)
    if total_feeders == 0:
        return 0.0, 0

    is_single_day = (from_date == to_date)
    period_days   = 1 if is_single_day else (to_date - from_date).days + 1
    max_hours     = 24.0 * period_days

    start_dt = timezone.make_aware(datetime.combine(from_date, datetime.min.time()))
    end_dt   = timezone.make_aware(datetime.combine(to_date, datetime.max.time()))

    excl_clause  = ""
    excl_params: list = []
    if exclude_types:
        ph = ','.join(['%s'] * len(exclude_types))
        excl_clause = f"AND fi.interruption_type NOT IN ({ph})"
        excl_params = list(exclude_types)

    dur_row = _sql_one(f"""
        SELECT COALESCE(SUM(capped_hours), 0)
        FROM (
            SELECT fi.feeder_id,
                LEAST(
                    SUM(GREATEST(
                        EXTRACT(EPOCH FROM (
                            LEAST(COALESCE(fi.restored_at, %s), %s) - GREATEST(fi.occurred_at, %s)
                        )) / 3600.0,
                        0
                    )),
                    %s
                ) AS capped_hours
            FROM technical_feederinterruption fi
            INNER JOIN common_feeder f ON fi.feeder_id = f.id {join}
            WHERE f.is_onboarded = TRUE AND {where} {v_clause}
                AND (
                    fi.occurred_at >= %s AND fi.occurred_at <= %s
                    OR (fi.occurred_at < %s AND (fi.restored_at IS NULL OR fi.restored_at >= %s))
                )
                {excl_clause}
            GROUP BY fi.feeder_id
        ) t
    """, [now, end_dt, start_dt, max_hours]
       + params + v_params
       + [start_dt, end_dt, start_dt, start_dt]
       + excl_params)

    total_hours = float(dur_row[0] or 0) if dur_row else 0.0

    cnt_row = _sql_one(f"""
        SELECT COUNT(*)
        FROM technical_feederinterruption fi
        INNER JOIN common_feeder f ON fi.feeder_id = f.id {join}
        WHERE f.is_onboarded = TRUE AND {where} {v_clause}
            AND fi.occurred_at >= %s AND fi.occurred_at <= %s
            {excl_clause}
    """, params + v_params + [start_dt, end_dt] + excl_params)

    total_count = int(cnt_row[0] or 0) if cnt_row else 0

    denom   = total_feeders if is_single_day else total_feeders * period_days
    avg_hrs = max(0.0, min(total_hours / denom if denom > 0 else 0.0, 24.0))

    return round(avg_hrs, 2), total_count


def _tech_avg_int_duration(
    entity_type: str, entity_id,
    from_date: date, to_date: date, voltage_level=None,
) -> float:
    """Average hours per interruption event (total hours / event count)."""
    now   = timezone.now()
    today = now.date()
    if from_date > today:
        return 0.0

    where, params, join = _feeder_scope(entity_type, entity_id)
    v_clause = "AND f.voltage_level = %s" if voltage_level else ""
    v_params = [voltage_level] if voltage_level else []

    start_dt = timezone.make_aware(datetime.combine(from_date, datetime.min.time()))
    end_dt   = timezone.make_aware(datetime.combine(to_date, datetime.max.time()))

    row = _sql_one(f"""
        SELECT COUNT(*), COALESCE(SUM(
            EXTRACT(EPOCH FROM (
                LEAST(COALESCE(fi.restored_at, %s), %s) - GREATEST(fi.occurred_at, %s)
            )) / 3600.0
        ), 0)
        FROM technical_feederinterruption fi
        INNER JOIN common_feeder f ON fi.feeder_id = f.id {join}
        WHERE f.is_onboarded = TRUE AND {where} {v_clause}
            AND (
                (fi.occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
                OR (fi.occurred_at < %s AND fi.restored_at IS NULL)
            )
    """, [now, end_dt, start_dt] + params + v_params + [from_date, to_date, start_dt])

    if row and row[0]:
        count, total_hours = int(row[0]), float(row[1] or 0)
        return round(total_hours / count, 2) if count > 0 else 0.0
    return 0.0


def _tech_energy(
    entity_type: str, entity_id,
    from_date: date, to_date: date, voltage_level=None,
) -> float:
    """Total energy delivered (MWh) via hybrid meter/system calculation."""
    qs = Feeder.objects.filter(is_onboarded=True)

    if entity_type == 'district':
        qs = qs.filter(business_district_id=entity_id)
    elif entity_type == 'state':
        qs = qs.filter(business_district__state_id=entity_id)
    elif entity_type == 'feeder':
        qs = qs.filter(id=entity_id)
    elif entity_type == 'band':
        qs = qs.filter(band_id=entity_id)

    if voltage_level:
        qs = qs.filter(voltage_level=voltage_level)

    feeder_ids = list(qs.values_list('id', flat=True))
    if not feeder_ids:
        return 0.0

    result = calculate_energy_delivered(feeder_ids, from_date, to_date)
    return round(float(result.get('total_mwh', 0) or 0), 2)


def _fetch_technical(
    entity_type: str, entity_id,
    metrics: list, from_date: date, to_date: date, voltage_level=None,
) -> dict:
    result: dict = {}
    needed = {m for m in metrics if METRIC_MODULE_MAP.get(m) == 'technical'}
    if not needed:
        return result

    # Cache computed values to avoid duplicate queries when multiple
    # metrics share the same underlying SQL call.
    _hos   = None   # hours_of_supply
    _enrgy = None   # energy_delivered
    _intr  = None   # (interruption_hrs/day, total_count) — all types
    _turn  = None   # (turnaround_hrs/day, _) — local faults only
    _adur  = None   # avg duration per event
    _peak  = None   # peak_load
    _fcnt  = None   # feeder_count

    def hos():
        nonlocal _hos
        if _hos is None:
            _hos = _tech_hours_of_supply(entity_type, entity_id, from_date, to_date, voltage_level)
        return _hos

    def enrgy():
        nonlocal _enrgy
        if _enrgy is None:
            _enrgy = _tech_energy(entity_type, entity_id, from_date, to_date, voltage_level)
        return _enrgy

    def intr():
        nonlocal _intr
        if _intr is None:
            _intr = _tech_interruptions(entity_type, entity_id, from_date, to_date, voltage_level)
        return _intr

    def turn():
        nonlocal _turn
        if _turn is None:
            _turn = _tech_interruptions(
                entity_type, entity_id, from_date, to_date, voltage_level,
                exclude_types=TURNAROUND_EXCLUSIONS,
            )
        return _turn

    def adur():
        nonlocal _adur
        if _adur is None:
            _adur = _tech_avg_int_duration(entity_type, entity_id, from_date, to_date, voltage_level)
        return _adur

    def peak():
        nonlocal _peak
        if _peak is None:
            _peak = _tech_peak_load(entity_type, entity_id, from_date, to_date, voltage_level)
        return _peak

    def fcnt():
        nonlocal _fcnt
        if _fcnt is None:
            _fcnt = _tech_feeder_count(entity_type, entity_id, voltage_level)
        return _fcnt

    for m in needed:
        try:
            if m == 'hours_of_supply':
                result[m] = hos()
            elif m == 'energy_delivered':
                result[m] = enrgy()
            elif m == 'peak_load':
                result[m] = peak()
            elif m == 'total_interruptions':
                _, count = intr()
                result[m] = count
            elif m == 'interruption_hours':
                hrs, _ = intr()
                result[m] = hrs
            elif m == 'avg_interruption_duration':
                result[m] = adur()
            elif m == 'turnaround_time':
                hrs, _ = turn()
                result[m] = hrs
            elif m == 'feeder_count':
                result[m] = fcnt()
            elif m == 'availability_pct':
                result[m] = round(min(hos() / 24.0 * 100.0, 100.0), 2)
        except Exception as exc:
            logger.warning("Tech metric %s error (%s %s): %s", m, entity_type, entity_id, exc)
            result[m] = None

    return result


# ─── Commercial fetchers ───────────────────────────────────────────────────────

def _comm_customer_filter(entity_type: str, entity_id) -> dict:
    """ORM filter kwargs for CommercialCustomer by entity scope."""
    if entity_type == 'district':
        return {'feeder__business_district_id': entity_id}
    elif entity_type == 'state':
        return {'feeder__business_district__state_id': entity_id}
    elif entity_type == 'feeder':
        return {'feeder_id': entity_id}
    elif entity_type == 'band':
        return {'feeder__band_id': entity_id}
    return {}


def _comm_reading_filter(entity_type: str, entity_id) -> dict:
    """ORM filter kwargs for MeterReading by entity scope (through customer)."""
    if entity_type == 'district':
        return {'customer__feeder__business_district_id': entity_id}
    elif entity_type == 'state':
        return {'customer__feeder__business_district__state_id': entity_id}
    elif entity_type == 'feeder':
        return {'customer__feeder_id': entity_id}
    elif entity_type == 'band':
        return {'customer__feeder__band_id': entity_id}
    return {}


def _fetch_commercial(
    entity_type: str, entity_id,
    metrics: list, from_date: date, to_date: date,
) -> dict:
    result: dict = {}
    needed = {m for m in metrics if METRIC_MODULE_MAP.get(m) == 'commercial'}
    if not needed:
        return result

    c_filter = _comm_customer_filter(entity_type, entity_id)
    r_filter = _comm_reading_filter(entity_type, entity_id)

    _total_cust  = None
    _cust_read   = None
    _billed_kwh  = None
    _energy_chg  = None  # sum of (billed_consumption × tariff_rate) — pre-VAT revenue
    _energy_del  = None  # energy delivered MWh (for efficiency calc)

    def total_cust():
        nonlocal _total_cust
        if _total_cust is None:
            _total_cust = CommercialCustomer.objects.filter(**c_filter).count()
        return _total_cust

    def cust_read():
        nonlocal _cust_read
        if _cust_read is None:
            _cust_read = (
                MeterReading.objects
                .filter(**r_filter, reading_date__range=(from_date, to_date))
                .values('customer_id').distinct().count()
            )
        return _cust_read

    def billing():
        nonlocal _billed_kwh, _energy_chg
        if _billed_kwh is None:
            agg = (
                MeterReading.objects
                .filter(**r_filter, reading_date__range=(from_date, to_date))
                .annotate(
                    ec=ExpressionWrapper(
                        F('billed_consumption') * F('tariff_rate'),
                        output_field=DField(max_digits=20, decimal_places=4),
                    )
                )
                .aggregate(kwh=Sum('billed_consumption'), chg=Sum('ec'))
            )
            _billed_kwh = float(agg['kwh'] or 0)
            _energy_chg = float(agg['chg'] or 0)
        return _billed_kwh, _energy_chg

    def energy_del():
        nonlocal _energy_del
        if _energy_del is None:
            fids = list(
                CommercialCustomer.objects
                .filter(**c_filter, feeder__isnull=False)
                .values_list('feeder_id', flat=True)
                .distinct()
            )
            if fids:
                _energy_del = float(
                    calculate_energy_delivered(fids, from_date, to_date).get('total_mwh', 0) or 0
                )
            else:
                _energy_del = 0.0
        return _energy_del

    for m in needed:
        try:
            if m == 'total_customers':
                result[m] = total_cust()

            elif m == 'customers_read':
                result[m] = cust_read()

            elif m == 'coverage_rate':
                tc = total_cust()
                result[m] = round(cust_read() / tc * 100, 2) if tc > 0 else 0.0

            elif m == 'energy_billed_kwh':
                kwh, _ = billing()
                result[m] = round(kwh, 2)

            elif m == 'revenue_billed':
                _, chg = billing()
                # total_billed = energy_charge × (1 + VAT 7.5%)
                result[m] = round(chg * 1.075, 2)

            elif m == 'billing_efficiency':
                kwh, _ = billing()
                del_kwh = energy_del() * 1000  # MWh → kWh
                result[m] = round(kwh / del_kwh * 100, 2) if del_kwh > 0 else 0.0

            elif m == 'atc_loss':
                kwh, _ = billing()
                del_kwh = energy_del() * 1000
                eff = kwh / del_kwh * 100 if del_kwh > 0 else 0.0
                result[m] = round(100.0 - eff, 2)

            elif m == 'arpu':
                _, chg = billing()
                rev  = chg * 1.075
                read = cust_read()
                result[m] = round(rev / read, 2) if read > 0 else 0.0

        except Exception as exc:
            logger.warning("Commercial metric %s error (%s %s): %s", m, entity_type, entity_id, exc)
            result[m] = None

    return result


# ─── Financial fetchers ────────────────────────────────────────────────────────

def _fin_filter(entity_type: str, entity_id) -> dict:
    if entity_type == 'district':
        return {'district_id': entity_id}
    elif entity_type == 'state':
        return {'district__state_id': entity_id}
    return {}


def _fetch_financial(
    entity_type: str, entity_id,
    metrics: list, from_date: date, to_date: date,
) -> dict:
    result: dict = {}
    needed = {m for m in metrics if METRIC_MODULE_MAP.get(m) == 'financial'}
    if not needed:
        return result

    if entity_type not in ('state', 'district'):
        return {m: None for m in needed}

    f_filter = _fin_filter(entity_type, entity_id)
    _opex   = None
    _salary = None

    def opex():
        nonlocal _opex
        if _opex is None:
            agg = (
                Opex.objects
                .filter(**f_filter, date__range=(from_date, to_date))
                .aggregate(credit=Sum('credit'), debit=Sum('debit'))
            )
            _opex = float(agg['credit'] or 0) + float(agg['debit'] or 0)
        return _opex

    def salary():
        nonlocal _salary
        if _salary is None:
            # SalaryPayment.month is first-of-month; include all months in range
            month_start = from_date.replace(day=1)
            agg = (
                SalaryPayment.objects
                .filter(**f_filter, month__gte=month_start, month__lte=to_date)
                .aggregate(total=Sum('amount'))
            )
            _salary = float(agg['total'] or 0)
        return _salary

    for m in needed:
        try:
            if m == 'total_opex':
                result[m] = round(opex(), 2)
            elif m == 'salary_cost':
                result[m] = round(salary(), 2)
            elif m == 'total_cost':
                result[m] = round(opex() + salary(), 2)
        except Exception as exc:
            logger.warning("Financial metric %s error (%s %s): %s", m, entity_type, entity_id, exc)
            result[m] = None

    return result


# ─── HR fetchers ───────────────────────────────────────────────────────────────

def _fetch_hr(
    entity_type: str, entity_id,
    metrics: list, from_date: date, to_date: date,
) -> dict:
    result: dict = {}
    needed = {m for m in metrics if METRIC_MODULE_MAP.get(m) == 'hr'}
    if not needed:
        return result

    if entity_type not in ('state', 'district'):
        return {m: None for m in needed}

    scope_kwargs: dict = {}
    if entity_type == 'district':
        scope_kwargs['district_ids'] = [entity_id]
    elif entity_type == 'state':
        scope_kwargs['state_ids'] = [entity_id]

    try:
        staff_qs = get_active_staff(to_date=to_date, **scope_kwargs)
        overview = get_hr_overview(staff_qs, from_date, to_date)
        tenure   = get_staff_metrics(staff_qs) if 'avg_tenure_years' in needed else {}
        wage     = get_wage_bill(staff_qs)     if 'total_wage_bill'  in needed else {}
    except Exception as exc:
        logger.warning("HR fetch error (%s %s): %s", entity_type, entity_id, exc)
        return {m: None for m in needed}

    mapping = {
        'total_staff':      overview.get('total_staff', 0),
        'attrition_rate':   overview.get('attrition_rate', 0.0),
        'new_hires':        overview.get('new_hires', 0),
        'avg_tenure_years': tenure.get('avg_tenure_years', 0.0),
        'total_wage_bill':  round(wage.get('total_wage_bill', 0.0), 2),
    }

    for m in needed:
        result[m] = mapping.get(m)

    return result


# ─── Core data gate ────────────────────────────────────────────────────────────

def _get_entity_data(
    entity_type: str, entity_id,
    metrics: list, from_date: date, to_date: date, voltage_level=None,
) -> dict:
    """Fetch all metrics for one entity over one time window."""
    data: dict = {}
    data.update(_fetch_technical(entity_type, entity_id, metrics, from_date, to_date, voltage_level))
    data.update(_fetch_commercial(entity_type, entity_id, metrics, from_date, to_date))
    data.update(_fetch_financial(entity_type, entity_id, metrics, from_date, to_date))
    data.update(_fetch_hr(entity_type, entity_id, metrics, from_date, to_date))
    return data


# ─── Metric gating ─────────────────────────────────────────────────────────────

def _gate_metrics(user, metrics: list, entity_type: str) -> tuple:
    """
    Split requested metrics into (allowed, denied).
    Denied reasons: module inaccessible, unknown metric, not supported at entity level.
    """
    accessible = get_user_accessible_modules(user)
    allowed, denied = [], []

    for m in metrics:
        if m not in METRICS_REGISTRY:
            denied.append({'metric': m, 'reason': 'Unknown metric'})
            continue
        meta   = METRICS_REGISTRY[m]
        module = meta['module']
        if module not in accessible:
            denied.append({'metric': m, 'module': module,
                           'reason': f"No access to the {module} module"})
            continue
        if entity_type not in meta.get('entity_types', []):
            denied.append({'metric': m,
                           'reason': f"Not available at {entity_type} level"})
            continue
        allowed.append(m)

    return allowed, denied


# ─── Public API ────────────────────────────────────────────────────────────────

def compare_entities(
    user,
    entity_type: str,
    entity_ids: list,
    metrics: list,
    from_date: date,
    to_date: date,
    feeder_type: str = None,
    granularity: str = 'monthly',
    include_trend: bool = True,
) -> dict:
    """
    Compare multiple entities of the same type over a shared time period.

    Returns one result object per entity containing:
      - entity metadata (id, name, type)
      - summary data for the full period
      - trend data bucketed by granularity (if include_trend=True)
    """
    allowed, denied = _gate_metrics(user, metrics, entity_type)

    if not allowed:
        return {
            'results': [],
            'metrics_returned': [],
            'metrics_denied': denied,
            'error': 'No accessible metrics for this request.',
        }

    entities      = resolve_entities(entity_type, entity_ids)
    voltage_level = feeder_type if feeder_type in ('11kv', '33kv') else None
    results       = []

    for ent in entities:
        eid = ent.id
        entry = {
            'entity': {
                'id':   str(eid),
                'name': entity_name(entity_type, ent),
                'type': entity_type,
            },
            'data': _get_entity_data(
                entity_type, eid, allowed, from_date, to_date, voltage_level,
            ),
        }

        if include_trend:
            trend = []
            for b_from, b_to, label in generate_time_buckets(from_date, to_date, granularity):
                bucket = _get_entity_data(
                    entity_type, eid, allowed, b_from, b_to, voltage_level,
                )
                trend.append({
                    'period':    label,
                    'from_date': b_from.isoformat(),
                    'to_date':   b_to.isoformat(),
                    **bucket,
                })
            entry['trend'] = trend

        results.append(entry)

    return {
        'compare_mode':    'entities',
        'entity_type':     entity_type,
        'granularity':     granularity,
        'feeder_type':     feeder_type,
        'period':          {'from_date': from_date.isoformat(), 'to_date': to_date.isoformat()},
        'metrics_returned': allowed,
        'metrics_denied':   denied,
        'results':          results,
    }


def compare_periods(
    user,
    entity_type: str,
    entity_id,
    metrics: list,
    periods: list,
    feeder_type: str = None,
) -> dict:
    """
    Compare one entity across multiple time windows (today vs yesterday, etc.).

    Each period: {'label': str, 'from_date': 'YYYY-MM-DD', 'to_date': 'YYYY-MM-DD'}
    Returns one result per period.
    """
    allowed, denied = _gate_metrics(user, metrics, entity_type)

    entities = resolve_entities(entity_type, [entity_id])
    if not entities:
        return {'error': f'{entity_type} with id {entity_id!r} not found.'}

    ent           = entities[0]
    voltage_level = feeder_type if feeder_type in ('11kv', '33kv') else None
    period_results = []

    for p in periods:
        label = p.get('label', '')
        try:
            p_from = datetime.strptime(str(p['from_date'])[:10], '%Y-%m-%d').date()
            p_to   = datetime.strptime(str(p['to_date'])[:10],   '%Y-%m-%d').date()
        except (KeyError, ValueError):
            period_results.append({'period': {'label': label}, 'error': 'Invalid date format'})
            continue

        data = _get_entity_data(entity_type, ent.id, allowed, p_from, p_to, voltage_level)
        period_results.append({
            'period': {
                'label':     label,
                'from_date': p_from.isoformat(),
                'to_date':   p_to.isoformat(),
                'days':      (p_to - p_from).days + 1,
            },
            'data': data,
        })

    return {
        'compare_mode':    'periods',
        'entity_type':     entity_type,
        'entity': {
            'id':   str(ent.id),
            'name': entity_name(entity_type, ent),
            'type': entity_type,
        },
        'feeder_type':      feeder_type,
        'metrics_returned': allowed,
        'metrics_denied':   denied,
        'results':          period_results,
    }
