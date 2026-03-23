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
from django.db.models import DecimalField, ExpressionWrapper, F

from rest_framework.decorators import api_view
from rest_framework.response import Response

from commercial.analytics_utils import (
    VAT_RATE,
    customer_filter_kwargs,
    metric,
    parse_date_range,
    reading_filter_kwargs,
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
    read_base = {}
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

    # ── ONE query spanning all 5 periods ─────────────────────────────────────
    overall_start = all_periods[0]['start_date']
    overall_end   = all_periods[-1]['end_date']

    rows = list(
        MeterReading.objects
        .filter(
            **read_base,
            reading_date__gte=overall_start,
            reading_date__lte=overall_end,
            billed_consumption__isnull=False,
            tariff_rate__isnull=False,
        )
        .values(
            'reading_date',
            'customer_id',
            'billed_consumption',
            'tariff_rate',
            'present_reading',
            'previous_reading',
        )
    )

    # ── Bucket rows into periods in Python — no extra DB hits ─────────────────
    ZERO = Decimal('0')

    results = []
    for period in all_periods:
        p_start = period['start_date']
        p_end   = period['end_date']

        total_kwh    = ZERO
        raw_ec       = ZERO
        consumed_kwh = ZERO
        read_ids     = set()

        for r in rows:
            if not (p_start <= r['reading_date'] <= p_end):
                continue
            kwh  = Decimal(str(r['billed_consumption']))
            rate = Decimal(str(r['tariff_rate']))
            total_kwh += kwh
            raw_ec    += kwh * rate
            read_ids.add(r['customer_id'])
            if r['present_reading'] is not None and r['previous_reading'] is not None:
                consumed_kwh += Decimal(str(r['present_reading'])) - Decimal(str(r['previous_reading']))

        energy_charge  = round(raw_ec, 2)
        vat            = round(raw_ec * VAT_RATE, 2)
        total_billed   = round(energy_charge + vat, 2)
        customers_read = len(read_ids)
        coverage_rate  = round(customers_read / total_customers * 100, 2) if total_customers else 0
        arpu           = round(total_billed / customers_read, 2) if customers_read else ZERO

        results.append({
            'period': {
                'mode':       period['mode'],
                'start_date': str(p_start),
                'end_date':   str(p_end),
                'label':      period['label'],
                'days':       period['days'],
                'is_current': period['is_current'],
            },
            'actual_billed_kwh':   metric(float(round(total_kwh, 2)),    unit='kWh',  explanation='Energy billed from real meter readings in this period.'),
            'energy_consumed_kwh': metric(float(round(consumed_kwh, 2)), unit='kWh',  explanation='Sum of (present_reading - previous_reading) for all customers read in this period.'),
            'actual_total_billed': metric(float(total_billed),           unit='NGN',  explanation='Total revenue billed including VAT.'),
            'energy_charge':       metric(float(energy_charge),          unit='NGN',  explanation='Revenue excluding VAT — billed_consumption × tariff_rate.'),
            'vat':                 metric(float(vat),                    unit='NGN',  explanation='7.5% VAT on energy charge.'),
            'customers_read':      metric(customers_read,                             explanation='Distinct customers with at least one reading in this period.'),
            'coverage_rate':       metric(coverage_rate,                 unit='%',    explanation='customers_read / total_registered × 100.'),
            'arpu':                metric(float(arpu),                   unit='NGN',  explanation='Average Revenue Per Customer — actual_total_billed / customers_read.'),
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
