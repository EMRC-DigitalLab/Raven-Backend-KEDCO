# technical/scripts/club_substation_report.py
"""
Management Report Generator for CLUB Injection Substation (KEDCO)
Generates comprehensive technical performance data and insights for management reporting
"""

import json
import os
import sys
from datetime import datetime, timedelta

import django
from dateutil.relativedelta import relativedelta
from django.db.models import Avg, Count, F, Max, Min, Q, Sum
from django.utils import timezone

# Add the parent directory to sys.path to import Django modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure Django settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'raven.settings')
django.setup()

from analytics.models import DailyTechnicalSummary, MonthlyTechnicalSummary
from commercial.models import Customer
from common.models import Feeder, InjectionSubstation
from technical.models import (
    DailyHoursOfSupply,
    FeederEnergyDaily,
    FeederEnergyMonthly,
    FeederInterruption,
    HourlyLoad,
    calculate_interruption_metrics,
)


class CLUBSubstationReportGenerator:
    """Generate comprehensive technical report for CLUB substation"""
    
    def __init__(self):
        self.substation_id = "0052cf6e-15ef-4de2-9c05-5895875ef791"
        self.substation_slug = "KN-CLU"
        self.substation_name = "CLUB"
        self.report_date = datetime.now().date()
        
        # KEDCO deployment timeline
        self.deployment_start = datetime(2025, 8, 9).date()  # Raven deployment start
        self.current_date = datetime(2025, 9, 25).date()     # Today's date
        self.historical_data_available = True                # Complete 2024 data available
        
        # Calculate deployment duration
        self.days_since_deployment = (self.current_date - self.deployment_start).days + 1
        
        # Get substation and feeders
        try:
            self.substation = InjectionSubstation.objects.get(id=self.substation_id)
            self.feeders = Feeder.objects.filter(substation=self.substation)
            self.feeder_ids = list(self.feeders.values_list('id', flat=True))
            print(f"Found {len(self.feeder_ids)} feeders under {self.substation_name} substation")
            print(f"Deployment period: {self.deployment_start} to {self.current_date} ({self.days_since_deployment} days)")
        except InjectionSubstation.DoesNotExist:
            raise ValueError(f"Substation with ID {self.substation_id} not found")
    
    def get_date_ranges(self):
        """Generate date ranges based on actual deployment timeline and available data"""
        today = self.current_date
        deployment_start = self.deployment_start
        
        # 2025 ranges (post-deployment only)
        august_2025_start = max(deployment_start, datetime(2025, 8, 1).date())
        august_2025_end = datetime(2025, 8, 31).date()
        
        september_2025_start = datetime(2025, 9, 1).date()
        september_2025_end = min(today, datetime(2025, 9, 30).date())
        
        # 2024 ranges (complete historical data)
        august_2024 = (datetime(2024, 8, 1).date(), datetime(2024, 8, 31).date())
        september_2024 = (datetime(2024, 9, 1).date(), datetime(2024, 9, 30).date())
        
        ranges = {
            # Current deployment period analysis
            'since_deployment': (deployment_start, today),
            'deployment_august_portion': (august_2025_start, august_2025_end),
            'september_2025': (september_2025_start, september_2025_end),
            
            # Recent periods
            'last_7_days': (today - timedelta(days=6), today),
            'last_14_days': (today - timedelta(days=13), today),
            'last_30_days': (max(deployment_start, today - timedelta(days=29)), today),
            
            # Historical comparison periods (2024)
            'august_2024': august_2024,
            'september_2024_partial': (september_2024[0], datetime(2024, 9, 25).date()),  # Same period last year
            'august_september_2024': (august_2024[0], september_2024[1]),
            
            # Full 2024 for baseline
            'full_year_2024': (datetime(2024, 1, 1).date(), datetime(2024, 12, 31).date()),
            'q3_2024': (datetime(2024, 7, 1).date(), datetime(2024, 9, 30).date()),
        }
        
        # Add weekly breakdown of deployment period
        current_week_start = today - timedelta(days=today.weekday())
        if current_week_start >= deployment_start:
            ranges['current_week'] = (current_week_start, today)
        
        previous_week_start = current_week_start - timedelta(days=7)
        previous_week_end = current_week_start - timedelta(days=1)
        if previous_week_start >= deployment_start:
            ranges['previous_week'] = (previous_week_start, previous_week_end)
        
        return ranges
    
    def _get_last_month_range(self, today):
        """Get first and last day of previous month"""
        first_this_month = today.replace(day=1)
        last_month = first_this_month - timedelta(days=1)
        first_last_month = last_month.replace(day=1)
        return (first_last_month, last_month)
    
    def _get_last_year_same_period(self, today):
        """Get same period last year for comparison"""
        try:
            year_start = today.replace(year=today.year - 1, month=1, day=1)
            year_end = today.replace(year=today.year - 1)
            return (year_start, year_end)
        except ValueError:
            # Handle leap year edge case
            year_end = today.replace(year=today.year - 1, day=28)
            year_start = year_end.replace(month=1, day=1)
            return (year_start, year_end)
    
    def calculate_delta(self, current, previous):
        """Calculate percentage change between current and previous values"""
        if previous == 0:
            return 100 if current > 0 else 0
        return round(((current - previous) / previous) * 100, 2)
    
    def get_infrastructure_overview(self):
        """Get basic infrastructure information"""
        print("Generating infrastructure overview...")
        
        feeder_details = []
        for feeder in self.feeders:
            # Get transformer count per feeder
            transformer_count = feeder.transformers.count()
            
            # Get customer count per feeder (if available)
            customer_count = Customer.objects.filter(
                transformer__feeder=feeder
            ).count() if hasattr(Customer, 'transformer') else 0
            
            feeder_details.append({
                'name': feeder.name,
                'slug': feeder.slug,
                'voltage_level': feeder.voltage_level,
                'band': feeder.band.name if feeder.band else 'Unassigned',
                'business_district': feeder.business_district.name if feeder.business_district else 'Unassigned',
                'transformer_count': transformer_count,
                'customer_count': customer_count
            })
        
        total_transformers = sum(f['transformer_count'] for f in feeder_details)
        total_customers = sum(f['customer_count'] for f in feeder_details)
        
        return {
            'substation_name': self.substation_name,
            'substation_slug': self.substation_slug,
            'total_feeders': len(self.feeders),
            'total_transformers': total_transformers,
            'total_customers': total_customers,
            'feeder_breakdown': {
                '11kv_feeders': len([f for f in feeder_details if f['voltage_level'] == '11kv']),
                '33kv_feeders': len([f for f in feeder_details if f['voltage_level'] == '33kv'])
            },
            'band_distribution': self._get_band_distribution(feeder_details),
            'feeder_details': feeder_details
        }
    
    def _get_band_distribution(self, feeder_details):
        """Get distribution of feeders by band"""
        bands = {}
        for feeder in feeder_details:
            band = feeder['band']
            bands[band] = bands.get(band, 0) + 1
        return bands
    
    def get_performance_metrics(self):
        """Get comprehensive performance metrics for various time periods"""
        print("Calculating performance metrics...")
        
        date_ranges = self.get_date_ranges()
        metrics = {}
        
        for period_name, (start_date, end_date) in date_ranges.items():
            print(f"Processing {period_name}: {start_date} to {end_date}")
            
            period_metrics = self._calculate_period_metrics(start_date, end_date)
            metrics[period_name] = period_metrics
        
        # Calculate key comparisons
        comparisons = self._calculate_comparisons(metrics)
        
        return {
            'metrics_by_period': metrics,
            'key_comparisons': comparisons
        }
    
    def _calculate_period_metrics(self, start_date, end_date):
        """Calculate metrics for a specific period"""
        
        # Hours of Supply
        avg_supply = self._calculate_avg_supply_hours(start_date, end_date)
        
        # Load Metrics
        load_metrics = self._calculate_load_metrics(start_date, end_date)
        
        # Energy Delivered
        energy_delivered = self._calculate_energy_delivered(start_date, end_date)
        
        # Interruption Metrics
        interruption_metrics = self._calculate_interruption_metrics(start_date, end_date)
        
        # Reliability Indices
        reliability_indices = self._calculate_reliability_indices(
            start_date, end_date, avg_supply, interruption_metrics
        )
        
        return {
            'period': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'days': (end_date - start_date).days + 1
            },
            'supply_hours': avg_supply,
            'load_metrics': load_metrics,
            'energy_delivered': energy_delivered,
            'interruptions': interruption_metrics,
            'reliability': reliability_indices
        }
    
    def _calculate_avg_supply_hours(self, start_date, end_date):
        """Calculate average supply hours"""
        # Try DailyHoursOfSupply first
        daily_supply = DailyHoursOfSupply.objects.filter(
            feeder_id__in=self.feeder_ids,
            date__range=(start_date, end_date)
        ).aggregate(
            avg_hours=Avg('hours_supplied'),
            max_hours=Max('hours_supplied'),
            min_hours=Min('hours_supplied')
        )
        
        if daily_supply['avg_hours'] is not None:
            return {
                'average': round(float(daily_supply['avg_hours']), 2),
                'maximum': round(float(daily_supply['max_hours']), 2),
                'minimum': round(float(daily_supply['min_hours']), 2)
            }
        
        # Fallback to HourlyLoad calculation
        hourly_supply = HourlyLoad.objects.filter(
            feeder_id__in=self.feeder_ids,
            date__range=(start_date, end_date),
            load_mw__gt=0
        ).values('feeder', 'date').annotate(
            hours_count=Count('hour')
        ).aggregate(
            avg_hours=Avg('hours_count'),
            max_hours=Max('hours_count'),
            min_hours=Min('hours_count')
        )
        
        return {
            'average': round(float(hourly_supply['avg_hours'] or 0), 2),
            'maximum': round(float(hourly_supply['max_hours'] or 0), 2),
            'minimum': round(float(hourly_supply['min_hours'] or 0), 2)
        }
    
    def _calculate_load_metrics(self, start_date, end_date):
        """Calculate load-related metrics"""
        load_stats = HourlyLoad.objects.filter(
            feeder_id__in=self.feeder_ids,
            date__range=(start_date, end_date)
        ).aggregate(
            avg_load=Avg('load_mw'),
            max_load=Max('load_mw'),
            min_load=Min('load_mw'),
            total_records=Count('id')
        )
        
        # Peak load by feeder
        peak_loads_by_feeder = HourlyLoad.objects.filter(
            feeder_id__in=self.feeder_ids,
            date__range=(start_date, end_date)
        ).values(
            'feeder__name', 'feeder__voltage_level'
        ).annotate(
            peak_load=Max('load_mw')
        ).order_by('-peak_load')
        
        return {
            'average_load_mw': round(float(load_stats['avg_load'] or 0), 2),
            'peak_load_mw': round(float(load_stats['max_load'] or 0), 2),
            'minimum_load_mw': round(float(load_stats['min_load'] or 0), 2),
            'total_load_records': load_stats['total_records'],
            'peak_loads_by_feeder': [
                {
                    'feeder': item['feeder__name'],
                    'voltage_level': item['feeder__voltage_level'],
                    'peak_load_mw': round(float(item['peak_load'] or 0), 2)
                }
                for item in peak_loads_by_feeder[:10]  # Top 10
            ]
        }
    
    def _calculate_energy_delivered(self, start_date, end_date):
        """Calculate energy delivered metrics"""
        # Try monthly data first if period spans full months
        if start_date.day == 1 and end_date == (start_date + relativedelta(months=1) - timedelta(days=1)):
            monthly_energy = FeederEnergyMonthly.objects.filter(
                feeder_id__in=self.feeder_ids,
                period=start_date
            ).aggregate(
                total_energy=Sum('energy_mwh')
            )
            
            if monthly_energy['total_energy'] is not None:
                return {
                    'total_mwh': round(float(monthly_energy['total_energy']), 2),
                    'data_source': 'monthly_aggregation'
                }
        
        # Use daily data
        daily_energy = FeederEnergyDaily.objects.filter(
            feeder_id__in=self.feeder_ids,
            date__range=(start_date, end_date)
        ).aggregate(
            total_energy=Sum('energy_mwh'),
            avg_daily=Avg('energy_mwh')
        )
        
        return {
            'total_mwh': round(float(daily_energy['total_energy'] or 0), 2),
            'average_daily_mwh': round(float(daily_energy['avg_daily'] or 0), 2),
            'data_source': 'daily_aggregation'
        }
    
    def _calculate_interruption_metrics(self, start_date, end_date):
        """Calculate detailed interruption metrics"""
        interruptions_qs = FeederInterruption.objects.filter(
            feeder_id__in=self.feeder_ids,
            occurred_at__date__range=(start_date, end_date)
        )
        
        # Use the utility function from models
        metrics = calculate_interruption_metrics(interruptions_qs)
        
        # Add feeder-specific breakdown
        feeder_breakdown = {}
        for feeder in self.feeders:
            feeder_interruptions = interruptions_qs.filter(feeder=feeder)
            feeder_metrics = calculate_interruption_metrics(feeder_interruptions)
            feeder_breakdown[feeder.name] = feeder_metrics
        
        # Add daily trend
        daily_trend = self._get_daily_interruption_trend(start_date, end_date)
        
        return {
            **metrics,
            'feeder_breakdown': feeder_breakdown,
            'daily_trend': daily_trend
        }
    
    def _get_daily_interruption_trend(self, start_date, end_date):
        """Get daily interruption counts for trend analysis"""
        daily_counts = FeederInterruption.objects.filter(
            feeder_id__in=self.feeder_ids,
            occurred_at__date__range=(start_date, end_date)
        ).extra(
            select={'day': 'DATE(occurred_at)'}
        ).values('day').annotate(
            count=Count('id')
        ).order_by('day')
        
        return [
            {
                'date': item['day'],
                'interruption_count': item['count']
            }
            for item in daily_counts
        ]
    
    def _calculate_reliability_indices(self, start_date, end_date, supply_metrics, interruption_metrics):
        """Calculate standard reliability indices"""
        days_in_period = (end_date - start_date).days + 1
        
        # System Average Interruption Frequency Index (SAIFI)
        total_customers = sum(
            Customer.objects.filter(transformer__feeder=feeder).count() 
            for feeder in self.feeders
        ) if hasattr(Customer, 'transformer') else 1
        
        saifi = interruption_metrics['total_interruptions'] / total_customers if total_customers > 0 else 0
        
        # System Average Interruption Duration Index (SAIDI)
        saidi = interruption_metrics['total_duration_hours'] / total_customers if total_customers > 0 else 0
        
        # Customer Average Interruption Duration Index (CAIDI)
        caidi = (interruption_metrics['total_duration_hours'] / interruption_metrics['total_interruptions'] 
                if interruption_metrics['total_interruptions'] > 0 else 0)
        
        # Availability percentage (based on supply hours)
        availability = (supply_metrics['average'] / 24) * 100 if supply_metrics['average'] else 0
        
        return {
            'saifi': round(saifi, 4),
            'saidi': round(saidi, 4),
            'caidi': round(caidi, 4),
            'availability_percentage': round(availability, 2),
            'total_customer_base': total_customers
        }
    
    def _calculate_comparisons(self, metrics):
        """Calculate meaningful comparisons based on deployment timeline and available data"""
        comparisons = {}
        
        # Deployment period performance vs 2024 baseline
        if 'since_deployment' in metrics and 'august_september_2024' in metrics:
            current = metrics['since_deployment']
            baseline_2024 = metrics['august_september_2024']
            
            comparisons['deployment_vs_2024_baseline'] = {
                'description': 'Current deployment period vs same period 2024',
                'supply_hours': {
                    'current': current['supply_hours']['average'],
                    'baseline_2024': baseline_2024['supply_hours']['average'],
                    'improvement_percent': self.calculate_delta(
                        current['supply_hours']['average'],
                        baseline_2024['supply_hours']['average']
                    )
                },
                'peak_load': {
                    'current': current['load_metrics']['peak_load_mw'],
                    'baseline_2024': baseline_2024['load_metrics']['peak_load_mw'],
                    'change_percent': self.calculate_delta(
                        current['load_metrics']['peak_load_mw'],
                        baseline_2024['load_metrics']['peak_load_mw']
                    )
                },
                'interruption_frequency': {
                    'current_daily_avg': current['interruptions']['total_interruptions'] / current['period']['days'],
                    'baseline_daily_avg': baseline_2024['interruptions']['total_interruptions'] / baseline_2024['period']['days'],
                    'change_percent': self.calculate_delta(
                        current['interruptions']['total_interruptions'] / current['period']['days'],
                        baseline_2024['interruptions']['total_interruptions'] / baseline_2024['period']['days']
                    )
                },
                'availability': {
                    'current': current['reliability']['availability_percentage'],
                    'baseline_2024': baseline_2024['reliability']['availability_percentage'],
                    'improvement_percent': self.calculate_delta(
                        current['reliability']['availability_percentage'],
                        baseline_2024['reliability']['availability_percentage']
                    )
                }
            }
        
        # Month-over-month comparison for 2025
        if 'deployment_august_portion' in metrics and 'september_2025' in metrics:
            aug_2025 = metrics['deployment_august_portion']
            sep_2025 = metrics['september_2025']
            
            comparisons['august_to_september_2025'] = {
                'description': 'August 2025 (partial) vs September 2025',
                'supply_hours': {
                    'august': aug_2025['supply_hours']['average'],
                    'september': sep_2025['supply_hours']['average'],
                    'change_percent': self.calculate_delta(
                        sep_2025['supply_hours']['average'],
                        aug_2025['supply_hours']['average']
                    )
                },
                'interruptions': {
                    'august_daily_avg': aug_2025['interruptions']['total_interruptions'] / aug_2025['period']['days'],
                    'september_daily_avg': sep_2025['interruptions']['total_interruptions'] / sep_2025['period']['days'],
                    'change_percent': self.calculate_delta(
                        sep_2025['interruptions']['total_interruptions'] / sep_2025['period']['days'],
                        aug_2025['interruptions']['total_interruptions'] / aug_2025['period']['days']
                    )
                }
            }
        
        # Year-over-year comparison for same calendar periods
        if 'august_2024' in metrics and 'deployment_august_portion' in metrics:
            aug_2024 = metrics['august_2024']
            aug_2025 = metrics['deployment_august_portion']
            
            comparisons['august_year_over_year'] = {
                'description': 'August 2025 vs August 2024 (same month comparison)',
                'supply_hours_improvement': self.calculate_delta(
                    aug_2025['supply_hours']['average'],
                    aug_2024['supply_hours']['average']
                ),
                'load_growth': self.calculate_delta(
                    aug_2025['load_metrics']['average_load_mw'],
                    aug_2024['load_metrics']['average_load_mw']
                ),
                'reliability_improvement': self.calculate_delta(
                    aug_2025['reliability']['availability_percentage'],
                    aug_2024['reliability']['availability_percentage']
                )
            }
        
        # Weekly trend analysis if we have enough data
        if 'current_week' in metrics and 'previous_week' in metrics:
            current_week = metrics['current_week']
            previous_week = metrics['previous_week']
            
            comparisons['week_over_week'] = {
                'description': 'Current week vs previous week',
                'supply_hours': {
                    'current': current_week['supply_hours']['average'],
                    'previous': previous_week['supply_hours']['average'],
                    'change_percent': self.calculate_delta(
                        current_week['supply_hours']['average'],
                        previous_week['supply_hours']['average']
                    )
                },
                'interruptions': {
                    'current': current_week['interruptions']['total_interruptions'],
                    'previous': previous_week['interruptions']['total_interruptions'],
                    'change_percent': self.calculate_delta(
                        current_week['interruptions']['total_interruptions'],
                        previous_week['interruptions']['total_interruptions']
                    )
                }
            }
        
        return comparisons
    
    def get_operational_insights(self, metrics):
        """Generate operational insights and recommendations specific to deployment period"""
        print("Generating operational insights...")
        
        insights = {
            'deployment_performance': [],
            'performance_highlights': [],
            'areas_of_concern': [],
            'recommendations': [],
            'trends': [],
            'comparative_analysis': []
        }
        
        deployment_metrics = metrics['metrics_by_period'].get('since_deployment', {})
        september_metrics = metrics['metrics_by_period'].get('september_2025', {})
        comparisons = metrics.get('key_comparisons', {})
        
        # Deployment-specific insights
        if deployment_metrics:
            deployment_days = deployment_metrics.get('period', {}).get('days', 0)
            insights['deployment_performance'].append(
                f"Raven system operational for {deployment_days} days since August 9, 2025"
            )
            
            avg_supply = deployment_metrics.get('supply_hours', {}).get('average', 0)
            availability = deployment_metrics.get('reliability', {}).get('availability_percentage', 0)
            total_interruptions = deployment_metrics.get('interruptions', {}).get('total_interruptions', 0)
            
            insights['deployment_performance'].append(
                f"Overall deployment period average: {avg_supply:.1f} hours daily supply ({availability:.1f}% availability)"
            )
            insights['deployment_performance'].append(
                f"Total interruptions recorded: {total_interruptions} ({total_interruptions/deployment_days:.1f} per day average)"
            )
        
        # Performance analysis
        if september_metrics:
            sep_supply = september_metrics.get('supply_hours', {}).get('average', 0)
            sep_availability = september_metrics.get('reliability', {}).get('availability_percentage', 0)
            
            if sep_supply >= 20:
                insights['performance_highlights'].append(
                    f"September 2025: Excellent supply reliability at {sep_supply:.1f} hours/day"
                )
            elif sep_supply >= 16:
                insights['performance_highlights'].append(
                    f"September 2025: Good supply performance at {sep_supply:.1f} hours/day"
                )
            elif sep_supply >= 12:
                insights['areas_of_concern'].append(
                    f"September 2025: Moderate supply hours at {sep_supply:.1f} hours/day - improvement needed"
                )
            else:
                insights['areas_of_concern'].append(
                    f"September 2025: Low supply hours at {sep_supply:.1f} hours/day - requires urgent attention"
                )
            
            # Interruption analysis for September
            sep_interruptions = september_metrics.get('interruptions', {})
            fault_count = sep_interruptions.get('fault_count', 0)
            load_shedding_count = sep_interruptions.get('load_shedding_count', 0)
            
            if fault_count > 0 and load_shedding_count > 0:
                fault_ratio = fault_count / (fault_count + load_shedding_count) * 100
                if fault_ratio > 60:
                    insights['areas_of_concern'].append(
                        f"High fault-related interruptions: {fault_ratio:.1f}% of total interruptions"
                    )
                    insights['recommendations'].append(
                        "Implement proactive maintenance program to reduce equipment faults"
                    )
        
        # Comparative analysis with 2024 baseline
        baseline_comparison = comparisons.get('deployment_vs_2024_baseline', {})
        if baseline_comparison:
            supply_improvement = baseline_comparison.get('supply_hours', {}).get('improvement_percent', 0)
            availability_improvement = baseline_comparison.get('availability', {}).get('improvement_percent', 0)
            
            if supply_improvement > 10:
                insights['comparative_analysis'].append(
                    f"Supply hours improved by {supply_improvement:.1f}% compared to same period in 2024"
                )
                insights['performance_highlights'].append(
                    "Significant improvement over 2024 baseline performance"
                )
            elif supply_improvement > 0:
                insights['comparative_analysis'].append(
                    f"Modest supply hours improvement of {supply_improvement:.1f}% vs 2024"
                )
            elif supply_improvement < -10:
                insights['areas_of_concern'].append(
                    f"Supply hours declined by {abs(supply_improvement):.1f}% compared to 2024"
                )
            
            if availability_improvement > 5:
                insights['performance_highlights'].append(
                    f"System availability improved by {availability_improvement:.1f}% vs 2024 baseline"
                )
        
        # Month-to-month trend analysis
        monthly_comparison = comparisons.get('august_to_september_2025', {})
        if monthly_comparison:
            supply_change = monthly_comparison.get('supply_hours', {}).get('change_percent', 0)
            if abs(supply_change) > 5:
                trend_direction = "improved" if supply_change > 0 else "declined"
                insights['trends'].append(
                    f"Supply hours {trend_direction} by {abs(supply_change):.1f}% from August to September 2025"
                )
        
        # Weekly trend analysis
        weekly_comparison = comparisons.get('week_over_week', {})
        if weekly_comparison:
            weekly_supply_change = weekly_comparison.get('supply_hours', {}).get('change_percent', 0)
            if abs(weekly_supply_change) > 10:
                trend = "improving" if weekly_supply_change > 0 else "declining"
                insights['trends'].append(
                    f"Recent weekly trend: {trend} ({weekly_supply_change:+.1f}%)"
                )
        
        # Generate specific recommendations
        if deployment_metrics:
            avg_interruption_duration = deployment_metrics.get('interruptions', {}).get('avg_duration_hours', 0)
            if avg_interruption_duration > 2:
                insights['recommendations'].append(
                    f"Average interruption duration of {avg_interruption_duration:.1f}h suggests need for faster restoration procedures"
                )
            
            peak_load = deployment_metrics.get('load_metrics', {}).get('peak_load_mw', 0)
            avg_load = deployment_metrics.get('load_metrics', {}).get('average_load_mw', 0)
            if peak_load > 0 and avg_load > 0:
                load_factor = (avg_load / peak_load) * 100
                if load_factor < 60:
                    insights['recommendations'].append(
                        f"Load factor of {load_factor:.1f}% indicates potential for demand management optimization"
                    )
        
        # Data quality and system insights
        insights['recommendations'].extend([
            "Continue monitoring system performance as deployment matures",
            "Establish benchmarks using 2024 historical data for ongoing comparisons",
            "Consider expanding Raven deployment to additional substations based on CLUB performance"
        ])
        
        return insights
    
    def generate_executive_summary(self, infrastructure, performance, insights):
        """Generate executive summary tailored to KEDCO deployment timeline"""
        deployment_metrics = performance['metrics_by_period'].get('since_deployment', {})
        september_metrics = performance['metrics_by_period'].get('september_2025', {})
        
        # Use September data if available, otherwise deployment period
        current_metrics = september_metrics if september_metrics else deployment_metrics
        
        return {
            'report_date': self.current_date.isoformat(),
            'substation': self.substation_name,
            'deployment_info': {
                'status': 'Progressive deployment - Technical module active',
                'deployment_date': self.deployment_start.isoformat(),
                'days_operational': self.days_since_deployment,
                'data_coverage': 'August 9, 2025 to September 25, 2025',
                'historical_baseline': 'Complete 2024 data available for comparison'
            },
            'infrastructure_summary': {
                'feeders': infrastructure['total_feeders'],
                'transformers': infrastructure['total_transformers'],
                'customers_served': infrastructure['total_customers'],
                'voltage_breakdown': infrastructure['feeder_breakdown']
            },
            'key_performance_indicators': {
                'period': 'Since Deployment' if not september_metrics else 'September 2025',
                'average_supply_hours': current_metrics.get('supply_hours', {}).get('average', 0),
                'system_availability': current_metrics.get('reliability', {}).get('availability_percentage', 0),
                'total_interruptions': current_metrics.get('interruptions', {}).get('total_interruptions', 0),
                'daily_interruption_rate': (current_metrics.get('interruptions', {}).get('total_interruptions', 0) / 
                                           current_metrics.get('period', {}).get('days', 1)),
                'peak_load_mw': current_metrics.get('load_metrics', {}).get('peak_load_mw', 0),
                'energy_delivered_mwh': current_metrics.get('energy_delivered', {}).get('total_mwh', 0)
            },
            'deployment_performance': insights['deployment_performance'][:3],
            'highlights': insights['performance_highlights'][:3],
            'concerns': insights['areas_of_concern'][:2],
            'recommendations': insights['recommendations'][:3],
            'comparative_insights': insights['comparative_analysis'][:2]
        } 
    
    def generate_full_report(self):
        """Generate the complete management report"""
        print(f"Generating full management report for {self.substation_name} substation...")
        print("=" * 60)
        
        try:
            # Get all report components
            infrastructure = self.get_infrastructure_overview()
            performance = self.get_performance_metrics()
            insights = self.get_operational_insights(performance)
            executive_summary = self.generate_executive_summary(infrastructure, performance, insights)
            
            # Compile full report
            full_report = {
                'executive_summary': executive_summary,
                'infrastructure_overview': infrastructure,
                'performance_metrics': performance,
                'operational_insights': insights,
                'metadata': {
                    'generated_at': datetime.now().isoformat(),
                    'generator_version': '1.0',
                    'substation_id': self.substation_id,
                    'data_sources': [
                        'HourlyLoad', 'FeederInterruption', 'DailyHoursOfSupply',
                        'FeederEnergyDaily', 'FeederEnergyMonthly'
                    ]
                }
            }
            
            print("Report generation completed successfully!")
            print("=" * 60)
            
            return full_report
            
        except Exception as e:
            print(f"Error generating report: {str(e)}")
            import traceback
            traceback.print_exc()
            raise


def main():
    """Main function to run the report generator"""
    try:
        generator = CLUBSubstationReportGenerator()
        report = generator.generate_full_report()
        
        # Save report to file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"club_substation_report_{timestamp}.json"
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        print(f"Report saved to: {filename}")
        
        # Print executive summary
        print("\n" + "=" * 80)
        print("KEDCO CLUB SUBSTATION - RAVEN DEPLOYMENT REPORT")
        print("=" * 80)
        
        summary = report['executive_summary']
        deployment_info = summary['deployment_info']
        
        print(f"Substation: {summary['substation']}")
        print(f"Report Date: {summary['report_date']}")
        print(f"Deployment Status: {deployment_info['status']}")
        print(f"Operational Since: {deployment_info['deployment_date']} ({deployment_info['days_operational']} days)")
        print(f"Data Coverage: {deployment_info['data_coverage']}")
        print(f"Historical Context: {deployment_info['historical_baseline']}")
        
        print(f"\nINFRASTRUCTURE OVERVIEW:")
        infra = summary['infrastructure_summary']
        voltage = infra['voltage_breakdown']
        print(f"  • Total Feeders: {infra['feeders']} ({voltage['11kv_feeders']} x 11kV, {voltage['33kv_feeders']} x 33kV)")
        print(f"  • Distribution Transformers: {infra['transformers']}")
        print(f"  • Customers Served: {infra['customers_served']:,}")
        
        print(f"\nKEY PERFORMANCE INDICATORS ({summary['key_performance_indicators']['period']}):")
        kpis = summary['key_performance_indicators']
        print(f"  • Average Supply Hours: {kpis['average_supply_hours']:.1f} hrs/day")
        print(f"  • System Availability: {kpis['system_availability']:.1f}%")
        print(f"  • Total Interruptions: {kpis['total_interruptions']}")
        print(f"  • Daily Interruption Rate: {kpis['daily_interruption_rate']:.1f} per day")
        print(f"  • Peak Load: {kpis['peak_load_mw']:.1f} MW")
        print(f"  • Energy Delivered: {kpis['energy_delivered_mwh']:.1f} MWh")
        
        if summary.get('deployment_performance'):
            print(f"\nDEPLOYMENT PERFORMANCE:")
            for performance in summary['deployment_performance']:
                print(f"  • {performance}")
        
        if summary.get('highlights'):
            print(f"\nPERFORMANCE HIGHLIGHTS:")
            for highlight in summary['highlights']:
                print(f"  • {highlight}")
        
        if summary.get('comparative_insights'):
            print(f"\nCOMPARATIVE ANALYSIS:")
            for insight in summary['comparative_insights']:
                print(f"  • {insight}")
        
        if summary.get('concerns'):
            print(f"\nAREAS OF CONCERN:")
            for concern in summary['concerns']:
                print(f"  • {concern}")
        
        if summary.get('recommendations'):
            print(f"\nKEY RECOMMENDATIONS:")
            for rec in summary['recommendations']:
                print(f"  • {rec}")
        
        # Print detailed comparison data if available
        comparisons = report['performance_metrics'].get('key_comparisons', {})
        baseline_comp = comparisons.get('deployment_vs_2024_baseline', {})
        
        if baseline_comp:
            print(f"\nDETAILED 2024 BASELINE COMPARISON:")
            print(f"  Supply Hours: {baseline_comp['supply_hours']['current']:.1f}h vs {baseline_comp['supply_hours']['baseline_2024']:.1f}h (2024) = {baseline_comp['supply_hours']['improvement_percent']:+.1f}%")
            print(f"  System Availability: {baseline_comp['availability']['current']:.1f}% vs {baseline_comp['availability']['baseline_2024']:.1f}% (2024) = {baseline_comp['availability']['improvement_percent']:+.1f}%")
            print(f"  Daily Interruptions: {baseline_comp['interruption_frequency']['current_daily_avg']:.1f} vs {baseline_comp['interruption_frequency']['baseline_daily_avg']:.1f} (2024) = {baseline_comp['interruption_frequency']['change_percent']:+.1f}%")
        
        monthly_comp = comparisons.get('august_to_september_2025', {})
        if monthly_comp:
            print(f"\nMONTH-TO-MONTH PROGRESS (2025):")
            aug_supply = monthly_comp['supply_hours']['august']
            sep_supply = monthly_comp['supply_hours']['september'] 
            supply_change = monthly_comp['supply_hours']['change_percent']
            print(f"  Supply Hours: August {aug_supply:.1f}h → September {sep_supply:.1f}h ({supply_change:+.1f}%)")
        
        print("\n" + "=" * 80)
        print("Report generation completed successfully!")
        print(f"Full detailed data available in: {filename}")
        print("=" * 80)
        
        return report
        
    except Exception as e:
        print(f"Failed to generate report: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    main()