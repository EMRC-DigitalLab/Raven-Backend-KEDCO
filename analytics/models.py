# analytics/models.py
from django.db import models
from common.models import UUIDModel
from django.core.validators import MinValueValidator, MaxValueValidator

class MonthlyOverviewSummary(UUIDModel, models.Model):
    """
    Pre-calculated monthly overview metrics for fast dashboard loading.
    Combines data from commercial, financial, technical, and HR apps.
    
    This model serves as a materialized view for the overview dashboard,
    dramatically improving response times from seconds to milliseconds.
    """
    month = models.DateField(
        unique=True,
        db_index=True,
        help_text="First day of the month (e.g., 2025-01-01)"
    )
    
    # === COMMERCIAL METRICS ===
    revenue_billed = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0,
        help_text="Total revenue billed for the month"
    )
    revenue_collected = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0,
        help_text="Total revenue collected for the month"
    )
    customers_billed = models.PositiveIntegerField(
        default=0,
        help_text="Number of customers billed"
    )
    customers_responded = models.PositiveIntegerField(
        default=0,
        help_text="Number of customers who responded/paid"
    )
    
    # === ENERGY METRICS ===
    energy_delivered = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        help_text="Total energy delivered in MWh"
    )
    energy_billed = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        help_text="Total energy billed in MWh"
    )
    energy_collected = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        help_text="Energy equivalent of collections in MWh"
    )
    
    # === TECHNICAL METRICS ===
    avg_hours_supply = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(24)],
        help_text="Average hours of electricity supply per day"
    )
    avg_interruption_duration = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=0,
        help_text="Average duration of interruptions in hours"
    )
    avg_turnaround_time = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=0,
        help_text="Average time to restore power in hours"
    )
    
    # === FINANCIAL METRICS ===
    total_cost = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0,
        help_text="Total operational cost for the month"
    )
    total_opex = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0,
        help_text="Total operational expenditure"
    )
    total_salaries = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0,
        help_text="Total salary payments"
    )
    total_nbet = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0,
        help_text="NBET invoice amount"
    )
    total_mo = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0,
        help_text="Market Operator invoice amount"
    )
    
    # === CALCULATED EFFICIENCY METRICS ===
    billing_efficiency = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Billing efficiency percentage (Energy Billed / Energy Delivered)"
    )
    collection_efficiency = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Collection efficiency percentage (Revenue Collected / Revenue Billed)"
    )
    atc_losses = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Aggregate Technical & Commercial losses percentage"
    )
    customer_response_rate = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Customer response rate percentage"
    )
    
    # === METADATA ===
    calculated_at = models.DateTimeField(
        auto_now=True,
        help_text="When this summary was last calculated"
    )
    calculation_duration = models.DurationField(
        null=True,
        blank=True,
        help_text="How long the calculation took"
    )
    source_data_hash = models.CharField(
        max_length=64, 
        null=True,
        blank=True,
        help_text="Hash of source data to detect changes and avoid unnecessary recalculations"
    )
    has_complete_data = models.BooleanField(
        default=True,
        help_text="Whether all required source data was available for calculation"
    )
    
    class Meta:
        ordering = ['-month']
        verbose_name = "Monthly Overview Summary"
        verbose_name_plural = "Monthly Overview Summaries"
        indexes = [
            models.Index(fields=['month']),
            models.Index(fields=['-month']),
            models.Index(fields=['calculated_at']),
            models.Index(fields=['has_complete_data', 'month']),
        ]
    
    def __str__(self):
        return f"Overview Summary - {self.month.strftime('%Y-%m')}"
    
    @property
    def is_current_month(self):
        """Check if this summary is for the current month"""
        from datetime import date
        today = date.today()
        return self.month.year == today.year and self.month.month == today.month
    
    @property
    def data_completeness_score(self):
        """Calculate a completeness score based on non-zero fields"""
        total_fields = 15  # Number of main metric fields
        non_zero_fields = sum([
            1 if self.revenue_billed > 0 else 0,
            1 if self.energy_delivered > 0 else 0,
            1 if self.customers_billed > 0 else 0,
            1 if self.total_cost > 0 else 0,
            # Add more field checks as needed
        ])
        return round((non_zero_fields / total_fields) * 100, 1)
    
    def get_efficiency_summary(self):
        """Get a summary of efficiency metrics"""
        return {
            'billing_efficiency': float(self.billing_efficiency),
            'collection_efficiency': float(self.collection_efficiency),
            'atc_losses': float(self.atc_losses),
            'customer_response_rate': float(self.customer_response_rate),
        }
    
    def needs_recalculation(self, max_age_hours=24):
        """Check if summary needs recalculation based on age"""
        from datetime import datetime, timedelta
        if self.is_current_month:
            return datetime.now() - self.calculated_at > timedelta(hours=max_age_hours)
        return False