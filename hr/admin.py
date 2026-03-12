from django.contrib import admin
from django.db.models import Q
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    Department,
    ExecutiveKPIAlert,
    ExecutiveKPIDefinition,
    ExecutivePerformance,
    Role,
    Staff,
)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug',]
    search_fields = ['name']


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ['title', 'department', 'slug',]
    search_fields = ['title', 'department']

@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'email', 'phone_number', 'role', 'department', 'state', 'district']
    search_fields = ['full_name', 'email', 'phone_number']
    list_filter = ['grade', 'gender', 'department']



@admin.register(ExecutiveKPIDefinition)
class ExecutiveKPIDefinitionAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'executive_role', 'category', 'priority', 
        'target_display', 'deadline', 'is_active', 'created_at'
    ]
    list_filter = [
        'executive_role', 'category', 'priority', 'is_active', 
        'measurement_frequency', 'is_range_target'
    ]
    search_fields = ['name', 'description']
    ordering = ['executive_role', 'category', 'priority']
    
    fieldsets = (
        ('Basic Information', {
            'fields': (
                'executive_role', 'category', 'name', 'description', 
                'priority', 'is_active'
            )
        }),
        ('Data Configuration', {
            'fields': (
                'data_type', 'unit', 'measurement_frequency'
            )
        }),
        ('Target Configuration', {
            'fields': (
                'is_range_target', 'target_value', 'target_min', 'target_max',
                'is_reverse_polarity', 'deadline'
            ),
            'description': 'For range targets, use target_min and target_max. For single targets, use target_value only.'
        }),
        ('Metadata', {
            'fields': ('created_by',),
            'classes': ('collapse',)
        })
    )
    
    readonly_fields = ['created_at', 'updated_at']
    
    def target_display(self, obj):
        """Display formatted target"""
        if obj.is_range_target:
            return f"{obj.target_min}-{obj.target_max}{obj.unit}"
        else:
            return f"{obj.target_value}{obj.unit}"
    target_display.short_description = 'Target'
    
    def save_model(self, request, obj, form, change):
        if not change:  # Only set created_by on creation
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class ExecutivePerformanceInline(admin.TabularInline):
    model = ExecutivePerformance
    extra = 1
    fields = [
        'period_date', 'period_type', 'actual_value', 
        'progress_percentage', 'status', 'verified'
    ]
    readonly_fields = ['progress_percentage', 'status']
    
    def progress_percentage(self, obj):
        if obj.pk:
            return f"{obj.progress_percentage:.1f}%"
        return "-"
    progress_percentage.short_description = 'Progress %'
    
    def status(self, obj):
        if obj.pk:
            status_colors = {
                'on_track': 'green',
                'at_risk': 'orange', 
                'off_track': 'red',
                'not_started': 'gray',
                'on_target': 'green',
                'below_target': 'orange',
                'above_target': 'blue'
            }
            color = status_colors.get(obj.status, 'black')
            return format_html(
                '<span style="color: {};">{}</span>',
                color,
                obj.status.replace('_', ' ').title()
            )
        return "-"


@admin.register(ExecutivePerformance)
class ExecutivePerformanceAdmin(admin.ModelAdmin):
    list_display = [
        'kpi_definition', 'period_date', 'period_type', 
        'actual_value_display', 'progress_percentage_display', 
        'status_display', 'verified'
    ]
    list_filter = [
        'kpi_definition__executive_role', 'period_type', 'verified',
        'kpi_definition__category', 'kpi_definition__priority'
    ]
    search_fields = [
        'kpi_definition__name', 'kpi_definition__executive_role', 
        'notes'
    ]
    ordering = ['-period_date', 'kpi_definition']
    
    fieldsets = (
        ('KPI Information', {
            'fields': ('kpi_definition',)
        }),
        ('Performance Data', {
            'fields': (
                'period_date', 'period_type', 'actual_value',
                'progress_percentage_display', 'status_display'
            )
        }),
        ('Location Context', {
            'fields': ('state', 'business_district'),
            'classes': ('collapse',)
        }),
        ('Data Quality', {
            'fields': (
                'data_source', 'notes', 'verified', 'verified_by', 'verified_at'
            )
        }),
        ('Metadata', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    readonly_fields = [
        'progress_percentage_display', 'status_display', 
        'created_at', 'updated_at'
    ]
    
    def actual_value_display(self, obj):
        return f"{obj.actual_value}{obj.kpi_definition.unit}"
    actual_value_display.short_description = 'Actual Value'
    
    def progress_percentage_display(self, obj):
        return f"{obj.progress_percentage:.1f}%"
    progress_percentage_display.short_description = 'Progress %'
    
    def status_display(self, obj):
        status_colors = {
            'on_track': 'green',
            'at_risk': 'orange', 
            'off_track': 'red',
            'not_started': 'gray',
            'on_target': 'green',
            'below_target': 'orange',
            'above_target': 'blue'
        }
        color = status_colors.get(obj.status, 'black')
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color,
            obj.status.replace('_', ' ').title()
        )
    status_display.short_description = 'Status'
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        if obj.verified and not obj.verified_by:
            obj.verified_by = request.user
            obj.verified_at = timezone.now()
        super().save_model(request, obj, form, change)


@admin.register(ExecutiveKPIAlert)
class ExecutiveKPIAlertAdmin(admin.ModelAdmin):
    list_display = [
        'kpi_definition', 'alert_type', 'severity', 
        'is_active', 'acknowledged', 'created_at'
    ]
    list_filter = [
        'alert_type', 'severity', 'is_active', 'acknowledged',
        'kpi_definition__executive_role'
    ]
    search_fields = ['kpi_definition__name', 'message']
    ordering = ['-created_at', '-severity']
    
    fieldsets = (
        ('Alert Information', {
            'fields': (
                'kpi_definition', 'alert_type', 'severity', 'message'
            )
        }),
        ('Status', {
            'fields': (
                'is_active', 'acknowledged', 'acknowledged_by', 'acknowledged_at'
            )
        }),
        ('Metadata', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        })
    )
    
    readonly_fields = ['created_at', 'acknowledged_at']
    
    def save_model(self, request, obj, form, change):
        if obj.acknowledged and not obj.acknowledged_by:
            obj.acknowledged_by = request.user
            obj.acknowledged_at = timezone.now()
        super().save_model(request, obj, form, change)


# Update the existing Staff admin to include executive role fields
class StaffAdmin(admin.ModelAdmin):  # Extend your existing StaffAdmin
    list_display = [
        'full_name', 'executive_role', 'department', 'state', 
        'district', 'salary', 'hire_date', 'is_active'
    ]
    list_filter = [
        'executive_role', 'department', 'state', 'district', 
        'gender', 'grade'
    ]
    search_fields = ['full_name', 'email']
    
    fieldsets = (
        ('Personal Information', {
            'fields': (
                'full_name', 'email', 'phone_number', 'gender', 'birth_date'
            )
        }),
        ('Employment Details', {
            'fields': (
                'role', 'department', 'grade', 'salary', 
                'hire_date', 'exit_date'
            )
        }),
        ('Location', {
            'fields': ('state', 'district')
        }),
        ('Executive Role', {
            'fields': (
                'executive_role', 'kpi_targets_set', 'performance_review_frequency'
            ),
            'classes': ('collapse',)
        })
    )
    
    inlines = []
    
    def get_inlines(self, request, obj):
        """Only show performance inline for executives"""
        inlines = []
        if obj and obj.executive_role:
            inlines.append(ExecutivePerformanceInline)
        return inlines


# Custom admin views for executive dashboards
class ExecutiveDashboardAdmin(admin.ModelAdmin):
    """Custom admin view for executive performance overview"""
    change_list_template = 'admin/hr/executive_dashboard.html'
    
    def changelist_view(self, request, extra_context=None):
        # Get performance summary data
        extra_context = extra_context or {}
        
        # Summary by executive role
        role_summary = {}
        for role_code, role_name in ExecutiveKPIDefinition.ExecutiveRole.choices:
            kpis = ExecutiveKPIDefinition.objects.filter(
                executive_role=role_code, is_active=True
            )
            performances = ExecutivePerformance.objects.filter(
                kpi_definition__executive_role=role_code
            ).order_by('-period_date')
            
            role_summary[role_code] = {
                'name': role_name,
                'total_kpis': kpis.count(),
                'recent_performances': performances[:5],  # Last 5 records
            }
        
        extra_context['role_summary'] = role_summary
        
        return super().changelist_view(request, extra_context)



# Custom filters for better admin experience
class ExecutiveRoleFilter(admin.SimpleListFilter):
    title = 'Executive Role'
    parameter_name = 'executive_role'
    
    def lookups(self, request, model_admin):
        return ExecutiveKPIDefinition.ExecutiveRole.choices
    
    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(kpi_definition__executive_role=self.value())
        return queryset