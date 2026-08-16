# reports/models.py - UPDATED WITH HR AND EXECUTIVE CATEGORIES
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

        # Comparison sections (data served by compare engine, rendered client-side)
        ('entity_comparison', 'Entity Comparison'),
        ('period_comparison', 'Period Comparison'),
        ('customer_comparison', 'Customer Comparison'),

        # TMO sections (legacy — kept for existing saved templates)
        ('tmo_feeder_dispatch',        'TMO Feeder Dispatch Targets vs Actuals'),
        ('tmo_collection_performance', 'TMO Collection Performance by Segment'),
        ('tmo_billing_efficiency',     'TMO Billing Efficiency (BE/FBE)'),

        # TMO sections (current, granular — flexible/customizable TMO report)
        ('tmo_overview',               'TMO Overview'),
        ('tmo_daily_network_energy',   'Daily Energy Forecast vs Actual'),
        ('tmo_daily_energy_consumed',  'Daily Energy Consumed'),
        ('tmo_daily_allocation',       'Daily Real-Time Allocation'),
        ('tmo_feeder_compliance_table', 'Feeder Compliance Criticality'),
        ('tmo_compliance_by_segment',  'Compliance by Segment'),
        ('tmo_minigrids_daily',        'Daily Feeder / Minigrid Energy'),
        ('tmo_pear',                   'PEAR — Premium Energy Allocation Ratio'),
        ('tmo_energy_pnl_donut',       'Energy by P&L Segment'),
        ('tmo_energy_by_voltage',      'Daily Energy by Segment & Voltage'),
        ('tmo_incidents',              'Techno-Commercial Incidents'),
        ('tmo_pnl_deficit',            'P&L Target Realization Deficit'),
        ('tmo_gcr',                    'GCR — P&L Target vs Billing Value'),
        ('tmo_volatility',             'P&L Mix Volatility Index'),
        ('tmo_feeder_scoped_summary',  'Selected Feeders Summary'),
        ('tmo_supply_hours',           'Feeder Hours Supplied vs Target'),
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
    GENERATION_METHOD_CHOICES = [
        ('pdf', 'PDF (server-side)'),
        ('data', 'Data (client-side)'),
    ]

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

    # 'pdf' = server generated PDF, 'data' = JSON returned for client-side PDF
    generation_method = models.CharField(
        max_length=10,
        choices=GENERATION_METHOD_CHOICES,
        default='pdf',
        blank=True,
    )

    # Generation info
    generated_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='generated_reports'
    )
    generated_at = models.DateTimeField(auto_now_add=True)

    # File reference (only set for server-side PDFs)
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


class ReportInsightsCache(models.Model):
    """
    Caches Claude AI insights for a report section or overall report summary.

    Keyed by a SHA-256 hash of (section_type + normalised section data).
    Same data always returns the same insight — no duplicate API calls.
    Expires after 24 hours.
    """
    cache_key  = models.CharField(max_length=64, unique=True, db_index=True)
    insights   = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'ReportInsightsCache {self.cache_key[:12]}… (expires {self.expires_at:%Y-%m-%d %H:%M})'