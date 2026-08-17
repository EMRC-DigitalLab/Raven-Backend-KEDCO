# tmo/tasks.py
import logging
from datetime import date

import resend
from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def _start_sync_run(feed_type: str):
    """Record the start of a sync attempt — real history, not just 'the latest
    run', so intermittent/silent failures leave a trace instead of vanishing
    the moment a later run succeeds."""
    from tmo.models import SheetSyncRun
    return SheetSyncRun.objects.create(feed_type=feed_type, started_at=timezone.now(), status='failed')


def _finish_sync_run(run, status: str, result):
    run.finished_at = timezone.now()
    run.status = status
    run.result = str(result)
    run.save(update_fields=['finished_at', 'status', 'result'])


def _alert_final_failure(task_self, feed_type: str, year: int, month: int, exc: Exception):
    """Only fires once retries are genuinely exhausted — a task's 1st/2nd
    failed attempt is expected/normal (transient network blip, etc.) and
    stays a quiet retry; only the FINAL failure is worth waking someone up
    for. Previously nothing ever alerted on this — a task could exhaust all
    3 retries and just stop, with no signal to anyone that it had failed."""
    if task_self.request.retries >= task_self.max_retries:
        _send_resend_email(
            subject=f'[Raven] ALERT: {feed_type} sync failed after all retries ({year}-{month:02d})',
            body=(
                f'{feed_type} sync for {year}-{month:02d} failed and exhausted all '
                f'{task_self.max_retries} retries.\n\nLast error:\n{exc}'
            ),
        )


def _send_resend_email(subject: str, body: str):
    """Send a plain-text alert email to all active SheetAlertEmail recipients via Resend."""
    from tmo.models import SheetAlertEmail
    recipients = list(
        SheetAlertEmail.objects.filter(is_active=True).values_list('email', flat=True)
    )
    if not recipients:
        logger.warning('No active SheetAlertEmail recipients — alert not sent: %s', subject)
        return
    resend.api_key = settings.RESEND_API_KEY
    try:
        resend.Emails.send({
            'from':    settings.RESEND_FROM_EMAIL,
            'to':      recipients,
            'subject': subject,
            'text':    body,
        })
        logger.info('Alert email sent: %s → %s', subject, recipients)
    except Exception as exc:
        logger.error('Failed to send alert email "%s": %s', subject, exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def sync_33kv_sheet_task(self):
    """
    Hourly Celery task: sync the active 33KV Load Flow Google Sheet for the current month.

    Reliability behaviour:
      - force=True: every day of the month is re-checked on every run, not just
        the last 3 days — the source sheet gets corrected retroactively at
        unpredictable points, so completeness alone isn't a safe signal to
        stop re-checking a day
      - DataNest (dso) submissions are never overwritten — protected by their
        own always-on check inside sync_33kv_sheet, independent of force
      - No active feed for this month → alert email sent
      - Failure → retries up to 3× with 5-minute delay
    """
    today = date.today()
    year, month = today.year, today.month
    run = _start_sync_run('33kv_load_flow')

    try:
        from tmo.models import GoogleSheetFeed
        from tmo.sheet_sync import sync_33kv_sheet

        feed = GoogleSheetFeed.objects.filter(
            feed_type='33kv_load_flow', year=year, month=month, is_active=True
        ).first()

        if not feed:
            msg = (
                f'No active 33KV Load Flow Google Sheet registered for '
                f'{year}-{month:02d}.\n\n'
                f'Please register the link at POST /api/tmo/sheet-feeds/ '
                f'with feed_type=33kv_load_flow.'
            )
            logger.warning('sync_33kv_sheet_task: %s', msg)
            _send_resend_email(
                subject=f'[Raven] ALERT: No 33KV sheet registered for {year}-{month:02d}',
                body=msg,
            )
            _finish_sync_run(run, 'failed', msg)
            return {'status': 'no_feed'}

        logger.info('sync_33kv_sheet_task: starting for %s', feed)

        result = sync_33kv_sheet(
            spreadsheet_id=feed.spreadsheet_id,
            year=year,
            month=month,
            # Always re-check every day of the month, not just the last 3 —
            # the source sheet gets corrected retroactively at unpredictable
            # points in the month, and force=False would silently miss those
            # once a day already has "enough" rows. Safe regardless: DataNest
            # (dso) submissions are protected by their own always-on check in
            # sync_33kv_sheet, independent of this flag.
            force=True,
            dry_run=False,
        )

        feed.last_synced_at = timezone.now()
        feed.last_sync_log  = str(result)
        feed.save(update_fields=['last_synced_at', 'last_sync_log'])
        _finish_sync_run(run, 'success', result)

        logger.info(
            'sync_33kv_sheet_task done: %d days, %d HL rows, %d dispatch-hours',
            result['days_synced'], result['hl_rows'], result['dispatch_days'],
        )
        return result

    except Exception as exc:
        logger.exception('sync_33kv_sheet_task failed')
        _finish_sync_run(run, 'failed', exc)
        _alert_final_failure(self, '33kv_load_flow', year, month, exc)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def sync_11kv_sheet_task(self):
    """
    Hourly Celery task: sync the active 11KV Load Flow Google Sheet for the current month.

    Only fills slots that DataNest has not already provided — dso submissions are never
    overwritten. Both 11KV and 33KV feeder rows in the sheet are processed.
    """
    today = date.today()
    year, month = today.year, today.month
    run = _start_sync_run('11kv_load_flow')

    try:
        from tmo.models import GoogleSheetFeed
        from tmo.sheet_sync import sync_11kv_sheet

        feed = GoogleSheetFeed.objects.filter(
            feed_type='11kv_load_flow', year=year, month=month, is_active=True
        ).first()

        if not feed:
            msg = (
                f'No active 11KV Load Flow Google Sheet registered for '
                f'{year}-{month:02d}.\n\n'
                f'Please register the link at POST /api/tmo/sheet-feeds/ '
                f'with feed_type=11kv_load_flow.'
            )
            logger.warning('sync_11kv_sheet_task: %s', msg)
            _send_resend_email(
                subject=f'[Raven] ALERT: No 11KV sheet registered for {year}-{month:02d}',
                body=msg,
            )
            _finish_sync_run(run, 'failed', msg)
            return {'status': 'no_feed'}

        logger.info('sync_11kv_sheet_task: starting for %s', feed)

        result = sync_11kv_sheet(
            spreadsheet_id=feed.spreadsheet_id,
            year=year,
            month=month,
            # See sync_33kv_sheet_task — always re-check every day for
            # retroactive corrections. DataNest (dso) slots stay protected
            # by their own always-on check inside sync_11kv_sheet.
            force=True,
            dry_run=False,
        )

        feed.last_synced_at = timezone.now()
        feed.last_sync_log  = str(result)
        feed.save(update_fields=['last_synced_at', 'last_sync_log'])
        _finish_sync_run(run, 'success', result)

        logger.info(
            'sync_11kv_sheet_task done: %d days, %d HL rows',
            result['days_synced'], result['hl_rows'],
        )
        return result

    except Exception as exc:
        logger.exception('sync_11kv_sheet_task failed')
        _finish_sync_run(run, 'failed', exc)
        _alert_final_failure(self, '11kv_load_flow', year, month, exc)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def sync_33kv_energy_sheet_task(self):
    """
    Hourly Celery task: sync the active 33KV Energy Accounting Google Sheet for the
    current month into CumulativeMeterReading + EnergyDelivered.

    DSO data wins: existing 'dso' readings are never overwritten by sheet data.
    """
    today = date.today()
    year, month = today.year, today.month
    run = _start_sync_run('33kv_energy_accounting')

    try:
        from tmo.models import GoogleSheetFeed
        from tmo.sheet_sync import sync_33kv_energy_sheet

        feed = GoogleSheetFeed.objects.filter(
            feed_type='33kv_energy_accounting', year=year, month=month, is_active=True
        ).first()

        if not feed:
            msg = (
                f'No active 33KV Energy Accounting Google Sheet registered for '
                f'{year}-{month:02d}.\n\n'
                f'Please register the link at POST /api/tmo/sheet-feeds/ '
                f'with feed_type=33kv_energy_accounting.'
            )
            logger.warning('sync_33kv_energy_sheet_task: %s', msg)
            _send_resend_email(
                subject=f'[Raven] ALERT: No 33KV energy sheet registered for {year}-{month:02d}',
                body=msg,
            )
            _finish_sync_run(run, 'failed', msg)
            return {'status': 'no_feed'}

        logger.info('sync_33kv_energy_sheet_task: starting for %s', feed)

        result = sync_33kv_energy_sheet(
            spreadsheet_id=feed.spreadsheet_id,
            year=year,
            month=month,
            # See sync_33kv_sheet_task — always re-check every day for
            # retroactive corrections. DataNest (dso) readings stay protected
            # by their own always-on check inside sync_33kv_energy_sheet.
            force=True,
            dry_run=False,
        )

        feed.last_synced_at = timezone.now()
        feed.last_sync_log  = str(result)
        feed.save(update_fields=['last_synced_at', 'last_sync_log'])
        _finish_sync_run(run, 'success', result)

        logger.info(
            'sync_33kv_energy_sheet_task done: %d days, %d CMR rows',
            result['days_synced'], result['cmr_rows'],
        )
        return result

    except Exception as exc:
        logger.exception('sync_33kv_energy_sheet_task failed')
        _finish_sync_run(run, 'failed', exc)
        _alert_final_failure(self, '33kv_energy_accounting', year, month, exc)
        raise self.retry(exc=exc)


@shared_task
def send_monthly_sheet_reminder_task():
    """
    Runs on the 25th of each month.
    Sends a reminder email for any feed type that has no registered sheet for next month.
    """
    today = date.today()
    if today.month == 12:
        next_year, next_month = today.year + 1, 1
    else:
        next_year, next_month = today.year, today.month + 1

    from tmo.models import GoogleSheetFeed

    feed_labels = [
        ('33kv_load_flow',         '33KV Load Flow'),
        ('33kv_energy_accounting', '33KV Energy Accounting'),
        ('11kv_load_flow',         '11KV Load Flow'),
        ('11kv_energy_accounting', '11KV Energy Accounting'),
    ]

    missing = []
    for feed_type, label in feed_labels:
        exists = GoogleSheetFeed.objects.filter(
            feed_type=feed_type, year=next_year, month=next_month, is_active=True
        ).exists()
        if not exists:
            missing.append(label)

    if not missing:
        logger.info('send_monthly_sheet_reminder_task: all feeds registered for %d-%02d', next_year, next_month)
        return {'status': 'all_registered'}

    month_label = date(next_year, next_month, 1).strftime('%B %Y')
    missing_list = '\n'.join(f'  - {m}' for m in missing)
    body = (
        f'The following Google Sheet feeds have not been registered for {month_label}:\n\n'
        f'{missing_list}\n\n'
        f'Please register each link before the 1st of the month via the Raven TMO settings page '
        f'or POST /api/tmo/sheet-feeds/\n\n'
        f'If not registered in time, data sync for {month_label} will not start automatically '
        f'and an alert will be sent on the 1st.'
    )

    logger.warning('send_monthly_sheet_reminder_task: missing feeds for %d-%02d: %s', next_year, next_month, missing)
    _send_resend_email(
        subject=f'[Raven] Reminder: Register Google Sheet feeds for {month_label}',
        body=body,
    )
    return {'status': 'reminder_sent', 'missing': missing}
