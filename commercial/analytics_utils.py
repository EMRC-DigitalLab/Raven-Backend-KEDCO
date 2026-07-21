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


# ── Baseline helpers ───────────────────────────────────────────────────────────

def fetch_customer_baseline(customer_ids, period_start, period_end):
    """
    For first-sync customers (no prior reading in DB), derive estimated daily
    kWh from readings in the 60-day window AFTER period_end.
    Falls back to the 60-day window BEFORE period_start when no forward data.
    Returns {customer_id: Decimal daily_kwh}.
    """
    from datetime import timedelta

    fwd_start = period_end  + timedelta(days=1)
    fwd_end   = period_end  + timedelta(days=60)

    fwd_rows = list(
        MeterReading.objects
        .filter(
            customer_id__in=customer_ids,
            reading_date__gte=fwd_start,
            reading_date__lte=fwd_end,
            billed_consumption__isnull=False,
            billed_consumption__gt=0,
        )
        .values('customer_id')
        .annotate(total_kwh=Sum('billed_consumption'))
    )

    ref_days = max((fwd_end - fwd_start).days + 1, 1)
    result   = {}
    covered  = set()
    for r in fwd_rows:
        result[r['customer_id']] = Decimal(str(r['total_kwh'])) / Decimal(str(ref_days))
        covered.add(r['customer_id'])

    remaining = [cid for cid in customer_ids if cid not in covered]
    if remaining:
        bwd_start = period_start - timedelta(days=60)
        bwd_end   = period_start - timedelta(days=1)
        bwd_days  = max((bwd_end - bwd_start).days + 1, 1)
        bwd_rows  = list(
            MeterReading.objects
            .filter(
                customer_id__in=remaining,
                reading_date__gte=bwd_start,
                reading_date__lte=bwd_end,
                billed_consumption__isnull=False,
                billed_consumption__gt=0,
            )
            .values('customer_id')
            .annotate(total_kwh=Sum('billed_consumption'))
        )
        for r in bwd_rows:
            result[r['customer_id']] = Decimal(str(r['total_kwh'])) / Decimal(str(bwd_days))

    return result


def compute_period_baseline(readings_qs, period_start, period_end):
    """
    Identify customers in readings_qs that have no prior reading in Raven's DB
    (i.e. first-sync period), then return their estimated daily kWh derived
    from surrounding months via fetch_customer_baseline().

    Uses period_start as the cutoff (not min reading date from the queryset) so
    that the same customer is always classified the same way regardless of which
    scoped queryset calls this function.

    Returns {customer_id: Decimal daily_kwh}.
    """
    customer_ids = list(
        readings_qs
        .filter(billed_consumption__isnull=False)
        .values_list('customer_id', flat=True)
        .distinct()
    )
    if not customer_ids:
        return {}

    has_prior = set(
        MeterReading.objects
        .filter(customer_id__in=customer_ids, reading_date__lt=period_start)
        .values_list('customer_id', flat=True)
        .distinct()
    )

    no_prev_ids = [cid for cid in customer_ids if cid not in has_prior]
    if not no_prev_ids:
        return {}

    return fetch_customer_baseline(no_prev_ids, period_start, period_end)


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
    kwargs = {'feeder__commercial_is_onboarded': True}
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
        'customer__feeder__commercial_is_onboarded': True,
    }
    ctype       = request.GET.get('type', '').upper()
    feeder_type = request.GET.get('feeder_type', '').upper()

    if ctype in ('MDI', 'MDNI'):
        kwargs['reading_type'] = ctype
    if feeder_type in ('11KV', '33KV'):
        kwargs['customer__feeder__voltage_level__iexact'] = feeder_type
    return kwargs


# ── Billing calculations ──────────────────────────────────────────────────────

def calc_billing(readings_qs, period_days=None, customer_baseline=None, period_start=None, baseline_period_days=None):
    """
    Calculate energy and revenue totals from a MeterReading queryset.
    Raven does its own math: energy_charge = billed_consumption × tariff_rate.

    When period_days is supplied, each reading's billed_consumption is
    normalised to the selected period:
        period_kwh = billed_consumption / billing_cycle_days × period_days
    billing_cycle_days defaults to 30 when no previous reading exists in DB.

    customer_baseline: optional {customer_id: Decimal daily_kwh} produced by
    compute_period_baseline(). When a customer has no prior reading in DB and
    appears in this map, their contribution is derived from the baseline daily
    rate instead of the raw (potentially inflated first-sync) billed_consumption.
    Only one baseline contribution is counted per customer regardless of how
    many readings they have in the period. All baseline-derived values are
    counted as estimated.

    period_start: the start date of the period being analysed. Used as the
    cutoff for the prev_date_map lookup so results are consistent regardless of
    which customers happen to have the earliest reading date in the queryset.
    """
    from django.db.models import Max

    _ZERO = Decimal('0')
    _EMPTY = {
        'total_billed_kwh': _ZERO, 'estimated_billed_kwh': _ZERO,
        'energy_charge': _ZERO, 'vat': _ZERO, 'total_billed_amount': _ZERO,
    }

    customer_baseline = customer_baseline or {}

    rows = list(
        readings_qs
        .filter(billed_consumption__isnull=False, tariff_rate__isnull=False)
        .values('customer_id', 'reading_date', 'billed_consumption', 'tariff_rate', 'estimation_method')
    )

    if not rows:
        return _EMPTY

    # ── Period normalisation setup ────────────────────────────────────────────
    prev_date_map = {}
    if period_days:
        customer_ids = list({r['customer_id'] for r in rows})
        # Use period_start as the cutoff if provided; otherwise fall back to
        # the earliest reading date in the queryset. period_start is preferred
        # because it makes the prev_date lookup stable across different query scopes.
        cutoff = period_start if period_start else min(r['reading_date'] for r in rows)
        prev_date_map = {
            r['customer_id']: r['prev_date']
            for r in (
                MeterReading.objects
                .filter(customer_id__in=customer_ids, reading_date__lt=cutoff)
                .values('customer_id')
                .annotate(prev_date=Max('reading_date'))
            )
        }

    estimated_kwh  = _ZERO
    energy_charge  = _ZERO
    baseline_done  = set()  # prevent counting baseline customers more than once

    for r in rows:
        customer_id = r['customer_id']
        raw_kwh     = Decimal(str(r['billed_consumption']))
        rate        = Decimal(str(r['tariff_rate']))

        if period_days:
            prev_date = prev_date_map.get(customer_id)
            if prev_date:
                billing_days = max((r['reading_date'] - prev_date).days, 1)
                kwh = raw_kwh / Decimal(str(billing_days)) * Decimal(str(period_days))
            elif customer_id in customer_baseline:
                if customer_id in baseline_done:
                    continue
                kwh = customer_baseline[customer_id] * Decimal(str(period_days))
                baseline_done.add(customer_id)
            else:
                kwh = raw_kwh / Decimal('30') * Decimal(str(period_days))
        else:
            # No period normalisation — use raw billed_consumption.
            # Exception: first-sync customers whose raw value is inflated
            # (accumulated months of data). For those, use the baseline
            # daily estimate × baseline_period_days to get a realistic figure.
            if customer_id in customer_baseline and baseline_period_days:
                if customer_id in baseline_done:
                    continue
                kwh = customer_baseline[customer_id] * Decimal(str(baseline_period_days))
                baseline_done.add(customer_id)
            else:
                kwh = raw_kwh

        energy_charge += kwh * rate
        estimated_kwh += kwh

    vat          = round(energy_charge * VAT_RATE, 2)
    total_billed = round(energy_charge + vat, 2)
    energy_charge = round(energy_charge, 2)

    return {
        'total_billed_kwh':     round(estimated_kwh, 2),
        'estimated_billed_kwh': round(estimated_kwh, 2),
        'energy_charge':        energy_charge,
        'vat':                  vat,
        'total_billed_amount':  total_billed,
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
    Excludes faulty and missing meters from the denominator — those customers
    cannot physically be read, so including them distorts field performance.
    Returns: total, readable, read, unread, coverage_rate (%)
    """
    total      = customers_qs.count()
    readable_qs = customers_qs.exclude(meter_status__in=('faulty', 'missing'))
    readable   = readable_qs.count()
    read_ids   = set(readings_qs.values_list('customer_id', flat=True).distinct())
    read       = len(read_ids)
    unread     = readable - read
    rate       = round(read / readable * 100, 2) if readable else 0
    return {'total': total, 'readable': readable, 'read': read, 'unread': unread, 'rate': rate, 'read_ids': read_ids}


def calc_estimated_billing(customers_qs, read_ids, date_range):
    """
    For unread customers: estimate their energy and revenue for the period
    using their last known daily average:
      daily_kwh = last_billed_consumption / reading_interval_days
      estimate  = daily_kwh * period_days

    reading_interval_days is derived from the gap between the customer's last
    two positive readings. If only one reading exists, falls back to the number
    of days in that reading's month (e.g. 31 for January, 28 for February).

    Returns:
      estimated_kwh           — estimated energy (mode: estimated)
      estimated_revenue       — estimated revenue incl VAT (mode: estimated)
      estimated_energy_charge — estimated energy charge excl VAT
    """
    from calendar import monthrange
    from collections import defaultdict

    days       = date_range['days']
    unread_ids = list(
        customers_qs
        .exclude(id__in=read_ids)
        .exclude(meter_status__in=('faulty', 'missing'))
        .values_list('id', flat=True)
    )

    # Fetch the last 2 positive readings per customer to calculate interval
    all_readings = list(
        MeterReading.objects
        .filter(customer_id__in=unread_ids, billed_consumption__gt=0, tariff_rate__isnull=False)
        .order_by('customer_id', '-reading_date')
        .values('customer_id', 'billed_consumption', 'tariff_rate', 'reading_date')
    )

    # Group by customer, keep only the latest 2 per customer
    by_customer = defaultdict(list)
    for r in all_readings:
        if len(by_customer[r['customer_id']]) < 2:
            by_customer[r['customer_id']].append(r)

    est_kwh           = Decimal('0')
    est_energy_charge = Decimal('0')
    est_revenue       = Decimal('0')

    for readings in by_customer.values():
        latest = readings[0]
        bc     = Decimal(str(latest['billed_consumption']))
        rate   = Decimal(str(latest['tariff_rate']))

        if len(readings) == 2:
            interval = (readings[0]['reading_date'] - readings[1]['reading_date']).days
        else:
            _, interval = monthrange(latest['reading_date'].year, latest['reading_date'].month)

        if interval <= 0:
            interval = 30

        daily_kwh      = bc / interval
        daily_charge   = daily_kwh * rate
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
    qs = CommercialCustomer.objects.filter(feeder__commercial_is_onboarded=True)
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

def get_commercial_overview(customer_qs, reading_qs, feeder_ids, from_date, to_date,
                            normalize=True):
    """
    Full commercial overview — delegates to existing calc_* helpers.

    Args:
        customer_qs : CommercialCustomer queryset (already scoped)
        reading_qs  : MeterReading queryset (already scoped)
        feeder_ids  : list of feeder UUIDs (for energy delivered lookup)
        from_date   : date
        to_date     : date
        normalize   : if True (default/dashboard), normalise each reading to the
                      selected period length.  Pass False for management reports
                      that must show actual billed totals, not normalised projections.

    Returns: dict with all key commercial KPIs
    """
    date_range = {
        'start_date': from_date,
        'end_date':   to_date,
        'days':       (to_date - from_date).days + 1,
    }

    period_days_arg = date_range['days'] if normalize else None

    baseline   = compute_period_baseline(reading_qs, from_date, to_date)
    billing    = calc_billing(reading_qs, period_days=period_days_arg, customer_baseline=baseline, period_start=from_date)
    coverage   = calc_coverage(customer_qs, reading_qs)
    estimated  = calc_estimated_billing(customer_qs, coverage['read_ids'], date_range)
    energy_del = calc_energy_delivered(feeder_ids, date_range)
    energy_con = calc_energy_consumed(reading_qs)
    efficiency, atc_loss = calc_atc_loss(billing['total_billed_kwh'], energy_del['total_mwh'])
    arpu = calc_arpu(billing['total_billed_amount'], coverage['read'])

    mdi_qs       = reading_qs.filter(reading_type='MDI')
    mdni_qs      = reading_qs.filter(reading_type='MDNI')
    mdi_billing  = calc_billing(mdi_qs,  period_days=period_days_arg, customer_baseline=baseline, period_start=from_date)
    mdni_billing = calc_billing(mdni_qs, period_days=period_days_arg, customer_baseline=baseline, period_start=from_date)

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


def get_commercial_coverage(customer_qs, reading_qs, from_date, to_date):
    """
    Reading coverage detail — how many customers were read vs unread,
    and the estimated revenue at risk from unread customers.

    Args:
        customer_qs : CommercialCustomer queryset (already scoped)
        reading_qs  : MeterReading queryset (already scoped)
        from_date   : date
        to_date     : date

    Returns: dict
    """
    date_range = {
        'start_date': from_date,
        'end_date':   to_date,
        'days':       (to_date - from_date).days + 1,
    }
    baseline  = compute_period_baseline(reading_qs, from_date, to_date)
    billing   = calc_billing(reading_qs, period_days=date_range['days'], customer_baseline=baseline, period_start=from_date)
    coverage  = calc_coverage(customer_qs, reading_qs)
    estimated = calc_estimated_billing(customer_qs, coverage['read_ids'], date_range)

    return {
        'total_customers':      coverage['total'],
        'customers_read':       coverage['read'],
        'customers_unread':     coverage['unread'],
        'coverage_rate':        coverage['rate'],
        'total_billed_amount':  float(billing['total_billed_amount']),
        'total_billed_kwh':     float(billing['total_billed_kwh']),
        'estimated_kwh':        float(estimated['estimated_kwh']),
        'estimated_revenue':    float(estimated['estimated_revenue']),
    }


def get_commercial_energy(reading_qs, feeder_ids, from_date, to_date):
    """
    Energy analysis — delivered vs consumed vs billed, with AT&C loss.

    Args:
        reading_qs : MeterReading queryset (already scoped)
        feeder_ids : list of feeder UUIDs
        from_date  : date
        to_date    : date

    Returns: dict
    """
    date_range = {
        'start_date': from_date,
        'end_date':   to_date,
        'days':       (to_date - from_date).days + 1,
    }
    baseline   = compute_period_baseline(reading_qs, from_date, to_date)
    billing    = calc_billing(reading_qs, period_days=date_range['days'], customer_baseline=baseline, period_start=from_date)
    energy_del = calc_energy_delivered(feeder_ids, date_range)
    energy_con = calc_energy_consumed(reading_qs)
    efficiency, atc_loss = calc_atc_loss(billing['total_billed_kwh'], energy_del['total_mwh'])

    return {
        'energy_delivered_mwh':  energy_del['total_mwh'],
        'energy_delivered_mode': energy_del['mode'],
        'energy_consumed_kwh':   float(energy_con),
        'total_billed_kwh':      float(billing['total_billed_kwh']),
        'billing_efficiency':    efficiency,
        'atc_loss':              atc_loss,
    }


def get_revenue_by_feeder(customer_qs, from_date, to_date):
    """
    Revenue and consumption breakdown per feeder for the period.

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
        .values('customer__feeder__name', 'billed_consumption', 'tariff_rate')
    )

    by_feeder = defaultdict(lambda: {'kwh': Decimal('0'), 'revenue': Decimal('0'), 'readings': 0})
    for r in rows:
        feeder  = r['customer__feeder__name'] or 'Unassigned'
        kwh     = Decimal(str(r['billed_consumption']))
        rate    = Decimal(str(r['tariff_rate']))
        charge  = kwh * rate
        by_feeder[feeder]['kwh']      += kwh
        by_feeder[feeder]['revenue']  += charge + (charge * VAT_RATE)
        by_feeder[feeder]['readings'] += 1

    result = [
        {
            'feeder':              feeder,
            'readings':            vals['readings'],
            'total_billed_kwh':    float(round(vals['kwh'], 2)),
            'total_billed_amount': float(round(vals['revenue'], 2)),
        }
        for feeder, vals in by_feeder.items()
    ]
    result.sort(key=lambda x: x['total_billed_amount'], reverse=True)
    return result


def get_customer_type_summary(customer_qs, reading_qs, period_days=None, period_start=None, customer_baseline=None):
    """
    MDI vs MDNI headcount and revenue summary.

    Args:
        customer_qs       : CommercialCustomer queryset (already scoped)
        reading_qs        : MeterReading queryset (already scoped)
        period_days       : int — when supplied, normalises billing to this period
        period_start      : date — start of the period; stabilises prev_date_map lookup
        customer_baseline : {customer_id: Decimal daily_kwh} from compute_period_baseline

    Returns: dict with mdi and mdni sub-dicts
    """
    type_counts = dict(
        customer_qs
        .values('customer_type')
        .annotate(count=Count('id'))
        .values_list('customer_type', 'count')
    )

    mdi_billing  = calc_billing(reading_qs.filter(reading_type='MDI'),  period_days=period_days, customer_baseline=customer_baseline, period_start=period_start)
    mdni_billing = calc_billing(reading_qs.filter(reading_type='MDNI'), period_days=period_days, customer_baseline=customer_baseline, period_start=period_start)

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
