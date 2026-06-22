from datetime import datetime

from django.db.models import Avg, Count, ExpressionWrapper, DecimalField, F, Q, Sum


def _parse(d: str):
    return datetime.strptime(d, '%Y-%m-%d').date()


def query_commercial(start_date: str, end_date: str, feeder: str = None, district: str = None, state: str = None) -> dict:
    """Return commercial metrics (billing efficiency, ATC loss proxy, customer counts, revenue) for the period."""
    from commercial.models import CommercialCustomer, MeterReading
    from common.models import BusinessDistrict, Feeder, State

    start, end = _parse(start_date), _parse(end_date)

    cust_filter: dict = {}
    read_filter: dict = {'reading_date__gte': start, 'reading_date__lte': end}

    if feeder:
        obj = Feeder.objects.filter(Q(slug=feeder) | Q(name__icontains=feeder)).first()
        if obj:
            cust_filter['feeder'] = obj
            read_filter['customer__feeder'] = obj

    if district:
        obj = BusinessDistrict.objects.filter(Q(slug=district) | Q(name__icontains=district)).first()
        if obj:
            cust_filter['district'] = obj
            read_filter['customer__district'] = obj

    if state:
        obj = State.objects.filter(Q(slug=state) | Q(name__icontains=state)).first()
        if obj:
            districts = BusinessDistrict.objects.filter(state=obj)
            cust_filter['district__in'] = districts
            read_filter['customer__district__in'] = districts

    total_customers = CommercialCustomer.objects.filter(**cust_filter).count()
    mdi_count  = CommercialCustomer.objects.filter(**cust_filter, customer_type='MDI').count()
    mdni_count = CommercialCustomer.objects.filter(**cust_filter, customer_type='MDNI').count()
    bypass_count  = CommercialCustomer.objects.filter(**cust_filter, is_bypass=True).count()
    faulty_meters = CommercialCustomer.objects.filter(**cust_filter, meter_status='faulty').count()

    readings_qs = MeterReading.objects.filter(**read_filter)
    customers_read = readings_qs.values('customer').distinct().count()

    totals = readings_qs.aggregate(
        total_billed_kwh=Sum('billed_consumption'),
        total_readings=Count('id'),
    )

    billed_naira_data = (
        readings_qs
        .exclude(billed_consumption__isnull=True)
        .exclude(tariff_rate__isnull=True)
        .aggregate(
            energy_charge=Sum(
                ExpressionWrapper(F('billed_consumption') * F('tariff_rate'), output_field=DecimalField())
            )
        )
    )

    total_billed_kwh    = float(totals['total_billed_kwh'] or 0)
    total_energy_charge = float(billed_naira_data['energy_charge'] or 0)
    total_vat           = round(total_energy_charge * 0.075, 2)
    total_billed_naira  = round(total_energy_charge + total_vat, 2)
    billing_efficiency  = round(customers_read / total_customers * 100, 1) if total_customers else 0

    # Late / OCR / audit breakdown
    late_readings     = readings_qs.filter(submission_status='late').count()
    ocr_mismatch      = readings_qs.filter(ocr_status='mismatch').count()
    audit_rejected    = readings_qs.filter(audit_status='rejected').count()

    return {
        'period': {'start': start_date, 'end': end_date},
        'scope': {'feeder': feeder, 'district': district, 'state': state},
        'customers': {
            'total': total_customers,
            'mdi': mdi_count,
            'mdni': mdni_count,
            'with_bypass_meter': bypass_count,
            'with_faulty_meter': faulty_meters,
            'read_in_period': customers_read,
        },
        'billing': {
            'billing_efficiency_pct': billing_efficiency,
            'total_billed_kwh': total_billed_kwh,
            'total_billed_mwh': round(total_billed_kwh / 1000, 2),
            'total_billed_naira': total_billed_naira,
            'total_readings_submitted': totals['total_readings'],
            'late_submissions': late_readings,
            'ocr_mismatches': ocr_mismatch,
            'audit_rejections': audit_rejected,
        },
    }


def query_top_commercial_feeders(start_date: str, end_date: str, limit: int = 10) -> dict:
    """Return top feeders ranked by billed consumption."""
    from commercial.models import MeterReading

    start, end = _parse(start_date), _parse(end_date)

    rows = (
        MeterReading.objects
        .filter(reading_date__gte=start, reading_date__lte=end)
        .values('customer__feeder__name', 'customer__feeder__business_district__name')
        .annotate(total_kwh=Sum('billed_consumption'), reading_count=Count('id'))
        .order_by('-total_kwh')[:limit]
    )

    return {
        'period': {'start': start_date, 'end': end_date},
        'top_feeders_by_billed_kwh': [
            {
                'feeder': r['customer__feeder__name'],
                'district': r['customer__feeder__business_district__name'],
                'total_kwh': float(r['total_kwh'] or 0),
                'total_mwh': round(float(r['total_kwh'] or 0) / 1000, 2),
                'readings': r['reading_count'],
            }
            for r in rows
        ],
    }
