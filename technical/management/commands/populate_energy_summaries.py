# technical/management/commands/populate_energy_summaries.py
import logging
from datetime import date
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum
from technical.models import EnergyDelivered, FeederEnergyDaily, FeederEnergyMonthly
from django.db import models


logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = "Populate FeederEnergyDaily & FeederEnergyMonthly from EnergyDelivered"

    def add_arguments(self, parser):
        parser.add_argument('--from-date', type=str, help='YYYY-MM-DD')
        parser.add_argument('--to-date', type=str, help='YYYY-MM-DD')
        parser.add_argument('--dry-run', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        qs = EnergyDelivered.objects.all().order_by('date')
        if options['from_date']:
            qs = qs.filter(date__gte=options['from_date'])
        if options['to_date']:
            qs = qs.filter(date__lte=options['to_date'])

        if not qs.exists():
            self.stdout.write("No EnergyDelivered rows found.")
            return

        # 1. Group by (feeder, date) → daily
        daily_data = qs.values('feeder_id', 'date').annotate(
            energy=Sum('energy_mwh')
        )

        daily_objs = [
            FeederEnergyDaily(
                feeder_id=row['feeder_id'],
                date=row['date'],
                energy_mwh=row['energy']
            )
            for row in daily_data
        ]

        if options['dry_run']:
            self.stdout.write(f"[DRY-RUN] Would create {len(daily_objs)} daily rows")
        else:
            FeederEnergyDaily.objects.bulk_create(
                daily_objs,
                update_conflicts=True,
                update_fields=['energy_mwh'],
                unique_fields=['feeder', 'date']
            )
            self.stdout.write(f"Created/updated {len(daily_objs)} daily rows")

        # 2. Monthly roll-up
        monthly_data = qs.annotate(
            month_start=models.ExpressionWrapper(
                models.functions.TruncMonth('date'),
                output_field=models.DateField()
            )
        ).values('feeder_id', 'month_start').annotate(
            energy=Sum('energy_mwh')
        )

        monthly_objs = [
            FeederEnergyMonthly(
                feeder_id=row['feeder_id'],
                period=row['month_start'],
                energy_mwh=row['energy']
            )
            for row in monthly_data
        ]

        if options['dry_run']:
            self.stdout.write(f"[DRY-RUN] Would create {len(monthly_objs)} monthly rows")
        else:
            FeederEnergyMonthly.objects.bulk_create(
                monthly_objs,
                update_conflicts=True,
                update_fields=['energy_mwh'],
                unique_fields=['feeder', 'period']
            )
            self.stdout.write(f"Created/updated {len(monthly_objs)} monthly rows")