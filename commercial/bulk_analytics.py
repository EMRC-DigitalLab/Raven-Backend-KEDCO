"""
commercial/bulk_analytics.py

Bulk aggregation helpers for list endpoints.

Strategy: ALL queries group by customer__feeder_id (1 JOIN max) or feeder_id
(0 JOINs on CommercialCustomer). Python-level rollup maps feeder → dim.
This cuts JOIN depth from 3 hops to 0-1 hop for every query.

Usage in list views:
    feeder_to_dim = feeder_dim_map(customers_qs, 'feeder__business_district__state_id')
    billing       = bulk_billing(readings_qs, feeder_to_dim)
    coverage      = bulk_coverage(customers_qs, readings_qs, feeder_to_dim)
    ...

For all_feeders, pass feeder_to_dim = {f.id: f.id for f in feeders}.
"""

from decimal import Decimal

from django.db.models import Avg, Case, Count, ExpressionWrapper, F, Max, Sum, Value, When, DecimalField

from commercial.models import MeterManager, MeterReading
from technical.models import EnergyDelivered, HourlyLoad

VAT_RATE = Decimal('0.075')
ZERO     = Decimal('0')

_EC_EXPR = ExpressionWrapper(
    F('billed_consumption') * F('tariff_rate'),
    output_field=DecimalField(max_digits=20, decimal_places=4),
)


# ── Defaults ──────────────────────────────────────────────────────────────────

def empty_billing():
    return {'total_billed_kwh': ZERO, 'actual_billed_kwh': ZERO, 'estimated_billed_kwh': ZERO, 'energy_charge': ZERO, 'vat': ZERO, 'total_billed_amount': ZERO}

def empty_estimated():
    return {'estimated_kwh': ZERO, 'estimated_energy_charge': ZERO, 'estimated_revenue': ZERO}

def empty_coverage():
    return {'total': 0, 'read': 0, 'unread': 0, 'rate': 0, 'read_ids': set()}


# ── feeder → dimension map ────────────────────────────────────────────────────

def feeder_dim_map(customers_qs, dim_field):
    """
    ONE query → {feeder_id: dim_id}.

    dim_field is a field path FROM CommercialCustomer (not from readings).
    e.g. 'feeder__business_district__state_id'
         'feeder__business_district_id'
         'feeder__band_id'
    For feeders endpoint, caller builds {f.id: f.id} directly — no query needed.
    """
    return dict(
        customers_qs.filter(feeder__isnull=False)
        .values_list('feeder_id', dim_field)
        .distinct()
    )


# ── Billing ───────────────────────────────────────────────────────────────────

_ACTUAL_KWH_EXPR = Case(
    When(estimation_method='', then=F('billed_consumption')),
    default=Value(0),
    output_field=DecimalField(max_digits=20, decimal_places=4),
)
_ESTIMATED_KWH_EXPR = Case(
    When(estimation_method='', then=Value(0)),
    default=F('billed_consumption'),
    output_field=DecimalField(max_digits=20, decimal_places=4),
)


def bulk_billing(readings_qs, feeder_to_dim):
    """
    ONE query (1 JOIN: customer→feeder) → {dim_id: billing dict}.
    Splits actual_billed_kwh (real reads, estimation_method='') from
    estimated_billed_kwh (DataNest-estimated reads, estimation_method non-empty).
    """
    rows = (
        readings_qs
        .filter(billed_consumption__isnull=False, tariff_rate__isnull=False)
        .values('customer__feeder_id')
        .annotate(
            total_kwh=Sum('billed_consumption'),
            actual_kwh=Sum(_ACTUAL_KWH_EXPR),
            estimated_kwh=Sum(_ESTIMATED_KWH_EXPR),
            raw_ec=Sum(_EC_EXPR),
        )
    )
    acc = {}
    for row in rows:
        dim_id = feeder_to_dim.get(row['customer__feeder_id'])
        if dim_id is None:
            continue
        if dim_id not in acc:
            acc[dim_id] = {'kwh': ZERO, 'actual_kwh': ZERO, 'estimated_kwh': ZERO, 'ec': ZERO}
        acc[dim_id]['kwh']           += Decimal(str(row['total_kwh']     or 0))
        acc[dim_id]['actual_kwh']    += Decimal(str(row['actual_kwh']    or 0))
        acc[dim_id]['estimated_kwh'] += Decimal(str(row['estimated_kwh'] or 0))
        acc[dim_id]['ec']            += Decimal(str(row['raw_ec']        or 0))

    result = {}
    for dim_id, r in acc.items():
        kwh           = round(r['kwh'], 2)
        actual_kwh    = round(r['actual_kwh'], 2)
        estimated_kwh = round(r['estimated_kwh'], 2)
        ec            = round(r['ec'], 2)
        vat           = round(ec * VAT_RATE, 2)
        result[dim_id] = {
            'total_billed_kwh':     kwh,
            'actual_billed_kwh':    actual_kwh,
            'estimated_billed_kwh': estimated_kwh,
            'energy_charge':        ec,
            'vat':                  vat,
            'total_billed_amount':  round(ec + vat, 2),
        }
    return result


def bulk_billing_by_type(readings_qs, feeder_to_dim):
    """
    ONE query (1 JOIN) → {(dim_id, reading_type): total_billed_amount}.
    """
    rows = (
        readings_qs
        .filter(billed_consumption__isnull=False, tariff_rate__isnull=False)
        .values('customer__feeder_id', 'reading_type')
        .annotate(raw_ec=Sum(_EC_EXPR))
    )
    result = {}
    for row in rows:
        dim_id = feeder_to_dim.get(row['customer__feeder_id'])
        if dim_id is None:
            continue
        ec  = Decimal(str(row['raw_ec'] or 0))
        vat = round(ec * VAT_RATE, 2)
        key = (dim_id, row['reading_type'])
        result[key] = round(result.get(key, ZERO) + ec + vat, 2)
    return result


# ── Customer type counts ──────────────────────────────────────────────────────

def bulk_customer_types(customers_qs, feeder_to_dim):
    """
    ONE query (0 JOINs — feeder_id is direct FK) → {dim_id: {'MDI': n, 'MDNI': n}}.
    """
    rows = customers_qs.values('feeder_id', 'customer_type').annotate(count=Count('id'))
    result = {}
    for row in rows:
        dim_id = feeder_to_dim.get(row['feeder_id'])
        if dim_id is None:
            continue
        if dim_id not in result:
            result[dim_id] = {'MDI': 0, 'MDNI': 0}
        ctype = row['customer_type']
        if ctype in ('MDI', 'MDNI'):
            result[dim_id][ctype] += row['count']
    return result


# ── Coverage ──────────────────────────────────────────────────────────────────

def bulk_coverage(customers_qs, readings_qs, feeder_to_dim):
    """
    TWO queries (0 + 1 JOIN) → {dim_id: {total, readable, read, unread, rate, read_ids}}.
    Excludes faulty and missing meters from the denominator — those customers
    cannot physically be read, so including them distorts field performance.
    """
    # Total customers per feeder (0 JOINs)
    totals = {}
    for row in customers_qs.values('feeder_id').annotate(total=Count('id')):
        dim_id = feeder_to_dim.get(row['feeder_id'])
        if dim_id is not None:
            totals[dim_id] = totals.get(dim_id, 0) + row['total']

    # Readable customers per feeder — exclude faulty/missing (0 JOINs)
    readables = {}
    for row in customers_qs.exclude(meter_status__in=('faulty', 'missing')).values('feeder_id').annotate(count=Count('id')):
        dim_id = feeder_to_dim.get(row['feeder_id'])
        if dim_id is not None:
            readables[dim_id] = readables.get(dim_id, 0) + row['count']

    # Covered customer IDs per dim (1 JOIN: customer→feeder)
    read_ids_by_dim = {}
    for row in readings_qs.values('customer__feeder_id', 'customer_id').distinct():
        dim_id = feeder_to_dim.get(row['customer__feeder_id'])
        if dim_id is None:
            continue
        if dim_id not in read_ids_by_dim:
            read_ids_by_dim[dim_id] = set()
        read_ids_by_dim[dim_id].add(row['customer_id'])

    result = {}
    for dim_id in set(totals) | set(read_ids_by_dim):
        total    = totals.get(dim_id, 0)
        readable = readables.get(dim_id, total)
        read_ids = read_ids_by_dim.get(dim_id, set())
        read     = len(read_ids)
        result[dim_id] = {
            'total': total, 'readable': readable, 'read': read,
            'unread': readable - read,
            'rate':   round(read / readable * 100, 2) if readable else 0,
            'read_ids': read_ids,
        }
    return result


# ── Estimated billing ─────────────────────────────────────────────────────────

def bulk_estimated_billing(customers_qs, coverage_by_dim, date_range, feeder_to_dim):
    """
    TWO queries (0 JOINs each) → {dim_id: {estimated_kwh, estimated_energy_charge, estimated_revenue}}.
    """
    days        = date_range['days']
    all_covered = {cid for cov in coverage_by_dim.values() for cid in cov['read_ids']}

    # Unread customers with feeder_id — exclude faulty/missing (0 JOINs)
    unread = list(
        customers_qs
        .exclude(id__in=all_covered)
        .exclude(meter_status__in=('faulty', 'missing'))
        .values('id', 'feeder_id')
    )
    if not unread:
        return {}

    unread_ids   = [c['id'] for c in unread]
    customer_dim = {}
    for c in unread:
        dim_id = feeder_to_dim.get(c['feeder_id'])
        if dim_id is not None:
            customer_dim[c['id']] = dim_id

    # Fetch last 2 positive readings per unread customer to calculate interval
    from calendar import monthrange
    from collections import defaultdict as _defaultdict

    all_readings = list(
        MeterReading.objects
        .filter(customer_id__in=unread_ids, billed_consumption__gt=0, tariff_rate__isnull=False)
        .order_by('customer_id', '-reading_date')
        .values('customer_id', 'billed_consumption', 'tariff_rate', 'reading_date')
    )

    by_customer = _defaultdict(list)
    for r in all_readings:
        if len(by_customer[r['customer_id']]) < 2:
            by_customer[r['customer_id']].append(r)

    result = {}
    for cid, readings in by_customer.items():
        dim_id = customer_dim.get(cid)
        if dim_id is None:
            continue

        latest = readings[0]
        bc     = Decimal(str(latest['billed_consumption']))
        rate   = Decimal(str(latest['tariff_rate']))

        if len(readings) == 2:
            interval = (readings[0]['reading_date'] - readings[1]['reading_date']).days
        else:
            _, interval = monthrange(latest['reading_date'].year, latest['reading_date'].month)

        if interval <= 0:
            interval = 30

        daily_kwh    = bc / interval
        daily_charge = daily_kwh * rate
        daily_total  = daily_charge * (1 + VAT_RATE)
        if dim_id not in result:
            result[dim_id] = {'estimated_kwh': ZERO, 'estimated_energy_charge': ZERO, 'estimated_revenue': ZERO}
        result[dim_id]['estimated_kwh']           += daily_kwh * days
        result[dim_id]['estimated_energy_charge'] += daily_charge * days
        result[dim_id]['estimated_revenue']       += daily_total * days

    return {
        dim_id: {k: round(v, 2) for k, v in vals.items()}
        for dim_id, vals in result.items()
    }


# ── Energy delivered ──────────────────────────────────────────────────────────

_BALLOON = 500.0  # max acceptable daily MWh per feeder before treating as outlier


def _energy_by_feeder(feeder_ids, from_date, to_date):
    """
    Per-feeder energy delivered for the period.
    PRIMARY  — EnergyDelivered meter sum if max single day ≤ 500 MWh.
    FALLBACK — HourlyLoad avg_load × supply_hours.
    Returns {feeder_id: {'mwh': float, 'mode': 'meter'|'system'}}.
    """
    per_feeder = {}

    for row in (
        EnergyDelivered.objects
        .filter(feeder_id__in=feeder_ids, date__gte=from_date, date__lte=to_date)
        .values('feeder_id')
        .annotate(total=Sum('energy_mwh'), max_daily=Max('energy_mwh'), days=Count('id'))
    ):
        fid     = row['feeder_id']
        max_d   = float(row['max_daily'] or 0)
        days    = int(row['days'] or 1)
        if days > 0 and 0 < max_d <= _BALLOON:
            per_feeder[fid] = {'mwh': float(row['total'] or 0), 'mode': 'meter'}

    needs_system = [fid for fid in feeder_ids if fid not in per_feeder]
    if needs_system:
        for row in (
            HourlyLoad.objects
            .filter(feeder_id__in=needs_system, date__gte=from_date, date__lte=to_date, load_mw__gt=0)
            .values('feeder_id')
            .annotate(avg_load=Avg('load_mw'), supply_hours=Count('id'))
        ):
            fid = row['feeder_id']
            per_feeder[fid] = {
                'mwh':  float(row['avg_load'] or 0) * int(row['supply_hours'] or 0),
                'mode': 'system',
            }

    return per_feeder


def bulk_energy_delivered(feeder_ids, date_range, feeder_to_dim=None):
    """
    Returns {dim_id: {'total_mwh': float, 'mode': 'meter'|'system'|'mixed'}}.
    feeder_to_dim: {feeder_id: dim_id}. If None, feeder_id IS the dim (all_feeders).
    """
    if not feeder_ids:
        return {}

    per_feeder = _energy_by_feeder(feeder_ids, date_range['start_date'], date_range['end_date'])

    acc = {}
    for fid, ed in per_feeder.items():
        dim_id = feeder_to_dim[fid] if feeder_to_dim else fid
        if dim_id not in acc:
            acc[dim_id] = {'mwh': 0.0, 'meter': 0, 'system': 0}
        acc[dim_id]['mwh']        += ed['mwh']
        acc[dim_id][ed['mode']]   += 1

    result = {}
    for dim_id, r in acc.items():
        if r['system'] == 0:
            mode = 'meter'
        elif r['meter'] == 0:
            mode = 'system'
        else:
            mode = 'mixed'
        result[dim_id] = {'total_mwh': round(r['mwh'], 2), 'mode': mode}
    return result


def energy_per_feeder(feeder_ids, date_range):
    """
    Returns raw {feeder_id: {'mwh': float, 'mode': 'meter'|'system'}} for each feeder.
    Use this when you need to roll up to multiple dimensions without re-hitting the DB.
    """
    if not feeder_ids:
        return {}
    return _energy_by_feeder(feeder_ids, date_range['start_date'], date_range['end_date'])


def rollup_energy(per_feeder, feeder_to_dim):
    """
    Roll up per_feeder energy to per_dim without hitting the DB.
    per_feeder: output of energy_per_feeder()
    Returns {dim_id: {'total_mwh': float, 'mode': 'meter'|'system'|'mixed'}}.
    """
    acc = {}
    for fid, ed in per_feeder.items():
        dim_id = feeder_to_dim.get(fid)
        if dim_id is None:
            continue
        if dim_id not in acc:
            acc[dim_id] = {'mwh': 0.0, 'meter': 0, 'system': 0}
        acc[dim_id]['mwh']       += ed['mwh']
        acc[dim_id][ed['mode']]  += 1

    result = {}
    for dim_id, r in acc.items():
        if r['system'] == 0:
            mode = 'meter'
        elif r['meter'] == 0:
            mode = 'system'
        else:
            mode = 'mixed'
        result[dim_id] = {'total_mwh': round(r['mwh'], 2), 'mode': mode}
    return result


# ── Energy consumed ───────────────────────────────────────────────────────────

def bulk_energy_consumed(readings_qs, feeder_to_dim):
    """
    ONE query → {dim_id: consumed_kwh}.
    consumed = sum(present_reading - previous_reading) per dim.
    """
    _consumed_expr = ExpressionWrapper(
        F('present_reading') - F('previous_reading'),
        output_field=DecimalField(max_digits=20, decimal_places=4),
    )
    rows = (
        readings_qs
        .filter(present_reading__isnull=False, previous_reading__isnull=False)
        .values('customer__feeder_id')
        .annotate(total=Sum(_consumed_expr))
    )
    result = {}
    for row in rows:
        dim_id = feeder_to_dim.get(row['customer__feeder_id'])
        if dim_id is None:
            continue
        val = Decimal(str(row['total'] or 0))
        result[dim_id] = round(result.get(dim_id, ZERO) + val, 2)
    return result


# ── Managers ──────────────────────────────────────────────────────────────────

def bulk_managers(feeder_to_dim):
    """
    TWO queries (1 JOIN each: manager→assignment→feeder) → {dim_id: {'mdi': n, 'mdni': n}}.
    """
    result = {}
    for mtype in ('MDI', 'MDNI'):
        for row in (
            MeterManager.objects.filter(manager_type=mtype)
            .values('assignments__feeder_id')
            .annotate(count=Count('id', distinct=True))
        ):
            dim_id = feeder_to_dim.get(row['assignments__feeder_id'])
            if dim_id is None:
                continue
            if dim_id not in result:
                result[dim_id] = {'mdi': 0, 'mdni': 0}
            result[dim_id][mtype.lower()] += row['count']
    return result
