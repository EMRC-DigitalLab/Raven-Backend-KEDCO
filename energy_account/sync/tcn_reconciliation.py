"""
energy_account/sync/tcn_reconciliation.py

Incremental sync: DataNest `ea_tcn_reconciliation` → Raven `EATCNReconciliation`.

Depends on: EAMonthlyReturn.
"""

from django.db import connections
from django.utils.timezone import is_naive, make_aware

from energy_account.models import EAMonthlyReturn, EATCNReconciliation
from technical.sync.base import get_sync_window


def _aware(dt):
    if dt and is_naive(dt):
        return make_aware(dt)
    return dt


def run_sync() -> dict:
    window_start, window_end, _ = get_sync_window('ea_tcn_reconciliation')

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
    existing   = {r.datanest_id: r for r in EATCNReconciliation.objects.all()}

    with connections['external'].cursor() as cursor:
        cursor.execute("""
            SELECT tcn_recon_id, return_id,
                   kedco_figure_mwh, kedco_figure_naira, kedco_submitted_at, kedco_notes,
                   tcn_figure_mwh, tcn_figure_naira, tcn_submitted_at,
                   tcn_contact_name, tcn_contact_email, tcn_notes,
                   difference_mwh, difference_naira, difference_pct,
                   status, resolution_notes, agreed_figure_mwh, resolved_at,
                   created_at, updated_at
            FROM ea_tcn_reconciliation
            WHERE updated_at >= %s AND updated_at <= %s
            ORDER BY created_at ASC
        """, [window_start, window_end])
        rows = cursor.fetchall()

    for row in rows:
        (
            tcn_recon_id, return_id,
            kedco_figure_mwh, kedco_figure_naira, kedco_submitted_at, kedco_notes,
            tcn_figure_mwh, tcn_figure_naira, tcn_submitted_at,
            tcn_contact_name, tcn_contact_email, tcn_notes,
            difference_mwh, difference_naira, difference_pct,
            status, resolution_notes, agreed_figure_mwh, resolved_at,
            created_at, updated_at,
        ) = row

        stats['records_fetched'] += 1

        monthly_return = return_map.get(return_id)
        if not monthly_return:
            stats['records_skipped'] += 1
            stats['errors'].append(f'tcn_recon {tcn_recon_id}: return {return_id} not in Raven')
            continue

        fields = dict(
            monthly_return      = monthly_return,
            kedco_figure_mwh    = kedco_figure_mwh,
            kedco_figure_naira  = kedco_figure_naira,
            kedco_submitted_at  = _aware(kedco_submitted_at),
            kedco_notes         = kedco_notes or '',
            tcn_figure_mwh      = tcn_figure_mwh,
            tcn_figure_naira    = tcn_figure_naira,
            tcn_submitted_at    = _aware(tcn_submitted_at),
            tcn_contact_name    = tcn_contact_name or '',
            tcn_contact_email   = tcn_contact_email or '',
            tcn_notes           = tcn_notes or '',
            difference_mwh      = difference_mwh,
            difference_naira    = difference_naira,
            difference_pct      = difference_pct,
            status              = status,
            resolution_notes    = resolution_notes or '',
            agreed_figure_mwh   = agreed_figure_mwh,
            resolved_at         = _aware(resolved_at),
            datanest_created_at = _aware(created_at),
            datanest_updated_at = _aware(updated_at),
        )

        try:
            if tcn_recon_id in existing:
                obj = existing[tcn_recon_id]
                for k, v in fields.items():
                    setattr(obj, k, v)
                obj.save()
                stats['records_updated'] += 1
            else:
                EATCNReconciliation.objects.create(datanest_id=tcn_recon_id, **fields)
                stats['records_created'] += 1
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'tcn_recon {tcn_recon_id}: {exc}')

    return stats
