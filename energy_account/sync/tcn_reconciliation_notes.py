"""
energy_account/sync/tcn_reconciliation_notes.py

Incremental sync: DataNest `ea_tcn_recon_notes` → Raven `EATCNReconciliationNote`.

Notes are append-only in DataNest (no updated_at), so we sync on created_at.
Depends on: EATCNReconciliation.
"""

from django.db import connections
from django.utils.timezone import is_naive, make_aware

from energy_account.models import EATCNReconciliation, EATCNReconciliationNote
from technical.sync.base import get_sync_window


def _aware(dt):
    if dt and is_naive(dt):
        return make_aware(dt)
    return dt


def run_sync() -> dict:
    window_start, window_end, _ = get_sync_window('ea_tcn_reconciliation_notes')

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

    recon_map = {r.datanest_id: r for r in EATCNReconciliation.objects.all()}
    existing  = {n.datanest_id: n for n in EATCNReconciliationNote.objects.all()}

    with connections['external'].cursor() as cursor:
        cursor.execute("""
            SELECT note_id, tcn_recon_id, author_type,
                   author_user_id, author_name, note_text,
                   figure_at_time_mwh, created_at
            FROM ea_tcn_recon_notes
            WHERE created_at >= %s AND created_at <= %s
            ORDER BY created_at ASC
        """, [window_start, window_end])
        rows = cursor.fetchall()

    for row in rows:
        (
            note_id, tcn_recon_id, author_type,
            author_user_id, author_name, note_text,
            figure_at_time_mwh, created_at,
        ) = row

        stats['records_fetched'] += 1

        tcn_reconciliation = recon_map.get(tcn_recon_id)
        if not tcn_reconciliation:
            stats['records_skipped'] += 1
            stats['errors'].append(f'tcn_note {note_id}: recon {tcn_recon_id} not in Raven')
            continue

        # Notes are immutable — skip if already present
        if note_id in existing:
            stats['records_skipped'] += 1
            continue

        try:
            EATCNReconciliationNote.objects.create(
                datanest_id        = note_id,
                tcn_reconciliation = tcn_reconciliation,
                author_type        = author_type,
                author_name        = author_name or '',
                note_text          = note_text,
                figure_at_time_mwh = figure_at_time_mwh,
                datanest_created_at= _aware(created_at),
            )
            stats['records_created'] += 1
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'tcn_note {note_id}: {exc}')

    return stats
