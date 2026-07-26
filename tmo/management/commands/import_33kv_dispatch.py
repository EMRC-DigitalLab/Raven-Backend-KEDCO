# -*- coding: utf-8 -*-
"""
Management command: import_33kv_dispatch

Imports daily 33KV network dispatch data from the TCN monthly load-flow Excel
into TMONetworkDispatch (daily summary) + TMONetworkDispatchHourly (24-point detail).

Source rows (0-indexed) in each day sheet:
  row 90: TOTAL ENERGY OFFTAKE PER HOUR  → disco_offtake_mw
  row 91: AVAILABLE GENERATION PER HOUR  → available_generation_mw
  row 94: KEDCO ALLOCATION PER HOUR      → kedco_allocation_mw
  variance = kedco_allocation − disco_offtake
    positive → GREEN (KEDCO over-took)
    negative → RED   (KEDCO under-took / loss)

Hour columns: 2–25 (01:00 → 24:00). Daily value = avg of available hourly readings.

Usage:
    python manage.py import_33kv_dispatch --file /path/file.xlsx --month 2026-06
    python manage.py import_33kv_dispatch --file /path/file.xlsx --month 2026-06 --dry-run
"""

import math
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from tmo.models import TMONetworkDispatch, TMONetworkDispatchHourly

HOUR_COLS = list(range(2, 26))   # cols 2-25 = 01:00 → 24:00

# Row keywords (matched against col 0, case-insensitive, uppercase)
ROW_KEYS = {
    'disco':  'TOTAL ENERGY OFFTAKE PER HOUR',
    'alloc':  'KEDCO ALLOCATION PER HOUR',
    'avail':  'AVAILABLE GENERATION PER HOUR',
}


def _safe_float(val):
    try:
        f = float(str(val).strip().replace(',', ''))
        return None if math.isnan(f) else f
    except (ValueError, TypeError):
        return None


def _row_hourly(df, row_idx):
    """Return list of 24 values (None where missing/non-numeric) for cols 2-25."""
    return [
        _safe_float(df.iloc[row_idx, c]) if c < df.shape[1] else None
        for c in HOUR_COLS
    ]


def _row_avg(vals):
    clean = [v for v in vals if v is not None]
    return sum(clean) / len(clean) if clean else None


def _find_row(df, keyword):
    kw = keyword.strip().upper()
    for r in range(df.shape[0]):
        label = str(df.iloc[r, 0]).strip().upper()
        if kw in label:
            return r
    return None


def _sheet_day(name):
    m = re.match(r'^(\d+)', str(name).strip())
    return int(m.group(1)) if m else None


class Command(BaseCommand):
    help = 'Import daily 33KV dispatch reconciliation from TCN load-flow Excel → TMONetworkDispatch + TMONetworkDispatchHourly'

    def add_arguments(self, parser):
        parser.add_argument('--file',  required=True, type=str)
        parser.add_argument('--month', required=True, type=str, help='YYYY-MM')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        file_path = Path(options['file'])
        if not file_path.is_file():
            raise CommandError(f'File not found: {file_path}')

        try:
            year, month = (int(x) for x in options['month'].split('-'))
            date(year, month, 1)
        except (ValueError, TypeError):
            raise CommandError('--month must be YYYY-MM')

        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN — nothing will be written'))

        xl = pd.ExcelFile(file_path, engine='openpyxl')

        created = updated = skipped = 0
        errors = []

        for sheet_name in xl.sheet_names:
            day = _sheet_day(sheet_name)
            if not day:
                continue
            try:
                reading_date = date(year, month, day)
            except ValueError:
                continue

            df = xl.parse(sheet_name, header=None)
            if df.shape[1] < 26:
                errors.append(f'{reading_date}: only {df.shape[1]} cols — skipped')
                continue

            r_disco = _find_row(df, ROW_KEYS['disco'])
            r_alloc = _find_row(df, ROW_KEYS['alloc'])
            r_avail = _find_row(df, ROW_KEYS['avail'])

            if r_disco is None or r_alloc is None:
                errors.append(f'{reading_date}: could not find required rows — skipped')
                continue

            disco_vals = _row_hourly(df, r_disco)
            alloc_vals = _row_hourly(df, r_alloc)
            avail_vals = _row_hourly(df, r_avail) if r_avail is not None else [None] * 24

            disco_avg = _row_avg(disco_vals)
            alloc_avg = _row_avg(alloc_vals)
            avail_avg = _row_avg(avail_vals) or 0.0

            if disco_avg is None or alloc_avg is None:
                errors.append(f'{reading_date}: blank data — skipped')
                continue

            variance = alloc_avg - disco_avg
            status = 'GREEN' if variance >= 0 else 'RED'

            if dry_run:
                self.stdout.write(
                    f'  [DRY] {reading_date}: alloc={alloc_avg:.2f} MW  '
                    f'disco={disco_avg:.2f} MW  var={variance:+.2f} MW  {status}'
                )
                created += 1
                continue

            try:
                with transaction.atomic():
                    dispatch_obj, was_created = TMONetworkDispatch.objects.update_or_create(
                        date=reading_date,
                        defaults={
                            'kedco_allocation_mw':     Decimal(str(round(alloc_avg, 4))),
                            'disco_offtake_mw':        Decimal(str(round(disco_avg, 4))),
                            'variance_mw':             Decimal(str(round(variance, 4))),
                            'available_generation_mw': Decimal(str(round(avail_avg, 4))),
                            'source': 'manual',
                            'notes': f'Imported from TCN 33KV load-flow Excel ({options["month"]})',
                        },
                    )

                    # Rebuild hourly rows for this day
                    TMONetworkDispatchHourly.objects.filter(date=reading_date).delete()
                    hourly_objs = []
                    for h_idx, hour_num in enumerate(range(1, 25)):
                        a = alloc_vals[h_idx]
                        d = disco_vals[h_idx]
                        av = avail_vals[h_idx] if avail_vals else None
                        var_h = (a - d) if (a is not None and d is not None) else None
                        hourly_objs.append(TMONetworkDispatchHourly(
                            dispatch=dispatch_obj,
                            date=reading_date,
                            hour=hour_num,
                            kedco_allocation_mw=Decimal(str(round(a, 4))) if a is not None else None,
                            disco_offtake_mw=Decimal(str(round(d, 4))) if d is not None else None,
                            variance_mw=Decimal(str(round(var_h, 4))) if var_h is not None else None,
                            available_generation_mw=Decimal(str(round(av, 4))) if av is not None else None,
                        ))
                    TMONetworkDispatchHourly.objects.bulk_create(hourly_objs)

                if was_created:
                    created += 1
                else:
                    updated += 1
                self.stdout.write(
                    f'  {reading_date}: alloc={alloc_avg:.2f} MW  '
                    f'disco={disco_avg:.2f} MW  var={variance:+.2f} MW  {status}  '
                    f'({len(hourly_objs)} hourly rows)'
                )
            except Exception as exc:
                errors.append(f'{reading_date}: DB error — {exc}')

        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS(f'Created : {created}'))
        self.stdout.write(self.style.SUCCESS(f'Updated : {updated}'))
        if skipped:
            self.stdout.write(self.style.WARNING(f'Skipped : {skipped}'))
        if errors:
            self.stdout.write(self.style.ERROR(f'\nErrors ({len(errors)}):'))
            for e in errors:
                self.stdout.write(self.style.ERROR(f'  x {e}'))
        if dry_run:
            self.stdout.write(self.style.NOTICE('--- DRY-RUN COMPLETE ---'))
