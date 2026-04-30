"""
technical/tasks.py

Celery tasks that drive the DataNest → Raven incremental sync for the
Technical module.  Each task:

  1. Writes a DataSyncLog row with status='running'.
  2. Calls the corresponding sync service (returns a stats dict).
  3. Finalises the log row (status=success|partial|error, counts, watermark).

Schedules (registered in raven/settings.py CELERY_BEAT_SCHEDULE):
  - sync_hourly_load_task      → every 15 minutes
  - sync_interruptions_task    → every 15 minutes
  - sync_meter_readings_task   → every 4 hours
"""

import logging

from celery import shared_task
from django.utils import timezone

from technical.models import DataSyncLog

logger = logging.getLogger(__name__)


# ── shared helper ──────────────────────────────────────────────────────────────

def _run_sync(data_type: str, sync_fn):
    """
    Boilerplate wrapper around any sync service function.

    sync_fn must return the stats dict produced by run_sync() in each
    service module.
    """
    log = DataSyncLog.objects.create(data_type=data_type, status='running')
    try:
        stats = sync_fn()

        has_errors = bool(stats.get('errors'))
        status = 'partial' if has_errors else 'success'

        log.status = status
        log.completed_at = timezone.now()
        log.window_start = stats.get('window_start')
        log.window_end = stats.get('window_end')
        log.records_fetched = stats.get('records_fetched', 0)
        log.records_created = stats.get('records_created', 0)
        log.records_updated = stats.get('records_updated', 0)
        log.records_skipped = stats.get('records_skipped', 0)
        log.records_deleted = stats.get('records_deleted', 0)
        log.records_errored = stats.get('records_errored', 0)
        log.error_message = '\n'.join(stats.get('errors', []))
        log.save()

        logger.info(
            '[DataSync] %s → %s | created=%d updated=%d skipped=%d errored=%d',
            data_type, status,
            log.records_created, log.records_updated,
            log.records_skipped, log.records_errored,
        )

    except Exception as exc:
        log.status = 'error'
        log.completed_at = timezone.now()
        log.error_message = str(exc)
        log.save()
        logger.exception('[DataSync] %s → UNHANDLED ERROR: %s', data_type, exc)
        raise  # let Celery handle retry / failure tracking


# ── tasks ──────────────────────────────────────────────────────────────────────

@shared_task(
    name='technical.tasks.sync_hourly_load_task',
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def sync_hourly_load_task(self):
    """Sync Technicalhourlydata → HourlyLoad. Runs every 15 minutes."""
    from technical.sync.hourly_load import run_sync
    try:
        _run_sync('technical_hourly_load', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(
    name='technical.tasks.sync_interruptions_task',
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def sync_interruptions_task(self):
    """Sync technicalenergyfault → FeederInterruption. Runs every 15 minutes."""
    from technical.sync.interruptions import run_sync
    try:
        _run_sync('technical_interruptions', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(
    name='technical.tasks.sync_meter_readings_task',
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def sync_meter_readings_task(self):
    """Sync techicalenergyreadingdailydta → CumulativeMeterReading. Runs every 4 hours."""
    from technical.sync.meter_readings import run_sync
    try:
        _run_sync('technical_meter_readings', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(
    name='technical.tasks.backfill_technical_data_task',
    bind=True,
    max_retries=0,          # No automatic retry — caller decides
    time_limit=3600,        # Hard 1-hour ceiling
    soft_time_limit=3300,   # Soft 55-minute warning
)
def backfill_technical_data_task(self, start_date_str: str, end_date_str: str,
                                  do_hourly: bool = True, do_energy: bool = True):
    """
    Manual backfill of hourly load and/or energy readings for a fixed date range.

    Triggered from the API (POST /api/technical/sync/backfill/).
    Processes in 7-day chunks and updates task meta so the frontend can poll
    progress via GET /api/technical/sync/backfill/<job_id>/.

    start_date_str / end_date_str: ISO format strings 'YYYY-MM-DD'.
    """
    import datetime
    from technical.sync.hourly_load  import run_sync as hl_sync
    from technical.sync.meter_readings import run_sync as mr_sync

    CHUNK_DAYS = 7

    start_date = datetime.date.fromisoformat(start_date_str)
    end_date   = datetime.date.fromisoformat(end_date_str)

    # Build chunks
    chunks = []
    cs = start_date
    while cs <= end_date:
        ce = min(cs + datetime.timedelta(days=CHUNK_DAYS - 1), end_date)
        chunks.append((cs, ce))
        cs = ce + datetime.timedelta(days=1)

    total_chunks = len(chunks)
    totals = {
        'hourly':  {'created': 0, 'updated': 0, 'deleted': 0, 'errored': 0, 'errors': []},
        'energy':  {'created': 0, 'updated': 0, 'deleted': 0, 'errored': 0, 'errors': []},
    }

    def _update_meta(chunk_idx, current_label=''):
        self.update_state(state='PROGRESS', meta={
            'chunk': chunk_idx,
            'total_chunks': total_chunks,
            'pct': round(chunk_idx / total_chunks * 100) if total_chunks else 100,
            'current_range': current_label,
            'totals': totals,
        })

    for idx, (cs, ce) in enumerate(chunks):
        label = f'{cs} → {ce}'
        _update_meta(idx, label)

        if do_hourly:
            try:
                stats = hl_sync(override_start=cs, override_end=ce)
                _save_sync_log('technical_hourly_load', stats)
                t = totals['hourly']
                t['created'] += stats.get('records_created', 0)
                t['updated'] += stats.get('records_updated', 0)
                t['deleted'] += stats.get('records_deleted', 0)
                t['errored'] += stats.get('records_errored', 0)
                t['errors'].extend(stats.get('errors', []))
            except Exception as exc:
                totals['hourly']['errors'].append(f'{label}: {exc}')
                logger.exception('[Backfill] hourly load %s error: %s', label, exc)

        if do_energy:
            try:
                stats = mr_sync(override_start=cs, override_end=ce)
                _save_sync_log('technical_meter_readings', stats)
                t = totals['energy']
                t['created'] += stats.get('records_created', 0)
                t['updated'] += stats.get('records_updated', 0)
                t['deleted'] += stats.get('records_deleted', 0)
                t['errored'] += stats.get('records_errored', 0)
                t['errors'].extend(stats.get('errors', []))
            except Exception as exc:
                totals['energy']['errors'].append(f'{label}: {exc}')
                logger.exception('[Backfill] energy readings %s error: %s', label, exc)

    _update_meta(total_chunks)
    return {
        'status': 'done',
        'start_date': start_date_str,
        'end_date': end_date_str,
        'totals': totals,
    }


def _save_sync_log(data_type: str, stats: dict):
    """Write a DataSyncLog entry for a single backfill chunk."""
    has_errors = bool(stats.get('errors'))
    DataSyncLog.objects.create(
        data_type=data_type,
        status='partial' if has_errors else 'success',
        window_start=stats.get('window_start'),
        window_end=stats.get('window_end'),
        records_fetched=stats.get('records_fetched', 0),
        records_created=stats.get('records_created', 0),
        records_updated=stats.get('records_updated', 0),
        records_skipped=stats.get('records_skipped', 0),
        records_deleted=stats.get('records_deleted', 0),
        records_errored=stats.get('records_errored', 0),
        error_message='\n'.join(stats.get('errors', [])),
        completed_at=timezone.now(),
    )
