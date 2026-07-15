from datetime import datetime

from django.db.models import Count, Q, Sum


def _parse(d: str):
    return datetime.strptime(d, '%Y-%m-%d').date()


def query_hr(as_of_date: str = None, district: str = None, state: str = None) -> dict:
    """Return HR metrics: staff headcount, department breakdown, grade distribution, attrition."""
    from hr.models import Staff, Department
    from common.models import BusinessDistrict, State

    cutoff = _parse(as_of_date) if as_of_date else datetime.today().date()

    # Active staff = hired before/on cutoff AND (no exit OR exit after cutoff)
    base_filter = Q(hire_date__lte=cutoff) & (Q(exit_date__isnull=True) | Q(exit_date__gt=cutoff))

    if district:
        obj = BusinessDistrict.objects.filter(Q(slug=district) | Q(name__icontains=district)).first()
        if obj:
            base_filter &= Q(district=obj)

    if state:
        obj = State.objects.filter(Q(slug=state) | Q(name__icontains=state)).first()
        if obj:
            base_filter &= Q(state=obj)

    active_staff = Staff.objects.filter(base_filter)
    total_active = active_staff.count()

    # By department
    by_dept = list(
        active_staff.values('department__name')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    # By grade
    by_grade = list(
        active_staff.values('grade')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    # Gender split
    gender_split = list(
        active_staff.values('gender').annotate(count=Count('id'))
    )

    # Attrition: exited in the period (if as_of_date implies a year, show year-to-date exits)
    from datetime import date
    year_start = date(cutoff.year, 1, 1)
    exits_ytd = Staff.objects.filter(exit_date__gte=year_start, exit_date__lte=cutoff).count()
    avg_active_for_rate = Staff.objects.filter(hire_date__lte=cutoff).count()
    attrition_rate = round(exits_ytd / avg_active_for_rate * 100, 1) if avg_active_for_rate else 0

    # Salary cost
    salary_total = active_staff.aggregate(total=Sum('salary'))

    return {
        'as_of': str(cutoff),
        'scope': {'district': district, 'state': state},
        'headcount': {
            'total_active': total_active,
            'exits_ytd': exits_ytd,
            'attrition_rate_pct': attrition_rate,
        },
        'by_department': [
            {'department': r['department__name'] or 'Unassigned', 'count': r['count']}
            for r in by_dept
        ],
        'by_grade': [
            {'grade': r['grade'] or 'Unassigned', 'count': r['count']}
            for r in by_grade
        ],
        'by_gender': {r['gender']: r['count'] for r in gender_split},
        'estimated_monthly_wage_bill_naira': float(salary_total['total'] or 0),
    }


def query_executive_kpis(executive_role: str = None) -> dict:
    """Return executive KPI definitions and latest performance records."""
    from hr.models import ExecutiveKPIDefinition, ExecutivePerformance

    kpi_filter: dict = {'is_active': True}
    if executive_role:
        kpi_filter['executive_role__iexact'] = executive_role

    kpis = ExecutiveKPIDefinition.objects.filter(**kpi_filter).prefetch_related('performance_records')

    results = []
    for kpi in kpis:
        latest = kpi.performance_records.order_by('-period_date').first()
        results.append({
            'role': kpi.get_executive_role_display(),
            'kpi_name': kpi.name,
            'category': kpi.category,
            'unit': kpi.unit,
            'target': kpi.get_target_display(),
            'priority': kpi.priority,
            'latest_value': float(latest.actual_value) if latest else None,
            'latest_period': str(latest.period_date) if latest else None,
            'status': latest.status if latest else 'no_data',
        })

    return {'executive_kpis': results, 'total': len(results)}
