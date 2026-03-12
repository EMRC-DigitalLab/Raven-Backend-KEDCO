# reports/hr_service.py - NEW FILE
"""
HR Data Service for fetching HR report data.
"""
from datetime import date, datetime
from decimal import Decimal

from django.db.models import Avg, Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone

from hr.models import (
    Department,
    ExecutiveKPIDefinition,
    ExecutivePerformance,
    LeaveRequest,
    PerformanceReview,
    Staff,
)


class HRReportDataService:
    """Service for fetching data for HR report sections"""
    
    def __init__(self, filters):
        """
        Initialize with filters.
        
        filters = {
            'from_date': '2025-01-01',
            'to_date': '2025-01-31',
            'departments': [uuid1, uuid2],  # optional
            'employee_types': ['permanent', 'contract'],  # optional
            'grade_levels': ['L1', 'L2'],  # optional
        }
        """
        self.filters = filters
        self.from_date = self._parse_date(filters.get('from_date'))
        self.to_date = self._parse_date(filters.get('to_date'))
        
        # Build staff queryset based on filters
        self.staff_queryset = self._get_filtered_staff()
    
    def _parse_date(self, date_val):
        """Parse date from string or return as-is if already a date"""
        if isinstance(date_val, str):
            if 'T' in date_val:
                return datetime.fromisoformat(date_val.replace('Z', '+00:00')).date()
            return datetime.strptime(date_val, '%Y-%m-%d').date()
        elif isinstance(date_val, datetime):
            return date_val.date()
        return date_val
    
    def _get_filtered_staff(self):
        """Get staff queryset based on filters"""
        queryset = Staff.objects.filter(is_active=True)
        
        # Filter by departments
        if self.filters.get('departments'):
            queryset = queryset.filter(department_id__in=self.filters['departments'])
        
        # Filter by employee types
        if self.filters.get('employee_types'):
            queryset = queryset.filter(employee_type__in=self.filters['employee_types'])
        
        # Filter by grade levels
        if self.filters.get('grade_levels'):
            queryset = queryset.filter(grade_level__in=self.filters['grade_levels'])
        
        return queryset
    
    # =========================================================================
    # HR OVERVIEW
    # =========================================================================
    
    def get_hr_overview(self):
        """Get HR overview metrics"""
        total_staff = self.staff_queryset.count()
        
        # Headcount by type
        permanent = self.staff_queryset.filter(employee_type='permanent').count()
        contract = self.staff_queryset.filter(employee_type='contract').count()
        
        # Gender distribution
        male_count = self.staff_queryset.filter(gender='M').count()
        female_count = self.staff_queryset.filter(gender='F').count()
        
        # Department count
        departments_count = self.staff_queryset.values('department').distinct().count()
        
        # Attrition in period (assuming we have termination_date field)
        attrition_count = Staff.objects.filter(
            is_active=False,
            termination_date__range=(self.from_date, self.to_date)
        ).count()
        
        attrition_rate = (attrition_count / total_staff * 100) if total_staff > 0 else 0
        
        # New hires in period
        new_hires = self.staff_queryset.filter(
            date_joined__range=(self.from_date, self.to_date)
        ).count()
        
        return {
            'total_staff': total_staff,
            'permanent_staff': permanent,
            'contract_staff': contract,
            'male_count': male_count,
            'female_count': female_count,
            'departments_count': departments_count,
            'attrition_count': attrition_count,
            'attrition_rate': round(attrition_rate, 2),
            'new_hires': new_hires,
        }
    
    # =========================================================================
    # STAFF METRICS
    # =========================================================================
    
    def get_staff_metrics(self):
        """Get staff metrics for cards"""
        total_staff = self.staff_queryset.count()
        
        # Average tenure (years)
        today = timezone.now().date()
        staff_with_tenure = self.staff_queryset.exclude(date_joined__isnull=True)
        
        if staff_with_tenure.exists():
            avg_tenure_days = sum(
                [(today - s.date_joined).days for s in staff_with_tenure]
            ) / staff_with_tenure.count()
            avg_tenure_years = avg_tenure_days / 365.25
        else:
            avg_tenure_years = 0
        
        # Staff with active performance reviews
        reviewed_staff = PerformanceReview.objects.filter(
            staff__in=self.staff_queryset,
            review_date__range=(self.from_date, self.to_date),
            status='completed'
        ).values('staff').distinct().count()
        
        review_completion_rate = (reviewed_staff / total_staff * 100) if total_staff > 0 else 0
        
        # Leave utilization
        total_leave_days = LeaveRequest.objects.filter(
            staff__in=self.staff_queryset,
            start_date__lte=self.to_date,
            end_date__gte=self.from_date,
            status='approved'
        ).aggregate(
            total=Sum(
                ExpressionWrapper(
                    F('end_date') - F('start_date'),
                    output_field=DecimalField()
                )
            )
        )['total'] or 0
        
        avg_leave_days = total_leave_days / total_staff if total_staff > 0 else 0
        
        # Training hours (if you have training records)
        total_training_hours = 0  # Implement if you have training model
        
        return {
            'total_staff': total_staff,
            'avg_tenure_years': round(avg_tenure_years, 1),
            'review_completion_rate': round(review_completion_rate, 1),
            'avg_leave_days': round(float(avg_leave_days), 1),
            'training_hours': total_training_hours,
        }
    
    # =========================================================================
    # DEPARTMENT HEADCOUNT
    # =========================================================================
    
    def get_department_headcount(self):
        """Get headcount breakdown by department"""
        departments = Department.objects.all().order_by('name')
        
        result = []
        for dept in departments:
            dept_staff = self.staff_queryset.filter(department=dept)
            
            permanent = dept_staff.filter(employee_type='permanent').count()
            contract = dept_staff.filter(employee_type='contract').count()
            total = dept_staff.count()
            
            if total > 0:  # Only include departments with staff
                result.append({
                    'department': dept.name,
                    'permanent': permanent,
                    'contract': contract,
                    'total': total,
                    'percentage': round((total / self.staff_queryset.count() * 100), 1) if self.staff_queryset.count() > 0 else 0
                })
        
        return sorted(result, key=lambda x: x['total'], reverse=True)
    
    # =========================================================================
    # STAFF PRODUCTIVITY
    # =========================================================================
    
    def get_staff_productivity(self):
        """Get staff productivity metrics"""
        # This would ideally pull from your productivity tracking system
        # For now, returning structure based on Board KPI metrics
        
        total_staff = self.staff_queryset.count()
        
        # Calculate revenue per employee (if you have revenue data)
        # This is a placeholder - integrate with your financial data
        total_revenue = 0  # Get from financial module
        revenue_per_employee = total_revenue / total_staff if total_staff > 0 else 0
        
        # Performance ratings distribution
        excellent = PerformanceReview.objects.filter(
            staff__in=self.staff_queryset,
            review_date__range=(self.from_date, self.to_date),
            overall_rating__gte=4.5
        ).count()
        
        good = PerformanceReview.objects.filter(
            staff__in=self.staff_queryset,
            review_date__range=(self.from_date, self.to_date),
            overall_rating__gte=3.5,
            overall_rating__lt=4.5
        ).count()
        
        needs_improvement = PerformanceReview.objects.filter(
            staff__in=self.staff_queryset,
            review_date__range=(self.from_date, self.to_date),
            overall_rating__lt=3.5
        ).count()
        
        return {
            'revenue_per_employee': round(revenue_per_employee, 2),
            'total_staff': total_staff,
            'performance_distribution': {
                'excellent': excellent,
                'good': good,
                'needs_improvement': needs_improvement,
            }
        }
    
    # =========================================================================
    # WAGE BILL ANALYSIS
    # =========================================================================
    
    def get_wage_bill_analysis(self):
        """Get wage bill analysis"""
        # Calculate total wage bill
        total_wage_bill = self.staff_queryset.aggregate(
            total=Sum('salary')
        )['total'] or Decimal('0')
        
        # Wage bill by department
        dept_breakdown = []
        for dept in Department.objects.all():
            dept_wages = self.staff_queryset.filter(department=dept).aggregate(
                total=Sum('salary')
            )['total'] or Decimal('0')
            
            if dept_wages > 0:
                dept_breakdown.append({
                    'department': dept.name,
                    'total_wages': float(dept_wages),
                    'percentage': round((float(dept_wages) / float(total_wage_bill) * 100), 1) if total_wage_bill > 0 else 0
                })
        
        # Wage bill by employee type
        permanent_wages = self.staff_queryset.filter(
            employee_type='permanent'
        ).aggregate(total=Sum('salary'))['total'] or Decimal('0')
        
        contract_wages = self.staff_queryset.filter(
            employee_type='contract'
        ).aggregate(total=Sum('salary'))['total'] or Decimal('0')
        
        return {
            'total_wage_bill': float(total_wage_bill),
            'permanent_wages': float(permanent_wages),
            'contract_wages': float(contract_wages),
            'department_breakdown': sorted(dept_breakdown, key=lambda x: x['total_wages'], reverse=True),
            'avg_salary': float(total_wage_bill / self.staff_queryset.count()) if self.staff_queryset.count() > 0 else 0
        }
    
    # =========================================================================
    # ATTRITION ANALYSIS
    # =========================================================================
    
    def get_attrition_analysis(self):
        """Get attrition analysis"""
        # Get terminations in period
        terminations = Staff.objects.filter(
            is_active=False,
            termination_date__range=(self.from_date, self.to_date)
        )
        
        total_terminations = terminations.count()
        
        # By reason
        by_reason = terminations.values('termination_reason').annotate(
            count=Count('id')
        ).order_by('-count')
        
        # By department
        by_department = []
        for dept in Department.objects.all():
            dept_terminations = terminations.filter(department=dept).count()
            if dept_terminations > 0:
                by_department.append({
                    'department': dept.name,
                    'terminations': dept_terminations
                })
        
        # Calculate attrition rate
        total_staff = self.staff_queryset.count()
        attrition_rate = (total_terminations / total_staff * 100) if total_staff > 0 else 0
        
        return {
            'total_terminations': total_terminations,
            'attrition_rate': round(attrition_rate, 2),
            'by_reason': list(by_reason),
            'by_department': sorted(by_department, key=lambda x: x['terminations'], reverse=True),
        }
    
    # =========================================================================
    # RECRUITMENT SUMMARY
    # =========================================================================
    
    def get_recruitment_summary(self):
        """Get recruitment summary"""
        # New hires in period
        new_hires = self.staff_queryset.filter(
            date_joined__range=(self.from_date, self.to_date)
        )
        
        total_new_hires = new_hires.count()
        
        # By department
        by_department = []
        for dept in Department.objects.all():
            dept_hires = new_hires.filter(department=dept).count()
            if dept_hires > 0:
                by_department.append({
                    'department': dept.name,
                    'new_hires': dept_hires
                })
        
        # By employee type
        permanent_hires = new_hires.filter(employee_type='permanent').count()
        contract_hires = new_hires.filter(employee_type='contract').count()
        
        return {
            'total_new_hires': total_new_hires,
            'permanent_hires': permanent_hires,
            'contract_hires': contract_hires,
            'by_department': sorted(by_department, key=lambda x: x['new_hires'], reverse=True),
        }
    
    # =========================================================================
    # TRAINING SUMMARY
    # =========================================================================
    
    def get_training_summary(self):
        """Get training and development summary"""
        # This is a placeholder - implement when you have training tracking
        return {
            'total_training_hours': 0,
            'staff_trained': 0,
            'training_completion_rate': 0,
            'by_department': [],
        }
    
    # =========================================================================
    # PERFORMANCE APPRAISALS
    # =========================================================================
    
    def get_performance_appraisals(self):
        """Get performance appraisals summary"""
        # Reviews in period
        reviews = PerformanceReview.objects.filter(
            staff__in=self.staff_queryset,
            review_date__range=(self.from_date, self.to_date)
        )
        
        total_reviews = reviews.count()
        completed_reviews = reviews.filter(status='completed').count()
        
        # Calculate completion rate
        expected_reviews = self.staff_queryset.count()
        completion_rate = (completed_reviews / expected_reviews * 100) if expected_reviews > 0 else 0
        
        # Average rating
        avg_rating = reviews.filter(
            status='completed'
        ).aggregate(avg=Avg('overall_rating'))['avg'] or 0
        
        # Rating distribution
        rating_distribution = {
            'excellent': reviews.filter(overall_rating__gte=4.5).count(),
            'good': reviews.filter(overall_rating__gte=3.5, overall_rating__lt=4.5).count(),
            'satisfactory': reviews.filter(overall_rating__gte=2.5, overall_rating__lt=3.5).count(),
            'needs_improvement': reviews.filter(overall_rating__lt=2.5).count(),
        }
        
        return {
            'total_reviews': total_reviews,
            'completed_reviews': completed_reviews,
            'completion_rate': round(completion_rate, 1),
            'avg_rating': round(float(avg_rating), 2),
            'rating_distribution': rating_distribution,
        }
    
    # =========================================================================
    # MASTER DATA GETTER
    # =========================================================================
    
    def get_all_section_data(self, section_type, config=None):
        """Get data for a specific HR section type"""
        config = config or {}
        
        data_methods = {
            'hr_overview': self.get_hr_overview,
            'staff_metrics': self.get_staff_metrics,
            'department_headcount': self.get_department_headcount,
            'staff_productivity': self.get_staff_productivity,
            'wage_bill_analysis': self.get_wage_bill_analysis,
            'attrition_analysis': self.get_attrition_analysis,
            'recruitment_summary': self.get_recruitment_summary,
            'training_summary': self.get_training_summary,
            'performance_appraisals': self.get_performance_appraisals,
        }
        
        method = data_methods.get(section_type)
        if method:
            return method()
        
        return {}