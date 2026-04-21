# technical/management/commands/resync_interruptions.py
"""
Re-run the interruptions sync for a specific date range, bypassing the
watermark window. Useful for backfilling or correcting a period.

Usage:
    # This month (default)
    python manage.py resync_interruptions

    # Specific range
    python manage.py resync_interruptions --start 2026-04-01 --end 2026-04-30

    # All time (everything in technicalenergyfault)
    python manage.py resync_interruptions --start 2025-01-01
"""

from datetime import datetime, date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from technical.sync.interruptions import (
    _pass1_new_faults,
    _pass2_resolve_open,
    _pass3_delete_stale,
)
from technical.sync.fault_normalizer import FaultNormalizer
from common.models import Feeder


class Command(BaseCommand):
    help = 'Re-sync FeederInterruption records from DataNest for a date range'

    def add_arguments(self, parser):
        today = date.today()
        parser.add_argument(
            '--start',
            type=str,
            default=today.replace(day=1).isoformat(),
            help='Start date YYYY-MM-DD (default: first day of current month)',
        )
        parser.add_argument(
            '--end',
            type=str,
            default=today.isoformat(),
            help='End date YYYY-MM-DD (default: today)',
        )
        parser.add_argument(
            '--skip-delete',
            action='store_true',
            help='Skip the stale-record deletion pass (pass 3)',
        )

    def handle(self, *args, **options):
        try:
            window_start = timezone.make_aware(
                datetime.strptime(options['start'], '%Y-%m-%d')
            )
            window_end = timezone.make_aware(
                datetime.strptime(options['end'], '%Y-%m-%d').replace(
                    hour=23, minute=59, second=59
                )
            )
        except ValueError as exc:
            raise CommandError(f'Invalid date format: {exc}')

        self.stdout.write(f'Re-syncing interruptions: {window_start.date()} -> {window_end.date()}')

        feeder_map = {f.slug: f for f in Feeder.objects.all()}
        self.stdout.write(f'Feeders loaded: {len(feeder_map)}')

        normaliser = FaultNormalizer()

        stats = {
            'window_start': window_start,
            'window_end': window_end,
            'records_fetched': 0,
            'records_created': 0,
            'records_updated': 0,
            'records_skipped': 0,
            'records_deleted': 0,
            'records_errored': 0,
            'errors': [],
        }

        datanest_keys = _pass1_new_faults(stats, feeder_map, window_start, window_end, normaliser)
        _pass2_resolve_open(stats, feeder_map, normaliser)

        if not options['skip_delete']:
            _pass3_delete_stale(stats, window_start, window_end, datanest_keys)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Done.'))
        self.stdout.write(f'  Fetched:  {stats["records_fetched"]:,}')
        self.stdout.write(f'  Created:  {stats["records_created"]:,}')
        self.stdout.write(f'  Updated:  {stats["records_updated"]:,}')
        self.stdout.write(f'  Skipped:  {stats["records_skipped"]:,}')
        self.stdout.write(f'  Deleted:  {stats["records_deleted"]:,}')
        self.stdout.write(f'  Errors:   {stats["records_errored"]:,}')

        if stats['errors']:
            self.stdout.write(self.style.WARNING('Warnings/Errors:'))
            for e in stats['errors']:
                self.stdout.write(f'  - {e}')

        if normaliser.unrecognised:
            self.stdout.write(self.style.WARNING(
                f'Unrecognised fault codes: {", ".join(sorted(normaliser.unrecognised))}'
            ))
