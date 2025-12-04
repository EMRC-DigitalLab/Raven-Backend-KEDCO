# hr/views/executive_kpis/role_views.py - FIXED VERSION
"""
Executive KPI Role Views - REAL-TIME CALCULATIONS ONLY
No caching in ExecutivePerformance - all values calculated on-demand
ExecutivePerformance only used for manual-entry KPIs
"""
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.utils import timezone
from datetime import date
from decimal import Decimal

from ...models import ExecutiveKPIDefinition, ExecutivePerformance
from ...utils.kpi_calculator import UnifiedKPICalculator, CTOKPICalculator, CCOKPICalculator, CFOKPICalculator, CHROKPICalculator


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


def calculate_kpi_with_status(kpi_key, period_date, target_value, is_reverse_polarity=False, **kwargs):
    """
    Calculate KPI and determine status based on target
    
    Args:
        kpi_key: KPI identifier for calculator
        period_date: Date for calculation
        target_value: Target value (or dict with min/max for range)
        is_reverse_polarity: True if lower is better
        **kwargs: Additional filters (state, district)
    
    Returns:
        dict: KPI data with current value, target, status, progress
    """
    try:
        # Calculate using UnifiedKPICalculator
        result = UnifiedKPICalculator.calculate_kpi(
            kpi_key,
            period_date,
            period_type='monthly',
            **kwargs
        )
        
        current = result.get('value', 0)
        unit = result.get('unit', '')
        
        # Determine if target is range or single value
        if isinstance(target_value, dict):
            # Range target
            target_min = target_value.get('min', 0)
            target_max = target_value.get('max', 100)
            is_range = True
            
            # Check if within range
            if current >= target_min and current <= target_max:
                kpi_status = 'on_track'
                progress = 100
            elif current < target_min:
                gap_percentage = ((target_min - current) / target_min * 100) if target_min > 0 else 0
                if gap_percentage > 30:
                    kpi_status = 'critical'
                elif gap_percentage > 20:
                    kpi_status = 'off_track'
                else:
                    kpi_status = 'at_risk'
                progress = (current / target_min * 100) if target_min > 0 else 0
            else:  # Above max
                kpi_status = 'exceeding'
                progress = 100
        else:
            # Single target
            is_range = False
            target_min = None
            target_max = None
            
            if is_reverse_polarity:
                # Lower is better (e.g., cost ratios, attrition)
                if current <= target_value:
                    kpi_status = 'on_track'
                    progress = 100
                else:
                    excess_percentage = ((current - target_value) / target_value * 100) if target_value > 0 else 0
                    if excess_percentage > 30:
                        kpi_status = 'critical'
                    elif excess_percentage > 20:
                        kpi_status = 'off_track'
                    else:
                        kpi_status = 'at_risk'
                    progress = max(0, 100 - excess_percentage)
            else:
                # Higher is better (most KPIs)
                if current >= target_value:
                    kpi_status = 'on_track'
                    progress = 100
                else:
                    progress = (current / target_value * 100) if target_value > 0 else 0
                    gap_percentage = 100 - progress
                    
                    if gap_percentage > 30:
                        kpi_status = 'critical'
                    elif gap_percentage > 20:
                        kpi_status = 'off_track'
                    elif gap_percentage > 10:
                        kpi_status = 'at_risk'
                    else:
                        kpi_status = 'on_track'
        
        return {
            'current': current,
            'target': target_value if not is_range else None,
            'target_min': target_min,
            'target_max': target_max,
            'is_range': is_range,
            'status': kpi_status,
            'progress': min(progress, 100),
            'unit': unit,
            'is_auto_calculated': True,
            'calculation_source': result.get('source', ''),
            'metadata': result.get('metadata', {})
        }
    except Exception as e:
        # If calculation fails, return error state
        return {
            'current': 0,
            'target': target_value,
            'status': 'error',
            'progress': 0,
            'unit': '',
            'is_auto_calculated': False,
            'error': str(e)
        }


@api_view(['GET'])
def cto_kpis(request):
    """
    Get CTO KPI data with REAL-TIME AUTO-CALCULATION
    All values calculated on-demand, no caching
    
    Supports date filtering via query params:
    - mode: 'monthly' or 'range'
    - year, month: for monthly mode
    - from_date, to_date: for range mode
    """
    try:
        # Parse date parameters
        period_date, mode = parse_date_params(request)
        
        # Optional filters
        state_id = request.GET.get('state')
        district_id = request.GET.get('district')
        
        filters = {}
        if state_id:
            from common.models import State
            try:
                filters['state'] = State.objects.get(id=state_id)
            except State.DoesNotExist:
                pass
        if district_id:
            from common.models import BusinessDistrict
            try:
                filters['district'] = BusinessDistrict.objects.get(id=district_id)
            except BusinessDistrict.DoesNotExist:
                pass
        
        # Calculate all CTO KPIs using auto-calculation
        kpi_data = {}
        
        # Energy Delivered (MWh - NOT GWh!)
        energy_result = calculate_kpi_with_status(
            'energy_delivered_gwh',
            period_date,
            target_value=50000.0,  # Target in MWh
            **filters
        )
        kpi_data['energyDelivery'] = {
            **energy_result,
            'description': 'Total electrical energy delivered to customers',
            'priority': 'high',
            'deadline': 'Monthly',
            'monthlyData': [],
            'period_date': period_date.isoformat()
        }
        
        # Average Hours of Supply
        hours_result = calculate_kpi_with_status(
            'avg_hours_of_supply',
            period_date,
            target_value=20.0,
            **filters
        )
        kpi_data['hoursOfSupply'] = {
            **hours_result,
            'description': 'Average hours of electricity supply per day',
            'priority': 'critical',
            'deadline': 'Monthly',
            'monthlyData': [],
            'period_date': period_date.isoformat()
        }
        
        # Grid Offtake Capacity
        offtake_result = calculate_kpi_with_status(
            'grid_offtake_capacity',
            period_date,
            target_value=150.0,
            **filters
        )
        kpi_data['gridOfftake'] = {
            **offtake_result,
            'description': 'Maximum power capacity drawn from the grid',
            'priority': 'high',
            'deadline': 'Monthly',
            'monthlyData': [],
            'period_date': period_date.isoformat()
        }
        
        # SLA Compliance
        sla_result = calculate_kpi_with_status(
            'sla_compliance',
            period_date,
            target_value=90.0,
            **filters
        )
        kpi_data['slaCompliance'] = {
            **sla_result,
            'description': 'Percentage of feeders meeting band-specific supply hour targets',
            'priority': 'critical',
            'deadline': 'Monthly',
            'monthlyData': [],
            'period_date': period_date.isoformat()
        }
        
        # System Availability
        availability_result = calculate_kpi_with_status(
            'system_availability',
            period_date,
            target_value=95.0,
            **filters
        )
        kpi_data['systemAvailability'] = {
            **availability_result,
            'description': 'Percentage of time the system was operational',
            'priority': 'high',
            'deadline': 'Monthly',
            'monthlyData': [],
            'period_date': period_date.isoformat()
        }
        
        # Average Interruption Duration
        duration_result = calculate_kpi_with_status(
            'avg_interruption_duration',
            period_date,
            target_value=2.0,  # Target: max 2 hours/day
            is_reverse_polarity=True,  # Lower is better
            **filters
        )
        kpi_data['avgInterruptionDuration'] = {
            **duration_result,
            'description': 'Average interruption hours per feeder per day',
            'priority': 'high',
            'deadline': 'Monthly',
            'monthlyData': [],
            'period_date': period_date.isoformat()
        }
        
        # SAIFI
        saifi_result = calculate_kpi_with_status(
            'saifi',
            period_date,
            target_value=5.0,  # Lower is better
            is_reverse_polarity=True,
            **filters
        )
        kpi_data['saifi'] = {
            **saifi_result,
            'description': 'System Average Interruption Frequency Index (feeder-based)',
            'priority': 'medium',
            'deadline': 'Monthly',
            'monthlyData': [],
            'period_date': period_date.isoformat()
        }
        
        # SAIDI
        saidi_result = calculate_kpi_with_status(
            'saidi',
            period_date,
            target_value=300.0,  # Minutes - lower is better
            is_reverse_polarity=True,
            **filters
        )
        kpi_data['saidi'] = {
            **saidi_result,
            'description': 'System Average Interruption Duration Index (feeder-based)',
            'priority': 'medium',
            'deadline': 'Monthly',
            'monthlyData': [],
            'period_date': period_date.isoformat()
        }
        
        # For manual entry KPIs (like feeders upgraded), fetch from ExecutivePerformance
        # These cannot be auto-calculated and require manual data entry
        try:
            feeders_upgraded_kpi = ExecutiveKPIDefinition.objects.get(
                executive_role='CTO',
                name__icontains='Feeders Upgraded',
                is_active=True
            )
            
            feeders_upgraded_performance = ExecutivePerformance.objects.filter(
                kpi_definition=feeders_upgraded_kpi,
                period_date=period_date,
                period_type='monthly'
            ).first()
            
            if feeders_upgraded_performance:
                current_value = float(feeders_upgraded_performance.actual_value)
                target_value = float(feeders_upgraded_kpi.target_value) if feeders_upgraded_kpi.target_value else 10.0
                progress = (current_value / target_value * 100) if target_value > 0 else 0
                
                if progress >= 100:
                    kpi_status = 'on_track'
                elif progress >= 70:
                    kpi_status = 'at_risk'
                else:
                    kpi_status = 'off_track'
            else:
                current_value = 0
                target_value = 10.0
                progress = 0
                kpi_status = 'not_started'
            
            kpi_data['feedersUpgrade'] = {
                'current': current_value,
                'target': target_value,
                'status': kpi_status,
                'progress': min(progress, 100),
                'description': 'Number of feeders upgraded or rehabilitated',
                'unit': 'feeders',
                'priority': 'high',
                'deadline': 'Q4 2025',
                'monthlyData': [],
                'period_date': period_date.isoformat(),
                'is_auto_calculated': False,
                'requires_manual_entry': True
            }
        except ExecutiveKPIDefinition.DoesNotExist:
            # If KPI definition doesn't exist, show placeholder
            kpi_data['feedersUpgrade'] = {
                'current': 0,
                'target': 10.0,
                'status': 'not_started',
                'progress': 0,
                'description': 'Number of feeders upgraded or rehabilitated',
                'unit': 'feeders',
                'priority': 'high',
                'deadline': 'Q4 2025',
                'monthlyData': [],
                'period_date': period_date.isoformat(),
                'is_auto_calculated': False,
                'requires_manual_entry': True
            }
        
        return Response({
            'success': True,
            'data': kpi_data,
            'meta': {
                'executive_role': 'CTO',
                'period_date': period_date.isoformat(),
                'mode': mode,
                'last_updated': timezone.now().isoformat(),
                'kpi_count': len(kpi_data),
                'auto_calculated_count': sum(1 for v in kpi_data.values() if v.get('is_auto_calculated')),
                'manual_entry_count': sum(1 for v in kpi_data.values() if not v.get('is_auto_calculated')),
                'calculation_mode': 'real_time'
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        import traceback
        return Response({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc(),
            'message': 'Failed to retrieve CTO KPIs'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def cco_kpis(request):
    """
    Get CCO KPI data with REAL-TIME AUTO-CALCULATION
    """
    try:
        # Parse date parameters
        period_date, mode = parse_date_params(request)
        
        # Optional filters
        state_id = request.GET.get('state')
        district_id = request.GET.get('district')
        
        filters = {}
        if state_id:
            from common.models import State
            try:
                filters['state'] = State.objects.get(id=state_id)
            except State.DoesNotExist:
                pass
        if district_id:
            from common.models import BusinessDistrict
            try:
                filters['district'] = BusinessDistrict.objects.get(id=district_id)
            except BusinessDistrict.DoesNotExist:
                pass
        
        kpi_data = {
            'billingEfficiency': {
                'title': 'Billing Efficiency',
                'kpis': {}
            },
            'collectionEfficiency': {
                'title': 'Collection Efficiency',
                'kpis': {}
            },
            'bandAGrowth': {
                'title': 'Band A Growth & Customer Expansion',
                'kpis': {}
            }
        }
        
        # Billing Efficiency - MD Industrial
        md1_billing = calculate_kpi_with_status(
            'billing_efficiency_md1',
            period_date,
            target_value=100.0,
            **filters
        )
        kpi_data['billingEfficiency']['kpis']['mdIndustrial'] = {
            **md1_billing,
            'description': 'Billing efficiency for MD1 (industrial) customers',
            'priority': 'critical',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat()
        }
        
        # Billing Efficiency - MD Non-Industrial
        md2_billing = calculate_kpi_with_status(
            'billing_efficiency_md2',
            period_date,
            target_value=100.0,
            **filters
        )
        kpi_data['billingEfficiency']['kpis']['mdNonIndustrial'] = {
            **md2_billing,
            'description': 'Billing efficiency for MD2 (non-industrial) customers',
            'priority': 'critical',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat()
        }
        
        # Billing Efficiency - Regions
        non_md_billing = calculate_kpi_with_status(
            'billing_efficiency_non_md',
            period_date,
            target_value=100.0,
            **filters
        )
        kpi_data['billingEfficiency']['kpis']['regions'] = {
            **non_md_billing,
            'description': 'Billing efficiency for Non-MD (regional) customers',
            'priority': 'high',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat()
        }
        
        # Collection Efficiency - MD Industrial
        md1_collection = calculate_kpi_with_status(
            'collection_efficiency_md1',
            period_date,
            target_value=100.0,
            **filters
        )
        kpi_data['collectionEfficiency']['kpis']['mdIndustrial'] = {
            **md1_collection,
            'description': 'Collection efficiency for MD1 customers',
            'priority': 'critical',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat()
        }
        
        # Collection Efficiency - MD Non-Industrial
        md2_collection = calculate_kpi_with_status(
            'collection_efficiency_md2',
            period_date,
            target_value=100.0,
            **filters
        )
        kpi_data['collectionEfficiency']['kpis']['mdNonIndustrial'] = {
            **md2_collection,
            'description': 'Collection efficiency for MD2 customers',
            'priority': 'critical',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat()
        }
        
        # Collection Efficiency - Regions
        non_md_collection = calculate_kpi_with_status(
            'collection_efficiency_non_md',
            period_date,
            target_value=100.0,
            **filters
        )
        kpi_data['collectionEfficiency']['kpis']['regions'] = {
            **non_md_collection,
            'description': 'Collection efficiency for Non-MD customers',
            'priority': 'high',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat()
        }
        
        # Band A Growth - Feeders Commercially Ready
        feeders_ready = calculate_kpi_with_status(
            'feeders_commercially_ready',
            period_date,
            target_value=50,
            **filters
        )
        kpi_data['bandAGrowth']['kpis']['feedersCommerciallyReady'] = {
            **feeders_ready,
            'description': 'Number of Band A feeders (commercially ready)',
            'priority': 'high',
            'deadline': 'Q4 2025',
            'period_date': period_date.isoformat()
        }
        
        # Customers in Billing System
        customers_count = calculate_kpi_with_status(
            'customers_in_billing_system',
            period_date,
            target_value=500000,
            **filters
        )
        kpi_data['bandAGrowth']['kpis']['customersBillingSystem'] = {
            **customers_count,
            'description': 'Total active customers in billing system',
            'priority': 'high',
            'deadline': 'Q4 2025',
            'period_date': period_date.isoformat()
        }
        
        # PPM Revenue
        ppm_revenue = calculate_kpi_with_status(
            'ppm_revenue',
            period_date,
            target_value=500.0,
            **filters
        )
        kpi_data['bandAGrowth']['kpis']['ppmRevenue'] = {
            **ppm_revenue,
            'description': 'Revenue from prepaid meters',
            'priority': 'high',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat()
        }
        
        # Customer Attrition Rate
        attrition = calculate_kpi_with_status(
            'customer_attrition_rate',
            period_date,
            target_value=2.0,
            is_reverse_polarity=True,  # Lower is better
            **filters
        )
        kpi_data['bandAGrowth']['kpis']['customerRetention'] = {
            **attrition,
            'description': 'Customer attrition rate (lower is better)',
            'priority': 'medium',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat()
        }
        
        # New MD Customers Value
        new_md_value = calculate_kpi_with_status(
            'new_md_customers_value',
            period_date,
            target_value=100.0,
            **filters
        )
        kpi_data['bandAGrowth']['kpis']['newCustomersValue'] = {
            **new_md_value,
            'description': 'Revenue value from new MD customers',
            'priority': 'high',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat()
        }
        
        return Response({
            'success': True,
            'data': kpi_data,
            'meta': {
                'executive_role': 'CCO',
                'period_date': period_date.isoformat(),
                'mode': mode,
                'last_updated': timezone.now().isoformat(),
                'auto_calculated_kpis': 11,
                'calculation_mode': 'real_time'
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        import traceback
        return Response({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc(),
            'message': 'Failed to retrieve CCO KPIs'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def cfo_kpis(request):
    """
    Get CFO KPI data with REAL-TIME AUTO-CALCULATION
    """
    try:
        period_date, mode = parse_date_params(request)
        
        # Optional filters
        state_id = request.GET.get('state')
        district_id = request.GET.get('district')
        
        filters = {}
        if state_id:
            from common.models import State
            try:
                filters['state'] = State.objects.get(id=state_id)
            except State.DoesNotExist:
                pass
        if district_id:
            from common.models import BusinessDistrict
            try:
                filters['district'] = BusinessDistrict.objects.get(id=district_id)
            except BusinessDistrict.DoesNotExist:
                pass
        
        kpi_data = {
            'financialExcellence': {
                'title': 'Financial Excellence & Cost Optimization',
                'kpis': {}
            }
        }
        
        # Cost-to-Revenue Ratio
        cost_ratio = calculate_kpi_with_status(
            'cost_to_revenue_ratio',
            period_date,
            target_value={'min': 40.0, 'max': 50.0},  # Range target
            **filters
        )
        kpi_data['financialExcellence']['kpis']['costToRevenueRatio'] = {
            **cost_ratio,
            'description': 'Ratio of operational costs to revenue collected',
            'priority': 'critical',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat()
        }
        
        # OPEX per kWh
        opex_per_kwh = calculate_kpi_with_status(
            'opex_per_kwh',
            period_date,
            target_value=50.0,
            is_reverse_polarity=True,  # Lower is better
            **filters
        )
        kpi_data['financialExcellence']['kpis']['opexPerKwh'] = {
            **opex_per_kwh,
            'description': 'Operational expenditure per kilowatt-hour delivered',
            'priority': 'high',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat()
        }
        
        # Collection to NBET Ratio
        nbet_ratio = calculate_kpi_with_status(
            'collection_to_nbet_ratio',
            period_date,
            target_value=1.0
        )
        kpi_data['financialExcellence']['kpis']['collectionToNbetRatio'] = {
            **nbet_ratio,
            'description': 'Ratio of collections to NBET obligations',
            'priority': 'critical',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat()
        }
        
        return Response({
            'success': True,
            'data': kpi_data,
            'meta': {
                'executive_role': 'CFO',
                'period_date': period_date.isoformat(),
                'mode': mode,
                'last_updated': timezone.now().isoformat(),
                'auto_calculated_kpis': 3,
                'calculation_mode': 'real_time'
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        import traceback
        return Response({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc(),
            'message': 'Failed to retrieve CFO KPIs'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def chro_kpis(request):
    """
    Get CHRO KPI data with REAL-TIME AUTO-CALCULATION
    """
    try:
        period_date, mode = parse_date_params(request)
        
        # Optional filters
        state_id = request.GET.get('state')
        district_id = request.GET.get('district')
        
        filters = {}
        if state_id:
            from common.models import State
            try:
                filters['state'] = State.objects.get(id=state_id)
            except State.DoesNotExist:
                pass
        if district_id:
            from common.models import BusinessDistrict
            try:
                filters['district'] = BusinessDistrict.objects.get(id=district_id)
            except BusinessDistrict.DoesNotExist:
                pass
        
        kpi_data = {
            'humanResourceExcellence': {
                'title': 'Human Resource Excellence',
                'kpis': {}
            }
        }
        
        # Staff Productivity
        productivity = calculate_kpi_with_status(
            'staff_productivity',
            period_date,
            target_value=5.0,
            **filters
        )
        kpi_data['humanResourceExcellence']['kpis']['staffProductivity'] = {
            **productivity,
            'description': 'Revenue generated per staff member',
            'priority': 'high',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat()
        }
        
        # Employee Utilization
        utilization = calculate_kpi_with_status(
            'employee_utilization_rate',
            period_date,
            target_value=95.0,
            **filters
        )
        kpi_data['humanResourceExcellence']['kpis']['employeeUtilization'] = {
            **utilization,
            'description': 'Percentage of staff with assigned roles',
            'priority': 'medium',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat()
        }
        
        # Wage Bill vs Revenue
        wage_revenue = calculate_kpi_with_status(
            'wage_bill_vs_revenue',
            period_date,
            target_value=20.0,
            is_reverse_polarity=True,  # Lower is better
            **filters
        )
        kpi_data['humanResourceExcellence']['kpis']['wageBillVsRevenue'] = {
            **wage_revenue,
            'description': 'Wage bill as percentage of revenue',
            'priority': 'critical',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat()
        }
        
        # Wage Bill Reduction
        wage_reduction = calculate_kpi_with_status(
            'wage_bill_reduction',
            period_date,
            target_value=10.0,
            **filters
        )
        kpi_data['humanResourceExcellence']['kpis']['wageBillReduction'] = {
            **wage_reduction,
            'description': 'Wage bill reduction vs 2024 baseline',
            'priority': 'high',
            'deadline': 'Q4 2025',
            'period_date': period_date.isoformat()
        }
        
        # Staff Attrition
        attrition = calculate_kpi_with_status(
            'staff_attrition_rate',
            period_date,
            target_value=5.0,
            is_reverse_polarity=True,  # Lower is better
            **filters
        )
        kpi_data['humanResourceExcellence']['kpis']['staffAttrition'] = {
            **attrition,
            'description': 'Staff attrition rate (lower is better)',
            'priority': 'medium',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat()
        }
        
        return Response({
            'success': True,
            'data': kpi_data,
            'meta': {
                'executive_role': 'CHRO',
                'period_date': period_date.isoformat(),
                'mode': mode,
                'last_updated': timezone.now().isoformat(),
                'auto_calculated_kpis': 5,
                'calculation_mode': 'real_time'
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        import traceback
        return Response({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc(),
            'message': 'Failed to retrieve CHRO KPIs'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)