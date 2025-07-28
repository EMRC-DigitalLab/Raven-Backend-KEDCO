# analytics/admin.py
from django.contrib import admin
from .models import MonthlyOverviewSummary


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