# technical/management/commands/resolve_old_faults.py
"""
Management command to resolve old unresolved faults in the database.

This command finds all FeederInterruption records that:
- Occurred before August 6, 2025
- Have no restored_at date (still marked as ongoing)

And sets their restored_at to August 1, 2025.

Usage:
    python manage.py resolve_old_faults
    python manage.py resolve_old_faults --dry-run
"""

from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from technical.models import FeederInterruption


class Command(BaseCommand):
    help = 'Resolve all unresolved faults that occurred before August 6, 2025'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without saving data (for testing)',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No data will be saved'))
        
        # Define the cutoff date (August 6, 2025)
        cutoff_date = timezone.make_aware(datetime(2025, 8, 6, 0, 0, 0))
        
        # Define the resolution date (August 1, 2025)
        resolution_date = timezone.make_aware(datetime(2025, 8, 1, 0, 0, 0))
        
        self.stdout.write(f'Finding unresolved faults that occurred before {cutoff_date.date()}...')
        
        # Find all unresolved faults before the cutoff date
        unresolved_faults = FeederInterruption.objects.filter(
            occurred_at__lt=cutoff_date,
            restored_at__isnull=True
        )
        
        count = unresolved_faults.count()
        self.stdout.write(f'Found {count} unresolved faults')
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS('No faults to resolve. Done!'))
            return
        
        # Show some examples
        self.stdout.write('\nSample of faults to be resolved:')
        for fault in unresolved_faults[:10]:
            self.stdout.write(
                f'  - {fault.feeder.name}: {fault.interruption_type} '
                f'(occurred: {fault.occurred_at})'
            )
        
        if count > 10:
            self.stdout.write(f'  ... and {count - 10} more')
        
        if not dry_run:
            self.stdout.write(f'\nResolving {count} faults with restored_at = {resolution_date}...')
            
            # Bulk update all unresolved faults
            updated = unresolved_faults.update(restored_at=resolution_date)
            
            self.stdout.write(self.style.SUCCESS(f'\nDone! Resolved {updated} faults.'))
        else:
            self.stdout.write(self.style.WARNING(f'\nDRY RUN: Would have resolved {count} faults.'))