# tmo/tasks.py
import logging
from datetime import date

import resend
from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


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
      - Today + last 2 days → always re-synced (catches live data + corrections)
      - Older past days with partial/missing data → retried automatically
      - No active feed for this month → alert email sent
      - Failure → retries up to 3× with 5-minute delay
    """
    today = date.today()
    year, month = today.year, today.month

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
            return {'status': 'no_feed'}

        logger.info('sync_33kv_sheet_task: starting for %s', feed)

        result = sync_33kv_sheet(
            spreadsheet_id=feed.spreadsheet_id,
            year=year,
            month=month,
            force=False,
            dry_run=False,
        )

        feed.last_synced_at = timezone.now()
        feed.last_sync_log  = str(result)
        feed.save(update_fields=['last_synced_at', 'last_sync_log'])

        logger.info(
            'sync_33kv_sheet_task done: %d days, %d HL rows, %d dispatch-hours',
            result['days_synced'], result['hl_rows'], result['dispatch_days'],
        )
        return result

    except Exception as exc:
        logger.exception('sync_33kv_sheet_task failed')
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
            return {'status': 'no_feed'}

        logger.info('sync_11kv_sheet_task: starting for %s', feed)

        result = sync_11kv_sheet(
            spreadsheet_id=feed.spreadsheet_id,
            year=year,
            month=month,
            force=False,
            dry_run=False,
        )

        feed.last_synced_at = timezone.now()
        feed.last_sync_log  = str(result)
        feed.save(update_fields=['last_synced_at', 'last_sync_log'])

        logger.info(
            'sync_11kv_sheet_task done: %d days, %d HL rows',
            result['days_synced'], result['hl_rows'],
        )
        return result

    except Exception as exc:
        logger.exception('sync_11kv_sheet_task failed')
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
            return {'status': 'no_feed'}

        logger.info('sync_33kv_energy_sheet_task: starting for %s', feed)

        result = sync_33kv_energy_sheet(
            spreadsheet_id=feed.spreadsheet_id,
            year=year,
            month=month,
            force=False,
            dry_run=False,
        )

        feed.last_synced_at = timezone.now()
        feed.last_sync_log  = str(result)
        feed.save(update_fields=['last_synced_at', 'last_sync_log'])

        logger.info(
            'sync_33kv_energy_sheet_task done: %d days, %d CMR rows',
            result['days_synced'], result['cmr_rows'],
        )
        return result

    except Exception as exc:
        logger.exception('sync_33kv_energy_sheet_task failed')
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
