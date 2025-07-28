# analytics/utils/technical_calculations.py
from django.db.models import Sum, Avg, Count, Max, Q
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal
import logging

from technical.models import (
    FeederEnergyDaily, FeederEnergyMonthly, HourlyLoad, 
    FeederInterruption, DailyHoursOfSupply
)
from common.models import Feeder, BusinessDistrict, State
from commercial.models import Customer

logger = logging.getLogger(__name__)

class TechnicalCalculator:
    """
    Handles all technical metric calculations with proper filtering support.
    Corrected calculations based on domain knowledge and best practices.
    """
    
    def __init__(self, month_date, state=None, business_district=None, feeder=None):
        self.month_date = month_date
        self.state = state
        self.business_district = business_district
        self.feeder = feeder
        
        # Calculate month boundaries
        self.start_date = month_date.replace(day=1)
        if month_date.month == 12:
            self.end_date = month_date.replace(year=month_date.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            self.end_date = month_date.replace(month=month_date.month + 1, day=1) - timedelta(days=1)
        
        # Get filtered feeder queryset
        self.feeders = self._get_filtered_feeders()
        
    def _get_filtered_feeders(self):
        """Get feeders based on filtering criteria"""
        if self.feeder:
            return Feeder.objects.filter(id=self.feeder.id)
        elif self.business_district:
            return Feeder.objects.filter(business_district=self.business_district)
        elif self.state:
            return Feeder.objects.filter(business_district__state=self.state)
        else:
            return Feeder.objects.all()
    
    def calculate_energy_metrics(self):
        """Calculate energy delivery metrics"""
        # Use monthly aggregates if available, otherwise sum daily
        monthly_energy = FeederEnergyMonthly.objects.filter(
            feeder__in=self.feeders,
            period=self.start_date
        ).aggregate(
            total=Sum('energy_mwh')
        )['total'] or Decimal('0')
        
        if monthly_energy == 0:
            # Fallback to daily aggregation
            monthly_energy = FeederEnergyDaily.objects.filter(
                feeder__in=self.feeders,
                date__range=(self.start_date, self.end_date)
            ).aggregate(
                total=Sum('energy_mwh')
            )['total'] or Decimal('0')
        
        return {
            'total_energy_delivered': monthly_energy
        }
    
    def calculate_load_metrics(self):
        """Calculate load-related metrics"""
        # Get hourly load data for the month
        hourly_loads = HourlyLoad.objects.filter(
            feeder__in=self.feeders,
            date__range=(self.start_date, self.end_date)
        )
        
        if not hourly_loads.exists():
            return {
                'avg_peak_load': Decimal('0'),
                'max_peak_load': Decimal('0')
            }
        
        # Calculate daily peak loads first
        daily_peaks = hourly_loads.values('feeder', 'date').annotate(
            daily_peak=Max('load_mw')
        )
        
        # Average of daily peaks across all feeders and days
        avg_peak = daily_peaks.aggregate(
            avg=Avg('daily_peak')
        )['avg'] or Decimal('0')
        
        # Maximum peak across all feeders and days
        max_peak = daily_peaks.aggregate(
            max=Max('daily_peak')
        )['max'] or Decimal('0')
        
        return {
            'avg_peak_load': avg_peak,
            'max_peak_load': max_peak
        }
    
    def calculate_supply_hours(self):
        """Calculate hours of supply metrics"""
        # Method 1: Use DailyHoursOfSupply if available
        daily_supply = DailyHoursOfSupply.objects.filter(
            feeder__in=self.feeders,
            date__range=(self.start_date, self.end_date)
        )
        
        if daily_supply.exists():
            avg_hours = daily_supply.aggregate(
                avg=Avg('hours_supplied')
            )['avg'] or Decimal('0')
            
            total_hours = daily_supply.aggregate(
                total=Sum('hours_supplied')
            )['total'] or Decimal('0')
        else:
            # Method 2: Calculate from hourly load data
            # Count hours where load > 0 for each feeder-day combination
            hourly_data = HourlyLoad.objects.filter(
                feeder__in=self.feeders,
                date__range=(self.start_date, self.end_date),
                load_mw__gt=0
            ).values('feeder', 'date').annotate(
                daily_hours=Count('hour')
            )
            
            if hourly_data.exists():
                avg_hours = hourly_data.aggregate(
                    avg=Avg('daily_hours')
                )['avg'] or Decimal('0')
                
                total_hours = hourly_data.aggregate(
                    total=Sum('daily_hours')
                )['total'] or Decimal('0')
            else:
                avg_hours = Decimal('0')
                total_hours = Decimal('0')
        
        return {
            'avg_hours_of_supply': avg_hours,
            'total_supply_hours': total_hours
        }
    
    def calculate_interruption_metrics(self):
        """Calculate interruption-related metrics"""
        interruptions = FeederInterruption.objects.filter(
            feeder__in=self.feeders,
            occurred_at__date__range=(self.start_date, self.end_date)
        )
        
        total_interruptions = interruptions.count()
        
        if total_interruptions == 0:
            return {
                'total_interruptions': 0,
                'avg_daily_interruptions': Decimal('0'),
                'avg_interruption_duration': Decimal('0'),
                'total_interruption_hours': Decimal('0'),
                'avg_turnaround_time': Decimal('0'),
                'interruption_breakdown': {}
            }
        
        # Calculate durations for restored interruptions
        restored_interruptions = interruptions.filter(restored_at__isnull=False)
        
        total_duration_hours = Decimal('0')
        for interruption in restored_interruptions:
            duration = (interruption.restored_at - interruption.occurred_at).total_seconds() / 3600
            total_duration_hours += Decimal(str(duration))
        
        # Average metrics
        days_in_month = (self.end_date - self.start_date).days + 1
        avg_daily_interruptions = Decimal(total_interruptions) / Decimal(days_in_month)
        
        restored_count = restored_interruptions.count()
        avg_duration = total_duration_hours / restored_count if restored_count > 0 else Decimal('0')
        avg_turnaround = avg_duration  # Same as duration for power restoration
        
        # Interruption breakdown by type
        breakdown = self._calculate_interruption_breakdown(interruptions)
        
        return {
            'total_interruptions': total_interruptions,
            'avg_daily_interruptions': avg_daily_interruptions,
            'avg_interruption_duration': avg_duration,
            'total_interruption_hours': total_duration_hours,
            'avg_turnaround_time': avg_turnaround,
            'interruption_breakdown': breakdown
        }
    
    def calculate_interruption_metrics(self):
        """Calculate interruption-related metrics with proper fault categorization"""
        interruptions = FeederInterruption.objects.filter(
            feeder__in=self.feeders,
            occurred_at__date__range=(self.start_date, self.end_date)
        )
        
        total_interruptions = interruptions.count()
        
        if total_interruptions == 0:
            return {
                'total_interruptions': 0,
                'avg_daily_interruptions': Decimal('0'),
                'avg_interruption_duration': Decimal('0'),
                'total_interruption_hours': Decimal('0'),
                'avg_turnaround_time': Decimal('0'),
                'avg_fault_turnaround_time': Decimal('0'),
                'interruption_breakdown': {},
                'summary_breakdown': {}
            }
        
        # Calculate durations for restored interruptions
        restored_interruptions = interruptions.filter(restored_at__isnull=False)
        
        # Total duration including all interruptions
        total_duration_hours = Decimal('0')
        for interruption in restored_interruptions:
            duration = (interruption.restored_at - interruption.occurred_at).total_seconds() / 3600
            total_duration_hours += Decimal(str(duration))
        
        # Fault-only duration (excluding load shedding)
        fault_interruptions = restored_interruptions.exclude(
            interruption_type__in=['L/S', 'L/S GS', '330KV L/S', 'T/LS']
        )
        
        fault_duration_hours = Decimal('0')
        for interruption in fault_interruptions:
            duration = (interruption.restored_at - interruption.occurred_at).total_seconds() / 3600
            fault_duration_hours += Decimal(str(duration))
        
        # Average metrics
        days_in_month = (self.end_date - self.start_date).days + 1
        avg_daily_interruptions = Decimal(total_interruptions) / Decimal(days_in_month)
        
        restored_count = restored_interruptions.count()
        avg_duration = total_duration_hours / restored_count if restored_count > 0 else Decimal('0')
        
        fault_count = fault_interruptions.count()
        avg_fault_turnaround = fault_duration_hours / fault_count if fault_count > 0 else Decimal('0')
        
        # Detailed and summary breakdowns
        detailed_breakdown, summary_breakdown = self._calculate_interruption_breakdowns(restored_interruptions)
        
        return {
            'total_interruptions': total_interruptions,
            'avg_daily_interruptions': avg_daily_interruptions,
            'avg_interruption_duration': avg_duration,
            'total_interruption_hours': total_duration_hours,
            'avg_turnaround_time': avg_duration,  # Keep for backward compatibility
            'avg_fault_turnaround_time': avg_fault_turnaround,  # New separate metric
            'interruption_breakdown': detailed_breakdown,
            'summary_breakdown': summary_breakdown
        }
    
    def _calculate_interruption_breakdowns(self, interruptions):
        """Calculate both detailed and summary interruption breakdowns"""
        
        # Detailed breakdown - store exact fault types
        detailed_breakdown = {}
        
        # Summary breakdown - categorize into business groups
        summary_breakdown = {
            'load_shedding_hours': Decimal('0'),
            'equipment_fault_hours': Decimal('0'),
            'line_fault_hours': Decimal('0'),
            'maintenance_hours': Decimal('0'),
            'other_fault_hours': Decimal('0'),
        }
        
        # Mapping from specific fault types to summary categories
        summary_mapping = {
            # Load Shedding types
            'L/S': 'load_shedding_hours',
            'L/S GS': 'load_shedding_hours',
            '330KV L/S': 'load_shedding_hours',
            'T/LS': 'load_shedding_hours',
            
            # Equipment Fault types
            'E/F': 'equipment_fault_hours',
            'O/C': 'equipment_fault_hours',
            'O/C & E/F': 'equipment_fault_hours',
            'OC & E/F': 'equipment_fault_hours',
            'O/S': 'equipment_fault_hours',
            'T/F': 'equipment_fault_hours',
            'B/F': 'equipment_fault_hours',
            'O/N': 'equipment_fault_hours',
            'O/E': 'equipment_fault_hours',
            'P/O': 'equipment_fault_hours',
            'O/F': 'equipment_fault_hours',
            'P/M': 'equipment_fault_hours',
            'T/S': 'equipment_fault_hours',
            'EM/D': 'equipment_fault_hours',
            'S/C': 'equipment_fault_hours',
            'D/C': 'equipment_fault_hours',
            'IN O/C': 'equipment_fault_hours',
            'LIM': 'equipment_fault_hours',
            '132KV E/F': 'equipment_fault_hours',
            '132KV CB/F': 'equipment_fault_hours',
            
            # Line Fault types
            '330KV L/F': 'line_fault_hours',
            '132KV L/F': 'line_fault_hours',
            
            # Maintenance types
            'MTNC': 'maintenance_hours',
            'MTCE': 'maintenance_hours',
            '132KV MTCE': 'maintenance_hours',
            'permit': 'maintenance_hours',
            
            # Other/Unspecified
            'NO RI': 'other_fault_hours',
            'N/A': 'other_fault_hours',
            'O': 'other_fault_hours',
            'OFF': 'other_fault_hours',
            'tcn': 'other_fault_hours',
            'fault': 'other_fault_hours',
        }
        
        for interruption in interruptions:
            duration_hours = Decimal(str(interruption.duration_hours))
            fault_type = interruption.interruption_type
            
            # Store detailed breakdown
            if fault_type in detailed_breakdown:
                detailed_breakdown[fault_type] += duration_hours
            else:
                detailed_breakdown[fault_type] = duration_hours
            
            # Categorize into summary breakdown
            summary_category = summary_mapping.get(fault_type, 'other_fault_hours')
            summary_breakdown[summary_category] += duration_hours
        
        # Convert detailed breakdown to float for JSON storage
        detailed_breakdown = {k: float(v) for k, v in detailed_breakdown.items()}
        
        return detailed_breakdown, summary_breakdown
    
    def calculate_infrastructure_metrics(self):
        """Calculate infrastructure-related metrics"""
        active_feeders = self.feeders.count()
        
        # Count customers served by filtered feeders
        if self.feeder:
            customer_count = Customer.objects.filter(
                transformer__feeder=self.feeder
            ).count()
        elif self.business_district:
            customer_count = Customer.objects.filter(
                transformer__feeder__business_district=self.business_district
            ).count()
        elif self.state:
            customer_count = Customer.objects.filter(
                transformer__feeder__business_district__state=self.state
            ).count()
        else:
            customer_count = Customer.objects.count()
        
        return {
            'active_feeder_count': active_feeders,
            'total_customer_count': customer_count
        }
    
    def calculate_reliability_indices(self, interruption_data, infrastructure_data):
        """Calculate SAIFI and SAIDI reliability indices"""
        total_customers = infrastructure_data['total_customer_count']
        
        if total_customers == 0:
            return {
                'saifi': Decimal('0'),
                'saidi': Decimal('0')
            }
        
        # SAIFI = Total Customer Interruptions / Total Customers Served
        # Approximation: Total Interruptions * Avg Customers per Feeder
        avg_customers_per_feeder = total_customers / max(infrastructure_data['active_feeder_count'], 1)
        total_customer_interruptions = interruption_data['total_interruptions'] * avg_customers_per_feeder
        saifi = total_customer_interruptions / total_customers
        
        # SAIDI = Total Customer Interruption Hours / Total Customers Served
        total_customer_hours = interruption_data['total_interruption_hours'] * avg_customers_per_feeder
        saidi = total_customer_hours / total_customers
        
        return {
            'saifi': Decimal(str(round(float(saifi), 4))),
            'saidi': Decimal(str(round(float(saidi), 4)))
        }
    
    def calculate_all_metrics(self):
        """Calculate all technical metrics for the summary"""
        start_time = timezone.now()
        
        try:
            energy_metrics = self.calculate_energy_metrics()
            load_metrics = self.calculate_load_metrics()
            supply_metrics = self.calculate_supply_hours()
            interruption_metrics = self.calculate_interruption_metrics()
            infrastructure_metrics = self.calculate_infrastructure_metrics()
            
            reliability_metrics = self.calculate_reliability_indices(
                interruption_metrics, 
                infrastructure_metrics
            )
            
            # Combine all metrics
            all_metrics = {
                **energy_metrics,
                **load_metrics,
                **supply_metrics,
                **interruption_metrics['summary_breakdown'],
                **infrastructure_metrics,
                **reliability_metrics,
                'total_interruptions': interruption_metrics['total_interruptions'],
                'avg_daily_interruptions': interruption_metrics['avg_daily_interruptions'],
                'avg_interruption_duration': interruption_metrics['avg_interruption_duration'],
                'total_interruption_hours': interruption_metrics['total_interruption_hours'],
                'avg_turnaround_time': interruption_metrics['avg_turnaround_time'],
                'avg_fault_turnaround_time': interruption_metrics['avg_fault_turnaround_time'],
                'interruption_breakdown_json': interruption_metrics['interruption_breakdown'],
            }
            
            # Add metadata
            calculation_time = timezone.now() - start_time
            all_metrics.update({
                'calculation_duration': calculation_time,
                'has_complete_data': self._check_data_completeness(all_metrics)
            })
            
            return all_metrics
            
        except Exception as e:
            logger.error(f"Error calculating technical metrics: {str(e)}")
            raise
    
    def _check_data_completeness(self, metrics):
        """Check if calculated metrics indicate complete data"""
        # Basic completeness check
        key_metrics = [
            'total_energy_delivered',
            'avg_hours_of_supply', 
            'active_feeder_count'
        ]
        
        for metric in key_metrics:
            if metrics.get(metric, 0) == 0:
                return False
        
        return True