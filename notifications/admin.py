# notifications/admin.py
from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Announcement,
    BandSubscription,
    Notification,
    NotificationPreference,
    ReportRecipient,
)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'recipient', 'category', 'notification_type',
        'priority', 'is_read', 'send_email', 'email_sent', 'created_at',
    ]
    list_filter = ['category', 'notification_type', 'priority', 'is_read', 'email_sent']
    search_fields = ['title', 'message', 'recipient__username', 'recipient__email']
    readonly_fields = ['created_at', 'read_at', 'email_sent_at']
    ordering = ['-created_at']
    raw_id_fields = ['recipient', 'sender']

    def has_add_permission(self, request):
        # Notifications are created programmatically — not via admin
        return False


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ['user', 'email_enabled', 'updated_at']
    search_fields = ['user__username', 'user__email']
    raw_id_fields = ['user']


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'announcement_type', 'target_roles_display',
        'created_by', 'dispatched', 'is_active', 'created_at',
    ]
    list_filter = ['announcement_type', 'is_active', 'dispatched']
    search_fields = ['title', 'message']
    readonly_fields = ['dispatched', 'created_at']
    raw_id_fields = ['created_by']

    def target_roles_display(self, obj):
        if not obj.target_roles:
            return format_html('<span style="color:green;">All users</span>')
        return ', '.join(obj.target_roles)
    target_roles_display.short_description = 'Target Roles'

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(ReportRecipient)
class ReportRecipientAdmin(admin.ModelAdmin):
    list_display = [
        'report_title', 'report_type', 'sender', 'recipient',
        'email_status', 'email_sent_at', 'created_at',
    ]
    list_filter = ['email_status', 'report_type']
    search_fields = ['report_title', 'sender__username', 'recipient__username']
    readonly_fields = ['created_at', 'email_sent_at', 'viewed_at']
    raw_id_fields = ['sender', 'recipient', 'notification']

    def has_add_permission(self, request):
        return False


@admin.register(BandSubscription)
class BandSubscriptionAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'feeder_name', 'feeder_id',
        'notify_in_app', 'notify_email', 'is_active', 'created_at',
    ]
    list_filter = ['is_active', 'notify_email']
    search_fields = ['user__username', 'feeder_name']
    raw_id_fields = ['user']
