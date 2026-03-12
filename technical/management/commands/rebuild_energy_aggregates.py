# technical/management/commands/rebuild_energy_aggregates.py

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from common.models import Feeder
from technical.models import EnergyDelivered, FeederEnergyDaily, FeederEnergyMonthly

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = """
    Rebuild FeederEnergyDaily and FeederEnergyMonthly pre-aggregated tables
    from the corrected EnergyDelivered data.
    
    This should be run AFTER recalculating EnergyDelivered from cumulative readings.
    
    Examples:
        # Rebuild all aggregates
        python manage.py rebuild_energy_aggregates --all
        
        # Rebuild for specific date range
        python manage.py rebuild_energy_aggregates --from-date 2024-01-01 --to-date 2024-12-31
        
        # Rebuild for specific feeder
        python manage.py rebuild_energy_aggregates --feeder-slug my-feeder-01 --all
        
        # Dry run
        python manage.py rebuild_energy_aggregates --all --dry-run
    """

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='Rebuild all energy aggregates'
        )
        parser.add_argument(
            '--from-date',
            type=str,
            help='Start date (YYYY-MM-DD)'
        )
        parser.add_argument(
            '--to-date',
            type=str,
            help='End date (YYYY-MM-DD)'
        )
        parser.add_argument(
            '--feeder-slug',
            type=str,
            help='Specific feeder slug to rebuild'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be rebuilt without making changes'
        )
        parser.add_argument(
            '--daily-only',
            action='store_true',
            help='Only rebuild FeederEnergyDaily'
        )
        parser.add_argument(
            '--monthly-only',
            action='store_true',
            help='Only rebuild FeederEnergyMonthly'
        )

    def handle(self, *args, **options):
        # Parse date range
        if options['from_date']:
            from_date = datetime.strptime(options['from_date'], '%Y-%m-%d').date()
        else:
            from_date = None
        
        if options['to_date']:
            to_date = datetime.strptime(options['to_date'], '%Y-%m-%d').date()
        else:
            to_date = None
        
        # Parse feeder
        feeder_filter = {}
        if options['feeder_slug']:
            try:
                feeder = Feeder.objects.get(slug=options['feeder_slug'])
                feeder_filter['feeder'] = feeder
                self.stdout.write(f"Filtering for feeder: {feeder.name}")
            except Feeder.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"Feeder not found: {options['feeder_slug']}"))
                return
        
        if not options['all'] and not from_date and not to_date:
            self.stdout.write(self.style.ERROR('Please specify --all or date range'))
            return
        
        # Rebuild daily aggregates
        if not options['monthly_only']:
            self.stdout.write("\n" + "="*70)
            self.stdout.write(self.style.SUCCESS("REBUILDING DAILY AGGREGATES"))
            self.stdout.write("="*70)
            self._rebuild_daily_aggregates(from_date, to_date, feeder_filter, options['dry_run'])
        
        # Rebuild monthly aggregates
        if not options['daily_only']:
            self.stdout.write("\n" + "="*70)
            self.stdout.write(self.style.SUCCESS("REBUILDING MONTHLY AGGREGATES"))
            self.stdout.write("="*70)
            self._rebuild_monthly_aggregates(from_date, to_date, feeder_filter, options['dry_run'])
    
    def _rebuild_daily_aggregates(self, from_date, to_date, feeder_filter, dry_run):
        """
        Rebuild FeederEnergyDaily from EnergyDelivered.
        Since EnergyDelivered is already daily, this is a 1:1 copy.
        """
        # Build filter
        date_filter = {}
        if from_date:
            date_filter['date__gte'] = from_date
        if to_date:
            date_filter['date__lte'] = to_date
        
        energy_records = EnergyDelivered.objects.filter(
            **feeder_filter,
            **date_filter
        ).order_by('feeder', 'date')
        
        total_records = energy_records.count()
        self.stdout.write(f"Found {total_records} EnergyDelivered records to process")
        
        created_count = 0
        updated_count = 0
        
        for energy_record in energy_records:
            if not dry_run:
                daily_record, created = FeederEnergyDaily.objects.update_or_create(
                    feeder=energy_record.feeder,
                    date=energy_record.date,
                    defaults={
                        'energy_mwh': energy_record.energy_mwh
                    }
                )
                
                if created:
                    created_count += 1
                else:
                    updated_count += 1
                
                if (created_count + updated_count) % 100 == 0:
                    self.stdout.write(f"  Processed {created_count + updated_count} records...")
            else:
                exists = FeederEnergyDaily.objects.filter(
                    feeder=energy_record.feeder,
                    date=energy_record.date
                ).exists()
                
                if exists:
                    updated_count += 1
                else:
                    created_count += 1
        
        self.stdout.write("\n" + "-"*70)
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes made"))
        self.stdout.write(self.style.SUCCESS(f"Processed {total_records} daily records"))
        self.stdout.write(f"  Created: {created_count}")
        self.stdout.write(f"  Updated: {updated_count}")
    
    def _rebuild_monthly_aggregates(self, from_date, to_date, feeder_filter, dry_run):
        """
        Rebuild FeederEnergyMonthly by aggregating EnergyDelivered records.
        """
        # Get all feeders
        if feeder_filter:
            feeders = [feeder_filter['feeder']]
        else:
            feeders = Feeder.objects.all()
        
        # Determine month range
        if from_date and to_date:
            months = []
            current_month = from_date.replace(day=1)
            end_month = to_date.replace(day=1)
            
            while current_month <= end_month:
                months.append(current_month)
                current_month = current_month + relativedelta(months=1)
        else:
            # Get all unique months from EnergyDelivered
            months = EnergyDelivered.objects.filter(
                **feeder_filter
            ).dates('date', 'month')
        
        self.stdout.write(f"Processing {len(feeders)} feeders across {len(list(months))} months")
        
        created_count = 0
        updated_count = 0
        total_combinations = len(feeders) * len(list(months))
        processed = 0
        
        for feeder in feeders:
            for month_start in months:
                # Calculate month end
                if month_start.month == 12:
                    month_end = month_start.replace(year=month_start.year + 1, month=1, day=1) - timedelta(days=1)
                else:
                    month_end = month_start.replace(month=month_start.month + 1, day=1) - timedelta(days=1)
                
                # Aggregate energy for this feeder and month
                monthly_total = EnergyDelivered.objects.filter(
                    feeder=feeder,
                    date__gte=month_start,
                    date__lte=month_end
                ).aggregate(total=Sum('energy_mwh'))['total']
                
                if monthly_total is None:
                    continue  # No data for this month
                
                if not dry_run:
                    monthly_record, created = FeederEnergyMonthly.objects.update_or_create(
                        feeder=feeder,
                        period=month_start,
                        defaults={
                            'energy_mwh': monthly_total
                        }
                    )
                    
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1
                else:
                    exists = FeederEnergyMonthly.objects.filter(
                        feeder=feeder,
                        period=month_start
                    ).exists()
                    
                    if exists:
                        updated_count += 1
                    else:
                        created_count += 1
                
                processed += 1
                if processed % 50 == 0:
                    self.stdout.write(f"  Processed {processed}/{total_combinations} feeder-month combinations...")
        
        self.stdout.write("\n" + "-"*70)
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes made"))
        self.stdout.write(self.style.SUCCESS(f"Processed {processed} feeder-month combinations"))
        self.stdout.write(f"  Created: {created_count}")
        self.stdout.write(f"  Updated: {updated_count}")