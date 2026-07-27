# -*- coding: utf-8 -*-
"""
Management command: import_load_flow_excel

Imports data from the monthly 33KV Feeder Load Flow Excel
(e.g. "JULY 2026 33KV FEEDER LOAD FLOW.xlsx") into:

  - HourlyLoad            (feeder + date + hour + load_mw)
  - TMONetworkDispatch    (daily KEDCO allocation vs offtake summary)
  - TMONetworkDispatchHourly (24-hour breakdown of above)

Excel structure (sheets named "1ST", "2ND", ... "27TH"):
  Row 0: title
  Row 1: headers  — col 0 = "33KV Feeders", cols 2-25 = hours (01:00 ... 00:00)
  Row 2: sub-header
  Rows 3-86: one feeder per row
    col 0  = feeder name
    col 1  = PEAK LOAD EVER (skip)
    col 2  = load at 01:00 (HourlyLoad hour=0, DispatchHourly hour=1)
    ...
    col 25 = load at 00:00 (HourlyLoad hour=23, DispatchHourly hour=24)
    col 26 = MIN  (skip)
    col 27 = AVG  (skip)
    col 28 = MAX  (skip)
  Row 87: KEDCO TOTAL LOAD PER HOUR
  Row 88: ENERGY OFFTAKE PER HOUR (KBT)
  Row 89: ENERGY OFFTAKE PER HOUR (FT)
  Row 90: TOTAL ENERGY OFFTAKE PER HOUR → disco_offtake_mw
  Row 91: AVAILABLE GENERATION PER HOUR → available_generation_mw
  Row 92: DISCO ALLOCATION PER HOUR (regional)
  Row 93: DISCO ALLOCATION PER HOUR (national)
  Row 94: KEDCO ALLOCATION PER HOUR → kedco_allocation_mw
  Row 95: VARIANCE → variance_mw

Usage:
    python manage.py import_load_flow_excel --file /path/file.xlsx --month 2026-07
    python manage.py import_load_flow_excel --file /path/file.xlsx --month 2026-07 --dry-run
    python manage.py import_load_flow_excel --file /path/file.xlsx --month 2026-07 --skip-dispatch
    python manage.py import_load_flow_excel --file /path/file.xlsx --month 2026-07 --skip-hourly-load
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
from technical.models import HourlyLoad
from tmo.models import TMONetworkDispatch, TMONetworkDispatchHourly

# ── Column layout ─────────────────────────────────────────────────────────────
# Hourly load columns: cols 2-25 (24 hours, 01:00 through 00:00 next day)
HOUR_COLS = list(range(2, 26))   # 24 entries

# Summary row identifiers (startswith check on col 0, uppercased)
SUMMARY_ROW_LABELS = {
    'TOTAL ENERGY OFFTAKE PER HOUR': 'disco_offtake',
    'AVAILABLE GENERATION PER HOUR': 'available_gen',
    'KEDCO ALLOCATION PER HOUR':     'kedco_alloc',
    'VARIANCE':                       'variance',
}

FEEDER_DATA_END_ROW = 87   # rows 3-86 are feeder data; row 87+ are summaries

# Feeder rows to skip (non-data)
SKIP_PREFIXES = (
    'KEDCO TOTAL', 'ENERGY OFFTAKE', 'TOTAL ENERGY', 'AVAILABLE GENERATION',
    'DISCO ALLOCATION', 'KEDCO ALLOCATION', 'VARIANCE', 'NUMBER OF',
    'TOTAL NUMBER', 'DATE', 'TRANSMISSION', 'KUMBOTSO', "DAN'AGUNDI 132",
    'DAKATA', 'KANKIA', 'KATSINA 132', 'KWANAR', 'TAMBURAWA 132',
    'HADEIJA', 'DUTSE 132', 'FUNTUA', 'DAURA 132', 'WUDIL 132',
    'GAGARAWA', 'BICHI', 'TOTAL',
)

NAME_MAP = {
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

# Fault codes — explicit outage/load-shed strings that mean 0 MW supply
FAULT_PREFIXES = ('OC', 'E/', 'LS', 'L/', 'EMG', 'EMRG', 'L/S', 'L/L', 'ON ')


def _safe_float(val):
    if val is None:
        return None
    try:
        f = float(str(val).strip().replace(',', ''))
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (ValueError, TypeError):
        return None


def _is_blank(val):
    """True for empty/null/NaN — genuinely missing or not yet submitted."""
    if val is None:
        return True
    s = str(val).strip().upper()
    return not s or s == 'NAN'


def _is_explicit_fault(val):
    """True for explicit fault/outage/load-shed code strings (e.g. OC/EF, LS/GS)."""
    if val is None:
        return False
    s = str(val).strip().upper()
    if not s or s == 'NAN':
        return False
    return any(s.startswith(p) for p in FAULT_PREFIXES)


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


def _is_skip_row(val):
    if val is None:
        return True
    s = str(val).strip().upper()
    return not s or s == 'NAN' or any(s.startswith(k) for k in SKIP_PREFIXES)


def _row_avg(df, row, cols):
    """Average of numeric values from given cols in a row (ignores None)."""
    vals = [_safe_float(df.iloc[row, c]) for c in cols if c < df.shape[1]]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


class Command(BaseCommand):
    help = 'Import 33KV feeder load flow Excel → HourlyLoad + TMONetworkDispatch(Hourly)'

    def add_arguments(self, parser):
        parser.add_argument('--file',              required=True, type=str)
        parser.add_argument('--month',             required=True, type=str, help='YYYY-MM')
        parser.add_argument('--dry-run',           action='store_true')
        parser.add_argument('--skip-dispatch',     action='store_true',
                            help='Skip writing TMONetworkDispatch / Hourly rows')
        parser.add_argument('--skip-hourly-load',  action='store_true',
                            help='Skip writing HourlyLoad rows')

    def handle(self, *args, **options):
        file_path = Path(options['file'])
        if not file_path.is_file():
            raise CommandError(f'File not found: {file_path}')

        try:
            year, month = (int(x) for x in options['month'].split('-'))
            date(year, month, 1)
        except (ValueError, TypeError):
            raise CommandError('--month must be YYYY-MM, e.g. 2026-07')

        dry_run          = options['dry_run']
        skip_dispatch    = options['skip_dispatch']
        skip_hourly_load = options['skip_hourly_load']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN — nothing will be written'))

        feeder_lookup = _build_feeder_lookup()
        self.stdout.write(f'Loaded {len(feeder_lookup)} 33KV feeders from DB')

        xl = pd.ExcelFile(file_path, engine='openpyxl')

        total_hl       = 0
        total_dispatch = 0
        days_done      = 0
        unmatched      = set()
        errors         = []

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

            # ── 1. Feeder HourlyLoad ────────────────────────────────────────
            hl_rows       = []   # (feeder, hour 0-23, mw)
            day_unmatched = []

            if not skip_hourly_load:
                for r in range(3, min(FEEDER_DATA_END_ROW, df.shape[0])):
                    name_val = df.iloc[r, 0]
                    if _is_skip_row(name_val):
                        continue

                    feeder = _find_feeder(name_val, feeder_lookup)
                    if feeder is None:
                        label = str(name_val).strip().upper()
                        if label and label != 'NAN':
                            day_unmatched.append(label)
                            unmatched.add(label)
                        continue

                    for h_idx, col in enumerate(HOUR_COLS):   # h_idx 0-23
                        if col >= df.shape[1]:
                            continue
                        val = df.iloc[r, col]
                        if _is_blank(val):
                            continue                 # genuinely no data — skip
                        if _is_explicit_fault(val):
                            mw = 0.0                # feeder on fault/outage = 0 MW
                        else:
                            mw = _safe_float(val)
                            if mw is None:
                                continue            # unrecognised string — skip
                        hl_rows.append((feeder, h_idx, round(max(mw, 0.0), 4)))

            # ── 2. Dispatch (offtake / allocation / generation) ─────────────
            dispatch_hourly = {}   # hour(1-24) → dict of values
            dispatch_daily  = {}   # field → value

            if not skip_dispatch:
                # Scan rows 87+ for summary labels
                for r in range(FEEDER_DATA_END_ROW, min(100, df.shape[0])):
                    label_raw = str(df.iloc[r, 0]).strip().upper()
                    field = None
                    for lbl, fname in SUMMARY_ROW_LABELS.items():
                        if label_raw.startswith(lbl.upper()):
                            field = fname
                            break
                    if field is None:
                        continue

                    hourly_vals = {}
                    for h_idx, col in enumerate(HOUR_COLS):   # h_idx 0-23 → hour 1-24
                        if col >= df.shape[1]:
                            continue
                        v = _safe_float(df.iloc[r, col])
                        if v is not None:
                            hourly_vals[h_idx + 1] = v   # hour 1-indexed

                    if hourly_vals:
                        daily_avg = sum(hourly_vals.values()) / len(hourly_vals)
                        dispatch_daily[field] = daily_avg
                        for hr, val in hourly_vals.items():
                            if hr not in dispatch_hourly:
                                dispatch_hourly[hr] = {}
                            dispatch_hourly[hr][field] = val

            # ── Dry-run summary ─────────────────────────────────────────────
            if dry_run:
                self.stdout.write(
                    f'  [DRY] {reading_date}: '
                    f'{len(set((r[0].id, r[1]) for r in hl_rows))} feeder-hours HL  '
                    f'{len(dispatch_hourly)} dispatch-hours'
                    + (f' | unmatched: {day_unmatched}' if day_unmatched else '')
                )
                days_done += 1
                continue

            # ── Write ───────────────────────────────────────────────────────
            try:
                with transaction.atomic():
                    # HourlyLoad
                    if not skip_hourly_load and hl_rows:
                        feeder_ids = list({r[0].id for r in hl_rows})
                        HourlyLoad.objects.filter(
                            date=reading_date,
                            feeder_id__in=feeder_ids,
                        ).delete()
                        objs = [
                            HourlyLoad(
                                feeder=feeder,
                                date=reading_date,
                                hour=hour,
                                load_mw=Decimal(str(mw)),
                                submission_type='admin_override',
                            )
                            for feeder, hour, mw in hl_rows
                        ]
                        HourlyLoad.objects.bulk_create(objs, batch_size=500)
                        total_hl += len(objs)

                    # TMONetworkDispatch + Hourly
                    if not skip_dispatch and dispatch_daily:
                        daily_obj, _ = TMONetworkDispatch.objects.update_or_create(
                            date=reading_date,
                            defaults={
                                'disco_offtake_mw':     Decimal(str(round(dispatch_daily.get('disco_offtake', 0), 4))),
                                'kedco_allocation_mw':  Decimal(str(round(dispatch_daily.get('kedco_alloc', 0), 4))),
                                'available_generation_mw': Decimal(str(round(dispatch_daily.get('available_gen', 0), 4))),
                                'variance_mw':          Decimal(str(round(dispatch_daily.get('variance', 0), 4))),
                                'source': 'manual',
                            }
                        )
                        TMONetworkDispatchHourly.objects.filter(date=reading_date).delete()
                        hourly_objs = [
                            TMONetworkDispatchHourly(
                                dispatch=daily_obj,
                                date=reading_date,
                                hour=hr,
                                disco_offtake_mw=     Decimal(str(round(vals.get('disco_offtake', 0), 4))),
                                kedco_allocation_mw=  Decimal(str(round(vals.get('kedco_alloc', 0), 4))),
                                available_generation_mw=Decimal(str(round(vals.get('available_gen', 0), 4))),
                                variance_mw=          Decimal(str(round(vals.get('variance', 0), 4))),
                            )
                            for hr, vals in sorted(dispatch_hourly.items())
                        ]
                        TMONetworkDispatchHourly.objects.bulk_create(hourly_objs, batch_size=100)
                        total_dispatch += len(hourly_objs)

                days_done += 1
                self.stdout.write(
                    f'  {reading_date}: '
                    f'{total_hl} HL rows total  '
                    f'{len(dispatch_hourly)} dispatch-hours'
                    + (f'  | unmatched: {day_unmatched}' if day_unmatched else '')
                )
            except Exception as exc:
                errors.append(f'{reading_date}: {exc}')

        self.stdout.write(self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS(f'Days processed      : {days_done}'))
        self.stdout.write(self.style.SUCCESS(f'HourlyLoad rows     : {total_hl}'))
        self.stdout.write(self.style.SUCCESS(f'Dispatch-hourly rows: {total_dispatch}'))
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
