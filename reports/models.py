# reports/models.py - UPDATED WITH HR AND EXECUTIVE CATEGORIES
import json
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import models

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
    
    # ✅ NEW: Report categories
    CATEGORY_CHOICES = [
        ('technical', 'Technical Performance'),
        ('commercial', 'Commercial Performance'),
        ('financial', 'Financial Performance'),
        ('hr', 'Human Resources'),
        ('executive', 'Executive Performance'),
        ('compliance', 'Compliance Report'),
        ('general', 'General Report'),
    ]

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    
    # ✅ NEW: Category field
    category = models.CharField(
        max_length=20, 
        choices=CATEGORY_CHOICES, 
        default='general',
        help_text='Type of report (Technical, HR, Executive, etc.)'
    )
    
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
    is_public = models.BooleanField(default=False)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['category', 'status']),
            models.Index(fields=['created_by', 'category']),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_category_display()}) - {self.created_by.username}"


class ReportSection(UUIDModel):
    """
    Individual sections within a report template.
    Sections are ordered and can be toggled on/off.
    """
    SECTION_TYPE_CHOICES = [
        # Cover and intro
        ('cover_page', 'Cover Page'),
        ('table_of_contents', 'Table of Contents'),
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
        
        # ✅ NEW: HR sections
        ('hr_overview', 'HR Overview'),
        ('staff_metrics', 'Staff Metrics Cards'),
        ('department_headcount', 'Headcount by Department'),
        ('staff_productivity', 'Staff Productivity Metrics'),
        ('wage_bill_analysis', 'Wage Bill Analysis'),
        ('attrition_analysis', 'Attrition Analysis'),
        ('recruitment_summary', 'Recruitment Summary'),
        ('training_summary', 'Training & Development Summary'),
        ('performance_appraisals', 'Performance Appraisals Summary'),
        
        # ✅ NEW: Executive Performance sections
        ('executive_overview', 'Executive Performance Overview'),
        ('cfo_performance', 'CFO Performance Metrics'),
        ('cto_performance', 'CTO Performance Metrics'),
        ('cco_performance', 'CCO Performance Metrics'),
        ('chro_performance', 'CHRO Performance Metrics'),
        ('executive_kpi_summary', 'Executive KPI Summary Table'),
        ('executive_comparison', 'Executive Performance Comparison'),
        ('board_kpi_status', 'Board KPI Status'),
        ('kpi_trends', 'KPI Trends Over Time'),
        
        # DSO Compliance sections
        ('dso_compliance_overview', 'DSO Compliance Overview'),
        ('dso_compliance_table', 'DSO Compliance by Station'),

        # Generic sections
        ('custom_text', 'Custom Text/Notes'),
        ('gaps_improvements', 'Gaps and Improvement Areas'),
        
        # Commercial & Financial (existing placeholders)
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
    title = models.CharField(max_length=255, blank=True)
    
    # Section ordering
    order = models.PositiveIntegerField(default=0)
    is_enabled = models.BooleanField(default=True)
    
    # Section-specific configuration (stored as JSON)
    config = models.JSONField(default=dict, blank=True)
    
    # Chart settings (if applicable)
    show_chart = models.BooleanField(default=False)
    chart_type = models.CharField(max_length=20, blank=True)

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
    
    # ✅ NEW: Category tracking
    category = models.CharField(
        max_length=20,
        choices=ReportTemplate.CATEGORY_CHOICES,
        default='general'
    )
    
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
    file_size = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['-generated_at']
        indexes = [
            models.Index(fields=['category', 'generated_at']),
            models.Index(fields=['generated_by', 'category']),
        ]

    def __str__(self):
        return f"{self.report_title} ({self.get_category_display()}) - {self.generated_at.strftime('%Y-%m-%d %H:%M')}"