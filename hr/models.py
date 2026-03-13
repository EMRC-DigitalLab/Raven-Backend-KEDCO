# hr/models.py
import uuid
from datetime import date
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from common.models import BusinessDistrict, State, UUIDModel


class ExecutiveRole(models.TextChoices):
    """Executive roles with KPI tracking"""
    CFO = 'CFO', 'Chief Financial Officer'
    CTO = 'CTO', 'Chief Technology Officer'
    CCO = 'CCO', 'Chief Commercial Officer'
    CHRO = 'CHRO', 'Chief Human Resources Officer'


class KPICategory(models.TextChoices):
    """KPI categorization for organization"""
    FINANCIAL = 'financial', 'Financial Excellence'
    TECHNICAL = 'technical', 'Technical Operations'
    COMMERCIAL = 'commercial', 'Commercial Performance'
    HR = 'hr', 'Human Resources'


class KPIDataType(models.TextChoices):
    """Data types for KPI values"""
    PERCENTAGE = 'percentage', 'Percentage (%)'
    CURRENCY = 'currency', 'Currency (₦)'
    INTEGER = 'integer', 'Integer'
    DECIMAL = 'decimal', 'Decimal'
    RATIO = 'ratio', 'Ratio'


class KPIPriority(models.TextChoices):
    """Priority levels for KPIs"""
    CRITICAL = 'critical', 'Critical'
    HIGH = 'high', 'High'
    MEDIUM = 'medium', 'Medium'
    LOW = 'low', 'Low'

class Department(UUIDModel, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True, max_length=100)

    def __str__(self):
        return self.name

class Role(UUIDModel, models.Model):
    title = models.CharField(max_length=100)
    department = models.ForeignKey(Department, on_delete=models.CASCADE)
    slug = models.SlugField(unique=True, max_length=100)

    def __str__(self):
        return self.title


class Staff(UUIDModel, models.Model):
    GRADE_CHOICES = [
        ('associate', 'Associate'),
        ('graduate_trainee', 'Graduate Trainee'),
        ('management_trainee', 'Management Trainee'),
        ('junior_assistant', 'Junior Assistant'),
        ('senior_manager', 'Senior Manager'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    full_name = models.CharField(max_length=150)
    email = models.EmailField(null=True, blank=True)
    phone_number = models.CharField(null=True, blank=True, max_length=20)
    gender = models.CharField(max_length=10, choices=[('Male', 'Male'), ('Female', 'Female')])
    birth_date = models.DateField(null=True, blank=True)
    salary = models.DecimalField(max_digits=12, decimal_places=2)
    hire_date = models.DateField()
    exit_date = models.DateField(null=True, blank=True)
    grade = models.CharField(null=True, blank=True, max_length=100, choices=GRADE_CHOICES)

    executive_role = models.CharField(
        max_length=10,
        choices=ExecutiveRole.choices,
        null=True,
        blank=True,
        help_text="Executive role if this staff member is a C-suite executive"
    )

    kpi_targets_set = models.BooleanField(
        default=False,
        help_text="Whether KPI targets have been set for this executive"
    )

    performance_review_frequency = models.CharField(
        max_length=20,
        choices=[
            ('monthly', 'Monthly'),
            ('quarterly', 'Quarterly'),
            ('annually', 'Annually'),
        ],
        default='quarterly',
        help_text="How frequently this executive's performance is reviewed"
    )

    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True)
    state = models.ForeignKey(State, on_delete=models.SET_NULL, null=True)
    district = models.ForeignKey(BusinessDistrict, on_delete=models.SET_NULL, null=True)

    def is_active(self):
        return self.exit_date is None

    def age(self):
        from datetime import date
        return (date.today() - self.birth_date).days // 365

    def __str__(self):
        return self.full_name



class ExecutiveKPIDefinition(UUIDModel, models.Model):
    """
    Master definition of executive KPIs and their targets
    """
    executive_role = models.CharField(
        max_length=10,
        choices=ExecutiveRole.choices,
        help_text="Which executive role this KPI belongs to"
    )
    
    category = models.CharField(
        max_length=20,
        choices=KPICategory.choices,
        help_text="Category of the KPI"
    )
    
    name = models.CharField(
        max_length=200,
        help_text="Name of the KPI"
    )
    
    description = models.TextField(
        help_text="Detailed description of what this KPI measures"
    )
    
    data_type = models.CharField(
        max_length=20,
        choices=KPIDataType.choices,
        help_text="Type of data this KPI represents"
    )
    
    unit = models.CharField(
        max_length=20,
        help_text="Unit of measurement (%, ₦, GWh, etc.)"
    )
    
    priority = models.CharField(
        max_length=10,
        choices=KPIPriority.choices,
        default=KPIPriority.MEDIUM,
        help_text="Priority level of this KPI"
    )
    
    # Target configuration
    target_value = models.DecimalField(
        max_digits=20,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Single target value (for fixed targets)"
    )
    
    target_min = models.DecimalField(
        max_digits=20,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Minimum target value (for range targets)"
    )
    
    target_max = models.DecimalField(
        max_digits=20,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Maximum target value (for range targets)"
    )
    
    is_range_target = models.BooleanField(
        default=False,
        help_text="Whether this KPI has a range target (min-max) or single target"
    )
    
    is_reverse_polarity = models.BooleanField(
        default=False,
        help_text="True if lower values are better (e.g., cost ratios, attrition)"
    )
    
    # Timeline and deadlines
    deadline = models.CharField(
        max_length=50,
        help_text="Target deadline (e.g., 'Q4 2025', 'Monthly', 'July 2025')"
    )
    
    measurement_frequency = models.CharField(
        max_length=20,
        choices=[
            ('daily', 'Daily'),
            ('weekly', 'Weekly'),
            ('monthly', 'Monthly'),
            ('quarterly', 'Quarterly'),
            ('annually', 'Annually'),
        ],
        default='monthly',
        help_text="How frequently this KPI is measured"
    )
    
    # Metadata
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this KPI is currently being tracked"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_kpis'
    )

    class Meta:
        unique_together = ['executive_role', 'name']
        verbose_name = "Executive KPI Definition"
        verbose_name_plural = "Executive KPI Definitions"
        ordering = ['executive_role', 'category', 'priority', 'name']

    def __str__(self):
        return f"{self.get_executive_role_display()} - {self.name}"

    def get_target_display(self):
        """Get formatted target string for display"""
        if self.is_range_target:
            return f"{self.target_min}-{self.target_max}{self.unit}"
        else:
            return f"{self.target_value}{self.unit}"

    def calculate_progress(self, current_value):
        """Calculate progress percentage for given current value"""
        if current_value is None:
            return 0
        
        current = Decimal(str(current_value))
        
        if self.is_range_target:
            # For range targets
            mid_target = (self.target_min + self.target_max) / 2
            if self.is_reverse_polarity:
                # Lower is better (e.g., cost ratios)
                if current <= self.target_max:
                    return min(((self.target_max - current) / (self.target_max - self.target_min)) * 100, 100)
                else:
                    return max(0, 100 - ((current - self.target_max) / self.target_max) * 100)
            else:
                # Higher is better
                return min((current / mid_target) * 100, 100)
        else:
            # Single target
            if self.is_reverse_polarity:
                # Lower is better
                if current <= self.target_value:
                    return 100
                else:
                    return max(0, 100 - ((current - self.target_value) / self.target_value) * 100)
            else:
                # Higher is better
                return min((current / self.target_value) * 100, 100)

    def get_status(self, current_value, evaluation_date=None):
        """Calculate status with deadline and time awareness"""
        if current_value is None or current_value == 0:
            return 'not_started'
    
        evaluation_date = evaluation_date or date.today()
    
        # Convert to Decimal for consistent arithmetic
        current = Decimal(str(current_value))
        progress = self.calculate_progress(current)  # Returns Decimal
        time_progress = Decimal(str(self._calculate_time_progress(evaluation_date)))  # Convert to Decimal
    
        # For range targets
        if self.is_range_target:
            if current >= self.target_min and current <= self.target_max:
                return 'on_target'
            elif current < self.target_min:
                gap = ((self.target_min - current) / self.target_min) * Decimal('100')
                if gap > time_progress + Decimal('20'):
                    return 'critical'
                elif gap > time_progress + Decimal('10'):
                    return 'off_track'
                else:
                    return 'at_risk'
            else:
                return 'exceeding'
    
        # For reverse polarity
        if self.is_reverse_polarity:
            if current <= self.target_value:
                return 'on_target'
            excess = ((current - self.target_value) / self.target_value) * Decimal('100')
            if excess > Decimal('30'):
                return 'critical'
            else:
                return 'off_track'
    
        # Standard targets - compare progress to time progress
        progress_gap = time_progress - progress
    
        if progress_gap <= Decimal('0'):
            return 'on_track'
        elif progress_gap <= Decimal('10'):
            return 'satisfactory'
        elif progress_gap <= Decimal('20'):
            return 'at_risk'
        else:
            return 'critical'
        
    def _calculate_time_progress(self, evaluation_date):
        """Calculate what % of time has elapsed - returns float"""
        if self.deadline.lower() == 'monthly' or self.deadline.lower() == 'ongoing':
            return 50.0
    
        if '2025' in self.deadline:
            if 'Q4' in self.deadline or 'Dec' in self.deadline:
                target_date = date(2025, 12, 31)
            elif 'Q3' in self.deadline or 'Sep' in self.deadline:
                target_date = date(2025, 9, 30)
            elif 'July' in self.deadline:
                target_date = date(2025, 7, 31)
            else:
                target_date = date(2025, 12, 31)
        
            start_date = date(2025, 1, 1)
            total_days = (target_date - start_date).days
            elapsed_days = (evaluation_date - start_date).days
        
            return min((elapsed_days / total_days) * 100.0, 100.0)
    
        return 50.0


class ExecutivePerformance(UUIDModel, models.Model):
    """
    Actual performance data for executive KPIs
    """
    kpi_definition = models.ForeignKey(
        ExecutiveKPIDefinition,
        on_delete=models.CASCADE,
        related_name='performance_records'
    )
    
    # Time period
    period_date = models.DateField(
        help_text="Date representing the period (first day of month/quarter/year)"
    )
    
    period_type = models.CharField(
        max_length=20,
        choices=[
            ('daily', 'Daily'),
            ('weekly', 'Weekly'),
            ('monthly', 'Monthly'),
            ('quarterly', 'Quarterly'),
            ('annually', 'Annually'),
        ],
        help_text="Type of period this performance record represents"
    )
    
    # Performance data
    actual_value = models.DecimalField(
        max_digits=20,
        decimal_places=4,
        help_text="Actual achieved value"
    )
    
    # Optional contextual data
    state = models.ForeignKey(
        State,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Specific state if KPI is state-specific"
    )
    
    business_district = models.ForeignKey(
        BusinessDistrict,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Specific business district if KPI is district-specific"
    )
    
    # Metadata
    notes = models.TextField(
        blank=True,
        help_text="Additional notes or context for this performance record"
    )
    
    data_source = models.CharField(
        max_length=100,
        blank=True,
        help_text="Source of the data (manual entry, API, calculation, etc.)"
    )
    
    verified = models.BooleanField(
        default=False,
        help_text="Whether this data has been verified/approved"
    )
    
    verified_by = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_performances'
    )
    
    verified_at = models.DateTimeField(
        null=True,
        blank=True
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_performances'
    )

    class Meta:
        unique_together = ['kpi_definition', 'period_date', 'period_type', 'state', 'business_district']
        verbose_name = "Executive Performance Record"
        verbose_name_plural = "Executive Performance Records"
        ordering = ['-period_date', 'kpi_definition']

    def __str__(self):
        return f"{self.kpi_definition.name} - {self.period_date} - {self.actual_value}{self.kpi_definition.unit}"

    @property
    def progress_percentage(self):
        """Calculate progress percentage"""
        return self.kpi_definition.calculate_progress(self.actual_value)

    @property
    def status(self):
        """Get current status"""
        return self.kpi_definition.get_status(self.actual_value)

    @property
    def target_variance(self):
        """Calculate variance from target"""
        if self.kpi_definition.is_range_target:
            mid_target = (self.kpi_definition.target_min + self.kpi_definition.target_max) / 2
            return float(self.actual_value - mid_target)
        else:
            return float(self.actual_value - self.kpi_definition.target_value)


class ExecutiveKPIAlert(UUIDModel, models.Model):
    """
    Alerts for KPI performance issues
    """
    ALERT_TYPES = [
        ('off_track', 'Off Track'),
        ('at_risk', 'At Risk'),
        ('deadline_approaching', 'Deadline Approaching'),
        ('target_missed', 'Target Missed'),
        ('exceptional_performance', 'Exceptional Performance'),
    ]
    
    kpi_definition = models.ForeignKey(
        ExecutiveKPIDefinition,
        on_delete=models.CASCADE,
        related_name='alerts'
    )
    
    alert_type = models.CharField(
        max_length=30,
        choices=ALERT_TYPES
    )
    
    message = models.TextField(
        help_text="Alert message describing the issue"
    )
    
    severity = models.CharField(
        max_length=10,
        choices=[
            ('low', 'Low'),
            ('medium', 'Medium'),
            ('high', 'High'),
            ('critical', 'Critical'),
        ],
        default='medium'
    )
    
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this alert is still active"
    )
    
    acknowledged = models.BooleanField(
        default=False,
        help_text="Whether this alert has been acknowledged"
    )
    
    acknowledged_by = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='acknowledged_alerts'
    )
    
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Executive KPI Alert"
        verbose_name_plural = "Executive KPI Alerts"
        ordering = ['-created_at', '-severity']

    def __str__(self):
        return f"{self.get_alert_type_display()} - {self.kpi_definition.name}"