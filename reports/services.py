# reports/services.py
"""
Services for fetching report data and generating PDFs.
"""
from datetime import datetime, date, timedelta
from django.db import connection
from django.db.models import Sum, Avg, Max, Count, Q
from django.utils import timezone
from django.conf import settings
import logging

from common.models import State, BusinessDistrict, InjectionSubstation, Feeder, Band
from technical.models import HourlyLoad, FeederInterruption
from technical.constants import TURNAROUND_EXCLUSIONS
from technical.utils.energy_utils import calculate_energy_delivered

logger = logging.getLogger(__name__)


# =============================================================================
# SECTION TYPE DEFINITIONS
# =============================================================================

SECTION_DEFINITIONS = {
    'cover_page': {
        'display_name': 'Cover Page',
        'description': 'Title page with report name, date, and company branding',
        'category': 'general',
        'supports_chart': False,
        'config_options': {
            'show_logo': {'type': 'boolean', 'default': True},
            'show_subtitle': {'type': 'boolean', 'default': True},
        }
    },
    'table_of_contents': {
        'display_name': 'Table of Contents',
        'description': 'Auto-generated table of contents with section names and page numbers',
        'category': 'general',
        'supports_chart': False,
        'config_options': {},
    },
    'infrastructure_overview': {
        'display_name': 'Infrastructure Overview',
        'description': 'Summary of monitored feeders, transformers, and substations',
        'category': 'technical',
        'supports_chart': False,
        'config_options': {
            'show_feeder_table': {'type': 'boolean', 'default': True},
            'show_summary_points': {'type': 'boolean', 'default': True},
            'summary_points': {'type': 'text_list', 'default': []},
        }
    },
    'technical_metrics': {
        'display_name': 'Technical Metrics Cards',
        'description': 'Key technical metrics displayed as cards (hours of supply, load, energy, etc.)',
        'category': 'technical',
        'supports_chart': False,
        'config_options': {
            'metrics': {
                'type': 'multi_select',
                'options': [
                    'hours_of_supply',
                    'average_load',
                    'peak_load',
                    'energy_delivered',
                    'daily_average_consumption',
                    'total_interruptions',
                    'load_shedding_count',
                ],
                'default': ['hours_of_supply', 'average_load', 'energy_delivered', 'total_interruptions']
            }
        }
    },
    'system_reliability': {
        'display_name': 'System Reliability',
        'description': 'Reliability metrics including interruption hours, duration, and turnaround time',
        'category': 'technical',
        'supports_chart': False,
        'config_options': {
            'show_cumulative_hours': {'type': 'boolean', 'default': True},
            'show_avg_duration': {'type': 'boolean', 'default': True},
            'show_turnaround_time': {'type': 'boolean', 'default': True},
        }
    },
    'interruption_breakdown': {
        'display_name': 'Interruption Breakdown Table',
        'description': 'Table showing interruptions grouped by type with counts and durations',
        'category': 'technical',
        'supports_chart': True,
        'config_options': {
            'group_by': {
                'type': 'select',
                'options': ['type', 'feeder', 'day'],
                'default': 'type'
            }
        }
    },
    'hours_of_supply_chart': {
        'display_name': 'Hours of Supply Chart',
        'description': 'Chart showing hours of supply trend over the period',
        'category': 'technical',
        'supports_chart': True,
        'config_options': {
            'chart_type': {
                'type': 'select',
                'options': ['line', 'bar'],
                'default': 'line'
            },
            'group_by': {
                'type': 'select',
                'options': ['day', 'week', 'feeder'],
                'default': 'day'
            }
        }
    },
    'load_trend_chart': {
        'display_name': 'Load Trend Chart',
        'description': 'Chart showing load trends over the period',
        'category': 'technical',
        'supports_chart': True,
        'config_options': {
            'chart_type': {
                'type': 'select',
                'options': ['line', 'bar', 'area'],
                'default': 'line'
            },
            'metric': {
                'type': 'select',
                'options': ['average_load', 'peak_load'],
                'default': 'average_load'
            }
        }
    },
    'energy_delivered_chart': {
        'display_name': 'Energy Delivered Chart',
        'description': 'Chart showing energy delivered over the period',
        'category': 'technical',
        'supports_chart': True,
        'config_options': {
            'chart_type': {
                'type': 'select',
                'options': ['line', 'bar', 'area'],
                'default': 'bar'
            }
        }
    },
    'feeder_performance_table': {
        'display_name': 'Feeder Performance Table',
        'description': 'Table showing performance metrics for each feeder',
        'category': 'technical',
        'supports_chart': False,
        'config_options': {
            'columns': {
                'type': 'multi_select',
                'options': [
                    'hours_of_supply',
                    'interruptions',
                    'peak_load',
                    'energy_delivered',
                    'availability_percentage',
                ],
                'default': ['hours_of_supply', 'interruptions', 'peak_load']
            },
            'sort_by': {
                'type': 'select',
                'options': ['name', 'hours_of_supply', 'interruptions'],
                'default': 'name'
            }
        }
    },
    'state_performance_table': {
        'display_name': 'State Performance Table',
        'description': 'Table showing performance metrics grouped by state',
        'category': 'technical',
        'supports_chart': False,
        'config_options': {}
    },
    'district_performance_table': {
        'display_name': 'District Performance Table',
        'description': 'Table showing performance metrics grouped by business district',
        'category': 'technical',
        'supports_chart': False,
        'config_options': {}
    },
    'service_band_summary': {
        'display_name': 'Service Band Summary',
        'description': 'Summary of metrics grouped by service band (A, B, C, etc.)',
        'category': 'technical',
        'supports_chart': True,
        'config_options': {
            'show_chart': {'type': 'boolean', 'default': False},
        }
    },
    'custom_text': {
        'display_name': 'Custom Text/Notes',
        'description': 'Add custom text, notes, or observations to the report',
        'category': 'general',
        'supports_chart': False,
        'config_options': {
            'title': {'type': 'text', 'default': 'Notes'},
            'content': {'type': 'rich_text', 'default': ''},
        }
    },
    'gaps_improvements': {
        'display_name': 'Gaps and Improvement Areas',
        'description': 'Section for documenting gaps and areas for improvement',
        'category': 'general',
        'supports_chart': False,
        'config_options': {
            'sections': {
                'type': 'list',
                'item_schema': {
                    'title': {'type': 'text'},
                    'content': {'type': 'text_list'},
                },
                'default': []
            }
        }
    },
    'commercial_summary': {
        'display_name': 'Commercial Summary',
        'description': 'Commercial metrics summary (coming soon)',
        'category': 'commercial',
        'supports_chart': False,
        'config_options': {},
        'coming_soon': True,
    },
    'financial_summary': {
        'display_name': 'Financial Summary',
        'description': 'Financial metrics summary (coming soon)',
        'category': 'financial',
        'supports_chart': False,
        'config_options': {},
        'coming_soon': True,
    },
    'collection_efficiency': {
        'display_name': 'Collection Efficiency',
        'description': 'Collection efficiency metrics (coming soon)',
        'category': 'commercial',
        'supports_chart': True,
        'config_options': {},
        'coming_soon': True,
    },
}


def get_available_sections():
    """Return list of available section types with their definitions"""
    return [
        {
            'section_type': key,
            **value
        }
        for key, value in SECTION_DEFINITIONS.items()
        if not value.get('coming_soon', False)
    ]


# =============================================================================
# DATA FETCHING SERVICES
# =============================================================================

class ReportDataService:
    """Service for fetching data for report sections"""
    
    def __init__(self, filters):
        """
        Initialize with filters.
        
        filters = {
            'from_date': '2025-01-01',
            'to_date': '2025-01-31',
            'states': [uuid1, uuid2],  # optional
            'districts': [uuid1, uuid2],  # optional
            'substations': [uuid1, uuid2],  # optional
            'feeders': [uuid1, uuid2],  # optional
            'bands': [uuid1, uuid2],  # optional
        }
        """
        self.filters = filters
        self.from_date = self._parse_date(filters.get('from_date'))
        self.to_date = self._parse_date(filters.get('to_date'))
        
        # Build feeder queryset based on filters
        self.feeder_ids = self._get_filtered_feeder_ids()

        # Determine if single-day query
        self.is_single_day = (self.from_date == self.to_date)

        # Calculate period days
        if self.is_single_day:
            self.period_days = 1
        else:
            self.period_days = (self.to_date - self.from_date).days + 1
    
    def _parse_date(self, date_val):
        """
        Parse date from string or return as-is if already a date.

        Always uses the YYYY-MM-DD portion of any ISO string, discarding time
        and timezone parts.  This prevents off-by-N-day errors when the frontend
        sends midnight-UTC strings for a date selected in a UTC+1 locale.
        """
        if isinstance(date_val, str):
            # Take only the date portion (first 10 chars) regardless of whether
            # the string includes a time or timezone component.
            date_str = date_val.split('T')[0].split(' ')[0].strip()
            return datetime.strptime(date_str, '%Y-%m-%d').date()
        elif isinstance(date_val, datetime):
            return date_val.date()
        return date_val
    
    def _get_filtered_feeder_ids(self):
        """Get feeder IDs based on all filters"""
        queryset = Feeder.objects.filter(is_onboarded=True)  # ✅ Only onboarded feeders

        # Filter by voltage level: '11kv' or '33kv' (optional)
        # Normalize to lowercase so '11kV', '11KV', etc. all work
        voltage_level = self.filters.get('voltage_level')
        if voltage_level:
            voltage_level = str(voltage_level).lower().strip()
        if voltage_level in ('11kv', '33kv'):
            queryset = queryset.filter(voltage_level=voltage_level)

        # Filter by specific feeders (only if list is not empty)
        if self.filters.get('feeders') and len(self.filters['feeders']) > 0:
            queryset = queryset.filter(id__in=self.filters['feeders'])

        # Filter by bands (only if list is not empty)
        if self.filters.get('bands') and len(self.filters['bands']) > 0:
            queryset = queryset.filter(band_id__in=self.filters['bands'])

        # Filter by substations (only if list is not empty)
        if self.filters.get('substations') and len(self.filters['substations']) > 0:
            queryset = queryset.filter(substation_id__in=self.filters['substations'])

        # Filter by districts (only if list is not empty)
        if self.filters.get('districts') and len(self.filters['districts']) > 0:
            queryset = queryset.filter(business_district_id__in=self.filters['districts'])

        # Filter by states (only if list is not empty)
        if self.filters.get('states') and len(self.filters['states']) > 0:
            queryset = queryset.filter(business_district__state_id__in=self.filters['states'])

        return list(queryset.values_list('id', flat=True))
    
    def get_infrastructure_data(self):
        """Get infrastructure overview data"""
        feeders = Feeder.objects.filter(id__in=self.feeder_ids).select_related(
            'band', 'substation', 'business_district', 'business_district__state'
        )

        feeders_11kv = feeders.filter(voltage_level='11kv').count()
        feeders_33kv = feeders.filter(voltage_level='33kv').count()

        return {
            'total_feeders': feeders.count(),
            'feeders_11kv': feeders_11kv,
            'feeders_33kv': feeders_33kv,
            'total_substations': feeders.values('substation').distinct().count(),
            'total_transformers': sum(f.transformers.count() for f in feeders),
            'feeders': [
                {
                    'name': f.name,
                    'voltage': f.get_voltage_level_display(),
                    'band': f.band.name if f.band else '-',
                    'district': f.business_district.name if f.business_district else '-',
                    'substation': f.substation.name if f.substation else '-',
                    'transformer_count': f.transformers.count(),
                }
                for f in feeders
            ]
        }
    
    def get_technical_metrics(self):
        """Get technical overview metrics"""
        if not self.feeder_ids:
            return self._empty_technical_metrics()
        
        total_feeders = len(self.feeder_ids)
        
        # Hours of supply
        hours_of_supply = self._calculate_hours_of_supply()
        
        # Load metrics
        today = timezone.now().date()
        now = timezone.now()

        if self.to_date == today:
            # For current day, calculate actual elapsed hours
            full_days = (self.to_date - self.from_date).days
            current_hour = now.hour
            current_minute = now.minute
    
            # Include minutes for precision
            hours_elapsed = current_hour + (current_minute / 60.0)
            period_hours = (full_days * 24) + hours_elapsed
    
            # Ensure minimum period to avoid division by zero
            if period_hours == 0:
                period_hours = 1  # 1 hour minimum
        else:
            # For past periods, use full hours
            period_days_calc = (self.to_date - self.from_date).days + 1
            period_hours = period_days_calc * 24

        # Sum total load across all feeders
        load_data = HourlyLoad.objects.filter(
            feeder_id__in=self.feeder_ids,
            date__range=(self.from_date, self.to_date)
        ).aggregate(
            total_load=Sum('load_mw'),
            max_load=Max('load_mw')
        )

        total_load = float(load_data['total_load'] or 0)
        peak_load = float(load_data['max_load'] or 0)

        # Average = Total Load / (Total Feeders × Total Hours)
        avg_load = total_load / (total_feeders * period_hours) if (total_feeders * period_hours) > 0 else 0
        
        # ✅ FIXED: Use hybrid energy calculation
        total_energy = self._calculate_energy_delivered_hybrid()
        daily_avg_energy = total_energy / self.period_days if self.period_days > 0 else 0
        
        # Interruption counts
        total_interruptions = FeederInterruption.objects.filter(
            feeder_id__in=self.feeder_ids,
            occurred_at__date__range=(self.from_date, self.to_date)
        ).count()
        
        load_shedding_count = FeederInterruption.objects.filter(
            feeder_id__in=self.feeder_ids,
            occurred_at__date__range=(self.from_date, self.to_date),
            interruption_type='L/S'
        ).count()
        
        return {
            'hours_of_supply': round(hours_of_supply, 2),
            'average_load': round(avg_load, 2),
            'peak_load': round(peak_load, 2),
            'energy_delivered': round(total_energy, 2),
            'daily_average_consumption': round(daily_avg_energy, 2),
            'total_interruptions': total_interruptions,
            'load_shedding_count': load_shedding_count,
        }
    
    def _calculate_hours_of_supply(self):
        """
        Calculate average hours of supply per day.
        
        ✅ FIXED: Handles single-day vs multi-day queries properly
        """
        if not self.feeder_ids:
            return 0.0
        
        placeholders = ','.join(['%s'] * len(self.feeder_ids))
        
        query = f"""
            SELECT COUNT(DISTINCT CONCAT(feeder_id, '-', date, '-', hour)) as total_hours
            FROM technical_hourlyload
            WHERE feeder_id IN ({placeholders})
                AND date BETWEEN %s AND %s
                AND load_mw > 0
        """
        
        with connection.cursor() as cursor:
            cursor.execute(query, list(self.feeder_ids) + [self.from_date, self.to_date])
            result = cursor.fetchone()
            total_hours = result[0] if result else 0
        
        total_feeders = len(self.feeder_ids)
        
        # ✅ CRITICAL: For single-day queries, return average hours per feeder (not daily average)
        # For multi-day queries, return average hours per day per feeder
        if self.is_single_day:
            # Single day: Average hours per feeder
            avg_hours = total_hours / total_feeders if total_feeders > 0 else 0
        else:
            # Multi-day: Average hours per day per feeder
            avg_hours = total_hours / (total_feeders * self.period_days) if (total_feeders * self.period_days) > 0 else 0
        
        return min(avg_hours, 24.0)
    
    def _calculate_energy_delivered_hybrid(self):
        """
        Calculate energy delivered using the shared energy_utils function.

        Delegates to calculate_energy_delivered() which applies per-feeder
        hybrid logic (meter primary, HourlyLoad fallback) with a MAX-daily
        balloon-limit check that guarantees:

            monthly total = Σ daily totals

        Returns total energy in MWh across all feeders for the period.
        """
        if not self.feeder_ids:
            return 0.0
        result = calculate_energy_delivered(self.feeder_ids, self.from_date, self.to_date)
        return result['total_mwh']
    
    def _empty_technical_metrics(self):
        """Return empty technical metrics"""
        return {
            'hours_of_supply': 0.0,
            'average_load': 0.0,
            'peak_load': 0.0,
            'energy_delivered': 0.0,
            'daily_average_consumption': 0.0,
            'total_interruptions': 0,
            'load_shedding_count': 0,
        }
    
    def get_system_reliability(self):
        """Get system reliability metrics"""
        if not self.feeder_ids:
            return {
                'cumulative_interruption_hours': 0.0,
                'avg_duration_of_interruption': 0.0,
                'avg_turnaround_time': 0.0,
            }
        
        total_feeders = len(self.feeder_ids)
        
        # ✅ FIXED: Use proper max hours based on single-day vs multi-day
        if self.is_single_day:
            max_hours_per_feeder = 24.0
        else:
            max_hours_per_feeder = self.period_days * 24.0
        
        start_of_period = timezone.make_aware(
            datetime.combine(self.from_date, datetime.min.time())
        )
        end_of_period = timezone.make_aware(
            datetime.combine(self.to_date, datetime.max.time())
        )
        
        placeholders = ','.join(['%s'] * len(self.feeder_ids))
        
        # All interruptions (for duration)
        duration_query = f"""
            SELECT 
                COALESCE(SUM(capped_hours), 0) as total_hours
            FROM (
                SELECT 
                    fi.feeder_id,
                    LEAST(
                        SUM(
                            GREATEST(
                                EXTRACT(EPOCH FROM (
                                    LEAST(COALESCE(restored_at, %s), %s) - GREATEST(occurred_at, %s)
                                )) / 3600.0,
                                0
                            )
                        ),
                        %s
                    ) as capped_hours
                FROM technical_feederinterruption fi
                WHERE fi.feeder_id IN ({placeholders})
                    AND (
                        fi.occurred_at >= %s AND fi.occurred_at <= %s
                        OR (fi.occurred_at < %s AND (fi.restored_at IS NULL OR fi.restored_at >= %s))
                    )
                GROUP BY fi.feeder_id
            ) per_feeder_totals
        """
        
        duration_params = [
            end_of_period, end_of_period, start_of_period, max_hours_per_feeder
        ] + list(self.feeder_ids) + [
            start_of_period, end_of_period, start_of_period, start_of_period
        ]
        
        with connection.cursor() as cursor:
            cursor.execute(duration_query, duration_params)
            result = cursor.fetchone()
            total_interruption_hours = float(result[0]) if result else 0
        
        # ✅ FIXED: Handle single-day vs multi-day
        if self.is_single_day:
            avg_duration = total_interruption_hours / total_feeders if total_feeders > 0 else 0
        else:
            avg_duration = total_interruption_hours / (total_feeders * self.period_days) if (total_feeders * self.period_days) > 0 else 0
        
        # avg_duration = 24 - hours_of_supply  (same HourlyLoad source as Technical Overview
        # so avg_supply + avg_duration always sums to exactly 24)
        hours_of_supply = self._calculate_hours_of_supply()
        avg_duration = max(0.0, min(24.0 - hours_of_supply, 24.0))

        # Cumulative interruption hours = avg_duration × feeders × period_days
        if self.is_single_day:
            total_interruption_hours = avg_duration * total_feeders
        else:
            total_interruption_hours = avg_duration * total_feeders * self.period_days

        return {
            'cumulative_interruption_hours': round(total_interruption_hours, 2),
            'avg_duration_of_interruption': round(avg_duration, 2),
            'avg_turnaround_time': round(avg_turnaround, 2),
        }
    
    def get_interruption_breakdown(self, group_by='type'):
        """Get interruption breakdown data"""
        if not self.feeder_ids:
            return []
        
        interruptions = FeederInterruption.objects.filter(
            feeder_id__in=self.feeder_ids,
            occurred_at__date__range=(self.from_date, self.to_date)
        )
        
        if group_by == 'type':
            breakdown = interruptions.values('interruption_type').annotate(
                count=Count('id'),
            ).order_by('-count')
            
            # Calculate total hours per type
            result = []
            for item in breakdown:
                int_type = item['interruption_type']
                type_interruptions = interruptions.filter(interruption_type=int_type)
                
                total_hours = 0
                for intr in type_interruptions:
                    if intr.restored_at:
                        duration = (intr.restored_at - intr.occurred_at).total_seconds() / 3600
                        total_hours += max(0, min(duration, 720))  # Cap at 30 days
                
                avg_duration = total_hours / item['count'] if item['count'] > 0 else 0
                
                result.append({
                    'type': int_type,
                    'count': item['count'],
                    'total_hours': round(total_hours, 1),
                    'avg_duration': round(avg_duration, 1),
                })
            
            return result
        
        return []
    
    def get_feeder_performance(self):
        """Get performance data for each feeder"""
        if not self.feeder_ids:
            return []
        
        feeders = Feeder.objects.filter(id__in=self.feeder_ids).select_related(
            'band', 'business_district'
        )
        
        result = []
        for feeder in feeders:
            # Hours of supply for this feeder
            hours_count = HourlyLoad.objects.filter(
                feeder=feeder,
                date__range=(self.from_date, self.to_date),
                load_mw__gt=0
            ).count()
            
            # ✅ FIXED: Handle single-day vs multi-day
            if self.is_single_day:
                avg_supply = float(hours_count)
            else:
                avg_supply = hours_count / self.period_days if self.period_days > 0 else 0
            
            # Peak load
            peak_load = HourlyLoad.objects.filter(
                feeder=feeder,
                date__range=(self.from_date, self.to_date)
            ).aggregate(max_load=Max('load_mw'))['max_load'] or 0
            
            # Duration of interruptions = 24h minus avg supply hours per day
            avg_supply_capped = min(avg_supply, 24.0)
            duration_hours = round(max(24.0 - avg_supply_capped, 0.0), 2)

            # ✅ FIXED: Use hybrid energy calculation for single feeder
            energy = self._calculate_energy_for_feeder(feeder.id)

            result.append({
                'id': str(feeder.id),
                'name': feeder.name,
                'band': feeder.band.name if feeder.band else '-',
                'district': feeder.business_district.name if feeder.business_district else '-',
                'hours_of_supply': round(avg_supply_capped, 2),
                'availability_percentage': round((avg_supply_capped / 24) * 100, 1),
                'duration_hours': duration_hours,
                'peak_load': round(float(peak_load), 2),
                'energy_delivered': round(float(energy), 2),
            })
        # Sort by band name (A→E) then feeder name within each band
        return sorted(result, key=lambda x: (x['band'], x['name']))

    def _calculate_energy_for_feeder(self, feeder_id):
        """Calculate energy for a single feeder using the shared energy_utils function."""
        result = calculate_energy_delivered([feeder_id], self.from_date, self.to_date)
        return result['total_mwh']
    
    def get_hours_of_supply_trend(self, group_by='day'):
        """Get hours of supply trend data for charts"""
        if not self.feeder_ids:
            return []
        
        total_feeders = len(self.feeder_ids)
        
        if group_by == 'day':
            # Group by date
            daily_data = HourlyLoad.objects.filter(
                feeder_id__in=self.feeder_ids,
                date__range=(self.from_date, self.to_date),
                load_mw__gt=0
            ).values('date').annotate(
                total_hours=Count('id')
            ).order_by('date')
            
            return [
                {
                    'date': item['date'].strftime('%Y-%m-%d'),
                    'hours': round(item['total_hours'] / total_feeders, 2) if total_feeders > 0 else 0
                }
                for item in daily_data
            ]
        
        return []
    
    def get_load_trend(self, metric='average_load'):
        """Get load trend data for charts"""
        if not self.feeder_ids:
            return []
        
        if metric == 'average_load':
            daily_data = HourlyLoad.objects.filter(
                feeder_id__in=self.feeder_ids,
                date__range=(self.from_date, self.to_date)
            ).values('date').annotate(
                value=Avg('load_mw')
            ).order_by('date')
        else:  # peak_load
            daily_data = HourlyLoad.objects.filter(
                feeder_id__in=self.feeder_ids,
                date__range=(self.from_date, self.to_date)
            ).values('date').annotate(
                value=Max('load_mw')
            ).order_by('date')
        
        return [
            {
                'date': item['date'].strftime('%Y-%m-%d'),
                'value': round(float(item['value'] or 0), 2)
            }
            for item in daily_data
        ]
    
    def get_energy_trend(self):
        """Get energy delivered trend data for charts - FIXED"""
        if not self.feeder_ids:
            return []
        
        # Generate daily data with hybrid calculation
        daily_data = []
        current_date = self.from_date
        
        while current_date <= self.to_date:
            # Create temporary service for single day
            single_day_filters = self.filters.copy()
            single_day_filters['from_date'] = current_date
            single_day_filters['to_date'] = current_date
            
            temp_service = ReportDataService(single_day_filters)
            daily_energy = temp_service._calculate_energy_delivered_hybrid()
            
            daily_data.append({
                'date': current_date.strftime('%Y-%m-%d'),
                'value': round(daily_energy, 2)
            })
            
            current_date += timedelta(days=1)
        
        return daily_data
    
    def get_service_band_summary(self):
        """Get summary by service band"""
        # Build band -> feeder_ids mapping in one shot to avoid N+1 queries
        feeder_band_map = dict(
            Feeder.objects.filter(id__in=self.feeder_ids)
            .values_list('id', 'band_id')
        )
        # Group feeder_ids by band_id
        from collections import defaultdict
        band_feeder_map = defaultdict(list)
        for fid, bid in feeder_band_map.items():
            if bid is not None:
                band_feeder_map[bid].append(fid)

        bands = Band.objects.filter(id__in=band_feeder_map.keys()).order_by('name')

        result = []
        for band in bands:
            band_feeder_ids = band_feeder_map[band.id]

            if not band_feeder_ids:
                continue

            feeder_count = len(band_feeder_ids)

            # Hours of supply
            hours_count = HourlyLoad.objects.filter(
                feeder_id__in=band_feeder_ids,
                date__range=(self.from_date, self.to_date),
                load_mw__gt=0
            ).count()

            # Match main metrics: single-day vs multi-day
            if self.is_single_day:
                avg_supply = float(hours_count) / feeder_count if feeder_count > 0 else 0
            else:
                avg_supply = hours_count / (feeder_count * self.period_days) if feeder_count > 0 else 0

            # Interruptions
            interruption_count = FeederInterruption.objects.filter(
                feeder_id__in=band_feeder_ids,
                occurred_at__date__range=(self.from_date, self.to_date)
            ).count()

            result.append({
                'band': band.name,
                'feeder_count': feeder_count,
                'hours_of_supply': round(min(avg_supply, 24.0), 2),
                'interruptions': interruption_count,
            })

        return result
    
    def get_state_performance(self):
        """Get performance data grouped by state"""
        if not self.feeder_ids:
            return []
        
        # Get all states that have feeders in our filter
        feeders = Feeder.objects.filter(id__in=self.feeder_ids).select_related(
            'business_district__state'
        )
        
        states_dict = {}
        for feeder in feeders:
            if not feeder.business_district or not feeder.business_district.state:
                continue
            
            state = feeder.business_district.state
            if state.id not in states_dict:
                states_dict[state.id] = {
                    'state_name': state.name,
                    'feeder_ids': []
                }
            states_dict[state.id]['feeder_ids'].append(feeder.id)
        
        result = []
        for state_id, state_data in states_dict.items():
            feeder_ids = state_data['feeder_ids']
            feeder_count = len(feeder_ids)
            
            # Hours of supply
            hours_count = HourlyLoad.objects.filter(
                feeder_id__in=feeder_ids,
                date__range=(self.from_date, self.to_date),
                load_mw__gt=0
            ).count()

            # Match main metrics: single-day vs multi-day
            if self.is_single_day:
                avg_supply = float(hours_count) / feeder_count if feeder_count > 0 else 0
            else:
                avg_supply = hours_count / (feeder_count * self.period_days) if feeder_count > 0 else 0

            # Peak load
            peak_load = HourlyLoad.objects.filter(
                feeder_id__in=feeder_ids,
                date__range=(self.from_date, self.to_date)
            ).aggregate(max_load=Max('load_mw'))['max_load'] or 0

            # Energy — use same hybrid function as main metrics for consistency
            energy = calculate_energy_delivered(feeder_ids, self.from_date, self.to_date)['total_mwh']

            # Interruptions
            interruption_count = FeederInterruption.objects.filter(
                feeder_id__in=feeder_ids,
                occurred_at__date__range=(self.from_date, self.to_date)
            ).count()
            
            result.append({
                'state_name': state_data['state_name'],
                'feeder_count': feeder_count,
                'hours_of_supply': round(min(avg_supply, 24.0), 2),
                'availability_percentage': round((min(avg_supply, 24.0) / 24) * 100, 1),
                'interruptions': interruption_count,
                'peak_load': round(float(peak_load), 2),
                'energy_delivered': round(float(energy), 2),
            })
        
        return sorted(result, key=lambda x: x['state_name'])
    
    def get_district_performance(self):
        """Get performance data grouped by business district"""
        if not self.feeder_ids:
            return []
        
        # Get all districts that have feeders in our filter
        feeders = Feeder.objects.filter(id__in=self.feeder_ids).select_related(
            'business_district__state'
        )
        
        districts_dict = {}
        for feeder in feeders:
            if not feeder.business_district:
                continue
            
            district = feeder.business_district
            if district.id not in districts_dict:
                districts_dict[district.id] = {
                    'district_name': district.name,
                    'state_name': district.state.name if district.state else '-',
                    'feeder_ids': []
                }
            districts_dict[district.id]['feeder_ids'].append(feeder.id)
        
        result = []
        for district_id, district_data in districts_dict.items():
            feeder_ids = district_data['feeder_ids']
            feeder_count = len(feeder_ids)
            
            # Hours of supply
            hours_count = HourlyLoad.objects.filter(
                feeder_id__in=feeder_ids,
                date__range=(self.from_date, self.to_date),
                load_mw__gt=0
            ).count()

            # Match main metrics: single-day vs multi-day
            if self.is_single_day:
                avg_supply = float(hours_count) / feeder_count if feeder_count > 0 else 0
            else:
                avg_supply = hours_count / (feeder_count * self.period_days) if feeder_count > 0 else 0

            # Peak load
            peak_load = HourlyLoad.objects.filter(
                feeder_id__in=feeder_ids,
                date__range=(self.from_date, self.to_date)
            ).aggregate(max_load=Max('load_mw'))['max_load'] or 0

            # Energy — use same hybrid function as main metrics for consistency
            energy = calculate_energy_delivered(feeder_ids, self.from_date, self.to_date)['total_mwh']

            # Interruptions
            interruption_count = FeederInterruption.objects.filter(
                feeder_id__in=feeder_ids,
                occurred_at__date__range=(self.from_date, self.to_date)
            ).count()
            
            result.append({
                'district_name': district_data['district_name'],
                'state_name': district_data['state_name'],
                'feeder_count': feeder_count,
                'hours_of_supply': round(min(avg_supply, 24.0), 2),
                'availability_percentage': round((min(avg_supply, 24.0) / 24) * 100, 1),
                'interruptions': interruption_count,
                'peak_load': round(float(peak_load), 2),
                'energy_delivered': round(float(energy), 2),
            })
        
        return sorted(result, key=lambda x: x['district_name'])
    
    def get_all_section_data(self, section_type, config=None):
        """Get data for a specific section type"""
        config = config or {}
        
        data_methods = {
            'cover_page': lambda: {
                'from_date': self.from_date.strftime('%Y-%m-%d'),
                'to_date': self.to_date.strftime('%Y-%m-%d'),
                'period_days': self.period_days,
            },
            'table_of_contents': lambda: {},  # Populated by PDFGenerator
            'infrastructure_overview': self.get_infrastructure_data,
            'technical_metrics': self.get_technical_metrics,
            'system_reliability': self.get_system_reliability,
            'interruption_breakdown': lambda: self.get_interruption_breakdown(
                group_by=config.get('group_by', 'type')
            ),
            'feeder_performance_table': self.get_feeder_performance,
            'state_performance_table': self.get_state_performance,
            'district_performance_table': self.get_district_performance,
            'hours_of_supply_chart': lambda: self.get_hours_of_supply_trend(
                group_by=config.get('group_by', 'day')
            ),
            'load_trend_chart': lambda: self.get_load_trend(
                metric=config.get('metric', 'average_load')
            ),
            'energy_delivered_chart': self.get_energy_trend,
            'service_band_summary': self.get_service_band_summary,
            'custom_text': lambda: config,
            'gaps_improvements': lambda: config,
        }
        
        method = data_methods.get(section_type)
        if method:
            return method()
        
        return {}