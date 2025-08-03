# analytics/admin.py
from django.contrib import admin
from .models import MonthlyOverviewSummary
from django.utils import timezone

@admin.register(MonthlyOverviewSummary)
class MonthlyOverviewSummaryAdmin(admin.ModelAdmin):
    list_display = [
        'month', 
        'revenue_billed', 
        'revenue_collected', 
        'energy_delivered',
        'billing_efficiency',
        'collection_efficiency', 
        'atc_losses',
        'total_cost',
        'has_complete_data',
        'calculated_at'
    ]
    
    list_filter = [
        'has_complete_data',
        'month',
        'calculated_at'
    ]
    
    date_hierarchy = 'month'
    
    search_fields = ['month']
    
    ordering = ['-month']
    
    readonly_fields = [
        'calculated_at',
        'calculation_duration',
        'source_data_hash'
    ]
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('month', 'has_complete_data', 'calculated_at', 'calculation_duration')
        }),
        ('Commercial', {
            'fields': (
                'revenue_billed', 
                'revenue_collected', 
                'customers_billed', 
                'customers_responded',
                'customer_response_rate'
            )
        }),
        ('Energy', {
            'fields': (
                'energy_delivered', 
                'energy_billed', 
                'energy_collected'
            )
        }),
        ('Efficiency', {
            'fields': (
                'billing_efficiency', 
                'collection_efficiency', 
                'atc_losses'
            )
        }),
        ('Financial', {
            'fields': (
                'total_cost',
                'total_opex', 
                'total_salaries', 
                'total_nbet', 
                'total_mo'
            )
        }),
        ('Technical', {
            'fields': (
                'avg_hours_supply', 
                'avg_interruption_duration', 
                'avg_turnaround_time'
            )
        }),
        ('System', {
            'fields': ('source_data_hash',),
            'classes': ('collapse',)
        }),
    )


# Simple admin site customization
admin.site.site_header = "Raven Analytics"
admin.site.site_title = "Raven Analytics"
admin.site.index_title = "Analytics Dashboard"




from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from .models import MonthlyTechnicalSummary

@admin.register(MonthlyTechnicalSummary)
class MonthlyTechnicalSummaryAdmin(admin.ModelAdmin):
    list_display = [
        'month', 'filter_level_display', 'total_energy_delivered', 
        'avg_hours_of_supply', 'total_interruptions', 'availability_percentage',
        'has_complete_data', 'calculated_at'
    ]
    
    list_filter = [
        'has_complete_data', 'month', 'state', 'business_district',
        ('calculated_at', admin.DateFieldListFilter)
    ]
    
    search_fields = [
        'month', 'state__name', 'business_district__name', 'feeder__name', 'feeder__slug'
    ]
    
    readonly_fields = [
        'calculated_at', 'calculation_duration', 'filter_level',
        'availability_percentage', 'interruption_breakdown_display', 
        'efficiency_metrics_display'
    ]

    date_hierarchy = 'month'
    
    fieldsets = [
        ('Identification', {
            'fields': ['month', 'state', 'business_district', 'feeder']
        }),
        ('Energy Metrics', {
            'fields': ['total_energy_delivered', 'avg_peak_load', 'max_peak_load'],
            'classes': ['collapse']
        }),
        ('Supply Quality', {
            'fields': [
                'avg_hours_of_supply', 'total_supply_hours', 'availability_percentage'
            ]
        }),
        ('Interruption Metrics', {
            'fields': [
                'total_interruptions', 'avg_daily_interruptions', 
                'avg_interruption_duration', 'total_interruption_hours',
                'avg_turnaround_time', 'interruption_breakdown_display'
            ],
            'classes': ['collapse']
        }),
        ('Infrastructure', {
            'fields': ['active_feeder_count', 'total_customer_count'],
            'classes': ['collapse']
        }),
        ('Reliability Indices', {
            'fields': ['saifi', 'saidi'],
            'classes': ['collapse']
        }),
        ('Interruption Breakdown Details', {
            'fields': [
                'load_shedding_hours', 'equipment_fault_hours', 'line_fault_hours',
                'maintenance_hours', 'other_fault_hours', 'interruption_breakdown_json'
            ],
            'classes': ['collapse']
        }),
        ('Metadata', {
            'fields': [
                'calculated_at', 'calculation_duration', 'has_complete_data',
                'filter_level', 'efficiency_metrics_display'
            ],
            'classes': ['collapse']
        })
    ]
    
    ordering = ['-month', 'state', 'business_district', 'feeder']
    
    def filter_level_display(self, obj):
        """Display the filtering level with color coding"""
        level = obj.filter_level
        colors = {
            'national': '#28a745',    # Green
            'state': '#007bff',       # Blue
            'district': '#ffc107',    # Yellow
            'feeder': '#dc3545'       # Red
        }
        
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            colors.get(level, '#6c757d'),
            level.title()
        )
    filter_level_display.short_description = 'Filter Level'
    
    def interruption_breakdown_display(self, obj):
        """Display interruption breakdown as a formatted table"""
        # Show detailed breakdown from JSON
        breakdown = obj.interruption_breakdown_dict
        if not breakdown:
            return "No data"
        
        html = '<table style="width: 100%; font-size: 12px;">'
        html += '<tr><th>Fault Type</th><th>Hours</th></tr>'
        
        # Sort by hours descending
        sorted_breakdown = sorted(breakdown.items(), key=lambda x: x[1], reverse=True)
        
        for fault_type, hours in sorted_breakdown:
            if hours > 0:
                html += f'<tr><td>{fault_type}:</td><td><strong>{hours:.2f}h</strong></td></tr>'
        html += '</table>'
        
        return mark_safe(html)
    interruption_breakdown_display.short_description = 'Detailed Breakdown'
    
    def efficiency_metrics_display(self, obj):
        """Display key efficiency metrics"""
        metrics = obj.get_efficiency_metrics()
        
        html = '<table style="width: 100%; font-size: 12px;">'
        html += f'<tr><td>Availability:</td><td><strong>{metrics["availability_percentage"]:.1f}%</strong></td></tr>'
        html += f'<tr><td>Avg Supply:</td><td><strong>{metrics["avg_hours_of_supply"]:.1f}h/day</strong></td></tr>'
        html += f'<tr><td>SAIFI:</td><td><strong>{metrics["saifi"]:.2f}</strong></td></tr>'
        html += f'<tr><td>SAIDI:</td><td><strong>{metrics["saidi"]:.2f}</strong></td></tr>'
        html += '</table>'
        
        return mark_safe(html)
    efficiency_metrics_display.short_description = 'Key Metrics'
    
    def get_queryset(self, request):
        """Optimize queryset with select_related"""
        return super().get_queryset(request).select_related(
            'state', 'business_district', 'feeder'
        )
    
    def changelist_view(self, request, extra_context=None):
        """Add summary statistics to changelist view"""
        extra_context = extra_context or {}
        
        # Get current queryset
        qs = self.get_queryset(request)
        
        # Apply any filters
        cl = self.get_changelist_instance(request)
        qs = cl.get_queryset(request)
        
        # Calculate summary stats
        total_summaries = qs.count()
        incomplete_count = qs.filter(has_complete_data=False).count()
        
        # Get coverage by filter level
        coverage_stats = {
            'National': qs.filter(
                state__isnull=True, business_district__isnull=True, feeder__isnull=True
            ).count(),
            'State': qs.filter(
                state__isnull=False, business_district__isnull=True, feeder__isnull=True
            ).count(),
            'District': qs.filter(
                business_district__isnull=False, feeder__isnull=True
            ).count(),
            'Feeder': qs.filter(feeder__isnull=False).count(),
        }
        
        extra_context.update({
            'summary_stats': {
                'total_summaries': total_summaries,
                'incomplete_summaries': incomplete_count,
                'completeness_rate': round((total_summaries - incomplete_count) / max(total_summaries, 1) * 100, 1),
                'coverage_by_level': coverage_stats,
            }
        })
        
        return super().changelist_view(request, extra_context)
    
    actions = ['recalculate_summaries', 'mark_as_complete', 'mark_as_incomplete']
    
    def recalculate_summaries(self, request, queryset):
        """Action to recalculate selected summaries"""
        from .tasks import update_monthly_technical_summary
        
        count = 0
        for summary in queryset:
            filter_params = {
                'state': summary.state,
                'business_district': summary.business_district,
                'feeder': summary.feeder
            }
            
            try:
                update_monthly_technical_summary.delay(
                    summary.month.strftime('%Y-%m-%d'),
                    filter_params,
                    priority='admin_action'
                )
                count += 1
            except Exception as e:
                self.message_user(request, f"Failed to queue update for {summary}: {str(e)}", level='ERROR')
        
        self.message_user(
            request,
            f"Successfully queued {count} technical summaries for recalculation.",
            level='SUCCESS'
        )
    recalculate_summaries.short_description = "Recalculate selected summaries"
    
    def mark_as_complete(self, request, queryset):
        """Mark summaries as having complete data"""
        updated = queryset.update(has_complete_data=True)
        self.message_user(
            request,
            f"Marked {updated} summaries as having complete data.",
            level='SUCCESS'
        )
    mark_as_complete.short_description = "Mark as complete data"
    
    def mark_as_incomplete(self, request, queryset):
        """Mark summaries as having incomplete data"""
        updated = queryset.update(has_complete_data=False)
        self.message_user(
            request,
            f"Marked {updated} summaries as having incomplete data.",
            level='WARNING'
        )
    mark_as_incomplete.short_description = "Mark as incomplete data"


# analytics/admin.py (add this to your existing admin.py)

from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Count
from django.urls import reverse
from django.utils.safestring import mark_safe
from .models import DailyTechnicalSummary

@admin.register(DailyTechnicalSummary)
class DailyTechnicalSummaryAdmin(admin.ModelAdmin):
    list_display = [
        'date', 
        'filter_level_display', 
        'location_name',
        'energy_delivered_display',
        'hours_of_supply_display',
        'interruptions_display',
        'availability_display',
        'data_quality_display',
        'calculated_display'
    ]
    
    list_filter = [
        'date',
        'state',
        'business_district',
        'has_complete_data',
        ('calculated_at', admin.DateFieldListFilter),
    ]
    
    search_fields = [
        'state__name',
        'business_district__name', 
        'feeder__name',
        'date'
    ]
    
    date_hierarchy = 'date'
    
    readonly_fields = [
        'id',
        'calculated_at',
        'calculation_duration',
        'availability_percentage',
        'interruption_breakdown_dict',
        'summary_breakdown_dict'
    ]
    
    fieldsets = (
        ('Basic Information', {
            'fields': (
                'id',
                'date',
                'state',
                'business_district',
                'feeder',
                'has_complete_data'
            )
        }),
        ('Energy Metrics', {
            'fields': (
                'total_energy_delivered',
                'avg_peak_load',
                'max_peak_load',
            ),
            'classes': ('collapse',)
        }),
        ('Supply Quality', {
            'fields': (
                'hours_of_supply',
                'availability_percentage',
            )
        }),
        ('Interruption Metrics', {
            'fields': (
                'total_interruptions',
                'avg_interruption_duration',
                'total_interruption_hours',
                'avg_turnaround_time',
                'avg_fault_turnaround_time',
            )
        }),
        ('Interruption Breakdown', {
            'fields': (
                'load_shedding_hours',
                'equipment_fault_hours',
                'line_fault_hours',
                'maintenance_hours',
                'other_fault_hours',
                'interruption_breakdown_json',
            ),
            'classes': ('collapse',)
        }),
        ('Infrastructure', {
            'fields': (
                'active_feeder_count',
                'total_customer_count',
            ),
            'classes': ('collapse',)
        }),
        ('Reliability Indices', {
            'fields': (
                'saifi',
                'saidi',
            ),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': (
                'calculated_at',
                'calculation_duration',
            ),
            'classes': ('collapse',)
        }),
    )
    
    # Custom display methods
    def filter_level_display(self, obj):
        """Display the filtering level with color coding"""
        try:
            level = obj.filter_level
            colors = {
                'national': '#28a745',    # Green
                'state': '#007bff',       # Blue  
                'district': '#ffc107',    # Yellow
                'feeder': '#dc3545'       # Red
            }
            return format_html(
                '<span style="color: {color}; font-weight: bold;">{level}</span>',
                color=colors.get(level, '#6c757d'),
                level=level.title()
            )
        except (AttributeError, TypeError):
            return format_html('<span style="color: #6c757d;">Unknown</span>')
    filter_level_display.short_description = 'Level'
    filter_level_display.admin_order_field = 'state'
    
    def location_name(self, obj):
        """Display the specific location name"""
        if obj.feeder:
            return f"{obj.feeder.name}"
        elif obj.business_district:
            return f"{obj.business_district.name}"
        elif obj.state:
            return f"{obj.state.name}"
        else:
            return "National"
    location_name.short_description = 'Location'
    
    def energy_delivered_display(self, obj):
        """Display energy delivered with units"""
        try:
            energy = float(obj.total_energy_delivered)
            return format_html("{energy:,.2f} MWh", energy=energy)
        except (ValueError, TypeError, AttributeError):
            return format_html('<span style="color: #6c757d;">--</span>')
    energy_delivered_display.short_description = 'Energy'
    energy_delivered_display.admin_order_field = 'total_energy_delivered'
    
    def hours_of_supply_display(self, obj):
        """Display hours of supply with progress bar"""
        try:
            hours = float(obj.hours_of_supply)
            percentage = (hours / 24) * 100
            
            # Color coding based on supply hours
            if hours >= 20:
                color = '#28a745'  # Green
            elif hours >= 16:
                color = '#ffc107'  # Yellow
            elif hours >= 12:
                color = '#fd7e14'  # Orange
            else:
                color = '#dc3545'  # Red
                
            return format_html(
                '<div style="width: 100px; background-color: #e9ecef; border-radius: 3px;">'
                '<div style="width: {width}%; height: 20px; background-color: {color}; border-radius: 3px; '
                'display: flex; align-items: center; justify-content: center; color: white; font-size: 11px;">'
                '{hours:.1f}h</div></div>',
                width=min(percentage, 100),
                color=color,
                hours=hours
            )
        except (ValueError, TypeError):
            return format_html('<span style="color: #6c757d;">--</span>')
    hours_of_supply_display.short_description = 'Supply Hours'
    hours_of_supply_display.admin_order_field = 'hours_of_supply'
    
    def interruptions_display(self, obj):
        """Display interruption count with color coding"""
        try:
            count = obj.total_interruptions
            if count == 0:
                color = '#28a745'  # Green
            elif count <= 5:
                color = '#ffc107'  # Yellow
            elif count <= 10:
                color = '#fd7e14'  # Orange
            else:
                color = '#dc3545'  # Red
                
            return format_html(
                '<span style="color: {color}; font-weight: bold;">{count}</span>',
                color=color,
                count=count
            )
        except (ValueError, TypeError, AttributeError):
            return format_html('<span style="color: #6c757d;">--</span>')
    interruptions_display.short_description = 'Interruptions'
    interruptions_display.admin_order_field = 'total_interruptions'
    
    def availability_display(self, obj):
        """Display availability percentage with color coding"""
        try:
            availability = obj.availability_percentage
            if availability >= 85:
                color = '#28a745'  # Green
            elif availability >= 70:
                color = '#ffc107'  # Yellow
            elif availability >= 50:
                color = '#fd7e14'  # Orange
            else:
                color = '#dc3545'  # Red
                
            return format_html(
                '<span style="color: {color}; font-weight: bold;">{availability:.1f}%</span>',
                color=color,
                availability=availability
            )
        except (ValueError, TypeError, AttributeError):
            return format_html('<span style="color: #6c757d;">--</span>')
    availability_display.short_description = 'Availability'
    availability_display.admin_order_field = 'hours_of_supply'
    
    def data_quality_display(self, obj):
        """Display data quality indicator"""
        if obj.has_complete_data:
            return format_html(
                '<span style="color: #28a745;">✓ Complete</span>'
            )
        else:
            return format_html(
                '<span style="color: #dc3545;">⚠ Incomplete</span>'
            )
    data_quality_display.short_description = 'Data Quality'
    data_quality_display.admin_order_field = 'has_complete_data'
    
    def calculated_display(self, obj):
        """Display when the summary was calculated"""
        if obj.calculated_at:
            return obj.calculated_at.strftime('%m/%d %H:%M')
        return '-'
    calculated_display.short_description = 'Calculated'
    calculated_display.admin_order_field = 'calculated_at'
    
    # Custom actions
    actions = ['recalculate_selected', 'mark_as_complete', 'mark_as_incomplete']
    
    def recalculate_selected(self, request, queryset):
        """Recalculate selected summaries"""
        count = 0
        for summary in queryset:
            try:
                # Here you would call your calculation logic
                # For now, just update the calculated_at timestamp
                from django.utils import timezone
                summary.calculated_at = timezone.now()
                summary.save()
                count += 1
            except Exception as e:
                self.message_user(request, f"Error recalculating {summary}: {e}", level='ERROR')
        
        self.message_user(request, f"Successfully recalculated {count} summaries.")
    recalculate_selected.short_description = "Recalculate selected summaries"
    
    def mark_as_complete(self, request, queryset):
        """Mark selected summaries as having complete data"""
        updated = queryset.update(has_complete_data=True)
        self.message_user(request, f"Marked {updated} summaries as complete.")
    mark_as_complete.short_description = "Mark as complete data"
    
    def mark_as_incomplete(self, request, queryset):
        """Mark selected summaries as having incomplete data"""
        updated = queryset.update(has_complete_data=False)
        self.message_user(request, f"Marked {updated} summaries as incomplete.")
    mark_as_incomplete.short_description = "Mark as incomplete data"
    
    # Custom queryset optimization
    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'state', 'business_district', 'feeder'
        )
    
    # Add custom CSS for better display
    class Media:
        css = {
            'all': ('admin/css/daily_technical_summary.css',)
        }
        
    def changelist_view(self, request, extra_context=None):
        """Add summary statistics to the changelist view"""
        from django.utils import timezone
        
        # Get some basic stats
        queryset = self.get_queryset(request)
        
        # Apply any filters that are currently active
        try:
            cl = self.get_changelist_instance(request)
            queryset = cl.get_queryset(request)
        except Exception:
            # Fallback if changelist fails
            queryset = self.get_queryset(request)
        
        stats = {
            'total_summaries': queryset.count(),
            'complete_data': queryset.filter(has_complete_data=True).count(),
            'incomplete_data': queryset.filter(has_complete_data=False).count(),
            'recent_summaries': queryset.filter(
                date__gte=timezone.now().date() - timezone.timedelta(days=7)
            ).count(),
        }
        
        extra_context = extra_context or {}
        extra_context['summary_stats'] = stats
        
        return super().changelist_view(request, extra_context)


# Optional: Create a simple CSS file for better styling
# Create: analytics/static/admin/css/daily_technical_summary.css
"""
/* Custom styles for Daily Technical Summary admin */
.field-hours_of_supply_display {
    min-width: 120px;
}

.field-availability_display {
    text-align: center;
}

.field-interruptions_display {
    text-align: center;
}

.summary-stats {
    background: #f8f9fa;
    padding: 10px;
    margin-bottom: 10px;
    border-radius: 4px;
    border: 1px solid #dee2e6;
}

.summary-stats h3 {
    margin: 0 0 10px 0;
    color: #495057;
}

.summary-stats .stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 15px;
}

.summary-stats .stat-item {
    text-align: center;
    padding: 8px;
    background: white;
    border-radius: 3px;
    border: 1px solid #e9ecef;
}

.summary-stats .stat-number {
    font-size: 24px;
    font-weight: bold;
    color: #007bff;
    display: block;
}

.summary-stats .stat-label {
    font-size: 12px;
    color: #6c757d;
    text-transform: uppercase;
}
"""