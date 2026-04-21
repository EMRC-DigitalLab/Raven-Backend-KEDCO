"""
technical/sync/interruptions.py

Incremental sync: DataNest `technicalenergyfault` → Raven `FeederInterruption`.

Two passes per run:
  Pass 1 — NEW faults: fetch rows with time_of_occurrence >= window_start,
            create any that don't already exist in Raven.
  Pass 2 — RESOLVE open faults: for every Raven FeederInterruption that still
            has restored_at=NULL, check DataNest to see if it has now been
            resolved and update accordingly.

Fault type resolution (per row):
  - Primary:  fault_types.fault_code  (via LEFT JOIN on fault_type_id)
  - Fallback: technicalenergyfault.fault_type  (legacy free-text column)
  - Both NULL/empty → FaultNormalizer returns 'N/A'

Soft-deleted rows (deleted_at IS NOT NULL) are excluded from all queries.
"""

from django.db import connections
from django.utils.timezone import make_aware, is_naive

from common.models import Feeder
from technical.models import FeederInterruption
from technical.sync.base import get_sync_window
from technical.sync.fault_normalizer import FaultNormalizer

MYSQL_CHUNK = 50_000
BULK_BATCH = 10_000


def run_sync() -> dict:
    window_start, window_end, _ = get_sync_window('technical_interruptions')

    stats = {
        'window_start': window_start,
        'window_end': window_end,
        'records_fetched': 0,
        'records_created': 0,
        'records_updated': 0,
        'records_skipped': 0,
        'records_deleted': 0,
        'records_errored': 0,
        'errors': [],
    }

    feeder_map = {f.slug: f for f in Feeder.objects.all()}
    if not feeder_map:
        stats['errors'].append('No feeders found — skipping.')
        return stats

    # Build normaliser once — loads FaultTypeCategory from DB once per run
    normaliser = FaultNormalizer()

    datanest_keys = _pass1_new_faults(stats, feeder_map, window_start, window_end, normaliser)
    _pass2_resolve_open(stats, feeder_map, normaliser)
    _pass3_delete_stale(stats, window_start, window_end, datanest_keys)

    # Log any unrecognised fault codes so they can be reviewed in admin
    if normaliser.unrecognised:
        stats['errors'].append(
            f'Unrecognised fault codes stored raw (add to FaultTypeCategory): '
            f'{", ".join(sorted(normaliser.unrecognised))}'
        )

    return stats


# ──────────────────────────────────────────────────────────────────────────────
# Pass 1: new faults
# ──────────────────────────────────────────────────────────────────────────────

def _pass1_new_faults(stats, feeder_map, window_start, window_end, normaliser) -> set:
    """
    Fetch DataNest faults in the window, create missing Raven records.
    Returns the set of (feeder_id, occurred_at, interruption_type) keys
    seen in DataNest — used by _pass3 to detect stale Raven records.

    Also upgrades any existing N/A record to the correct fault type when the
    DSO has since filled in fault_type_id in DataNest.
    """
    existing_keys = set(
        FeederInterruption.objects
        .filter(occurred_at__gte=window_start)
        .values_list('feeder_id', 'occurred_at', 'interruption_type')
    )

    to_create = []
    seen = set()

    conn = connections['external']
    with conn.cursor() as cursor:
        offset = 0
        while True:
            cursor.execute(
                """
                SELECT tef.Feeder_id,
                       tef.time_of_occurrence,
                       tef.time_of_restoration,
                       COALESCE(ft.fault_code, tef.fault_type) AS resolved_fault_type,
                       tef.description,
                       tef.load_interrupted_mw
                FROM technicalenergyfault tef
                LEFT JOIN fault_types ft
                       ON tef.fault_type_id = ft.fault_type_id
                WHERE tef.time_of_occurrence >= %s
                  AND tef.time_of_occurrence <= %s
                  AND tef.deleted_at IS NULL
                ORDER BY tef.time_of_occurrence ASC
                LIMIT %s OFFSET %s
                """,
                [window_start, window_end, MYSQL_CHUNK, offset],
            )
            rows = cursor.fetchall()
            if not rows:
                break

            for row in rows:
                stats['records_fetched'] += 1
                feeder_slug, occurred_at, restored_at, fault_type, description, load_mw = row

                feeder = feeder_map.get(feeder_slug)
                if not feeder:
                    stats['records_skipped'] += 1
                    continue

                fault_type = normaliser.normalise(fault_type)
                occurred_at = _aware(occurred_at)
                restored_at = _aware(restored_at) if restored_at else None

                if not occurred_at:
                    stats['records_skipped'] += 1
                    continue

                key = (feeder.id, occurred_at, fault_type)

                # If a record with this exact key already exists, just update restoration.
                if key in seen or key in existing_keys:
                    if key in existing_keys and restored_at:
                        _update_restoration(feeder.id, occurred_at, fault_type, restored_at, stats)
                    else:
                        stats['records_skipped'] += 1
                    seen.add(key)
                    continue

                # If an N/A record exists for this (feeder, occurred_at) and the correct
                # fault type is now known, upgrade it instead of creating a duplicate.
                na_key = (feeder.id, occurred_at, 'N/A')
                if na_key in existing_keys and fault_type != 'N/A':
                    _upgrade_fault_type(feeder.id, occurred_at, fault_type, restored_at, stats)
                    seen.add(key)
                    continue

                seen.add(key)
                to_create.append(FeederInterruption(
                    feeder=feeder,
                    interruption_type=fault_type,
                    occurred_at=occurred_at,
                    restored_at=restored_at,
                    description=(description or '').strip(),
                ))

                if len(to_create) >= BULK_BATCH:
                    _flush_create(to_create, stats)
                    to_create = []

            offset += len(rows)

    if to_create:
        _flush_create(to_create, stats)

    return seen


# ──────────────────────────────────────────────────────────────────────────────
# Pass 2: resolve open faults
# ──────────────────────────────────────────────────────────────────────────────

def _pass2_resolve_open(stats, _, normaliser):
    """
    For every Raven interruption still open (restored_at=NULL),
    check DataNest and update if now resolved.
    """
    open_interruptions = list(
        FeederInterruption.objects
        .filter(restored_at__isnull=True)
        .select_related('feeder')
    )

    if not open_interruptions:
        return

    slug_to_open = {}
    for intr in open_interruptions:
        slug_to_open.setdefault(intr.feeder.slug, []).append(intr)

    feeder_slugs = list(slug_to_open.keys())
    to_update = []

    conn = connections['external']
    with conn.cursor() as cursor:
        SLUG_CHUNK = 200
        for i in range(0, len(feeder_slugs), SLUG_CHUNK):
            batch_slugs = feeder_slugs[i:i + SLUG_CHUNK]
            placeholders = ','.join(['%s'] * len(batch_slugs))
            cursor.execute(
                f"""
                SELECT tef.Feeder_id,
                       tef.time_of_occurrence,
                       COALESCE(ft.fault_code, tef.fault_type) AS resolved_fault_type,
                       tef.time_of_restoration
                FROM technicalenergyfault tef
                LEFT JOIN fault_types ft
                       ON tef.fault_type_id = ft.fault_type_id
                WHERE tef.Feeder_id IN ({placeholders})
                  AND tef.time_of_restoration IS NOT NULL
                  AND tef.deleted_at IS NULL
                """,
                batch_slugs,
            )
            for feeder_slug, occurred_at, fault_type, restored_at in cursor.fetchall():
                occurred_at = _aware(occurred_at)
                restored_at = _aware(restored_at)
                fault_type = normaliser.normalise(fault_type)

                if not occurred_at or not restored_at:
                    continue

                for intr in slug_to_open.get(feeder_slug, []):
                    if (
                        intr.occurred_at == occurred_at
                        and intr.interruption_type == fault_type
                        and intr.restored_at is None
                    ):
                        intr.restored_at = restored_at
                        to_update.append(intr)
                        break

    if to_update:
        try:
            FeederInterruption.objects.bulk_update(
                to_update,
                fields=['restored_at'],
                batch_size=BULK_BATCH,
            )
            stats['records_updated'] += len(to_update)
        except Exception as exc:
            stats['records_errored'] += len(to_update)
            stats['errors'].append(f'bulk_update (resolve) error: {exc}')


# ──────────────────────────────────────────────────────────────────────────────
# Pass 3: delete stale faults
# ──────────────────────────────────────────────────────────────────────────────

def _pass3_delete_stale(stats, window_start, window_end, datanest_keys: set):
    """
    Delete Raven interruptions in the window whose
    (feeder_id, occurred_at, interruption_type) key was NOT seen in DataNest.
    These records were removed upstream and should be removed from Raven too.
    """
    raven_in_window = FeederInterruption.objects.filter(
        occurred_at__gte=window_start,
        occurred_at__lte=window_end,
    ).values('id', 'feeder_id', 'occurred_at', 'interruption_type')

    stale_ids = []
    for row in raven_in_window:
        key = (row['feeder_id'], row['occurred_at'], row['interruption_type'])
        if key not in datanest_keys:
            stale_ids.append(row['id'])

    if stale_ids:
        try:
            deleted, _ = FeederInterruption.objects.filter(id__in=stale_ids).delete()
            stats['records_deleted'] = deleted
        except Exception as exc:
            stats['errors'].append(f'delete (stale) error: {exc}')


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _aware(dt):
    if dt is None:
        return None
    if is_naive(dt):
        return make_aware(dt)
    return dt


def _upgrade_fault_type(feeder_id, occurred_at, new_fault_type, restored_at, stats):
    """
    Upgrade an existing N/A record to the correct fault type when the DSO has
    since filled in fault_type_id in DataNest. Also sets restored_at if known.
    """
    update_fields = {'interruption_type': new_fault_type}
    if restored_at:
        update_fields['restored_at'] = restored_at
    updated = FeederInterruption.objects.filter(
        feeder_id=feeder_id,
        occurred_at=occurred_at,
        interruption_type='N/A',
    ).update(**update_fields)
    if updated:
        stats['records_updated'] += updated


def _update_restoration(feeder_id, occurred_at, fault_type, restored_at, stats):
    updated = FeederInterruption.objects.filter(
        feeder_id=feeder_id,
        occurred_at=occurred_at,
        interruption_type=fault_type,
        restored_at__isnull=True,
    ).update(restored_at=restored_at)
    if updated:
        stats['records_updated'] += updated


def _flush_create(batch: list, stats: dict):
    try:
        FeederInterruption.objects.bulk_create(
            batch,
            batch_size=BULK_BATCH,
            ignore_conflicts=True,
        )
        stats['records_created'] += len(batch)
    except Exception as exc:
        stats['records_errored'] += len(batch)
        stats['errors'].append(f'bulk_create error: {exc}')
