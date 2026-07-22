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

# ── Feeder segment / dispatch compliance constants ─────────────────────────
BAND_SUPPLY_TARGETS = {
    'A': 20.0,   # hrs/day
    'B': 16.0,
    'C': 12.0,
    'D':  8.0,
    'E':  8.0,
}

def _compliance_status(pct_achieved: float) -> str:
    if pct_achieved >= 105: return 'exceeding'
    if pct_achieved >= 95:  return 'on_target'
    if pct_achieved >= 85:  return 'below_target'
    if pct_achieved >= 75:  return 'poor'
    return 'critical'


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
    # ── TMO sections ─────────────────────────────────────────────────────────
    'tmo_feeder_dispatch': {
        'display_name': 'Feeder Dispatch Targets vs Actuals',
        'description': 'Per-feeder MWh dispatch targets from DataNest vs actual energy delivered from Raven',
        'category': 'tmo',
        'supports_chart': True,
        'config_options': {},
    },
    'tmo_collection_performance': {
        'display_name': 'Collection Performance by Segment',
        'description': 'Target vs actual collection amounts by segment and sub-segment from DataNest',
        'category': 'tmo',
        'supports_chart': True,
        'config_options': {},
    },
    'tmo_billing_efficiency': {
        'display_name': 'Billing Efficiency (BE/FBE)',
        'description': 'Energy delivered vs billed and revenue targets vs actuals by scope from DataNest',
        'category': 'tmo',
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
    # ── Comparison sections ───────────────────────────────────────────────────
    # These sections are populated by the compare engine and rendered client-side.
    # Access is gated: entity/period comparison require any module; customer requires commercial.
    'entity_comparison': {
        'display_name': 'Entity Comparison',
        'description': 'Compare multiple states, districts, feeders, or bands across the report period',
        'category': 'comparison',
        'supports_chart': True,
        'config_options': {
            'entity_type': {
                'type': 'select',
                'options': ['state', 'district', 'feeder', 'band'],
                'default': 'district',
            },
            'entity_ids': {'type': 'multi_select', 'default': []},
            'metrics': {'type': 'multi_select', 'default': []},
            'granularity': {
                'type': 'select',
                'options': ['daily', 'monthly'],
                'default': 'monthly',
            },
            'feeder_type': {
                'type': 'select',
                'options': ['11kv', '33kv'],
                'default': '11kv',
            },
            'include_trend': {'type': 'boolean', 'default': True},
        },
    },
    'period_comparison': {
        'display_name': 'Period Comparison',
        'description': 'Compare a single entity across multiple custom time periods',
        'category': 'comparison',
        'supports_chart': True,
        'config_options': {
            'entity_type': {
                'type': 'select',
                'options': ['state', 'district', 'feeder', 'band'],
                'default': 'feeder',
            },
            'entity_id': {'type': 'select', 'default': None},
            'metrics': {'type': 'multi_select', 'default': []},
            'periods': {'type': 'list', 'default': []},
            'feeder_type': {
                'type': 'select',
                'options': ['11kv', '33kv'],
                'default': '11kv',
            },
        },
    },
    'energy_by_segment_pl': {
        'display_name': 'Energy by P&L Segment',
        'description': 'Total energy delivered split by MDI vs Non-MDI (MDNI) with % share',
        'category': 'technical',
        'supports_chart': True,
        'config_options': {},
    },
    'segment_voltage_energy': {
        'display_name': 'Energy by Segment & Voltage',
        'description': 'Energy breakdown by MDI/Non-MDI × 33kV/11kV voltage level',
        'category': 'technical',
        'supports_chart': True,
        'config_options': {},
    },
    'energy_md_nmd_mix': {
        'display_name': 'MD vs NMD Energy Mix',
        'description': 'MD (MDI) vs NMD (Non-MDI) % share of energy vs 60/40 target mix',
        'category': 'technical',
        'supports_chart': True,
        'config_options': {},
    },
    'segment_compliance_trend': {
        'display_name': 'Segment Compliance Trend',
        'description': 'Daily % of feeders on-target-or-better per segment over the reporting period',
        'category': 'technical',
        'supports_chart': True,
        'config_options': {},
    },
    # ── Segment / Dispatch Compliance sections ────────────────────────────────
    'segment_compliance_summary': {
        'display_name': 'Segment Compliance Summary',
        'description': 'Compliance overview split by MDI / Non-MDI Band A / Non-MDI Non-Band A against band supply targets',
        'category': 'technical',
        'supports_chart': False,
        'config_options': {},
    },
    'feeder_segment_compliance': {
        'display_name': 'Feeder Compliance by Segment',
        'description': 'Per-feeder dispatch compliance table (Target vs Actual vs % Achieved) grouped by MDI / Non-MDI Band A / Non-MDI Non-Band A',
        'category': 'technical',
        'supports_chart': False,
        'config_options': {},
    },
    'customer_comparison': {
        'display_name': 'Customer Comparison',
        'description': 'Compare top MDI/MDNI customers by consumption across two periods',
        'category': 'commercial',
        'supports_chart': True,
        'config_options': {
            'customer_type': {
                'type': 'select',
                'options': ['MDI', 'MDNI', 'all'],
                'default': 'MDI',
            },
            'current_period': {'type': 'date_range', 'default': None},
            'previous_period': {'type': 'date_range', 'default': None},
            'scope_type': {
                'type': 'select',
                'options': ['feeder', 'district', 'state', 'station'],
                'default': None,
            },
            'scope_id': {'type': 'uuid', 'default': None},
            'top_n': {'type': 'number', 'default': 50},
            'include_insights': {'type': 'boolean', 'default': False},
        },
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
        if category == 'general':
            return True
        # 'comparison' sections (entity/period compare) are available to any
        # user who has at least one module — the compare engine enforces finer
        # metric-level access internally.
        if category == 'comparison':
            return bool(accessible_modules)
        return category in accessible_modules

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

    def __init__(self, filters, user=None):
        """
        Initialize with filters and optional user (required for comparison sections).

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
        self._user = user
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

        # Voltage-level breakdown (only when both 11kV and 33kV feeders are present)
        from common.models import Feeder as _Feeder
        vmap = dict(_Feeder.objects.filter(id__in=self.feeder_ids).values_list('id', 'voltage_level'))
        ids_11 = [fid for fid in self.feeder_ids if vmap.get(fid) == '11kv']
        ids_33 = [fid for fid in self.feeder_ids if vmap.get(fid) == '33kv']
        energy_11kv = energy_33kv = None
        if ids_11 and ids_33:
            energy_11kv = round(calculate_energy_delivered(ids_11, self.from_date, self.to_date)['total_mwh'], 2)
            energy_33kv = round(calculate_energy_delivered(ids_33, self.from_date, self.to_date)['total_mwh'], 2)
        
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
            'energy_11kv': energy_11kv,
            'energy_33kv': energy_33kv,
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
            dso_hourly   = hourly_qs.filter(submission_type='dso').count()
            admin_hourly = hourly_qs.filter(submission_type='admin_override').count()
            late_hourly  = hourly_qs.filter(is_late=True).count()

            # Energy reading submissions
            energy_qs = CumulativeMeterReading.objects.filter(
                feeder_id__in=feeder_ids,
                reading_date__range=(self.from_date, self.to_date),
            )
            dso_energy    = energy_qs.filter(submission_type='dso').count()
            admin_energy  = energy_qs.filter(submission_type='admin_override').count()
            late_energy   = energy_qs.filter(is_late=True).count()

            # Compliance is DSO submissions only — admin override means DSO didn't submit
            hourly_pct = round((dso_hourly / expected_hourly * 100) if expected_hourly else 0, 1)
            energy_pct = round((dso_energy / expected_energy * 100) if expected_energy else 0, 1)
            is_compliant = hourly_pct >= 80 and energy_pct >= 80
            if is_compliant:
                compliant_count += 1

            station_rows.append({
                'station_name':   station.name,
                'station_type':   station.get_station_type_display(),
                'feeder_count':   n,
                'expected_hourly': expected_hourly,
                'dso_hourly':     dso_hourly,
                'admin_hourly':   admin_hourly,
                'late_hourly':    late_hourly,
                'hourly_pct':     hourly_pct,
                'expected_energy': expected_energy,
                'dso_energy':     dso_energy,
                'admin_energy':   admin_energy,
                'late_energy':    late_energy,
                'energy_pct':     energy_pct,
                'is_compliant':   is_compliant,
            })

        # Best performing first (highest avg compliance %), worst last
        station_rows.sort(key=lambda r: -(r['hourly_pct'] + r['energy_pct']) / 2)

        compliance_rate = round((compliant_count / total_stations * 100) if total_stations else 0, 1)

        # Network-wide totals for dashboard summary
        net_exp_hourly   = sum(r['expected_hourly'] for r in station_rows)
        net_dso_hourly   = sum(r['dso_hourly']      for r in station_rows)
        net_admin_hourly = sum(r['admin_hourly']     for r in station_rows)
        net_exp_energy   = sum(r['expected_energy']  for r in station_rows)
        net_dso_energy   = sum(r['dso_energy']       for r in station_rows)
        net_admin_energy = sum(r['admin_energy']     for r in station_rows)

        return {
            'total_stations':      total_stations,
            'compliant_count':     compliant_count,
            'non_compliant_count': total_stations - compliant_count,
            'compliance_rate':     compliance_rate,
            'period_days':         self.period_days,
            'period': (
                f"{self.from_date.strftime('%d %b %Y')} – "
                f"{self.to_date.strftime('%d %b %Y')}"
            ),
            'net_exp_hourly':   net_exp_hourly,
            'net_dso_hourly':   net_dso_hourly,
            'net_admin_hourly': net_admin_hourly,
            'net_exp_energy':   net_exp_energy,
            'net_dso_energy':   net_dso_energy,
            'net_admin_energy': net_admin_energy,
            'stations': station_rows,
        }

    # =========================================================================
    # COMPARISON DATA METHODS
    # These delegate to the analytics compare engine, reusing the same logic
    # and access-control that powers the standalone compare endpoints.
    # =========================================================================

    def get_entity_comparison_data(self, config, user):
        from analytics.services.compare_service import compare_entities

        entity_type   = config.get('entity_type', 'district')
        entity_ids    = config.get('entity_ids', [])
        metrics       = config.get('metrics', [])
        granularity   = config.get('granularity', 'monthly')
        feeder_type   = config.get('feeder_type') or None
        include_trend = bool(config.get('include_trend', True))

        if not entity_ids or not metrics:
            return {'error': 'entity_ids and metrics are required for entity_comparison'}

        try:
            return compare_entities(
                user=user,
                entity_type=entity_type,
                entity_ids=entity_ids,
                metrics=metrics,
                from_date=self.from_date,
                to_date=self.to_date,
                feeder_type=feeder_type,
                granularity=granularity,
                include_trend=include_trend,
            )
        except Exception as exc:
            logger.error("entity_comparison failed in report: %s", exc)
            return {'error': str(exc)}

    def get_period_comparison_data(self, config, user):
        from analytics.services.compare_service import compare_periods

        entity_type = config.get('entity_type', 'feeder')
        entity_id   = config.get('entity_id')
        metrics     = config.get('metrics', [])
        feeder_type = config.get('feeder_type') or None
        periods     = config.get('periods', [])

        if not entity_id or not metrics:
            return {'error': 'entity_id and metrics are required for period_comparison'}

        try:
            return compare_periods(
                user=user,
                entity_type=entity_type,
                entity_id=entity_id,
                metrics=metrics,
                periods=periods,
                feeder_type=feeder_type,
            )
        except Exception as exc:
            logger.error("period_comparison failed in report: %s", exc)
            return {'error': str(exc)}

    def get_customer_comparison_data(self, config, user):
        from datetime import timedelta

        from analytics.services.compare_service import compare_customers, user_has_permission

        if not user_has_permission(user, 'view_customer_comparison'):
            return {'error': 'No permission for customer comparison'}

        customer_type   = config.get('customer_type', 'all')
        current_period  = config.get('current_period')
        previous_period = config.get('previous_period')

        if current_period and previous_period:
            try:
                from datetime import datetime
                current_from  = datetime.strptime(str(current_period['from_date'])[:10], '%Y-%m-%d').date()
                current_to    = datetime.strptime(str(current_period['to_date'])[:10],   '%Y-%m-%d').date()
                previous_from = datetime.strptime(str(previous_period['from_date'])[:10], '%Y-%m-%d').date()
                previous_to   = datetime.strptime(str(previous_period['to_date'])[:10],   '%Y-%m-%d').date()
            except (KeyError, ValueError, TypeError) as exc:
                return {'error': f'Invalid period format in customer_comparison config: {exc}'}
        else:
            # Fall back to the report's main date range as current period
            current_from  = self.from_date
            current_to    = self.to_date
            previous_to   = current_from - timedelta(days=1)
            previous_from = previous_to - timedelta(days=(current_to - current_from).days)

        sort_by = config.get('sort_by', 'current_consumption')
        if sort_by not in ('current_consumption', 'variance_pct', 'decline'):
            sort_by = 'current_consumption'

        try:
            result = compare_customers(
                user=user,
                current_from=current_from,
                current_to=current_to,
                previous_from=previous_from,
                previous_to=previous_to,
                customer_type=customer_type,
                scope_type=config.get('scope_type') or None,
                scope_id=config.get('scope_id') or None,
                customer_ids=config.get('customer_ids') or None,
                select_all=bool(config.get('select_all', False)),
                top_n=min(int(config.get('top_n', 50)), 200),
                sort_by=sort_by,
                positive_threshold=float(config.get('positive_threshold', 10.0)),
                declined_threshold=float(config.get('declined_threshold', -30.0)),
            )
        except Exception as exc:
            logger.error("customer_comparison failed in report: %s", exc)
            return {'error': str(exc)}

        if config.get('include_insights') and 'error' not in result:
            try:
                from analytics.services.ai_insights import get_comparison_insights
                result['ai_insights'] = get_comparison_insights(
                    comparison=result,
                    customer_type=customer_type,
                    current_from=current_from,
                    current_to=current_to,
                    previous_from=previous_from,
                    previous_to=previous_to,
                    scope_type=config.get('scope_type') or None,
                    scope_id=config.get('scope_id') or None,
                    positive_threshold=float(config.get('positive_threshold', 10.0)),
                    declined_threshold=float(config.get('declined_threshold', -30.0)),
                )
            except Exception as exc:
                logger.warning("AI insights failed in report: %s", exc)
                result['ai_insights'] = None

        return result

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

    def _get_tmo_service(self):
        if not hasattr(self, '_tmo_service_instance'):
            from reports.tmo_service import TMOReportService
            self._tmo_service_instance = TMOReportService({
                'from_date':  self.filters.get('from_date'),
                'to_date':    self.filters.get('to_date'),
                'feeder_ids': self.feeder_ids,
            })
        return self._tmo_service_instance

    # =========================================================================
    # MASTER DISPATCHER
    # =========================================================================

    # Section types owned by each sub-service
    _HR_SECTIONS         = {'hr_overview', 'staff_metrics', 'wage_bill_analysis',
                            'department_headcount', 'attrition_analysis', 'recruitment_summary'}
    _COMMERCIAL_SECTIONS = {'commercial_overview', 'revenue_by_district', 'customer_type_summary'}
    _FINANCIAL_SECTIONS  = {'financial_overview', 'opex_by_category', 'opex_by_district'}
    _COMPARISON_SECTIONS = {'entity_comparison', 'period_comparison', 'customer_comparison'}
    _TMO_SECTIONS        = {'tmo_feeder_dispatch', 'tmo_collection_performance', 'tmo_billing_efficiency'}

    def get_feeder_segment_compliance(self):
        """
        Return feeder performance annotated with:
        - segment: MDI | Non-MDI Band A | Non-MDI Non-Band A
        - target_hours: band-based supply target (hrs/day)
        - gap: actual - target
        - pct_achieved: (actual / target) * 100
        - status: exceeding | on_target | below_target | poor | critical

        Segment derivation:
          MDI           = feeder has at least one CommercialCustomer with customer_type='MDI'
          Non-MDI Band A = feeder band is A and no MDI customers
          Non-MDI Non-Band A = everything else
        """
        try:
            from commercial.models import CommercialCustomer
            mdi_fids = set(
                str(fid) for fid in
                CommercialCustomer.objects.filter(
                    feeder_id__in=self.feeder_ids,
                    customer_type='MDI',
                ).values_list('feeder_id', flat=True).distinct()
            )
        except Exception:
            mdi_fids = set()

        feeders_perf = self.get_feeder_performance()
        SEGMENT_ORDER = {'MDI': 0, 'Non-MDI Band A': 1, 'Non-MDI Non-Band A': 2}
        result = []
        for f in feeders_perf:
            fid  = str(f.get('id', ''))
            band = str(f.get('band') or 'E').upper().strip('-')
            band = band if band in BAND_SUPPLY_TARGETS else 'E'

            if fid in mdi_fids:
                segment = 'MDI'
            elif band == 'A':
                segment = 'Non-MDI Band A'
            else:
                segment = 'Non-MDI Non-Band A'

            target       = BAND_SUPPLY_TARGETS.get(band, 8.0)
            actual       = float(f.get('hours_of_supply', 0) or 0)
            gap          = round(actual - target, 2)
            pct_achieved = round((actual / target) * 100, 1) if target > 0 else 0
            status       = _compliance_status(pct_achieved)

            result.append({
                **f,
                'segment':      segment,
                'target_hours': target,
                'gap':          gap,
                'pct_achieved': pct_achieved,
                'status':       status,
            })

        result.sort(key=lambda x: (SEGMENT_ORDER.get(x['segment'], 3), x.get('name', '')))
        return result

    def get_segment_compliance_summary(self):
        """
        Aggregate feeder compliance by segment.  Returns a list of segment dicts, each with:
        - segment, total, avg_supply, avg_pct_achieved
        - counts/pct for each status (exceeding, on_target, below_target, poor, critical)
        """
        data     = self.get_feeder_segment_compliance()
        segments = ['MDI', 'Non-MDI Band A', 'Non-MDI Non-Band A']
        statuses = ['exceeding', 'on_target', 'below_target', 'poor', 'critical']
        result   = []
        for seg in segments:
            fds   = [f for f in data if f['segment'] == seg]
            total = len(fds)
            if total == 0:
                continue
            row = {'segment': seg, 'total': total}
            for st in statuses:
                cnt      = sum(1 for f in fds if f['status'] == st)
                row[st]  = {'count': cnt, 'pct': round(cnt / total * 100, 1)}
            row['avg_supply']       = round(sum(float(f.get('hours_of_supply', 0) or 0) for f in fds) / total, 2)
            row['avg_pct_achieved'] = round(sum(float(f.get('pct_achieved', 0) or 0) for f in fds) / total, 1)
            row['total_energy_mwh'] = round(sum(float(f.get('energy_delivered', 0) or 0) for f in fds), 2)
            result.append(row)
        return result


    def get_energy_by_segment_pl(self):
        """
        Total energy delivered split by P&L segment:
          MDI, Non-MDI (MDNI), and overall totals.
        Returns a dict with segment breakdown + % share.
        """
        from common.models import Feeder as _Feeder

        try:
            from commercial.models import CommercialCustomer
            mdi_fids = set(
                str(fid) for fid in
                CommercialCustomer.objects.filter(
                    feeder_id__in=self.feeder_ids, customer_type='MDI'
                ).values_list('feeder_id', flat=True).distinct()
            )
        except Exception:
            mdi_fids = set()

        feeders_perf = self.get_feeder_performance()
        total        = sum(float(f.get('energy_delivered', 0) or 0) for f in feeders_perf)
        mdi_energy   = sum(float(f.get('energy_delivered', 0) or 0)
                           for f in feeders_perf if str(f.get('id', '')) in mdi_fids)
        mdni_energy  = total - mdi_energy

        def _pct(val):
            return round((val / total) * 100, 1) if total > 0 else 0

        return {
            'total_energy_mwh':  round(total, 2),
            'mdi_energy_mwh':    round(mdi_energy, 2),
            'mdni_energy_mwh':   round(mdni_energy, 2),
            'mdi_pct':           _pct(mdi_energy),
            'mdni_pct':          _pct(mdni_energy),
            'segments': [
                {
                    'label':      'MDI',
                    'energy_mwh': round(mdi_energy, 2),
                    'pct':        _pct(mdi_energy),
                    'feeders':    sum(1 for f in feeders_perf if str(f.get('id','')) in mdi_fids),
                },
                {
                    'label':      'Non-MDI (MDNI)',
                    'energy_mwh': round(mdni_energy, 2),
                    'pct':        _pct(mdni_energy),
                    'feeders':    sum(1 for f in feeders_perf if str(f.get('id','')) not in mdi_fids),
                },
            ],
        }

    def get_segment_voltage_energy(self):
        """
        Energy delivered broken down by Segment × Voltage:
          MDI 33kV, MDI 11kV, Non-MDI 33kV, Non-MDI 11kV
        """
        from common.models import Feeder as _Feeder

        try:
            from commercial.models import CommercialCustomer
            mdi_fids = set(
                str(fid) for fid in
                CommercialCustomer.objects.filter(
                    feeder_id__in=self.feeder_ids, customer_type='MDI'
                ).values_list('feeder_id', flat=True).distinct()
            )
        except Exception:
            mdi_fids = set()

        # Get voltage level per feeder
        voltage_map = {
            str(fid): vl
            for fid, vl in _Feeder.objects.filter(id__in=self.feeder_ids)
                                          .values_list('id', 'voltage_level')
        }

        feeders_perf = self.get_feeder_performance()

        buckets = {
            'MDI 33kV':     0.0,
            'MDI 11kV':     0.0,
            'Non-MDI 33kV': 0.0,
            'Non-MDI 11kV': 0.0,
        }
        for f in feeders_perf:
            fid     = str(f.get('id', ''))
            energy  = float(f.get('energy_delivered', 0) or 0)
            voltage = str(voltage_map.get(fid, '11kv')).lower()
            is_mdi  = fid in mdi_fids
            seg     = 'MDI' if is_mdi else 'Non-MDI'
            vkey    = '33kV' if '33' in voltage else '11kV'
            key     = f'{seg} {vkey}'
            buckets[key] = buckets.get(key, 0.0) + energy

        total = sum(buckets.values())

        def _pct(v):
            return round((v / total) * 100, 1) if total > 0 else 0

        rows = [
            {'label': k, 'energy_mwh': round(v, 2), 'pct': _pct(v)}
            for k, v in buckets.items()
        ]
        return {
            'total_energy_mwh': round(total, 2),
            'rows': rows,
            'mdi_total':  round(buckets['MDI 33kV'] + buckets['MDI 11kV'], 2),
            'mdni_total': round(buckets['Non-MDI 33kV'] + buckets['Non-MDI 11kV'], 2),
        }

    def get_energy_md_nmd_mix(self):
        """
        MD (MDI) vs NMD (Non-MDI) % share of energy dispatched.
        Includes target mix (60% MD / 40% NMD) vs actual.
        """
        pl_data     = self.get_energy_by_segment_pl()
        total       = pl_data['total_energy_mwh']
        md_actual   = pl_data['mdi_pct']
        nmd_actual  = pl_data['mdni_pct']

        return {
            'total_energy_mwh': total,
            'md_target_pct':    60.0,
            'nmd_target_pct':   40.0,
            'md_actual_pct':    md_actual,
            'nmd_actual_pct':   nmd_actual,
            'md_energy_mwh':    pl_data['mdi_energy_mwh'],
            'nmd_energy_mwh':   pl_data['mdni_energy_mwh'],
            'md_gap_pct':       round(md_actual - 60.0, 1),
        }

    def get_segment_compliance_trend(self):
        """
        Daily compliance % for each segment over the report period.
        For each day: compute avg supply hrs vs target for MDI/Non-MDI Band A/Non-MDI Non-Band A,
        then compute % of feeders that are 'on target or better' (>= 95%).
        """
        from common.models import Feeder as _Feeder
        from django.db.models import Count

        try:
            from commercial.models import CommercialCustomer
            mdi_fids = set(
                str(fid) for fid in
                CommercialCustomer.objects.filter(
                    feeder_id__in=self.feeder_ids, customer_type='MDI'
                ).values_list('feeder_id', flat=True).distinct()
            )
        except Exception:
            mdi_fids = set()

        # Get band per feeder
        band_map = {
            str(fid): (name or 'E').upper()
            for fid, name in _Feeder.objects.filter(id__in=self.feeder_ids)
                                            .values_list('id', 'band__name')
        }

        # Classify feeders into segments once
        def _seg(fid_str):
            if fid_str in mdi_fids:
                return 'MDI'
            if band_map.get(fid_str, 'E') == 'A':
                return 'Non-MDI Band A'
            return 'Non-MDI Non-Band A'

        fid_strs = [str(fid) for fid in self.feeder_ids]
        seg_by_fid = {fid: _seg(fid) for fid in fid_strs}

        # Group feeder ids by segment
        segs = {
            'MDI':              [fid for fid, s in seg_by_fid.items() if s == 'MDI'],
            'Non-MDI Band A':   [fid for fid, s in seg_by_fid.items() if s == 'Non-MDI Band A'],
            'Non-MDI Non-Band A': [fid for fid, s in seg_by_fid.items() if s == 'Non-MDI Non-Band A'],
        }

        # Build daily series
        import datetime as _dt
        trend = []
        current = self.from_date
        while current <= self.to_date:
            day_entry = {'date': current.strftime('%Y-%m-%d')}
            for seg_name, fids in segs.items():
                if not fids:
                    day_entry[seg_name] = None
                    continue
                # Count hours on (load > 0) per feeder for this day
                on_target_count = 0
                for fid in fids:
                    hrs_on = HourlyLoad.objects.filter(
                        feeder_id=fid, date=current, load_mw__gt=0
                    ).count()
                    # Get band for this feeder
                    band   = band_map.get(fid, 'E')
                    target = BAND_SUPPLY_TARGETS.get(band, 8.0)
                    pct    = (hrs_on / target) * 100 if target > 0 else 0
                    if pct >= 95:
                        on_target_count += 1
                day_entry[seg_name] = round((on_target_count / len(fids)) * 100, 1)
            trend.append(day_entry)
            current += _dt.timedelta(days=1)

        return {
            'trend':    trend,
            'segments': list(segs.keys()),
        }
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
        if section_type in self._TMO_SECTIONS:
            return self._get_tmo_service().get_all_section_data(section_type, config)

        # ── Comparison sections — require user in context ─────────────────────
        if section_type in self._COMPARISON_SECTIONS:
            user = self._user
            if user is None:
                return {'error': 'User context required for comparison sections'}
            if section_type == 'entity_comparison':
                return self.get_entity_comparison_data(config, user)
            if section_type == 'period_comparison':
                return self.get_period_comparison_data(config, user)
            if section_type == 'customer_comparison':
                return self.get_customer_comparison_data(config, user)

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
            'dso_compliance_overview':      self.get_dso_compliance_data,
            'dso_compliance_table':          self.get_dso_compliance_data,
            'segment_compliance_summary':     self.get_segment_compliance_summary,
            'feeder_segment_compliance':      self.get_feeder_segment_compliance,
            'energy_by_segment_pl':           self.get_energy_by_segment_pl,
            'segment_voltage_energy':         self.get_segment_voltage_energy,
            'energy_md_nmd_mix':              self.get_energy_md_nmd_mix,
            'segment_compliance_trend':       self.get_segment_compliance_trend,
            'custom_text': lambda: config,
            'gaps_improvements': lambda: config,
        }

        method = data_methods.get(section_type)
        if method:
            return method()

        return {}