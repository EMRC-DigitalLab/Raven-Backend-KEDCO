"""
commercial/models.py

Raven's commercial data tables.

NEW (active):
  TariffRate              — KEDCO tariff rates by band + customer type
  CommercialCustomer      — Customer profiles (MDI / MDNI), pulled from DataNest
  MeterReading            — Weekly meter readings per customer, pulled from DataNest
  MeterManager            — Field officers who record readings (MDI / MDNI), pulled from DataNest
  MeterManagerAssignment  — Which feeders each manager is assigned to

LEGACY (kept for compatibility with financial/analytics modules — not used in new commercial APIs):
  Customer, MonthlyEnergyBilled, MonthlyCustomerStats,
  SalesRepresentative, SalesRepPerformance, MonthlyRevenueBilled,
  DailyCollection, MonthlyCommercialSummary
"""

from decimal import Decimal

from django.db import models
from django.utils import timezone

from common.models import Band, BusinessDistrict, DistributionTransformer, Feeder, UUIDModel


VAT_RATE = Decimal('0.075')  # 7.5% Nigerian VAT


# ── NEW COMMERCIAL MODELS ─────────────────────────────────────────────────────

class TariffRate(UUIDModel, models.Model):
    """
    KEDCO tariff rates per service band and customer type.
    Pulled from DataNest tariff_rates table.
    Raven uses this to calculate energy_charge, VAT, and total_billed_amount.
    """

    BAND_CHOICES = [
        ('A', 'Band A'), ('B', 'Band B'), ('C', 'Band C'),
        ('D', 'Band D'), ('E', 'Band E'),
    ]
    CUSTOMER_TYPE_CHOICES = [
        ('MDI',  'MDI (Maximum Demand Installation)'),
        ('MDNI', 'MDNI (Non Maximum Demand)'),
    ]

    band           = models.CharField(max_length=1, choices=BAND_CHOICES)
    customer_type  = models.CharField(max_length=10, choices=CUSTOMER_TYPE_CHOICES)
    rate_per_kwh   = models.DecimalField(max_digits=10, decimal_places=2)
    effective_from = models.DateField()
    effective_to   = models.DateField(null=True, blank=True)
    is_active      = models.BooleanField(default=True)
    datanest_id    = models.IntegerField(unique=True, null=True, blank=True)

    class Meta:
        unique_together = ('band', 'customer_type', 'effective_from')
        ordering = ['band', 'customer_type']

    def __str__(self):
        return f'Band {self.band} / {self.customer_type} — ₦{self.rate_per_kwh}/kWh'


class CommercialCustomer(UUIDModel, models.Model):
    """
    MDI and MDNI customer profiles.
    Pulled from DataNest customer_information table.
    """

    CUSTOMER_TYPE_CHOICES = [
        ('MDI',  'MDI (Maximum Demand Installation)'),
        ('MDNI', 'MDNI (Non Maximum Demand)'),
    ]

    external_id      = models.CharField(max_length=36, unique=True)
    account_no       = models.CharField(max_length=50, unique=True)
    meter_number     = models.CharField(max_length=50, blank=True)
    customer_name    = models.CharField(max_length=255)
    customer_address = models.TextField(blank=True)
    phone_number     = models.CharField(max_length=20, blank=True)
    feeder           = models.ForeignKey(Feeder, on_delete=models.SET_NULL, null=True, blank=True, related_name='commercial_customers')
    district         = models.ForeignKey(BusinessDistrict, on_delete=models.SET_NULL, null=True, blank=True, related_name='commercial_customers')
    customer_type    = models.CharField(max_length=10, choices=CUSTOMER_TYPE_CHOICES)
    datanest_created_at = models.DateTimeField(null=True, blank=True)
    synced_at        = models.DateTimeField(auto_now=True)

    METER_STATUS_CHOICES = [
        ('active',   'Active'),
        ('faulty',   'Faulty'),
        ('missing',  'Missing'),
        ('bypassed', 'Bypassed'),
    ]

    meter_status          = models.CharField(
        max_length=10, choices=METER_STATUS_CHOICES, default='active',
        help_text="Current meter status synced from DataNest"
    )
    meter_fault_logged_at = models.DateTimeField(null=True, blank=True)
    meter_fault_logged_by = models.CharField(max_length=36, blank=True)

    # Derived from meter_status == 'bypassed' during sync
    is_bypass        = models.BooleanField(
        default=False,
        help_text="True if meter_status is 'bypassed' in DataNest"
    )

    class Meta:
        ordering = ['customer_name']
        indexes = [
            models.Index(fields=['customer_type']),
            models.Index(fields=['meter_status']),
            models.Index(fields=['is_bypass']),
        ]

    def __str__(self):
        return f'{self.customer_name} ({self.account_no}) — {self.customer_type}'


class MeterReading(UUIDModel, models.Model):
    """
    Weekly meter readings for MDI and MDNI customers.
    Pulled from DataNest meter_readings table.

    Raven calculates billing:
      energy_charge       = billed_consumption × tariff_rate
      vat                 = energy_charge × 7.5%
      total_billed_amount = energy_charge + vat

    Daily breakdown:
      daily_consumption = billed_consumption / days_since_last_reading
      mode = 'actual' if reading exists, 'estimated' if using last avg.
    """

    READING_TYPE_CHOICES = [
        ('MDI',  'MDI'),
        ('MDNI', 'MDNI'),
    ]

    OCR_STATUS_CHOICES = [
        ('pending',  'Pending'),
        ('matched',  'Matched — OCR agrees with submitted reading'),
        ('mismatch', 'Mismatch — OCR value differs from submission'),
        ('failed',   'Failed — OCR could not process image'),
        ('skipped',  'Skipped — no image submitted'),
    ]

    AUDIT_STATUS_CHOICES = [
        ('pending',  'Pending Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    external_id        = models.CharField(max_length=36, unique=True)
    customer           = models.ForeignKey(CommercialCustomer, on_delete=models.CASCADE, related_name='readings')
    reading_date       = models.DateField()
    reading_time       = models.TimeField(null=True, blank=True)
    previous_reading   = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    present_reading    = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    consumption        = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    multiplier_factor  = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    billed_consumption = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    tariff_rate        = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    reading_type       = models.CharField(max_length=10, choices=READING_TYPE_CHOICES)
    recorded_by_id     = models.CharField(max_length=36, blank=True)
    recorded_by_name   = models.CharField(max_length=255, blank=True)
    gps_latitude       = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    gps_longitude      = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    gps_location_name  = models.CharField(max_length=500, blank=True)
    has_proof          = models.BooleanField(default=False)
    observation        = models.TextField(blank=True)
    datanest_created_at = models.DateTimeField(null=True, blank=True)
    synced_at          = models.DateTimeField(auto_now=True)

    # ── GIS verification — synced from DataNest ──────────────────────────────
    gis_id    = models.CharField(
        max_length=20, blank=True,
        help_text="GIS point ID linked to this reading location"
    )
    gis_match = models.BooleanField(
        null=True, blank=True,
        help_text="True if GPS coordinates match the GIS reference point, False if they don't, None if unchecked"
    )

    # ── OCR verification — synced from DataNest ──────────────────────────────
    ocr_status          = models.CharField(
        max_length=10, choices=OCR_STATUS_CHOICES, default='pending',
        help_text="Result of OCR check against the proof image"
    )
    ocr_extracted_value = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Meter value read by OCR from the proof photo"
    )
    ocr_confidence      = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="OCR confidence score (0–1). Low confidence = unreliable extraction"
    )

    # ── Audit workflow — synced from DataNest ────────────────────────────────
    audit_status = models.CharField(
        max_length=10, choices=AUDIT_STATUS_CHOICES, null=True, blank=True,
        help_text="Supervisor audit decision on this reading"
    )
    audited_by   = models.CharField(max_length=36, blank=True, help_text="User ID of the auditor")
    audit_note   = models.TextField(blank=True, help_text="Auditor's note or rejection reason")
    audited_at   = models.DateTimeField(null=True, blank=True, help_text="When the audit decision was made")

    # ── Submission + fault metadata — synced from DataNest ───────────────────
    SUBMISSION_STATUS_CHOICES = [
        ('on_time', 'On Time'),
        ('late',    'Late'),
    ]
    FAULT_SOURCE_CHOICES = [
        ('feeder_fault', 'Feeder Fault'),
        ('meter_fault',  'Meter Fault'),
    ]

    submission_status = models.CharField(
        max_length=10, choices=SUBMISSION_STATUS_CHOICES, default='on_time',
        help_text="Whether the reading was submitted on time or late"
    )
    fault_source = models.CharField(
        max_length=15, choices=FAULT_SOURCE_CHOICES, null=True, blank=True,
        help_text="If this reading occurred during a fault — which type"
    )
    estimation_method = models.CharField(
        max_length=30, blank=True,
        help_text="Non-empty when DataNest estimated this reading rather than taking an actual meter read"
    )

    class Meta:
        unique_together = ('customer', 'reading_date')
        ordering = ['-reading_date']
        indexes = [
            models.Index(fields=['reading_date']),
            models.Index(fields=['reading_type']),
            models.Index(fields=['customer', 'reading_date']),
            models.Index(fields=['ocr_status']),
            models.Index(fields=['audit_status']),
            models.Index(fields=['submission_status']),
            models.Index(fields=['fault_source']),
            models.Index(fields=['estimation_method']),
        ]

    def __str__(self):
        return f'{self.customer.customer_name} — {self.reading_date}'

    @property
    def energy_charge(self):
        if self.billed_consumption is not None and self.tariff_rate is not None:
            return round(self.billed_consumption * self.tariff_rate, 2)
        return None

    @property
    def vat(self):
        ec = self.energy_charge
        return round(ec * VAT_RATE, 2) if ec is not None else None

    @property
    def total_billed_amount(self):
        ec = self.energy_charge
        vt = self.vat
        return round(ec + vt, 2) if (ec is not None and vt is not None) else None

    @property
    def days_covered(self):
        prev = (
            MeterReading.objects
            .filter(customer=self.customer, reading_date__lt=self.reading_date)
            .order_by('-reading_date')
            .values_list('reading_date', flat=True)
            .first()
        )
        return (self.reading_date - prev).days if prev else 7

    @property
    def daily_consumption(self):
        if self.billed_consumption is None:
            return None
        days = self.days_covered
        return round(self.billed_consumption / days, 4) if days > 0 else None


# ── METER MANAGERS ───────────────────────────────────────────────────────────

class MeterManager(UUIDModel, models.Model):
    """
    Field officers responsible for recording meter readings.
    Pulled from DataNest feeder_managers table.
    manager_type tells us whether they handle MDI or MDNI customers.
    """

    MANAGER_TYPE_CHOICES = [
        ('MDI',  'MDI'),
        ('MDNI', 'MDNI'),
    ]

    external_id      = models.CharField(max_length=36, unique=True)  # DataNest manager_id
    user_external_id = models.CharField(max_length=36, blank=True)   # DataNest user_id
    staff_id         = models.CharField(max_length=50, blank=True)
    name             = models.CharField(max_length=255)
    manager_type     = models.CharField(max_length=10, choices=MANAGER_TYPE_CHOICES)
    synced_at        = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.manager_type})'


class MeterManagerAssignment(UUIDModel, models.Model):
    """
    Links a MeterManager to a Feeder.
    Pulled from DataNest feeder_manager_assignments table.
    """

    STATUS_CHOICES = [
        ('active',   'Active'),
        ('inactive', 'Inactive'),
    ]

    external_id  = models.CharField(max_length=36, unique=True)  # DataNest assignment_id
    manager      = models.ForeignKey(MeterManager, on_delete=models.CASCADE, related_name='assignments')
    feeder       = models.ForeignKey(Feeder, on_delete=models.CASCADE, related_name='manager_assignments')
    status       = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    start_date   = models.DateField(null=True, blank=True)
    end_date     = models.DateField(null=True, blank=True)
    synced_at    = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('manager', 'feeder')
        ordering = ['manager', 'feeder']

    def __str__(self):
        return f'{self.manager.name} → {self.feeder.name} ({self.status})'


# ── COMPARISON AI CACHE ──────────────────────────────────────────────────────

class CommercialComparisonCache(models.Model):
    """
    Caches Claude AI insights for a given comparison so the API is not
    called twice for identical parameters.
    Keyed by a SHA-256 hash of the normalised comparison params.
    Expires after 24 hours — next request after expiry triggers a fresh call.
    """
    comparison_hash = models.CharField(max_length=64, unique=True, db_index=True)
    insights        = models.JSONField()
    created_at      = models.DateTimeField(auto_now_add=True)
    expires_at      = models.DateTimeField()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'ComparisonCache {self.comparison_hash[:12]}… (expires {self.expires_at:%Y-%m-%d %H:%M})'


# ── LEGACY MODELS (kept for financial/analytics compatibility) ─────────────────

class Customer(UUIDModel, models.Model):
    METERING_TYPE_CHOICES = [('MD1','MD1'),('MD2','MD2'),('Non-MD','Non-MD')]
    CATEGORY_CHOICES = [('Prepaid','Prepaid'),('Postpaid','Postpaid'),('Unmetered','Unmetered')]
    name          = models.CharField(max_length=255)
    category      = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    metering_type = models.CharField(max_length=20, choices=METERING_TYPE_CHOICES)
    band          = models.ForeignKey(Band, on_delete=models.SET_NULL, null=True, blank=True)
    transformer   = models.ForeignKey(DistributionTransformer, on_delete=models.PROTECT)
    joined_date   = models.DateField(default=timezone.now)

    def __str__(self):
        return self.name


class MonthlyEnergyBilled(UUIDModel, models.Model):
    feeder     = models.ForeignKey('common.Feeder', on_delete=models.CASCADE)
    month      = models.DateField()
    energy_mwh = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ('feeder', 'month')


class MonthlyCustomerStats(UUIDModel, models.Model):
    feeder                 = models.ForeignKey('common.Feeder', on_delete=models.CASCADE)
    month                  = models.DateField()
    customer_count         = models.PositiveIntegerField()
    customers_billed       = models.PositiveIntegerField()
    customer_response_count = models.PositiveIntegerField()

    class Meta:
        unique_together = ('feeder', 'month')


class SalesRepresentative(UUIDModel, models.Model):
    name                 = models.CharField(max_length=255)
    assigned_transformers = models.ManyToManyField(DistributionTransformer)
    slug                 = models.SlugField(unique=True)

    def __str__(self):
        return self.name


class SalesRepPerformance(UUIDModel, models.Model):
    sales_rep                = models.ForeignKey(SalesRepresentative, on_delete=models.CASCADE)
    month                    = models.DateField()
    outstanding_billed       = models.DecimalField(max_digits=15, decimal_places=2)
    current_billed           = models.DecimalField(max_digits=15, decimal_places=2)
    collections              = models.DecimalField(max_digits=15, decimal_places=2)
    daily_run_rate           = models.DecimalField(max_digits=15, decimal_places=2)
    collections_on_outstanding = models.DecimalField(max_digits=15, decimal_places=2)
    active_accounts          = models.PositiveIntegerField()
    suspended_accounts       = models.PositiveIntegerField()


class MonthlyRevenueBilled(UUIDModel, models.Model):
    sales_rep       = models.ForeignKey(SalesRepresentative, on_delete=models.CASCADE)
    transformer     = models.ForeignKey(DistributionTransformer, on_delete=models.CASCADE)
    month           = models.DateField()
    amount          = models.DecimalField(max_digits=12, decimal_places=2)
    customers_billed = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('sales_rep', 'transformer', 'month')
        ordering = ['-month', 'sales_rep', 'transformer']


class DailyCollection(UUIDModel, models.Model):
    COLLECTION_TYPE_CHOICES = [('Prepaid','Prepaid'),('Postpaid','Postpaid')]
    VENDOR_CHOICES = [
        ('BuyPower.ng','BuyPower.ng'),('Banahim.net','Banahim.net'),
        ('Bank','Bank'),('Cash','Cash'),('POS','POS'),
        ('powershop.ng','powershop.ng'),('Remita','Remita'),
    ]
    sales_rep          = models.ForeignKey(SalesRepresentative, on_delete=models.CASCADE)
    transformer        = models.ForeignKey(DistributionTransformer, on_delete=models.CASCADE)
    date               = models.DateField()
    amount             = models.DecimalField(max_digits=15, decimal_places=2)
    collection_type    = models.CharField(max_length=10, choices=COLLECTION_TYPE_CHOICES)
    vendor_name        = models.CharField(max_length=20, choices=VENDOR_CHOICES)
    customers_collected = models.PositiveIntegerField(default=0)
    created_at         = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('sales_rep', 'transformer', 'date', 'collection_type', 'vendor_name')
        ordering = ['-date', 'sales_rep', 'transformer']


class MonthlyCommercialSummary(UUIDModel):
    sales_rep           = models.ForeignKey(SalesRepresentative, on_delete=models.CASCADE)
    transformer         = models.ForeignKey(DistributionTransformer, on_delete=models.CASCADE)
    month               = models.DateField()
    customers_billed    = models.PositiveIntegerField(default=0)
    customers_responded = models.PositiveIntegerField(default=0)
    revenue_billed      = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    revenue_collected   = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    created_at          = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('sales_rep', 'transformer', 'month')


# =============================================================================
# TMO (Technical & Management Operations) — synced from DataNest
# =============================================================================

class TMOFeederTarget(UUIDModel, models.Model):
    """Feeder dispatch targets — mirrors DataNest tmo_targets."""
    tmo_id         = models.IntegerField(unique=True, help_text='DataNest tmo_id')
    feeder         = models.ForeignKey(Feeder, on_delete=models.SET_NULL, null=True, blank=True, related_name='tmo_targets')
    feeder_code    = models.CharField(max_length=100, help_text='Raw DataNest feeder_id')
    target_date    = models.DateField(db_index=True)
    target_mwh     = models.DecimalField(max_digits=12, decimal_places=4)
    set_by_user_id = models.CharField(max_length=100, blank=True)
    dn_created_at  = models.DateTimeField(null=True, blank=True)
    dn_updated_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-target_date', 'feeder_code']
        indexes  = [models.Index(fields=['target_date'])]

    def __str__(self):
        return f'{self.feeder_code} | {self.target_date} | {self.target_mwh} MWh'


class TMOCollectionTarget(UUIDModel, models.Model):
    """Collection targets vs actuals by segment — mirrors DataNest tmo_collection_targets."""
    tmo_id         = models.IntegerField(unique=True, help_text='DataNest tmo_id')
    segment_code   = models.CharField(max_length=100, db_index=True)
    sub_segment    = models.CharField(max_length=100)
    period_month   = models.DateField(db_index=True)
    target_amount  = models.DecimalField(max_digits=18, decimal_places=2)
    actual_amount  = models.DecimalField(max_digits=18, decimal_places=2)
    set_by_user_id = models.CharField(max_length=100, blank=True)
    dn_created_at  = models.DateTimeField(null=True, blank=True)
    dn_updated_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-period_month', 'segment_code', 'sub_segment']
        indexes  = [models.Index(fields=['period_month', 'segment_code'])]

    def __str__(self):
        return f'{self.segment_code}/{self.sub_segment} | {self.period_month}'


class TMOBillingEfficiency(UUIDModel, models.Model):
    """Billing efficiency BE/FBE — mirrors DataNest tmo_billing_efficiency."""
    SCOPE_CHOICES = [
        ('feeder',   'Feeder'),
        ('district', 'District'),
        ('state',    'State'),
    ]
    tmo_id                = models.IntegerField(unique=True, help_text='DataNest tmo_id')
    scope_type            = models.CharField(max_length=20, choices=SCOPE_CHOICES, db_index=True)
    scope_code            = models.CharField(max_length=100)
    scope_label           = models.CharField(max_length=200, blank=True)
    period_month          = models.DateField(db_index=True)
    energy_delivered_gwh  = models.DecimalField(max_digits=14, decimal_places=4)
    energy_billed_gwh     = models.DecimalField(max_digits=14, decimal_places=4)
    target_revenue_amount = models.DecimalField(max_digits=18, decimal_places=2)
    billed_amount         = models.DecimalField(max_digits=18, decimal_places=2)
    set_by_user_id        = models.CharField(max_length=100, blank=True)
    dn_created_at         = models.DateTimeField(null=True, blank=True)
    dn_updated_at         = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-period_month', 'scope_type', 'scope_label']
        indexes  = [models.Index(fields=['period_month', 'scope_type'])]

    def __str__(self):
        return f'{self.scope_label or self.scope_code} | {self.period_month}'
