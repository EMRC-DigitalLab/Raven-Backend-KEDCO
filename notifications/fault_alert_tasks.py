# notifications/fault_alert_tasks.py
"""
Raven Realtime Fault Alert System — the trigger.

Runs every minute (see raven/celery.py beat_schedule). For every feeder on
the admin-managed watchlist (FaultAlertFeederWatch), checks its CURRENT
fault state and compares it to what was last alerted. Only fires an email
on an actual transition (clear -> faulted, or faulted -> clear) — a fault
that's still ongoing on the next check does not re-trigger anything.

Hybrid source rule (confirmed 2026-08-20 against live data — zero 33kV
FeederInterruption rows exist in 30 days, DSO simply never submits 33kV
fault data through DataNest):
  - 11kV: check DataNest's FeederInterruption first (real submitted fault
    data). Only fall back to the TMO sheet's HourlyLoad.fault_code if
    DataNest shows nothing currently open for that feeder — avoids a
    duplicate alert when both sources would otherwise report the same
    real-world fault.
  - 33kV: HourlyLoad.fault_code is the only source; there is nothing to
    dedupe against.
"""
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def _sheet_fault_onset(feeder, latest_row):
    """
    HourlyLoad is hourly-granularity source data, synced on its own schedule —
    the row the 1-minute checker happens to see first is very often NOT the
    hour the fault actually began, it's just whichever hour was faulted when
    the checker last ran. This walks backward hour-by-hour from latest_row,
    while fault_code stays set, to find the true first faulted hour in the
    current unbroken run — that hour's own timestamp becomes fault_started_at,
    not "whenever Raven happened to notice."
    """
    from datetime import datetime, timedelta

    from django.utils import timezone as tz

    from technical.models import HourlyLoad

    cursor_date, cursor_hour = latest_row.date, latest_row.hour
    onset_date, onset_hour = cursor_date, cursor_hour

    # Bounded walk — a feeder can't realistically be faulted for more than
    # ~90 days straight; this just guards against an unbounded loop on bad data.
    for _ in range(24 * 90):
        prev_hour = cursor_hour - 1
        prev_date = cursor_date
        if prev_hour < 0:
            prev_hour = 23
            prev_date = cursor_date - timedelta(days=1)

        prev_row = HourlyLoad.objects.filter(feeder=feeder, date=prev_date, hour=prev_hour).first()
        if not prev_row or not prev_row.fault_code:
            break  # hit a clear hour, or a gap in the data — the run ends here
        onset_date, onset_hour = prev_date, prev_hour
        cursor_date, cursor_hour = prev_date, prev_hour

    naive = datetime.combine(onset_date, datetime.min.time()) + timedelta(hours=onset_hour)
    return tz.make_aware(naive) if tz.is_naive(naive) else naive


def _current_fault_state(feeder):
    """
    Returns (is_faulted, category, raw_code, onset_at) for a feeder right now.
    category/raw_code/onset_at are None when is_faulted is False. onset_at is
    the actual start of the fault (see _sheet_fault_onset), not the moment
    the checker happened to run.
    """
    from technical.models import FeederInterruption, HourlyLoad
    from tmo.sheet_sync import categorize_fault_code

    if feeder.voltage_level == '11kv':
        open_fault = (
            FeederInterruption.objects
            .filter(feeder=feeder, restored_at__isnull=True)
            .order_by('-occurred_at')
            .first()
        )
        if open_fault:
            return True, open_fault.get_interruption_type_display(), open_fault.interruption_type, open_fault.occurred_at

    # Sheet source — always for 33kV, fallback for 11kV when DataNest is silent.
    latest = (
        HourlyLoad.objects
        .filter(feeder=feeder)
        .order_by('-date', '-hour')
        .first()
    )
    if latest and latest.fault_code:
        onset_at = _sheet_fault_onset(feeder, latest)
        return True, categorize_fault_code(latest.fault_code), latest.fault_code, onset_at

    return False, None, None, None


def _segment_for(feeder):
    return feeder.pl_segment or 'Regions'


def _active_recipient_emails():
    from .models import FaultAlertRecipient
    return list(
        FaultAlertRecipient.objects
        .filter(is_active=True, user__email__isnull=False)
        .exclude(user__email='')
        .values_list('user__email', flat=True)
    )


def _fire_occurred(watch, category, raw_code, onset_at):
    from notifications.emails.fault_alerts import render_fault_occurred_email

    feeder = watch.feeder
    # onset_at is the fault's true start (see _sheet_fault_onset / DataNest's
    # own occurred_at) — never "now", which is only when the checker noticed it.
    detected_at_str = timezone.localtime(onset_at).strftime('%d %b %Y, %I:%M %p')
    email = render_fault_occurred_email(feeder, _segment_for(feeder), category, raw_code, detected_at_str)

    for to_email in _active_recipient_emails():
        send_fault_alert_email_task.delay(to_email, email['subject'], email['html'])

    watch.is_currently_faulted = True
    watch.current_fault_category = category
    watch.current_fault_raw_code = (raw_code or '')[:32]
    watch.fault_started_at = onset_at
    watch.save(update_fields=[
        'is_currently_faulted', 'current_fault_category',
        'current_fault_raw_code', 'fault_started_at',
    ])
    logger.info(f"[FaultAlert] OCCURRED: {feeder.name} ({category}), started {detected_at_str}")


def _fire_restored(watch):
    from notifications.emails.fault_alerts import render_fault_restored_email

    feeder = watch.feeder
    duration_hours = 0.0
    if watch.fault_started_at:
        duration_hours = (timezone.now() - watch.fault_started_at).total_seconds() / 3600
    restored_at_str = timezone.localtime().strftime('%d %b %Y, %I:%M %p')
    email = render_fault_restored_email(feeder, _segment_for(feeder), duration_hours, restored_at_str)

    for to_email in _active_recipient_emails():
        send_fault_alert_email_task.delay(to_email, email['subject'], email['html'])

    watch.is_currently_faulted = False
    watch.current_fault_category = None
    watch.current_fault_raw_code = None
    watch.fault_started_at = None
    watch.save(update_fields=[
        'is_currently_faulted', 'current_fault_category',
        'current_fault_raw_code', 'fault_started_at',
    ])
    logger.info(f"[FaultAlert] RESTORED: {feeder.name} (down {duration_hours:.1f}h)")


@shared_task(name='notifications.fault_alert_tasks.check_fault_alerts')
def check_fault_alerts_task():
    """The 1-minute Beat task. Detects transitions only — never re-alerts
    for a fault that's still ongoing since the last check."""
    from .models import FaultAlertFeederWatch

    watches = (
        FaultAlertFeederWatch.objects
        .filter(is_active=True)
        .select_related('feeder', 'feeder__band')
    )
    checked = 0
    fired = 0
    errored = 0
    for watch in watches:
        checked += 1
        # One feeder's failure (a Resend hiccup, anything) must not stop
        # every other watched feeder from being checked this minute -- the
        # original unguarded loop meant a single bad watch, once hit, would
        # silently block ALL feeders after it in iteration order on EVERY
        # run, forever, since the task has no retry and this ran every
        # minute regardless.
        try:
            is_faulted, category, raw_code, onset_at = _current_fault_state(watch.feeder)
            if is_faulted and not watch.is_currently_faulted:
                _fire_occurred(watch, category, raw_code, onset_at)
                fired += 1
            elif not is_faulted and watch.is_currently_faulted:
                _fire_restored(watch)
                fired += 1
        except Exception:
            errored += 1
            logger.exception(f"[FaultAlert] check failed for {watch.feeder.name} — continuing with remaining watches")
    if fired or errored:
        logger.info(f"[FaultAlert] check complete: {checked} watched, {fired} transition(s) fired, {errored} errored")
    return {'checked': checked, 'fired': fired, 'errored': errored}


@shared_task(bind=True, max_retries=3, default_retry_delay=60,
             name='notifications.fault_alert_tasks.send_fault_alert_email')
def send_fault_alert_email_task(self, to_email: str, subject: str, html: str):
    from .tasks import _resolve_to_email, _send_via_resend

    params = {
        'from': settings.RESEND_FROM_EMAIL,
        'to': [_resolve_to_email(to_email)],
        'subject': subject,
        'html': html,
    }
    try:
        _send_via_resend(params)
        logger.info(f"[FaultAlert] email sent to {to_email}")
    except Exception as exc:
        logger.error(f"[FaultAlert] failed to send to {to_email}: {exc}")
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
