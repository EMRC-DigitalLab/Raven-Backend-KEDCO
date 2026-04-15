"""
energy_account/sync/mo_reconciliation.py

Incremental sync: DataNest `ea_mo_reconciliation` → Raven `EAMOReconciliation`.

Depends on: EAMonthlyReturn.
"""

from django.db import connections
from django.utils.timezone import is_naive, make_aware

from energy_account.models import EAMOReconciliation, EAMonthlyReturn
from technical.sync.base import get_sync_window


def _aware(dt):
    if dt and is_naive(dt):
        return make_aware(dt)
    return dt


def run_sync() -> dict:
    window_start, window_end, _ = get_sync_window('ea_mo_reconciliation')

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

    return_map = {r.datanest_id: r for r in EAMonthlyReturn.objects.all()}
    existing   = {r.datanest_id: r for r in EAMOReconciliation.objects.all()}

    with connections['external'].cursor() as cursor:
        cursor.execute("""
            SELECT reconciliation_id, return_id,
                   mo_figure_mwh, kedco_figure_mwh, difference_mwh,
                   status, notes, created_at, updated_at
            FROM ea_mo_reconciliation
            WHERE updated_at >= %s AND updated_at <= %s
            ORDER BY created_at ASC
        """, [window_start, window_end])
        rows = cursor.fetchall()

    for row in rows:
        (
            recon_id, return_id,
            mo_figure_mwh, kedco_figure_mwh, difference_mwh,
            status, notes, created_at, updated_at,
        ) = row

        stats['records_fetched'] += 1

        monthly_return = return_map.get(return_id)
        if not monthly_return:
            stats['records_skipped'] += 1
            stats['errors'].append(f'mo_recon {recon_id}: return {return_id} not in Raven')
            continue

        fields = dict(
            monthly_return      = monthly_return,
            mo_figure_mwh       = mo_figure_mwh,
            kedco_figure_mwh    = kedco_figure_mwh,
            difference_mwh      = difference_mwh,
            status              = status,
            notes               = notes or '',
            datanest_created_at = _aware(created_at),
            datanest_updated_at = _aware(updated_at),
        )

        try:
            if recon_id in existing:
                obj = existing[recon_id]
                for k, v in fields.items():
                    setattr(obj, k, v)
                obj.save()
                stats['records_updated'] += 1
            else:
                EAMOReconciliation.objects.create(datanest_id=recon_id, **fields)
                stats['records_created'] += 1
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'mo_recon {recon_id}: {exc}')

    return stats
