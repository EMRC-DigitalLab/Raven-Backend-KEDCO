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