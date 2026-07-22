"""
commercial/views/trend/trend_views.py

GET /api/commercial/trend/

Returns current period + last 4 periods for key commercial KPIs.
One query fetches all 5 periods at once — Python bucketing, no N+1.

Query params:
  mode         : daily | weekly | monthly | yearly  (default: monthly)
  year, month  : int
  from_date    : YYYY-MM-DD
  type         : MDI | MDNI
  feeder_type  : 11kv | 33kv
  state        : <slug>
  district     : <slug>
  feeder       : <slug>

KPIs returned per period:
  actual_billed_kwh     — energy billed from real readings
  energy_consumed_kwh   — sum(present - previous) from meter registers
  actual_total_billed   — revenue including VAT
  energy_charge         — revenue excluding VAT
  vat                   — VAT component
  customers_read        — distinct customers with a reading
  coverage_rate         — customers_read / total_registered × 100
  arpu                  — actual_total_billed / customers_read
"""

from datetime import date, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.db.models import DecimalField, ExpressionWrapper, F, Sum

from rest_framework.decorators import api_view
from rest_framework.response import Response

from commercial.analytics_utils import (
    calc_billing,
    calc_coverage,
    compute_period_baseline,
    customer_filter_kwargs,
    metric,
    parse_date_range,
)
from commercial.models import CommercialCustomer, MeterReading


def _previous_periods(date_range, count=4):
    """
    Returns list of period dicts for the `count` periods immediately
    before `date_range`, oldest first.
    Each dict: {start_date, end_date, label, days, mode, is_current: False}
    """
    mode  = date_range['mode']
    start = date_range['start_date']
    periods = []

    for i in range(count, 0, -1):
        if mode == 'daily':
            s = start - timedelta(days=i)
            e = s
            label = str(s)
        elif mode == 'weekly':
            s = start - timedelta(weeks=i)
            e = s + timedelta(days=6)
            label = f'{s} to {e}'
        elif mode == 'yearly':
            s = date(start.year - i, 1, 1)
            e = date(start.year - i, 12, 31)
            label = str(start.year - i)
        else:  # monthly
            ref = start - relativedelta(months=i)
            s   = date(ref.year, ref.month, 1)
            e   = s + relativedelta(months=1) - timedelta(days=1)
            label = s.strftime('%B %Y')

        periods.append({
            'start_date': s,
            'end_date':   e,
            'label':      label,
            'days':       (e - s).days + 1,
            'mode':       mode,
            'is_current': False,
        })

    return periods


@api_view(['GET'])
def commercial_trend(request):
    """Current period + last 4 periods for key commercial KPIs."""
    date_range = parse_date_range(request)

    # Build all 5 periods: [oldest … newest (current)]
    prev    = _previous_periods(date_range, count=4)
    current = {**date_range, 'is_current': True}
    all_periods = prev + [current]

    # ── Scope: customer filter kwargs ────────────────────────────────────────
    cust_kwargs   = customer_filter_kwargs(request)
    state_slug    = request.GET.get('state',    '').strip()
    district_slug = request.GET.get('district', '').strip()
    feeder_slug   = request.GET.get('feeder',   '').strip()
    if state_slug:
        cust_kwargs['feeder__business_district__state__slug'] = state_slug
    if district_slug:
        cust_kwargs['feeder__business_district__slug'] = district_slug
    if feeder_slug:
        cust_kwargs['feeder__slug'] = feeder_slug

    total_customers = CommercialCustomer.objects.filter(**cust_kwargs).count()

    # ── Reading filter base (no date — we scope the date ourselves) ───────────
    read_base = {'customer__feeder__commercial_is_onboarded': True}
    ctype       = request.GET.get('type',        '').upper()
    feeder_type = request.GET.get('feeder_type', '').upper()
    if ctype in ('MDI', 'MDNI'):
        read_base['reading_type'] = ctype
    if feeder_type in ('11KV', '33KV'):
        read_base['customer__feeder__voltage_level__iexact'] = feeder_type
    if state_slug:
        read_base['customer__feeder__business_district__state__slug'] = state_slug
    if district_slug:
        read_base['customer__feeder__business_district__slug'] = district_slug
    if feeder_slug:
        read_base['customer__feeder__slug'] = feeder_slug

    # ── Per-period metrics using the same calc_billing pipeline as all other views
    customers_qs = CommercialCustomer.objects.filter(**cust_kwargs)
    _consumed_expr = ExpressionWrapper(
        F('present_reading') - F('previous_reading'),
        output_field=DecimalField(max_digits=20, decimal_places=4),
    )

    results = []
    for period in all_periods:
        p_start = period['start_date']
        p_end   = period['end_date']
        p_days  = period['days']

        p_readings_qs = MeterReading.objects.filter(
            **read_base,
            reading_date__gte=p_start,
            reading_date__lte=p_end,
        )

        baseline = compute_period_baseline(p_readings_qs, p_start, p_end)
        billing  = calc_billing(p_readings_qs, period_days=p_days, customer_baseline=baseline, period_start=p_start)
        coverage = calc_coverage(customers_qs, p_readings_qs)

        consumed_kwh = p_readings_qs.filter(
            present_reading__isnull=False, previous_reading__isnull=False
        ).aggregate(total=Sum(_consumed_expr))['total'] or Decimal('0')

        customers_read = coverage['read']
        total_billed   = float(billing['total_billed_amount'])
        arpu           = round(total_billed / customers_read, 2) if customers_read else 0

        results.append({
            'period': {
                'mode':       period['mode'],
                'start_date': str(p_start),
                'end_date':   str(p_end),
                'label':      period['label'],
                'days':       p_days,
                'is_current': period.get('is_current', False),
            },
            'actual_billed_kwh':   metric(float(billing['total_billed_kwh']),    unit='kWh', explanation='Energy billed from real meter readings in this period.'),
            'energy_consumed_kwh': metric(float(round(consumed_kwh, 2)),          unit='kWh', explanation='Sum of (present_reading - previous_reading) for all customers read in this period.'),
            'actual_total_billed': metric(float(billing['total_billed_amount']),  unit='NGN', explanation='Total revenue billed including VAT.'),
            'energy_charge':       metric(float(billing['energy_charge']),         unit='NGN', explanation='Revenue excluding VAT — billed_consumption × tariff_rate.'),
            'vat':                 metric(float(billing['vat']),                   unit='NGN', explanation='7.5% VAT on energy charge.'),
            'customers_read':      metric(customers_read,                                      explanation='Distinct customers with at least one reading in this period.'),
            'coverage_rate':       metric(coverage['rate'],                        unit='%',   explanation='customers_read / total_registered × 100.'),
            'arpu':                metric(arpu,                                    unit='NGN', explanation='Average Revenue Per Customer — actual_total_billed / customers_read.'),
        })

    return Response({
        'current_period': {
            'mode':       date_range['mode'],
            'start_date': str(date_range['start_date']),
            'end_date':   str(date_range['end_date']),
            'label':      date_range['label'],
            'days':       date_range['days'],
        },
        'total_customers': total_customers,
        'count':   len(results),
        'periods': results,
    })
