"""
commercial/sync/tmo.py

Full-refresh sync of DataNest TMO tables -> Raven PostgreSQL.

Three tables, all upserted by tmo_id (DataNest PK):
  - tmo_targets            -> TMOFeederTarget
  - tmo_collection_targets -> TMOCollectionTarget
  - tmo_billing_efficiency -> TMOBillingEfficiency

Runs every 30 minutes via Celery Beat (data changes infrequently — monthly targets).
"""

import logging
from django.db import connections
from django.utils.timezone import make_aware, is_naive

from commercial.models import TMOBillingEfficiency, TMOCollectionTarget, TMOFeederTarget
from common.models import Feeder

logger = logging.getLogger(__name__)

BATCH = 500


def _make_aware(dt):
    if dt is None:
        return None
    if is_naive(dt):
        return make_aware(dt)
    return dt


def _feeder_map():
    """slug (upper/lower) -> Feeder instance."""
    m = {}
    for f in Feeder.objects.only('id', 'slug', 'name'):
        m[f.slug]        = f
        m[f.slug.upper()] = f
        m[f.slug.lower()] = f
    return m


# ── 1. tmo_targets -> TMOFeederTarget ─────────────────────────────────────────

def sync_tmo_feeder_targets() -> dict:
    stats = _empty_stats()
    feeder_map = _feeder_map()

    with connections['external'].cursor() as cur:
        cur.execute("""
            SELECT tmo_id, feeder_id, target_date, target_mwh,
                   set_by_user_id, created_at, updated_at
            FROM tmo_targets
            ORDER BY tmo_id
        """)
        rows = cur.fetchall()

    stats['records_fetched'] = len(rows)
    objects = []
    for tmo_id, feeder_id, target_date, target_mwh, user_id, created_at, updated_at in rows:
        feeder_code = str(feeder_id) if feeder_id else ''
        feeder_obj  = feeder_map.get(feeder_code)
        objects.append(TMOFeederTarget(
            tmo_id         = tmo_id,
            feeder         = feeder_obj,
            feeder_code    = feeder_code,
            target_date    = target_date,
            target_mwh     = target_mwh or 0,
            set_by_user_id = str(user_id) if user_id else '',
            dn_created_at  = _make_aware(created_at),
            dn_updated_at  = _make_aware(updated_at),
        ))

    try:
        TMOFeederTarget.objects.bulk_create(
            objects,
            update_conflicts=True,
            unique_fields=['tmo_id'],
            update_fields=['feeder', 'feeder_code', 'target_date', 'target_mwh',
                           'set_by_user_id', 'dn_created_at', 'dn_updated_at'],
            batch_size=BATCH,
        )
        stats['records_created'] = len(objects)
    except Exception as exc:
        stats['records_errored'] = len(objects)
        stats['errors'].append(str(exc))
        logger.exception('[TMOSync] tmo_targets error: %s', exc)

    return stats


# ── 2. tmo_collection_targets -> TMOCollectionTarget ─────────────────────────

def sync_tmo_collection_targets() -> dict:
    stats = _empty_stats()

    with connections['external'].cursor() as cur:
        cur.execute("""
            SELECT tmo_id, segment_code, sub_segment, period_month,
                   target_amount, actual_amount, set_by_user_id,
                   created_at, updated_at
            FROM tmo_collection_targets
            ORDER BY tmo_id
        """)
        rows = cur.fetchall()

    stats['records_fetched'] = len(rows)
    objects = []
    for tmo_id, seg, sub, period, target, actual, user_id, created_at, updated_at in rows:
        objects.append(TMOCollectionTarget(
            tmo_id         = tmo_id,
            segment_code   = seg or '',
            sub_segment    = sub or '',
            period_month   = period,
            target_amount  = target or 0,
            actual_amount  = actual or 0,
            set_by_user_id = str(user_id) if user_id else '',
            dn_created_at  = _make_aware(created_at),
            dn_updated_at  = _make_aware(updated_at),
        ))

    try:
        TMOCollectionTarget.objects.bulk_create(
            objects,
            update_conflicts=True,
            unique_fields=['tmo_id'],
            update_fields=['segment_code', 'sub_segment', 'period_month',
                           'target_amount', 'actual_amount', 'set_by_user_id',
                           'dn_created_at', 'dn_updated_at'],
            batch_size=BATCH,
        )
        stats['records_created'] = len(objects)
    except Exception as exc:
        stats['records_errored'] = len(objects)
        stats['errors'].append(str(exc))
        logger.exception('[TMOSync] tmo_collection_targets error: %s', exc)

    return stats


# ── 3. tmo_billing_efficiency -> TMOBillingEfficiency ─────────────────────────

def sync_tmo_billing_efficiency() -> dict:
    stats = _empty_stats()

    with connections['external'].cursor() as cur:
        cur.execute("""
            SELECT tmo_id, scope_type, scope_code, scope_label,
                   period_month, energy_delivered_gwh, energy_billed_gwh,
                   target_revenue_amount, billed_amount,
                   set_by_user_id, created_at, updated_at
            FROM tmo_billing_efficiency
            ORDER BY tmo_id
        """)
        rows = cur.fetchall()

    stats['records_fetched'] = len(rows)
    objects = []
    for (tmo_id, s_type, s_code, s_label, period,
         del_gwh, bil_gwh, t_rev, b_rev,
         user_id, created_at, updated_at) in rows:
        objects.append(TMOBillingEfficiency(
            tmo_id                = tmo_id,
            scope_type            = str(s_type) if s_type else 'feeder',
            scope_code            = s_code or '',
            scope_label           = s_label or '',
            period_month          = period,
            energy_delivered_gwh  = del_gwh or 0,
            energy_billed_gwh     = bil_gwh or 0,
            target_revenue_amount = t_rev or 0,
            billed_amount         = b_rev or 0,
            set_by_user_id        = str(user_id) if user_id else '',
            dn_created_at         = _make_aware(created_at),
            dn_updated_at         = _make_aware(updated_at),
        ))

    try:
        TMOBillingEfficiency.objects.bulk_create(
            objects,
            update_conflicts=True,
            unique_fields=['tmo_id'],
            update_fields=['scope_type', 'scope_code', 'scope_label', 'period_month',
                           'energy_delivered_gwh', 'energy_billed_gwh',
                           'target_revenue_amount', 'billed_amount',
                           'set_by_user_id', 'dn_created_at', 'dn_updated_at'],
            batch_size=BATCH,
        )
        stats['records_created'] = len(objects)
    except Exception as exc:
        stats['records_errored'] = len(objects)
        stats['errors'].append(str(exc))
        logger.exception('[TMOSync] tmo_billing_efficiency error: %s', exc)

    return stats


def _empty_stats():
    from django.utils import timezone
    now = timezone.now()
    return {
        'window_start':    now,
        'window_end':      now,
        'records_fetched': 0,
        'records_created': 0,
        'records_updated': 0,
        'records_skipped': 0,
        'records_deleted': 0,
        'records_errored': 0,
        'errors':          [],
    }
