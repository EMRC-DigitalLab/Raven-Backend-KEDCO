# users/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    AccessLog,
    Department,
    Permission,
    Role,
    RolePermission,
    Section,
    TemporaryAccess,
    User,
    UserSectionAccess,
    UserSession,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['username', 'email', 'first_name', 'last_name', 'role', 'department', 'is_active']
    list_filter = ['role', 'department', 'is_active', 'created_at']
    search_fields = ['username', 'email', 'first_name', 'last_name', 'employee_id']
    ordering = ['username']
    
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Additional Info', {
            'fields': ('role', 'phone_number', 'department', 'employee_id', 'created_by')
        }),
    )
    
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('Additional Info', {
            'fields': ('role', 'phone_number', 'department', 'employee_id')
        }),
    )


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ['name', 'display_name', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'display_name']


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ['name', 'section', 'permission_type', 'codename']
    list_filter = ['section', 'permission_type']
    search_fields = ['name', 'codename']


@admin.register(UserSectionAccess)
class UserSectionAccessAdmin(admin.ModelAdmin):
    list_display = ['user', 'section', 'is_manager', 'granted_by', 'is_active', 'granted_at']
    list_filter = ['section', 'is_manager', 'is_active', 'granted_at']
    search_fields = ['user__username', 'user__email', 'section__name']
    filter_horizontal = ['permissions']


@admin.register(TemporaryAccess)
class TemporaryAccessAdmin(admin.ModelAdmin):
    list_display = ['user', 'section', 'granted_by', 'expires_at', 'is_active', 'is_expired']
    list_filter = ['section', 'is_active', 'granted_at', 'expires_at']
    search_fields = ['user__username', 'user__email', 'section__name']
    
    def is_expired(self, obj):
        return obj.is_expired()
    is_expired.boolean = True
    is_expired.short_description = 'Expired'


@admin.register(AccessLog)
class AccessLogAdmin(admin.ModelAdmin):
    list_display = ['user', 'section', 'action', 'performed_by', 'timestamp']
    list_filter = ['action', 'section', 'timestamp']
    search_fields = ['user__username', 'user__email', 'performed_by__username']
    readonly_fields = ['timestamp']


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ['name', 'display_name', 'is_system', 'created_by', 'created_at']
    list_filter = ['is_system']
    search_fields = ['name', 'display_name']
    readonly_fields = ['created_at']


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_at']
    search_fields = ['name']
    readonly_fields = ['created_at']


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ['role', 'section', 'is_manager', 'updated_by', 'updated_at']
    list_filter = ['role', 'section', 'is_manager']
    filter_horizontal = ['permissions']
    readonly_fields = ['updated_at']


@admin.register(UserSession)
class UserSessionAdmin(admin.ModelAdmin):
    list_display = ['user', 'device_label', 'ip_address', 'is_active', 'created_at', 'last_seen_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['user__username', 'user__email', 'ip_address']
    readonly_fields = ['jti', 'created_at', 'last_seen_at']