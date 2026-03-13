# hr/utils/kpi_utils.py
import calendar
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional, Tuple, Union

from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.utils import timezone

from ..models import ExecutiveKPIAlert, ExecutiveKPIDefinition, ExecutivePerformance


class KPICalculator:
    """Utility class for KPI calculations and status determination"""
    
    @staticmethod
    def format_currency(amount: Union[Decimal, float, int], scale: str = 'auto') -> str:
        """Format currency values with appropriate scaling"""
        if amount is None or amount == 0:
            return "₦0"
        
        amount = Decimal(str(amount))
        
        if scale == 'auto':
            if amount >= 1_000_000_000:
                return f"₦{(amount / 1_000_000_000).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}B"
            elif amount >= 1_000_000:
                return f"₦{(amount / 1_000_000).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}M"
            elif amount >= 1_000:
                return f"₦{(amount / 1_000).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}K"
            else:
                return f"₦{amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}"
        
        # Manual scale specification
        scale_divisors = {
            'B': 1_000_000_000,
            'M': 1_000_000,
            'K': 1_000,
            'raw': 1
        }
        
        divisor = scale_divisors.get(scale, 1)
        scaled_amount = (amount / divisor).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
        return f"₦{scaled_amount}{scale if scale != 'raw' else ''}"
    
    @staticmethod
    def calculate_progress_percentage(
        current_value: Union[Decimal, float], 
        kpi_definition: ExecutiveKPIDefinition
    ) -> float:
        """Calculate progress percentage for a KPI"""
        return float(kpi_definition.calculate_progress(current_value))
    
    @staticmethod
    def get_status_info(
        current_value: Union[Decimal, float], 
        kpi_definition: ExecutiveKPIDefinition
    ) -> Dict[str, Union[str, float]]:
        """Get comprehensive status information for a KPI"""
        progress = KPICalculator.calculate_progress_percentage(current_value, kpi_definition)
        status = kpi_definition.get_status(current_value)
        
        # Status color mapping
        status_colors = {
            'on_track': '#28a745',      # Green
            'at_risk': '#ffc107',       # Orange
            'off_track': '#dc3545',     # Red
            'not_started': '#6c757d',   # Gray
            'on_target': '#28a745',     # Green
            'below_target': '#ffc107',  # Orange
            'above_target': '#17a2b8'   # Blue
        }
        
        # Status priority for sorting
        status_priority = {
            'off_track': 1,
            'at_risk': 2,
            'below_target': 3,
            'not_started': 4,
            'above_target': 5,
            'on_target': 6,
            'on_track': 6
        }
        
        return {
            'progress': progress,
            'status': status,
            'status_display': status.replace('_', ' ').title(),
            'color': status_colors.get(status, '#6c757d'),
            'priority': status_priority.get(status, 5)
        }
    
    @staticmethod
    def format_kpi_value(
        value: Union[Decimal, float], 
        kpi_definition: ExecutiveKPIDefinition
    ) -> str:
        """Format KPI value for display"""
        if value is None:
            return "--"
        
        if kpi_definition.data_type == 'currency':
            if kpi_definition.unit == 'M':
                return f"₦{value}M"
            else:
                return KPICalculator.format_currency(value)
        elif kpi_definition.data_type == 'percentage':
            return f"{value}%"
        elif kpi_definition.data_type == 'decimal':
            return f"{value}{kpi_definition.unit}"
        else:
            return f"{value}{kpi_definition.unit}"


class KPIDataService:
    """Service class for KPI data operations"""
    
    @staticmethod
    def get_executive_kpi_summary(executive_role: str) -> Dict:
        """Get comprehensive KPI summary for an executive"""
        kpis = ExecutiveKPIDefinition.objects.filter(
            executive_role=executive_role,
            is_active=True
        ).order_by('category', 'priority')
        
        # Role name mapping
        role_names = {
            'CFO': 'Chief Financial Officer',
            'CTO': 'Chief Technology Officer',
            'CCO': 'Chief Commercial Officer',
            'CHRO': 'Chief Human Resources Officer'
        }
        
        summary = {
            'executive_role': executive_role,
            'role_name': role_names.get(executive_role, executive_role),
            'total_kpis': kpis.count(),
            'categories': {},
            'recent_performance': {},
            'alerts': []
        }
        
        # Category name mapping
        category_names = {
            'financial': 'Financial Excellence',
            'technical': 'Technical Operations',
            'commercial': 'Commercial Performance',
            'hr': 'Human Resources'
        }
        
        # Group by category
        for kpi in kpis:
            category = kpi.category
            if category not in summary['categories']:
                summary['categories'][category] = {
                    'name': category_names.get(category, category.title()),
                    'kpis': [],
                    'count': 0
                }
            
            # Get latest performance
            latest_performance = ExecutivePerformance.objects.filter(
                kpi_definition=kpi
            ).order_by('-period_date').first()
            
            kpi_data = {
                'id': str(kpi.id),
                'name': kpi.name,
                'description': kpi.description,
                'priority': kpi.priority,
                'target': kpi.get_target_display(),
                'unit': kpi.unit,
                'deadline': kpi.deadline,
                'current_value': None,
                'status_info': None,
                'last_updated': None
            }
            
            if latest_performance:
                kpi_data['current_value'] = float(latest_performance.actual_value)
                kpi_data['status_info'] = KPICalculator.get_status_info(
                    latest_performance.actual_value, kpi
                )
                kpi_data['last_updated'] = latest_performance.period_date.isoformat()
            
            summary['categories'][category]['kpis'].append(kpi_data)
            summary['categories'][category]['count'] += 1
        
        # Get recent alerts
        alerts = ExecutiveKPIAlert.objects.filter(
            kpi_definition__executive_role=executive_role,
            is_active=True
        ).order_by('-created_at')[:5]
        
        summary['alerts'] = [
            {
                'id': str(alert.id),
                'kpi_name': alert.kpi_definition.name,
                'type': alert.alert_type,
                'message': alert.message,
                'severity': alert.severity,
                'created_at': alert.created_at.isoformat()
            }
            for alert in alerts
        ]
        
        return summary
    
    @staticmethod
    def get_kpi_monthly_trend(
        kpi_definition: ExecutiveKPIDefinition, 
        months: int = 6
    ) -> List[Dict]:
        """Get monthly trend data for a KPI"""
        end_date = date.today().replace(day=1)  # First day of current month
        start_date = end_date - timedelta(days=months * 32)  # Approximate months back
        start_date = start_date.replace(day=1)  # First day of start month
        
        performances = ExecutivePerformance.objects.filter(
            kpi_definition=kpi_definition,
            period_type='monthly',
            period_date__gte=start_date,
            period_date__lte=end_date
        ).order_by('period_date')
        
        # Create monthly data points
        trend_data = []
        current_date = start_date
        
        while current_date <= end_date:
            performance = performances.filter(period_date=current_date).first()
            
            data_point = {
                'month': current_date.strftime('%Y-%m'),
                'month_name': current_date.strftime('%b %Y'),
                'value': float(performance.actual_value) if performance else None,
                'target': float(kpi_definition.target_value) if not kpi_definition.is_range_target else None,
                'target_min': float(kpi_definition.target_min) if kpi_definition.is_range_target else None,
                'target_max': float(kpi_definition.target_max) if kpi_definition.is_range_target else None,
                'status': performance.status if performance else 'not_started'
            }
            
            trend_data.append(data_point)
            
            # Move to next month
            if current_date.month == 12:
                current_date = current_date.replace(year=current_date.year + 1, month=1)
            else:
                current_date = current_date.replace(month=current_date.month + 1)
        
        return trend_data
    
    @staticmethod
    def create_performance_record(
        kpi_definition: ExecutiveKPIDefinition,
        actual_value: Union[Decimal, float],
        period_date: date,
        period_type: str = 'monthly',
        **kwargs
    ) -> ExecutivePerformance:
        """Create or update a performance record"""
        defaults = {
            'actual_value': Decimal(str(actual_value)),
            **kwargs
        }
        
        performance, created = ExecutivePerformance.objects.update_or_create(
            kpi_definition=kpi_definition,
            period_date=period_date,
            period_type=period_type,
            state=kwargs.get('state'),
            business_district=kwargs.get('business_district'),
            defaults=defaults
        )
        
        # Check if alert needs to be created
        KPIAlertManager.check_and_create_alerts(performance)
        
        return performance


class KPIAlertManager:
    """Manages KPI alerts and notifications"""
    
    @staticmethod
    def check_and_create_alerts(performance: ExecutivePerformance) -> List[ExecutiveKPIAlert]:
        """Check performance and create alerts if needed"""
        alerts_created = []
        kpi = performance.kpi_definition
        status = performance.status
        
        # Define alert rules
        alert_rules = {
            'off_track': ('high', 'KPI is significantly off track'),
            'at_risk': ('medium', 'KPI performance is at risk'),
            'target_missed': ('critical', 'Target deadline missed'),
        }
        
        # Check if we should create an alert
        if status in alert_rules:
            severity, base_message = alert_rules[status]
            
            # Check if similar alert already exists
            existing_alert = ExecutiveKPIAlert.objects.filter(
                kpi_definition=kpi,
                alert_type=status,
                is_active=True,
                acknowledged=False
            ).first()
            
            if not existing_alert:
                message = f"{base_message}. Current: {performance.actual_value}{kpi.unit}, Target: {kpi.get_target_display()}"
                
                alert = ExecutiveKPIAlert.objects.create(
                    kpi_definition=kpi,
                    alert_type=status,
                    message=message,
                    severity=severity
                )
                alerts_created.append(alert)
        
        return alerts_created
    
    @staticmethod
    def get_dashboard_alerts(executive_role: Optional[str] = None) -> List[Dict]:
        """Get alerts for dashboard display"""
        query = Q(is_active=True, acknowledged=False)
        if executive_role:
            query &= Q(kpi_definition__executive_role=executive_role)
        
        alerts = ExecutiveKPIAlert.objects.filter(query).order_by(
            '-severity', '-created_at'
        )[:10]  # Limit to 10 most recent/critical
        
        return [
            {
                'id': str(alert.id),
                'kpi_name': alert.kpi_definition.name,
                'executive_role': alert.kpi_definition.executive_role,
                'type': alert.alert_type,
                'message': alert.message,
                'severity': alert.severity,
                'created_at': alert.created_at.isoformat()
            }
            for alert in alerts
        ]


# Utility functions for API responses
def format_kpi_for_frontend(kpi_definition: ExecutiveKPIDefinition, performance: Optional[ExecutivePerformance] = None) -> Dict:
    """Format KPI data for frontend consumption"""
    data = {
        'id': str(kpi_definition.id),
        'name': kpi_definition.name,
        'description': kpi_definition.description,
        'category': kpi_definition.category,
        'priority': kpi_definition.priority,
        'unit': kpi_definition.unit,
        'deadline': kpi_definition.deadline,
        'target': {
            'value': float(kpi_definition.target_value) if kpi_definition.target_value else None,
            'min': float(kpi_definition.target_min) if kpi_definition.target_min else None,
            'max': float(kpi_definition.target_max) if kpi_definition.target_max else None,
            'is_range': kpi_definition.is_range_target,
            'display': kpi_definition.get_target_display()
        },
        'current': {
            'value': 0,
            'progress': 0,
            'status': 'not_started',
            'last_updated': None
        }
    }
    
    if performance:
        status_info = KPICalculator.get_status_info(performance.actual_value, kpi_definition)
        data['current'] = {
            'value': float(performance.actual_value),
            'progress': status_info['progress'],
            'status': status_info['status'],
            'status_display': status_info['status_display'],
            'color': status_info['color'],
            'last_updated': performance.period_date.isoformat()
        }
    
    return data