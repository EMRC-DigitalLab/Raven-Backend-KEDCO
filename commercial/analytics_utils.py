"""
commercial/analytics_utils.py

Shared helpers for all commercial API views.
"""

from datetime import date, datetime, timedelta
from collections import defaultdict
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.db.models import Count, ExpressionWrapper, F, Sum
from django.db.models import DecimalField as DField

from commercial.models import CommercialCustomer, MeterReading
from technical.utils.energy_utils import calculate_energy_delivered as _tech_energy_delivered

VAT_RATE = Decimal('0.075')


# ── Date helpers ──────────────────────────────────────────────────────────────

def parse_date_range(request):
    """
    Parse query params → {mode, start_date, end_date, label, days}
    Supports: daily | weekly | monthly | yearly
    """
    mode  = request.GET.get('mode', 'monthly')
    today = date.today()

    if mode == 'daily':
        raw = request.GET.get('from_date', str(today))
        try:
            d = datetime.strptime(raw[:10], '%Y-%m-%d').date()
        except ValueError:
            d = today
        return {'mode': 'daily', 'start_date': d, 'end_date': d, 'label': str(d), 'days': 1}

    if mode == 'weekly':
        raw = request.GET.get('from_date', str(today - timedelta(days=today.weekday())))
        try:
            start = datetime.strptime(raw[:10], '%Y-%m-%d').date()
        except ValueError:
            start = today - timedelta(days=today.weekday())
        end  = min(start + timedelta(days=6), today)
        days = (end - start).days + 1
        return {'mode': 'weekly', 'start_date': start, 'end_date': end, 'label': f'{start} to {end}', 'days': days}

    if mode == 'yearly':
        year  = int(request.GET.get('year', today.year))
        start = date(year, 1, 1)
        end   = min(date(year, 12, 31), today)
        days  = (end - start).days + 1
        return {'mode': 'yearly', 'start_date': start, 'end_date': end, 'label': str(year), 'days': days}

    # default: monthly
    year  = int(request.GET.get('year',  today.year))
    month = int(request.GET.get('month', today.month))
    start = date(year, month, 1)
    end   = min(start + relativedelta(months=1) - timedelta(days=1), today)
    days  = (end - start).days + 1
    return {
        'mode':       'monthly',
        'start_date': start,
        'end_date':   end,
        'label':      start.strftime('%B %Y'),
        'days':       days,
    }


# ── Filter helpers ─────────────────────────────────────────────────────────────

def customer_filter_kwargs(request):
    """Return filter kwargs for CommercialCustomer based on request params."""
    kwargs = {}
    ctype       = request.GET.get('type', '').upper()
    feeder_type = request.GET.get('feeder_type', '').upper()

    if ctype in ('MDI', 'MDNI'):
        kwargs['customer_type'] = ctype
    if feeder_type in ('11KV', '33KV'):
        kwargs['feeder__voltage_level__iexact'] = feeder_type
    return kwargs


def reading_filter_kwargs(request, date_range):
    """Return filter kwargs for MeterReading based on request params."""
    kwargs = {
        'reading_date__gte': date_range['start_date'],
        'reading_date__lte': date_range['end_date'],
    }
    ctype       = request.GET.get('type', '').upper()
    feeder_type = request.GET.get('feeder_type', '').upper()

    if ctype in ('MDI', 'MDNI'):
        kwargs['reading_type'] = ctype
    if feeder_type in ('11KV', '33KV'):
        kwargs['customer__feeder__voltage_level__iexact'] = feeder_type
    return kwargs


# ── Billing calculations ──────────────────────────────────────────────────────

def calc_billing(readings_qs):
    """
    Calculate energy and revenue totals from a MeterReading queryset.
    Raven does its own math: energy_charge = billed_consumption × tariff_rate
    Returns all figures in Decimal.
    """
    rows = list(
        readings_qs
        .filter(billed_consumption__isnull=False, tariff_rate__isnull=False)
        .values('billed_consumption', 'tariff_rate')
    )

    total_kwh    = Decimal('0')
    energy_charge = Decimal('0')

    for r in rows:
        kwh  = Decimal(str(r['billed_consumption']))
        rate = Decimal(str(r['tariff_rate']))
        total_kwh     += kwh
        energy_charge += kwh * rate

    vat          = round(energy_charge * VAT_RATE, 2)
    total_billed = round(energy_charge + vat, 2)
    energy_charge = round(energy_charge, 2)
    total_kwh     = round(total_kwh, 2)

    return {
        'total_billed_kwh':    total_kwh,
        'energy_charge':       energy_charge,
        'vat':                 vat,
        'total_billed_amount': total_billed,
    }


def calc_daily_estimate(billing, date_range):
    """
    Break weekly billed consumption into a daily estimate.
    daily = total_billed_kwh / days_in_period
    mode = 'estimated'
    """
    days = date_range['days']
    if not days:
        return Decimal('0')
    return round(billing['total_billed_kwh'] / days, 4)


def calc_coverage(customers_qs, readings_qs):
    """
    Customer reading coverage.
    Returns: total, read, unread, coverage_rate (%)
    """
    total      = customers_qs.count()
    read_ids   = set(readings_qs.values_list('customer_id', flat=True).distinct())
    read       = len(read_ids)
    unread     = total - read
    rate       = round(read / total * 100, 2) if total else 0
    return {'total': total, 'read': read, 'unread': unread, 'rate': rate, 'read_ids': read_ids}


def calc_estimated_billing(customers_qs, read_ids, date_range):
    """
    For unread customers: estimate their energy and revenue for the period
    using their last known daily average (last_billed_consumption ÷ 7 × period_days).

    Returns:
      estimated_kwh      — estimated energy that should have been billed (mode: estimated)
      estimated_revenue  — estimated revenue at risk including VAT (mode: estimated)
      estimated_energy_charge — estimated energy charge excl VAT
    """
    days       = date_range['days']
    unread_ids = list(customers_qs.exclude(id__in=read_ids).values_list('id', flat=True))

    last_readings = (
        MeterReading.objects
        .filter(customer_id__in=unread_ids, billed_consumption__isnull=False, tariff_rate__isnull=False)
        .order_by('customer_id', '-reading_date')
        .distinct('customer_id')
        .values('customer_id', 'billed_consumption', 'tariff_rate')
    )

    est_kwh           = Decimal('0')
    est_energy_charge = Decimal('0')
    est_revenue       = Decimal('0')

    for r in last_readings:
        daily_kwh      = Decimal(str(r['billed_consumption'])) / 7
        daily_charge   = daily_kwh * Decimal(str(r['tariff_rate']))
        daily_total    = daily_charge * (1 + VAT_RATE)
        est_kwh           += daily_kwh * days
        est_energy_charge += daily_charge * days
        est_revenue       += daily_total * days

    return {
        'estimated_kwh':           round(est_kwh, 2),
        'estimated_energy_charge': round(est_energy_charge, 2),
        'estimated_revenue':       round(est_revenue, 2),
    }


def calc_energy_delivered(feeder_ids, date_range):
    """
    Total energy delivered for the period (MWh) using the technical module.
    PRIMARY: EnergyDelivered meter data (actual sum for the period).
    FALLBACK: HourlyLoad avg × supply hours when no valid meter data.
    Returns {'total_mwh': float, 'mode': 'meter'|'system'|'mixed'}.
    """
    if not feeder_ids:
        return {'total_mwh': 0.0, 'mode': 'system'}
    result = _tech_energy_delivered(feeder_ids, date_range['start_date'], date_range['end_date'])
    total  = result['total_mwh']
    meter  = result['meter_feeders']
    system = result['system_feeders']
    if system == 0:
        mode = 'meter'
    elif meter == 0:
        mode = 'system'
    else:
        mode = 'mixed'
    return {'total_mwh': total, 'mode': mode}


def calc_energy_consumed(readings_qs):
    """
    Total energy consumed = sum(present_reading - previous_reading) for all readings in period.
    Uses actual meter register values, not billed_consumption.
    """
    result = (
        readings_qs
        .filter(present_reading__isnull=False, previous_reading__isnull=False)
        .aggregate(
            total=Sum(
                ExpressionWrapper(
                    F('present_reading') - F('previous_reading'),
                    output_field=DField(max_digits=20, decimal_places=4),
                )
            )
        )
    )
    return round(Decimal(str(result['total'] or 0)), 2)


def calc_atc_loss(energy_billed_kwh, energy_delivered_mwh):
    """
    AT&C loss = 100 - billing_efficiency
    billing_efficiency = energy_billed_kwh / (energy_delivered_mwh × 1000) × 100
    energy_delivered_mwh is the total for the period (not daily).
    Defaulting to 0 until energy_delivered data is complete across all commercial feeders.
    """
    return 0, 0
    # energy_delivered_kwh = float(energy_delivered_mwh) * 1000
    # if not energy_delivered_kwh:
    #     return 0, 0
    # efficiency = round(float(energy_billed_kwh) / energy_delivered_kwh * 100, 2)
    # atc_loss   = round(100 - efficiency, 2)
    # return efficiency, atc_loss


def calc_arpu(total_billed_amount, customers_billed):
    """Average Revenue Per Customer"""
    if not customers_billed:
        return Decimal('0')
    return round(Decimal(str(total_billed_amount)) / customers_billed, 2)


# ── Standard metric wrapper ───────────────────────────────────────────────────

def metric(value, unit='', mode='actual', explanation=''):
    """
    Every metric returned by the commercial API uses this structure.
    Ensures consistency across all endpoints and levels.
    """
    return {
        'value':       value,
        'unit':        unit,
        'mode':        mode,
        'explanation': explanation,
    }


# =============================================================================
# PARAMETER-BASED QUERYSET BUILDERS
# Used by reports/commercial_service.py — accept plain params, not request objects.
# =============================================================================

def get_customer_queryset(feeder_ids=None, district_ids=None, state_ids=None,
                          customer_type=None, voltage_level=None):
    """
    Return a scoped CommercialCustomer queryset.

    Args:
        feeder_ids    : list of feeder UUIDs
        district_ids  : list of BusinessDistrict UUIDs
        state_ids     : list of state UUIDs (via district__state)
        customer_type : 'MDI' | 'MDNI'
        voltage_level : '11KV' | '33KV'

    Returns: CommercialCustomer queryset
    """
    qs = CommercialCustomer.objects.all()
    if feeder_ids:
        qs = qs.filter(feeder_id__in=feeder_ids)
    if district_ids:
        qs = qs.filter(district_id__in=district_ids)
    if state_ids:
        qs = qs.filter(district__state_id__in=state_ids)
    if customer_type and customer_type.upper() in ('MDI', 'MDNI'):
        qs = qs.filter(customer_type=customer_type.upper())
    if voltage_level and voltage_level.upper() in ('11KV', '33KV'):
        qs = qs.filter(feeder__voltage_level__iexact=voltage_level)
    return qs


def get_reading_queryset(customer_qs, from_date, to_date, reading_type=None):
    """
    Return a scoped MeterReading queryset for the given customers and period.

    Args:
        customer_qs  : CommercialCustomer queryset (already scoped)
        from_date    : date
        to_date      : date
        reading_type : 'MDI' | 'MDNI' (optional further filter)

    Returns: MeterReading queryset
    """
    qs = MeterReading.objects.filter(
        customer__in=customer_qs,
        reading_date__gte=from_date,
        reading_date__lte=to_date,
    )
    if reading_type and reading_type.upper() in ('MDI', 'MDNI'):
        qs = qs.filter(reading_type=reading_type.upper())
    return qs


# =============================================================================
# AGGREGATED METRIC FUNCTIONS
# Each accepts already-scoped querysets — callers build the scope first.
# =============================================================================

def get_commercial_overview(customer_qs, reading_qs, feeder_ids, from_date, to_date):
    """
    Full commercial overview — delegates to existing calc_* helpers.

    Args:
        customer_qs : CommercialCustomer queryset (already scoped)
        reading_qs  : MeterReading queryset (already scoped)
        feeder_ids  : list of feeder UUIDs (for energy delivered lookup)
        from_date   : date
        to_date     : date

    Returns: dict with all key commercial KPIs
    """
    date_range = {
        'start_date': from_date,
        'end_date':   to_date,
        'days':       (to_date - from_date).days + 1,
    }

    billing   = calc_billing(reading_qs)
    coverage  = calc_coverage(customer_qs, reading_qs)
    estimated = calc_estimated_billing(customer_qs, coverage['read_ids'], date_range)
    energy_del = calc_energy_delivered(feeder_ids, date_range)
    energy_con = calc_energy_consumed(reading_qs)
    efficiency, atc_loss = calc_atc_loss(billing['total_billed_kwh'], energy_del['total_mwh'])
    arpu = calc_arpu(billing['total_billed_amount'], coverage['read'])

    # MDI / MDNI split
    mdi_billing  = calc_billing(reading_qs.filter(reading_type='MDI'))
    mdni_billing = calc_billing(reading_qs.filter(reading_type='MDNI'))

    return {
        'total_customers':      coverage['total'],
        'customers_read':       coverage['read'],
        'customers_unread':     coverage['unread'],
        'coverage_rate':        coverage['rate'],
        'mdi_count':            customer_qs.filter(customer_type='MDI').count(),
        'mdni_count':           customer_qs.filter(customer_type='MDNI').count(),
        'total_billed_kwh':     float(billing['total_billed_kwh']),
        'energy_charge':        float(billing['energy_charge']),
        'vat':                  float(billing['vat']),
        'total_billed_amount':  float(billing['total_billed_amount']),
        'estimated_kwh':        float(estimated['estimated_kwh']),
        'estimated_revenue':    float(estimated['estimated_revenue']),
        'energy_delivered_mwh': energy_del['total_mwh'],
        'energy_delivered_mode': energy_del['mode'],
        'energy_consumed_kwh':  float(energy_con),
        'billing_efficiency':   efficiency,
        'atc_loss':             atc_loss,
        'arpu':                 float(arpu),
        'mdi_revenue':          float(mdi_billing['total_billed_amount']),
        'mdni_revenue':         float(mdni_billing['total_billed_amount']),
    }


def get_revenue_by_district(customer_qs, from_date, to_date):
    """
    Revenue and consumption breakdown per business district for the period.

    Args:
        customer_qs : CommercialCustomer queryset (already scoped)
        from_date   : date
        to_date     : date

    Returns: list of dicts ordered by total_billed_amount descending
    """
    rows = list(
        MeterReading.objects
        .filter(
            customer__in=customer_qs,
            reading_date__gte=from_date,
            reading_date__lte=to_date,
            billed_consumption__isnull=False,
            tariff_rate__isnull=False,
        )
        .values('customer__district__name', 'billed_consumption', 'tariff_rate')
    )

    by_district = defaultdict(lambda: {'kwh': Decimal('0'), 'revenue': Decimal('0')})
    for r in rows:
        district = r['customer__district__name'] or 'Unassigned'
        kwh      = Decimal(str(r['billed_consumption']))
        rate     = Decimal(str(r['tariff_rate']))
        charge   = kwh * rate
        by_district[district]['kwh']     += kwh
        by_district[district]['revenue'] += charge + (charge * VAT_RATE)

    result = [
        {
            'district':            district,
            'total_billed_kwh':    float(round(vals['kwh'], 2)),
            'total_billed_amount': float(round(vals['revenue'], 2)),
        }
        for district, vals in by_district.items()
    ]
    result.sort(key=lambda x: x['total_billed_amount'], reverse=True)
    return result


def get_customer_type_summary(customer_qs, reading_qs):
    """
    MDI vs MDNI headcount and revenue summary.

    Args:
        customer_qs : CommercialCustomer queryset (already scoped)
        reading_qs  : MeterReading queryset (already scoped)

    Returns: dict with mdi and mdni sub-dicts
    """
    type_counts = dict(
        customer_qs
        .values('customer_type')
        .annotate(count=Count('id'))
        .values_list('customer_type', 'count')
    )

    mdi_billing  = calc_billing(reading_qs.filter(reading_type='MDI'))
    mdni_billing = calc_billing(reading_qs.filter(reading_type='MDNI'))

    return {
        'mdi': {
            'count':               type_counts.get('MDI', 0),
            'total_billed_kwh':    float(mdi_billing['total_billed_kwh']),
            'total_billed_amount': float(mdi_billing['total_billed_amount']),
        },
        'mdni': {
            'count':               type_counts.get('MDNI', 0),
            'total_billed_kwh':    float(mdni_billing['total_billed_kwh']),
            'total_billed_amount': float(mdni_billing['total_billed_amount']),
        },
    }
