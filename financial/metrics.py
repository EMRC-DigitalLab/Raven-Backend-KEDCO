# financial/metrics.py
"""
Shared financial metric calculations — single source of truth.

All functions accept explicit filter params so they work with any scope
(network-wide, per state, per district, per report filter set, etc.).
Callers are responsible for scoping before calling.

Available models:
    Opex            — district (FK), date, debit, credit, opex_category (FK)
    HQOpex          — date, debit, credit, opex_category (FK)
    SalaryPayment   — district (FK), month, staff (FK), payment_date, amount
    NBETInvoice     — month, amount, is_paid
    MOInvoice       — month, amount, is_paid

Usage:
    from financial.metrics import get_financial_overview, get_opex_by_category
"""

from decimal import Decimal

from django.db.models import Count, Sum

from financial.models import HQOpex, MOInvoice, NBETInvoice, Opex, SalaryPayment


# =============================================================================
# INDIVIDUAL COST COMPONENTS
# =============================================================================

def get_opex_total(from_date, to_date, district_ids=None):
    """
    Total district OPEX (credit = actual spend) for the period.

    Args:
        from_date    : date
        to_date      : date
        district_ids : list of BusinessDistrict UUIDs (None = all districts)

    Returns: Decimal
    """
    qs = Opex.objects.filter(date__range=(from_date, to_date))
    if district_ids:
        qs = qs.filter(district_id__in=district_ids)
    return qs.aggregate(total=Sum('credit'))['total'] or Decimal('0')


def get_hq_opex_total(from_date, to_date):
    """
    Total HQ OPEX (credit = actual spend) for the period.

    Args:
        from_date : date
        to_date   : date

    Returns: Decimal
    """
    return (
        HQOpex.objects
        .filter(date__range=(from_date, to_date))
        .aggregate(total=Sum('credit'))['total'] or Decimal('0')
    )


def get_salary_total(from_date, to_date, district_ids=None):
    """
    Total salary payments for the period.

    Args:
        from_date    : date
        to_date      : date
        district_ids : list of BusinessDistrict UUIDs (None = all districts)

    Returns: Decimal
    """
    qs = SalaryPayment.objects.filter(payment_date__range=(from_date, to_date))
    if district_ids:
        qs = qs.filter(district_id__in=district_ids)
    return qs.aggregate(total=Sum('amount'))['total'] or Decimal('0')


def get_nbet_total(from_date, to_date):
    """
    Total NBET invoice amounts for months overlapping the period.

    Args:
        from_date : date
        to_date   : date

    Returns: Decimal
    """
    return (
        NBETInvoice.objects
        .filter(month__range=(from_date, to_date))
        .aggregate(total=Sum('amount'))['total'] or Decimal('0')
    )


def get_mo_total(from_date, to_date):
    """
    Total Market Operator invoice amounts for months overlapping the period.

    Args:
        from_date : date
        to_date   : date

    Returns: Decimal
    """
    return (
        MOInvoice.objects
        .filter(month__range=(from_date, to_date))
        .aggregate(total=Sum('amount'))['total'] or Decimal('0')
    )


# =============================================================================
# FINANCIAL OVERVIEW
# =============================================================================

def get_financial_overview(from_date, to_date, district_ids=None):
    """
    Aggregated financial cost overview for the period.

    Args:
        from_date    : date
        to_date      : date
        district_ids : list of BusinessDistrict UUIDs (None = all)

    Returns: dict
    """
    opex        = get_opex_total(from_date, to_date, district_ids)
    hq_opex     = get_hq_opex_total(from_date, to_date)
    salaries    = get_salary_total(from_date, to_date, district_ids)
    nbet        = get_nbet_total(from_date, to_date)
    mo          = get_mo_total(from_date, to_date)
    total_cost  = opex + hq_opex + salaries + nbet + mo

    return {
        'opex':         float(opex),
        'hq_opex':      float(hq_opex),
        'salaries':     float(salaries),
        'nbet_invoice': float(nbet),
        'mo_invoice':   float(mo),
        'total_cost':   float(total_cost),
    }


# =============================================================================
# OPEX BREAKDOWN BY CATEGORY
# =============================================================================

def get_opex_by_category(from_date, to_date, district_ids=None):
    """
    District OPEX broken down by category for the period.

    Args:
        from_date    : date
        to_date      : date
        district_ids : list of BusinessDistrict UUIDs (None = all)

    Returns: list of dicts ordered by total descending
    """
    qs = Opex.objects.filter(date__range=(from_date, to_date))
    if district_ids:
        qs = qs.filter(district_id__in=district_ids)

    total = qs.aggregate(grand_total=Sum('credit'))['grand_total'] or Decimal('0')

    rows = (
        qs
        .values('opex_category__name')
        .annotate(total=Sum('credit'), count=Count('id'))
        .order_by('-total')
    )

    return [
        {
            'category':   row['opex_category__name'] or 'Uncategorised',
            'total':      float(row['total'] or 0),
            'count':      row['count'],
            'percentage': round(
                float(row['total'] or 0) / float(total) * 100, 1
            ) if total > 0 else 0.0,
        }
        for row in rows
    ]


# =============================================================================
# OPEX BREAKDOWN BY DISTRICT
# =============================================================================

def get_opex_by_district(from_date, to_date, district_ids=None):
    """
    District OPEX broken down per business district for the period.

    Args:
        from_date    : date
        to_date      : date
        district_ids : list of BusinessDistrict UUIDs (None = all)

    Returns: list of dicts ordered by total descending
    """
    qs = Opex.objects.filter(date__range=(from_date, to_date))
    if district_ids:
        qs = qs.filter(district_id__in=district_ids)

    total = qs.aggregate(grand_total=Sum('credit'))['grand_total'] or Decimal('0')

    rows = (
        qs
        .values('district__name')
        .annotate(total=Sum('credit'), count=Count('id'))
        .order_by('-total')
    )

    return [
        {
            'district':   row['district__name'] or 'Unassigned',
            'total':      float(row['total'] or 0),
            'count':      row['count'],
            'percentage': round(
                float(row['total'] or 0) / float(total) * 100, 1
            ) if total > 0 else 0.0,
        }
        for row in rows
    ]
