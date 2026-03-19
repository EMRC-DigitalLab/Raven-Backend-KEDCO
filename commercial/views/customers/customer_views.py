"""
commercial/views/customers/customer_views.py

GET /api/commercial/customers/
Returns paginated list of commercial customers with their period billing summary.
Filters: ?type=MDI|MDNI  ?feeder=<slug>  ?district=<slug>  ?state=<slug>
         ?search=<name|account|meter>
         ?page=1  ?page_size=50

GET /api/commercial/customers/<id>/
Full profile for one customer — all readings in the selected period.

GET /api/commercial/customers/top/
Top N customers by billed amount in the selected period.
?n=10  (default 10, max 50)
"""

from decimal import Decimal

from django.db.models import Q, Sum
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from commercial.analytics_utils import (
    customer_filter_kwargs,
    parse_date_range,
    reading_filter_kwargs,
)
from commercial.models import CommercialCustomer, MeterReading

VAT_RATE = Decimal('0.075')


def _customer_billing_summary(customer, readings_qs):
    """Compute billing summary for a single customer from a pre-scoped readings queryset."""
    r_qs = readings_qs.filter(customer=customer)
    rows = list(
        r_qs.filter(billed_consumption__isnull=False, tariff_rate__isnull=False)
        .values('billed_consumption', 'tariff_rate', 'reading_date')
        .order_by('-reading_date')
    )
    total_kwh     = Decimal('0')
    energy_charge = Decimal('0')
    for r in rows:
        kwh  = Decimal(str(r['billed_consumption']))
        rate = Decimal(str(r['tariff_rate']))
        total_kwh     += kwh
        energy_charge += kwh * rate

    vat   = round(energy_charge * VAT_RATE, 2)
    total = round(energy_charge + vat, 2)
    return {
        'readings_count':   len(rows),
        'total_billed_kwh': float(round(total_kwh, 2)),
        'energy_charge':    float(round(energy_charge, 2)),
        'vat':              float(vat),
        'total_billed':     float(total),
        'last_reading_date': str(rows[0]['reading_date']) if rows else None,
    }


@api_view(['GET'])
def customer_list(request):
    """Paginated customer list with period billing summary."""
    date_range   = parse_date_range(request)
    customers_qs = CommercialCustomer.objects.filter(**customer_filter_kwargs(request))
    readings_qs  = MeterReading.objects.filter(**reading_filter_kwargs(request, date_range))

    # Scope filters
    feeder_slug   = request.GET.get('feeder', '').strip()
    district_slug = request.GET.get('district', '').strip()
    state_slug    = request.GET.get('state', '').strip()
    search        = request.GET.get('search', '').strip()

    if feeder_slug:
        customers_qs = customers_qs.filter(feeder__slug=feeder_slug)
    if district_slug:
        customers_qs = customers_qs.filter(feeder__business_district__slug=district_slug)
    if state_slug:
        customers_qs = customers_qs.filter(feeder__business_district__state__slug=state_slug)
    if search:
        customers_qs = customers_qs.filter(
            Q(customer_name__icontains=search) |
            Q(account_no__icontains=search) |
            Q(meter_number__icontains=search)
        )

    # Pagination
    try:
        page      = max(1, int(request.GET.get('page', 1)))
        page_size = min(200, max(1, int(request.GET.get('page_size', 50))))
    except ValueError:
        page, page_size = 1, 50

    total_count = customers_qs.count()
    offset      = (page - 1) * page_size
    customers   = customers_qs.select_related(
        'feeder__business_district__state'
    ).order_by('customer_name')[offset: offset + page_size]

    results = []
    for c in customers:
        billing  = _customer_billing_summary(c, readings_qs)
        feeder   = c.feeder
        district = feeder.business_district if feeder else None
        state    = district.state if district else None
        results.append({
            'id':               c.id,
            'external_id':      c.external_id,
            'account_no':       c.account_no,
            'meter_number':     c.meter_number,
            'customer_name':    c.customer_name,
            'customer_type':    c.customer_type,
            'customer_address': c.customer_address,
            'phone_number':     c.phone_number,
            'feeder':           {'slug': feeder.slug, 'name': feeder.name} if feeder else None,
            'district':         {'slug': district.slug, 'name': district.name} if district else None,
            'state':            {'slug': state.slug, 'name': state.name} if state else None,
            'period_billing':   billing,
        })

    return Response({
        'period': {
            'mode': date_range['mode'], 'start_date': str(date_range['start_date']),
            'end_date': str(date_range['end_date']), 'label': date_range['label'], 'days': date_range['days'],
        },
        'pagination': {
            'total':     total_count,
            'page':      page,
            'page_size': page_size,
            'pages':     (total_count + page_size - 1) // page_size,
        },
        'customers': results,
    })


@api_view(['GET'])
def customer_detail(request, pk):
    """Full profile for one customer — all readings in the selected period."""
    try:
        customer = CommercialCustomer.objects.select_related(
            'feeder__business_district__state'
        ).get(pk=pk)
    except CommercialCustomer.DoesNotExist:
        return Response({'error': 'Customer not found.'}, status=status.HTTP_404_NOT_FOUND)

    date_range  = parse_date_range(request)
    readings_qs = MeterReading.objects.filter(
        customer=customer,
        reading_date__gte=date_range['start_date'],
        reading_date__lte=date_range['end_date'],
    ).order_by('-reading_date')

    readings_data = []
    for r in readings_qs:
        kwh  = Decimal(str(r.billed_consumption)) if r.billed_consumption else Decimal('0')
        rate = Decimal(str(r.tariff_rate))        if r.tariff_rate        else Decimal('0')
        ec   = round(kwh * rate, 2)
        vat  = round(ec * VAT_RATE, 2)
        readings_data.append({
            'id':                 r.id,
            'reading_date':       str(r.reading_date),
            'reading_type':       r.reading_type,
            'previous_reading':   float(r.previous_reading) if r.previous_reading else None,
            'present_reading':    float(r.present_reading)  if r.present_reading  else None,
            'consumption':        float(r.consumption)      if r.consumption       else None,
            'billed_consumption': float(kwh),
            'tariff_rate':        float(rate),
            'energy_charge':      float(ec),
            'vat':                float(vat),
            'total_billed':       float(ec + vat),
            'has_proof':          r.has_proof,
            'recorded_by':        r.recorded_by_name,
            'observation':        r.observation,
        })

    # Billing summary scoped to this customer in the period
    total_kwh     = Decimal('0')
    energy_charge = Decimal('0')
    for rd in readings_data:
        total_kwh     += Decimal(str(rd['billed_consumption']))
        energy_charge += Decimal(str(rd['energy_charge']))
    vat   = round(energy_charge * VAT_RATE, 2)
    total = round(energy_charge + vat, 2)
    billing_summary = {
        'readings_count':   len(readings_data),
        'total_billed_kwh': float(round(total_kwh, 2)),
        'energy_charge':    float(round(energy_charge, 2)),
        'vat':              float(vat),
        'total_billed':     float(total),
        'last_reading_date': readings_data[0]['reading_date'] if readings_data else None,
    }

    feeder   = customer.feeder
    district = feeder.business_district if feeder else None
    state    = district.state if district else None

    return Response({
        'period': {
            'mode': date_range['mode'], 'start_date': str(date_range['start_date']),
            'end_date': str(date_range['end_date']), 'label': date_range['label'], 'days': date_range['days'],
        },
        'customer': {
            'id':               customer.id,
            'external_id':      customer.external_id,
            'account_no':       customer.account_no,
            'meter_number':     customer.meter_number,
            'customer_name':    customer.customer_name,
            'customer_type':    customer.customer_type,
            'customer_address': customer.customer_address,
            'phone_number':     customer.phone_number,
            'feeder':           {'slug': feeder.slug, 'name': feeder.name} if feeder else None,
            'district':         {'slug': district.slug, 'name': district.name} if district else None,
            'state':            {'slug': state.slug, 'name': state.name} if state else None,
        },
        'period_billing': billing_summary,
        'readings':       readings_data,
    })


@api_view(['GET'])
def top_customers(request):
    """Top N customers by billed amount in the selected period."""
    date_range = parse_date_range(request)
    try:
        n = min(50, max(1, int(request.GET.get('n', 10))))
    except ValueError:
        n = 10

    filter_kwargs = reading_filter_kwargs(request, date_range)
    # Optional scope
    feeder_slug   = request.GET.get('feeder', '').strip()
    district_slug = request.GET.get('district', '').strip()
    state_slug    = request.GET.get('state', '').strip()
    if feeder_slug:
        filter_kwargs['customer__feeder__slug'] = feeder_slug
    if district_slug:
        filter_kwargs['customer__feeder__business_district__slug'] = district_slug
    if state_slug:
        filter_kwargs['customer__feeder__business_district__state__slug'] = state_slug

    # Get top N customer IDs by total billed_consumption in the period
    top_ids = list(
        MeterReading.objects
        .filter(**filter_kwargs, billed_consumption__isnull=False)
        .values('customer_id')
        .annotate(total_kwh=Sum('billed_consumption'))
        .order_by('-total_kwh')[:n]
        .values_list('customer_id', flat=True)
    )

    customers = CommercialCustomer.objects.filter(
        id__in=top_ids
    ).select_related('feeder__business_district__state')

    readings_qs = MeterReading.objects.filter(**reading_filter_kwargs(request, date_range))

    ranked = []
    for c in customers:
        billing  = _customer_billing_summary(c, readings_qs)
        feeder   = c.feeder
        district = feeder.business_district if feeder else None
        state    = district.state if district else None
        ranked.append({
            'id':             c.id,
            'account_no':     c.account_no,
            'meter_number':   c.meter_number,
            'customer_name':  c.customer_name,
            'customer_type':  c.customer_type,
            'feeder':         {'slug': feeder.slug, 'name': feeder.name} if feeder else None,
            'district':       {'slug': district.slug, 'name': district.name} if district else None,
            'state':          {'slug': state.slug, 'name': state.name} if state else None,
            'period_billing': billing,
        })

    # Sort by total_billed descending (accurate after VAT computation)
    ranked.sort(key=lambda x: x['period_billing']['total_billed'], reverse=True)

    return Response({
        'period': {
            'mode': date_range['mode'], 'start_date': str(date_range['start_date']),
            'end_date': str(date_range['end_date']), 'label': date_range['label'], 'days': date_range['days'],
        },
        'n':         n,
        'customers': ranked,
    })
