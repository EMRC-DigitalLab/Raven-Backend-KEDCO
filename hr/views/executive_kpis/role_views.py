# hr/views/executive_kpis/role_views.py - UPDATED WITH AUTO-CALCULATION
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


def get_kpi_value_auto_or_manual(kpi_definition, period_date, mode='monthly', **kwargs):
    """
    Get KPI value - auto-calculate if possible, otherwise fetch manual entry
    
    Args:
        kpi_definition: ExecutiveKPIDefinition instance
        period_date: Date for calculation
        mode: 'monthly' or 'range'
        **kwargs: Additional filters (state, district, feeder)
    
    Returns:
        dict: KPI value data with metadata
    """
    # Map KPI names to calculator keys
    kpi_name_mapping = {
        # CTO
        'Energy Delivered (GWh)': 'energy_delivered_gwh',
        'Average Hours of Supply per Day': 'avg_hours_of_supply',
        'Grid Offtake Capacity (MW)': 'grid_offtake_capacity',
        'SLA Compliance (%)': 'sla_compliance',
        'System Availability (%)': 'system_availability',
        'Average Interruption Duration (hours)': 'avg_interruption_duration',
        'SAIFI': 'saifi',
        'SAIDI': 'saidi',
        
        # CCO - Billing Efficiency
        'MD Industrial Billing Efficiency (%)': 'billing_efficiency_md1',
        'MD Non-Industrial Billing Efficiency (%)': 'billing_efficiency_md2',
        'Regions Billing Efficiency (%)': 'billing_efficiency_non_md',
        
        # CCO - Collection Efficiency
        'MD Industrial Collection Efficiency (%)': 'collection_efficiency_md1',
        'MD Non-Industrial Collection Efficiency (%)': 'collection_efficiency_md2',
        'Regions Collection Efficiency (%)': 'collection_efficiency_non_md',
        
        # CCO - Band A Growth
        'Feeders Commercially Ready': 'feeders_commercially_ready',
        'Customers in Billing System': 'customers_in_billing_system',
        'PPM Revenue Collected (₦M)': 'ppm_revenue',
        'Customer Attrition Rate (%)': 'customer_attrition_rate',
        'New MD Customers Value (₦M)': 'new_md_customers_value',
        
        # CFO
        'Cost-to-Revenue Ratio (%)': 'cost_to_revenue_ratio',
        'OPEX per kWh Delivered (₦/kWh)': 'opex_per_kwh',
        'Collection to NBET Payment Ratio': 'collection_to_nbet_ratio',
        
        # CHRO
        'Staff Productivity (₦M per staff)': 'staff_productivity',
        'Employee Utilization Rate (%)': 'employee_utilization_rate',
        'Wage Bill vs Revenue (%)': 'wage_bill_vs_revenue',
        'Wage Bill Reduction vs 2024 Baseline (%)': 'wage_bill_reduction',
        'Staff Attrition Rate (%)': 'staff_attrition_rate',
    }
    
    kpi_key = kpi_name_mapping.get(kpi_definition.name)
    
    if kpi_key:
        # Auto-calculable KPI
        try:
            result = UnifiedKPICalculator.calculate_kpi(
                kpi_key,
                period_date,
                period_type='monthly',
                **kwargs
            )
            
            # Store the calculated value in ExecutivePerformance for caching
            performance, created = ExecutivePerformance.objects.update_or_create(
                kpi_definition=kpi_definition,
                period_date=period_date,
                period_type='monthly',
                defaults={
                    'actual_value': Decimal(str(result['value'])),
                    'data_source': 'auto_calculated',
                    'notes': f"Auto-calculated from {result['source']}",
                }
            )
            
            return {
                'current': result['value'],
                'data_source': 'auto_calculated',
                'calculation_method': result['calculation_method'],
                'source_models': result['source'],
                'metadata': result.get('metadata', {}),
                'calculated_at': result.get('calculated_at'),
                'is_auto': True
            }
        except Exception as e:
            # If auto-calculation fails, try to get manual entry
            print(f"Auto-calculation failed for {kpi_definition.name}: {e}")
            pass
    
    # Fall back to manual entry
    performance = ExecutivePerformance.objects.filter(
        kpi_definition=kpi_definition,
        period_date=period_date,
        period_type='monthly'
    ).first()
    
    if performance:
        return {
            'current': float(performance.actual_value),
            'data_source': performance.data_source or 'manual_entry',
            'notes': performance.notes,
            'verified': performance.verified,
            'is_auto': False
        }
    else:
        return {
            'current': 0,
            'data_source': 'no_data',
            'is_auto': False
        }


def get_monthly_trend_data(kpi_definition, period_date, months_back=4, **kwargs):
    """
    Get trend data for the last N months before period_date
    Auto-calculates if possible, otherwise uses manual entries
    """
    kpi_name_mapping = {
        'Energy Delivered (GWh)': 'energy_delivered_gwh',
        'Average Hours of Supply per Day': 'avg_hours_of_supply',
        'Grid Offtake Capacity (MW)': 'grid_offtake_capacity',
        'SLA Compliance (%)': 'sla_compliance',
        # Add all other mappings as needed
    }
    
    kpi_key = kpi_name_mapping.get(kpi_definition.name)
    monthly_data = []
    
    for i in range(months_back, 0, -1):
        month_date = period_date - relativedelta(months=i)
        
        if kpi_key:
            # Try auto-calculation
            try:
                result = UnifiedKPICalculator.calculate_kpi(
                    kpi_key,
                    month_date,
                    period_type='monthly',
                    **kwargs
                )
                value = result['value']
            except:
                # Fall back to manual entry
                performance = ExecutivePerformance.objects.filter(
                    kpi_definition=kpi_definition,
                    period_date=month_date,
                    period_type='monthly'
                ).first()
                value = float(performance.actual_value) if performance else 0
        else:
            # Manual entry only
            performance = ExecutivePerformance.objects.filter(
                kpi_definition=kpi_definition,
                period_date=month_date,
                period_type='monthly'
            ).first()
            value = float(performance.actual_value) if performance else 0
        
        monthly_data.append({
            'month': month_date.strftime('%b'),
            'value': value
        })
    
    return monthly_data


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cto_kpis(request):
    """
    Get CTO KPI data with AUTO-CALCULATION
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
            filters['state'] = State.objects.get(id=state_id)
        if district_id:
            from common.models import BusinessDistrict
            filters['district'] = BusinessDistrict.objects.get(id=district_id)
        
        # Calculate all CTO KPIs using auto-calculation
        kpi_data = {}
        
        # Energy Delivered
        energy_result = CTOKPICalculator.calculate_energy_delivered(period_date, **filters)
        kpi_data['energyDelivery'] = {
            'current': energy_result['value'],
            'target': 50.0,  # Example target - should come from KPI definition
            'status': 'on_track',  # Calculate based on target
            'progress': (energy_result['value'] / 50.0 * 100) if energy_result['value'] else 0,
            'description': 'Total electrical energy delivered to customers',
            'unit': energy_result['unit'],
            'priority': 'high',
            'deadline': 'Monthly',
            'monthlyData': [],  # Can be populated with historical data
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'calculation_source': energy_result['source']
        }
        
        # Average Hours of Supply
        hours_result = CTOKPICalculator.calculate_avg_hours_of_supply(period_date, **filters)
        kpi_data['hoursOfSupply'] = {
            'current': hours_result['value'],
            'target': 20.0,  # Example target
            'status': 'on_track',
            'progress': (hours_result['value'] / 20.0 * 100) if hours_result['value'] else 0,
            'description': 'Average hours of electricity supply per day',
            'unit': hours_result['unit'],
            'priority': 'critical',
            'deadline': 'Monthly',
            'monthlyData': [],
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'calculation_source': hours_result['source']
        }
        
        # Grid Offtake
        offtake_result = CTOKPICalculator.calculate_grid_offtake_capacity(period_date, **filters)
        kpi_data['gridOfftake'] = {
            'current': offtake_result['value'],
            'target': 150.0,  # Example target
            'status': 'on_track',
            'progress': (offtake_result['value'] / 150.0 * 100) if offtake_result['value'] else 0,
            'description': 'Maximum power capacity drawn from the grid',
            'unit': offtake_result['unit'],
            'priority': 'high',
            'deadline': 'Monthly',
            'monthlyData': [],
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'calculation_source': offtake_result['source']
        }
        
        # SLA Compliance
        sla_result = CTOKPICalculator.calculate_sla_compliance(period_date, **filters)
        kpi_data['slaCompliance'] = {
            'current': sla_result['value'],
            'target': 90.0,
            'status': 'on_track' if sla_result['value'] >= 90 else 'off_track',
            'progress': sla_result['value'],
            'description': 'Service Level Agreement compliance percentage',
            'unit': sla_result['unit'],
            'priority': 'critical',
            'deadline': 'Monthly',
            'monthlyData': [],
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'calculation_source': sla_result['source'],
            'metadata': sla_result.get('metadata', {})
        }
        
        # System Availability
        availability_result = CTOKPICalculator.calculate_system_availability(period_date, **filters)
        kpi_data['systemAvailability'] = {
            'current': availability_result['value'],
            'target': 95.0,
            'status': 'on_track' if availability_result['value'] >= 95 else 'off_track',
            'progress': availability_result['value'],
            'description': 'Percentage of time the system was operational',
            'unit': availability_result['unit'],
            'priority': 'high',
            'deadline': 'Monthly',
            'monthlyData': [],
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'calculation_source': availability_result['source']
        }
        
        # SAIFI
        saifi_result = CTOKPICalculator.calculate_saifi(period_date, **filters)
        kpi_data['saifi'] = {
            'current': saifi_result['value'],
            'target': 5.0,  # Lower is better
            'status': 'on_track' if saifi_result['value'] <= 5.0 else 'off_track',
            'progress': 100 - (saifi_result['value'] / 5.0 * 100) if saifi_result['value'] <= 5.0 else 0,
            'description': 'System Average Interruption Frequency Index',
            'unit': saifi_result['unit'],
            'priority': 'medium',
            'deadline': 'Monthly',
            'monthlyData': [],
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'calculation_source': saifi_result['source']
        }
        
        # SAIDI
        saidi_result = CTOKPICalculator.calculate_saidi(period_date, **filters)
        kpi_data['saidi'] = {
            'current': saidi_result['value'],
            'target': 300.0,  # Minutes - lower is better
            'status': 'on_track' if saidi_result['value'] <= 300.0 else 'off_track',
            'progress': 100 - (saidi_result['value'] / 300.0 * 100) if saidi_result['value'] <= 300.0 else 0,
            'description': 'System Average Interruption Duration Index',
            'unit': saidi_result['unit'],
            'priority': 'medium',
            'deadline': 'Monthly',
            'monthlyData': [],
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'calculation_source': saidi_result['source']
        }
        
        # For manual entry KPIs (like feeders upgraded), still fetch from database
        feeders_upgraded_performance = ExecutivePerformance.objects.filter(
            kpi_definition__executive_role='CTO',
            kpi_definition__name__icontains='Feeders Upgraded',
            period_date=period_date,
            period_type='monthly'
        ).first()
        
        kpi_data['feedersUpgrade'] = {
            'current': float(feeders_upgraded_performance.actual_value) if feeders_upgraded_performance else 0,
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
                'manual_entry_count': sum(1 for v in kpi_data.values() if not v.get('is_auto_calculated'))
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
    Get CCO KPI data with AUTO-CALCULATION
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
            filters['state'] = State.objects.get(id=state_id)
        if district_id:
            from common.models import BusinessDistrict
            filters['district'] = BusinessDistrict.objects.get(id=district_id)
        
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
        md1_billing = CCOKPICalculator.calculate_billing_efficiency_by_metering_type(
            period_date, 'MD1', **filters
        )
        kpi_data['billingEfficiency']['kpis']['mdIndustrial'] = {
            'current': md1_billing['value'],
            'target': 100.0,
            'description': 'Billing efficiency for MD1 (industrial) customers',
            'unit': md1_billing['unit'],
            'priority': 'critical',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'metadata': md1_billing.get('metadata', {})
        }
        
        # Billing Efficiency - MD Non-Industrial
        md2_billing = CCOKPICalculator.calculate_billing_efficiency_by_metering_type(
            period_date, 'MD2', **filters
        )
        kpi_data['billingEfficiency']['kpis']['mdNonIndustrial'] = {
            'current': md2_billing['value'],
            'target': 100.0,
            'description': 'Billing efficiency for MD2 (non-industrial) customers',
            'unit': md2_billing['unit'],
            'priority': 'critical',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'metadata': md2_billing.get('metadata', {})
        }
        
        # Billing Efficiency - Regions
        non_md_billing = CCOKPICalculator.calculate_billing_efficiency_by_metering_type(
            period_date, 'Non-MD', **filters
        )
        kpi_data['billingEfficiency']['kpis']['regions'] = {
            'current': non_md_billing['value'],
            'target': 100.0,
            'description': 'Billing efficiency for Non-MD (regional) customers',
            'unit': non_md_billing['unit'],
            'priority': 'high',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'metadata': non_md_billing.get('metadata', {})
        }
        
        # Collection Efficiency - MD Industrial
        md1_collection = CCOKPICalculator.calculate_collection_efficiency_by_metering_type(
            period_date, 'MD1', **filters
        )
        kpi_data['collectionEfficiency']['kpis']['mdIndustrial'] = {
            'current': md1_collection['value'],
            'target': 100.0,
            'description': 'Collection efficiency for MD1 customers',
            'unit': md1_collection['unit'],
            'priority': 'critical',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'metadata': md1_collection.get('metadata', {})
        }
        
        # Collection Efficiency - MD Non-Industrial
        md2_collection = CCOKPICalculator.calculate_collection_efficiency_by_metering_type(
            period_date, 'MD2', **filters
        )
        kpi_data['collectionEfficiency']['kpis']['mdNonIndustrial'] = {
            'current': md2_collection['value'],
            'target': 100.0,
            'description': 'Collection efficiency for MD2 customers',
            'unit': md2_collection['unit'],
            'priority': 'critical',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'metadata': md2_collection.get('metadata', {})
        }
        
        # Collection Efficiency - Regions
        non_md_collection = CCOKPICalculator.calculate_collection_efficiency_by_metering_type(
            period_date, 'Non-MD', **filters
        )
        kpi_data['collectionEfficiency']['kpis']['regions'] = {
            'current': non_md_collection['value'],
            'target': 100.0,
            'description': 'Collection efficiency for Non-MD customers',
            'unit': non_md_collection['unit'],
            'priority': 'high',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'metadata': non_md_collection.get('metadata', {})
        }
        
        # Band A Growth - Feeders Commercially Ready
        feeders_ready = CCOKPICalculator.calculate_feeders_commercially_ready(period_date, **filters)
        kpi_data['bandAGrowth']['kpis']['feedersCommerciallyReady'] = {
            'current': feeders_ready['value'],
            'target': 50,
            'description': 'Number of Band A feeders (commercially ready)',
            'unit': feeders_ready['unit'],
            'priority': 'high',
            'deadline': 'Q4 2025',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True
        }
        
        # Customers in Billing System
        customers_count = CCOKPICalculator.calculate_customers_in_billing_system(period_date, **filters)
        kpi_data['bandAGrowth']['kpis']['customersBillingSystem'] = {
            'current': customers_count['value'],
            'target': 500000,
            'description': 'Total active customers in billing system',
            'unit': customers_count['unit'],
            'priority': 'high',
            'deadline': 'Q4 2025',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True
        }
        
        # PPM Revenue
        ppm_revenue = CCOKPICalculator.calculate_ppm_revenue(period_date, **filters)
        kpi_data['bandAGrowth']['kpis']['ppmRevenue'] = {
            'current': ppm_revenue['value'],
            'target': 500.0,
            'description': 'Revenue from prepaid meters',
            'unit': ppm_revenue['unit'],
            'priority': 'high',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'metadata': ppm_revenue.get('metadata', {})
        }
        
        # Customer Attrition Rate
        attrition = CCOKPICalculator.calculate_customer_attrition_rate(period_date, **filters)
        kpi_data['bandAGrowth']['kpis']['customerRetention'] = {
            'current': attrition['value'],
            'target': 2.0,  # Lower is better
            'description': 'Customer attrition rate (lower is better)',
            'unit': attrition['unit'],
            'priority': 'medium',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'metadata': attrition.get('metadata', {})
        }
        
        # New MD Customers Value
        new_md_value = CCOKPICalculator.calculate_new_md_customers_value(period_date, **filters)
        kpi_data['bandAGrowth']['kpis']['newCustomersValue'] = {
            'current': new_md_value['value'],
            'target': 100.0,
            'description': 'Revenue value from new MD customers',
            'unit': new_md_value['unit'],
            'priority': 'high',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'metadata': new_md_value.get('metadata', {})
        }
        
        return Response({
            'success': True,
            'data': kpi_data,
            'meta': {
                'executive_role': 'CCO',
                'period_date': period_date.isoformat(),
                'mode': mode,
                'last_updated': timezone.now().isoformat(),
                'auto_calculated_kpis': 11
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
    Get CFO KPI data with AUTO-CALCULATION
    """
    try:
        period_date, mode = parse_date_params(request)
        
        # Optional filters
        state_id = request.GET.get('state')
        district_id = request.GET.get('district')
        
        filters = {}
        if state_id:
            from common.models import State
            filters['state'] = State.objects.get(id=state_id)
        if district_id:
            from common.models import BusinessDistrict
            filters['district'] = BusinessDistrict.objects.get(id=district_id)
        
        kpi_data = {
            'financialExcellence': {
                'title': 'Financial Excellence & Cost Optimization',
                'kpis': {}
            }
        }
        
        # Cost-to-Revenue Ratio
        cost_ratio = CFOKPICalculator.calculate_cost_to_revenue_ratio(period_date, **filters)
        kpi_data['financialExcellence']['kpis']['costToRevenueRatio'] = {
            'current': cost_ratio['value'],
            'target': {'min': 40.0, 'max': 50.0},
            'description': 'Ratio of operational costs to revenue collected',
            'unit': cost_ratio['unit'],
            'priority': 'critical',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'metadata': cost_ratio.get('metadata', {})
        }
        
        # OPEX per kWh
        opex_per_kwh = CFOKPICalculator.calculate_opex_per_kwh(period_date, **filters)
        kpi_data['financialExcellence']['kpis']['opexPerKwh'] = {
            'current': opex_per_kwh['value'],
            'target': 50.0,
            'description': 'Operational expenditure per kilowatt-hour delivered',
            'unit': opex_per_kwh['unit'],
            'priority': 'high',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'metadata': opex_per_kwh.get('metadata', {})
        }
        
        # Collection to NBET Ratio
        nbet_ratio = CFOKPICalculator.calculate_collection_to_nbet_ratio(period_date)
        kpi_data['financialExcellence']['kpis']['collectionToNbetRatio'] = {
            'current': nbet_ratio['value'],
            'target': 1.0,
            'description': 'Ratio of collections to NBET obligations',
            'unit': nbet_ratio['unit'],
            'priority': 'critical',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'metadata': nbet_ratio.get('metadata', {})
        }
        
        return Response({
            'success': True,
            'data': kpi_data,
            'meta': {
                'executive_role': 'CFO',
                'period_date': period_date.isoformat(),
                'mode': mode,
                'last_updated': timezone.now().isoformat(),
                'auto_calculated_kpis': 3
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
    Get CHRO KPI data with AUTO-CALCULATION
    """
    try:
        period_date, mode = parse_date_params(request)
        
        # Optional filters
        state_id = request.GET.get('state')
        district_id = request.GET.get('district')
        
        filters = {}
        if state_id:
            from common.models import State
            filters['state'] = State.objects.get(id=state_id)
        if district_id:
            from common.models import BusinessDistrict
            filters['district'] = BusinessDistrict.objects.get(id=district_id)
        
        kpi_data = {
            'humanResourceExcellence': {
                'title': 'Human Resource Excellence',
                'kpis': {}
            }
        }
        
        # Staff Productivity
        productivity = CHROKPICalculator.calculate_staff_productivity(period_date, **filters)
        kpi_data['humanResourceExcellence']['kpis']['staffProductivity'] = {
            'current': productivity['value'],
            'target': 5.0,
            'description': 'Revenue generated per staff member',
            'unit': productivity['unit'],
            'priority': 'high',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'metadata': productivity.get('metadata', {})
        }
        
        # Employee Utilization
        utilization = CHROKPICalculator.calculate_employee_utilization_rate(period_date, **filters)
        kpi_data['humanResourceExcellence']['kpis']['employeeUtilization'] = {
            'current': utilization['value'],
            'target': 95.0,
            'description': 'Percentage of staff with assigned roles',
            'unit': utilization['unit'],
            'priority': 'medium',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'metadata': utilization.get('metadata', {})
        }
        
        # Wage Bill vs Revenue
        wage_revenue = CHROKPICalculator.calculate_wage_bill_vs_revenue(period_date, **filters)
        kpi_data['humanResourceExcellence']['kpis']['wageBillVsRevenue'] = {
            'current': wage_revenue['value'],
            'target': 20.0,
            'description': 'Wage bill as percentage of revenue',
            'unit': wage_revenue['unit'],
            'priority': 'critical',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'metadata': wage_revenue.get('metadata', {})
        }
        
        # Wage Bill Reduction
        wage_reduction = CHROKPICalculator.calculate_wage_bill_reduction(period_date, **filters)
        kpi_data['humanResourceExcellence']['kpis']['wageBillReduction'] = {
            'current': wage_reduction['value'],
            'target': 10.0,
            'description': 'Wage bill reduction vs 2024 baseline',
            'unit': wage_reduction['unit'],
            'priority': 'high',
            'deadline': 'Q4 2025',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'metadata': wage_reduction.get('metadata', {})
        }
        
        # Staff Attrition
        attrition = CHROKPICalculator.calculate_staff_attrition_rate(period_date, **filters)
        kpi_data['humanResourceExcellence']['kpis']['staffAttrition'] = {
            'current': attrition['value'],
            'target': 5.0,
            'description': 'Staff attrition rate (lower is better)',
            'unit': attrition['unit'],
            'priority': 'medium',
            'deadline': 'Monthly',
            'period_date': period_date.isoformat(),
            'is_auto_calculated': True,
            'metadata': attrition.get('metadata', {})
        }
        
        return Response({
            'success': True,
            'data': kpi_data,
            'meta': {
                'executive_role': 'CHRO',
                'period_date': period_date.isoformat(),
                'mode': mode,
                'last_updated': timezone.now().isoformat(),
                'auto_calculated_kpis': 5
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e),
            'message': 'Failed to retrieve CHRO KPIs'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)