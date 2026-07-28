# technical/management/commands/sync_11kv_sheet.py
"""
Management command: sync_11kv_sheet

Syncs the active 11KV load-flow Google Sheet for a given month into HourlyLoad
and DailyHoursOfSupply.

Only feeders that do NOT already have a DataNest (dso) submission for each
feeder+date+hour slot are written — DataNest data is never overwritten.
Both 11KV and 33KV feeder rows that appear in the sheet are processed.

Usage:
    python manage.py sync_11kv_sheet --month 2026-07
    python manage.py sync_11kv_sheet --month 2026-07 --force
    python manage.py sync_11kv_sheet --month 2026-07 --day 15
    python manage.py sync_11kv_sheet --month 2026-07 --dry-run
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tmo.models import GoogleSheetFeed
from tmo.sheet_sync import sync_11kv_sheet


class Command(BaseCommand):
    help = 'Sync 11KV load-flow Google Sheet → HourlyLoad + DailyHoursOfSupply'

    def add_arguments(self, parser):
        parser.add_argument('--month',   type=str, required=True, help='YYYY-MM')
        parser.add_argument('--force',   action='store_true',
                            help='Re-sync all days even if data already exists')
        parser.add_argument('--day',     type=int, default=None,
                            help='Sync only a specific day (1-31)')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        try:
            year, month = (int(x) for x in options['month'].split('-'))
            date(year, month, 1)
        except (ValueError, TypeError):
            raise CommandError('--month must be YYYY-MM, e.g. 2026-07')

        feed = GoogleSheetFeed.objects.filter(
            feed_type='11kv_load_flow', year=year, month=month, is_active=True
        ).first()

        if not feed:
            raise CommandError(
                f'No active 11KV Load Flow feed registered for {year}-{month:02d}. '
                f'POST to /api/tmo/sheet-feeds/ with feed_type=11kv_load_flow to register it.'
            )

        self.stdout.write(f'Feed: {feed} | spreadsheet_id={feed.spreadsheet_id}')
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('DRY-RUN — nothing will be written'))

        def log_fn(msg):
            self.stdout.write(msg)

        result = sync_11kv_sheet(
            spreadsheet_id=feed.spreadsheet_id,
            year=year,
            month=month,
            force=options['force'],
            dry_run=options['dry_run'],
            only_day=options['day'],
            log_fn=log_fn,
        )

        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS(f"Days synced : {result['days_synced']}"))
        self.stdout.write(self.style.SUCCESS(f"HourlyLoad  : {result['hl_rows']} rows"))

        if result['unmatched']:
            self.stdout.write(self.style.WARNING(f"\nUnmatched feeders ({len(result['unmatched'])})"))
            for n in result['unmatched']:
                self.stdout.write(self.style.WARNING(f'  ! {n}'))

        if result['errors']:
            self.stdout.write(self.style.ERROR(f"\nErrors ({len(result['errors'])})"))
            for e in result['errors']:
                self.stdout.write(self.style.ERROR(f'  x {e}'))

        if not options['dry_run']:
            feed.last_synced_at = timezone.now()
            feed.last_sync_log  = str(result)
            feed.save(update_fields=['last_synced_at', 'last_sync_log'])
