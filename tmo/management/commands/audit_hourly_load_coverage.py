"""
tmo/management/commands/audit_hourly_load_coverage.py

Full-month resync (33kV + 11kV Load Flow sheets) plus a coverage audit that
catches the whole class of "feeder silently gets zero data every sync" bugs
without needing to notice it feeder-by-feeder first — exactly the kind of
gap that hid GAGARAWA's real data for as long as it did (a skip-list typo
excluded it from every single sync, with nothing ever flagging that a real,
onboarded feeder was getting zero data).

What it checks, for every onboarded 33kV/11kV feeder:
  1. Zero HourlyLoad coverage for the ENTIRE requested period — the
     strongest signal something is wrong (a real feeder with a full outage
     for a whole month is implausible; far more likely a name-matching or
     skip-list bug is silently discarding its rows before they ever reach
     the database).
  2. Whether the feeder's own name (or a prefix of it) appears in
     tmo.sheet_sync.SKIP_PREFIXES — this is the exact GAGARAWA bug pattern:
     a real feeder accidentally caught by a skip rule meant for summary/
     substation-total rows. Flagged explicitly since it's directly
     actionable (fix the skip pattern), not just "something looks off."

Usage:
    python manage.py audit_hourly_load_coverage --month 2026-08
    python manage.py audit_hourly_load_coverage --month 2026-08 --no-sync   (audit only, skip the resync)
"""
from django.core.management.base import BaseCommand, CommandError

from tmo.models import GoogleSheetFeed


class Command(BaseCommand):
    help = 'Full-month 33kV/11kV Load Flow resync + zero-coverage audit'

    def add_arguments(self, parser):
        parser.add_argument('--month', type=str, required=True, help='YYYY-MM')
        parser.add_argument('--no-sync', action='store_true', help='Skip the resync, audit existing data only')

    def handle(self, *args, **options):
        try:
            year, month = (int(x) for x in options['month'].split('-'))
        except ValueError:
            raise CommandError('--month must be YYYY-MM, e.g. 2026-08')

        if not options['no_sync']:
            self._run_resync(year, month)

        self.stdout.write('')
        self._audit_coverage(year, month, '33kv')
        self.stdout.write('')
        self._audit_coverage(year, month, '11kv')

    def _run_resync(self, year, month):
        from tmo.sheet_sync import sync_33kv_sheet, sync_11kv_sheet

        feed33 = GoogleSheetFeed.objects.filter(
            feed_type='33kv_load_flow', year=year, month=month, is_active=True
        ).first()
        if feed33:
            self.stdout.write(f'Syncing 33kV Load Flow {year}-{month:02d} (force=True, full month)...')
            r = sync_33kv_sheet(spreadsheet_id=feed33.spreadsheet_id, year=year, month=month, force=True, dry_run=False)
            self.stdout.write(f'  {r}')
        else:
            self.stdout.write(self.style.WARNING(f'No active 33kv_load_flow feed for {year}-{month:02d}'))

        feed11 = GoogleSheetFeed.objects.filter(
            feed_type='11kv_load_flow', year=year, month=month, is_active=True
        ).first()
        if feed11:
            self.stdout.write(f'Syncing 11kV Load Flow {year}-{month:02d} (force=True, full month)...')
            r = sync_11kv_sheet(spreadsheet_id=feed11.spreadsheet_id, year=year, month=month, force=True, dry_run=False)
            self.stdout.write(f'  {r}')
        else:
            self.stdout.write(self.style.WARNING(f'No active 11kv_load_flow feed for {year}-{month:02d}'))

    def _audit_coverage(self, year, month, voltage_level):
        from tmo.sheet_sync import audit_hourly_load_coverage

        result = audit_hourly_load_coverage(year, month, voltage_level)
        zero_coverage = result['zero_coverage']

        self.stdout.write(f'=== {voltage_level.upper()} — {year}-{month:02d} coverage audit ===')
        self.stdout.write(f'{result["total"]} onboarded feeders, {len(zero_coverage)} with ZERO real (>0) load all month')

        if not zero_coverage:
            self.stdout.write(self.style.SUCCESS('  All feeders have at least some real data this month.'))
            return

        for f in zero_coverage:
            if f['skip_hit']:
                self.stdout.write(self.style.ERROR(
                    f'  {f["name"]} ({f["slug"]}) — LIKELY SKIP-LIST BUG: matches SKIP_PREFIXES entry "{f["skip_hit"]}" '
                    f'(the exact GAGARAWA pattern — check tmo/sheet_sync.py SKIP_PREFIXES)'
                ))
            else:
                self.stdout.write(self.style.WARNING(
                    f'  {f["name"]} ({f["slug"]}) — zero coverage all month, no obvious skip-list match; '
                    f'check name-matching (NAME_MAP / _find_feeder) or whether it\'s genuinely absent from the sheet'
                ))
