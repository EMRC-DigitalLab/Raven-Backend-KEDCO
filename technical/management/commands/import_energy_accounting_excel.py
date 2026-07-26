# -*- coding: utf-8 -*-
"""
Management command: import_energy_accounting_excel

Imports 33KV feeder daily energy from the monthly energy accounting Excel
(JULY 2026, RECORD AND ACCOUNTING format) into:
  - EnergyDelivered    (feeder + date + energy_mwh = closing − opening meter)
  - DailyHoursOfSupply (feeder + date + hours_supplied = hours meter moved)

NOTE: HourlyLoad is NOT written here — hourly load comes from a separate Excel.

Excel structure (each sheet = one day, named "1st", "2nd", ..., "27th"):
  Row 0: empty
  Row 1: hour labels (01:00 ... 24:00)
  Row 2: column headers
  Row 3+: one feeder per row
    col 0  = REGION
    col 7  = ASSOCIATED 33KV FEEDER (feeder name)
    col 11 = METER READING AT 00:00HRS MWH (opening cumulative)
    col 13/17/.../105 = PRESENT METER READING MWH per hour (closing = col 105)

Usage:
    python manage.py import_energy_accounting_excel --file /path/file.xlsx --month 2026-07
    python manage.py import_energy_accounting_excel --file /path/file.xlsx --month 2026-07 --dry-run
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
from technical.models import DailyHoursOfSupply, EnergyDelivered

# Col index of PRESENT METER READING MWH for each hour (1-24): 13 + (h-1)*4
HOUR_METER_COLS = [13 + (h - 1) * 4 for h in range(1, 25)]   # 24 values
OPENING_COL     = 11   # METER READING AT 00:00HRS MWH
CLOSING_COL     = 105  # PRESENT METER READING MWH for hour 24
FEEDER_NAME_COL = 7    # ASSOCIATED 33KV FEEDER
REGION_COL      = 0

# Rows to skip
SKIP_PREFIXES = (
    'REGION', 'DISCO', 'AREA CONTROL', 'STATION',
    'TRANSFORMER', 'FEEDER BAND', 'ASSOCIATED', 'TCN LIMITS',
    'DISCO BASE', 'TOTAL', 'KEDCO', 'REMARKS', 'GRAND TOTAL',
)

NAME_MAP = {
    "SMALL SCALE":            "SMALL SCALE",
    "DAN'AGUNDI 1":           "DAN AGUNDI 1",
    "DAN'AGUNDI 2":           "DAN AGUNDI 2",
    "MAI'ADU'A":              "MAI ADUA",
    "RICE FIELD (FEEDER 3)":  "RICE FIELD",
    "HON. ABUBAKAR KABIR":    "HON. ABUBAKAR",
    "COCA - COLA":            "COCA COLA",
    "N B CERAMICS":           "NB CERAMIC",
    "NB-CERAMIC":             "NB CERAMIC",
    "MR JIMETA":              "JIMETA",
    "R/ZAKI":                 "RIJIYAR ZAKI",
    "RUMMAWA":                "RUMAWA",
    "POLY":                   "POLYTECHNIC",
    "CHALLAWA WATER PLANT":   "CHALAWA WATER PLANT",
    "DUTSE GOVERNMENT HOUSE": "GOVERNMENT HOUSE DUTSE",
    "DR. JIMETA":             "DR JIMETA",
}


def _safe_float(val):
    try:
        f = float(str(val).strip().replace(',', ''))
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (ValueError, TypeError):
        return None


def _sheet_day(name):
    m = re.match(r'^(\d+)', str(name).strip())
    return int(m.group(1)) if m else None


def _build_feeder_lookup():
    lookup = {}
    for f in Feeder.objects.filter(voltage_level='33kv'):
        key = re.sub(r'\s+', ' ', f.name.upper().strip())
        lookup[key] = f
    return lookup


def _find_feeder(raw_name, lookup):
    raw = str(raw_name).strip().upper()
    mapped = NAME_MAP.get(raw, raw)
    mapped = re.sub(r'\s+', ' ', mapped.strip())
    if mapped in lookup:
        return lookup[mapped]
    for key, feeder in lookup.items():
        if mapped in key or key in mapped:
            return feeder
    return None


def _is_skip(val):
    if val is None:
        return True
    s = str(val).strip().upper()
    return not s or s == 'NAN' or any(s.startswith(k) for k in SKIP_PREFIXES)


class Command(BaseCommand):
    help = 'Import 33KV feeder data from monthly energy accounting Excel → HourlyLoad + DailyHoursOfSupply + EnergyDelivered'

    def add_arguments(self, parser):
        parser.add_argument('--file',    required=True, type=str, help='Path to Excel file')
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
            raise CommandError('--month must be YYYY-MM, e.g. 2026-07')

        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN — nothing will be written'))

        feeder_lookup = _build_feeder_lookup()
        self.stdout.write(f'Loaded {len(feeder_lookup)} 33KV feeders from DB')

        xl = pd.ExcelFile(file_path, engine='openpyxl')

        total_dhos  = 0
        total_ed    = 0
        days_done   = 0
        unmatched   = set()
        errors      = []

        for sheet_name in xl.sheet_names:
            day = _sheet_day(sheet_name)
            if not day:
                continue
            try:
                reading_date = date(year, month, day)
            except ValueError:
                continue

            df = xl.parse(sheet_name, header=None)
            if df.shape[1] < 106:
                errors.append(f'{reading_date}: only {df.shape[1]} cols — skipped')
                continue

            dhos_map      = {}   # feeder_id → (feeder, hours_with_supply)
            ed_map        = {}   # feeder_id → (feeder, daily_mwh)
            day_unmatched = []

            for r in range(3, df.shape[0]):
                region_val = df.iloc[r, REGION_COL]
                name_val   = df.iloc[r, FEEDER_NAME_COL]

                if _is_skip(name_val):
                    continue
                if _is_skip(region_val):
                    continue

                feeder = _find_feeder(name_val, feeder_lookup)
                if feeder is None:
                    label = str(name_val).strip().upper()
                    if label and label != 'NAN':
                        day_unmatched.append(label)
                        unmatched.add(label)
                    continue

                # Opening and closing cumulative meter readings
                opening = _safe_float(df.iloc[r, OPENING_COL])
                closing = _safe_float(df.iloc[r, CLOSING_COL])

                # Daily energy (MWh) = closing − opening
                if opening is not None and closing is not None and closing >= opening:
                    daily_mwh = round(closing - opening, 2)
                    ed_map[feeder.id] = (feeder, daily_mwh)

                # Supply hours = count of hours where meter reading moved (energy > 0)
                prev = opening
                hours_with_supply = 0
                for col in HOUR_METER_COLS:
                    if col >= df.shape[1]:
                        continue
                    curr = _safe_float(df.iloc[r, col])
                    if curr is not None and prev is not None and curr > prev:
                        hours_with_supply += 1
                    if curr is not None:
                        prev = curr
                dhos_map[feeder.id] = (feeder, hours_with_supply)

            if dry_run:
                self.stdout.write(
                    f'  [DRY] {reading_date}: {len(dhos_map)} feeders, '
                    f'{len(ed_map)} energy rows, {len(dhos_map)} supply-hours rows'
                    + (f' | unmatched: {day_unmatched}' if day_unmatched else '')
                )
                days_done += 1
                continue

            try:
                with transaction.atomic():
                    feeder_ids_today = list(dhos_map.keys())

                    # Clear existing EnergyDelivered for this date
                    EnergyDelivered.objects.filter(
                        date=reading_date, feeder_id__in=feeder_ids_today
                    ).delete()

                    for feeder, mwh in ed_map.values():
                        EnergyDelivered.objects.create(
                            feeder=feeder,
                            date=reading_date,
                            energy_mwh=Decimal(str(mwh)),
                        )
                    total_ed += len(ed_map)

                    for feeder, hrs in dhos_map.values():
                        DailyHoursOfSupply.objects.update_or_create(
                            feeder=feeder,
                            date=reading_date,
                            defaults={'hours_supplied': Decimal(str(hrs))},
                        )
                    total_dhos += len(dhos_map)

                days_done += 1
                self.stdout.write(
                    f'  {reading_date}: {len(ed_map)} EnergyDelivered  '
                    f'{len(dhos_map)} DailyHoursOfSupply'
                    + (f'  | unmatched: {day_unmatched}' if day_unmatched else '')
                )
            except Exception as exc:
                errors.append(f'{reading_date}: {exc}')

        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS(f'Days processed      : {days_done}'))
        self.stdout.write(self.style.SUCCESS(f'EnergyDelivered rows: {total_ed}'))
        self.stdout.write(self.style.SUCCESS(f'DailyHoursOfSupply  : {total_dhos}'))
        if unmatched:
            self.stdout.write(self.style.WARNING(f'\nUnmatched feeder names ({len(unmatched)}):'))
            for n in sorted(unmatched):
                self.stdout.write(self.style.WARNING(f'  ! {n}'))
        if errors:
            self.stdout.write(self.style.ERROR(f'\nErrors ({len(errors)}):'))
            for e in errors:
                self.stdout.write(self.style.ERROR(f'  x {e}'))
        if dry_run:
            self.stdout.write(self.style.NOTICE('--- DRY-RUN COMPLETE — nothing was written ---'))
