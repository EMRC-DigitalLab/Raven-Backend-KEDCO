# common/admin.py
from django.contrib import admin, messages
from django.db.models import Count, Q
from django.utils import timezone

from .models import (
    Band,
    BusinessDistrict,
    DistributionTransformer,
    Feeder,
    FeederSupplyRelationship,
    FeederTransformerMapping,
    InjectionSubstation,
    PowerTransformer,
    State,
)


@admin.register(Band)
class BandAdmin(admin.ModelAdmin):
    list_display = ('name', 'minimum_hours', 'priority_order', 'description', 'slug')
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}
    ordering = ('priority_order',)


@admin.register(State)
class StateAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}


@admin.register(BusinessDistrict)
class BusinessDistrictAdmin(admin.ModelAdmin):
    list_display = ('name', 'state', 'slug')
    list_filter = ('state',)
    search_fields = ('name', 'state__name')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(InjectionSubstation)
class InjectionSubstationAdmin(admin.ModelAdmin):
    list_display = ('name', 'state', 'station_type', 'status', 'slug', 'total_feeders', 'onboarded_feeders', 'pending_feeders')
    list_filter = ('state', 'station_type', 'status')
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}
    actions = ['onboard_all_feeders']
    
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        queryset = queryset.select_related('state').annotate(
            _total_feeders=Count('feeders'),
            _onboarded_feeders=Count('feeders', filter=Q(feeders__is_onboarded=True))
        )
        return queryset
    
    def total_feeders(self, obj):
        return obj._total_feeders
    total_feeders.short_description = 'Total Feeders'
    total_feeders.admin_order_field = '_total_feeders'
    
    def onboarded_feeders(self, obj):
        return obj._onboarded_feeders
    onboarded_feeders.short_description = 'Onboarded'
    onboarded_feeders.admin_order_field = '_onboarded_feeders'
    
    def pending_feeders(self, obj):
        return obj._total_feeders - obj._onboarded_feeders
    pending_feeders.short_description = 'Pending'
    
    def onboard_all_feeders(self, request, queryset):
        """Onboard all feeders in selected substations"""
        total_onboarded = 0
        
        for substation in queryset:
            # Get all non-onboarded feeders for this substation
            feeders_to_onboard = Feeder.objects.filter(
                substation=substation,
                is_onboarded=False
            )
            
            count = feeders_to_onboard.count()
            
            # Update all feeders
            feeders_to_onboard.update(
                is_onboarded=True,
                onboarded_at=timezone.now(),
                onboarded_by=request.user
            )
            
            total_onboarded += count
            
            self.message_user(
                request,
                f"Onboarded {count} feeders in {substation.name}",
                messages.SUCCESS
            )
        
        self.message_user(
            request,
            f"Total: {total_onboarded} feeders onboarded across {queryset.count()} substation(s)",
            messages.SUCCESS
        )
    
    onboard_all_feeders.short_description = "Onboard all feeders in selected substations"


@admin.register(Feeder)
class FeederAdmin(admin.ModelAdmin):
    list_display = (
        'name', 
        'substation', 
        'voltage_level', 
        'feeder_class',
        'status',
        'band', 
        'business_district',
        'is_onboarded',
        'onboarded_at',
        'onboarded_by'
    )
    list_filter = (
        'is_onboarded',
        'voltage_level',
        'feeder_class',
        'status',
        'band', 
        'substation',
        'business_district'
    )
    search_fields = ('name', 'substation__name', 'business_district__name')
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('onboarded_at', 'onboarded_by')
    actions = ['onboard_feeders', 'offboard_feeders']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'slug', 'substation', 'voltage_level', 'feeder_class', 'status', 'band', 'business_district')
        }),
        ('Onboarding Status', {
            'fields': ('is_onboarded', 'onboarded_at', 'onboarded_by'),
            'classes': ('collapse',),
        }),
    )
    
    def get_queryset(self, request):
        """Optimize queries"""
        queryset = super().get_queryset(request)
        queryset = queryset.select_related(
            'substation', 
            'band', 
            'business_district',
            'onboarded_by'
        )
        return queryset
    
    def onboard_feeders(self, request, queryset):
        """Onboard selected feeders"""
        # Filter only non-onboarded feeders
        feeders_to_onboard = queryset.filter(is_onboarded=False)
        count = feeders_to_onboard.count()
        
        if count == 0:
            self.message_user(
                request,
                "No feeders to onboard (all selected feeders are already onboarded)",
                messages.WARNING
            )
            return
        
        # Update feeders
        feeders_to_onboard.update(
            is_onboarded=True,
            onboarded_at=timezone.now(),
            onboarded_by=request.user
        )
        
        self.message_user(
            request,
            f"Successfully onboarded {count} feeder(s)",
            messages.SUCCESS
        )
    
    onboard_feeders.short_description = "Onboard selected feeders"
    
    def offboard_feeders(self, request, queryset):
        """Offboard selected feeders (mark as not onboarded)"""
        # Filter only onboarded feeders
        feeders_to_offboard = queryset.filter(is_onboarded=True)
        count = feeders_to_offboard.count()
        
        if count == 0:
            self.message_user(
                request,
                "No feeders to offboard (all selected feeders are already not onboarded)",
                messages.WARNING
            )
            return
        
        # Update feeders
        feeders_to_offboard.update(
            is_onboarded=False,
            onboarded_at=None,
            onboarded_by=None
        )
        
        self.message_user(
            request,
            f"Successfully offboarded {count} feeder(s)",
            messages.WARNING
        )
    
    offboard_feeders.short_description = "Offboard selected feeders (remove onboarding)"


@admin.register(DistributionTransformer)
class DistributionTransformerAdmin(admin.ModelAdmin):
    list_display = ('name', 'feeder', 'feeder_onboarded', 'slug')
    list_filter = ('feeder__substation', 'feeder__is_onboarded')
    search_fields = ('name', 'feeder__name')
    prepopulated_fields = {'slug': ('name',)}
    
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        queryset = queryset.select_related('feeder', 'feeder__substation')
        return queryset
    
    def feeder_onboarded(self, obj):
        return obj.feeder.is_onboarded
    feeder_onboarded.short_description = 'Feeder Onboarded'
    feeder_onboarded.boolean = True


@admin.register(PowerTransformer)
class PowerTransformerAdmin(admin.ModelAdmin):
    list_display = ('name', 'capacity_mva', 'voltage_rating', 'status', 'slug')
    list_filter = ('status', 'voltage_rating')
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}


@admin.register(FeederTransformerMapping)
class FeederTransformerMappingAdmin(admin.ModelAdmin):
    list_display = ('feeder', 'transformer', 'connection_type', 'status')
    list_filter = ('status', 'connection_type')
    search_fields = ('feeder__name', 'transformer__name')
    raw_id_fields = ('feeder', 'transformer')


@admin.register(FeederSupplyRelationship)
class FeederSupplyRelationshipAdmin(admin.ModelAdmin):
    list_display = ('supplier_feeder', 'supplied_feeder', 'supply_type', 'priority_order', 'status')
    list_filter = ('supply_type', 'status', 'supplier_feeder__voltage_level')
    search_fields = ('supplier_feeder__name', 'supplied_feeder__name')
    raw_id_fields = ('supplier_feeder', 'supplied_feeder')
