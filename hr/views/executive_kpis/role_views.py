# hr/views/executive_kpis/role_views.py - WITH DATE FILTERING
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db.models import Q
from django.utils import timezone
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from decimal import Decimal

from ...models import ExecutiveKPIDefinition, ExecutivePerformance
from ...utils.kpi_utils import KPICalculator


def parse_date_params(request):
    """
    Parse date parameters from request
    Returns (period_date, mode) tuple
    """
    mode = request.GET.get('mode', 'monthly')
    
    if mode == 'monthly':
        year = request.GET.get('year')
        month = request.GET.get('month')
        
        if year and month:
            # Parse year-month format
            try:
                period_date = date(int(year), int(month), 1)
            except (ValueError, TypeError):
                period_date = date.today().replace(day=1)
        else:
            period_date = date.today().replace(day=1)
    else:
        # Range mode - use the to_date as reference
        to_date_str = request.GET.get('to_date')
        if to_date_str:
            try:
                period_date = date.fromisoformat(to_date_str)
                period_date = period_date.replace(day=1)
            except (ValueError, TypeError):
                period_date = date.today().replace(day=1)
        else:
            period_date = date.today().replace(day=1)
    
    return period_date, mode


def get_performance_for_period(kpi, period_date, mode='monthly'):
    """
    Get performance data for a specific period
    """
    if mode == 'monthly':
        # Get performance for exact month
        performance = ExecutivePerformance.objects.filter(
            kpi_definition=kpi,
            period_date=period_date,
            period_type='monthly'
        ).first()
        
        return performance
    else:
        # For range mode, get the most recent performance up to period_date
        performance = ExecutivePerformance.objects.filter(
            kpi_definition=kpi,
            period_date__lte=period_date,
            period_type='monthly'
        ).order_by('-period_date').first()
        
        return performance


def get_monthly_trend_data(kpi, period_date, months_back=4):
    """
    Get trend data for the last N months before period_date
    """
    monthly_data = []
    
    for i in range(months_back, 0, -1):
        month_date = period_date - relativedelta(months=i)
        
        performance = ExecutivePerformance.objects.filter(
            kpi_definition=kpi,
            period_date=month_date,
            period_type='monthly'
        ).first()
        
        monthly_data.append({
            'month': month_date.strftime('%b'),
            'value': float(performance.actual_value) if performance else 0
        })
    
    return monthly_data


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cto_kpis(request):
    """
    Get CTO KPI data formatted for frontend CTO-Target component
    Supports date filtering via query params:
    - mode: 'monthly' or 'range'
    - year, month: for monthly mode
    - from_date, to_date: for range mode
    """
    try:
        # Parse date parameters
        period_date, mode = parse_date_params(request)
        
        # Get CTO KPIs
        kpis = ExecutiveKPIDefinition.objects.filter(
            executive_role='CTO',
            is_active=True
        ).order_by('priority', 'name')
        
        kpi_data = {}
        
        for kpi in kpis:
            # Get performance for the selected period
            latest_performance = get_performance_for_period(kpi, period_date, mode)
            
            # Get monthly trend data (4 months before selected period)
            monthly_data = get_monthly_trend_data(kpi, period_date, months_back=4)
            
            # Map to frontend structure
            key = kpi.name.lower().replace(' ', '_').replace('-', '_')
            if 'feeder' in kpi.name.lower() and 'upgrade' in kpi.name.lower():
                key = 'feedersUpgrade'
            elif 'grid' in kpi.name.lower() and 'offtake' in kpi.name.lower():
                key = 'gridOfftake'
            elif 'energy delivered' in kpi.name.lower():
                key = 'energyDelivery'
            elif 'sla compliance' in kpi.name.lower():
                key = 'slaCompliance'
            elif 'igr' in kpi.name.lower():
                key = 'monthlyIGR'
            
            # Format target based on KPI type
            if kpi.is_range_target:
                target = {
                    'min': float(kpi.target_min),
                    'max': float(kpi.target_max)
                }
            else:
                target = float(kpi.target_value) if kpi.target_value else 0
            
            kpi_data[key] = {
                'current': float(latest_performance.actual_value) if latest_performance else 0,
                'target': target,

                'status': latest_performance.status if latest_performance else 'not_started',
                'progress': float(latest_performance.progress_percentage) if latest_performance else 0,
                'description': kpi.description,

                'description': kpi.description,
                'unit': kpi.unit,
                'priority': kpi.priority,
                'deadline': kpi.deadline,
                'monthlyData': monthly_data,
                'period_date': period_date.isoformat()
            }
        
        return Response({
            'success': True,
            'data': kpi_data,
            'meta': {
                'executive_role': 'CTO',
                'period_date': period_date.isoformat(),
                'mode': mode,
                'last_updated': timezone.now().isoformat(),
                'kpi_count': len(kpi_data)
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e),
            'message': 'Failed to retrieve CTO KPIs'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cco_kpis(request):
    """
    Get CCO KPI data with date filtering support
    """
    try:
        # Parse date parameters
        period_date, mode = parse_date_params(request)
        
        # Get CCO KPIs grouped by category
        kpis = ExecutiveKPIDefinition.objects.filter(
            executive_role='CCO',
            is_active=True
        ).order_by('priority', 'name')
        
        # Group KPIs by functional area
        billing_efficiency_kpis = kpis.filter(
            Q(name__icontains='billing efficiency') | 
            Q(name__icontains='smart meters') | 
            Q(name__icontains='meters acquired')
        )
        
        collection_efficiency_kpis = kpis.filter(
            name__icontains='collection efficiency'
        )
        
        band_a_growth_kpis = kpis.filter(
            Q(name__icontains='feeders commercially') |
            Q(name__icontains='customers in billing') |
            Q(name__icontains='ppm collected') |
            Q(name__icontains='mamuda') |
            Q(name__icontains='attrition') |
            Q(name__icontains='new md customers')
        )
        
        igr_kpis = kpis.filter(name__icontains='internally generated revenue')
        
        def format_kpi_group(kpi_queryset, group_title):
            group_data = {'title': group_title, 'kpis': {}}
            
            for kpi in kpi_queryset:
                # Get performance for selected period
                latest_performance = get_performance_for_period(kpi, period_date, mode)
                
                # Create key from KPI name
                key = kpi.name.lower().replace(' ', '_').replace('-', '_')
                if 'md industrial' in kpi.name.lower() and 'billing' in kpi.name.lower():
                    key = 'mdIndustrial'
                elif 'md non-industrial' in kpi.name.lower() and 'billing' in kpi.name.lower():
                    key = 'mdNonIndustrial'
                elif 'regions' in kpi.name.lower() and 'billing' in kpi.name.lower():
                    key = 'regions'
                elif 'smart meters' in kpi.name.lower():
                    key = 'amiStreaming'
                elif 'meters acquired' in kpi.name.lower():
                    key = 'metersAcquired'
                elif 'md industrial' in kpi.name.lower() and 'collection' in kpi.name.lower():
                    key = 'mdIndustrial'
                elif 'md non-industrial' in kpi.name.lower() and 'collection' in kpi.name.lower():
                    key = 'mdNonIndustrial'
                elif 'regions' in kpi.name.lower() and 'collection' in kpi.name.lower():
                    key = 'regions'
                elif 'feeders commercially' in kpi.name.lower():
                    key = 'feedersCommerciallyReady'
                elif 'customers in billing' in kpi.name.lower():
                    key = 'customersBillingSystem'
                elif 'ppm' in kpi.name.lower():
                    key = 'ppmRevenue'
                elif 'mamuda' in kpi.name.lower():
                    key = 'mamudaOfftake'
                elif 'attrition' in kpi.name.lower():
                    key = 'customerRetention'
                elif 'new md customers' in kpi.name.lower():
                    key = 'newCustomersValue'
                elif 'igr' in kpi.name.lower():
                    key = 'monthlyIGR'
                
                group_data['kpis'][key] = {
                    'current': float(latest_performance.actual_value) if latest_performance else 0,
                    'target': float(kpi.target_value) if kpi.target_value else 0,
                    'description': kpi.description,
                    'unit': kpi.unit,
                    'priority': kpi.priority,
                    'deadline': kpi.deadline,
                    'period_date': period_date.isoformat()
                }
            
            return group_data
        
        kpi_data = {
            'billingEfficiency': format_kpi_group(billing_efficiency_kpis, 'Billing Efficiency'),
            'collectionEfficiency': format_kpi_group(collection_efficiency_kpis, 'Collection Efficiency'),
            'bandAGrowth': format_kpi_group(band_a_growth_kpis, 'Band A Growth & Customer Expansion'),
            'igrGeneration': format_kpi_group(igr_kpis, 'Revenue Generation')
        }
        
        return Response({
            'success': True,
            'data': kpi_data,
            'meta': {
                'executive_role': 'CCO',
                'period_date': period_date.isoformat(),
                'mode': mode,
                'last_updated': timezone.now().isoformat(),
                'total_kpis': kpis.count()
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e),
            'message': 'Failed to retrieve CCO KPIs'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cfo_kpis(request):
    """
    Get CFO KPI data with date filtering support
    """
    try:
        # Parse date parameters
        period_date, mode = parse_date_params(request)
        
        # Get CFO KPIs
        kpis = ExecutiveKPIDefinition.objects.filter(
            executive_role='CFO',
            is_active=True
        ).order_by('priority', 'name')
        
        kpi_data = {
            'financialExcellence': {
                'title': 'Financial Excellence & Cost Optimization',
                'kpis': {}
            }
        }
        
        for kpi in kpis:
            # Get performance for selected period
            latest_performance = get_performance_for_period(kpi, period_date, mode)
            
            # Create key from KPI name
            key = kpi.name.lower().replace(' ', '_').replace('-', '_')
            if 'cost-to-revenue' in kpi.name.lower():
                key = 'costToRevenueRatio'
            elif 'admin' in kpi.name.lower() and 'budget' in kpi.name.lower():
                key = 'adminExpensesBudget'
            elif 'igr' in kpi.name.lower():
                key = 'monthlyIGR'
            
            # Handle range targets
            if kpi.is_range_target:
                target = {
                    'min': float(kpi.target_min),
                    'max': float(kpi.target_max)
                }
            else:
                target = float(kpi.target_value) if kpi.target_value else 0
            
            kpi_data['financialExcellence']['kpis'][key] = {
                'current': float(latest_performance.actual_value) if latest_performance else 0,
                'target': target,
                'description': kpi.description,
                'unit': kpi.unit,
                'priority': kpi.priority,
                'deadline': kpi.deadline,
                'period_date': period_date.isoformat(),
                'note': getattr(kpi, 'note', None) if hasattr(kpi, 'note') else None
            }
        
        return Response({
            'success': True,
            'data': kpi_data,
            'meta': {
                'executive_role': 'CFO',
                'period_date': period_date.isoformat(),
                'mode': mode,
                'last_updated': timezone.now().isoformat(),
                'kpi_count': kpis.count()
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e),
            'message': 'Failed to retrieve CFO KPIs'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def chro_kpis(request):
    """
    Get CHRO KPI data with date filtering support
    """
    try:
        # Parse date parameters
        period_date, mode = parse_date_params(request)
        
        # Get CHRO KPIs
        kpis = ExecutiveKPIDefinition.objects.filter(
            executive_role='CHRO',
            is_active=True
        ).order_by('priority', 'name')
        
        kpi_data = {
            'humanResourceExcellence': {
                'title': 'Human Resource Excellence',
                'kpis': {}
            }
        }
        
        for kpi in kpis:
            # Get performance for selected period
            latest_performance = get_performance_for_period(kpi, period_date, mode)
            
            # Create key from KPI name
            key = kpi.name.lower().replace(' ', '_').replace('-', '_')
            if 'productivity' in kpi.name.lower():
                key = 'staffProductivity'
            elif 'appraisal' in kpi.name.lower():
                key = 'executiveAppraisals'
            elif 'wage' in kpi.name.lower():
                key = 'wageBillReduction'
            
            kpi_data['humanResourceExcellence']['kpis'][key] = {
                'current': float(latest_performance.actual_value) if latest_performance else 0,
                'target': float(kpi.target_value) if kpi.target_value else 0,
                'description': kpi.description,
                'unit': kpi.unit,
                'priority': kpi.priority,
                'deadline': kpi.deadline,
                'period_date': period_date.isoformat(),
                'note': getattr(kpi, 'note', None) if hasattr(kpi, 'note') else None
            }
        
        return Response({
            'success': True,
            'data': kpi_data,
            'meta': {
                'executive_role': 'CHRO',
                'period_date': period_date.isoformat(),
                'mode': mode,
                'last_updated': timezone.now().isoformat(),
                'kpi_count': kpis.count()
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e),
            'message': 'Failed to retrieve CHRO KPIs'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)