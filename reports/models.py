# reports/models.py
from django.db import models
from django.contrib.auth import get_user_model
from uuid import uuid4
import json

User = get_user_model()


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4)

    class Meta:
        abstract = True


class ReportTemplate(UUIDModel):
    """
    Stores saved report templates that users can reuse.
    """
    ORIENTATION_CHOICES = [
        ('portrait', 'Portrait'),
        ('landscape', 'Landscape'),
    ]
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('published', 'Published'),
        ('archived', 'Archived'),
    ]

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    
    # Report metadata
    report_title = models.CharField(max_length=255, default="Monthly Performance Report")
    report_subtitle = models.CharField(max_length=255, blank=True)
    orientation = models.CharField(max_length=20, choices=ORIENTATION_CHOICES, default='portrait')
    
    # Default filters (stored as JSON)
    default_filters = models.JSONField(default=dict, blank=True)
    
    # Ownership and status
    created_by = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='report_templates'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    is_public = models.BooleanField(default=False)  # Can other users use this template?
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.name} - {self.created_by.username}"


class ReportSection(UUIDModel):
    """
    Individual sections within a report template.
    Sections are ordered and can be toggled on/off.
    """
    SECTION_TYPE_CHOICES = [
        # Cover and intro
        ('cover_page', 'Cover Page'),
        ('infrastructure_overview', 'Infrastructure Overview'),
        
        # Technical sections
        ('technical_metrics', 'Technical Metrics Cards'),
        ('system_reliability', 'System Reliability'),
        ('interruption_breakdown', 'Interruption Breakdown Table'),
        ('hours_of_supply_chart', 'Hours of Supply Chart'),
        ('load_trend_chart', 'Load Trend Chart'),
        ('energy_delivered_chart', 'Energy Delivered Chart'),
        ('feeder_performance_table', 'Feeder Performance Table'),
        ('state_performance_table', 'State Performance Table'),
        ('district_performance_table', 'District Performance Table'),
        ('service_band_summary', 'Service Band Summary'),
        
        # Generic sections
        ('custom_text', 'Custom Text/Notes'),
        ('gaps_improvements', 'Gaps and Improvement Areas'),
        
        # Future sections (commercial, financial, etc.)
        ('commercial_summary', 'Commercial Summary'),
        ('financial_summary', 'Financial Summary'),
        ('collection_efficiency', 'Collection Efficiency'),
    ]

    template = models.ForeignKey(
        ReportTemplate, 
        on_delete=models.CASCADE, 
        related_name='sections'
    )
    section_type = models.CharField(max_length=50, choices=SECTION_TYPE_CHOICES)
    title = models.CharField(max_length=255, blank=True)  # Custom title override
    
    # Section ordering
    order = models.PositiveIntegerField(default=0)
    is_enabled = models.BooleanField(default=True)
    
    # Section-specific configuration (stored as JSON)
    # Examples:
    # - For metric cards: which metrics to show
    # - For charts: chart type, colors, etc.
    # - For tables: which columns to include
    # - For custom text: the actual text content
    config = models.JSONField(default=dict, blank=True)
    
    # Chart settings (if applicable)
    show_chart = models.BooleanField(default=False)
    chart_type = models.CharField(max_length=20, blank=True)  # line, bar, pie, etc.

    class Meta:
        ordering = ['order']
        unique_together = ['template', 'order']

    def __str__(self):
        return f"{self.template.name} - {self.get_section_type_display()} (#{self.order})"


class GeneratedReport(UUIDModel):
    """
    Stores information about generated reports for history/audit.
    """
    template = models.ForeignKey(
        ReportTemplate, 
        on_delete=models.SET_NULL, 
        null=True,
        related_name='generated_reports'
    )
    
    # Report details at time of generation
    report_title = models.CharField(max_length=255)
    filters_used = models.JSONField(default=dict)
    sections_included = models.JSONField(default=list)
    
    # Generation info
    generated_by = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='generated_reports'
    )
    generated_at = models.DateTimeField(auto_now_add=True)
    
    # File reference (if stored)
    file_path = models.CharField(max_length=500, blank=True)
    file_size = models.PositiveIntegerField(null=True, blank=True)  # in bytes

    class Meta:
        ordering = ['-generated_at']

    def __str__(self):
        return f"{self.report_title} - {self.generated_at.strftime('%Y-%m-%d %H:%M')}"