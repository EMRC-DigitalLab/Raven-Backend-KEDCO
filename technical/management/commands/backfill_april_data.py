"""
technical/management/commands/backfill_april_data.py

Backfill hourly load AND energy (meter) readings for a date range directly
from the external DataNest MySQL database into the local Raven database.

Defaults to April 1 – April 30, 2026.  The exact range can be overridden
with --start-date / --end-date.

The command reuses the exact same sync logic as the Celery tasks (same
_to_decimal_or_zero(), same feeder_map lookup, same bulk create/update/delete
passes) — it just bypasses get_sync_window() and uses the supplied dates
instead.  Nothing new is introduced; nothing existing is changed.

Usage:
    # Dry-run to see what would be pulled:
    python manage.py backfill_april_data --dry-run

    # Run for the full April window (default):
    python manage.py backfill_april_data

    # Custom range:
    python manage.py backfill_april_data --start-date 2026-04-01 --end-date 2026-04-15

    # Hourly load only:
    python manage.py backfill_april_data --hourly-only

    # Energy readings only:
    python manage.py backfill_april_data --energy-only

Processing is chunked in 7-day slices so the command stays memory-safe even
for a full month of high-frequency hourly data.
"""

import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

CHUNK_DAYS = 7
DEFAULT_START = datetime.date(2026, 4, 1)
DEFAULT_END   = datetime.date(2026, 4, 30)


class Command(BaseCommand):
    help = 'Backfill hourly load and energy readings for April 2026 from DataNest.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--start-date', default=None,
            help='Start date YYYY-MM-DD (default: 2026-04-01)',
        )
        parser.add_argument(
            '--end-date', default=None,
            help='End date YYYY-MM-DD (default: 2026-04-30)',
        )
        parser.add_argument(
            '--hourly-only', action='store_true',
            help='Only sync hourly load — skip energy readings.',
        )
        parser.add_argument(
            '--energy-only', action='store_true',
            help='Only sync energy readings — skip hourly load.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print what would be synced without writing anything.',
        )

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        start_date = self._parse_date(options['start_date'], DEFAULT_START, '--start-date')
        end_date   = self._parse_date(options['end_date'],   DEFAULT_END,   '--end-date')

        if end_date < start_date:
            raise CommandError('--end-date must be on or after --start-date')

        do_hourly = not options['energy_only']
        do_energy = not options['hourly_only']
        dry_run   = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'[DRY RUN] Would pull {start_date} → {end_date} '
                f'(hourly={do_hourly}, energy={do_energy})'
            ))

        self.stdout.write(
            f'\nBackfill range : {start_date} → {end_date}'
            f'\nHourly load    : {"YES" if do_hourly else "skip"}'
            f'\nEnergy readings: {"YES" if do_energy else "skip"}'
            f'\nDry run        : {"YES — no writes" if dry_run else "NO — will write"}\n'
        )

        # ── Build 7-day chunks ────────────────────────────────────────────────
        chunks = []
        chunk_start = start_date
        while chunk_start <= end_date:
            chunk_end = min(chunk_start + datetime.timedelta(days=CHUNK_DAYS - 1), end_date)
            chunks.append((chunk_start, chunk_end))
            chunk_start = chunk_end + datetime.timedelta(days=1)

        total_stats = {
            'hourly': {'fetched': 0, 'created': 0, 'updated': 0, 'skipped': 0, 'deleted': 0, 'errored': 0},
            'energy': {'fetched': 0, 'created': 0, 'updated': 0, 'skipped': 0, 'deleted': 0, 'errored': 0},
        }

        for chunk_start, chunk_end in chunks:
            self.stdout.write(f'  Chunk {chunk_start} → {chunk_end} …')

            if do_hourly:
                self._sync_hourly(chunk_start, chunk_end, dry_run, total_stats['hourly'])

            if do_energy:
                self._sync_energy(chunk_start, chunk_end, dry_run, total_stats['energy'])

        # ── Final summary ─────────────────────────────────────────────────────
        self.stdout.write('\n' + '─' * 60)
        self.stdout.write(self.style.SUCCESS('BACKFILL COMPLETE'))
        self.stdout.write('─' * 60)

        if do_hourly:
            h = total_stats['hourly']
            self.stdout.write(
                f'Hourly load   — fetched:{h["fetched"]:,}  '
                f'created:{h["created"]:,}  updated:{h["updated"]:,}  '
                f'skipped:{h["skipped"]:,}  deleted:{h["deleted"]:,}  '
                f'errored:{h["errored"]:,}'
            )

        if do_energy:
            e = total_stats['energy']
            self.stdout.write(
                f'Energy rdg    — fetched:{e["fetched"]:,}  '
                f'created:{e["created"]:,}  updated:{e["updated"]:,}  '
                f'skipped:{e["skipped"]:,}  deleted:{e["deleted"]:,}  '
                f'errored:{e["errored"]:,}'
            )

        if dry_run:
            self.stdout.write(self.style.WARNING('\nDry run — no data was written.'))

    # ------------------------------------------------------------------
    def _sync_hourly(self, start, end, dry_run, totals):
        from technical.sync.hourly_load import run_sync
        if dry_run:
            self.stdout.write(f'    [DRY RUN] hourly load {start} → {end} (no write)')
            return
        try:
            stats = run_sync(override_start=start, override_end=end)
            self._accumulate(totals, stats)
            self.stdout.write(
                f'    hourly load  created={stats["records_created"]:,} '
                f'updated={stats["records_updated"]:,} '
                f'deleted={stats["records_deleted"]:,} '
                f'errored={stats["records_errored"]:,}'
            )
            for err in stats.get('errors', []):
                self.stderr.write(f'    WARN (hourly): {err}')
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'    ERROR (hourly {start}→{end}): {exc}'))

    def _sync_energy(self, start, end, dry_run, totals):
        from technical.sync.meter_readings import run_sync
        if dry_run:
            self.stdout.write(f'    [DRY RUN] energy readings {start} → {end} (no write)')
            return
        try:
            stats = run_sync(override_start=start, override_end=end)
            self._accumulate(totals, stats)
            self.stdout.write(
                f'    energy rdg   created={stats["records_created"]:,} '
                f'updated={stats["records_updated"]:,} '
                f'deleted={stats["records_deleted"]:,} '
                f'errored={stats["records_errored"]:,}'
            )
            for err in stats.get('errors', []):
                self.stderr.write(f'    WARN (energy): {err}')
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'    ERROR (energy {start}→{end}): {exc}'))

    # ------------------------------------------------------------------
    @staticmethod
    def _accumulate(totals, stats):
        totals['fetched']  += stats.get('records_fetched', 0)
        totals['created']  += stats.get('records_created', 0)
        totals['updated']  += stats.get('records_updated', 0)
        totals['skipped']  += stats.get('records_skipped', 0)
        totals['deleted']  += stats.get('records_deleted', 0)
        totals['errored']  += stats.get('records_errored', 0)

    @staticmethod
    def _parse_date(value, default, flag_name):
        if value is None:
            return default
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            raise CommandError(f'{flag_name} must be YYYY-MM-DD, got: {value!r}')
