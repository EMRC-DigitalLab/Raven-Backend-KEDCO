"""
commercial/tasks.py

Celery tasks driving the DataNest -> Raven incremental sync for the
Commercial module. Each task:

  1. Creates a DataSyncLog row with status='running'.
  2. Calls the sync service (returns a stats dict).
  3. Finalises the log row (status=success|partial|error).

Schedules (registered in raven/celery.py):
  - sync_commercial_readings_task  -> every 5 minutes
  - sync_commercial_customers_task -> every 60 minutes
  - sync_commercial_managers_task  -> every 60 minutes
  - sync_commercial_tariff_task    -> every 60 minutes
"""

import logging

from celery import shared_task
from django.utils import timezone

from technical.models import DataSyncLog

logger = logging.getLogger(__name__)


def _run_sync(data_type: str, sync_fn):
    log = DataSyncLog.objects.create(data_type=data_type, status='running')
    try:
        stats = sync_fn()

        has_errors = bool(stats.get('errors'))
        log.status = 'partial' if has_errors else 'success'
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
            '[CommercialSync] %s -> %s | created=%d updated=%d skipped=%d errored=%d',
            data_type, log.status,
            log.records_created, log.records_updated,
            log.records_skipped, log.records_errored,
        )

    except Exception as exc:
        log.status = 'error'
        log.completed_at = timezone.now()
        log.error_message = str(exc)
        log.save()
        logger.exception('[CommercialSync] %s -> UNHANDLED ERROR: %s', data_type, exc)
        raise


@shared_task(
    name='commercial.tasks.sync_commercial_readings_task',
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def sync_commercial_readings_task(self):
    """Sync DataNest meter_readings -> MeterReading. Runs every 5 minutes."""
    from commercial.sync.readings import run_sync
    try:
        _run_sync('commercial_readings', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(
    name='commercial.tasks.sync_commercial_customers_task',
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def sync_commercial_customers_task(self):
    """Sync DataNest customer_information -> CommercialCustomer. Runs every hour."""
    from commercial.sync.customers import run_sync
    try:
        _run_sync('commercial_customers', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(
    name='commercial.tasks.sync_commercial_managers_task',
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def sync_commercial_managers_task(self):
    """Sync DataNest feeder_managers + assignments. Runs every hour."""
    from commercial.sync.managers import run_sync
    try:
        _run_sync('commercial_managers', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(
    name='commercial.tasks.sync_commercial_tariff_task',
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def sync_commercial_tariff_task(self):
    """Sync DataNest tariff_rates -> TariffRate. Runs every hour."""
    from commercial.sync.tariff_rates import run_sync
    try:
        _run_sync('commercial_tariff_rates', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)
