from django.contrib import admin
from .models import TMODailyAllocation, TMOIncident, TMOMonthlySegmentTarget, TMONetworkConfig, TMOSupplyHoursTarget


@admin.register(TMOMonthlySegmentTarget)
class TMOMonthlySegmentTargetAdmin(admin.ModelAdmin):
    list_display  = ('segment', 'year', 'month', 'target_energy_mwh', 'target_revenue_ngn', 'target_collection_ngn', 'average_tariff_per_kwh')
    list_filter   = ('segment', 'year')
    ordering      = ('-year', '-month', 'segment')


@admin.register(TMONetworkConfig)
class TMONetworkConfigAdmin(admin.ModelAdmin):
    list_display  = ('year', 'month', 'target_md_share_pct', 'monthly_energy_target_gwh')
    ordering      = ('-year', '-month')


@admin.register(TMODailyAllocation)
class TMODailyAllocationAdmin(admin.ModelAdmin):
    list_display   = ('date', 'expected_mw', 'source', 'tmo_id', 'notes')
    list_filter    = ('source',)
    ordering       = ('-date',)
    search_fields  = ('date',)
    date_hierarchy = 'date'
    readonly_fields = ('tmo_id', 'source', 'created_at', 'updated_at')


@admin.register(TMOIncident)
class TMOIncidentAdmin(admin.ModelAdmin):
    list_display  = ('feeder', 'incident_date', 'coordinate', 'region', 'status', 'financial_loss_ngn')
    list_filter   = ('status', 'coordinate')
    search_fields = ('feeder__name', 'nature_of_fault', 'region')
    ordering      = ('-incident_date',)


@admin.register(TMOSupplyHoursTarget)
class TMOSupplyHoursTargetAdmin(admin.ModelAdmin):
    list_display  = ('segment', 'year', 'month', 'target_hours', 'updated_at')
    list_filter   = ('segment', 'year')
    ordering      = ('-year', '-month', 'segment')
