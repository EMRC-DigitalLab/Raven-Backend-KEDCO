# tmo/models.py
from django.conf import settings
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
    average_tariff_per_kwh = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Average electricity price in ₦/kWh for this segment (e.g. 225 for MDI, 52 for Regions)"
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


class TMONetworkDispatch(models.Model):
    """
    Daily 33KV network dispatch reconciliation (from TCN monthly load-flow Excel).
    Drives the "KEDCO Daily Real-time Allocation Based on Available Generation" chart.

      kedco_allocation_mw  — what TCN allocated to KEDCO   (blue bar)
      disco_offtake_mw     — what DISCO/KEDCO actually consumed (yellow bar)
      variance_mw          — kedco_allocation − disco_offtake
                             positive → GREEN (KEDCO over-took)
                             negative → RED   (KEDCO under-took / loss)
      available_generation_mw — national available generation that day
    """
    SOURCE_CHOICES = [
        ('manual',   'Manual / Excel Import'),
        ('datanest', 'DataNest Sync'),
    ]

    date                    = models.DateField(unique=True, db_index=True)
    kedco_allocation_mw     = models.DecimalField(max_digits=10, decimal_places=4)
    disco_offtake_mw        = models.DecimalField(max_digits=10, decimal_places=4)
    variance_mw             = models.DecimalField(max_digits=10, decimal_places=4)
    available_generation_mw = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    source                  = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='manual')
    notes                   = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date']

    def __str__(self):
        status = 'GREEN' if self.variance_mw >= 0 else 'RED'
        return f"{self.date} | alloc={self.kedco_allocation_mw} MW | disco={self.disco_offtake_mw} MW [{status}]"


class TMOSupplyHoursTarget(models.Model):
    """
    Admin-configurable monthly supply hours target per DM segment.
    Overrides feeder.band.minimum_hours in the supply compliance calculation.
    If no row exists for a segment/month, the band default is used as fallback.
    """
    SEGMENT_CHOICES = [
        ('MDI',              'MDI'),
        ('Non-MDI Band A',   'Non-MDI Band A'),
        ('Non-MDI, Non-Band A', 'Non-MDI, Non-Band A'),
    ]

    segment      = models.CharField(max_length=30, choices=SEGMENT_CHOICES, db_index=True)
    year         = models.PositiveSmallIntegerField()
    month        = models.PositiveSmallIntegerField()
    target_hours = models.DecimalField(
        max_digits=5, decimal_places=2,
        help_text="Daily hours-of-supply target for feeders in this segment (e.g. 20.00)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('segment', 'year', 'month')
        ordering = ['-year', '-month', 'segment']

    def __str__(self):
        return f"{self.segment} {self.year}-{self.month:02d} → {self.target_hours} hrs"


class TMOFeederSupplyTarget(models.Model):
    """
    Per-feeder monthly supply hours target, uploaded via Excel.
    Takes priority over the segment-level TMOSupplyHoursTarget.
    Falls back to segment target → band minimum_hours if no row exists.
    """
    feeder       = models.ForeignKey(
        'common.Feeder', on_delete=models.CASCADE, related_name='supply_targets'
    )
    year         = models.PositiveSmallIntegerField(db_index=True)
    month        = models.PositiveSmallIntegerField()
    target_hours = models.DecimalField(
        max_digits=5, decimal_places=2,
        help_text="Daily hours-of-supply target for this feeder"
    )
    uploaded_at  = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('feeder', 'year', 'month')
        ordering = ['year', 'month', 'feeder__name']

    def __str__(self):
        return f"{self.feeder.name} {self.year}-{self.month:02d} → {self.target_hours} hrs"


class TMONetworkDispatchHourly(models.Model):
    """
    24-hour breakdown for each day's 33KV dispatch reconciliation.
    Linked to the parent TMONetworkDispatch daily summary.
    hour = 1–24 (represents 01:00–24:00 from the TCN load-flow Excel).
    """
    dispatch = models.ForeignKey(
        TMONetworkDispatch, on_delete=models.CASCADE, related_name='hourly_readings'
    )
    date  = models.DateField(db_index=True)
    hour  = models.PositiveSmallIntegerField(help_text="1=01:00, 2=02:00, … 24=24:00")

    kedco_allocation_mw     = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    disco_offtake_mw        = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    variance_mw             = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    available_generation_mw = models.DecimalField(max_digits=12, decimal_places=4, null=True)

    class Meta:
        unique_together = ('date', 'hour')
        ordering = ['date', 'hour']

    def __str__(self):
        return f"{self.date} {self.hour:02d}:00 | alloc={self.kedco_allocation_mw} MW"


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


class GoogleSheetFeed(models.Model):
    """
    Monthly Google Sheets URL registry for live data pipeline.

    Each month, ops team POSTs the new spreadsheet URL here.
    The hourly Celery task reads the active feed and syncs any missing days.

    feed_type '33kv' → HourlyLoad + TMONetworkDispatch(Hourly)
    feed_type '11kv' → HourlyLoad (gap-fill only — DataNest takes priority)
    """
    FEED_TYPE_CHOICES = [
        ('33kv_load_flow',          '33KV Load Flow'),
        ('33kv_energy_accounting',  '33KV Energy Accounting'),
        ('11kv_load_flow',          '11KV Load Flow'),
        ('11kv_energy_accounting',  '11KV Energy Accounting'),
    ]

    feed_type       = models.CharField(max_length=30, choices=FEED_TYPE_CHOICES, db_index=True)
    year            = models.PositiveSmallIntegerField()
    month           = models.PositiveSmallIntegerField()
    spreadsheet_id  = models.CharField(max_length=100, help_text='Google Sheets file ID')
    spreadsheet_url = models.URLField(max_length=600, help_text='Full Google Sheets URL')
    is_active       = models.BooleanField(default=True)

    last_synced_at  = models.DateTimeField(null=True, blank=True)
    last_sync_log   = models.TextField(blank=True, help_text='Output from last sync run')

    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('feed_type', 'year', 'month')
        ordering = ['-year', '-month', 'feed_type']

    def __str__(self):
        return f"{self.get_feed_type_display()} {self.year}-{self.month:02d}"

    @staticmethod
    def extract_spreadsheet_id(url):
        import re
        m = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', url)
        return m.group(1) if m else None


class SheetSyncRun(models.Model):
    """
    One row per sync attempt (success or failure), for every feed type —
    a real history, not just "the latest run" (GoogleSheetFeed.last_synced_at/
    last_sync_log only ever holds the most recent attempt, so a task that
    silently failed at 3am and recovered by 9am left no trace of the gap).
    Written by every sync task in tmo/tasks.py, success or failure, so
    "has this been failing intermittently" becomes a query instead of a
    guess.
    """
    STATUS_CHOICES = [
        ('success', 'Success'),
        ('failed',  'Failed'),
    ]

    feed_type    = models.CharField(max_length=30, db_index=True)
    started_at   = models.DateTimeField(db_index=True)
    finished_at  = models.DateTimeField(null=True, blank=True)
    status       = models.CharField(max_length=10, choices=STATUS_CHOICES)
    result       = models.TextField(blank=True, help_text='Sync result summary, or error traceback on failure')

    class Meta:
        ordering = ['-started_at']
        indexes = [models.Index(fields=['feed_type', '-started_at'])]

    def __str__(self):
        return f'{self.feed_type} {self.status} @ {self.started_at:%Y-%m-%d %H:%M}'


class TMOFeederOutlierFlag(models.Model):
    """
    A TMO/admin decision recorded against a specific feeder-day reading that
    the automatic per-feeder outlier check (tmo.services._outlier_candidates)
    flagged as statistically unusual — far outside that feeder's own
    historical normal range. Detection is always computed live from
    EnergyDelivered; this table only remembers what a human decided about a
    specific flagged day, so the same reading doesn't need re-investigating
    every time the review list is loaded.

    Filing doesn't feed back into any calculation — it's a review/triage
    record, not a correction. A "confirmed_data_issue" doesn't fix the
    underlying EnergyDelivered row; someone still has to do that separately
    if it needs fixing.
    """
    RESOLUTION_CHOICES = [
        ('confirmed_data_issue', 'Confirmed data issue'),
        ('genuine_spike',        'Genuine spike'),
        ('false_positive',       'False positive'),
    ]

    feeder = models.ForeignKey(
        'common.Feeder', on_delete=models.CASCADE, related_name='outlier_flags'
    )
    date = models.DateField(db_index=True, help_text="The specific day being filed against")

    observed_mwh = models.DecimalField(
        max_digits=12, decimal_places=4,
        help_text="The feeder's raw EnergyDelivered reading on this day, captured at filing time"
    )
    feeder_median_mwh = models.DecimalField(
        max_digits=12, decimal_places=4,
        help_text="This feeder's own historical baseline median at filing time, for reference"
    )

    resolution = models.CharField(max_length=25, choices=RESOLUTION_CHOICES)
    notes = models.TextField(blank=True, default='')

    filed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='filed_outlier_flags',
    )
    filed_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['feeder', 'date'], name='unique_outlier_flag_per_feeder_day')
        ]
        ordering = ['-date', '-filed_at']

    def __str__(self):
        return f"{self.feeder.name} {self.date} — {self.get_resolution_display()}"


class TMONegativeNetFlag(models.Model):
    """
    A TMO/admin decision recorded against a specific 33kV bulk parent-day
    where gross minus its children sum went negative before the
    floor-at-zero clamp in the segment engine hides it (see
    TMOService.get_negative_net_candidates). Detection is always computed
    live; this table only remembers what a human decided about a specific
    flagged day, so a known/explained case (e.g. a coupling day not yet
    logged, or a confirmed one-off data error) doesn't need re-investigating
    every time the review list is loaded.

    The resolution choices mirror the actual categories found while manually
    reconciling all 72 bulk parents against TCN's ground truth this session:
    some cases were unlogged feeder coupling, some were genuine permanent
    topology mistakes, some were one-off corrupted source readings, and a
    couple (ATM, NNPC) never resolved to any of those and stayed open.
    """
    RESOLUTION_CHOICES = [
        ('needs_coupling_event', 'Needs a coupling event logged'),
        ('needs_topology_fix',   'Needs a permanent topology fix'),
        ('confirmed_data_issue', 'Confirmed data issue'),
        ('unresolved',           'Unresolved — still investigating'),
    ]

    feeder = models.ForeignKey(
        'common.Feeder', on_delete=models.CASCADE, related_name='negative_net_flags'
    )
    date = models.DateField(db_index=True, help_text="The specific day being filed against")

    gross_mwh = models.DecimalField(max_digits=12, decimal_places=4)
    children_sum_mwh = models.DecimalField(max_digits=12, decimal_places=4)
    net_mwh = models.DecimalField(
        max_digits=12, decimal_places=4,
        help_text="gross_mwh - children_sum_mwh, captured at filing time (always negative)"
    )

    resolution = models.CharField(max_length=25, choices=RESOLUTION_CHOICES)
    notes = models.TextField(blank=True, default='')

    filed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='filed_negative_net_flags',
    )
    filed_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['feeder', 'date'], name='unique_negative_net_flag_per_feeder_day')
        ]
        ordering = ['-date', '-filed_at']

    def __str__(self):
        return f"{self.feeder.name} {self.date} — {self.get_resolution_display()}"


class SheetAlertEmail(models.Model):
    """
    Recipients for Google Sheet feed alert emails.

    Managed via POST /api/tmo/sheet-feeds/alert-emails/ (technical access required).
    The hourly Celery sync task and monthly reminder task both read from this table.
    """
    email      = models.EmailField(unique=True)
    name       = models.CharField(max_length=100, blank=True)
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['email']

    def __str__(self):
        return f"{self.name} <{self.email}>" if self.name else self.email
