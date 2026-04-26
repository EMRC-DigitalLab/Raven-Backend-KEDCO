"""
commercial/sync/managers.py

Full-refresh sync: DataNest `feeder_managers` + `feeder_manager_assignments`
-> Raven `MeterManager` + `MeterManagerAssignment`.

Rules:
  - Runs every 1 hour via Celery Beat.
  - Full upsert every run (tables are small: ~71 managers, ~95 assignments).
  - Assignments that no longer exist in DataNest are NOT deleted — they may
    still be referenced by historical readings.
"""

from django.db import connections

from commercial.models import MeterManager, MeterManagerAssignment
from common.models import Feeder

CHUNK = 1_000


def run_sync() -> dict:
    from django.utils import timezone
    now = timezone.now()

    stats = {
        'window_start': now,
        'window_end': now,
        'records_fetched': 0,
        'records_created': 0,
        'records_updated': 0,
        'records_skipped': 0,
        'records_deleted': 0,
        'records_errored': 0,
        'errors': [],
    }

    _sync_managers(stats)
    _sync_assignments(stats)

    return stats


def _sync_managers(stats):
    existing = {
        str(m['external_id']): m
        for m in MeterManager.objects.values('id', 'external_id', 'name', 'manager_type', 'staff_id')
    }

    conn = connections['external']
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT manager_id, user_id, staff_id, meter_reader_name, manager_type
            FROM feeder_managers
            ORDER BY manager_id
            """
        )
        rows = cursor.fetchall()

    for row in rows:
        stats['records_fetched'] += 1
        manager_id, user_id, staff_id, name, manager_type = row

        ext_id = str(manager_id)
        kwargs = dict(
            external_id=ext_id,
            user_external_id=str(user_id) if user_id else '',
            staff_id=staff_id or '',
            name=name or '',
            manager_type=manager_type or 'MDNI',
        )

        existing_row = existing.get(ext_id)
        if existing_row:
            if (existing_row['name'] != (name or '') or
                    existing_row['manager_type'] != (manager_type or 'MDNI') or
                    existing_row['staff_id'] != (staff_id or '')):
                try:
                    MeterManager(id=existing_row['id'], **kwargs).save(force_update=True)
                    stats['records_updated'] += 1
                except Exception as exc:
                    stats['records_errored'] += 1
                    stats['errors'].append(f'manager update error ({ext_id}): {exc}')
            else:
                stats['records_skipped'] += 1
        else:
            try:
                MeterManager(**kwargs).save(force_insert=True)
                stats['records_created'] += 1
            except Exception as exc:
                stats['records_errored'] += 1
                stats['errors'].append(f'manager create error ({ext_id}): {exc}')


def _sync_assignments(stats):
    feeder_map = {f.slug: f.id for f in Feeder.objects.only('id', 'slug')}
    manager_map = {
        str(m['external_id']): m['id']
        for m in MeterManager.objects.values('id', 'external_id')
    }
    existing = {
        str(a['external_id']): a
        for a in MeterManagerAssignment.objects.values('id', 'external_id', 'status')
    }

    conn = connections['external']
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT assignment_id, manager_id, feeder_id, start_date, end_date, status
            FROM feeder_manager_assignments
            ORDER BY assignment_id
            """
        )
        rows = cursor.fetchall()

    for row in rows:
        assignment_id, manager_id, feeder_id, start_date, end_date, status = row

        manager_pk = manager_map.get(str(manager_id))
        feeder_pk = feeder_map.get(str(feeder_id))

        if not manager_pk or not feeder_pk:
            stats['records_skipped'] += 1
            continue

        ext_id = str(assignment_id)
        kwargs = dict(
            external_id=ext_id,
            manager_id=manager_pk,
            feeder_id=feeder_pk,
            status=status or 'active',
            start_date=start_date,
            end_date=end_date,
        )

        existing_row = existing.get(ext_id)
        if existing_row:
            if existing_row['status'] != (status or 'active'):
                try:
                    MeterManagerAssignment(id=existing_row['id'], **kwargs).save(force_update=True)
                    stats['records_updated'] += 1
                except Exception as exc:
                    stats['records_errored'] += 1
                    stats['errors'].append(f'assignment update error ({ext_id}): {exc}')
            else:
                stats['records_skipped'] += 1
        else:
            try:
                MeterManagerAssignment(**kwargs).save(force_insert=True)
                stats['records_created'] += 1
            except Exception as exc:
                stats['records_errored'] += 1
                stats['errors'].append(f'assignment create error ({ext_id}): {exc}')
