# hr/views/executive_kpis/overview_views.py
from datetime import date, datetime, timedelta

from django.db.models import Avg, Count, Max, Min, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from ...models import ExecutiveKPIAlert, ExecutiveKPIDefinition, ExecutivePerformance
from ...serializers import (
    ExecutiveKPIDefinitionSerializer,
    ExecutivePerformanceSerializer,
)
from ...utils.kpi_utils import KPICalculator, KPIDataService


@api_view(['GET'])
def executive_kpi_overview(request):
    """
    Get comprehensive KPI overview for all executives or specific role
    Query params:
    - executive_role: CFO, CTO, CCO, CHRO (optional)
    - period: month count for trend data (default: 6)
    """
    executive_role = request.GET.get('executive_role')
    period_months = int(request.GET.get('period', 6))
    
    try:
        # Filter KPIs
        queryset = ExecutiveKPIDefinition.objects.filter(is_active=True)
        if executive_role:
            queryset = queryset.filter(executive_role=executive_role.upper())
        
        # Get summary data
        if executive_role:
            summary_data = KPIDataService.get_executive_kpi_summary(executive_role.upper())
        else:
            # All executives summary - FIXED: Use hardcoded choices instead of model attribute
            summary_data = {}
            executive_roles = [
                ('CFO', 'Chief Financial Officer'),
                ('CTO', 'Chief Technology Officer'), 
                ('CCO', 'Chief Commercial Officer'),
                ('CHRO', 'Chief Human Resources Officer')
            ]
            
            for role_code, role_name in executive_roles:
                try:
                    role_summary = KPIDataService.get_executive_kpi_summary(role_code)
                    summary_data[role_code] = role_summary
                except Exception as e:
                    # If individual role fails, continue with others
                    summary_data[role_code] = {
                        'executive_role': role_code,
                        'total_kpis': 0,
                        'categories': {},
                        'recent_performance': {},
                        'alerts': []
                    }
        
        return Response({
            'success': True,
            'data': summary_data,
            'meta': {
                'period_months': period_months,
                'generated_at': timezone.now().isoformat(),
                'total_kpis': queryset.count()
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e),
            'message': 'Failed to retrieve KPI overview'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def kpi_alerts(request):
    """
    Get KPI alerts for dashboard
    Query params:
    - executive_role: CFO, CTO, CCO, CHRO (optional)
    - severity: low, medium, high, critical (optional)
    - limit: number of alerts (default: 10)
    """
    try:
        executive_role = request.GET.get('executive_role')
        severity = request.GET.get('severity')
        limit = int(request.GET.get('limit', 10))
        
        # Build query
        query = Q(is_active=True, acknowledged=False)
        if executive_role:
            query &= Q(kpi_definition__executive_role=executive_role.upper())
        if severity:
            query &= Q(severity=severity.lower())
        
        alerts = ExecutiveKPIAlert.objects.filter(query).order_by(
            '-severity', '-created_at'
        )[:limit]
        
        alert_data = []
        for alert in alerts:
            alert_data.append({
                'id': str(alert.id),
                'kpi_name': alert.kpi_definition.name,
                'executive_role': alert.kpi_definition.executive_role,
                'type': alert.alert_type,
                'message': alert.message,
                'severity': alert.severity,
                'created_at': alert.created_at.isoformat()
            })
        
        return Response({
            'success': True,
            'data': alert_data,
            'meta': {
                'count': len(alert_data),
                'limit': limit,
                'filters': {
                    'executive_role': executive_role,
                    'severity': severity
                }
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e),
            'message': 'Failed to retrieve KPI alerts'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)