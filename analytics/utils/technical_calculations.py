# analytics/utils/technical_calculations.py
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Max, Q, Sum
from django.utils import timezone

from commercial.models import Customer
from common.models import BusinessDistrict, Feeder, State
from technical.models import (
    DailyHoursOfSupply,
    EnergyDelivered,
    FeederEnergyDaily,
    FeederEnergyMonthly,
    FeederInterruption,
    HourlyLoad,
)

logger = logging.getLogger(__name__)

class TechnicalCalculator:
    """
    Handles all technical metric calculations with proper filtering support.
    Corrected calculations based on domain knowledge and best practices.
    """
    
    def __init__(self, month_date, state=None, business_district=None, feeder=None, feeder_type=None):
        self.month_date = month_date
        self.state = state
        self.business_district = business_district
        self.feeder = feeder
        self.feeder_type = feeder_type
        
        # Calculate month boundaries
        self.start_date = month_date.replace(day=1)
        if month_date.month == 12:
            self.end_date = month_date.replace(year=month_date.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            self.end_date = month_date.replace(month=month_date.month + 1, day=1) - timedelta(days=1)
        
        # Get filtered feeder queryset
        self.feeders = self._get_filtered_feeders()
        
    def _get_filtered_feeders(self):
        """Get feeders based on filtering criteria - ONLY ONOARDED AND ACTIVE"""
        qs = Feeder.objects.filter(is_onboarded=True, status='active')
        
        if self.feeder_type:
            qs = qs.filter(voltage_level=self.feeder_type)
            
        if self.feeder:
            return qs.filter(id=self.feeder.id)
        elif self.business_district:
            return qs.filter(business_district=self.business_district)
        elif self.state:
            return qs.filter(business_district__state=self.state)
        else:
            return qs
    
    def calculate_energy_metrics(self):
        """
        Calculate energy delivery metrics with high-fidelity validation.
        PRIORITY: Meter Readings -> System Estimate Fallback.
        
        Validation:
        1. Voltage-specific Ballooning Limit.
        2. System Estimate Cross-Check (Reading vs Load x Hours).
        """
        # Threshold for "ballooning" - more restrictive for 11kV
        if self.feeder_type == '11kv':
            BALLOON_LIMIT = Decimal('500.00')
        else:
            BALLOON_LIMIT = Decimal('1000.00')
            
        energy_qs = EnergyDelivered.objects.filter(
            feeder__in=self.feeders,
            date__range=(self.start_date, self.end_date),
            energy_mwh__lt=BALLOON_LIMIT
        )
        
        total_reading_energy = energy_qs.aggregate(
            total=Sum('energy_mwh')
        )['total'] or Decimal('0')
        
        # Calculate System Estimate for cross-check
        load_metrics = self.calculate_load_metrics()
        supply_metrics = self.calculate_supply_hours()
        avg_load = float(load_metrics.get('_avg_load_internal', 0))
        supply_hours = float(supply_metrics.get('total_supply_hours', 0))
        system_estimate = Decimal(str(round(avg_load * supply_hours, 2)))
        
        # Cross-Check Logic:
        # 1. Inflation Check: If reading > 3x estimate, readings are likely cumulative.
        # 2. Coverage Check: If reading < 0.5x estimate, readings are likely missing for many feeders.
        is_suspect = False
        reason = ""
        if system_estimate > 10:
            if total_reading_energy > (system_estimate * 3):
                is_suspect = True
                reason = "Inflation (Readings > 3x Estimate)"
            elif total_reading_energy < (system_estimate * 0.5) and total_reading_energy > 0:
                is_suspect = True
                reason = "Partial Coverage (Readings < 0.5x Estimate)"
        
        if is_suspect:
             logger.warning(
                 f"Suspect energy reading: {total_reading_energy} MWh vs Estimate {system_estimate} MWh. "
                 f"Reason: {reason}. Falling back to system estimate."
             )
             return {
                 'total_energy_delivered': system_estimate,
                 'energy_source': 'system',
                 'is_verified': False,
                 'reading_estimate_variance': total_reading_energy - system_estimate
             }

        if total_reading_energy > 0:
            return {
                'total_energy_delivered': total_reading_energy,
                'energy_source': 'meter_reading',
                'is_verified': True,
                'reading_estimate_variance': total_reading_energy - system_estimate
            }
        
        # Standard Fallback
        return {
            'total_energy_delivered': system_estimate,
            'energy_source': 'system',
            'is_verified': False,
            'reading_estimate_variance': Decimal('0')
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
                'avg_load': Decimal('0'),
                'avg_peak_load': Decimal('0'),
                'max_peak_load': Decimal('0')
            }
        
        # Calculate true average load across all hourly readings (for energy calculation)
        # Only count hours where load > 0 (power was flowing)
        positive_loads = hourly_loads.filter(load_mw__gt=0)
        avg_load = positive_loads.aggregate(
            avg=Avg('load_mw')
        )['avg'] or Decimal('0')
        
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
            'max_peak_load': max_peak,
            '_avg_load_internal': avg_load  # internal use for energy calc
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
        """
        Calculate interruption-related metrics with CORRECT monthly averaging.
        
        CRITICAL: For monthly summaries, we calculate DAILY averages first,
        then average those to get the monthly average. This ensures values
        stay realistic (< 24 hours for duration).
        
        Returns metrics for:
        - All interruptions (duration)
        - Local faults only (turnaround time - excludes L/S and TCN)
        """
        import calendar

        from technical.models import calculate_interruption_metrics as calc_metrics

        # Get all interruptions for the month - use explicit datetime range
        start_of_month = datetime.combine(self.start_date, datetime.min.time())
        end_of_month_date = self.end_date
        end_of_month = datetime.combine(end_of_month_date, datetime.max.time())
        
        # Make timezone aware
        start_of_month = timezone.make_aware(start_of_month) if timezone.is_naive(start_of_month) else start_of_month
        end_of_month = timezone.make_aware(end_of_month) if timezone.is_naive(end_of_month) else end_of_month
        
        interruptions = FeederInterruption.objects.filter(
            feeder__in=self.feeders,
            occurred_at__gte=start_of_month,
            occurred_at__lte=end_of_month
        )
        
        total_interruptions = interruptions.count()
        days_in_month = (self.end_date - self.start_date).days + 1
        
        if total_interruptions == 0:
            return {
                'total_interruptions': 0,
                'avg_daily_interruptions': Decimal('0'),
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
        
        # ========================================================================
        # STEP 1: Calculate DAILY metrics and collect them
        # This is CRITICAL to avoid > 24 hour averages
        # ========================================================================
        
        daily_durations = []
        daily_turnaround_times = []
        days_with_data = 0
        
        current_date = self.start_date
        while current_date <= self.end_date:
            # Get interruptions that occurred on this specific day
            # Use date range to handle timezone properly
            start_of_day = datetime.combine(current_date, datetime.min.time())
            end_of_day = datetime.combine(current_date, datetime.max.time())
            
            # Make timezone aware
            start_of_day = timezone.make_aware(start_of_day) if timezone.is_naive(start_of_day) else start_of_day
            end_of_day = timezone.make_aware(end_of_day) if timezone.is_naive(end_of_day) else end_of_day
            
            daily_interruptions = interruptions.filter(
                occurred_at__gte=start_of_day,
                occurred_at__lte=end_of_day
            )
            
            if daily_interruptions.exists():
                # Calculate metrics for this day
                daily_metrics = calc_metrics(daily_interruptions, reference_time=end_of_day)
                
                # Collect daily averages
                daily_durations.append(daily_metrics['avg_duration_hours'])
                daily_turnaround_times.append(daily_metrics['avg_turnaround_time'])
                days_with_data += 1
            
            current_date += timedelta(days=1)
        
        # ========================================================================
        # STEP 2: Calculate monthly averages FROM daily averages
        # ========================================================================
        
        # Average of daily averages (this keeps values realistic)
        avg_interruption_duration = (
            Decimal(str(sum(daily_durations) / len(daily_durations)))
            if daily_durations else Decimal('0')
        )
        
        avg_turnaround_time = (
            Decimal(str(sum(daily_turnaround_times) / len(daily_turnaround_times)))
            if daily_turnaround_times else Decimal('0')
        )
        
        # ========================================================================
        # STEP 3: Calculate monthly TOTALS using end-of-month reference
        # ========================================================================
        
        days_in_month_actual = calendar.monthrange(self.month_date.year, self.month_date.month)[1]
        end_of_month_final = datetime(
            self.month_date.year, 
            self.month_date.month, 
            days_in_month_actual, 
            23, 59, 59
        )
        end_of_month_final = timezone.make_aware(end_of_month_final) if timezone.is_naive(end_of_month_final) else end_of_month_final
        
        # Get monthly totals
        monthly_metrics = calc_metrics(interruptions, reference_time=end_of_month_final)
        
        # ========================================================================
        # STEP 4: Build detailed and summary breakdowns
        # ========================================================================
        
        # Detailed breakdown from monthly metrics
        interruption_breakdown = {
            k: v['duration'] 
            for k, v in monthly_metrics['breakdown_by_type'].items()
        }
        
        # Summary breakdown - categorize into business groups
        summary_breakdown = {
            'load_shedding_hours': Decimal(str(monthly_metrics['load_shedding_hours'])),
            'equipment_fault_hours': Decimal('0'),
            'line_fault_hours': Decimal('0'),
            'maintenance_hours': Decimal('0'),
            'other_fault_hours': Decimal(str(monthly_metrics['fault_hours'])),
        }
        
        # Enhanced categorization
        for fault_type, data in monthly_metrics['breakdown_by_type'].items():
            duration = Decimal(str(data['duration']))
            
            # Skip load shedding (already counted)
            if fault_type in ['L/S', 'L/S GS', '330KV L/S', 'T/LS']:
                continue
            
            # Categorize maintenance
            if any(term in fault_type for term in ['MTCE', 'MTNC', 'permit']):
                summary_breakdown['maintenance_hours'] += duration
                summary_breakdown['other_fault_hours'] -= duration
            
            # Categorize equipment faults
            elif any(term in fault_type for term in ['T/F', 'CB/F', 'B/F', 'O/S', 'E/F', 'O/C', 'S/C', 'O/N', 'O/E', 'P/O', 'O/F', 'P/M', 'T/S', 'EM/D', 'D/C', 'IN O/C', 'LIM']):
                summary_breakdown['equipment_fault_hours'] += duration
                summary_breakdown['other_fault_hours'] -= duration
            
            # Categorize line faults
            elif any(term in fault_type for term in ['L/F', '330KV L/F', '132KV L/F']):
                summary_breakdown['line_fault_hours'] += duration
                summary_breakdown['other_fault_hours'] -= duration
            
            # TCN faults stay in "other"
        
        # Ensure no negative values
        for key in summary_breakdown:
            if summary_breakdown[key] < 0:
                summary_breakdown[key] = Decimal('0')
        
        # ========================================================================
        # STEP 5: Return all metrics
        # ========================================================================
        
        return {
            'total_interruptions': total_interruptions,
            'avg_daily_interruptions': Decimal(total_interruptions) / Decimal(days_in_month),
            'avg_interruption_duration': avg_interruption_duration,  # DAILY average (< 24)
            'total_interruption_hours': Decimal(str(monthly_metrics['total_duration_hours'])),  # Monthly total
            'avg_turnaround_time': avg_turnaround_time,  # DAILY average for local faults
            'avg_fault_turnaround_time': avg_turnaround_time,  # Alias for compatibility
            'turnaround_count': monthly_metrics['turnaround_count'],
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
            
            # =====================================================================
            # Energy Delivered Logic: Integrated Reading-Priority & Cross-Check
            # =====================================================================
            final_energy = energy_metrics['total_energy_delivered']
            energy_source = energy_metrics['energy_source']
            
            # Combine all metrics
            all_metrics = {
                'total_energy_delivered': final_energy,
                'energy_source': energy_source,
                'avg_peak_load': load_metrics['avg_peak_load'],
                'max_peak_load': load_metrics['max_peak_load'],
                'avg_hours_of_supply': supply_metrics.get('avg_hours_of_supply', Decimal('0')),
                'total_supply_hours': supply_metrics.get('total_supply_hours', Decimal('0')),
                'total_interruptions': interruption_metrics['total_interruptions'],
                'avg_daily_interruptions': interruption_metrics['avg_daily_interruptions'],
                'avg_interruption_duration': interruption_metrics['avg_interruption_duration'],
                'total_interruption_hours': interruption_metrics['total_interruption_hours'],
                'avg_turnaround_time': interruption_metrics['avg_turnaround_time'],
                'avg_fault_turnaround_time': interruption_metrics['avg_fault_turnaround_time'],
                'interruption_breakdown_json': interruption_metrics['interruption_breakdown'],
                **interruption_metrics['summary_breakdown'],
                **infrastructure_metrics,
                **reliability_metrics,
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