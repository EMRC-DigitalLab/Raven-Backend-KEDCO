"""
energy_account/management/commands/resync_ea_month.py

Force a full re-sync of all EA tables for a specific month/year by overriding
the normal incremental watermark window.  Useful for backfilling after fixes.

Usage:
    python manage.py resync_ea_month               # defaults to March 2026
    python manage.py resync_ea_month --month 3 --year 2026
    python manage.py resync_ea_month --month 2 --year 2026 --env staging
"""

import calendar
from datetime import datetime
from unittest.mock import patch

from django.core.management.base import BaseCommand
from django.utils.timezone import make_aware

from technical.models import DataSyncLog


# Sync modules in FK dependency order (same order as tasks.py comments)
SYNC_MODULES = [
    ('ea_nbet_rates',               'energy_account.sync.nbet_rates'),
    ('ea_settings',                 'energy_account.sync.ea_settings'),
    ('ea_grid_meters',              'energy_account.sync.grid_meters'),
    ('ea_monthly_returns',          'energy_account.sync.monthly_returns'),
    ('ea_monthly_readings',         'energy_account.sync.monthly_readings'),
    ('ea_feeder_technical_energy',  'energy_account.sync.feeder_technical_energy'),
    ('ea_tcn_reconciliation',       'energy_account.sync.tcn_reconciliation'),
    ('ea_tcn_reconciliation_notes', 'energy_account.sync.tcn_reconciliation_notes'),
    ('ea_mo_reconciliation',        'energy_account.sync.mo_reconciliation'),
    ('ea_weekly_readings',          'energy_account.sync.weekly_readings'),
    ('ea_station_assignments',      'energy_account.sync.station_assignments'),
    ('ea_meter_check_schedules',    'energy_account.sync.meter_check_schedules'),
    ('ea_meter_check_records',      'energy_account.sync.meter_check_records'),
    ('ea_coupling_log',             'energy_account.sync.coupling_log'),
]


class Command(BaseCommand):
    help = 'Re-sync all EA tables for a specific month from DataNest into Raven.'

    def add_arguments(self, parser):
        parser.add_argument('--month', type=int, default=3, help='Month number (1-12)')
        parser.add_argument('--year',  type=int, default=2026, help='4-digit year')
        parser.add_argument('--window-start', type=str, default=None,
                            help='Override window start as YYYY-MM-DD (filters on updated_at in DataNest)')
        parser.add_argument('--window-end', type=str, default=None,
                            help='Override window end as YYYY-MM-DD (filters on updated_at in DataNest)')

    def handle(self, *args, **options):
        month = options['month']
        year  = options['year']

        if options['window_start'] and options['window_end']:
            window_start = make_aware(datetime.strptime(options['window_start'], '%Y-%m-%d'))
            window_end   = make_aware(datetime.strptime(options['window_end'],   '%Y-%m-%d').replace(
                hour=23, minute=59, second=59))
        else:
            # Default: billing month window (works when updated_at falls within that month)
            window_start = make_aware(datetime(year, month, 1, 0, 0, 0))
            last_day     = calendar.monthrange(year, month)[1]
            window_end   = make_aware(datetime(year, month, last_day, 23, 59, 59))

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\nRe-syncing EA data for {month:02d}/{year}'
        ))
        self.stdout.write(f'  Window: {window_start}  to  {window_end}\n')

        def fixed_window(_data_type):
            return window_start, window_end, window_start

        totals = {'created': 0, 'updated': 0, 'skipped': 0, 'errored': 0}

        for data_type, module_path in SYNC_MODULES:
            import importlib
            mod = importlib.import_module(module_path)

            # Patch get_sync_window in the sync module's own namespace
            patch_target = f'{module_path}.get_sync_window'

            log = DataSyncLog.objects.create(data_type=data_type, status='running')

            try:
                with patch(patch_target, side_effect=fixed_window):
                    stats = mod.run_sync()

                has_errors = bool(stats.get('errors'))
                status = 'partial' if has_errors else 'success'

                from django.utils import timezone
                log.status          = status
                log.completed_at    = timezone.now()
                log.window_start    = stats.get('window_start')
                log.window_end      = stats.get('window_end')
                log.records_fetched = stats.get('records_fetched', 0)
                log.records_created = stats.get('records_created', 0)
                log.records_updated = stats.get('records_updated', 0)
                log.records_skipped = stats.get('records_skipped', 0)
                log.records_deleted = stats.get('records_deleted', 0)
                log.records_errored = stats.get('records_errored', 0)
                log.error_message   = '\n'.join(stats.get('errors', []))
                log.save()

                c = stats.get('records_created', 0)
                u = stats.get('records_updated', 0)
                s = stats.get('records_skipped', 0)
                e = stats.get('records_errored', 0)
                totals['created']  += c
                totals['updated']  += u
                totals['skipped']  += s
                totals['errored']  += e

                style = self.style.SUCCESS if status == 'success' else self.style.WARNING
                self.stdout.write(style(
                    f'  [{status.upper():7}] {data_type:<35} '
                    f'created={c}  updated={u}  skipped={s}  errored={e}'
                ))
                if has_errors:
                    for err in stats['errors'][:5]:
                        self.stdout.write(self.style.ERROR(f'             {err}'))
                    if len(stats['errors']) > 5:
                        self.stdout.write(f'             … and {len(stats["errors"]) - 5} more errors')

            except Exception as exc:
                from django.utils import timezone
                log.status        = 'error'
                log.completed_at  = timezone.now()
                log.error_message = str(exc)
                log.save()
                self.stdout.write(self.style.ERROR(
                    f'  [ERROR  ] {data_type:<35} FAILED: {exc}'
                ))

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\nDone.  Total: created={totals["created"]}  '
            f'updated={totals["updated"]}  skipped={totals["skipped"]}  '
            f'errored={totals["errored"]}\n'
        ))
