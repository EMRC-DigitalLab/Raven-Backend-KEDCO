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
from django.db.models import Q
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials

from common.models import Feeder
from technical.models import CumulativeMeterReading, DailyHoursOfSupply, EnergyDelivered, HourlyLoad
from tmo.models import TMONetworkDispatch, TMONetworkDispatchHourly
from tmo.services import DUPLICATE_FEEDER_SLUGS
from technical.utils.energy_utils import DAILY_BALLOON_LIMIT

logger = logging.getLogger(__name__)

# decimal(10,4) hard limit
_MW_DECIMAL_MAX = Decimal('999999.9999')
# Nigeria's total installed grid capacity is ~6,000 MW; KEDCO's share is ~10%.
# Any single MW field above 10,000 is physically impossible — treat as bad sheet data.
_MW_REALISTIC_MAX = Decimal('10000')


def _safe_mw(v, label: str = 'mw') -> 'Decimal | None':
    """Last-resort guard: coerce to Decimal, return None only on decimal overflow."""
    if v is None:
        return None
    try:
        d = Decimal(str(round(float(v), 4)))
    except Exception:
        return None
    if abs(d) >= _MW_DECIMAL_MAX:
        logger.warning('Decimal overflow in %s=%s — stored as NULL', label, v)
        return None
    return d


def _impute_dispatch_outliers(dispatch_hourly: dict, dispatch_daily: dict) -> None:
    """
    For each MW field, replace physically impossible hourly values with the day
    average of valid hours, then recompute the daily summary from the clean set.

    Nigeria's total installed capacity is ~6,000 MW; KEDCO's share is ~10%.
    Anything beyond 10,000 MW in a single field is a sheet entry error.
    Mutates both dicts in place.
    """
    _MAX = float(_MW_REALISTIC_MAX)
    fields = ('disco_offtake', 'kedco_alloc', 'available_gen', 'variance')

    for field in fields:
        by_hour = {hr: vals[field] for hr, vals in dispatch_hourly.items() if field in vals}
        if not by_hour:
            continue

        valid = {hr: v for hr, v in by_hour.items() if abs(v) <= _MAX}
        bad_hrs = [hr for hr in by_hour if hr not in valid]
        if not bad_hrs:
            continue

        avg = sum(valid.values()) / len(valid) if valid else 0.0
        logger.warning(
            'Imputing %d bad %s hour(s) with day average %.4f MW (raw bad values: %s)',
            len(bad_hrs), field, avg, {hr: by_hour[hr] for hr in bad_hrs},
        )
        for hr in bad_hrs:
            dispatch_hourly[hr][field] = avg

        # Recompute daily average from the now-clean full set
        all_clean = [dispatch_hourly[hr][field] for hr in dispatch_hourly if field in dispatch_hourly[hr]]
        dispatch_daily[field] = sum(all_clean) / len(all_clean)


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
        # Write-back is best-effort: the file may be read-only (bind-mount with
        # tight host permissions). Skipping is safe — the refresh_token in the
        # file is long-lived, so the next run will simply refresh again.
        try:
            TOKEN_FILE.write_text(creds.to_json())
        except OSError:
            logger.debug('google_token.json is not writable; skipping write-back (non-fatal)')
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
    'GAGARAWA 132', 'BICHI', 'TOTAL',
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

# Read-time normalisation for HourlyLoad.fault_code — the source sheet is not
# consistent about how it spells the same underlying problem (confirmed
# 2026-08-18: 27 distinct raw codes across one month for what is really only
# ~6 categories). The raw code is stored verbatim in the DB for traceability;
# this dict is ONLY used when GROUPING/reporting on fault codes, so a new
# spelling variant just needs a new dict entry here — no migration, and the
# raw historical data doesn't need to change. Same pattern as NAME_MAP above.
# Any raw code not listed here falls back to 'Other/Unspecified' rather than
# raising or guessing.
FAULT_CODE_CATEGORIES: dict[str, str] = {
    # Earth fault
    'EF': 'Earth Fault', 'E/F': 'Earth Fault', 'EF/OC': 'Earth Fault',
    'INST. E/F': 'Earth Fault', 'INST E/F': 'Earth Fault',
    # Over current
    'OC': 'Over Current', 'O/C': 'Over Current',
    # Transformer fault
    'TR/F': 'Transformer Fault', 'TR2/F': 'Transformer Fault', 'T/F': 'Transformer Fault',
    # Breaker fault
    'CB/F': 'Breaker Fault',
    # Maintenance / disconnect (by far the most spelling variants observed)
    'MTC/DISC': 'Maintenance / Disconnect', 'MTC/DISCO': 'Maintenance / Disconnect',
    'MTC(DISC)': 'Maintenance / Disconnect', 'MTC/D': 'Maintenance / Disconnect',
    'MTCN': 'Maintenance / Disconnect', 'MTCN/D': 'Maintenance / Disconnect',
    'MTCN DIS': 'Maintenance / Disconnect', 'MTN/DSC': 'Maintenance / Disconnect',
    'MTCE/D': 'Maintenance / Disconnect', 'MTCE/DISC': 'Maintenance / Disconnect',
    'MTCE/KEDCO': 'Maintenance / Disconnect', 'DISC/MTCE': 'Maintenance / Disconnect',
    'DIS/MTC': 'Maintenance / Disconnect', 'D/MNT': 'Maintenance / Disconnect',
    'DISCO MAINT': 'Maintenance / Disconnect',
    # Emergency disconnect
    'EMERG/DISC': 'Emergency Disconnect', 'EMERG/D': 'Emergency Disconnect',
    'EMG/D': 'Emergency Disconnect', 'EMG': 'Emergency Disconnect', 'EMRG': 'Emergency Disconnect',
    # Out of supply (not "out of service" — the feeder itself may be fine,
    # it just has nothing to supply, e.g. upstream constraint)
    'O/S': 'Out of Supply',
    # Load shedding
    'LS': 'Load Shedding', 'L/S': 'Load Shedding', 'LS/GS': 'Load Shedding', 'L/L': 'Load Shedding',
}


# Fuzzy fallback for codes NOT in FAULT_CODE_CATEGORIES — catches a NEW
# spelling of a KNOWN family (e.g. "MTCE-DISCONNECT", "TRAFO 2 FLT") without
# needing a fresh dict entry for every variant TCN staff invent. Checked
# against the raw code with all punctuation/spaces stripped (so "E/F",
# "E-F", "E F" are all just "EF"), in priority order — most specific
# families first, broadest ("MTC"/"DISC") last, so e.g. "EMERG/DISC"
# (contains both "EMERG" and "DISC") correctly resolves to Emergency
# Disconnect rather than the more generic Maintenance/Disconnect bucket.
#
# Deliberately conservative: every fragment here was checked against the 27
# real codes confirmed 2026-08-18 plus the known garbage values ('N',
# 'EN/A', malformed numbers like '.0.1') to confirm no false positives — a
# 2-character fragment like 'LS' or 'OC' only ships once verified none of
# the OTHER known families accidentally contain it. Anything matching no
# fragment falls to 'Other/Unspecified' rather than a guessed category —
# an honest "unknown" is safer than dirty data baked into a report.
_FAULT_FRAGMENT_RULES: list[tuple[str, str]] = [
    ('EMERG',    'Emergency Disconnect'),
    ('EMG',      'Emergency Disconnect'),
    ('TRANSF',   'Transformer Fault'),
    ('TRAFO',    'Transformer Fault'),
    ('TR2',      'Transformer Fault'),
    ('TRF',      'Transformer Fault'),
    ('BREAKER',  'Breaker Fault'),
    ('BRKR',     'Breaker Fault'),
    ('CBF',      'Breaker Fault'),
    ('EARTH',    'Earth Fault'),
    ('EF',       'Earth Fault'),
    ('OVERCUR',  'Over Current'),
    ('OC',       'Over Current'),
    ('LOADSHED', 'Load Shedding'),
    ('SHED',     'Load Shedding'),
    ('LS',       'Load Shedding'),
    ('OOS',      'Out of Supply'),
    ('MTC',      'Maintenance / Disconnect'),
    ('MTN',      'Maintenance / Disconnect'),
    ('MAINT',    'Maintenance / Disconnect'),
    ('DISC',     'Maintenance / Disconnect'),
]
# Fragments shorter than this are too ambiguous to trust on their own (e.g.
# a bare 'N' or single stray letter) — only length-2+ fragments are used
# above anyway, but this also gates the MINIMUM length of the cleaned input
# itself, so single-character noise never reaches fragment matching at all.
_FAULT_FUZZY_MIN_LEN = 2


def categorize_fault_code(raw_code) -> str:
    """Normalise a raw fault_code string into one of the small, consistent
    categories in FAULT_CODE_CATEGORIES (or _FAULT_FRAGMENT_RULES as a fuzzy
    fallback), for grouping/reporting — never used to change what's stored,
    only how it's read back."""
    if not raw_code:
        return 'Other/Unspecified'
    raw = str(raw_code).strip().upper()
    if raw in FAULT_CODE_CATEGORIES:
        return FAULT_CODE_CATEGORIES[raw]

    cleaned = re.sub(r'[^A-Z0-9]', '', raw)
    if len(cleaned) < _FAULT_FUZZY_MIN_LEN:
        return 'Other/Unspecified'
    for fragment, category in _FAULT_FRAGMENT_RULES:
        if fragment in cleaned:
            return category
    return 'Other/Unspecified'


def _repair_malformed_decimal(s: str):
    """Recover a real number from a stray-extra-decimal-point typo, e.g.
    '.0.1' or '0..4' — both clearly meant a single decimal value ('0.1',
    '0.4') but picked up an extra '.' along the way. General rule, not a
    hardcoded fix for those two examples: only fires when the string is
    ENTIRELY digits and periods (nothing else — a real fault code always
    has letters, so this can never misfire on one) with more than one '.'.
    Treats the LAST '.' as the real decimal separator and drops the rest,
    keeping however many digits preceded it in their original position.
    Returns a float, or None if the string doesn't fit this exact pattern.
    """
    if not s or not all(c.isdigit() or c == '.' for c in s) or s.count('.') <= 1:
        return None
    last_dot = s.rfind('.')
    digits_before = sum(1 for c in s[:last_dot] if c.isdigit())
    digits_only = s.replace('.', '')
    if not digits_only:
        return None
    repaired = f'{digits_only[:digits_before]}.{digits_only[digits_before:]}'
    try:
        f = float(repaired)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except ValueError:
        return None


def _safe_float(val):
    if val is None:
        return None
    s = str(val).strip().replace(',', '')
    try:
        f = float(s)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (ValueError, TypeError):
        repaired = _repair_malformed_decimal(s)
        if repaired is not None:
            logger.debug('_safe_float: repaired malformed decimal %r -> %s', s, repaired)
        return repaired


def _is_blank(val):
    """True for empty/null/NaN — genuinely missing or not yet submitted."""
    if val is None:
        return True
    s = str(val).strip().upper()
    return not s or s == 'NAN'


def _sheet_day(name):
    m = re.match(r'^(\d+)', str(name).strip())
    return int(m.group(1)) if m else None


def _build_feeder_lookup():
    # Same exclusion as sync_11kv_sheet's feeder_map — see comment there.
    lookup = {}
    for f in Feeder.objects.filter(voltage_level='33kv').exclude(slug__in=DUPLICATE_FEEDER_SLUGS):
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
                mw = _safe_float(val)
                fault_code = None
                if mw is None:
                    # Any non-blank, non-numeric cell is a fault/outage/
                    # maintenance/disconnect code — the sheet uses dozens of
                    # ad hoc abbreviations for this (EF, O/C, TR2/F,
                    # MTC/DISC, DISCO MAINT, EMERG/DISC, ...) and new
                    # variants keep appearing. Rather than whack-a-moling a
                    # literal whitelist (confirmed 2026-08-18: 27 distinct
                    # unrecognised codes across just one month, effectively
                    # all fault/maintenance-related), treat any such value
                    # as 0 MW so the feeder still gets a real HourlyLoad row
                    # instead of that hour being silently dropped — this was
                    # producing full-day gaps for feeders like NB CERAMIC
                    # (TR2/F all day) and partial gaps for many others
                    # (O/C, EF) that FAULT_PREFIXES didn't happen to cover.
                    # The raw code itself is kept (not just flattened to 0)
                    # so reporting can say WHY a feeder had no supply, not
                    # just that it didn't.
                    mw = 0.0
                    fault_code = str(val).strip()[:32]
                hl_rows.append((feeder, h_idx, round(max(mw, 0.0), 4), fault_code))

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

        # Replace physically impossible MW values with the day average of valid hours
        if dispatch_hourly:
            _impute_dispatch_outliers(dispatch_hourly, dispatch_daily)

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
                    # Protect DSO submissions: only remove stale admin_override rows.
                    # DSO data wins — never overwrite it with sheet data — UNLESS
                    # DataNest's own value for that hour is exactly 0. A 0 there
                    # is far more often a DataNest-side gap than a real reading
                    # (confirmed 2026-08-17: several feeders had a full 24/24-hour
                    # wall of DataNest zeros while the source sheet showed real,
                    # multi-MW generation for those same hours) — trusting it
                    # blindly was hiding real data. This re-checks every sync pass
                    # (force=True), so once DataNest reports a genuine non-zero
                    # value, it correctly takes back over immediately; until then,
                    # the sheet's value keeps refreshing this slot instead of being
                    # permanently blocked by a stale zero-claim.
                    existing_dso_keys = set(
                        HourlyLoad.objects.filter(
                            date=reading_date,
                            feeder_id__in=feeder_ids,
                            submission_type='dso',
                            load_mw__gt=0,
                        ).values_list('feeder_id', 'hour')
                    )
                    # Also clears out any stale zero-value dso rows for slots
                    # we're about to fill from the sheet — HourlyLoad has a
                    # unique (feeder, date, hour) constraint regardless of
                    # submission_type, so the old dso=0 row must go before the
                    # new admin_override row can be inserted for that slot.
                    HourlyLoad.objects.filter(
                        date=reading_date,
                        feeder_id__in=feeder_ids,
                    ).filter(
                        Q(submission_type='admin_override') | Q(submission_type='dso', load_mw=0)
                    ).delete()
                    HourlyLoad.objects.bulk_create([
                        HourlyLoad(
                            feeder=feeder,
                            date=reading_date,
                            hour=hour,
                            load_mw=Decimal(str(mw)),
                            submission_type='admin_override',
                            fault_code=fault_code,
                        )
                        for feeder, hour, mw, fault_code in hl_rows
                        if (feeder.id, hour) not in existing_dso_keys
                    ], batch_size=500)
                    total_hl += len(hl_rows)

                    # Derive supply hours: count hours with load_mw > 0 per feeder
                    supply_hrs: dict = {}
                    for feeder, hour, mw, _fault_code in hl_rows:
                        if mw > 0:
                            supply_hrs[feeder] = supply_hrs.get(feeder, 0) + 1
                    if supply_hrs:
                        DailyHoursOfSupply.objects.bulk_create(
                            [
                                DailyHoursOfSupply(feeder=feeder, date=reading_date, hours_supplied=hrs)
                                for feeder, hrs in supply_hrs.items()
                            ],
                            update_conflicts=True,
                            unique_fields=['feeder', 'date'],
                            update_fields=['hours_supplied'],
                            batch_size=500,
                        )

                if dispatch_daily:
                    _zero = Decimal('0')
                    daily_obj, _ = TMONetworkDispatch.objects.update_or_create(
                        date=reading_date,
                        defaults={
                            # NOT NULL fields — fall back to 0 when sheet has no data
                            'disco_offtake_mw':        _safe_mw(dispatch_daily.get('disco_offtake'), 'disco_offtake') or _zero,
                            'kedco_allocation_mw':     _safe_mw(dispatch_daily.get('kedco_alloc'), 'kedco_alloc') or _zero,
                            'variance_mw':             _safe_mw(dispatch_daily.get('variance'), 'variance') or _zero,
                            # NOT NULL (default=0) — TMONetworkDispatchHourly's version of this
                            # field is nullable, this daily one is not; fall back like the rest
                            'available_generation_mw': _safe_mw(dispatch_daily.get('available_gen'), 'available_gen') or _zero,
                            'source': 'manual',
                        }
                    )
                    TMONetworkDispatchHourly.objects.filter(date=reading_date).delete()
                    TMONetworkDispatchHourly.objects.bulk_create([
                        TMONetworkDispatchHourly(
                            dispatch=daily_obj,
                            date=reading_date,
                            hour=hr,
                            disco_offtake_mw=         _safe_mw(vals.get('disco_offtake'), 'disco_offtake'),
                            kedco_allocation_mw=      _safe_mw(vals.get('kedco_alloc'), 'kedco_alloc'),
                            available_generation_mw=  _safe_mw(vals.get('available_gen'), 'available_gen'),
                            variance_mw=              _safe_mw(vals.get('variance'), 'variance'),
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


# ══════════════════════════════════════════════════════════════════════════════
# 11KV LOAD FLOW SYNC
# ══════════════════════════════════════════════════════════════════════════════

# Column layout for the 11KV Google Sheet
# Row 0: headers  — col 0=STATION, col 1=TR RATING, col 2=TR PEAK LOAD,
#                   col 3=Feeder, col 4=Band,
#                   cols 5-28 = hours 1:00…0:00  (h_idx 0-23 → HourlyLoad.hour 0-23)
#                   cols 29-34 = MIN/AVE/PEAK/PREV ENERGY/PRESENT ENERGY/VARIANCE
FEEDER_COL_11KV = 3
HOUR_COLS_11KV  = list(range(5, 29))   # 24 hour columns
# The sheet's own PRESENT − PREVIOUS energy figure — a real metered value, far
# more accurate than reconstructing energy from an avg-load × supply-hours
# estimate over the 24 hour columns. Used as EnergyDelivered whenever a feeder
# has no CumulativeMeterReading-based reading for the day (see write block).
ENERGY_VARIANCE_COL_11KV = 34
PREVIOUS_ENERGY_COL_11KV = 32
PRESENT_ENERGY_COL_11KV  = 33

# Rows to ignore — transformer instrument rows and summary totals
SKIP_NAMES_11KV = frozenset({
    'WINDING TEMP.', 'OIL TEMP.', 'TAP POSITION', 'TAP/WINDING/OIL',
    'TOTAL 11KV FEEDER LOAD', 'TOTAL FEEDER LOAD',
})

# Known sheet-name → DB-name aliases for 11KV feeders (add as needed)
NAME_MAP_11KV: dict[str, str] = {
    'ASIAN PLASTICS': 'ASIAN PLASTIC',  # sheet has extra S
}


def _clean_11kv_name(raw: str) -> str:
    """Strip '11KV '/'33KV ' voltage prefix and upper-case."""
    s = raw.strip()
    for prefix in ('11KV ', '33KV ', '11 KV ', '33 KV '):
        if s.upper().startswith(prefix):
            s = s[len(prefix):]
            break
    return s.strip().upper()


def _sheet_day_11kv(title: str) -> int | None:
    """
    Return day number only for sheets named like '01', '28th', '29'.
    Rejects 'Copy of 18', 'SheetXX', 'Dashboard', '000', etc.
    """
    m = re.fullmatch(r'(\d+)(st|nd|rd|th)?', title.strip(), re.IGNORECASE)
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 31 else None


def _days_needing_sync_11kv(worksheets, year, month, force=False, only_day=None):
    today = date.today()
    recency_cutoff = today - timedelta(days=RECENT_RESYNC_DAYS - 1)
    from common.models import Feeder as _Feeder
    total_11kv = _Feeder.objects.filter(voltage_level='11kv').count()
    min_feeders = max(10, int(total_11kv * 0.70))

    needs = []
    for ws in worksheets:
        day = _sheet_day_11kv(ws.title)
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
            count = (
                HourlyLoad.objects
                .filter(date=d, feeder__voltage_level='11kv', submission_type='admin_override')
                .values('feeder_id').distinct().count()
            )
            if count >= min_feeders:
                continue
        needs.append((ws, d))
    return needs


def sync_11kv_sheet(spreadsheet_id: str, year: int, month: int,
                    force: bool = False, dry_run: bool = False,
                    only_day: int = None, log_fn=None) -> dict:
    """
    Sync the 11KV load-flow Google Sheet into HourlyLoad + DailyHoursOfSupply.

    Rules:
    - Any non-numeric cell value → 0 MW (blank, fault code, any string)
    - Both 11KV and 33KV feeder rows in the sheet are processed
    - Slots that already have a 'dso' (DataNest) submission are skipped —
      DataNest owns those; we only fill gaps for non-DataNest feeders
    - DailyHoursOfSupply is updated for every feeder we write

    Returns: {'days_synced', 'hl_rows', 'unmatched', 'errors'}
    """
    if log_fn is None:
        log_fn = logger.info

    # Excludes confirmed phantom duplicate feeder records — a plain name-keyed dict
    # would otherwise let a real feeder's data silently land on its excluded
    # duplicate whenever two records share the exact same name (e.g. RANGAZA has
    # both KN-SMA-RAN and the phantom KN-DAK-RAN; without this exclusion, sheet
    # rows for "RANGAZA" could resolve to the excluded record and vanish from
    # every dashboard total that filters DUPLICATE_FEEDER_SLUGS out).
    feeder_map = {
        f.name.upper(): f
        for f in Feeder.objects.filter(is_onboarded=True).exclude(slug__in=DUPLICATE_FEEDER_SLUGS)
    }

    gc = _get_gspread_client()
    sh = gc.open_by_key(spreadsheet_id)
    worksheets = sh.worksheets()

    needs = _days_needing_sync_11kv(worksheets, year, month, force=force, only_day=only_day)
    log_fn(f'11KV sync: {len(needs)} day(s) to process for {year}-{month:02d}')

    days_synced = 0
    total_hl    = 0
    total_ed    = 0
    unmatched:  set  = set()
    errors:     list = []

    for ws, reading_date in needs:
        try:
            rows = ws.get_all_values()
        except Exception as exc:
            errors.append(f'{reading_date}: fetch error: {exc}')
            continue

        if not rows:
            continue

        # ── Parse feeder rows ────────────────────────────────────────────────
        hl_rows: list[tuple] = []   # (feeder, h_idx, mw)
        variance_rows: dict  = {}   # feeder -> energy_mwh (last one wins if dup rows)
        day_unmatched: list  = []

        for row in rows[1:]:
            if len(row) <= FEEDER_COL_11KV:
                continue
            raw_name = row[FEEDER_COL_11KV].strip()
            if not raw_name:
                continue

            clean = _clean_11kv_name(raw_name)
            if clean in SKIP_NAMES_11KV or clean.startswith('TOTAL'):
                continue

            clean = NAME_MAP_11KV.get(clean, clean)
            feeder = feeder_map.get(clean)
            if not feeder:
                if clean not in day_unmatched:
                    day_unmatched.append(clean)
                continue

            for h_idx, col in enumerate(HOUR_COLS_11KV):
                val = row[col] if col < len(row) else ''
                mw  = _safe_float(val)
                fault_code = None
                if mw is None:
                    mw = 0.0        # non-numeric (blank, fault code, etc.) → 0
                    raw = str(val).strip()
                    if raw:
                        fault_code = raw[:32]   # blank cells stay fault_code=None
                hl_rows.append((feeder, h_idx, round(max(mw, 0.0), 4), fault_code))

            variance_val = row[ENERGY_VARIANCE_COL_11KV] if ENERGY_VARIANCE_COL_11KV < len(row) else ''
            variance_mwh = _safe_float(variance_val)

            # Fallback for rows where the sheet's own VARIANCE cell is blank
            # (formula never filled in for that feeder) even though PREVIOUS/
            # PRESENT ENERGY are populated — e.g. Haske Solar, whose variance
            # column has been empty every day despite real readings sitting
            # right next to it. Different feeders' meters report cumulative
            # energy on different scales (most feeders: 1 unit = 1 MWh; some,
            # like Haske's solar meter: 1 unit = 1 kWh), so try the raw diff
            # first and only fall back to a /1000 scale if the raw diff itself
            # isn't a plausible daily MWh figure.
            if variance_mwh is None:
                prev_val = row[PREVIOUS_ENERGY_COL_11KV] if PREVIOUS_ENERGY_COL_11KV < len(row) else ''
                pres_val = row[PRESENT_ENERGY_COL_11KV] if PRESENT_ENERGY_COL_11KV < len(row) else ''
                prev_e, pres_e = _safe_float(prev_val), _safe_float(pres_val)
                if prev_e is not None and pres_e is not None:
                    raw_diff = pres_e - prev_e
                    if 0 <= raw_diff <= DAILY_BALLOON_LIMIT:
                        variance_mwh = raw_diff
                    elif 0 <= raw_diff / 1000 <= DAILY_BALLOON_LIMIT:
                        variance_mwh = raw_diff / 1000

            # Reject garbage sheet values before they ever reach the DB — a bad
            # PREVIOUS/PRESENT ENERGY entry in the source sheet (meter reset,
            # typo, blank treated as 0) can produce an absurd variance. Same
            # sanity bound as the balloon-limit check used everywhere else for
            # meter-derived energy: negative or implausibly large is rejected,
            # not saved and silently corrupting downstream totals.
            if variance_mwh is not None and 0 <= variance_mwh <= DAILY_BALLOON_LIMIT:
                variance_rows[feeder] = round(variance_mwh, 2)

        unmatched.update(day_unmatched)

        if dry_run:
            log_fn(
                f'  [DRY] {reading_date}: {len(hl_rows)} HL rows'
                + (f'  | unmatched: {day_unmatched}' if day_unmatched else '')
            )
            days_synced += 1
            continue

        # ── Write ────────────────────────────────────────────────────────────
        try:
            with transaction.atomic():
                rows_written = 0

                if hl_rows:
                    all_feeder_ids = {r[0].id for r in hl_rows}

                    # Slots already owned by DataNest — never overwrite, UNLESS
                    # DataNest's own value is exactly 0 (near-always a DataNest-
                    # side gap rather than a real reading — see matching comment
                    # in sync_33kv_sheet above for the confirmed evidence). Real
                    # non-zero DataNest data still always wins.
                    existing_dso_keys = set(
                        HourlyLoad.objects
                        .filter(
                            date=reading_date,
                            feeder_id__in=all_feeder_ids,
                            submission_type='dso',
                            load_mw__gt=0,
                        )
                        .values_list('feeder_id', 'hour')
                    )

                    rows_to_write = [
                        (f, h, mw, fc) for f, h, mw, fc in hl_rows
                        if (f.id, h) not in existing_dso_keys
                    ]

                    if rows_to_write:
                        write_feeder_ids = list({r[0].id for r in rows_to_write})

                        # Replace our own admin_override rows for this date —
                        # also clears stale zero-value dso rows for slots we're
                        # about to fill (unique (feeder, date, hour) constraint
                        # applies regardless of submission_type, so the old
                        # dso=0 row must go before the new one can be inserted).
                        HourlyLoad.objects.filter(
                            date=reading_date,
                            feeder_id__in=write_feeder_ids,
                        ).filter(
                            Q(submission_type='admin_override') | Q(submission_type='dso', load_mw=0)
                        ).delete()

                        HourlyLoad.objects.bulk_create([
                            HourlyLoad(
                                feeder=feeder,
                                date=reading_date,
                                hour=h_idx,
                                load_mw=Decimal(str(mw)),
                                submission_type='admin_override',
                                fault_code=fault_code,
                            )
                            for feeder, h_idx, mw, fault_code in rows_to_write
                        ], batch_size=500)
                        rows_written = len(rows_to_write)
                        total_hl += rows_written

                        # Update DailyHoursOfSupply for feeders we wrote
                        supply_hrs: dict = {}
                        for feeder, h_idx, mw, _fault_code in rows_to_write:
                            if mw > 0:
                                supply_hrs[feeder] = supply_hrs.get(feeder, 0) + 1
                        # Feeders with all-zero/fault hours still get 0 hours recorded
                        all_written_feeders = {r[0] for r in rows_to_write}
                        if all_written_feeders:
                            DailyHoursOfSupply.objects.bulk_create(
                                [
                                    DailyHoursOfSupply(
                                        feeder=feeder, date=reading_date,
                                        hours_supplied=supply_hrs.get(feeder, 0),
                                    )
                                    for feeder in all_written_feeders
                                ],
                                update_conflicts=True,
                                unique_fields=['feeder', 'date'],
                                update_fields=['hours_supplied'],
                                batch_size=500,
                            )

                # ── ENERGY VARIANCE → EnergyDelivered ───────────────────────
                # Only for feeders with no ALREADY-COMPUTED energy value for
                # this date — a real meter-difference or manual reading takes
                # priority whenever one exists (mirrors TCN's own "bulk
                # parent" vs "standalone lookup" row-formula split).
                #
                # Deliberately checks EnergyDelivered, not CumulativeMeterReading
                # — a feeder can have a CMR row for this date (e.g. a lone
                # DataNest submission with no adjacent-day reading to diff
                # against) without ever producing a real EnergyDelivered value.
                # Gating on CMR existence alone left feeders in that state with
                # NO energy value at all: not the correct meter-difference
                # (nothing to diff against yet) and not our own sheet-variance
                # estimate either (blocked by the CMR check). Confirmed
                # 2026-08-08: 11KV Ahmadu Bello had a dso CumulativeMeterReading
                # for Aug 1 but zero EnergyDelivered, silently dropping its real
                # 13.5 MWh sheet-variance value. Gating on EnergyDelivered
                # existence instead still lets a later, real meter-difference
                # calculation (once CumulativeMeterReading.save() has both days
                # to diff) overwrite this estimate, since that write isn't
                # gated by calculation_method at all.
                ed_written = 0
                if variance_rows:
                    # 'estimated' included alongside the real-data methods —
                    # it means a confirmed-bad raw reading was deliberately
                    # hand-corrected after investigation, not that no real
                    # value exists yet. Without it here, the next sync would
                    # silently recompute and overwrite the same bad value
                    # from the same uncorrected source sheet.
                    protected = set(
                        EnergyDelivered.objects
                        .filter(
                            date=reading_date,
                            feeder__in=variance_rows.keys(),
                            calculation_method__in=['meter_difference', 'manual_entry', 'estimated'],
                        )
                        .values_list('feeder_id', flat=True)
                    )
                    ed_to_write = [
                        EnergyDelivered(
                            feeder=feeder, date=reading_date, energy_mwh=Decimal(str(mwh)),
                            is_calculated=True, calculation_method='sheet_variance',
                        )
                        for feeder, mwh in variance_rows.items()
                        if feeder.id not in protected
                    ]
                    if ed_to_write:
                        EnergyDelivered.objects.bulk_create(
                            ed_to_write,
                            update_conflicts=True,
                            unique_fields=['feeder', 'date'],
                            update_fields=['energy_mwh', 'calculation_method', 'is_calculated'],
                            batch_size=500,
                        )
                        ed_written = len(ed_to_write)
                        total_ed  += ed_written

            days_synced += 1
            log_fn(
                f'  {reading_date}: {rows_written} HL, {ed_written} ED(variance)'
                + (f'  | unmatched: {day_unmatched}' if day_unmatched else '')
            )
        except Exception as exc:
            errors.append(f'{reading_date}: {exc}')
            logger.exception(f'11KV error syncing {reading_date}')

    return {
        'days_synced': days_synced,
        'hl_rows':     total_hl,
        'ed_rows':     total_ed,
        'unmatched':   sorted(unmatched),
        'errors':      errors,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 33KV ENERGY ACCOUNTING SYNC
# ══════════════════════════════════════════════════════════════════════════════
#
# Sheet layout (per day tab — e.g. "1st", "2nd", …):
#   Row 0: blank
#   Row 1: hour labels (01:00 … 24:00) — informational only
#   Row 2: column headers
#   Row 3+: one data row per 33KV feeder
#
#   Col 0:  REGION          (skip row if starts with 'TOTAL')
#   Col 7:  ASSOCIATED 33KV FEEDER  ← feeder name
#   Col 11: METER READING AT 00:00HRS MWH  ← stored as CumulativeMeterReading
#   Cols 13, 17, 21, ... (every 4th, 24 of them): PRESENT METER READING MWH for
#     each hour 1-24 — the last non-blank one is that day's closing reading.
#
# We store col 11 as CumulativeMeterReading(feeder, reading_date) for reference,
# but EnergyDelivered is computed as a WITHIN-DAY diff — that day's own closing
# reading (last hourly "PRESENT METER READING") minus that same day's opening
# reading (col 11) — NOT a cross-day diff against yesterday's CumulativeMeterReading.
# Confirmed against TCN's own ground-truth workbook (Aug 2026, 6 days): TCN's real
# per-feeder figures match the within-day diff, not the cross-day one — e.g. Small
# Scale Aug 2 is 138 MWh (within-day) vs 72 MWh (cross-day, what this sync used to
# compute). The cross-day method also depends on two separate days' 00:00 readings
# both being entered consistently, which is what caused the month-boundary
# "copy-paste" bug (see project memory) — the within-day method is self-contained
# per day and doesn't have that failure mode.
#
# DSO data wins: if a 'dso' CumulativeMeterReading already exists for the
# same (feeder, date), we skip that row — the DSO submission is authoritative.

_ENERGY_FEEDER_COL  = 7   # "ASSOCIATED 33KV FEEDER"
_ENERGY_READING_COL = 11  # "METER READING AT 00:00HRS MWH" (opening)
_ENERGY_PRESENT_COLS = [13 + 4 * i for i in range(24)]  # hourly "PRESENT METER READING MWH"


def _days_needing_sync_33kv_energy(worksheets, year, month, force=False, only_day=None):
    today = date.today()
    recency_cutoff = today - timedelta(days=RECENT_RESYNC_DAYS - 1)
    total_33kv = Feeder.objects.filter(voltage_level='33kv').count()
    min_feeders = max(5, int(total_33kv * 0.70))

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
            count = (
                CumulativeMeterReading.objects
                .filter(reading_date=d, feeder__voltage_level='33kv',
                        submission_type='admin_override')
                .count()
            )
            if count >= min_feeders:
                continue
        needs.append((ws, d))
    return needs


def sync_33kv_energy_sheet(spreadsheet_id: str, year: int, month: int,
                            force: bool = False, dry_run: bool = False,
                            only_day: int = None, log_fn=None) -> dict:
    """
    Sync the 33KV Energy Accounting Google Sheet into CumulativeMeterReading
    (and via its save(), into EnergyDelivered).

    Rules:
    - One row per 33KV feeder; col 7 = feeder name, col 11 = 00:00 cumulative MWh
    - Rows where col 0 starts with 'TOTAL', or col 7/11 is blank, are skipped
    - Feeders not in Raven DB are logged as unmatched (non-KEDCO feeders)
    - DSO submission wins: existing 'dso' CumulativeMeterReading is never overwritten
    - Otherwise: update_or_create with submission_type='admin_override'

    Returns: {'days_synced', 'cmr_rows', 'unmatched', 'errors'}
    """
    if log_fn is None:
        log_fn = logger.info

    feeder_lookup = _build_feeder_lookup()   # 33KV feeders only

    gc = _get_gspread_client()
    sh = gc.open_by_key(spreadsheet_id)
    worksheets = sh.worksheets()

    needs = _days_needing_sync_33kv_energy(
        worksheets, year, month, force=force, only_day=only_day
    )
    log_fn(f'33KV energy sync: {len(needs)} day(s) to process for {year}-{month:02d}')

    days_synced = 0
    total_cmr   = 0
    unmatched:  set  = set()
    errors:     list = []

    for ws, reading_date in needs:
        try:
            rows = ws.get_all_values()
        except Exception as exc:
            errors.append(f'{reading_date}: fetch error: {exc}')
            continue

        # Data starts at row 3 (0-indexed) — rows 0-2 are headers
        data_rows = rows[3:] if len(rows) > 3 else []
        if not data_rows:
            continue

        # ── Parse ─────────────────────────────────────────────────────────────
        parsed: list[tuple] = []   # (feeder, opening_mwh, closing_mwh_or_None)
        day_unmatched: list = []

        for row in data_rows:
            # Skip total/summary rows
            region_cell = row[0].strip().upper() if row else ''
            if not region_cell or region_cell.startswith('TOTAL'):
                continue

            if len(row) <= _ENERGY_FEEDER_COL:
                continue
            raw_name = row[_ENERGY_FEEDER_COL].strip()
            if not raw_name:
                continue

            if len(row) <= _ENERGY_READING_COL:
                continue
            raw_reading = row[_ENERGY_READING_COL].strip()
            if not raw_reading:
                continue

            mwh = _safe_float(raw_reading)
            if mwh is None:
                continue   # blank or non-numeric — no reading yet for this day

            feeder = _find_feeder(raw_name, feeder_lookup)
            if not feeder:
                if raw_name.upper() not in day_unmatched:
                    day_unmatched.append(raw_name.upper())
                continue

            closing_mwh = None
            for col in reversed(_ENERGY_PRESENT_COLS):
                if col < len(row):
                    v = _safe_float(row[col])
                    if v is not None:
                        closing_mwh = v
                        break

            parsed.append((feeder, Decimal(str(round(mwh, 4))), closing_mwh))

        unmatched.update(day_unmatched)

        if dry_run:
            log_fn(
                f'  [DRY] {reading_date}: {len(parsed)} CMR rows'
                + (f'  | unmatched: {day_unmatched}' if day_unmatched else '')
            )
            days_synced += 1
            continue

        # ── Write ─────────────────────────────────────────────────────────────
        written = 0
        try:
            # Fetch existing DSO readings for this date+feeder set in one query
            feeder_ids = [f.id for f, _, _ in parsed]
            dso_feeder_ids = set(
                CumulativeMeterReading.objects
                .filter(reading_date=reading_date, feeder_id__in=feeder_ids,
                        submission_type='dso')
                .values_list('feeder_id', flat=True)
            )

            to_write = [(feeder, mwh, closing) for feeder, mwh, closing in parsed if feeder.id not in dso_feeder_ids]

            if to_write:
                CumulativeMeterReading.objects.bulk_create(
                    [
                        CumulativeMeterReading(
                            feeder=feeder,
                            reading_date=reading_date,
                            cumulative_mwh=mwh,
                            submission_type='admin_override',
                            is_estimated=False,
                            notes='Synced from 33KV Energy Accounting Google Sheet',
                        )
                        for feeder, mwh, _ in to_write
                    ],
                    update_conflicts=True,
                    unique_fields=['feeder', 'reading_date'],
                    update_fields=['cumulative_mwh', 'submission_type', 'is_estimated', 'notes'],
                    batch_size=500,
                )
                written = len(to_write)

                # EnergyDelivered = today's own closing reading (last hourly "PRESENT
                # METER READING") minus today's own opening reading — a within-day
                # diff, self-contained on this single sheet row. See module comment
                # above for why this replaced the old cross-day CMR diff.
                #
                # Never overwrites a row already marked calculation_method='estimated'
                # — that means someone deliberately corrected a confirmed-bad raw
                # reading (e.g. a corrupted meter value far outside the feeder's own
                # history) after investigating it by hand. Without this check, the
                # very next hourly run would silently recompute the same bad value
                # from the same uncorrected source sheet and overwrite the fix —
                # confirmed 2026-08-11 this would have happened to several feeders
                # corrected this way (Dundu, Bompai, Tokarawa) within the hour.
                protected_dates = set(
                    EnergyDelivered.objects
                    .filter(date=reading_date, feeder_id__in=[f.id for f, _, _ in to_write], calculation_method='estimated')
                    .values_list('feeder_id', flat=True)
                )
                ed_rows = []
                for feeder, opening, closing in to_write:
                    if closing is None or feeder.id in protected_dates:
                        continue
                    diff = Decimal(str(round(closing, 4))) - opening
                    if diff < 0:
                        continue
                    ed_rows.append(EnergyDelivered(
                        feeder=feeder, date=reading_date, energy_mwh=diff,
                        is_calculated=True, calculation_method='meter_difference',
                    ))
                if ed_rows:
                    EnergyDelivered.objects.bulk_create(
                        ed_rows,
                        update_conflicts=True,
                        unique_fields=['feeder', 'date'],
                        update_fields=['energy_mwh', 'is_calculated', 'calculation_method'],
                        batch_size=500,
                    )

            total_cmr   += written
            days_synced += 1
            log_fn(
                f'  {reading_date}: {written} CMR rows'
                + (f'  | unmatched: {day_unmatched}' if day_unmatched else '')
            )
        except Exception as exc:
            errors.append(f'{reading_date}: {exc}')
            logger.exception(f'33KV energy error syncing {reading_date}')

    return {
        'days_synced': days_synced,
        'cmr_rows':    total_cmr,
        'unmatched':   sorted(unmatched),
        'errors':      errors,
    }


# ══════════════════════════════════════════════════════════════════════════════
# COVERAGE AUDIT — shared by audit_hourly_load_coverage (management command)
# and the daily Celery beat task, so both report the exact same thing.
# ══════════════════════════════════════════════════════════════════════════════

def audit_hourly_load_coverage(year: int, month: int, voltage_level: str) -> dict:
    """
    For every onboarded feeder at the given voltage level, check whether it has
    ANY real (>0) HourlyLoad reading across the given month (up to today, for
    the current month). A feeder with zero real readings for the whole period
    almost always means a name-matching/skip-list bug silently discarding its
    rows — a full-month outage is implausible — same pattern that hid
    GAGARAWA's real data for as long as it did.

    Returns {'total': N, 'zero_coverage': [{'name', 'slug', 'skip_hit'}, ...]}
    where skip_hit is the matching SKIP_PREFIXES entry if found (the exact
    GAGARAWA bug pattern), else None.
    """
    from common.models import Feeder
    from technical.models import HourlyLoad

    from_date = date(year, month, 1)
    to_date = min(
        date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1),
        date.today(),
    )

    feeders = list(Feeder.objects.filter(is_onboarded=True, voltage_level=voltage_level))
    covered_ids = set(
        HourlyLoad.objects.filter(
            feeder__in=feeders, date__gte=from_date, date__lte=to_date, load_mw__gt=0,
        ).values_list('feeder_id', flat=True).distinct()
    )

    zero_coverage = []
    for f in feeders:
        if f.id in covered_ids:
            continue
        name_upper = f.name.upper()
        skip_hit = next((p for p in SKIP_PREFIXES if name_upper.startswith(p)), None)
        zero_coverage.append({'name': f.name, 'slug': f.slug, 'skip_hit': skip_hit})

    return {'total': len(feeders), 'zero_coverage': zero_coverage}
