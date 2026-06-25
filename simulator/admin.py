from django.contrib import admin

from .models import PCCConfig, SimulationFeederResult, SimulationRun


@admin.register(PCCConfig)
class PCCConfigAdmin(admin.ModelAdmin):
    list_display = (
        'label', 'total_pcc_mwh_per_hour', 'total_pcc_mwh_per_day_display',
        'nerc_offtake_kpi_pct', 'nerc_kpi_floor_display',
        'effective_from', 'effective_to', 'is_active', 'source',
    )
    list_filter = ('is_active',)
    search_fields = ('label', 'source')
    ordering = ('-effective_from',)
    readonly_fields = ('total_pcc_mwh_per_day_display', 'nerc_kpi_floor_display', 'created_at')

    fieldsets = (
        ('PCC Figure', {
            'fields': ('label', 'total_pcc_mwh_per_hour', 'total_pcc_mwh_per_day_display', 'source'),
        }),
        ('NERC KPI', {
            'fields': ('nerc_offtake_kpi_pct', 'nerc_kpi_floor_display'),
        }),
        ('Engine Settings', {
            'fields': ('demand_lookback_days',),
        }),
        ('Validity', {
            'fields': ('effective_from', 'effective_to', 'is_active'),
        }),
        ('Metadata', {
            'fields': ('created_at',),
            'classes': ('collapse',),
        }),
    )

    def total_pcc_mwh_per_day_display(self, obj):
        return f"{obj.total_pcc_mwh_per_day:,.2f} MWh/day"
    total_pcc_mwh_per_day_display.short_description = 'Daily PCC (MWh)'

    def nerc_kpi_floor_display(self, obj):
        return f"{obj.nerc_kpi_floor_mwh_per_day:,.2f} MWh/day ({obj.nerc_offtake_kpi_pct}% of PCC)"
    nerc_kpi_floor_display.short_description = 'NERC 95% KPI Floor'


class SimulationFeederResultInline(admin.TabularInline):
    model = SimulationFeederResult
    extra = 0
    readonly_fields = (
        'feeder', 'assigned_band', 'effective_band',
        'allocated_energy_mwh', 'effective_hours', 'status',
        'forecasted_demand_mwh', 'band_minimum_energy_mwh',
    )
    can_delete = False
    show_change_link = False
    ordering = ('assigned_band__priority_order', 'feeder__name')

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SimulationRun)
class SimulationRunAdmin(admin.ModelAdmin):
    list_display = (
        'simulation_date', 'scenario', 'e_offtake', 'zone',
        'compliance_breach', 'nerc_kpi_breach', 'excess_supply',
        'energised_count', 'upgraded_count', 'downgraded_count', 'load_shed_count',
        'created_by', 'created_at',
    )
    list_filter = ('scenario', 'zone', 'compliance_breach', 'nerc_kpi_breach', 'shortage_severity')
    search_fields = ('simulation_date',)
    ordering = ('-created_at',)
    readonly_fields = (
        'e_min', 'e_max', 'e_actual',
        'zone', 'shortage_severity',
        'compliance_breach', 'excess_supply', 'band_a_greatly_downgraded', 'nerc_kpi_breach',
        'total_allocated_mwh', 'surplus_mwh', 'deficit_mwh',
        'deviation_from_actual', 'deviation_from_e_min', 'deviation_from_e_max',
        'energised_count', 'upgraded_count', 'downgraded_count', 'load_shed_count',
        'pcc_config', 'created_by', 'created_at',
    )
    inlines = [SimulationFeederResultInline]

    fieldsets = (
        ('Simulation Input', {
            'fields': ('scenario', 'simulation_date', 'e_offtake', 'pcc_config'),
        }),
        ('Benchmarks', {
            'fields': ('e_min', 'e_max', 'e_actual'),
        }),
        ('Classification', {
            'fields': ('zone', 'shortage_severity'),
        }),
        ('Flags', {
            'fields': ('compliance_breach', 'nerc_kpi_breach', 'excess_supply', 'band_a_greatly_downgraded'),
        }),
        ('Summary', {
            'fields': (
                'total_allocated_mwh', 'surplus_mwh', 'deficit_mwh',
                'deviation_from_actual', 'deviation_from_e_min', 'deviation_from_e_max',
            ),
        }),
        ('Feeder Counts', {
            'fields': ('energised_count', 'upgraded_count', 'downgraded_count', 'load_shed_count'),
        }),
        ('Metadata', {
            'fields': ('created_by', 'created_at'),
            'classes': ('collapse',),
        }),
    )

    def has_add_permission(self, request):
        return False
