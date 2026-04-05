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
