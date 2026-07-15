from django.conf import settings
from django.db import models

from common.models import Band, Feeder, UUIDModel


class FeederCommercialProfile(UUIDModel, models.Model):
    """
    Per-feeder commercial parameters used by the Phase 2 revenue simulation.

    ATC&C loss components (NERC definition):
      - billing_efficiency_pct  = Energy_Billed / Energy_Input (captures technical + commercial losses)
      - collection_efficiency_pct = Revenue_Collected / Revenue_Billed (captures collection losses)
      - ATC&C combined = 1 - (billing_efficiency × collection_efficiency)

    KEDCO benchmarks (NERC Q1 2025):
      billing_efficiency  ≈ 99%   (strong metering)
      collection_efficiency ≈ 80% (improving, up +6.55pp Q1 2025)
      ATC&C losses ≈ 42%
    """
    feeder = models.OneToOneField(
        Feeder, on_delete=models.CASCADE, related_name='commercial_profile'
    )
    billing_efficiency_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=99.00,
        help_text="ATC component: Energy_Billed / Energy_Input × 100. Captures technical and commercial losses."
    )
    collection_efficiency_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=80.00,
        help_text="C component: Revenue_Collected / Revenue_Billed × 100. Captures cash collection performance."
    )
    monthly_revenue_target_ngn = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Monthly revenue target in Naira for this feeder. Used to compute gap-to-target."
    )
    effective_from = models.DateField(
        help_text="Date from which this commercial profile applies."
    )
    is_active = models.BooleanField(default=True)
    source = models.CharField(
        max_length=255, blank=True,
        help_text="Reference e.g. 'NERC Q1 2025 KEDCO benchmark' or 'Internal audit Jan 2026'"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['feeder__name']
        verbose_name = 'Feeder Commercial Profile'
        verbose_name_plural = 'Feeder Commercial Profiles'

    @property
    def atcc_loss_pct(self):
        """ATC&C combined loss = 1 - (billing_efficiency × collection_efficiency)."""
        b = float(self.billing_efficiency_pct) / 100
        c = float(self.collection_efficiency_pct) / 100
        return round((1 - b * c) * 100, 2)

    def __str__(self):
        return (
            f"{self.feeder.name} | "
            f"Billing: {self.billing_efficiency_pct}% | "
            f"Collection: {self.collection_efficiency_pct}% | "
            f"ATC&C loss: {self.atcc_loss_pct}%"
        )


class PCCConfig(UUIDModel, models.Model):
    """
    Stores the DisCo-level PCC (Partially Contracted Capacity) figure from NERC.
    Configurable — no hard-coded values. Update here when NERC revises the figure.
    Multiple records supported so history is preserved. Engine uses the active record
    that covers the simulation date.
    """
    label = models.CharField(
        max_length=100,
        help_text="Descriptive label e.g. 'KEDCO PCC — NERC Order July 2025'"
    )
    total_pcc_mwh_per_hour = models.DecimalField(
        max_digits=10, decimal_places=4,
        help_text="Total DisCo PCC in MWh/h as issued by NERC (e.g. 268 for KEDCO)"
    )
    nerc_offtake_kpi_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=95.00,
        help_text="NERC minimum offtake KPI as a percentage (currently 95%)"
    )
    demand_lookback_days = models.PositiveSmallIntegerField(
        default=30,
        help_text="How many historical days to use when estimating feeder demand"
    )
    effective_from = models.DateField(
        help_text="Date from which this PCC config applies"
    )
    effective_to = models.DateField(
        null=True, blank=True,
        help_text="Date until which this config applies. Leave blank if currently active."
    )
    is_active = models.BooleanField(default=True)
    source = models.CharField(
        max_length=255, blank=True,
        help_text="Reference e.g. 'NERC Order No. NERC/2025/067'"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-effective_from']
        verbose_name = 'PCC Configuration'
        verbose_name_plural = 'PCC Configurations'

    @property
    def total_pcc_mwh_per_day(self):
        return float(self.total_pcc_mwh_per_hour) * 24

    @property
    def nerc_kpi_floor_mwh_per_day(self):
        return self.total_pcc_mwh_per_day * (float(self.nerc_offtake_kpi_pct) / 100)

    @classmethod
    def get_for_date(cls, sim_date):
        """Return the active PCC config that covers the given simulation date."""
        return (
            cls.objects
            .filter(
                is_active=True,
                effective_from__lte=sim_date,
            )
            .filter(
                models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=sim_date)
            )
            .order_by('-effective_from')
            .first()
        )

    def __str__(self):
        return f"{self.label} | {self.total_pcc_mwh_per_hour} MWh/h | from {self.effective_from}"


class SimulationRun(UUIDModel, models.Model):
    SCENARIO_CHOICES = [
        ('actual', 'Actual'),
        ('minimum_sbt', 'Minimum SBT'),
        ('maximum_sbt', 'Maximum SBT'),
        ('sbt_ration', 'SBT Ration'),
        ('unsuppressed', 'Unsuppressed'),
    ]
    ZONE_CHOICES = [
        ('zone_1', 'Zone 1 — Critical Shortage'),
        ('zone_2', 'Zone 2 — Operational'),
        ('zone_3', 'Zone 3 — Excess Supply'),
    ]
    SEVERITY_CHOICES = [
        ('none', 'None'),
        ('mild', 'Mild Shortage (75–99% of E_min)'),
        ('moderate', 'Moderate Shortage (50–74% of E_min)'),
        ('severe', 'Severe Shortage (25–49% of E_min)'),
        ('critical', 'Critical / L/S (< 25% of E_min)'),
    ]

    scenario = models.CharField(max_length=20, choices=SCENARIO_CHOICES)
    simulation_date = models.DateField()

    # The three benchmarks
    e_offtake = models.DecimalField(max_digits=14, decimal_places=4, help_text="User-supplied energy available (MWh)")
    e_min = models.DecimalField(max_digits=14, decimal_places=4, help_text="NERC compliance floor (MWh)")
    e_max = models.DecimalField(max_digits=14, decimal_places=4, help_text="Unsuppressed network maximum (MWh)")
    e_actual = models.DecimalField(max_digits=14, decimal_places=4, help_text="Historical ground truth (MWh)")

    # Classification
    zone = models.CharField(max_length=10, choices=ZONE_CHOICES)
    shortage_severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default='none')

    # Flags
    compliance_breach = models.BooleanField(default=False)
    excess_supply = models.BooleanField(default=False)
    band_a_greatly_downgraded = models.BooleanField(default=False)
    nerc_kpi_breach = models.BooleanField(
        default=False,
        help_text="True if E_offtake is below the NERC 95% offtake KPI threshold"
    )
    pcc_config = models.ForeignKey(
        PCCConfig, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='simulation_runs',
        help_text="The PCC configuration used for this simulation run"
    )

    # Summary totals
    total_allocated_mwh = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    surplus_mwh = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    deficit_mwh = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)

    # Benchmark deviations
    deviation_from_actual = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    deviation_from_e_min = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    deviation_from_e_max = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)

    # Feeder state counts
    energised_count = models.PositiveIntegerField(default=0)
    upgraded_count = models.PositiveIntegerField(default=0)
    downgraded_count = models.PositiveIntegerField(default=0)
    load_shed_count = models.PositiveIntegerField(default=0)

    # Phase 2 — Revenue summary (₦)
    total_revenue_potential_ngn = models.DecimalField(
        max_digits=20, decimal_places=2, null=True, blank=True,
        help_text="Gross revenue if 100% of allocated energy is billed and collected (₦)"
    )
    total_expected_billing_ngn = models.DecimalField(
        max_digits=20, decimal_places=2, null=True, blank=True,
        help_text="Expected billing after ATC losses applied (₦)"
    )
    total_expected_collection_ngn = models.DecimalField(
        max_digits=20, decimal_places=2, null=True, blank=True,
        help_text="Expected cash collected after collection efficiency applied (₦)"
    )
    total_atcc_loss_ngn = models.DecimalField(
        max_digits=20, decimal_places=2, null=True, blank=True,
        help_text="Total ATC&C loss exposure in Naira (₦)"
    )
    revenue_per_mwh_ngn = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="Expected collection per MWh allocated — efficiency ranking metric (₦/MWh)"
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='simulation_runs'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['simulation_date']),
            models.Index(fields=['scenario']),
            models.Index(fields=['-created_at']),
        ]

    def __str__(self):
        return f"{self.get_scenario_display()} | {self.simulation_date} | {self.e_offtake} MWh"


class SimulationFeederResult(UUIDModel, models.Model):
    STATUS_CHOICES = [
        ('energised', 'Energised'),
        ('upgraded', 'Upgraded'),
        ('downgraded', 'Downgraded'),
        ('load_shed', 'Load Shed'),
    ]

    simulation = models.ForeignKey(SimulationRun, on_delete=models.CASCADE, related_name='feeder_results')
    feeder = models.ForeignKey(Feeder, on_delete=models.CASCADE, related_name='simulation_results')
    assigned_band = models.ForeignKey(Band, on_delete=models.SET_NULL, null=True, related_name='assigned_results')
    effective_band = models.ForeignKey(Band, on_delete=models.SET_NULL, null=True, related_name='effective_results')

    allocated_energy_mwh = models.DecimalField(max_digits=12, decimal_places=4)
    effective_hours = models.DecimalField(max_digits=5, decimal_places=2)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES)

    forecasted_demand_mwh = models.DecimalField(max_digits=12, decimal_places=4)
    band_minimum_energy_mwh = models.DecimalField(max_digits=12, decimal_places=4)

    # Phase 2 — Per-feeder revenue (₦)
    tariff_rate_ngn_per_kwh = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Tariff rate used for this feeder's revenue calculation (₦/kWh)"
    )
    billing_efficiency_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="ATC component used: Energy_Billed / Energy_Input × 100"
    )
    collection_efficiency_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="C component used: Revenue_Collected / Revenue_Billed × 100"
    )
    revenue_potential_ngn = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Gross revenue potential: allocated MWh × 1000 × tariff rate (₦)"
    )
    expected_billing_ngn = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Revenue potential × billing efficiency (₦)"
    )
    expected_collection_ngn = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Expected billing × collection efficiency — cash KEDCO will actually receive (₦)"
    )
    atcc_loss_ngn = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Revenue potential − expected collection — ATC&C loss in Naira (₦)"
    )
    revenue_per_mwh_ngn = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="Expected collection ÷ allocated MWh — feeder efficiency ranking metric (₦/MWh)"
    )

    class Meta:
        unique_together = ('simulation', 'feeder')
        indexes = [
            models.Index(fields=['simulation', 'status']),
            models.Index(fields=['feeder']),
        ]

    def __str__(self):
        return f"{self.feeder.name} | {self.get_status_display()} | {self.allocated_energy_mwh} MWh"
