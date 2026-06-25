from datetime import date

from django.core.management.base import BaseCommand

from simulator.models import PCCConfig


class Command(BaseCommand):
    help = 'Seeds the initial KEDCO PCC configuration from NERC Order NERC/2025/067.'

    def handle(self, *args, **options):
        label = 'KEDCO PCC — NERC Order July 2025'

        if PCCConfig.objects.filter(label=label).exists():
            self.stdout.write(self.style.WARNING(f"PCCConfig '{label}' already exists — skipped."))
            return

        config = PCCConfig.objects.create(
            label=label,
            total_pcc_mwh_per_hour=268.0,
            nerc_offtake_kpi_pct=95.00,
            demand_lookback_days=30,
            effective_from=date(2025, 7, 1),
            effective_to=None,
            is_active=True,
            source='NERC Order No. NERC/2025/067',
        )

        self.stdout.write(self.style.SUCCESS(
            f"\nPCCConfig created:"
            f"\n  Label      : {config.label}"
            f"\n  PCC        : {config.total_pcc_mwh_per_hour} MWh/h = {config.total_pcc_mwh_per_day:,.1f} MWh/day"
            f"\n  KPI floor  : {config.nerc_kpi_floor_mwh_per_day:,.1f} MWh/day (95% of PCC)"
            f"\n  Effective  : {config.effective_from} onwards"
            f"\n  Source     : {config.source}"
        ))
