# tmo/sheet_sync.py
"""
Shared utilities for downloading and parsing the monthly 33KV load-flow
Google Sheet into the DB.

Used by:
  - technical/management/commands/sync_33kv_sheet.py  (manual CLI)
  - tmo/tasks.py                                       (hourly Celery beat)
"""
import logging
import math
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import gspread
import pandas as pd
from django.conf import settings
from django.db import transaction
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials

from common.models import Feeder
from technical.models import DailyHoursOfSupply, HourlyLoad
from tmo.models import TMONetworkDispatch, TMONetworkDispatchHourly

logger = logging.getLogger(__name__)

# ── Google auth ───────────────────────────────────────────────────────────────
SCOPES     = ['https://www.googleapis.com/auth/spreadsheets.readonly']
TOKEN_FILE = Path(settings.BASE_DIR) / 'google_token.json'


def _get_gspread_client():
    """Return an authenticated gspread client, refreshing token if needed."""
    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            f'google_token.json not found at {TOKEN_FILE}. '
            'Run python google_auth_setup.py first.'
        )
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        TOKEN_FILE.write_text(creds.to_json())
    return gspread.authorize(creds)


def open_spreadsheet(spreadsheet_id: str):
    """Open a Google Spreadsheet by ID and return the gspread Spreadsheet object."""
    gc = _get_gspread_client()
    return gc.open_by_key(spreadsheet_id)


def worksheet_to_df(worksheet) -> pd.DataFrame:
    """Convert a gspread Worksheet to a pandas DataFrame (no header parsing)."""
    rows = worksheet.get_all_values()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


# ── 33KV sheet parsing (same layout as import_load_flow_excel.py) ─────────────
HOUR_COLS = list(range(2, 26))          # cols 2-25 → hours 1-24
FEEDER_DATA_END_ROW = 87

SKIP_PREFIXES = (
    'KEDCO TOTAL', 'ENERGY OFFTAKE', 'TOTAL ENERGY', 'AVAILABLE GENERATION',
    'DISCO ALLOCATION', 'KEDCO ALLOCATION', 'VARIANCE', 'NUMBER OF',
    'TOTAL NUMBER', 'DATE', 'TRANSMISSION', 'KUMBOTSO', "DAN'AGUNDI 132",
    'DAKATA', 'KANKIA', 'KATSINA 132', 'KWANAR', 'TAMBURAWA 132',
    'HADEIJA', 'DUTSE 132', 'FUNTUA', 'DAURA 132', 'WUDIL 132',
    'GAGARAWA', 'BICHI', 'TOTAL',
    'ON SOAK',   # operational-state annotation, not a feeder
    'WIND FARM', # generation source row, not a distribution feeder
    # Non-KEDCO feeders — Bauchi/Yobe state (YEDC territory), appear in sheet but not KEDCO assets
    'NGURU',
    'MISAU',
    'AZARE',
    "JAMA'ARE",
    'JAMAARE',
)

SUMMARY_ROW_LABELS = {
    'TOTAL ENERGY OFFTAKE PER HOUR': 'disco_offtake',
    'AVAILABLE GENERATION PER HOUR': 'available_gen',
    'KEDCO ALLOCATION PER HOUR':     'kedco_alloc',
    'VARIANCE':                       'variance',
}

FAULT_PREFIXES = ('OC', 'E/', 'LS', 'L/', 'EMG', 'EMRG', 'L/S', 'L/L', 'ON ')

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
    raw    = str(raw_name).strip().upper()
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


# Days within this window are always re-synced (catches corrections to recent entries)
RECENT_RESYNC_DAYS = 3


def _days_needing_sync(worksheets, year: int, month: int,
                       force: bool = False, only_day: int = None):
    """
    Return list of (worksheet, date) that should be synced.

    Rules:
      - Skip sheets that are not day-numbered
      - Skip future days
      - Today + last 2 days: always include (corrections + live data)
      - Older past days: skip if already synced with enough feeders
        (uses 70% of total 33KV feeder count as completeness threshold)
      - force=True: include everything regardless
    """
    today = date.today()
    recency_cutoff = today - timedelta(days=RECENT_RESYNC_DAYS - 1)

    # Compute expected feeder count once — used for partial-sync detection
    from common.models import Feeder
    total_33kv = Feeder.objects.filter(voltage_level='33kv').count()
    min_feeders_complete = max(10, int(total_33kv * 0.70))

    needs = []

    for ws in worksheets:
        day = _sheet_day(ws.title)
        if not day:
            continue
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if d > today:
            continue
        if only_day and day != only_day:
            continue

        if not force and d < recency_cutoff:
            # Older day — skip only if we have a complete-enough sync
            feeder_count = (
                HourlyLoad.objects
                .filter(date=d, feeder__voltage_level='33kv')
                .values('feeder_id').distinct().count()
            )
            if feeder_count >= min_feeders_complete:
                continue   # already well-synced

        needs.append((ws, d))

    return needs


def sync_33kv_sheet(spreadsheet_id: str, year: int, month: int,
                    force: bool = False, dry_run: bool = False,
                    only_day: int = None, log_fn=None) -> dict:
    """
    Core sync function. Reads the 33KV Google Sheet and writes missing days to DB.

    Returns a summary dict:
      {'days_synced': N, 'hl_rows': N, 'dispatch_days': N, 'unmatched': [...], 'errors': [...]}
    """
    if log_fn is None:
        log_fn = logger.info

    spreadsheet   = open_spreadsheet(spreadsheet_id)
    worksheets    = spreadsheet.worksheets()
    feeder_lookup = _build_feeder_lookup()

    to_sync = _days_needing_sync(worksheets, year, month, force=force, only_day=only_day)
    log_fn(f'33KV sync: {len(to_sync)} day(s) to process for {year}-{month:02d}')

    total_hl       = 0
    total_dispatch = 0
    days_synced    = 0
    unmatched      = set()
    errors         = []

    for ws, reading_date in to_sync:
        df = worksheet_to_df(ws)
        if df.shape[1] < 26:
            errors.append(f'{reading_date}: only {df.shape[1]} cols — skipped')
            continue

        # ── Feeder HourlyLoad ────────────────────────────────────────────────
        hl_rows       = []
        day_unmatched = []

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
            for h_idx, col in enumerate(HOUR_COLS):
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

        # ── Dispatch rows ────────────────────────────────────────────────────
        dispatch_hourly = {}
        dispatch_daily  = {}

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
            for h_idx, col in enumerate(HOUR_COLS):
                if col >= df.shape[1]:
                    continue
                v = _safe_float(df.iloc[r, col])
                if v is not None:
                    hourly_vals[h_idx + 1] = v
            if hourly_vals:
                dispatch_daily[field] = sum(hourly_vals.values()) / len(hourly_vals)
                for hr, val in hourly_vals.items():
                    if hr not in dispatch_hourly:
                        dispatch_hourly[hr] = {}
                    dispatch_hourly[hr][field] = val

        # ── Dry run ──────────────────────────────────────────────────────────
        if dry_run:
            log_fn(
                f'  [DRY] {reading_date}: {len(hl_rows)} HL rows  '
                f'{len(dispatch_hourly)} dispatch-hours'
                + (f'  | unmatched: {day_unmatched}' if day_unmatched else '')
            )
            days_synced += 1
            continue

        # ── Write ────────────────────────────────────────────────────────────
        try:
            with transaction.atomic():
                if hl_rows:
                    feeder_ids = list({r[0].id for r in hl_rows})
                    HourlyLoad.objects.filter(
                        date=reading_date, feeder_id__in=feeder_ids
                    ).delete()
                    HourlyLoad.objects.bulk_create([
                        HourlyLoad(
                            feeder=feeder,
                            date=reading_date,
                            hour=hour,
                            load_mw=Decimal(str(mw)),
                            submission_type='admin_override',
                        )
                        for feeder, hour, mw in hl_rows
                    ], batch_size=500)
                    total_hl += len(hl_rows)

                    # Derive supply hours: count hours with load_mw > 0 per feeder
                    supply_hrs: dict = {}
                    for feeder, hour, mw in hl_rows:
                        if mw > 0:
                            supply_hrs[feeder] = supply_hrs.get(feeder, 0) + 1
                    for feeder, hrs in supply_hrs.items():
                        DailyHoursOfSupply.objects.update_or_create(
                            feeder=feeder, date=reading_date,
                            defaults={'hours_supplied': hrs},
                        )

                if dispatch_daily:
                    daily_obj, _ = TMONetworkDispatch.objects.update_or_create(
                        date=reading_date,
                        defaults={
                            'disco_offtake_mw':        Decimal(str(round(dispatch_daily.get('disco_offtake', 0), 4))),
                            'kedco_allocation_mw':     Decimal(str(round(dispatch_daily.get('kedco_alloc', 0), 4))),
                            'available_generation_mw': Decimal(str(round(dispatch_daily.get('available_gen', 0), 4))),
                            'variance_mw':             Decimal(str(round(dispatch_daily.get('variance', 0), 4))),
                            'source': 'manual',
                        }
                    )
                    TMONetworkDispatchHourly.objects.filter(date=reading_date).delete()
                    TMONetworkDispatchHourly.objects.bulk_create([
                        TMONetworkDispatchHourly(
                            dispatch=daily_obj,
                            date=reading_date,
                            hour=hr,
                            disco_offtake_mw=         Decimal(str(round(vals.get('disco_offtake', 0), 4))),
                            kedco_allocation_mw=      Decimal(str(round(vals.get('kedco_alloc', 0), 4))),
                            available_generation_mw=  Decimal(str(round(vals.get('available_gen', 0), 4))),
                            variance_mw=              Decimal(str(round(vals.get('variance', 0), 4))),
                        )
                        for hr, vals in sorted(dispatch_hourly.items())
                    ], batch_size=100)
                    total_dispatch += len(dispatch_hourly)

            days_synced += 1
            log_fn(
                f'  {reading_date}: {len(hl_rows)} HL  {len(dispatch_hourly)} dispatch-hrs'
                + (f'  | unmatched: {day_unmatched}' if day_unmatched else '')
            )
        except Exception as exc:
            errors.append(f'{reading_date}: {exc}')
            logger.exception(f'Error syncing {reading_date}')

    return {
        'days_synced':    days_synced,
        'hl_rows':        total_hl,
        'dispatch_days':  total_dispatch,
        'unmatched':      sorted(unmatched),
        'errors':         errors,
    }
