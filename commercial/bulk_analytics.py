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

from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count, ExpressionWrapper, F, Sum, DecimalField

from commercial.models import MeterManager, MeterReading
from technical.models import FeederEnergyDaily

VAT_RATE = Decimal('0.075')
ZERO     = Decimal('0')

_EC_EXPR = ExpressionWrapper(
    F('billed_consumption') * F('tariff_rate'),
    output_field=DecimalField(max_digits=20, decimal_places=4),
)


# ── Defaults ──────────────────────────────────────────────────────────────────

def empty_billing():
    return {'total_billed_kwh': ZERO, 'energy_charge': ZERO, 'vat': ZERO, 'total_billed_amount': ZERO}

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

def bulk_billing(readings_qs, feeder_to_dim):
    """
    ONE query (1 JOIN: customer→feeder) → {dim_id: billing dict}.
    Python rollup maps feeder → dim.
    """
    rows = (
        readings_qs
        .filter(billed_consumption__isnull=False, tariff_rate__isnull=False)
        .values('customer__feeder_id')
        .annotate(total_kwh=Sum('billed_consumption'), raw_ec=Sum(_EC_EXPR))
    )
    acc = {}
    for row in rows:
        dim_id = feeder_to_dim.get(row['customer__feeder_id'])
        if dim_id is None:
            continue
        if dim_id not in acc:
            acc[dim_id] = {'kwh': ZERO, 'ec': ZERO}
        acc[dim_id]['kwh'] += Decimal(str(row['total_kwh'] or 0))
        acc[dim_id]['ec']  += Decimal(str(row['raw_ec']    or 0))

    result = {}
    for dim_id, r in acc.items():
        kwh = round(r['kwh'], 2)
        ec  = round(r['ec'],  2)
        vat = round(ec * VAT_RATE, 2)
        result[dim_id] = {
            'total_billed_kwh':    kwh,
            'energy_charge':       ec,
            'vat':                 vat,
            'total_billed_amount': round(ec + vat, 2),
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
    TWO queries (0 + 1 JOIN) → {dim_id: {total, read, unread, rate, read_ids}}.
    """
    # Total customers per feeder (0 JOINs)
    totals = {}
    for row in customers_qs.values('feeder_id').annotate(total=Count('id')):
        dim_id = feeder_to_dim.get(row['feeder_id'])
        if dim_id is not None:
            totals[dim_id] = totals.get(dim_id, 0) + row['total']

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
        read_ids = read_ids_by_dim.get(dim_id, set())
        read     = len(read_ids)
        result[dim_id] = {
            'total': total, 'read': read, 'unread': total - read,
            'rate':  round(read / total * 100, 2) if total else 0,
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

    # Unread customers with feeder_id (0 JOINs)
    unread = list(customers_qs.exclude(id__in=all_covered).values('id', 'feeder_id'))
    if not unread:
        return {}

    unread_ids   = [c['id'] for c in unread]
    customer_dim = {}
    for c in unread:
        dim_id = feeder_to_dim.get(c['feeder_id'])
        if dim_id is not None:
            customer_dim[c['id']] = dim_id

    # Last reading per unread customer (0 JOINs — customer_id is direct FK)
    last_readings = (
        MeterReading.objects
        .filter(customer_id__in=unread_ids, billed_consumption__isnull=False, tariff_rate__isnull=False)
        .order_by('customer_id', '-reading_date')
        .distinct('customer_id')
        .values('customer_id', 'billed_consumption', 'tariff_rate')
    )

    result = {}
    for r in last_readings:
        dim_id = customer_dim.get(r['customer_id'])
        if dim_id is None:
            continue
        daily_kwh    = Decimal(str(r['billed_consumption'])) / 7
        daily_charge = daily_kwh * Decimal(str(r['tariff_rate']))
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

def bulk_energy_delivered(feeder_ids, feeder_to_dim=None):
    """
    TWO queries → {dim_id: daily_avg_mwh}.
    feeder_to_dim: {feeder_id: dim_id}. If None, feeder_id IS the dim (all_feeders).
    """
    if not feeder_ids:
        return {}

    latest = (
        FeederEnergyDaily.objects
        .filter(feeder_id__in=feeder_ids)
        .order_by('-date')
        .values_list('date', flat=True)
        .first()
    )
    if not latest:
        return {}

    avgs = (
        FeederEnergyDaily.objects
        .filter(feeder_id__in=feeder_ids, date__gte=latest - timedelta(89), date__lte=latest)
        .values('feeder_id')
        .annotate(avg_mwh=Avg('energy_mwh'))
    )

    result = {}
    for row in avgs:
        fid    = row['feeder_id']
        dim_id = feeder_to_dim[fid] if feeder_to_dim else fid
        val    = round(Decimal(str(row['avg_mwh'] or 0)), 4)
        result[dim_id] = round(result.get(dim_id, ZERO) + val, 4)
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
