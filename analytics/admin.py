# analytics/admin.py
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse, path
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.utils.safestring import mark_safe
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta # type: ignore
import json

from .models import MonthlyOverviewSummary
from .tasks import update_monthly_overview_summary, health_check_summaries
from .signals import trigger_summary_update, get_summary_health_status


@admin.register(MonthlyOverviewSummary)
class MonthlyOverviewSummaryAdmin(admin.ModelAdmin):
    """
    Admin interface for Monthly Overview Summary with advanced management features.
    """
    
    # === LIST DISPLAY ===
    list_display = [
        'month_display',
        'data_completeness_indicator',
        'efficiency_summary',
        'financial_summary', 
        'technical_summary',
        'calculation_info',
        'actions_column',
    ]
    
    list_filter = [
        'has_complete_data',
        ('calculated_at', admin.DateFieldListFilter),
        ('month', admin.DateFieldListFilter),
    ]
    
    search_fields = ['month']
    
    ordering = ['-month']
    
    list_per_page = 25
    
    # === READONLY FIELDS ===
    readonly_fields = [
        'calculated_at',
        'calculation_duration',
        'source_data_hash',
        'data_completeness_score_display',
        'efficiency_metrics_display',
        'cost_breakdown_display',
        'technical_metrics_display',
    ]
    
    # === FIELDSETS ===
    fieldsets = (
        ('Basic Information', {
            'fields': (
                'month',
                'has_complete_data',
                'data_completeness_score_display',
            )
        }),
        
        ('Commercial Metrics', {
            'fields': (
                ('revenue_billed', 'revenue_collected'),
                ('customers_billed', 'customers_responded'),
                'customer_response_rate',
            ),
            'classes': ('collapse',),
        }),
        
        ('Energy Metrics', {
            'fields': (
                ('energy_delivered', 'energy_billed', 'energy_collected'),
            ),
            'classes': ('collapse',),
        }),
        
        ('Efficiency Metrics', {
            'fields': (
                'efficiency_metrics_display',
                ('billing_efficiency', 'collection_efficiency'),
                'atc_losses',
            ),
            'classes': ('collapse',),
        }),
        
        ('Financial Metrics', {
            'fields': (
                'cost_breakdown_display',
                'total_cost',
                ('total_opex', 'total_salaries'),
                ('total_nbet', 'total_mo'),
            ),
            'classes': ('collapse',),
        }),
        
        ('Technical Metrics', {
            'fields': (
                'technical_metrics_display',
                ('avg_hours_supply', 'avg_interruption_duration'),
                'avg_turnaround_time',
            ),
            'classes': ('collapse',),
        }),
        
        ('Calculation Metadata', {
            'fields': (
                ('calculated_at', 'calculation_duration'),
                'source_data_hash',
            ),
            'classes': ('collapse',),
        }),
    )
    
    # === CUSTOM DISPLAY METHODS ===
    
    def month_display(self, obj):
        """Enhanced month display with current month indicator"""
        current_month = date.today().replace(day=1)
        if obj.month == current_month:
            return format_html(
                '<strong style="color: #0066cc;">{} 📅</strong>',
                obj.month.strftime('%B %Y')
            )
        return obj.month.strftime('%B %Y')
    month_display.short_description = 'Month'
    month_display.admin_order_field = 'month'
    
    def data_completeness_indicator(self, obj):
        """Visual indicator of data completeness"""
        score = obj.data_completeness_score
        
        if score >= 90:
            color = '#28a745'  # Green
            icon = '✅'
        elif score >= 70:
            color = '#ffc107'  # Yellow
            icon = '⚠️'
        else:
            color = '#dc3545'  # Red
            icon = '❌'
        
        return format_html(
            '<span style="color: {}; font-weight: bold;">{} {}%</span>',
            color, icon, score
        )
    data_completeness_indicator.short_description = 'Data Quality'
    
    def efficiency_summary(self, obj):
        """Quick efficiency metrics summary"""
        return format_html(
            '<div style="font-size: 11px;">'
            'Billing: <strong>{:.1f}%</strong><br>'
            'Collection: <strong>{:.1f}%</strong><br>'
            'AT&C Loss: <strong style="color: #dc3545;">{:.1f}%</strong>'
            '</div>',
            obj.billing_efficiency,
            obj.collection_efficiency,
            obj.atc_losses
        )
    efficiency_summary.short_description = 'Efficiency'
    
    def financial_summary(self, obj):
        """Quick financial summary"""
        return format_html(
            '<div style="font-size: 11px;">'
            'Revenue: <strong>₦{:,.0f}</strong><br>'
            'Collected: <strong>₦{:,.0f}</strong><br>'
            'Total Cost: <strong>₦{:,.0f}</strong>'
            '</div>',
            obj.revenue_billed,
            obj.revenue_collected,
            obj.total_cost
        )
    financial_summary.short_description = 'Financials'
    
    def technical_summary(self, obj):
        """Quick technical summary"""
        return format_html(
            '<div style="font-size: 11px;">'
            'Supply: <strong>{:.1f}h/day</strong><br>'
            'Energy: <strong>{:,.0f} MWh</strong><br>'
            'Interruptions: <strong>{:.1f}h avg</strong>'
            '</div>',
            obj.avg_hours_supply,
            obj.energy_delivered,
            obj.avg_interruption_duration
        )
    technical_summary.short_description = 'Technical'
    
    def calculation_info(self, obj):
        """Calculation timing and freshness info"""
        age = datetime.now() - obj.calculated_at
        
        if age < timedelta(hours=1):
            age_color = '#28a745'  # Green - fresh
            age_text = f"{int(age.total_seconds() / 60)}m ago"
        elif age < timedelta(hours=24):
            age_color = '#ffc107'  # Yellow - getting old
            age_text = f"{int(age.total_seconds() / 3600)}h ago"
        else:
            age_color = '#dc3545'  # Red - stale
            age_text = f"{age.days}d ago"
        
        duration_text = ""
        if obj.calculation_duration:
            duration_ms = int(obj.calculation_duration.total_seconds() * 1000)
            duration_text = f"<br>Calc: {duration_ms}ms"
        
        return format_html(
            '<div style="font-size: 11px;">'
            '<span style="color: {};">Updated: {}</span>{}'
            '</div>',
            age_color, age_text, duration_text
        )
    calculation_info.short_description = 'Last Updated'
    
    def actions_column(self, obj):
        """Quick action buttons"""
        return format_html(
            '<a href="{}?month={}" class="button" style="font-size: 10px; padding: 2px 6px;">🔄 Refresh</a>',
            reverse('admin:analytics_refresh_summary'),
            obj.month.strftime('%Y-%m')
        )
    actions_column.short_description = 'Actions'
    
    # === DETAILED DISPLAY METHODS FOR CHANGE FORM ===
    
    def data_completeness_score_display(self, obj):
        """Detailed data completeness analysis"""
        score = obj.data_completeness_score
        
        indicators = []
        if obj.revenue_billed > 0:
            indicators.append("✅ Revenue data")
        else:
            indicators.append("❌ Revenue data")
            
        if obj.energy_delivered > 0:
            indicators.append("✅ Energy data")
        else:
            indicators.append("❌ Energy data")
            
        if obj.total_cost > 0:
            indicators.append("✅ Cost data")
        else:
            indicators.append("❌ Cost data")
        
        return format_html(
            '<div>'
            '<strong>Completeness Score: {}%</strong><br>'
            '<ul style="margin: 5px 0; padding-left: 20px;">{}</ul>'
            '</div>',
            score,
            ''.join(f'<li>{indicator}</li>' for indicator in indicators)
        )
    data_completeness_score_display.short_description = 'Data Completeness Analysis'
    
    def efficiency_metrics_display(self, obj):
        """Detailed efficiency metrics display"""
        return format_html(
            '<div style="background: #f8f9fa; padding: 10px; border-radius: 4px;">'
            '<div><strong>Billing Efficiency:</strong> {:.2f}% (Energy Billed / Energy Delivered)</div>'
            '<div><strong>Collection Efficiency:</strong> {:.2f}% (Revenue Collected / Revenue Billed)</div>'
            '<div><strong>AT&C Losses:</strong> <span style="color: #dc3545; font-weight: bold;">{:.2f}%</span></div>'
            '<div><strong>Customer Response Rate:</strong> {:.2f}%</div>'
            '</div>',
            obj.billing_efficiency,
            obj.collection_efficiency,
            obj.atc_losses,
            obj.customer_response_rate
        )
    efficiency_metrics_display.short_description = 'Efficiency Analysis'
    
    def cost_breakdown_display(self, obj):
        """Detailed cost breakdown"""
        total = obj.total_cost
        
        return format_html(
            '<div style="background: #f8f9fa; padding: 10px; border-radius: 4px;">'
            '<div><strong>Total Cost:</strong> ₦{:,.2f}</div>'
            '<hr style="margin: 8px 0;">'
            '<div>OPEX: ₦{:,.2f} ({:.1f}%)</div>'
            '<div>Salaries: ₦{:,.2f} ({:.1f}%)</div>'
            '<div>NBET: ₦{:,.2f} ({:.1f}%)</div>'
            '<div>MO: ₦{:,.2f} ({:.1f}%)</div>'
            '</div>',
            total,
            obj.total_opex, (obj.total_opex / total * 100) if total > 0 else 0,
            obj.total_salaries, (obj.total_salaries / total * 100) if total > 0 else 0,
            obj.total_nbet, (obj.total_nbet / total * 100) if total > 0 else 0,
            obj.total_mo, (obj.total_mo / total * 100) if total > 0 else 0,
        )
    cost_breakdown_display.short_description = 'Cost Breakdown'
    
    def technical_metrics_display(self, obj):
        """Detailed technical metrics"""
        availability = (obj.avg_hours_supply / 24 * 100) if obj.avg_hours_supply else 0
        
        return format_html(
            '<div style="background: #f8f9fa; padding: 10px; border-radius: 4px;">'
            '<div><strong>Supply Hours:</strong> {:.2f}h/day ({:.1f}% availability)</div>'
            '<div><strong>Energy Delivered:</strong> {:,.2f} MWh</div>'
            '<div><strong>Interruption Duration:</strong> {:.2f}h average</div>'
            '<div><strong>Turnaround Time:</strong> {:.2f}h average</div>'
            '</div>',
            obj.avg_hours_supply, availability,
            obj.energy_delivered,
            obj.avg_interruption_duration,
            obj.avg_turnaround_time
        )
    technical_metrics_display.short_description = 'Technical Analysis'
    
    # === CUSTOM ADMIN ACTIONS ===
    
    actions = [
        'refresh_selected_summaries',
        'mark_for_recalculation',
        'export_to_csv',
    ]
    
    def refresh_selected_summaries(self, request, queryset):
        """Refresh selected summaries"""
        count = 0
        for summary in queryset:
            try:
                update_monthly_overview_summary.delay(
                    summary.month.strftime('%Y-%m-%d'),
                    priority='admin_refresh'
                )
                count += 1
            except Exception as e:
                messages.error(request, f"Failed to queue refresh for {summary.month}: {str(e)}")
        
        if count > 0:
            messages.success(request, f"Queued {count} summaries for refresh.")
    refresh_selected_summaries.short_description = "🔄 Refresh selected summaries"
    
    def mark_for_recalculation(self, request, queryset):
        """Mark summaries for recalculation by clearing cache"""
        count = 0
        for summary in queryset:
            if trigger_summary_update(summary.month, force=True):
                count += 1
        
        messages.success(request, f"Marked {count} summaries for recalculation.")
    mark_for_recalculation.short_description = "🔄 Force recalculation"
    
    def export_to_csv(self, request, queryset):
        """Export selected summaries to CSV"""
        import csv
        from django.http import HttpResponse
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="overview_summaries.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'Month', 'Revenue Billed', 'Revenue Collected', 'Energy Delivered',
            'Billing Efficiency', 'Collection Efficiency', 'AT&C Losses',
            'Total Cost', 'Avg Hours Supply', 'Data Complete'
        ])
        
        for summary in queryset.order_by('month'):
            writer.writerow([
                summary.month.strftime('%Y-%m'),
                float(summary.revenue_billed),
                float(summary.revenue_collected),
                float(summary.energy_delivered),
                float(summary.billing_efficiency),
                float(summary.collection_efficiency),
                float(summary.atc_losses),
                float(summary.total_cost),
                float(summary.avg_hours_supply),
                summary.has_complete_data,
            ])
        
        return response
    export_to_csv.short_description = "📊 Export to CSV"
    
    # === CUSTOM ADMIN URLS AND VIEWS ===
    
    def get_urls(self):
        """Add custom admin URLs"""
        urls = super().get_urls()
        custom_urls = [
            path(
                'refresh-summary/',
                self.admin_site.admin_view(self.refresh_summary_view),
                name='analytics_refresh_summary',
            ),
            path(
                'health-dashboard/',
                self.admin_site.admin_view(self.health_dashboard_view),
                name='analytics_health_dashboard',
            ),
            path(
                'bulk-operations/',
                self.admin_site.admin_view(self.bulk_operations_view),
                name='analytics_bulk_operations',
            ),
        ]
        return custom_urls + urls
    
    def refresh_summary_view(self, request):
        """Custom view to refresh a specific summary"""
        month_str = request.GET.get('month')
        if month_str:
            try:
                month_date = datetime.strptime(month_str, '%Y-%m').date()
                update_monthly_overview_summary.delay(
                    month_date.strftime('%Y-%m-%d'),
                    priority='admin_manual'
                )
                messages.success(request, f"Queued refresh for {month_str}")
            except Exception as e:
                messages.error(request, f"Failed to queue refresh: {str(e)}")
        
        return HttpResponseRedirect(reverse('admin:analytics_monthlyoverviewsummary_changelist'))
    
    def health_dashboard_view(self, request):
        """Health dashboard view"""
        health_status = get_summary_health_status()
        
        # Get recent summaries
        recent_summaries = MonthlyOverviewSummary.objects.order_by('-month')[:12]
        
        # Check for missing months
        current_month = date.today().replace(day=1)
        expected_months = [
            current_month - relativedelta(months=i) for i in range(12)
        ]
        existing_months = set(s.month for s in recent_summaries)
        missing_months = [m for m in expected_months if m not in existing_months]
        
        context = {
            'health_status': health_status,
            'recent_summaries': recent_summaries,
            'missing_months': missing_months,
            'title': 'Analytics Health Dashboard',
        }
        
        return render(request, 'admin/analytics/health_dashboard.html', context)
    
    def bulk_operations_view(self, request):
        """Bulk operations view"""
        if request.method == 'POST':
            operation = request.POST.get('operation')
            
            if operation == 'populate_missing':
                # Queue population for missing months
                try:
                    from .tasks import bulk_update_summaries
                    result = bulk_update_summaries.delay(2023, datetime.now().year)
                    messages.success(request, f"Queued bulk population task: {result.id}")
                except Exception as e:
                    messages.error(request, f"Failed to queue bulk operation: {str(e)}")
            
            elif operation == 'health_check':
                try:
                    result = health_check_summaries.delay()
                    messages.success(request, f"Queued health check task: {result.id}")
                except Exception as e:
                    messages.error(request, f"Failed to queue health check: {str(e)}")
        
        return render(request, 'admin/analytics/bulk_operations.html', {
            'title': 'Bulk Operations',
        })
    
    # === CUSTOM ADMIN TEMPLATE CONTEXT ===
    
    def changelist_view(self, request, extra_context=None):
        """Add extra context to changelist view"""
        extra_context = extra_context or {}
        
        # Add health status to changelist
        health_status = get_summary_health_status()
        extra_context.update({
            'health_status': health_status,
            'health_dashboard_url': reverse('admin:analytics_health_dashboard'),
            'bulk_operations_url': reverse('admin:analytics_bulk_operations'),
        })
        
        return super().changelist_view(request, extra_context)


# === ADMIN SITE CUSTOMIZATION ===

admin.site.site_header = "Raven Analytics Administration"
admin.site.site_title = "Raven Analytics"
admin.site.index_title = "Analytics Dashboard"