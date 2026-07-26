# tmo/models.py
from django.db import models


class TMOMonthlySegmentTarget(models.Model):
    """
    Monthly energy and revenue targets per P&L segment (MDI / MDNI).
    Also stores average tariff (₦/MWh) used for GCR billing calculations.
    """
    SEGMENT_CHOICES = [
        ('MDI',  'MD Industrial'),
        ('MDNI', 'MD Non-Industrial'),
        ('Regions', 'Regions'),
    ]

    segment = models.CharField(max_length=10, choices=SEGMENT_CHOICES, db_index=True)
    year    = models.PositiveSmallIntegerField()
    month   = models.PositiveSmallIntegerField()

    target_energy_mwh        = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    target_revenue_ngn       = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    target_collection_ngn    = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    average_tariff_ngn_per_mwh = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Average tariff in ₦/MWh for this segment (used for GCR billing value calc)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('segment', 'year', 'month')
        ordering = ['-year', '-month', 'segment']

    def __str__(self):
        return f"{self.segment} {self.year}-{self.month:02d}"


class TMONetworkConfig(models.Model):
    """
    Monthly network-level configuration: MD/NMD target mix and total energy target.
    Drives PEAR and daily energy forecast charts.
    """
    year  = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()

    target_md_share_pct     = models.DecimalField(
        max_digits=5, decimal_places=2, default=65.0,
        help_text="Target share of total energy for MD (MDI+MDNI) feeders, e.g. 65.0"
    )
    monthly_energy_target_gwh = models.DecimalField(
        max_digits=12, decimal_places=4, default=0,
        help_text="Monthly total energy target in GWh (shown on daily forecast chart)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('year', 'month')
        ordering = ['-year', '-month']

    def __str__(self):
        return f"Network Config {self.year}-{self.month:02d}"


class TMOIncident(models.Model):
    """
    Techno-Commercial Incident — fault events logged per feeder for the weekly incidence report.
    """
    STATUS_CHOICES = [
        ('rectified', 'Rectified'),
        ('lingering', 'Lingering'),
    ]

    feeder           = models.ForeignKey(
        'common.Feeder', on_delete=models.CASCADE, related_name='tmo_incidents'
    )
    coordinate       = models.CharField(max_length=100, blank=True, help_text="e.g. JIGAWA, KATSINA")
    region           = models.CharField(max_length=150, blank=True, help_text="e.g. JIGAWA SOUTH")
    nature_of_fault  = models.TextField()
    status           = models.CharField(max_length=20, choices=STATUS_CHOICES, default='lingering', db_index=True)
    financial_loss_ngn = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    incident_date    = models.DateField(db_index=True)
    rectified_date   = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-incident_date', 'status']

    def __str__(self):
        return f"{self.feeder.name} — {self.nature_of_fault[:50]} ({self.status})"


class TMODailyAllocation(models.Model):
    """
    Daily network-level allocation from TCN/NERC (MW).
    Source can be manual admin entry OR a DataNest sync (2-way until DataNest is wired up).
      Blue   = expected_mw  (what TCN allocated)
      Yellow = actual_mw    (avg consumption from HourlyLoad — computed, not stored)
      Red    = unpicked_mw  = expected_mw − actual_mw

    DataNest sync uses tmo_id as the upsert key and sets source='datanest'.
    Manual entry leaves tmo_id=None and source='manual'.
    DataNest values take precedence: if tmo_id is set, admin edits are overwritten on next sync.
    """
    SOURCE_CHOICES = [
        ('manual',   'Manual Entry'),
        ('datanest', 'DataNest Sync'),
    ]

    date        = models.DateField(unique=True, db_index=True)
    expected_mw = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Daily average MW allocation from TCN/NERC generation schedule"
    )
    tmo_id      = models.BigIntegerField(
        unique=True, null=True, blank=True, db_index=True,
        help_text="DataNest record ID — populated when synced; null for manual entries"
    )
    source      = models.CharField(
        max_length=10, choices=SOURCE_CHOICES, default='manual',
        help_text="How this record was created: manual admin entry or DataNest sync"
    )
    notes       = models.TextField(blank=True, help_text="Optional: source reference or remarks")

    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f"{self.date} — {self.expected_mw} MW [{self.source}]"
