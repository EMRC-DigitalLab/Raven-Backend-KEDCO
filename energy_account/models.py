# energy_account/models.py
from django.db import models

from common.models import Feeder, InjectionSubstation, PowerTransformer, UUIDModel


# ── NBET / Market Operator rates ──────────────────────────────────────────────

class NBETMarketBilateral(models.Model):
    """
    NBET and Market Operator billing rates, synced from DataNest.
    Used to compute Naira values from MWh readings.
    """
    datanest_id      = models.IntegerField(unique=True, help_text="DataNest auto-increment PK")
    effective_date   = models.DateField(unique=True)
    rate_year        = models.PositiveSmallIntegerField()
    nbet_rate        = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    market_operator_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    bilateral        = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_active        = models.BooleanField(default=True)
    datanest_created_at = models.DateTimeField(null=True, blank=True)
    datanest_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-effective_date']
        verbose_name        = 'NBET Market Bilateral Rate'
        verbose_name_plural = 'NBET Market Bilateral Rates'

    def __str__(self):
        return f"{self.effective_date} — NBET: {self.nbet_rate} | MO: {self.market_operator_rate}"


# ── Grid Meter Registry ───────────────────────────────────────────────────────

class EAGridMeter(UUIDModel, models.Model):
    """
    Physical grid-level meters installed at injection stations.
    One billing meter per transformer; one check meter per 33kV feeder.
    """
    OWNER_TYPE_CHOICES = [
        ('transformer', 'Transformer'),
        ('feeder',      'Feeder'),
    ]
    STATUS_CHOICES = [
        ('active',   'Active'),
        ('inactive', 'Inactive'),
        ('replaced', 'Replaced'),
    ]

    datanest_id         = models.CharField(max_length=36, unique=True)
    meter_no            = models.CharField(max_length=100, unique=True)
    multiplying_factor  = models.DecimalField(max_digits=10, decimal_places=4, default=1)
    meter_owner_type    = models.CharField(max_length=15, choices=OWNER_TYPE_CHOICES)
    transformer         = models.ForeignKey(
        PowerTransformer, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='grid_meters',
    )
    feeder              = models.ForeignKey(
        Feeder, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='grid_meters',
    )
    percentage_share    = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    status              = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    datanest_created_at = models.DateTimeField(null=True, blank=True)
    datanest_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = 'EA Grid Meter'
        verbose_name_plural = 'EA Grid Meters'
        indexes = [
            models.Index(fields=['meter_owner_type', 'status']),
        ]

    def __str__(self):
        return f"{self.meter_no} ({self.meter_owner_type})"


# ── Monthly Return (core billing envelope) ────────────────────────────────────

class EAMonthlyReturn(UUIDModel, models.Model):
    """
    One billing return per injection station per month.
    Drives the entire EA lifecycle: draft → reconciled.
    """
    STATUS_CHOICES = [
        ('draft',        'Draft'),
        ('submitted',    'Submitted'),
        ('tcn_submitted','TCN Submitted'),
        ('tcn_agreed',   'TCN Agreed'),
        ('tcn_disputed', 'TCN Disputed'),
        ('mo_submitted', 'MO Submitted'),
        ('mo_agreed',    'MO Agreed'),
        ('mo_disputed',  'MO Disputed'),
        ('settled',      'Settled'),
        ('reconciled',   'Reconciled'),
    ]

    datanest_id             = models.CharField(max_length=36, unique=True)
    station                 = models.ForeignKey(
        InjectionSubstation, on_delete=models.CASCADE, related_name='monthly_returns',
    )
    month                   = models.PositiveSmallIntegerField()
    year                    = models.PositiveSmallIntegerField()
    status                  = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', db_index=True)
    deviation_threshold_pct = models.DecimalField(max_digits=5, decimal_places=2, default=2)
    is_late                 = models.BooleanField(default=False)
    late_reason             = models.TextField(blank=True)
    station_billing_mwh     = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    station_billing_naira   = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    submitted_at            = models.DateTimeField(null=True, blank=True)
    datanest_created_at     = models.DateTimeField(null=True, blank=True)
    datanest_updated_at     = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('station', 'month', 'year')
        ordering        = ['-year', '-month', 'station']
        indexes = [
            models.Index(fields=['year', 'month']),
            models.Index(fields=['status']),
            models.Index(fields=['station', 'year', 'month']),
        ]

    def __str__(self):
        return f"{self.station.slug} — {self.year}-{self.month:02d} [{self.status}]"


# ── Monthly Readings (per meter per return) ───────────────────────────────────

class EAMonthlyReading(UUIDModel, models.Model):
    """
    Individual grid meter reading for a given monthly return.
    Holds both the raw reading and the computed billing figure.
    """
    DATA_SOURCE_CHOICES = [
        ('field_read',        'Field Read'),
        ('technical_derived', 'Technical Derived'),
        ('not_available',     'Not Available'),
    ]
    BILLING_RULE_CHOICES = [
        ('within_threshold',  'Within Threshold'),
        ('max_applied',       'Max Applied'),
        ('coupling_adjusted', 'Coupling Adjusted'),
        ('no_feeder_meters',  'No Feeder Meters'),
    ]

    datanest_id         = models.CharField(max_length=36, unique=True)
    monthly_return      = models.ForeignKey(
        EAMonthlyReturn, on_delete=models.CASCADE, related_name='readings',
    )
    grid_meter          = models.ForeignKey(
        EAGridMeter, on_delete=models.CASCADE, related_name='readings',
    )
    transformer         = models.ForeignKey(
        PowerTransformer, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='monthly_readings',
    )
    feeder              = models.ForeignKey(
        Feeder, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='monthly_readings',
    )
    previous_reading    = models.DecimalField(max_digits=14, decimal_places=2)
    present_reading     = models.DecimalField(max_digits=14, decimal_places=2)
    energy_mwh          = models.DecimalField(max_digits=14, decimal_places=4)
    deviation_pct       = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    is_flagged          = models.BooleanField(default=False, db_index=True)
    remarks             = models.TextField(blank=True)
    data_source         = models.CharField(max_length=20, choices=DATA_SOURCE_CHOICES, default='field_read')
    billing_mwh         = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    billing_rule_applied= models.CharField(max_length=30, choices=BILLING_RULE_CHOICES, null=True, blank=True)
    billing_naira       = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    nbet_rate_snapshot  = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    datanest_created_at = models.DateTimeField(null=True, blank=True)
    datanest_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = 'EA Monthly Reading'
        verbose_name_plural = 'EA Monthly Readings'
        indexes = [
            models.Index(fields=['monthly_return', 'grid_meter']),
            models.Index(fields=['is_flagged']),
        ]

    def __str__(self):
        return f"{self.monthly_return} | {self.grid_meter.meter_no} — {self.energy_mwh} MWh"


# ── Feeder Technical Energy (Stream B) ───────────────────────────────────────

class EAFeederTechnicalEnergy(UUIDModel, models.Model):
    """
    Auto-computed energy per feeder per billing period from the technical
    monitoring system (Stream B). Acts as cross-check against physical reads.
    """
    DATA_SOURCE_CHOICES = [
        ('auto_technical', 'Auto Technical'),
        ('manual_entry',   'Manual Entry'),
    ]

    datanest_id           = models.CharField(max_length=36, unique=True)
    monthly_return        = models.ForeignKey(
        EAMonthlyReturn, on_delete=models.CASCADE, related_name='feeder_technical_energies',
    )
    feeder                = models.ForeignKey(
        Feeder, on_delete=models.CASCADE, related_name='ea_technical_energies',
    )
    feeder_type           = models.CharField(max_length=5, choices=[('33KV', '33kV'), ('11KV', '11kV')])
    station               = models.ForeignKey(
        InjectionSubstation, on_delete=models.CASCADE, related_name='feeder_technical_energies',
    )
    energy_mwh            = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    data_completeness_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    days_with_data        = models.PositiveSmallIntegerField(default=0)
    total_days_in_period  = models.PositiveSmallIntegerField(default=30)
    has_gaps              = models.BooleanField(default=False, db_index=True)
    gap_notes             = models.TextField(blank=True)
    data_source           = models.CharField(max_length=20, choices=DATA_SOURCE_CHOICES, default='auto_technical')
    populated_at          = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = 'EA Feeder Technical Energy'
        verbose_name_plural = 'EA Feeder Technical Energies'
        unique_together     = ('monthly_return', 'feeder')
        indexes = [
            models.Index(fields=['monthly_return', 'feeder_type']),
            models.Index(fields=['has_gaps']),
        ]

    def __str__(self):
        return f"{self.feeder.slug} | {self.monthly_return} — {self.energy_mwh} MWh"


# ── TCN Reconciliation ────────────────────────────────────────────────────────

class EATCNReconciliation(UUIDModel, models.Model):
    """
    TCN vs KEDCO energy figure reconciliation for each monthly return.
    TCN accesses a PIN-protected portal to enter their figure.
    """
    STATUS_CHOICES = [
        ('pending',       'Pending'),
        ('sent',          'Sent'),
        ('tcn_submitted', 'TCN Submitted'),
        ('under_review',  'Under Review'),
        ('agreed',        'Agreed'),
        ('disputed',      'Disputed'),
        ('resolved',      'Resolved'),
    ]

    datanest_id          = models.CharField(max_length=36, unique=True)
    monthly_return       = models.OneToOneField(
        EAMonthlyReturn, on_delete=models.CASCADE, related_name='tcn_reconciliation',
    )
    kedco_figure_mwh     = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    kedco_figure_naira   = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    kedco_submitted_at   = models.DateTimeField(null=True, blank=True)
    kedco_notes          = models.TextField(blank=True)
    tcn_figure_mwh       = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    tcn_figure_naira     = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    tcn_submitted_at     = models.DateTimeField(null=True, blank=True)
    tcn_contact_name     = models.CharField(max_length=100, blank=True)
    tcn_contact_email    = models.CharField(max_length=100, blank=True)
    tcn_notes            = models.TextField(blank=True)
    difference_mwh       = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    difference_naira     = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    difference_pct       = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    status               = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
    resolution_notes     = models.TextField(blank=True)
    agreed_figure_mwh    = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    resolved_at          = models.DateTimeField(null=True, blank=True)
    datanest_created_at  = models.DateTimeField(null=True, blank=True)
    datanest_updated_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = 'EA TCN Reconciliation'
        verbose_name_plural = 'EA TCN Reconciliations'

    def __str__(self):
        return f"TCN Recon | {self.monthly_return} [{self.status}]"


class EATCNReconciliationNote(UUIDModel, models.Model):
    """
    Thread of notes between KEDCO and TCN during reconciliation.
    """
    AUTHOR_TYPE_CHOICES = [('kedco', 'KEDCO'), ('tcn', 'TCN')]

    datanest_id          = models.CharField(max_length=36, unique=True)
    tcn_reconciliation   = models.ForeignKey(
        EATCNReconciliation, on_delete=models.CASCADE, related_name='notes',
    )
    author_type          = models.CharField(max_length=10, choices=AUTHOR_TYPE_CHOICES, db_index=True)
    author_name          = models.CharField(max_length=100, blank=True)
    note_text            = models.TextField()
    figure_at_time_mwh   = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    datanest_created_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = 'EA TCN Reconciliation Note'
        verbose_name_plural = 'EA TCN Reconciliation Notes'
        ordering            = ['datanest_created_at']

    def __str__(self):
        return f"{self.author_type.upper()} note | {self.tcn_reconciliation}"


# ── MO Reconciliation ─────────────────────────────────────────────────────────

class EAMOReconciliation(UUIDModel, models.Model):
    """
    Market Operator reconciliation — MO invoice figure vs KEDCO figure.
    """
    STATUS_CHOICES = [
        ('pending',  'Pending'),
        ('agreed',   'Agreed'),
        ('disputed', 'Disputed'),
    ]

    datanest_id         = models.CharField(max_length=36, unique=True)
    monthly_return      = models.OneToOneField(
        EAMonthlyReturn, on_delete=models.CASCADE, related_name='mo_reconciliation',
    )
    mo_figure_mwh       = models.DecimalField(max_digits=14, decimal_places=4)
    kedco_figure_mwh    = models.DecimalField(max_digits=14, decimal_places=4)
    difference_mwh      = models.DecimalField(max_digits=14, decimal_places=4)
    status              = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)
    notes               = models.TextField(blank=True)
    datanest_created_at = models.DateTimeField(null=True, blank=True)
    datanest_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = 'EA MO Reconciliation'
        verbose_name_plural = 'EA MO Reconciliations'

    def __str__(self):
        return f"MO Recon | {self.monthly_return} [{self.status}]"


# ── Weekly Readings ───────────────────────────────────────────────────────────

class EAWeeklyReading(UUIDModel, models.Model):
    """
    Operational weekly meter readings per feeder.
    Flags week-on-week anomalies beyond the configured threshold (default 20%).
    """
    DATA_SOURCE_CHOICES = [
        ('field_read',        'Field Read'),
        ('technical_derived', 'Technical Derived'),
    ]

    datanest_id          = models.CharField(max_length=36, unique=True)
    station              = models.ForeignKey(
        InjectionSubstation, on_delete=models.CASCADE, related_name='weekly_readings',
    )
    feeder               = models.ForeignKey(
        Feeder, on_delete=models.CASCADE, related_name='ea_weekly_readings',
    )
    grid_meter           = models.ForeignKey(
        EAGridMeter, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='weekly_readings',
    )
    week_start_date      = models.DateField(db_index=True)
    week_end_date        = models.DateField()
    reading_value        = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    energy_mwh           = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    prior_week_energy_mwh= models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    delta_pct            = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    is_anomaly           = models.BooleanField(default=False, db_index=True)
    data_source          = models.CharField(max_length=20, choices=DATA_SOURCE_CHOICES, default='field_read')
    notes                = models.TextField(blank=True)
    datanest_created_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = 'EA Weekly Reading'
        verbose_name_plural = 'EA Weekly Readings'
        unique_together     = ('feeder', 'week_start_date')
        indexes = [
            models.Index(fields=['week_start_date', 'station']),
            models.Index(fields=['is_anomaly']),
        ]

    def __str__(self):
        return f"{self.feeder.slug} | {self.week_start_date} — {self.energy_mwh} MWh"


# ── Meter Check Schedules & Records ──────────────────────────────────────────

class EAMeterCheckSchedule(UUIDModel, models.Model):
    """
    Planned weekly physical inspection of grid meters at a station.
    """
    STATUS_CHOICES = [
        ('pending',   'Pending'),
        ('completed', 'Completed'),
        ('missed',    'Missed'),
    ]

    datanest_id         = models.CharField(max_length=36, unique=True)
    station             = models.ForeignKey(
        InjectionSubstation, on_delete=models.CASCADE, related_name='meter_check_schedules',
    )
    week_label          = models.CharField(max_length=100)
    scheduled_date      = models.DateField(db_index=True)
    assigned_by         = models.CharField(max_length=36, blank=True)
    status              = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)
    datanest_created_at = models.DateTimeField(null=True, blank=True)
    datanest_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = 'EA Meter Check Schedule'
        verbose_name_plural = 'EA Meter Check Schedules'

    def __str__(self):
        return f"{self.station.slug} | {self.scheduled_date} [{self.status}]"


class EAMeterCheckRecord(UUIDModel, models.Model):
    """
    Actual field check results — polarity, voltages, CT ratio, battery, risk.
    """
    POLARITY_CHOICES = [
        ('correct',     'Correct'),
        ('incorrect',   'Incorrect'),
        ('not_checked', 'Not Checked'),
    ]
    BATTERY_CHOICES = [
        ('ok',          'OK'),
        ('low',         'Low'),
        ('dead',        'Dead'),
        ('not_checked', 'Not Checked'),
    ]
    RISK_CHOICES = [
        ('none',     'None'),
        ('low',      'Low'),
        ('medium',   'Medium'),
        ('high',     'High'),
        ('critical', 'Critical'),
    ]

    datanest_id           = models.CharField(max_length=36, unique=True)
    schedule              = models.OneToOneField(
        EAMeterCheckSchedule, on_delete=models.CASCADE, related_name='record',
    )
    station               = models.ForeignKey(
        InjectionSubstation, on_delete=models.CASCADE, related_name='meter_check_records',
    )
    check_date            = models.DateField(db_index=True)
    polarity              = models.CharField(max_length=15, choices=POLARITY_CHOICES, default='not_checked')
    power_factor          = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    phase_angle           = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    voltage_r             = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    voltage_y             = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    voltage_b             = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    current_reading       = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    import_reading        = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    export_reading        = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    ct_ratio              = models.CharField(max_length=50, blank=True)
    battery_level         = models.CharField(max_length=15, choices=BATTERY_CHOICES, default='not_checked')
    meter_number_verified = models.BooleanField(default=False)
    issues_observed       = models.TextField(blank=True)
    risk_level            = models.CharField(max_length=10, choices=RISK_CHOICES, default='none', db_index=True)
    remarks               = models.TextField(blank=True)
    datanest_created_at   = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = 'EA Meter Check Record'
        verbose_name_plural = 'EA Meter Check Records'

    def __str__(self):
        return f"{self.station.slug} | {self.check_date} — {self.risk_level}"


# ── Station Assignments ───────────────────────────────────────────────────────

class EAStationAssignment(UUIDModel, models.Model):
    """
    Which DataNest users are assigned to which stations for EA operations.
    datanest_user_id stored as-is — no FK to Raven users (different PKs).
    """
    STATUS_CHOICES = [
        ('active',   'Active'),
        ('inactive', 'Inactive'),
    ]

    datanest_id         = models.CharField(max_length=36, unique=True)
    station             = models.ForeignKey(
        InjectionSubstation, on_delete=models.CASCADE, related_name='ea_assignments',
    )
    datanest_user_id    = models.CharField(max_length=36, db_index=True)
    assigned_by         = models.CharField(max_length=36, blank=True)
    assigned_at         = models.DateTimeField(null=True, blank=True)
    status              = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active', db_index=True)
    datanest_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = 'EA Station Assignment'
        verbose_name_plural = 'EA Station Assignments'
        unique_together     = ('station', 'datanest_user_id')

    def __str__(self):
        return f"{self.station.slug} ← {self.datanest_user_id} [{self.status}]"


# ── Coupling Log ──────────────────────────────────────────────────────────────

class EACouplingLog(UUIDModel, models.Model):
    """
    Records when a transformer goes on fault and its feeders are moved to
    another transformer, so billing adjusts for the coupling period.
    """
    COUPLING_TYPE_CHOICES = [
        ('fault',       'Fault'),
        ('maintenance', 'Maintenance'),
        ('planned',     'Planned'),
        ('emergency',   'Emergency'),
    ]

    datanest_id             = models.CharField(max_length=36, unique=True)
    station                 = models.ForeignKey(
        InjectionSubstation, on_delete=models.CASCADE, related_name='coupling_logs',
    )
    billing_month           = models.PositiveSmallIntegerField()
    billing_year            = models.SmallIntegerField(db_index=True)
    faulted_transformer     = models.ForeignKey(
        PowerTransformer, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='coupling_faults',
    )
    substitute_transformer  = models.ForeignKey(
        PowerTransformer, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='coupling_substitutes',
    )
    coupling_start          = models.DateTimeField()
    coupling_end            = models.DateTimeField(null=True, blank=True)
    affected_feeder_ids     = models.JSONField(default=list)
    coupling_type           = models.CharField(max_length=15, choices=COUPLING_TYPE_CHOICES, default='fault')
    billing_impact_notes    = models.TextField(blank=True)
    datanest_created_at     = models.DateTimeField(null=True, blank=True)
    datanest_updated_at     = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = 'EA Coupling Log'
        verbose_name_plural = 'EA Coupling Logs'
        indexes = [
            models.Index(fields=['station', 'billing_year', 'billing_month']),
        ]

    def __str__(self):
        return f"{self.station.slug} coupling | {self.billing_year}-{self.billing_month:02d} [{self.coupling_type}]"


# ── EA Settings ───────────────────────────────────────────────────────────────

class EASettings(models.Model):
    """
    Global EA configuration synced from DataNest (read-only mirror).
    e.g. deviation_threshold_pct, late_submission_cutoff_day.
    """
    setting_key         = models.CharField(max_length=100, primary_key=True)
    setting_value       = models.CharField(max_length=500)
    description         = models.TextField(blank=True)
    datanest_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = 'EA Setting'
        verbose_name_plural = 'EA Settings'

    def __str__(self):
        return f"{self.setting_key} = {self.setting_value}"
