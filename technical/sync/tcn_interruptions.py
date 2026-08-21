# technical/sync/tcn_interruptions.py
"""
Sync: TCN's own 33kV Feeder Reliability fault-log Google Sheet ->
Raven FeederInterruption (source='tcn').

Companion to technical/sync/interruptions.py (DataNest/DSO, 11kV-only,
confirmed zero 33kV coverage) -- TCN's own sheet is the only source Raven
has for 33kV interruptions at all. Parsing/matching logic here is shared
with (and was originally developed + verified against the full historical
dataset by) technical/management/commands/backfill_tcn_interruptions.py,
which uses this same module for its one-off Jan-Aug 2026 backfill.

Sheet layout (21 cols), one row per outage event:
  Disco, Region, SubRegion/ACC, Substation, 33kV Feeder,
  Date off, Hour Off, Minute off, Date on, Hour On, Minute on,
  Duration x2 (recomputed from timestamps, not trusted from the sheet),
  Class (Forced/Emergency/Planned), Last Load Recorded (MW),
  Event/Indication (fault code + party suffix), Party Responsible,
  Officer Confirming Interruption, Officer Confirming Restoration,
  Weather Condition, Remarks

Hour Off/On + Minute off/on quirk (confirmed against the sheet's own
Duration column on real rows before trusting this): "Hour Off" is a
time-formatted cell holding just the hour (e.g. "8:00:00" -> hour=8),
"Minute off" is ALSO time-formatted but really holds only minutes to ADD
(e.g. "0:11" -> +11 minutes). Combined: Date off + hour + minutes = the
real timestamp.

Unlike the DSO sync, this pulls from a human-maintained Google Sheet, not
a queryable DB table, so it works differently:
  - Only the CURRENT and PREVIOUS month's tab are checked each run (covers
    both new rows added late in a month and restorations reported/entered
    early the next month). Older tabs are assumed settled after the one-off
    backfill and aren't rescanned every run.
  - New rows are created via get_or_create() (normal .save() path), NOT
    bulk_create() -- unlike the historical backfill, genuinely new TCN
    faults SHOULD fire the usual FeederInterruption post_save notification,
    same as live DSO-sourced faults do.
  - No stale-delete pass: a spreadsheet is human-edited and typo-prone, so
    pruning Raven rows that vanish from the sheet is left as a deliberate/
    manual action rather than automatic.
"""
import re
from datetime import datetime, timedelta

from dateutil import parser as dtparser
from django.utils import timezone

from common.models import Feeder
from technical.models import FeederInterruption
from tmo.sheet_sync import NAME_MAP

SPREADSHEET_ID = '1viusCmn8GHKSUtzlB0Clu7iLzl_AmYCzPoL98UuDqrQ'


def _current_spreadsheet_id(year=None):
    """
    Prefer the year-scoped registration in GoogleSheetFeed
    (feed_type='tcn_33kv_fault_log') -- registered/updated via
    /api/tmo/sheet-feeds/ same as the 33kv/11kv feeds, one row per YEAR
    (month is null) since TCN publishes a single spreadsheet per year.
    Falls back to the hardcoded SPREADSHEET_ID above if no active
    registration exists yet for that year (e.g. before it's first
    registered, or a year TCN hasn't published a new sheet for).
    """
    from tmo.models import GoogleSheetFeed

    year = year or timezone.now().year
    feed = GoogleSheetFeed.objects.filter(
        feed_type='tcn_33kv_fault_log', year=year, month=None, is_active=True
    ).first()
    return feed.spreadsheet_id if feed else SPREADSHEET_ID

_MONTH_NUM = {name: i + 1 for i, name in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
)}
_MONTH_NAME = {v: k for k, v in _MONTH_NUM.items()}

# Direct map, built from the actual complete list of 31 distinct
# Event/Indication values across all 8 months (checked before writing this,
# not guessed) -> Raven's INTERRUPTION_TYPES choices. Genuinely new TCN-only
# concepts (line trips, under-frequency ops, specific transformer protection
# devices) got real new choices added to the model rather than forced into
# an ill-fitting existing one. 'SBEF' is a genuinely ambiguous abbreviation
# (12 rows) — kept as raw text rather than guessed at.
_FAULT_CODE_MAP = {
    'L/SHED': 'L/S GS',
    'EF': 'E/F',
    'OC': 'O/C',
    'OC/EF': 'O/C & E/F',
    'EF/OC': 'O/C & E/F',
    'TCN MAINT': 'MTCE',
    'LINE LIM': 'LIM',
    'EMRG': 'EM/D',
    '330LN TRIP': '330KV L/T',
    '132LN TRIP': '132KV L/T',
    'DISCO MAINT': 'MTNC',
    'INST EF': 'E/F',
    'TRF SEC OC': 'T/F',
    'INST OC/EF': 'O/C & E/F',
    'SYS DISTURB': 'SYS/D',
    '330TRF TRIP': '330KV T/T',
    '33KV U/FREQ OP': '33KV U/F',
    'INST OC': 'O/C',
    '132KV U/FREQ OP': '132KV U/F',
    'CB F': 'B/F',
    # Confirmed via remarks, not guessed: all 12 rows at the same substation
    # (Kumbotso 132kV), multiple feeders tripping together from one event,
    # remarks literally say "SEC CB TRIPPED ON SBEF" -- a bus-level earth
    # fault (Station/Sub-station Bus Earth Fault), matching B/F.
    'SBEF': 'B/F',
    'TRF SEC EF': 'T/F',
    'TRF LIM': 'LIM',
    'TRF BUCHHOLZ': 'BUCHHOLZ',
    'NO RELAY IND': 'NO RI',
    'TRF DIFF TRIP': 'TRF/DIFF',
    'HIGH WIND TEMP': 'O/N',
}

_PARTY_MAP = {'DISCO': 'DISCO', 'TCN': 'TCN', 'GENCO': 'GENCO'}
_CLASS_MAP = {'FORCED': 'FORCED', 'EMERGENCY': 'EMERGENCY', 'PLANNED': 'PLANNED'}

# Name variants confirmed unambiguous against Raven's actual feeder list —
# not guesses, checked one at a time (AJIWA/CHALLAWA/TAMBURAWA all had
# exactly one real onboarded match each).
_LOCAL_NAME_MAP = {
    'AJIWA': 'AJIWA WATER WORKS',
    'CHALLAWA': 'CHALLAWA WATER WORKS',
    'TAMBURAWA': 'TAMBURAWA  WATER WORKS',  # double space matches Raven's actual stored name
    'RICE MILL': 'RICE MILLS',  # confirmed via substation match (both Gagarawa 132kV) — not RICE FIELD (Tamburawa)
    'FEEDER 3': 'RICE FIELD',  # confirmed by user — "Feeder 3" is RICE FIELD's placeholder label at Tamburawa 132kV
}


def _normalise_fault_code(event_indication: str) -> str:
    """'EF (DISCO)' -> 'E/F'. Strips the trailing (PARTY)/(G/S) tag, direct
    lookup against the real observed vocabulary, else keeps the cleaned raw
    text as-is (never silently dropped, never guessed)."""
    raw = re.sub(r'\s*\([^)]*\)\s*$', '', (event_indication or '').strip()).upper()
    raw = re.sub(r'\s+', ' ', raw)
    if not raw:
        return 'N/A'
    return _FAULT_CODE_MAP.get(raw, raw)


def _parse_date_with_month_hint(date_val: str, expected_month: int):
    """
    The sheet mixes date formats WITHIN THE SAME TAB -- most rows are
    DD/MM/YYYY (e.g. '2/8/2026' = Aug 2) but some are MM/DD/YYYY
    (e.g. '8/3/2026', sitting chronologically between other Aug-2 and
    Aug-4 rows, really means Aug 3, not March 8). A blanket dayfirst=True
    assumption silently misparses these into the wrong month entirely.
    Confirmed by direct inspection of raw sheet rows (see the Aug 2026
    tab: '8/3/2026' rows sit between '2/8/2026' and '3/8/2026' rows).

    Parse both interpretations and prefer whichever's month matches the
    tab this row came from (the tab name is a reliable anchor -- rows are
    only ever plausibly the tab's own month or immediately adjacent, e.g.
    a Jul-31-to-Aug-1 boundary event logged in the Aug tab).
    """
    s = str(date_val).strip()
    if not s:
        return None
    try:
        d1 = dtparser.parse(s, dayfirst=True).date()
    except (ValueError, TypeError):
        d1 = None
    try:
        d2 = dtparser.parse(s, dayfirst=False).date()
    except (ValueError, TypeError):
        d2 = None
    candidates = [d for d in (d1, d2) if d is not None]
    if not candidates:
        return None
    if d1 == d2 or len(candidates) == 1:
        return candidates[0]

    def month_distance(m):
        diff = abs(m - expected_month)
        return min(diff, 12 - diff)

    candidates.sort(key=lambda d: month_distance(d.month))
    return candidates[0]


def _combine_timestamp(date_val: str, hour_val: str, minute_val: str, expected_month: int):
    """Date off/on + Hour Off/On (hour) + Minute off/on (extra minutes)."""
    if not date_val:
        return None
    base_date = _parse_date_with_month_hint(date_val, expected_month)
    if base_date is None:
        return None
    hour = 0
    if hour_val:
        try:
            hour = dtparser.parse(str(hour_val)).hour
        except (ValueError, TypeError):
            hour = 0
    minutes = 0
    if minute_val:
        try:
            parsed = dtparser.parse(str(minute_val))
            minutes = parsed.hour * 60 + parsed.minute
        except (ValueError, TypeError):
            minutes = 0
    naive = datetime.combine(base_date, datetime.min.time()) + timedelta(hours=hour, minutes=minutes)
    return timezone.make_aware(naive) if timezone.is_naive(naive) else naive


def _build_feeder_lookup():
    """
    33kV feeders, name -> {onboarded: [...], other: [...]}. Split rather
    than filtered outright: restricting to is_onboarded=True alone fixed
    WATARI's stale duplicate (one onboarded, one not) but also silently
    excluded DR JIMETA, which has no duplicate at all — it's just a single,
    real, not-yet-onboarded feeder with its own real synced data already.
    _find_feeder() below prefers the onboarded match when one exists, and
    only falls back to a non-onboarded one when it's unambiguous.
    """
    lookup = {}
    for f in Feeder.objects.filter(voltage_level='33kv'):
        key = re.sub(r'\s+', ' ', f.name.upper().strip())
        bucket = lookup.setdefault(key, {'onboarded': [], 'other': []})
        bucket['onboarded' if f.is_onboarded else 'other'].append(f)
    return lookup


def _find_feeder(raw_name, lookup):
    raw = str(raw_name or '').strip().upper()
    mapped = NAME_MAP.get(raw, _LOCAL_NAME_MAP.get(raw, raw))
    mapped = re.sub(r'\s+', ' ', mapped.strip())
    bucket = lookup.get(mapped)
    if not bucket:
        return None
    if len(bucket['onboarded']) == 1:
        return bucket['onboarded'][0]
    if not bucket['onboarded'] and len(bucket['other']) == 1:
        return bucket['other'][0]
    return None


def parse_row(row, expected_month, lookup):
    """
    Parse one 21-col sheet row into FeederInterruption field kwargs.
    Returns (fields_dict, None) on success, or (None, reason) where reason
    is one of 'blank_feeder' / 'unmatched_feeder:<name>' / 'bad_date'.
    """
    row = row + [''] * (21 - len(row))  # pad short rows
    feeder_name = row[4]
    if not feeder_name.strip():
        return None, 'blank_feeder'

    feeder = _find_feeder(feeder_name, lookup)
    if not feeder:
        return None, f'unmatched_feeder:{feeder_name.strip()}'

    try:
        occurred_at = _combine_timestamp(row[5], row[6], row[7], expected_month)
        restored_at = _combine_timestamp(row[8], row[9], row[10], expected_month)
    except Exception:
        return None, 'bad_date'
    if not occurred_at:
        return None, 'bad_date'

    fault_type = _normalise_fault_code(row[15])
    party = _PARTY_MAP.get(row[16].strip().upper())
    outage_class = _CLASS_MAP.get(row[13].strip().upper())
    try:
        load_mw = float(row[14]) if row[14].strip() else None
    except ValueError:
        load_mw = None

    return {
        'feeder': feeder,
        'interruption_type': fault_type,
        'description': row[20] or None,
        'occurred_at': occurred_at,
        'restored_at': restored_at,
        'source': 'tcn',
        'party_responsible': party,
        'outage_class': outage_class,
        'load_at_fault_mw': load_mw,
        'weather_condition': row[19] or None,
        'officer_confirming_interruption': row[17] or None,
        'officer_confirming_restoration': row[18] or None,
    }, None


def _recent_month_tabs(now=None):
    """
    Current + previous month's (tab_name, month, year), e.g.
    [('Aug 2026', 8, 2026), ('Jul 2026', 7, 2026)]. Returns year separately
    so a January run (checking December of the PRIOR year) can open the
    correct year's spreadsheet if TCN publishes a new one annually.
    """
    now = now or timezone.now()
    tabs = []
    year, month = now.year, now.month
    for _ in range(2):
        tabs.append((f'{_MONTH_NAME[month]} {year}', month, year))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return tabs


def _fetch_tab_rows(sh, month_tab, attempts=3):
    """Read a tab's values with retries (Google Sheets connections drop transiently)."""
    for attempt in range(attempts):
        try:
            ws = sh.worksheet(month_tab)
            return ws.get_all_values()
        except Exception:
            if attempt == attempts - 1:
                return None
    return None


def run_sync() -> dict:
    """
    Pull the current + previous month's TCN tabs, create any FeederInterruption
    rows that don't already exist, and fill in restored_at for rows that were
    previously open. Called by technical.tasks.sync_tcn_interruptions_task
    (hourly) and reuses the exact same parsing as the one-off historical
    backfill command.
    """
    from tmo.sheet_sync import open_spreadsheet

    stats = {
        'records_fetched': 0,
        'records_created': 0,
        'records_updated': 0,
        'records_skipped': 0,
        'records_errored': 0,
        'errors': [],
    }

    lookup = _build_feeder_lookup()
    unmatched_feeders = set()
    sheets_by_year = {}  # cache: only open each year's spreadsheet once per run

    for month_tab, expected_month, year in _recent_month_tabs():
        if year not in sheets_by_year:
            sheets_by_year[year] = open_spreadsheet(_current_spreadsheet_id(year))
        sh = sheets_by_year[year]
        rows = _fetch_tab_rows(sh, month_tab)
        if not rows:
            continue  # tab may not exist yet, or a transient fetch failure
        _header, data_rows = rows[0], rows[1:]

        for row in data_rows:
            stats['records_fetched'] += 1
            fields, reason = parse_row(row, expected_month, lookup)
            if fields is None:
                if reason and reason.startswith('unmatched_feeder:'):
                    unmatched_feeders.add(reason.split(':', 1)[1])
                elif reason == 'bad_date':
                    stats['records_errored'] += 1
                continue

            occurred_at = fields.pop('occurred_at')
            restored_at = fields.pop('restored_at')
            interruption_type = fields.pop('interruption_type')
            feeder = fields.pop('feeder')

            obj, created = FeederInterruption.objects.get_or_create(
                feeder=feeder,
                occurred_at=occurred_at,
                interruption_type=interruption_type,
                defaults={**fields, 'restored_at': restored_at},
            )
            if created:
                stats['records_created'] += 1
            elif restored_at and not obj.restored_at:
                obj.restored_at = restored_at
                obj.save(update_fields=['restored_at'])
                stats['records_updated'] += 1
            else:
                stats['records_skipped'] += 1

    if unmatched_feeders:
        stats['errors'].append(
            'Unmatched feeder names (not onboarded in Raven, skipped): '
            + ', '.join(sorted(unmatched_feeders))
        )

    return stats
