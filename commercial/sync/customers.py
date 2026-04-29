"""
commercial/sync/customers.py

Incremental sync: DataNest `customer_information` -> Raven `CommercialCustomer`.

Rules:
  - Runs every 1 hour via Celery Beat.
  - Watermark is based on DataNest `updated_at` — covers both new customers
    and updates to existing ones (meter status changes, feeder reassignments).
  - Look-back is 2 hours to handle clock skew between servers.
  - Upserts by external_id.
"""

from datetime import timedelta

from django.db import connections
from django.utils.timezone import make_aware, is_naive

from commercial.models import CommercialCustomer
from common.models import BusinessDistrict, Feeder
from technical.sync.base import get_sync_window

CHUNK = 5_000
LOOK_BACK_HOURS = 2


def run_sync() -> dict:
    window_start, window_end, watermark = get_sync_window('commercial_customers')

    earliest = window_end - timedelta(hours=LOOK_BACK_HOURS)
    if window_start > earliest:
        window_start = earliest

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

    feeder_map = {f.slug: f.id for f in Feeder.objects.only('id', 'slug')}
    district_map = {d.slug: d.id for d in BusinessDistrict.objects.only('id', 'slug')}

    existing = {
        str(c['external_id']): c
        for c in CommercialCustomer.objects.filter(
            datanest_created_at__gte=window_start - timedelta(days=365)
        ).values('id', 'external_id', 'meter_status', 'account_no',
                 'feeder_id', 'district_id', 'is_bypass')
    }

    to_create = []
    to_update = []
    seen = set()

    conn = connections['external']

    with conn.cursor() as cursor:
        offset = 0
        while True:
            cursor.execute(
                """
                SELECT
                    customer_id, account_no, meter_number,
                    customer_name, customer_address, phone_number,
                    feeder_id, district_id, customer_type,
                    meter_status, meter_fault_logged_at, meter_fault_logged_by,
                    created_at, updated_at
                FROM customer_information
                WHERE updated_at >= %s AND updated_at <= %s
                ORDER BY updated_at ASC
                LIMIT %s OFFSET %s
                """,
                [window_start, window_end, CHUNK, offset],
            )
            rows = cursor.fetchall()
            if not rows:
                break

            for row in rows:
                stats['records_fetched'] += 1
                (
                    customer_id, account_no, meter_number,
                    customer_name, customer_address, phone_number,
                    feeder_id, district_id, customer_type,
                    meter_status, fault_logged_at, fault_logged_by,
                    created_at, updated_at,
                ) = row

                ext_id = str(customer_id)
                if ext_id in seen:
                    stats['records_skipped'] += 1
                    continue
                seen.add(ext_id)

                if created_at and is_naive(created_at):
                    created_at = make_aware(created_at)
                if fault_logged_at and is_naive(fault_logged_at):
                    fault_logged_at = make_aware(fault_logged_at)

                feeder_pk = feeder_map.get(str(feeder_id))
                district_pk = district_map.get(str(district_id))

                status = meter_status or 'active'
                is_bypass = (status == 'bypassed')

                kwargs = dict(
                    external_id=ext_id,
                    account_no=account_no,
                    meter_number=meter_number or '',
                    customer_name=customer_name or '',
                    customer_address=customer_address or '',
                    phone_number=phone_number or '',
                    feeder_id=feeder_pk,
                    district_id=district_pk,
                    customer_type=customer_type,
                    meter_status=status,
                    meter_fault_logged_at=fault_logged_at,
                    meter_fault_logged_by=fault_logged_by or '',
                    is_bypass=is_bypass,
                    datanest_created_at=created_at,
                )

                existing_row = existing.get(ext_id)
                if existing_row:
                    to_update.append(CommercialCustomer(id=existing_row['id'], **kwargs))
                else:
                    to_create.append(CommercialCustomer(**kwargs))

            offset += len(rows)

    for obj in to_create:
        try:
            obj.save(force_insert=True)
            stats['records_created'] += 1
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'create error ({obj.account_no}): {exc}')

    for obj in to_update:
        try:
            obj.save(force_update=True)
            stats['records_updated'] += 1
        except Exception as exc:
            stats['records_errored'] += 1
            stats['errors'].append(f'update error ({obj.account_no}): {exc}')

    return stats
