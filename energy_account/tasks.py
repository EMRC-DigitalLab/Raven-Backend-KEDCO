"""
energy_account/tasks.py

Celery tasks for DataNest → Raven EA sync.
Follows the exact same _run_sync pattern as technical/tasks.py.

Sync order respects FK dependencies:
  1. nbet_rates       — no deps
  2. ea_settings      — no deps
  3. grid_meters      — deps: Feeder, PowerTransformer (already in Raven)
  4. monthly_returns  — deps: InjectionSubstation
  5. monthly_readings — deps: EAMonthlyReturn, EAGridMeter
  6. feeder_technical_energy — deps: EAMonthlyReturn, Feeder
  7. tcn_reconciliation      — deps: EAMonthlyReturn
  8. tcn_reconciliation_notes— deps: EATCNReconciliation
  9. mo_reconciliation       — deps: EAMonthlyReturn
  10. weekly_readings        — deps: InjectionSubstation, Feeder
  11. station_assignments    — deps: InjectionSubstation
  12. meter_check_schedules  — deps: InjectionSubstation
  13. meter_check_records    — deps: EAMeterCheckSchedule
  14. coupling_log           — deps: InjectionSubstation, PowerTransformer

Schedules registered in raven/celery.py beat_schedule.
"""

import logging

from celery import shared_task
from django.utils import timezone

from technical.models import DataSyncLog

logger = logging.getLogger(__name__)


# ── shared helper (same as technical/tasks.py) ────────────────────────────────

def _run_sync(data_type: str, sync_fn, notify_on_success: bool = True):
    log = DataSyncLog.objects.create(data_type=data_type, status='running')
    try:
        stats = sync_fn()

        has_errors = bool(stats.get('errors'))
        status = 'partial' if has_errors else 'success'

        log.status          = status
        log.completed_at    = timezone.now()
        log.window_start    = stats.get('window_start')
        log.window_end      = stats.get('window_end')
        log.records_fetched = stats.get('records_fetched', 0)
        log.records_created = stats.get('records_created', 0)
        log.records_updated = stats.get('records_updated', 0)
        log.records_skipped = stats.get('records_skipped', 0)
        log.records_deleted = stats.get('records_deleted', 0)
        log.records_errored = stats.get('records_errored', 0)
        log.error_message   = '\n'.join(stats.get('errors', []))
        log.save()

        logger.info(
            '[EA Sync] %s → %s | created=%d updated=%d skipped=%d errored=%d',
            data_type, status,
            log.records_created, log.records_updated,
            log.records_skipped, log.records_errored,
        )

        _emit_sync_notification(data_type, status, log, notify_on_success)

    except Exception as exc:
        log.status        = 'error'
        log.completed_at  = timezone.now()
        log.error_message = str(exc)
        log.save()
        logger.exception('[EA Sync] %s → UNHANDLED ERROR: %s', data_type, exc)
        _emit_sync_notification(data_type, 'error', log, notify_on_success=True)
        raise


def _emit_sync_notification(data_type: str, status: str, log, notify_on_success: bool = True):
    """Call NotificationService.notify_datasync — fails silently so it never breaks a sync."""
    try:
        from notifications.services import NotificationService
        NotificationService.notify_datasync(data_type, status, log, notify_on_success)
    except Exception as exc:
        logger.warning('[EA Sync] notify failed silently for %s: %s', data_type, exc)


# ── tasks ──────────────────────────────────────────────────────────────────────

@shared_task(name='energy_account.tasks.sync_nbet_rates', bind=True, max_retries=2, default_retry_delay=60)
def sync_nbet_rates(self):
    from energy_account.sync.nbet_rates import run_sync
    try:
        _run_sync('ea_nbet_rates', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(name='energy_account.tasks.sync_ea_settings', bind=True, max_retries=2, default_retry_delay=60)
def sync_ea_settings(self):
    from energy_account.sync.ea_settings import run_sync
    try:
        _run_sync('ea_settings', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(name='energy_account.tasks.sync_grid_meters', bind=True, max_retries=2, default_retry_delay=60)
def sync_grid_meters(self):
    from energy_account.sync.grid_meters import run_sync
    try:
        _run_sync('ea_grid_meters', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(name='energy_account.tasks.sync_monthly_returns', bind=True, max_retries=2, default_retry_delay=60)
def sync_monthly_returns(self):
    from energy_account.sync.monthly_returns import run_sync
    try:
        _run_sync('ea_monthly_returns', run_sync, notify_on_success=False)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(name='energy_account.tasks.sync_monthly_readings', bind=True, max_retries=2, default_retry_delay=60)
def sync_monthly_readings(self):
    from energy_account.sync.monthly_readings import run_sync
    try:
        _run_sync('ea_monthly_readings', run_sync, notify_on_success=False)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(name='energy_account.tasks.sync_feeder_technical_energy', bind=True, max_retries=2, default_retry_delay=60)
def sync_feeder_technical_energy(self):
    from energy_account.sync.feeder_technical_energy import run_sync
    try:
        _run_sync('ea_feeder_technical_energy', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(name='energy_account.tasks.sync_tcn_reconciliation', bind=True, max_retries=2, default_retry_delay=60)
def sync_tcn_reconciliation(self):
    from energy_account.sync.tcn_reconciliation import run_sync
    try:
        _run_sync('ea_tcn_reconciliation', run_sync, notify_on_success=False)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(name='energy_account.tasks.sync_tcn_reconciliation_notes', bind=True, max_retries=2, default_retry_delay=60)
def sync_tcn_reconciliation_notes(self):
    from energy_account.sync.tcn_reconciliation_notes import run_sync
    try:
        _run_sync('ea_tcn_reconciliation_notes', run_sync, notify_on_success=False)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(name='energy_account.tasks.sync_mo_reconciliation', bind=True, max_retries=2, default_retry_delay=60)
def sync_mo_reconciliation(self):
    from energy_account.sync.mo_reconciliation import run_sync
    try:
        _run_sync('ea_mo_reconciliation', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(name='energy_account.tasks.sync_weekly_readings', bind=True, max_retries=2, default_retry_delay=60)
def sync_weekly_readings(self):
    from energy_account.sync.weekly_readings import run_sync
    try:
        _run_sync('ea_weekly_readings', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(name='energy_account.tasks.sync_station_assignments', bind=True, max_retries=2, default_retry_delay=60)
def sync_station_assignments(self):
    from energy_account.sync.station_assignments import run_sync
    try:
        _run_sync('ea_station_assignments', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(name='energy_account.tasks.sync_meter_check_schedules', bind=True, max_retries=2, default_retry_delay=60)
def sync_meter_check_schedules(self):
    from energy_account.sync.meter_check_schedules import run_sync
    try:
        _run_sync('ea_meter_check_schedules', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(name='energy_account.tasks.sync_meter_check_records', bind=True, max_retries=2, default_retry_delay=60)
def sync_meter_check_records(self):
    from energy_account.sync.meter_check_records import run_sync
    try:
        _run_sync('ea_meter_check_records', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(name='energy_account.tasks.sync_coupling_log', bind=True, max_retries=2, default_retry_delay=60)
def sync_coupling_log(self):
    from energy_account.sync.coupling_log import run_sync
    try:
        _run_sync('ea_coupling_log', run_sync)
    except Exception as exc:
        raise self.retry(exc=exc)
