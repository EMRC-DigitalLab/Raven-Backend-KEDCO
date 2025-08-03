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
    



class MonthlyTechnicalSummary(UUIDModel, models.Model):
    """
    Pre-calculated monthly technical metrics for fast dashboard loading.
    Supports filtering by state, business district, and individual feeders.
    """
    month = models.DateField(
        db_index=True,
        help_text="First day of the month (e.g., 2025-01-01)"
    )
    
    # Filtering dimensions - nullable for aggregated views
    state = models.ForeignKey(
        'common.State', 
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="State filter (null = all states)"
    )
    business_district = models.ForeignKey(
        'common.BusinessDistrict',
        on_delete=models.CASCADE, 
        null=True,
        blank=True,
        help_text="Business district filter (null = all districts)"
    )
    feeder = models.ForeignKey(
        'common.Feeder',
        on_delete=models.CASCADE,
        null=True, 
        blank=True,
        help_text="Specific feeder filter (null = all feeders)"
    )
    
    # === ENERGY METRICS ===
    total_energy_delivered = models.DecimalField(
        max_digits=15,
        decimal_places=4,
        default=0,
        help_text="Total energy delivered in MWh"
    )
    
    # === LOAD METRICS ===
    avg_peak_load = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Average peak load in MW"
    )
    max_peak_load = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Maximum peak load in MW"
    )
    
    # === SUPPLY QUALITY METRICS ===
    avg_hours_of_supply = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(24)],
        help_text="Average hours of electricity supply per day"
    )
    total_supply_hours = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        help_text="Total hours of supply in the month"
    )
    
    # === INTERRUPTION METRICS ===
    total_interruptions = models.PositiveIntegerField(
        default=0,
        help_text="Total number of interruptions"
    )
    avg_daily_interruptions = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text="Average interruptions per day"
    )
    avg_interruption_duration = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text="Average duration of interruptions in hours"
    )
    total_interruption_hours = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Total hours of interruptions"
    )
    avg_turnaround_time = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text="Average restoration time in hours"
    )
    
    # === INFRASTRUCTURE METRICS ===
    active_feeder_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of active feeders"
    )
    total_customer_count = models.PositiveIntegerField(
        default=0,
        help_text="Total customers served"
    )
    
    # === INTERRUPTION BREAKDOWN BY TYPE ===
    # Store detailed breakdown as JSON for maximum flexibility
    interruption_breakdown_json = models.JSONField(
        default=dict,
        help_text="Detailed breakdown of interruption hours by specific fault type"
    )
    
    # Keep summary categories for high-level reporting
    load_shedding_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    equipment_fault_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    line_fault_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    maintenance_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    other_fault_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    
    # Separate turnaround time (excluding load shedding)
    avg_fault_turnaround_time = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text="Average restoration time for actual faults (excluding load shedding)"
    )
    
    # === RELIABILITY INDICES ===
    saifi = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=0,
        help_text="System Average Interruption Frequency Index"
    )
    saidi = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=0,
        help_text="System Average Interruption Duration Index"
    )
    
    # === METADATA ===
    calculated_at = models.DateTimeField(auto_now=True)
    calculation_duration = models.DurationField(null=True, blank=True)
    has_complete_data = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['-month', 'state', 'business_district', 'feeder']
        verbose_name = "Monthly Technical Summary"
        verbose_name_plural = "Monthly Technical Summaries"
        
        # Ensure uniqueness across filtering dimensions
        unique_together = [
            ('month', 'state', 'business_district', 'feeder')
        ]
        
        indexes = [
            models.Index(fields=['month']),
            models.Index(fields=['-month']),
            models.Index(fields=['month', 'state']),
            models.Index(fields=['month', 'business_district']),
            models.Index(fields=['month', 'feeder']),
            models.Index(fields=['calculated_at']),
            models.Index(fields=['has_complete_data', 'month']),
        ]
    
    def __str__(self):
        filter_desc = "All"
        if self.feeder:
            filter_desc = f"Feeder: {self.feeder.name}"
        elif self.business_district:
            filter_desc = f"District: {self.business_district.name}"
        elif self.state:
            filter_desc = f"State: {self.state.name}"
        
        return f"Technical Summary - {self.month.strftime('%Y-%m')} - {filter_desc}"
    
    @property
    def filter_level(self):
        """Return the filtering level for this summary"""
        if self.feeder:
            return 'feeder'
        elif self.business_district:
            return 'district'
        elif self.state:
            return 'state'
        else:
            return 'national'
    
    @property
    def availability_percentage(self):
        """Calculate system availability percentage"""
        total_possible_hours = 24 * 30  # Approximate month hours
        if total_possible_hours > 0:
            return round((float(self.avg_hours_of_supply) / 24) * 100, 2)
        return 0
    
    @property
    def interruption_breakdown_dict(self):
        """Return detailed interruption breakdown from JSON"""
        return self.interruption_breakdown_json or {}
    
    @property
    def summary_breakdown_dict(self):
        """Return high-level summary breakdown categories"""
        return {
            'Load Shedding': float(self.load_shedding_hours),
            'Equipment Fault': float(self.equipment_fault_hours),
            'Line Fault': float(self.line_fault_hours),
            'Maintenance': float(self.maintenance_hours),
            'Other': float(self.other_fault_hours),
        }
    
    def get_efficiency_metrics(self):
        """Get key efficiency metrics as a dictionary"""
        return {
            'avg_hours_of_supply': float(self.avg_hours_of_supply),
            'avg_interruption_duration': float(self.avg_interruption_duration),
            'avg_turnaround_time': float(self.avg_turnaround_time),
            'availability_percentage': self.availability_percentage,
            'total_interruptions': self.total_interruptions,
            'saifi': float(self.saifi),
            'saidi': float(self.saidi),
        }
    


# analytics/models.py (add this to your existing models)

from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from common.models import UUIDModel

class DailyTechnicalSummary(UUIDModel, models.Model):
    """
    Pre-calculated daily technical metrics for fast dashboard loading.
    Supports filtering by state, business district, and individual feeders.
    """
    date = models.DateField(
        db_index=True,
        help_text="Date of the summary (e.g., 2025-01-15)"
    )
    
    # Filtering dimensions - nullable for aggregated views
    state = models.ForeignKey(
        'common.State', 
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="State filter (null = all states)"
    )
    business_district = models.ForeignKey(
        'common.BusinessDistrict',
        on_delete=models.CASCADE, 
        null=True,
        blank=True,
        help_text="Business district filter (null = all districts)"
    )
    feeder = models.ForeignKey(
        'common.Feeder',
        on_delete=models.CASCADE,
        null=True, 
        blank=True,
        help_text="Specific feeder filter (null = all feeders)"
    )
    
    # === ENERGY METRICS ===
    total_energy_delivered = models.DecimalField(
        max_digits=15,
        decimal_places=4,
        default=0,
        help_text="Total energy delivered in MWh for the day"
    )
    
    # === LOAD METRICS ===
    avg_peak_load = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Average peak load in MW for the day"
    )
    max_peak_load = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Maximum peak load in MW for the day"
    )
    
    # === SUPPLY QUALITY METRICS ===
    hours_of_supply = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(24)],
        help_text="Hours of electricity supply for the day"
    )

    total_supply_hours = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0,
        help_text="Total hours of supply across all feeders for the day"
)
    
    # === INTERRUPTION METRICS ===
    total_interruptions = models.PositiveIntegerField(
        default=0,
        help_text="Total number of interruptions for the day"
    )
    avg_interruption_duration = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text="Average duration of interruptions in hours"
    )
    total_interruption_hours = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Total hours of interruptions for the day"
    )
    avg_turnaround_time = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text="Average restoration time in hours"
    )
    
    # === INFRASTRUCTURE METRICS ===
    active_feeder_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of active feeders"
    )
    total_customer_count = models.PositiveIntegerField(
        default=0,
        help_text="Total customers served"
    )
    
    # === INTERRUPTION BREAKDOWN BY TYPE ===
    # Store detailed breakdown as JSON for maximum flexibility
    interruption_breakdown_json = models.JSONField(
        default=dict,
        help_text="Detailed breakdown of interruption hours by specific fault type"
    )
    
    # Keep summary categories for high-level reporting
    load_shedding_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    equipment_fault_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    line_fault_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    maintenance_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    other_fault_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    
    # Separate turnaround time (excluding load shedding)
    avg_fault_turnaround_time = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text="Average restoration time for actual faults (excluding load shedding)"
    )
    
    # === RELIABILITY INDICES ===
    saifi = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=0,
        help_text="System Average Interruption Frequency Index"
    )
    saidi = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=0,
        help_text="System Average Interruption Duration Index"
    )
    
    # === METADATA ===
    calculated_at = models.DateTimeField(auto_now=True)
    calculation_duration = models.DurationField(null=True, blank=True)
    has_complete_data = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['-date', 'state', 'business_district', 'feeder']
        verbose_name = "Daily Technical Summary"
        verbose_name_plural = "Daily Technical Summaries"
        
        # Ensure uniqueness across filtering dimensions
        unique_together = [
            ('date', 'state', 'business_district', 'feeder')
        ]
        
        indexes = [
            models.Index(fields=['date']),
            models.Index(fields=['-date']),
            models.Index(fields=['date', 'state']),
            models.Index(fields=['date', 'business_district']),
            models.Index(fields=['date', 'feeder']),
            models.Index(fields=['calculated_at']),
            models.Index(fields=['has_complete_data', 'date']),
        ]
    
    def __str__(self):
        filter_desc = "All"
        if self.feeder:
            filter_desc = f"Feeder: {self.feeder.name}"
        elif self.business_district:
            filter_desc = f"District: {self.business_district.name}"
        elif self.state:
            filter_desc = f"State: {self.state.name}"
        
        return f"Daily Technical Summary - {self.date} - {filter_desc}"
    
    @property
    def filter_level(self):
        """Return the filtering level for this summary"""
        if self.feeder:
            return 'feeder'
        elif self.business_district:
            return 'district'
        elif self.state:
            return 'state'
        else:
            return 'national'
    
    @property
    def availability_percentage(self):
        """Calculate system availability percentage"""
        if self.hours_of_supply > 0:
            return round((float(self.hours_of_supply) / 24) * 100, 2)
        return 0
    
    @property
    def interruption_breakdown_dict(self):
        """Return detailed interruption breakdown from JSON"""
        return self.interruption_breakdown_json or {}
    
    @property
    def summary_breakdown_dict(self):
        """Return high-level summary breakdown categories"""
        return {
            'Load Shedding': float(self.load_shedding_hours),
            'Equipment Fault': float(self.equipment_fault_hours),
            'Line Fault': float(self.line_fault_hours),
            'Maintenance': float(self.maintenance_hours),
            'Other': float(self.other_fault_hours),
        }
    
    def get_efficiency_metrics(self):
        """Get key efficiency metrics as a dictionary"""
        return {
            'hours_of_supply': float(self.hours_of_supply),
            'avg_interruption_duration': float(self.avg_interruption_duration),
            'avg_turnaround_time': float(self.avg_turnaround_time),
            'availability_percentage': self.availability_percentage,
            'total_interruptions': self.total_interruptions,
            'saifi': float(self.saifi),
            'saidi': float(self.saidi),
        }

