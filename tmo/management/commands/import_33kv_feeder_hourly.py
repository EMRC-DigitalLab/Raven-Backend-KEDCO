# -*- coding: utf-8 -*-
"""
Management command: import_33kv_feeder_hourly

Imports per-feeder hourly MW readings from the TCN 33KV monthly load-flow Excel into:
  - HourlyLoad           (feeder + date + hour + load_mw)
  - DailyHoursOfSupply   (feeder + date + hours_supplied = count of hours with load_mw > 0)
  - FaultTypeCategory    (auto-inserts any new fault text codes as category='disco')

Source sheet structure (each sheet = one day):
  Row 1   : header (col 0="33KV Feeders", cols 2-25 = "01:00:00"..."24:00:00")
  Rows 3-86: individual 33KV feeders — col 0=feeder name, cols 2-25=MW or fault text
  Row 87+  : aggregate rows (KEDCO TOTAL, DISCO OFFTAKE, etc.) — skipped

Hour mapping: col 2 (01:00) → hour=0, col 3 (02:00) → hour=1, ..., col 25 (24:00) → hour=23
  (hour=0 represents the 00:00–01:00 interval, matching DataNest HourlyLoad convention)

Fault codes: any non-numeric cell value is treated as a fault code.
  - load_mw = 0 for that hour
  - code auto-inserted into FaultTypeCategory (category='disco') if not already present

Usage:
    python manage.py import_33kv_feeder_hourly --file /path/file.xlsx --month 2026-06
    python manage.py import_33kv_feeder_hourly --file /path/file.xlsx --month 2026-06 --dry-run
"""

import math
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from common.models import Feeder
from technical.models import DailyHoursOfSupply, FaultTypeCategory, HourlyLoad

HOUR_COLS = list(range(2, 26))   # cols 2-25 → hours 0-23

# Rows to always skip regardless of col-0 content
SKIP_LABELS = {
    'ON SOAK', 'KEDCO TOTAL', 'ENERGY OFFTAKE', 'TOTAL ENERGY',
    'AVAILABLE GENERATION', 'DISCO ALLOCATION', 'KEDCO ALLOCATION',
    'VARIANCE', 'NUMBER OF', 'REMARKS', '33KV FEEDERS',
}

# Manual name corrections: Excel name (uppercase) → DB feeder name fragment (uppercase)
NAME_MAP = {
    "SMALL SCALE":            "SMALL SCALE",
    "DAN'AGUNDI 1":           "DAN AGUNDI 1",
    "DAN'AGUNDI 2":           "DAN AGUNDI 2",
    "MAI'ADU'A":              "MAI ADUA",
    "RICE FIELD (FEEDER 3)":  "RICE FIELD",
    "HON. ABUBAKAR KABIR":    "HON. ABUBAKAR",
    "COCA - COLA":            "COCA COLA",
    "N B CERAMICS":           "NB CERAMIC",
    "MR JIMETA":              "JIMETA",
    "R/ZAKI":                 "RIJIYAR ZAKI",
    "RUMMAWA":                "RUMAWA",
    "POLY":                   "POLYTECHNIC",
}


def _categorize_fault(code):
    """Auto-classify a fault code into ls / tcn / disco."""
    up = code.strip().upper()
    # Load shedding variants
    if any(k in up for k in ('LS/GS', 'LSGS', 'LS/GW', 'L/SHED', 'LS/T', 'L/S GS')):
        return 'ls'
    # TCN / grid / line-limit variants
    if any(k in up for k in (
        'TCN', '330KV', 'FREQ/C', 'U/F',
        'L/LIM', 'LIN/LIM', 'LINE LIM', 'LINE/LIM', 'LI/LIM',
        'L/LM', 'L/IM', 'L/LIN', 'TR/LIM', 'LIN LIM', 'L/LIMM',
        'LINE/LIME', 'L\\LIM', 'L/LIM$', 'L\\LIM', 'LINE LIM',
    )):
        return 'tcn'
    return 'disco'


def _safe_float(val):
    try:
        f = float(str(val).strip().replace(',', ''))
        return None if math.isnan(f) else f
    except (ValueError, TypeError):
        return None


def _is_skip(label):
    up = label.strip().upper()
    return not up or up == 'NAN' or any(k in up for k in SKIP_LABELS)


def _sheet_day(name):
    m = re.match(r'^(\d+)', str(name).strip())
    return int(m.group(1)) if m else None


def _build_feeder_lookup():
    """Return dict: normalized_key → Feeder object, for all 33KV feeders."""
    lookup = {}
    for f in Feeder.objects.filter(voltage_level='33kv'):
        # Key: strip '33KV ' prefix, uppercase, collapse spaces
        key = re.sub(r'\s+', ' ', f.name.upper().replace('33KV', '').strip())
        lookup[key] = f
    return lookup


def _find_feeder(label, lookup):
    """Try to match an Excel row label to a DB feeder."""
    raw = label.strip().upper()
    # Apply manual map first
    mapped = NAME_MAP.get(raw, raw)
    mapped_clean = re.sub(r'\s+', ' ', mapped.strip())
    # Exact key match
    if mapped_clean in lookup:
        return lookup[mapped_clean]
    # Partial: find feeder whose key contains the mapped label (or vice versa)
    for key, feeder in lookup.items():
        if mapped_clean in key or key in mapped_clean:
            return feeder
    return None


class Command(BaseCommand):
    help = 'Import per-feeder hourly MW from TCN 33KV load-flow Excel → HourlyLoad + DailyHoursOfSupply'

    def add_arguments(self, parser):
        parser.add_argument('--file',    required=True, type=str)
        parser.add_argument('--month',   required=True, type=str, help='YYYY-MM')
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

        feeder_lookup = _build_feeder_lookup()
        self.stdout.write(f'Loaded {len(feeder_lookup)} 33KV feeders from DB')

        xl = pd.ExcelFile(file_path, engine='openpyxl')

        # Collect all new fault codes across all sheets, insert once at the end
        new_fault_codes = {}   # code → True

        days_written      = 0
        total_hl_rows     = 0
        total_dhos_rows   = 0
        unmatched_overall = set()
        errors            = []

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

            hl_objs        = []   # HourlyLoad to bulk-create for this day
            dhos_map       = {}   # feeder_id → hours_with_supply count
            unmatched_day  = []

            for r in range(3, min(87, df.shape[0])):
                label = str(df.iloc[r, 0]).strip()
                if _is_skip(label):
                    continue

                feeder = _find_feeder(label, feeder_lookup)
                if feeder is None:
                    unmatched_day.append(label)
                    unmatched_overall.add(label)
                    continue

                hours_with_supply = 0
                for h_idx, col in enumerate(HOUR_COLS):
                    if col >= df.shape[1]:
                        continue
                    raw_val = df.iloc[r, col]
                    mw = _safe_float(raw_val)

                    if mw is None:
                        # Text fault code
                        code = str(raw_val).strip().upper()
                        if code and code != 'NAN':
                            new_fault_codes[code] = True
                        load_mw = Decimal('0.00')
                    else:
                        load_mw = Decimal(str(round(mw, 2)))
                        if mw > 0:
                            hours_with_supply += 1

                    hl_objs.append(HourlyLoad(
                        feeder=feeder,
                        date=reading_date,
                        hour=h_idx,          # col2→0, col3→1, ..., col25→23
                        load_mw=load_mw,
                        submission_type='admin_override',
                    ))

                dhos_map[feeder.id] = (feeder, hours_with_supply)

            if dry_run:
                self.stdout.write(
                    f'  [DRY] {reading_date}: {len(dhos_map)} feeders, '
                    f'{len(hl_objs)} hourly rows | unmatched={unmatched_day}'
                )
                days_written += 1
                continue

            try:
                with transaction.atomic():
                    # Clear existing HourlyLoad rows for this date's 33KV feeders
                    feeder_ids_today = [f.id for f, _ in dhos_map.values()]
                    HourlyLoad.objects.filter(
                        date=reading_date, feeder_id__in=feeder_ids_today
                    ).delete()

                    HourlyLoad.objects.bulk_create(hl_objs, batch_size=500)
                    total_hl_rows += len(hl_objs)

                    # Upsert DailyHoursOfSupply
                    for feeder, hrs in dhos_map.values():
                        DailyHoursOfSupply.objects.update_or_create(
                            feeder=feeder,
                            date=reading_date,
                            defaults={'hours_supplied': Decimal(str(hrs))},
                        )
                    total_dhos_rows += len(dhos_map)

                days_written += 1
                self.stdout.write(
                    f'  {reading_date}: {len(dhos_map)} feeders  '
                    f'{len(hl_objs)} HourlyLoad rows  '
                    f'{len(dhos_map)} DailyHoursOfSupply rows'
                    + (f'  | unmatched: {unmatched_day}' if unmatched_day else '')
                )
            except Exception as exc:
                errors.append(f'{reading_date}: DB error — {exc}')

        # Insert new fault codes into FaultTypeCategory
        if new_fault_codes and not dry_run:
            existing = set(FaultTypeCategory.objects.values_list('code', flat=True))
            inserted = []
            for code in new_fault_codes:
                if code not in existing:
                    FaultTypeCategory.objects.create(
                        code=code,
                        label=code,
                        category=_categorize_fault(code),
                    )
                    inserted.append(code)
            if inserted:
                self.stdout.write(self.style.WARNING(
                    f'New fault codes inserted into FaultTypeCategory: {inserted}'
                ))

        # Summary
        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS(f'Days processed   : {days_written}'))
        self.stdout.write(self.style.SUCCESS(f'HourlyLoad rows  : {total_hl_rows}'))
        self.stdout.write(self.style.SUCCESS(f'DailyHoursOfSupply: {total_dhos_rows}'))
        if unmatched_overall:
            self.stdout.write(self.style.WARNING(
                f'\nUnmatched feeder names ({len(unmatched_overall)}):'
            ))
            for name in sorted(unmatched_overall):
                self.stdout.write(self.style.WARNING(f'  ! {name}'))
        if errors:
            self.stdout.write(self.style.ERROR(f'\nErrors ({len(errors)}):'))
            for e in errors:
                self.stdout.write(self.style.ERROR(f'  x {e}'))
        if dry_run:
            self.stdout.write(self.style.NOTICE('--- DRY-RUN COMPLETE ---'))
