# technical/management/commands/backfill_tcn_interruptions.py
"""
One-off backfill: TCN's own 33kV Feeder Reliability fault log -> FeederInterruption
(source='tcn'). DSO/DataNest never covers 33kV (confirmed zero 33kV rows in
30 days of live data) — this is the only source Raven has for 33kV
interruptions at all.

Parsing/matching logic lives in technical/sync/tcn_interruptions.py, shared
with the ongoing hourly sync task (technical.tasks.sync_tcn_interruptions_task)
that took over from this command for anything going forward — this command
now only exists to (re-)run the full historical range on demand.

Usage:
    python manage.py backfill_tcn_interruptions --dry-run
    python manage.py backfill_tcn_interruptions --months "Jan 2026,Feb 2026"
    python manage.py backfill_tcn_interruptions
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from technical.models import FeederInterruption
from technical.sync.tcn_interruptions import (
    SPREADSHEET_ID, _MONTH_NUM, _build_feeder_lookup, _fetch_tab_rows, parse_row,
)

DEFAULT_MONTHS = [
    'Jan 2026', 'Feb 2026', 'Mar 2026', 'Apr 2026',
    'May 2026', 'Jun 2026', 'Jul 2026', 'Aug 2026',
]


class Command(BaseCommand):
    help = "One-off backfill of TCN's 33kV fault log into FeederInterruption (source='tcn')."

    def add_arguments(self, parser):
        parser.add_argument('--months', type=str, default=None,
                             help='Comma-separated tab names, e.g. "Jan 2026,Feb 2026". Default: Jan-Aug 2026.')
        parser.add_argument('--dry-run', action='store_true', help='Parse and report only, write nothing.')

    def handle(self, *args, **options):
        from tmo.sheet_sync import open_spreadsheet

        months = [m.strip() for m in options['months'].split(',')] if options['months'] else DEFAULT_MONTHS
        dry_run = options['dry_run']

        sh = open_spreadsheet(SPREADSHEET_ID)
        lookup = _build_feeder_lookup()

        totals = {'rows': 0, 'created': 0, 'skipped_existing': 0, 'unmatched_feeder': set(), 'errors': 0}

        for month_tab in months:
            expected_month = _MONTH_NUM[month_tab.split()[0][:3]]

            rows = _fetch_tab_rows(sh, month_tab)
            if not rows:
                self.stdout.write(self.style.ERROR(f'{month_tab}: could not fetch tab'))
                continue
            header, data_rows = rows[0], rows[1:]
            self.stdout.write(f'{month_tab}: {len(data_rows)} rows')

            to_create = []
            for row in data_rows:
                totals['rows'] += 1
                fields, reason = parse_row(row, expected_month, lookup)
                if fields is None:
                    if reason and reason.startswith('unmatched_feeder:'):
                        totals['unmatched_feeder'].add(reason.split(':', 1)[1])
                    elif reason == 'bad_date':
                        totals['errors'] += 1
                    continue
                to_create.append(FeederInterruption(**fields))

            if dry_run:
                self.stdout.write(f'  {month_tab}: would create {len(to_create)} rows (dry-run)')
                continue

            # Bulk dedup + bulk_create: avoids get_or_create()'s one-query-per-row
            # cost AND avoids firing the FeederInterruption post_save signal
            # (notifications.signals.on_feeder_interruption), which would otherwise
            # fire a "new fault" alert to admins for every one of ~18k
            # months-old historical rows. bulk_create() never calls save(), so
            # post_save never fires for these rows -- correct for a one-off
            # historical backfill (the ongoing hourly sync task still uses
            # get_or_create() and still notifies as normal for genuinely new rows).
            feeder_ids = {obj.feeder_id for obj in to_create}
            existing_keys = set(
                FeederInterruption.objects.filter(feeder_id__in=feeder_ids).values_list(
                    'feeder_id', 'occurred_at', 'interruption_type'
                )
            )
            new_objs = []
            seen_in_batch = set()
            for obj in to_create:
                key = (obj.feeder_id, obj.occurred_at, obj.interruption_type)
                if key in existing_keys or key in seen_in_batch:
                    continue
                seen_in_batch.add(key)
                new_objs.append(obj)
            totals['skipped_existing'] += len(to_create) - len(new_objs)

            try:
                with transaction.atomic():
                    created_objs = FeederInterruption.objects.bulk_create(new_objs, batch_size=500)
                totals['created'] += len(created_objs)
            except Exception as exc:
                totals['errors'] += len(new_objs)
                self.stdout.write(self.style.ERROR(f'  {month_tab}: bulk_create failed: {exc}'))

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. rows_seen={totals['rows']} created={totals['created']} "
            f"skipped_existing={totals['skipped_existing']} errors={totals['errors']} "
            f"unmatched_feeders={len(totals['unmatched_feeder'])}"
        ))
        if totals['unmatched_feeder']:
            self.stdout.write(self.style.WARNING('Unmatched feeder names: ' + ', '.join(sorted(totals['unmatched_feeder']))))
