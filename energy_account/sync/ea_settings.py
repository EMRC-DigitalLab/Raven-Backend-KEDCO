"""
energy_account/sync/ea_settings.py

Full sync: DataNest `ea_settings` → Raven `EASettings`.

Small key-value table (6 rows). Full upsert each run.
"""

from django.db import connections

from energy_account.models import EASettings
from technical.sync.base import get_sync_window


def run_sync() -> dict:
    window_start, window_end, _ = get_sync_window('ea_settings')

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

    with connections['external'].cursor() as cursor:
        cursor.execute("""
            SELECT setting_key, setting_value, description, updated_at
            FROM ea_settings
        """)
        rows = cursor.fetchall()

    existing = {s.setting_key: s for s in EASettings.objects.all()}

    for setting_key, setting_value, description, updated_at in rows:
        stats['records_fetched'] += 1
        try:
            if setting_key in existing:
                obj = existing[setting_key]
                if obj.setting_value != setting_value:
                    obj.setting_value       = setting_value
                    obj.description         = description or ''
                    obj.datanest_updated_at = updated_at
                    obj.save()
                    stats['records_updated'] += 1
                else:
                    stats['records_skipped'] += 1
            else:
                EASettings.objects.create(
                    setting_key         = setting_key,
                    setting_value       = setting_value,
                    description         = description or '',
                    datanest_updated_at = updated_at,
                )
                stats['records_created'] += 1
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'ea_settings key={setting_key}: {exc}')

    return stats
