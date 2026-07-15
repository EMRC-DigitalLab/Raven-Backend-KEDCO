from django.contrib import admin

from .models import FeederCommercialProfile, PCCConfig, SimulationFeederResult, SimulationRun


@admin.register(FeederCommercialProfile)
class FeederCommercialProfileAdmin(admin.ModelAdmin):
    list_display = (
        'feeder', 'billing_efficiency_pct', 'collection_efficiency_pct',
        'atcc_loss_pct_display', 'monthly_revenue_target_ngn',
        'effective_from', 'is_active', 'source',
    )
    list_filter = ('is_active',)
    search_fields = ('feeder__name', 'source')
    ordering = ('feeder__name',)
    readonly_fields = ('atcc_loss_pct_display', 'created_at', 'updated_at')

    fieldsets = (
        ('Feeder', {
            'fields': ('feeder', 'is_active', 'effective_from', 'source'),
        }),
        ('ATC&C Components', {
            'description': (
                'ATC&C loss = 1 − (billing efficiency × collection efficiency). '
                'Billing efficiency captures technical + commercial losses. '
                'Collection efficiency captures cash collection performance.'
            ),
            'fields': ('billing_efficiency_pct', 'collection_efficiency_pct', 'atcc_loss_pct_display'),
        }),
        ('Revenue Target', {
            'fields': ('monthly_revenue_target_ngn',),
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def atcc_loss_pct_display(self, obj):
        return f"{obj.atcc_loss_pct}%"
    atcc_loss_pct_display.short_description = 'ATC&C Loss %'


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
        'tariff_rate_ngn_per_kwh', 'billing_efficiency_pct', 'collection_efficiency_pct',
        'revenue_potential_ngn', 'expected_billing_ngn',
        'expected_collection_ngn', 'atcc_loss_ngn', 'revenue_per_mwh_ngn',
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
        'total_revenue_potential_ngn', 'total_expected_billing_ngn',
        'total_expected_collection_ngn', 'total_atcc_loss_ngn', 'revenue_per_mwh_ngn',
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
        ('Phase 2 — Revenue (₦)', {
            'fields': (
                'total_revenue_potential_ngn', 'total_expected_billing_ngn',
                'total_expected_collection_ngn', 'total_atcc_loss_ngn',
                'revenue_per_mwh_ngn',
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
