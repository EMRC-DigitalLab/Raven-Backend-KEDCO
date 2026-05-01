# reports/services.py
"""
Services for fetching report data and generating PDFs.
"""
import logging
from datetime import datetime, timedelta

from django.db.models import Avg, Count, Max, Q
from django.utils import timezone

from common.models import Band, Feeder
from technical.constants import TURNAROUND_EXCLUSIONS
from technical.models import FeederInterruption, HourlyLoad
from technical.utils.compliance_utils import calculate_turnaround_time
from technical.utils.energy_utils import (
    calculate_average_load,
    calculate_energy_delivered,
    calculate_hours_of_supply,
)

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
    # ── HR sections ──────────────────────────────────────────────────────────
    'hr_overview': {
        'display_name': 'HR Overview',
        'description': 'Total staff, gender split, attrition rate, and new hires for the period',
        'category': 'hr',
        'supports_chart': False,
        'config_options': {},
    },
    'staff_metrics': {
        'display_name': 'Staff Metrics',
        'description': 'Average tenure, grade breakdown, and headcount',
        'category': 'hr',
        'supports_chart': False,
        'config_options': {},
    },
    'wage_bill_analysis': {
        'display_name': 'Wage Bill Analysis',
        'description': 'Total wage bill, average salary, and department breakdown',
        'category': 'hr',
        'supports_chart': False,
        'config_options': {},
    },
    'department_headcount': {
        'display_name': 'Department Headcount',
        'description': 'Headcount per department with percentage share',
        'category': 'hr',
        'supports_chart': True,
        'config_options': {},
    },
    'attrition_analysis': {
        'display_name': 'Attrition Analysis',
        'description': 'Staff exits and attrition rate for the period, broken down by department',
        'category': 'hr',
        'supports_chart': False,
        'config_options': {},
    },
    'recruitment_summary': {
        'display_name': 'Recruitment Summary',
        'description': 'New hires for the period by department and grade',
        'category': 'hr',
        'supports_chart': False,
        'config_options': {},
    },
    # ── Commercial sections ───────────────────────────────────────────────────
    'commercial_overview': {
        'display_name': 'Commercial Overview',
        'description': 'Billing, coverage, AT&C loss, ARPU, and MDI/MDNI split',
        'category': 'commercial',
        'supports_chart': False,
        'config_options': {},
    },
    'commercial_coverage': {
        'display_name': 'Reading Coverage',
        'description': 'Customers read vs unread with estimated revenue at risk from unread meters',
        'category': 'commercial',
        'supports_chart': False,
        'config_options': {},
    },
    'commercial_energy': {
        'display_name': 'Energy Analysis',
        'description': 'Energy delivered vs consumed vs billed, with AT&C loss and billing efficiency',
        'category': 'commercial',
        'supports_chart': False,
        'config_options': {},
    },
    'revenue_by_district': {
        'display_name': 'Revenue by District',
        'description': 'Revenue and consumption breakdown per business district',
        'category': 'commercial',
        'supports_chart': True,
        'config_options': {},
    },
    'revenue_by_feeder': {
        'display_name': 'Revenue by Feeder',
        'description': 'Revenue and consumption breakdown per feeder',
        'category': 'commercial',
        'supports_chart': True,
        'config_options': {},
    },
    'customer_type_summary': {
        'display_name': 'Customer Type Summary',
        'description': 'MDI vs MDNI headcount and revenue comparison',
        'category': 'commercial',
        'supports_chart': True,
        'config_options': {},
    },
    # ── DSO Compliance sections ───────────────────────────────────────────────
    'dso_compliance_overview': {
        'display_name': 'DSO Compliance Overview',
        'description': 'Summary of injection station submission compliance for hourly load and energy readings',
        'category': 'technical',
        'supports_chart': False,
        'config_options': {},
    },
    'dso_compliance_table': {
        'display_name': 'DSO Compliance Table',
        'description': 'Per-station breakdown showing hourly load and energy reading submission compliance by DSO vs admin override',
        'category': 'technical',
        'supports_chart': False,
        'config_options': {},
    },
    # ── Financial sections ────────────────────────────────────────────────────
    'financial_overview': {
        'display_name': 'Financial Overview',
        'description': 'Total cost broken down into OPEX, HQ OPEX, salaries, NBET, and MO invoices',
        'category': 'financial',
        'supports_chart': False,
        'config_options': {},
    },
    'opex_by_category': {
        'display_name': 'OPEX by Category',
        'description': 'District OPEX expenditure broken down by category with percentage share',
        'category': 'financial',
        'supports_chart': True,
        'config_options': {},
    },
    'opex_by_district': {
        'display_name': 'OPEX by District',
        'description': 'District OPEX expenditure per business district with percentage share',
        'category': 'financial',
        'supports_chart': True,
        'config_options': {},
    },
}


def get_available_sections(user=None):
    """Return section types the user is permitted to add to a report.

    Access rules:
      - 'general' category (cover, TOC, custom text) — always available.
      - All other categories require an active UserSectionAccess or a
        non-expired TemporaryAccess for the matching Section.name.
      - super_admin / admin roles bypass the check and see every module.
      - Unauthenticated or anonymous users receive general sections only.
    """
    from django.utils import timezone
    from users.models import TemporaryAccess, UserSectionAccess

    if user is None or not getattr(user, 'is_authenticated', False):
        accessible_modules = set()
    elif getattr(user, 'role', None) in ('super_admin', 'admin'):
        accessible_modules = {'technical', 'hr', 'commercial', 'financial', 'regulatory', 'overview'}
    else:
        permanent = set(
            UserSectionAccess.objects.filter(user=user, is_active=True)
            .values_list('section__name', flat=True)
        )
        temporary = set(
            TemporaryAccess.objects.filter(
                user=user, is_active=True, expires_at__gt=timezone.now()
            ).values_list('section__name', flat=True)
        )
        accessible_modules = permanent | temporary

    def _allowed(category):
        return category == 'general' or category in accessible_modules

    return [
        {'section_type': key, **value}
        for key, value in SECTION_DEFINITIONS.items()
        if not value.get('coming_soon', False) and _allowed(value.get('category', 'general'))
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
        from django.db.models import Q

        # ✅ Only onboarded feeders (SEASONALITY AWARE - just like overview_views)
        queryset = Feeder.objects.filter(is_onboarded=True).filter(
            Q(onboarded_at__lte=self.to_date) | Q(onboarded_at__isnull=True)
        )
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

        # Hours of supply — shared utility (one source of truth)
        hours_of_supply = calculate_hours_of_supply(
            self.feeder_ids, self.from_date, self.to_date
        )['hours']

        # Average load + peak load — shared utility (one source of truth)
        _load = calculate_average_load(self.feeder_ids, self.from_date, self.to_date)
        avg_load  = _load['avg']
        peak_load = _load['peak']

        # Energy — shared hybrid utility (one source of truth)
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

        # avg_duration = 24 - hours_of_supply (shared utility — one source of truth)
        hours_of_supply = calculate_hours_of_supply(
            self.feeder_ids, self.from_date, self.to_date
        )['hours']
        avg_duration = round(max(0.0, min(24.0 - hours_of_supply, 24.0)), 2)

        # Cumulative interruption hours = avg_duration × feeders × period_days
        if self.is_single_day:
            total_interruption_hours = avg_duration * total_feeders
        else:
            total_interruption_hours = avg_duration * total_feeders * self.period_days

        # Turnaround time — shared utility, excludes L/S and TCN (one source of truth)
        avg_turnaround = calculate_turnaround_time(
            self.feeder_ids, self.from_date, self.to_date,
            exclude_types=list(TURNAROUND_EXCLUSIONS),
        )

        return {
            'cumulative_interruption_hours': round(total_interruption_hours, 2),
            'avg_duration_of_interruption': avg_duration,
            'avg_turnaround_time': avg_turnaround,
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
            # ✅ FIXED: Use the exact same backend overview calculation for hours of supply
            from technical.views.overview.overview_views import (
                calculate_hours_of_supply_feeder,
            )
            avg_supply_capped = calculate_hours_of_supply_feeder(feeder.id, self.from_date, self.to_date)
            
            # Peak load
            peak_load = HourlyLoad.objects.filter(
                feeder=feeder,
                date__range=(self.from_date, self.to_date)
            ).aggregate(max_load=Max('load_mw'))['max_load'] or 0
            
            # Duration of interruptions = 24h minus avg supply hours per day
            duration_hours = round(max(24.0 - avg_supply_capped, 0.0), 2)

            # ✅ FIXED: Use hybrid energy calculation for single feeder, returning both value and source
            energy_data = self._calculate_energy_for_feeder(feeder.id)
            energy = energy_data['total_mwh']
            
            # Determine source: if meter_feeders > 0 it used the meter, otherwise it fell back to system
            energy_source = 'meter' if energy_data.get('meter_feeders', 0) > 0 else 'system'

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
                'energy_source': energy_source,
            })
        # Sort by band name (A→E) then feeder name within each band
        return sorted(result, key=lambda x: (x['band'], x['name']))

    def _calculate_energy_for_feeder(self, feeder_id):
        """Calculate energy for a single feeder using the shared energy_utils function."""
        result = calculate_energy_delivered([feeder_id], self.from_date, self.to_date)
        return result
    
    # -------------------------------------------------------------------------
    # Internal: previous-period helpers
    # -------------------------------------------------------------------------

    def _prev_period_dates(self):
        """Return (prev_from, prev_to) — the same-length period immediately before."""
        prev_to   = self.from_date - timedelta(days=1)
        prev_from = prev_to - timedelta(days=self.period_days - 1)
        return prev_from, prev_to

    def _fetch_hourly_supply_series(self, from_date, to_date):
        """Raw daily hours-of-supply list for a given date range."""
        if not self.feeder_ids:
            return []
        total_feeders = len(self.feeder_ids)
        from django.db.models import Count
        rows = (
            HourlyLoad.objects.filter(
                feeder_id__in=self.feeder_ids,
                date__range=(from_date, to_date),
                load_mw__gt=0,
            )
            .values('date')
            .annotate(total_hours=Count('id'))
            .order_by('date')
        )
        return [
            {
                'date':  item['date'].strftime('%Y-%m-%d'),
                'hours': round(item['total_hours'] / total_feeders, 2) if total_feeders else 0,
            }
            for item in rows
        ]

    def _fetch_load_series(self, from_date, to_date, metric='average_load'):
        """Raw daily load list for a given date range."""
        if not self.feeder_ids:
            return []
        agg = Avg('load_mw') if metric == 'average_load' else Max('load_mw')
        rows = (
            HourlyLoad.objects.filter(
                feeder_id__in=self.feeder_ids,
                date__range=(from_date, to_date),
            )
            .values('date')
            .annotate(value=agg)
            .order_by('date')
        )
        return [
            {'date': item['date'].strftime('%Y-%m-%d'), 'value': round(float(item['value'] or 0), 2)}
            for item in rows
        ]

    def _fetch_energy_series(self, from_date, to_date):
        """Daily energy-delivered list for a given date range (hybrid calculation)."""
        if not self.feeder_ids:
            return []
        result = []
        current = from_date
        while current <= to_date:
            single_filters = self.filters.copy()
            single_filters['from_date'] = current
            single_filters['to_date']   = current
            tmp    = ReportDataService(single_filters)
            energy = tmp._calculate_energy_delivered_hybrid()
            result.append({'date': current.strftime('%Y-%m-%d'), 'value': round(energy, 2)})
            current += timedelta(days=1)
        return result

    @staticmethod
    def _period_label(from_date, to_date):
        """Human-readable label for a date range."""
        if from_date.month == to_date.month and from_date.year == to_date.year:
            return from_date.strftime('%b %Y')
        return f"{from_date.strftime('%d %b')} – {to_date.strftime('%d %b %Y')}"

    # -------------------------------------------------------------------------
    # Public trend methods — now return {current, previous, labels} dicts
    # -------------------------------------------------------------------------

    def get_hours_of_supply_trend(self):
        """Hours of supply trend for current + previous period."""
        prev_from, prev_to = self._prev_period_dates()
        return {
            'current':  self._fetch_hourly_supply_series(self.from_date, self.to_date),
            'previous': self._fetch_hourly_supply_series(prev_from, prev_to),
            'curr_label': self._period_label(self.from_date, self.to_date),
            'prev_label': self._period_label(prev_from, prev_to),
            'value_key': 'hours',
            'unit': 'hrs',
        }

    def get_load_trend(self, metric='average_load'):
        """Load trend for current + previous period."""
        prev_from, prev_to = self._prev_period_dates()
        return {
            'current':  self._fetch_load_series(self.from_date, self.to_date, metric),
            'previous': self._fetch_load_series(prev_from, prev_to, metric),
            'curr_label': self._period_label(self.from_date, self.to_date),
            'prev_label': self._period_label(prev_from, prev_to),
            'value_key': 'value',
            'unit': 'MW',
        }

    def get_energy_trend(self):
        """Energy delivered trend for current + previous period."""
        prev_from, prev_to = self._prev_period_dates()
        return {
            'current':  self._fetch_energy_series(self.from_date, self.to_date),
            'previous': self._fetch_energy_series(prev_from, prev_to),
            'curr_label': self._period_label(self.from_date, self.to_date),
            'prev_label': self._period_label(prev_from, prev_to),
            'value_key': 'value',
            'unit': 'MWh',
        }
    
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
    
    def get_dso_compliance_data(self):
        """
        Per-injection-station submission compliance for hourly load and energy readings.

        Logic:
          - Considers all active InjectionSubstations (both 'injection' and 'transmission' types).
          - For each station, only onboarded 11kV feeders are counted.
          - Expected hourly load = feeder_count × period_days × 24.
          - Expected energy readings = feeder_count × period_days.
          - Actual counts come from HourlyLoad and CumulativeMeterReading, split by
            submission_type (dso / admin_override).
          - A station is 'compliant' when both hourly_pct ≥ 80 and energy_pct ≥ 80.
        """
        from common.models import InjectionSubstation
        from technical.models import CumulativeMeterReading

        substations = (
            InjectionSubstation.objects
            .filter(status='active')
            .prefetch_related('feeders')
            .order_by('name')
        )

        station_rows = []
        total_stations = 0
        compliant_count = 0

        for station in substations:
            feeder_ids = list(
                station.feeders.filter(voltage_level='11kv', is_onboarded=True)
                .values_list('id', flat=True)
            )
            if not feeder_ids:
                continue

            total_stations += 1
            n = len(feeder_ids)

            expected_hourly = n * self.period_days * 24
            expected_energy = n * self.period_days

            # Hourly load submissions
            hourly_qs = HourlyLoad.objects.filter(
                feeder_id__in=feeder_ids,
                date__range=(self.from_date, self.to_date),
            )
            actual_hourly = hourly_qs.count()
            dso_hourly   = hourly_qs.filter(submission_type='dso').count()
            admin_hourly = hourly_qs.filter(submission_type='admin_override').count()
            late_hourly  = hourly_qs.filter(is_late=True).count()

            # Energy reading submissions
            energy_qs = CumulativeMeterReading.objects.filter(
                feeder_id__in=feeder_ids,
                reading_date__range=(self.from_date, self.to_date),
            )
            actual_energy = energy_qs.count()
            dso_energy    = energy_qs.filter(submission_type='dso').count()
            admin_energy  = energy_qs.filter(submission_type='admin_override').count()
            late_energy   = energy_qs.filter(is_late=True).count()

            hourly_pct = round((actual_hourly / expected_hourly * 100) if expected_hourly else 0, 1)
            energy_pct = round((actual_energy / expected_energy * 100) if expected_energy else 0, 1)
            is_compliant = hourly_pct >= 80 and energy_pct >= 80
            if is_compliant:
                compliant_count += 1

            station_rows.append({
                'station_name':   station.name,
                'station_type':   station.get_station_type_display(),
                'feeder_count':   n,
                'expected_hourly': expected_hourly,
                'actual_hourly':  actual_hourly,
                'dso_hourly':     dso_hourly,
                'admin_hourly':   admin_hourly,
                'late_hourly':    late_hourly,
                'hourly_pct':     hourly_pct,
                'expected_energy': expected_energy,
                'actual_energy':  actual_energy,
                'dso_energy':     dso_energy,
                'admin_energy':   admin_energy,
                'late_energy':    late_energy,
                'energy_pct':     energy_pct,
                'is_compliant':   is_compliant,
            })

        # Best performing first (highest avg compliance %), worst last
        station_rows.sort(key=lambda r: -(r['hourly_pct'] + r['energy_pct']) / 2)

        compliance_rate = round((compliant_count / total_stations * 100) if total_stations else 0, 1)

        return {
            'total_stations':   total_stations,
            'compliant_count':  compliant_count,
            'non_compliant_count': total_stations - compliant_count,
            'compliance_rate':  compliance_rate,
            'period_days':      self.period_days,
            'period': (
                f"{self.from_date.strftime('%d %b %Y')} – "
                f"{self.to_date.strftime('%d %b %Y')}"
            ),
            'stations': station_rows,
        }

    # =========================================================================
    # LAZY SUB-SERVICE ACCESSORS
    # Each sub-service is instantiated once on first use using self.filters
    # mapped to the filter keys each service expects.
    # =========================================================================

    @property
    def _hr_filters(self):
        return {
            'from_date':      self.from_date,
            'to_date':        self.to_date,
            'department_ids': self.filters.get('departments'),
            'grade_levels':   self.filters.get('grade_levels'),
            'district_ids':   self.filters.get('districts'),
            'state_ids':      self.filters.get('states'),
        }

    @property
    def _commercial_filters(self):
        return {
            'from_date':      self.from_date,
            'to_date':        self.to_date,
            'feeder_ids':     self.feeder_ids,
            'district_ids':   self.filters.get('districts'),
            'state_ids':      self.filters.get('states'),
            'customer_type':  self.filters.get('customer_type'),
            'voltage_level':  self.filters.get('voltage_level'),
        }

    @property
    def _financial_filters(self):
        return {
            'from_date':      self.from_date,
            'to_date':        self.to_date,
            'district_ids':   self.filters.get('districts'),
            'state_ids':      self.filters.get('states'),
        }

    def _get_hr_service(self):
        if not hasattr(self, '_hr_service_instance'):
            from reports.hr_service import HRReportDataService
            self._hr_service_instance = HRReportDataService(self._hr_filters)
        return self._hr_service_instance

    def _get_commercial_service(self):
        if not hasattr(self, '_commercial_service_instance'):
            from reports.commercial_service import CommercialReportDataService
            self._commercial_service_instance = CommercialReportDataService(self._commercial_filters)
        return self._commercial_service_instance

    def _get_financial_service(self):
        if not hasattr(self, '_financial_service_instance'):
            from reports.financial_service import FinancialReportDataService
            self._financial_service_instance = FinancialReportDataService(self._financial_filters)
        return self._financial_service_instance

    # =========================================================================
    # MASTER DISPATCHER
    # =========================================================================

    # Section types owned by each sub-service
    _HR_SECTIONS         = {'hr_overview', 'staff_metrics', 'wage_bill_analysis',
                            'department_headcount', 'attrition_analysis', 'recruitment_summary'}
    _COMMERCIAL_SECTIONS = {'commercial_overview', 'revenue_by_district', 'customer_type_summary'}
    _FINANCIAL_SECTIONS  = {'financial_overview', 'opex_by_category', 'opex_by_district'}

    def get_all_section_data(self, section_type, config=None):
        """Get data for a specific section type"""
        config = config or {}

        # ── Delegate to module sub-services ──────────────────────────────────
        if section_type in self._HR_SECTIONS:
            return self._get_hr_service().get_all_section_data(section_type, config)
        if section_type in self._COMMERCIAL_SECTIONS:
            return self._get_commercial_service().get_all_section_data(section_type, config)
        if section_type in self._FINANCIAL_SECTIONS:
            return self._get_financial_service().get_all_section_data(section_type, config)

        # ── Technical / general sections ─────────────────────────────────────
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
            'hours_of_supply_chart': self.get_hours_of_supply_trend,
            'load_trend_chart': lambda: self.get_load_trend(
                metric=config.get('metric', 'average_load')
            ),
            'energy_delivered_chart': self.get_energy_trend,
            'service_band_summary': self.get_service_band_summary,
            'dso_compliance_overview': self.get_dso_compliance_data,
            'dso_compliance_table':    self.get_dso_compliance_data,
            'custom_text': lambda: config,
            'gaps_improvements': lambda: config,
        }

        method = data_methods.get(section_type)
        if method:
            return method()

        return {}