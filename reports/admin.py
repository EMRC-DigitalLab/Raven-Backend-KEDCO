# reports/admin.py
from django.contrib import admin

from .models import GeneratedReport, ReportSection, ReportTemplate


class ReportSectionInline(admin.TabularInline):
    model = ReportSection
    extra = 0
    fields = ['section_type', 'title', 'order', 'is_enabled', 'show_chart']
    ordering = ['order']


@admin.register(ReportTemplate)
class ReportTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'report_title', 'orientation', 'status', 'created_by', 'is_public', 'created_at']
    list_filter = ['status', 'orientation', 'is_public', 'created_at']
    search_fields = ['name', 'report_title', 'description']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [ReportSectionInline]
    
    fieldsets = (
        (None, {
            'fields': ('name', 'description')
        }),
        ('Report Settings', {
            'fields': ('report_title', 'report_subtitle', 'orientation')
        }),
        ('Filters', {
            'fields': ('default_filters',),
            'classes': ('collapse',)
        }),
        ('Ownership & Status', {
            'fields': ('created_by', 'status', 'is_public')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(ReportSection)
class ReportSectionAdmin(admin.ModelAdmin):
    list_display = ['template', 'section_type', 'title', 'order', 'is_enabled', 'show_chart']
    list_filter = ['section_type', 'is_enabled', 'show_chart']
    search_fields = ['template__name', 'title']
    ordering = ['template', 'order']


@admin.register(GeneratedReport)
class GeneratedReportAdmin(admin.ModelAdmin):
    list_display = ['report_title', 'template', 'generated_by', 'generated_at']
    list_filter = ['generated_at', 'generated_by']
    search_fields = ['report_title', 'template__name']
    readonly_fields = ['generated_at']