# hr/metrics.py
"""
Shared HR metric calculations — single source of truth.

All functions accept a staff queryset and date params so they work with
any scope (network-wide, per state, per district, per report filter set).
Callers are responsible for scoping the queryset before calling.

Available models:
    Staff           — hire_date, exit_date, salary, gender, grade,
                      department (FK), role (FK), state (FK), district (FK)
    Department      — name, slug
    ExecutiveKPIDefinition  — executive_role, category, name, target_value, …
    ExecutivePerformance    — kpi_definition (FK), period_date, actual_value, …

Usage:
    from hr.metrics import get_active_staff, get_hr_overview, get_wage_bill

    qs = get_active_staff(to_date=to_date, department_ids=[...])
    overview = get_hr_overview(qs, from_date, to_date)
"""

from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from hr.models import Staff


# =============================================================================
# STAFF QUERYSET BUILDER
# =============================================================================

def get_active_staff(to_date=None, department_ids=None, grade_levels=None,
                     district_ids=None, state_ids=None):
    """
    Return a queryset of currently active staff (exit_date is null),
    optionally scoped by filters.

    Args:
        to_date       : date — only include staff hired on or before this date
        department_ids: list of UUIDs
        grade_levels  : list of grade strings (e.g. ['associate', 'senior_manager'])
        district_ids  : list of UUIDs
        state_ids     : list of UUIDs

    Returns: Staff queryset
    """
    qs = Staff.objects.filter(exit_date__isnull=True)

    if to_date:
        qs = qs.filter(hire_date__lte=to_date)
    if department_ids:
        qs = qs.filter(department_id__in=department_ids)
    if grade_levels:
        qs = qs.filter(grade__in=grade_levels)
    if district_ids:
        qs = qs.filter(district_id__in=district_ids)
    if state_ids:
        qs = qs.filter(state_id__in=state_ids)

    return qs


# =============================================================================
# HR OVERVIEW
# =============================================================================

def get_hr_overview(staff_qs, from_date, to_date):
    """
    Core HR overview metrics derived from Staff model.

    Args:
        staff_qs  : active staff queryset (already scoped)
        from_date : date
        to_date   : date

    Returns: dict
    """
    total_staff = staff_qs.count()

    male_count = staff_qs.filter(gender='Male').count()
    female_count = staff_qs.filter(gender='Female').count()
    departments_count = staff_qs.values('department').distinct().count()

    # Attritions — staff who exited within the period
    attrition_count = Staff.objects.filter(
        exit_date__range=(from_date, to_date)
    ).count()
    attrition_rate = round((attrition_count / total_staff * 100), 2) if total_staff > 0 else 0.0

    # New hires in the period
    new_hires = Staff.objects.filter(
        hire_date__range=(from_date, to_date)
    ).count()

    return {
        'total_staff': total_staff,
        'male_count': male_count,
        'female_count': female_count,
        'departments_count': departments_count,
        'attrition_count': attrition_count,
        'attrition_rate': attrition_rate,
        'new_hires': new_hires,
    }


# =============================================================================
# STAFF METRICS
# =============================================================================

def get_staff_metrics(staff_qs):
    """
    Staff composition and tenure metrics.

    Args:
        staff_qs : active staff queryset (already scoped)

    Returns: dict
    """
    total_staff = staff_qs.count()
    today = timezone.now().date()

    # Average tenure in years
    staff_with_hire = list(staff_qs.exclude(hire_date__isnull=True).values_list('hire_date', flat=True))
    if staff_with_hire:
        avg_days = sum((today - h).days for h in staff_with_hire) / len(staff_with_hire)
        avg_tenure_years = round(avg_days / 365.25, 1)
    else:
        avg_tenure_years = 0.0

    # Grade breakdown
    grade_breakdown = list(
        staff_qs.values('grade')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    return {
        'total_staff': total_staff,
        'avg_tenure_years': avg_tenure_years,
        'grade_breakdown': grade_breakdown,
    }


# =============================================================================
# WAGE BILL
# =============================================================================

def get_wage_bill(staff_qs):
    """
    Wage bill totals and department breakdown.

    Args:
        staff_qs : active staff queryset (already scoped)

    Returns: dict
    """
    total_wage_bill = staff_qs.aggregate(total=Sum('salary'))['total'] or Decimal('0')
    total_staff = staff_qs.count()

    dept_rows = (
        staff_qs
        .values('department__name')
        .annotate(total_wages=Sum('salary'), headcount=Count('id'))
        .order_by('-total_wages')
    )

    dept_breakdown = [
        {
            'department': row['department__name'] or 'Unassigned',
            'total_wages': float(row['total_wages'] or 0),
            'headcount': row['headcount'],
            'percentage': round(
                float(row['total_wages'] or 0) / float(total_wage_bill) * 100, 1
            ) if total_wage_bill > 0 else 0.0,
        }
        for row in dept_rows
        if row['total_wages']
    ]

    return {
        'total_wage_bill': float(total_wage_bill),
        'avg_salary': float(total_wage_bill / total_staff) if total_staff > 0 else 0.0,
        'department_breakdown': dept_breakdown,
    }


# =============================================================================
# DEPARTMENT HEADCOUNT
# =============================================================================

def get_department_headcount(staff_qs):
    """
    Headcount per department.

    Args:
        staff_qs : active staff queryset (already scoped)

    Returns: list of dicts ordered by headcount descending
    """
    total_staff = staff_qs.count()

    rows = (
        staff_qs
        .values('department__name')
        .annotate(total=Count('id'))
        .order_by('-total')
    )

    return [
        {
            'department': row['department__name'] or 'Unassigned',
            'total': row['total'],
            'percentage': round(row['total'] / total_staff * 100, 1) if total_staff > 0 else 0.0,
        }
        for row in rows
    ]


# =============================================================================
# ATTRITION ANALYSIS
# =============================================================================

def get_attrition_analysis(from_date, to_date):
    """
    Attrition (staff exits) analysis for the period.

    Args:
        from_date : date
        to_date   : date

    Returns: dict
    """
    exits = Staff.objects.filter(exit_date__range=(from_date, to_date))
    total_exits = exits.count()

    active_at_start = Staff.objects.filter(
        hire_date__lte=from_date,
        exit_date__isnull=True
    ).count()
    attrition_rate = round(total_exits / active_at_start * 100, 2) if active_at_start > 0 else 0.0

    by_department = list(
        exits
        .values('department__name')
        .annotate(exits=Count('id'))
        .order_by('-exits')
    )

    return {
        'total_exits': total_exits,
        'attrition_rate': attrition_rate,
        'by_department': [
            {'department': row['department__name'] or 'Unassigned', 'exits': row['exits']}
            for row in by_department
        ],
    }


# =============================================================================
# RECRUITMENT SUMMARY
# =============================================================================

def get_recruitment_summary(from_date, to_date):
    """
    New hire summary for the period.

    Args:
        from_date : date
        to_date   : date

    Returns: dict
    """
    new_hires = Staff.objects.filter(hire_date__range=(from_date, to_date))
    total_new_hires = new_hires.count()

    by_department = list(
        new_hires
        .values('department__name')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    by_grade = list(
        new_hires
        .values('grade')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    return {
        'total_new_hires': total_new_hires,
        'by_department': [
            {'department': row['department__name'] or 'Unassigned', 'count': row['count']}
            for row in by_department
        ],
        'by_grade': by_grade,
    }
