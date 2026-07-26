# -*- coding: utf-8 -*-
"""
Management command: import_tmo_excel

Imports TWO things from the TCN monthly load-monitoring Excel file in one pass:

  1. ENERGY (physical meter readings) → EnergyDelivered + CumulativeMeterReading
       Source: col 11 (METER READING AT 00:00HRS MWH) and col 105 (24:00 reading)
       Formula: daily energy MWh = col_105 − col_11   (intra-day delta, no prev-day dep.)
       Only onboarded, matched KEDCO feeders.

  2. FORECAST (TCN daily allocation) → TMODailyAllocation.expected_mw
       Source: FORECAST MW col for each hour (cols 12, 16, 20, … 104)
       Formula: sum all Kano-DISCO FORECAST values for all 24 hours / 24 = expected_mw
       Includes ALL Kano rows (even non-onboarded) except "ON SOAK" (zero allocation).
       This fixes the flat forecast line on the Daily Energy chart.

Excel column layout (0-indexed, 108 columns total):
  0  REGION
  1  DISCO                         ← filter: Kano only
  2  AREA CONTROL
  3  STATION
  4  TRANSFORMER NAME
  5  RATING
  6  FEEDER BAND
  7  ASSOCIATED 33KV FEEDER        ← feeder name used for DB matching
  8  TCN Limits
  9  Disco Base Load
  10 Disco Peak Load
  11 METER READING AT 00:00HRS MWH ← start-of-day cumulative (energy col 1)
  12 hour 01:00 FORECAST MW        ← forecast col 1
  13 hour 01:00 PRESENT METER READING MWH
  14 hour 01:00 ACTUAL MW
  15 hour 01:00 DIFFERENCE
  16 hour 02:00 FORECAST MW
  … (pattern repeats × 24)
  104 hour 24:00 FORECAST MW
  105 hour 24:00 PRESENT METER READING MWH  ← end-of-day cumulative (energy col 2)
  106 hour 24:00 ACTUAL MW
  107 hour 24:00 DIFFERENCE

Row layout:
  Row 0: merged title (ignored)
  Row 1: hour labels 01:00 … 24:00
  Row 2: column headers
  Row 3+: feeder data rows

Usage:
    # Inspect column layout (no DB write):
    python manage.py import_tmo_excel --file /path/file.xlsx --inspect

    # Dry-run — see exactly what would be written:
    python manage.py import_tmo_excel --file /path/file.xlsx --month 2026-06 --dry-run

    # Full import:
    python manage.py import_tmo_excel --file /path/file.xlsx --month 2026-06

    # Re-run safely (skip already-imported energy, overwrite allocation):
    python manage.py import_tmo_excel --file /path/file.xlsx --month 2026-06 --skip-existing
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
from technical.models import CumulativeMeterReading, EnergyDelivered
from tmo.models import TMODailyAllocation


# ── Fixed column indices (0-based) ────────────────────────────────────────────
DISCO_COL    = 1    # DISCO name
FEEDER_COL   = 7    # ASSOCIATED 33KV FEEDER  ← actual feeder name
METER_00_COL = 11   # METER READING AT 00:00HRS MWH  (start of day)
METER_24_COL = 105  # PRESENT METER READING MWH @ 24:00  (end of day)
HEADER_ROWS  = 3    # rows 0, 1, 2 are title/labels/headers; data starts at row 3

# Hour FORECAST columns: 12, 16, 20, …, 104  (24 hours × 4-col blocks)
FORECAST_COLS = [12 + h * 4 for h in range(24)]

# ── Name map: Excel feeder name (upper) → DB feeder name ─────────────────────
# Only mismatches listed — exact matches resolve automatically
NAME_MAP = {
    'RUMMAWA':             'RUMAWA',
    "DAN'AGUNDI 1":        'DAN AGUNDI 1',
    "DAN'AGUNDI 2":        'DAN AGUNDI 2',
    "MAI'ADU'A":           'MAI ADUA',
    'AJIWA':               'AJIWA WATER WORKS',
    'POLY':                'POLYTECHNIC',
    'COCA - COLA':         'COCA COLA',
    'TAMBURAWA':           'TAMBURAWA WATER PLANT',
    'CHALLAWA':            'CHALAWA WATER PLANT',
    'RICE FIELD':          'FEEDER 3/RICE FIELD',
    'RICE MILL':           'RICE MILLS',
    'HON. ABUBAKAR KABIR': 'HON. ABUBAKAR',
    'DAWANAU INDUSTRIAL':  'DAWANAU',
}

# Skip entirely from ENERGY import (non-KEDCO, unregistered, or offline)
ENERGY_SKIP = frozenset({
    'ON SOAK',
    'WIND FARM', 'JIBIA', 'DR JIMETA', 'SARKIN YAKI',
    'AZARE', 'MISAU', "JAMA'ARE", 'NGURU',
})

# Skip from FORECAST sum (truly offline / zero-allocation placeholder)
FORECAST_SKIP = frozenset({'ON SOAK'})

KEDCO_DISCO = 'kano'


# ── Utilities ─────────────────────────────────────────────────────────────────

def _safe_float(val):
    """Return float or None for NaN / blank / non-numeric."""
    if val is None:
        return None
    s = str(val).strip().replace(',', '')
    if not s or s.lower() == 'nan':
        return None
    try:
        f = float(s)
        return None if math.isnan(f) else f
    except (ValueError, TypeError):
        return None


def _sheet_day(name: str):
    """'1st' → 1, '22nd' → 22, None if not a day sheet."""
    m = re.match(r'^(\d+)', str(name).strip())
    return int(m.group(1)) if m else None


# ── Command ───────────────────────────────────────────────────────────────────

class Command(BaseCommand):
    help = (
        'Import ENERGY (meter-based MWh) + FORECAST (TCN allocation MW) '
        'from TCN monthly Excel → EnergyDelivered, CumulativeMeterReading, TMODailyAllocation'
    )

    def add_arguments(self, parser):
        parser.add_argument('--file', required=True, type=str,
                            help='Full path to the Excel file')
        parser.add_argument('--month', type=str, default=None,
                            help='Month to import: YYYY-MM  (e.g. 2026-06)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Parse + validate but do NOT write to DB')
        parser.add_argument('--inspect', action='store_true',
                            help='Print column layout from first sheet and exit')
        parser.add_argument('--skip-existing', action='store_true',
                            help='Skip (feeder, date) pairs already in EnergyDelivered')

    def handle(self, *args, **options):
        file_path = Path(options['file'])
        if not file_path.is_file():
            raise CommandError(f'File not found: {file_path}')

        xl = pd.ExcelFile(file_path, engine='openpyxl')

        if options['inspect']:
            self._inspect(xl)
            return

        if not options['month']:
            raise CommandError('--month is required  (e.g. --month 2026-06)')
        try:
            year, month = (int(x) for x in options['month'].split('-'))
            date(year, month, 1)
        except (ValueError, TypeError):
            raise CommandError('--month must be YYYY-MM  (e.g. 2026-06)')

        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN MODE — nothing will be written to DB'))

        # ── Pre-load all onboarded feeders: upper(name) → Feeder obj ──────────
        feeder_cache = {
            f.name.strip().upper(): f
            for f in Feeder.objects.filter(is_onboarded=True)
        }
        self.stdout.write(f'Loaded {len(feeder_cache)} onboarded feeders from DB')

        # ── Collect all day sheets for the requested month ─────────────────────
        day_sheets = {}
        for sname in xl.sheet_names:
            day = _sheet_day(sname)
            if day is None:
                continue
            try:
                d = date(year, month, day)
            except ValueError:
                continue
            if day not in day_sheets:           # keep first occurrence
                day_sheets[day] = (sname, d)

        if not day_sheets:
            raise CommandError(
                f'No day-sheets found (expected "1st", "2nd" … "30th"). '
                f'Available sheets: {xl.sheet_names[:15]}'
            )

        all_days = set(range(1, 31))
        found_days = set(day_sheets.keys())
        missing_days = all_days - found_days
        if missing_days:
            self.stdout.write(self.style.WARNING(
                f'Missing sheets for days: {sorted(missing_days)}'
            ))
        self.stdout.write(
            f'Processing {len(day_sheets)} day sheets: {sorted(found_days)}'
        )

        # ── Global accumulators ────────────────────────────────────────────────
        stats = {
            'ed_created':   0, 'ed_updated':     0,
            'cmr_written':  0, 'alloc_written':   0,
            'blank_meter':  0, 'skipped_existing':0,
            'skipped_name': 0,
            'errors':       [],
            'warnings':     [],
        }
        unmatched: set[str] = set()

        # ── Process each day sheet ─────────────────────────────────────────────
        for day in sorted(day_sheets.keys()):
            sheet_name, reading_date = day_sheets[day]
            df = xl.parse(sheet_name, header=None)

            if df.shape[1] < 106:
                stats['errors'].append(
                    f'{reading_date} ({sheet_name}): only {df.shape[1]} cols — expected 108, skipped'
                )
                continue

            energy_count, forecast_mwh = self._process_sheet(
                df=df,
                reading_date=reading_date,
                feeder_cache=feeder_cache,
                dry_run=dry_run,
                skip_existing=options['skip_existing'],
                stats=stats,
                unmatched=unmatched,
            )

            # ── TMODailyAllocation (per day) ─────────────────────────────────
            expected_mw = round(forecast_mwh / 24.0, 2)

            if dry_run:
                self.stdout.write(
                    f'  [DRY] {reading_date}: {energy_count} energy rows  |  '
                    f'alloc expected_mw={expected_mw:.2f} '
                    f'({forecast_mwh:.1f} MWh ÷ 24)'
                )
            else:
                try:
                    TMODailyAllocation.objects.update_or_create(
                        date=reading_date,
                        defaults={
                            'expected_mw': Decimal(str(expected_mw)),
                            'source':      'manual',
                            'notes':       f'Imported from TCN monthly Excel ({options["month"]})',
                        },
                    )
                    stats['alloc_written'] += 1
                    self.stdout.write(
                        f'  {reading_date}: {energy_count} energy rows  |  '
                        f'alloc {expected_mw:.2f} MW'
                    )
                except Exception as exc:
                    stats['errors'].append(
                        f'{reading_date}: TMODailyAllocation error — {exc}'
                    )

        self._report(stats, unmatched, dry_run)

    # ── Per-sheet processing ───────────────────────────────────────────────────

    def _process_sheet(self, *, df, reading_date, feeder_cache,
                       dry_run, skip_existing, stats, unmatched):
        """
        Process one day sheet.  Returns (energy_row_count, total_forecast_mwh).

        Two independent passes over the rows in one loop:
          A) ENERGY: matched onboarded Kano feeders → EnergyDelivered + CMR
          B) FORECAST: ALL Kano rows (except ON SOAK) → accumulate forecast sum
        """
        energy_count  = 0
        forecast_mwh  = 0.0
        forecast_rows = 0  # how many feeder-rows contributed to forecast

        cmr_to_create = []  # for bulk_create at end (bypasses save() override)
        ed_rows       = []  # (feeder, energy_mwh) tuples

        for r in range(HEADER_ROWS, df.shape[0]):
            row = df.iloc[r]

            # ── DISCO filter ──────────────────────────────────────────────────
            disco = str(row.iloc[DISCO_COL]).strip().lower()
            if disco != KEDCO_DISCO:
                continue

            raw_name = str(row.iloc[FEEDER_COL]).strip()
            if not raw_name or raw_name == 'nan':
                continue
            excel_name = raw_name.strip().upper()

            # ── B) FORECAST accumulation (ALL Kano rows except ON SOAK) ───────
            if excel_name not in FORECAST_SKIP:
                for fc_col in FORECAST_COLS:
                    v = _safe_float(row.iloc[fc_col])
                    if v is not None and v >= 0:
                        forecast_mwh += v
                forecast_rows += 1

            # ── A) ENERGY import (onboarded + matched feeders only) ────────────
            if excel_name in ENERGY_SKIP:
                stats['skipped_name'] += 1
                continue

            db_name = NAME_MAP.get(excel_name, excel_name).upper()
            feeder  = feeder_cache.get(db_name)
            if feeder is None:
                unmatched.add(excel_name)
                continue

            # Read start and end cumulative meter readings
            v00 = _safe_float(row.iloc[METER_00_COL])
            v24 = _safe_float(row.iloc[METER_24_COL])

            if v00 is None or v24 is None:
                stats['blank_meter'] += 1
                stats['warnings'].append(
                    f'{reading_date} | {excel_name}: '
                    f'blank meter reading (00:00={row.iloc[METER_00_COL]}, '
                    f'24:00={row.iloc[METER_24_COL]}) — energy skipped'
                )
                continue

            energy_mwh = v24 - v00

            if energy_mwh < 0:
                stats['errors'].append(
                    f'{reading_date} | {excel_name}: '
                    f'negative daily energy ({energy_mwh:.2f} MWh) — skipped'
                )
                continue

            # Skip if already exists (--skip-existing flag)
            if skip_existing and EnergyDelivered.objects.filter(
                feeder=feeder, date=reading_date
            ).exists():
                stats['skipped_existing'] += 1
                continue

            if dry_run:
                self.stdout.write(
                    f'    [DRY] {feeder.name} | {energy_mwh:.2f} MWh '
                    f'({v00:.2f} → {v24:.2f})'
                )
                stats['ed_created'] += 1
                energy_count += 1
                continue

            ed_rows.append((feeder, energy_mwh, v24))

        # ── Write energy rows (outside the loop, in one transaction per sheet) ─
        if ed_rows and not dry_run:
            with transaction.atomic():
                for feeder, energy_mwh, v24 in ed_rows:
                    try:
                        # 1. EnergyDelivered — intra-day delta (authoritative)
                        _, created = EnergyDelivered.objects.update_or_create(
                            feeder=feeder,
                            date=reading_date,
                            defaults={
                                'energy_mwh': Decimal(str(round(energy_mwh, 4)))
                            },
                        )
                        if created:
                            stats['ed_created'] += 1
                        else:
                            stats['ed_updated'] += 1

                        # 2. CumulativeMeterReading — end-of-day value
                        #    Use bulk_create (bypasses save() override so it
                        #    does NOT re-compute EnergyDelivered and overwrite
                        #    the precise intra-day value we just set above)
                        CumulativeMeterReading.objects.filter(
                            feeder=feeder, reading_date=reading_date
                        ).delete()
                        CumulativeMeterReading.objects.bulk_create([
                            CumulativeMeterReading(
                                feeder=feeder,
                                reading_date=reading_date,
                                cumulative_mwh=Decimal(str(round(v24, 4))),
                                is_estimated=False,
                            )
                        ])
                        stats['cmr_written'] += 1
                        energy_count += 1

                    except Exception as exc:
                        stats['errors'].append(
                            f'{reading_date} | {feeder.name}: DB error — {exc}'
                        )

        if forecast_rows == 0:
            stats['warnings'].append(
                f'{reading_date}: no Kano rows found for forecast — allocation set to 0'
            )

        return energy_count, forecast_mwh

    # ── Final summary report ───────────────────────────────────────────────────

    def _report(self, stats, unmatched, dry_run):
        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write(self.style.SUCCESS(
            f"EnergyDelivered  created  : {stats['ed_created']}"
        ))
        self.stdout.write(self.style.SUCCESS(
            f"EnergyDelivered  updated  : {stats['ed_updated']}"
        ))
        self.stdout.write(self.style.SUCCESS(
            f"CumulativeMeter  written  : {stats['cmr_written']}"
        ))
        self.stdout.write(self.style.SUCCESS(
            f"TMODailyAlloc    written  : {stats['alloc_written']}"
        ))

        if stats['skipped_existing']:
            self.stdout.write(self.style.WARNING(
                f"Skipped (already exists) : {stats['skipped_existing']}"
            ))
        if stats['skipped_name']:
            self.stdout.write(self.style.WARNING(
                f"Skipped (skip-list)      : {stats['skipped_name']}"
            ))
        if stats['blank_meter']:
            self.stdout.write(self.style.WARNING(
                f"Blank meter readings     : {stats['blank_meter']}"
            ))

        if stats['warnings']:
            self.stdout.write(self.style.WARNING(
                f"\nWarnings ({len(stats['warnings'])}):"
            ))
            for w in stats['warnings']:
                self.stdout.write(self.style.WARNING(f'  ! {w}'))

        if unmatched:
            self.stdout.write(self.style.ERROR(
                f'\nUNMATCHED feeder names — energy NOT imported for these ({len(unmatched)}):'
            ))
            for n in sorted(unmatched):
                self.stdout.write(self.style.ERROR(f'  • {n}'))
            self.stdout.write(self.style.ERROR(
                '  → Add them to NAME_MAP in the command if they should be imported.'
            ))

        if stats['errors']:
            self.stdout.write(self.style.ERROR(
                f'\nErrors ({len(stats["errors"])}):'
            ))
            for e in stats['errors']:
                self.stdout.write(self.style.ERROR(f'  ✗ {e}'))

        self.stdout.write(self.style.SUCCESS('=' * 70))
        if dry_run:
            self.stdout.write(self.style.NOTICE('--- DRY-RUN COMPLETE — nothing was written ---'))

    # ── Inspect helper ─────────────────────────────────────────────────────────

    def _inspect(self, xl):
        day_sheets = [s for s in xl.sheet_names if re.match(r'^\d+', str(s).strip())]
        if not day_sheets:
            self.stdout.write(self.style.ERROR('No day-sheets found.'))
            for s in xl.sheet_names:
                self.stdout.write(f'  {s}')
            return

        sheet = day_sheets[0]
        df = xl.parse(sheet, header=None)
        self.stdout.write(
            f"\n=== Sheet '{sheet}' | {df.shape[0]} rows × {df.shape[1]} cols ===\n"
        )
        self.stdout.write('All metadata column headers (row 2):')
        for c in range(12):
            v = df.iloc[2, c] if df.shape[0] > 2 else ''
            self.stdout.write(f'  col {c:>3}: {str(v)[:50]}')

        self.stdout.write(f'\nExpected key cols:')
        self.stdout.write(f'  DISCO        = col {DISCO_COL}')
        self.stdout.write(f'  FEEDER NAME  = col {FEEDER_COL}')
        self.stdout.write(f'  00:00 meter  = col {METER_00_COL}')
        self.stdout.write(f'  24:00 meter  = col {METER_24_COL}')
        self.stdout.write(f'  FORECAST hrs = cols {FORECAST_COLS[:4]} … {FORECAST_COLS[-1]}')

        self.stdout.write('\nFirst 10 Kano data rows:')
        count = 0
        for r in range(HEADER_ROWS, df.shape[0]):
            if str(df.iloc[r, DISCO_COL]).strip().lower() != KEDCO_DISCO:
                continue
            feeder = df.iloc[r, FEEDER_COL]
            m00    = df.iloc[r, METER_00_COL]
            m24    = df.iloc[r, METER_24_COL] if df.shape[1] > METER_24_COL else 'N/A'
            fc1    = df.iloc[r, FORECAST_COLS[0]]
            self.stdout.write(
                f'  r{r:>3} | {str(feeder)[:25]:<25} | '
                f'00:00={m00}  24:00={m24}  FC_01:00={fc1}'
            )
            count += 1
            if count >= 10:
                break
