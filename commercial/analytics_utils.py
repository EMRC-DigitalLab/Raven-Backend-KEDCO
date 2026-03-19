"""
commercial/analytics_utils.py

Shared helpers for all commercial API views.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.db.models import Avg

from commercial.models import CommercialCustomer, MeterReading
from technical.models import FeederEnergyDaily

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


def calc_energy_delivered(feeder_ids):
    """
    Estimate daily energy delivered (MWh/day) using last 90 days average
    from FeederEnergyDaily (technical module).
    mode = 'estimated' — Option A.
    """
    latest = (
        FeederEnergyDaily.objects
        .filter(feeder_id__in=feeder_ids)
        .order_by('-date')
        .values_list('date', flat=True)
        .first()
    )
    if not latest:
        return Decimal('0')

    baseline_start = latest - timedelta(days=89)
    avg = (
        FeederEnergyDaily.objects
        .filter(feeder_id__in=feeder_ids, date__gte=baseline_start, date__lte=latest)
        .aggregate(avg_mwh=Avg('energy_mwh'))
    )
    return round(Decimal(str(avg['avg_mwh'] or 0)), 4)


def calc_atc_loss(energy_billed_kwh, daily_energy_delivered_mwh, days):
    """
    AT&C loss = 100 - billing_efficiency
    billing_efficiency = energy_billed / energy_delivered × 100
    Converts MWh → kWh for comparison.
    """
    energy_delivered_kwh = float(daily_energy_delivered_mwh) * 1000 * days
    if not energy_delivered_kwh:
        return None, None
    efficiency = round(float(energy_billed_kwh) / energy_delivered_kwh * 100, 2)
    atc_loss   = round(100 - efficiency, 2)
    return efficiency, atc_loss


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
