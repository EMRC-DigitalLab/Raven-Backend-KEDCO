"""
commercial/sync/tariff_rates.py

Full-refresh sync: DataNest `tariff_rates` -> Raven `TariffRate`.

Rules:
  - Runs every 1 hour via Celery Beat.
  - Full upsert every run (10 records — negligible cost).
  - MD2 rates (customer_type not in MDI/MDNI) are intentionally skipped.
"""

from django.db import connections

from commercial.models import TariffRate

VALID_TYPES = {'MDI', 'MDNI'}
VALID_BANDS = {'A', 'B', 'C', 'D', 'E'}


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

    existing = {
        r['datanest_id']: r
        for r in TariffRate.objects.values('id', 'datanest_id', 'rate_per_kwh', 'effective_to')
        if r['datanest_id'] is not None
    }

    conn = connections['external']
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, band, customer_type, rate_per_kwh, effective_from, effective_to
            FROM tariff_rates
            ORDER BY id
            """
        )
        rows = cursor.fetchall()

    for row in rows:
        stats['records_fetched'] += 1
        dn_id, band, customer_type, rate, effective_from, effective_to = row

        if customer_type not in VALID_TYPES or band not in VALID_BANDS:
            stats['records_skipped'] += 1
            continue

        kwargs = dict(
            datanest_id=dn_id,
            band=band,
            customer_type=customer_type,
            rate_per_kwh=rate,
            effective_from=effective_from,
            effective_to=effective_to,
            is_active=(effective_to is None),
        )

        existing_row = existing.get(dn_id)
        if existing_row:
            if (existing_row['rate_per_kwh'] != rate or
                    existing_row['effective_to'] != effective_to):
                try:
                    TariffRate(id=existing_row['id'], **kwargs).save(force_update=True)
                    stats['records_updated'] += 1
                except Exception as exc:
                    stats['records_errored'] += 1
                    stats['errors'].append(f'tariff update error (id={dn_id}): {exc}')
            else:
                stats['records_skipped'] += 1
        else:
            try:
                TariffRate(**kwargs).save(force_insert=True)
                stats['records_created'] += 1
            except Exception as exc:
                stats['records_errored'] += 1
                stats['errors'].append(f'tariff create error (id={dn_id}): {exc}')

    return stats
