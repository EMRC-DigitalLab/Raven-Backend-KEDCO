from datetime import datetime

from django.db.models import Count, Q, Sum


def _parse(d: str):
    return datetime.strptime(d, '%Y-%m-%d').date()


def query_financial(start_date: str, end_date: str, district: str = None, category: str = None) -> dict:
    """Return financial metrics: OPEX, salary costs, NBET and MO invoices."""
    from financial.models import HQOpex, NBETInvoice, MOInvoice, Opex, SalaryPayment
    from common.models import BusinessDistrict

    start, end = _parse(start_date), _parse(end_date)

    district_obj = None
    if district:
        district_obj = BusinessDistrict.objects.filter(
            Q(slug=district) | Q(name__icontains=district)
        ).first()

    # District OPEX
    opex_filter: dict = {'date__gte': start, 'date__lte': end}
    if district_obj:
        opex_filter['district'] = district_obj
    if category:
        opex_filter['opex_category__name__icontains'] = category

    opex_totals = Opex.objects.filter(**opex_filter).aggregate(
        total_debit=Sum('debit'),
        total_credit=Sum('credit'),
        count=Count('id'),
    )

    # OPEX by category
    opex_by_cat = list(
        Opex.objects.filter(**opex_filter)
        .values('opex_category__name')
        .annotate(total=Sum('credit'))
        .order_by('-total')[:8]
    )

    # HQ OPEX
    hq_filter: dict = {'date__gte': start, 'date__lte': end}
    hq_totals = HQOpex.objects.filter(**hq_filter).aggregate(
        total_debit=Sum('debit'),
        total_credit=Sum('credit'),
    )

    # Salary payments
    sal_filter: dict = {'month__gte': start, 'month__lte': end}
    if district_obj:
        sal_filter['district'] = district_obj
    salary_total = SalaryPayment.objects.filter(**sal_filter).aggregate(
        total=Sum('amount'), headcount=Count('staff', distinct=True)
    )

    # NBET & MO invoices
    nbet = NBETInvoice.objects.filter(month__gte=start, month__lte=end).aggregate(
        total=Sum('amount'), paid=Count('id', filter=Q(is_paid=True)), unpaid=Count('id', filter=Q(is_paid=False))
    )
    mo = MOInvoice.objects.filter(month__gte=start, month__lte=end).aggregate(
        total=Sum('amount'), paid=Count('id', filter=Q(is_paid=True)), unpaid=Count('id', filter=Q(is_paid=False))
    )

    return {
        'period': {'start': start_date, 'end': end_date},
        'scope': {'district': district, 'category_filter': category},
        'district_opex': {
            'total_credit_naira': float(opex_totals['total_credit'] or 0),
            'total_debit_naira': float(opex_totals['total_debit'] or 0),
            'transaction_count': opex_totals['count'],
            'by_category': [
                {'category': r['opex_category__name'], 'amount_naira': float(r['total'] or 0)}
                for r in opex_by_cat
            ],
        },
        'hq_opex': {
            'total_credit_naira': float(hq_totals['total_credit'] or 0),
            'total_debit_naira': float(hq_totals['total_debit'] or 0),
        },
        'salary': {
            'total_paid_naira': float(salary_total['total'] or 0),
            'staff_paid': salary_total['headcount'],
        },
        'nbet_invoices': {
            'total_amount_naira': float(nbet['total'] or 0),
            'paid_count': nbet['paid'],
            'unpaid_count': nbet['unpaid'],
        },
        'mo_invoices': {
            'total_amount_naira': float(mo['total'] or 0),
            'paid_count': mo['paid'],
            'unpaid_count': mo['unpaid'],
        },
    }
