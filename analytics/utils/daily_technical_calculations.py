# analytics/utils/daily_technical_calculations.py
from django.db.models import Sum, Avg, Count, Max, Q
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal
import logging

from technical.models import (
    FeederEnergyDaily, HourlyLoad, 
    FeederInterruption, DailyHoursOfSupply
)
from common.models import Feeder, BusinessDistrict, State
from commercial.models import Customer

logger = logging.getLogger(__name__)

class DailyTechnicalCalculator:
    """
    Handles daily technical metric calculations with proper filtering support.
    Specialized for single-day calculations.
    """
    
    def __init__(self, target_date, state=None, business_district=None, feeder=None):
        self.target_date = target_date
        self.state = state
        self.business_district = business_district
        self.feeder = feeder
        
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
        """Calculate daily energy delivery metrics"""
        daily_energy = FeederEnergyDaily.objects.filter(
            feeder__in=self.feeders,
            date=self.target_date
        ).aggregate(
            total=Sum('energy_mwh')
        )['total'] or Decimal('0')
        
        return {
            'total_energy_delivered': daily_energy
        }
    
    def calculate_load_metrics(self):
        """Calculate load-related metrics for the day"""
        # Get hourly load data for the target date
        hourly_loads = HourlyLoad.objects.filter(
            feeder__in=self.feeders,
            date=self.target_date
        )
        
        if not hourly_loads.exists():
            return {
                'avg_peak_load': Decimal('0'),
                'max_peak_load': Decimal('0')
            }
        
        # Calculate daily peak loads per feeder
        daily_peaks = hourly_loads.values('feeder').annotate(
            daily_peak=Max('load_mw')
        )
        
        # Average of daily peaks across all feeders
        avg_peak = daily_peaks.aggregate(
            avg=Avg('daily_peak')
        )['avg'] or Decimal('0')
        
        # Maximum peak across all feeders
        max_peak = daily_peaks.aggregate(
            max=Max('daily_peak')
        )['max'] or Decimal('0')
        
        return {
            'avg_peak_load': avg_peak,
            'max_peak_load': max_peak
        }
    
    def calculate_supply_hours(self):
        """Calculate hours of supply metrics for the day"""
        # Method 1: Use DailyHoursOfSupply if available
        daily_supply = DailyHoursOfSupply.objects.filter(
            feeder__in=self.feeders,
            date=self.target_date
        )
        
        if daily_supply.exists():
            # Average hours supplied across all feeders
            avg_hours = daily_supply.aggregate(
                avg=Avg('hours_supplied')
            )['avg'] or Decimal('0')
            
            total_hours = daily_supply.aggregate(
                total=Sum('hours_supplied')
            )['total'] or Decimal('0')
        else:
            # Method 2: Calculate from hourly load data
            # Count hours where load > 0 for each feeder
            hourly_data = HourlyLoad.objects.filter(
                feeder__in=self.feeders,
                date=self.target_date,
                load_mw__gt=0
            ).values('feeder').annotate(
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
            'hours_of_supply': avg_hours,  # For daily, we use individual day hours
            'total_supply_hours': total_hours
        }
    
    def calculate_interruption_metrics(self):
        """
        Calculate interruption-related metrics for a SINGLE day.
        
        Key distinctions:
        - Interruption Duration: ALL interruptions (including L/S and TCN)
        - Turnaround Time: ONLY local faults (excludes L/S and TCN issues)
        
        For daily calculations, we use end-of-day as reference time to ensure
        consistent historical analysis.
        """
        from technical.models import calculate_interruption_metrics as calc_metrics
        
        # Get interruptions that occurred on this specific day
        # Use explicit time range to handle timezone properly
        start_of_day = datetime.combine(self.target_date, datetime.min.time())
        end_of_day = datetime.combine(self.target_date, datetime.max.time())
        
        # Make timezone aware
        start_of_day = timezone.make_aware(start_of_day) if timezone.is_naive(start_of_day) else start_of_day
        end_of_day = timezone.make_aware(end_of_day) if timezone.is_naive(end_of_day) else end_of_day
        
        interruptions = FeederInterruption.objects.filter(
            feeder__in=self.feeders,
            occurred_at__gte=start_of_day,
            occurred_at__lte=end_of_day
        )
        
        total_interruptions = interruptions.count()
        
        if total_interruptions == 0:
            return {
                'total_interruptions': 0,
                'avg_interruption_duration': Decimal('0'),
                'total_interruption_hours': Decimal('0'),
                'avg_turnaround_time': Decimal('0'),
                'avg_fault_turnaround_time': Decimal('0'),
                'interruption_breakdown': {},
                'summary_breakdown': {
                    'load_shedding_hours': Decimal('0'),
                    'equipment_fault_hours': Decimal('0'),
                    'line_fault_hours': Decimal('0'),
                    'maintenance_hours': Decimal('0'),
                    'other_fault_hours': Decimal('0'),
                }
            }
        
        # Use utility function for comprehensive metrics
        # Use end of day as reference time for consistent historical analysis
        metrics = calc_metrics(interruptions, reference_time=end_of_day)
        
        # ========================================================================
        # Build detailed interruption breakdown
        # ========================================================================
        
        interruption_breakdown = {
            k: v['duration'] 
            for k, v in metrics['breakdown_by_type'].items()
        }
        
        # ========================================================================
        # Build summary breakdown - categorize into business groups
        # ========================================================================
        
        summary_breakdown = {
            'load_shedding_hours': Decimal(str(metrics['load_shedding_hours'])),
            'equipment_fault_hours': Decimal('0'),
            'line_fault_hours': Decimal('0'),
            'maintenance_hours': Decimal('0'),
            'other_fault_hours': Decimal(str(metrics['fault_hours'])),
        }
        
        # Enhanced categorization of fault types
        for fault_type, data in metrics['breakdown_by_type'].items():
            duration = Decimal(str(data['duration']))
            
            # Skip load shedding (already counted)
            if fault_type in ['L/S', 'L/S GS', '330KV L/S', 'T/LS']:
                continue
            
            # Categorize maintenance
            if any(term in fault_type for term in ['MTCE', 'MTNC', 'permit']):
                summary_breakdown['maintenance_hours'] += duration
                summary_breakdown['other_fault_hours'] -= duration
            
            # Categorize equipment faults
            elif any(term in fault_type for term in [
                'T/F', 'CB/F', 'B/F', 'O/S', 'E/F', 'O/C', 'S/C', 
                'O/N', 'O/E', 'P/O', 'O/F', 'P/M', 'T/S', 'EM/D', 
                'D/C', 'IN O/C', 'LIM', '132KV E/F', '132KV CB/F'
            ]):
                summary_breakdown['equipment_fault_hours'] += duration
                summary_breakdown['other_fault_hours'] -= duration
            
            # Categorize line faults
            elif any(term in fault_type for term in ['L/F', '330KV L/F', '132KV L/F']):
                summary_breakdown['line_fault_hours'] += duration
                summary_breakdown['other_fault_hours'] -= duration
            
            # Everything else (including TCN) stays in "other"
        
        # Ensure no negative values
        for key in summary_breakdown:
            if summary_breakdown[key] < 0:
                summary_breakdown[key] = Decimal('0')
        
        # ========================================================================
        # Return daily metrics
        # ========================================================================
        
        return {
            'total_interruptions': total_interruptions,
            'avg_interruption_duration': Decimal(str(metrics['avg_duration_hours'])),
            'total_interruption_hours': Decimal(str(metrics['total_duration_hours'])),
            'avg_turnaround_time': Decimal(str(metrics['avg_turnaround_time'])),  # Local faults only
            'avg_fault_turnaround_time': Decimal(str(metrics['avg_turnaround_time'])),  # Alias
            'interruption_breakdown': interruption_breakdown,
            'summary_breakdown': summary_breakdown
        }
    
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
        """Calculate SAIFI and SAIDI reliability indices for the day"""
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
        """Calculate all daily technical metrics"""
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
            logger.error(f"Error calculating daily technical metrics for {self.target_date}: {str(e)}")
            raise
    
    def _check_data_completeness(self, metrics):
        """Check if calculated metrics indicate complete data"""
        # Basic completeness check for daily data
        key_metrics = [
            'total_energy_delivered',
            'hours_of_supply', 
            'active_feeder_count'
        ]
        
        for metric in key_metrics:
            if metrics.get(metric, 0) == 0:
                return False
        
        return True