# notifications/tasks.py
"""
Celery tasks for async email delivery via Resend.

Environment behaviour:
  - staging  → all emails redirected to RESEND_TEST_EMAIL (no real users receive mail)
  - production → emails sent to actual recipient addresses
"""
import base64
import logging
import os

import resend
from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


# ── Email HTML templates ──────────────────────────────────────────────────────

def _base_email_html(title: str, body_html: str, action_url: str = '', action_label: str = '') -> str:
    """Minimal branded HTML wrapper for all outgoing emails."""
    cta = ''
    if action_url and action_label:
        full_url = f"{settings.BASE_URL.rstrip('/')}{action_url}" if action_url.startswith('/') else action_url
        cta = f"""
        <div style="margin-top:24px;">
          <a href="{full_url}"
             style="background:#1a56db;color:#fff;padding:12px 24px;border-radius:6px;
                    text-decoration:none;font-size:14px;font-weight:600;">
            {action_label}
          </a>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
    <body style="margin:0;padding:0;background:#f4f6f8;font-family:Arial,sans-serif;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8;padding:32px 0;">
        <tr><td align="center">
          <table width="600" cellpadding="0" cellspacing="0"
                 style="background:#ffffff;border-radius:8px;overflow:hidden;
                        box-shadow:0 2px 8px rgba(0,0,0,0.08);">
            <!-- Header -->
            <tr>
              <td style="background:#1a56db;padding:24px 32px;">
                <span style="color:#ffffff;font-size:20px;font-weight:700;">KEDCO Raven</span>
              </td>
            </tr>
            <!-- Body -->
            <tr>
              <td style="padding:32px;">
                <h2 style="margin:0 0 16px;color:#111827;font-size:18px;">{title}</h2>
                <div style="color:#374151;font-size:14px;line-height:1.6;">
                  {body_html}
                </div>
                {cta}
              </td>
            </tr>
            <!-- Footer -->
            <tr>
              <td style="background:#f9fafb;padding:16px 32px;border-top:1px solid #e5e7eb;">
                <p style="margin:0;color:#6b7280;font-size:12px;">
                  This is an automated notification from KEDCO Raven.
                  Please do not reply to this email.
                </p>
              </td>
            </tr>
          </table>
        </td></tr>
      </table>
    </body>
    </html>
    """


def _resolve_to_email(recipient_email: str) -> str:
    """
    In staging, redirect all outgoing emails to the test address.
    In production, use the real recipient email.
    """
    if getattr(settings, 'APP_ENV', 'production') == 'staging':
        return settings.RESEND_TEST_EMAIL
    return recipient_email


def _send_via_resend(params: dict) -> bool:
    """
    Low-level wrapper around the Resend SDK.
    Returns True on success, False on failure (caller handles retry logic).
    """
    resend.api_key = settings.RESEND_API_KEY
    try:
        resend.Emails.send(params)
        return True
    except Exception as e:
        logger.error(f"Resend API error: {e}")
        raise  # Let Celery retry


# ── Tasks ─────────────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_notification_email(self, notification_id: int):
    """
    Send an in-app notification as an email via Resend.
    Called by NotificationService after bulk_create when send_email=True.
    """
    from .models import Notification

    try:
        notif = Notification.objects.select_related('recipient', 'sender').get(id=notification_id)
    except Notification.DoesNotExist:
        logger.error(f"Notification {notification_id} not found — skipping email.")
        return

    if notif.email_sent:
        return  # Already sent (idempotency guard)

    recipient_email = notif.recipient.email
    if not recipient_email:
        logger.warning(f"Recipient {notif.recipient.username} has no email — skipping.")
        return

    body_html = f"<p>{notif.message}</p>"
    html = _base_email_html(
        title=notif.title,
        body_html=body_html,
        action_url=notif.action_url,
        action_label="View in Raven" if notif.action_url else '',
    )

    params = {
        "from": settings.RESEND_FROM_EMAIL,
        "to": [_resolve_to_email(recipient_email)],
        "subject": notif.title,
        "html": html,
    }

    try:
        _send_via_resend(params)
        notif.email_sent = True
        notif.email_sent_at = timezone.now()
        notif.save(update_fields=['email_sent', 'email_sent_at'])
        logger.info(f"Email sent for notification {notification_id} to {recipient_email}")
    except Exception as exc:
        logger.error(f"Failed to send email for notification {notification_id}: {exc}")
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_report_email(self, report_recipient_id: int):
    """
    Send a shared report via email with the PDF attached (if available).
    Called by NotificationService.share_report() after creating ReportRecipient records.
    """
    from .models import ReportRecipient

    try:
        rr = ReportRecipient.objects.select_related('recipient', 'sender').get(id=report_recipient_id)
    except ReportRecipient.DoesNotExist:
        logger.error(f"ReportRecipient {report_recipient_id} not found — skipping.")
        return

    if rr.email_status == ReportRecipient.EmailStatus.SENT:
        return  # Already sent

    recipient_email = rr.recipient.email
    if not recipient_email:
        logger.warning(f"ReportRecipient {rr.recipient.username} has no email — skipping.")
        rr.email_status = ReportRecipient.EmailStatus.FAILED
        rr.save(update_fields=['email_status'])
        return

    sender_name = rr.sender.get_full_name() or rr.sender.username
    personal_message = f"<p><em>{rr.message}</em></p>" if rr.message else ''

    body_html = f"""
    <p>Hello {rr.recipient.first_name or rr.recipient.username},</p>
    <p><strong>{sender_name}</strong> has shared a report with you:</p>
    <p style="font-size:16px;font-weight:600;color:#1a56db;">{rr.report_title}</p>
    {personal_message}
    <p>{'The report is attached to this email as a PDF.' if rr.report_file_path else 'Please log in to Raven to view the report.'}</p>
    """

    html = _base_email_html(
        title=f"Report: {rr.report_title}",
        body_html=body_html,
        action_url=f'/reports/{rr.report_type}/{rr.report_object_id}',
        action_label="View Report in Raven",
    )

    params = {
        "from": settings.RESEND_FROM_EMAIL,
        "to": [_resolve_to_email(recipient_email)],
        "subject": f"Report shared with you: {rr.report_title}",
        "html": html,
    }

    # Attach the PDF if the file exists on disk
    if rr.report_file_path and os.path.exists(rr.report_file_path):
        try:
            with open(rr.report_file_path, 'rb') as f:
                encoded = base64.b64encode(f.read()).decode('utf-8')
            params["attachments"] = [{
                "filename": f"{rr.report_title}.pdf",
                "content": encoded,
            }]
        except Exception as e:
            logger.warning(f"Could not attach PDF for ReportRecipient {report_recipient_id}: {e}")

    try:
        _send_via_resend(params)
        rr.email_status = ReportRecipient.EmailStatus.SENT
        rr.email_sent_at = timezone.now()
        rr.save(update_fields=['email_status', 'email_sent_at'])
        logger.info(f"Report email sent for ReportRecipient {report_recipient_id} to {recipient_email}")
    except Exception as exc:
        rr.email_status = ReportRecipient.EmailStatus.FAILED
        rr.save(update_fields=['email_status'])
        logger.error(f"Failed to send report email for ReportRecipient {report_recipient_id}: {exc}")
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


# ── Recipient helper ──────────────────────────────────────────────────────────

def _recipients_for_module(category: str):
    """
    Return users who should receive notifications for a given module category.
    Includes:
      - super_admin and admin (always)
      - Any user with an active UserSectionAccess grant for this category's section
    """
    from django.contrib.auth import get_user_model
    from django.db.models import Q
    from users.models import UserSectionAccess

    User = get_user_model()
    return User.objects.filter(
        Q(role__in=['super_admin', 'admin'], is_active=True) |
        Q(section_access__section__name=category, section_access__is_active=True, is_active=True)
    ).distinct()


# ── End-of-day scheduled checks ───────────────────────────────────────────────

def _build_fault_category_map():
    """
    Returns a dict of {interruption_type_code: category_label} from FaultTypeCategory.
    Any code NOT in the table is treated as DisCo fault — matching the model's own rule.
    e.g. {'L/S': 'L/S (Load Shedding)', '132KV E/F': 'TCN / Grid', 'O/C': 'DisCo Fault'}
    """
    from technical.models import FaultTypeCategory

    CATEGORY_LABELS = {
        'ls':    'L/S (Load Shedding)',
        'tcn':   'TCN / Grid',
        'disco': 'DisCo Fault',
    }
    return {
        ftc.code: CATEGORY_LABELS.get(ftc.category, 'DisCo Fault')
        for ftc in FaultTypeCategory.objects.all()
    }


def _classify_fault(interruption_type: str, fault_map: dict) -> str:
    """Classify a single interruption type code. Defaults to DisCo if not mapped."""
    return fault_map.get(interruption_type, 'DisCo Fault')


@shared_task
def daily_band_a_supply_check():
    """
    Runs at end of day (23:30 Africa/Lagos via Celery Beat).

    Three outcomes:
      1. No supply data entered at all for Band A today → warns that data is missing.
      2. All Band A feeders met the 20-hour SBT minimum → silent success log only.
      3. Some feeders fell short → urgent notification listing each feeder,
         hours supplied, shortfall, and the fault type breakdown (L/S / TCN / DisCo)
         from their interruption records for the day.
    """
    from datetime import date

    from technical.models import DailyHoursOfSupply, FeederInterruption

    from .services import NotificationService

    BAND_A_MINIMUM_HOURS = 20
    today = date.today()
    recipients = _recipients_for_module('technical')

    # ── Check if any data was entered at all for Band A today ─────────────────
    all_band_a_records = DailyHoursOfSupply.objects.filter(
        date=today,
        feeder__band__name__iexact='A',
    )

    if not all_band_a_records.exists():
        # No data entered — this itself needs attention
        title = f"No Band A Supply Data — {today}"
        message = (
            f"No hours-of-supply data has been entered for any Band A feeder on {today}. "
            f"Please ensure feeder data is uploaded before end of day."
        )
        NotificationService.notify(
            title=title,
            message=message,
            notification_type='action',
            category='technical',
            recipients=recipients,
            priority='high',
            send_email=True,
            action_url='/technical/feeders?band=A',
            metadata={'date': str(today), 'issue': 'no_data'},
        )
        logger.warning(f"[Band A Check {today}] No supply data found for Band A feeders.")
        return f"No Band A supply data on {today}"

    # ── Find feeders that fell short of 20 hours ──────────────────────────────
    defaulting = (
        all_band_a_records
        .filter(hours_supplied__lt=BAND_A_MINIMUM_HOURS)
        .select_related('feeder', 'feeder__band', 'feeder__business_district')
        .order_by('feeder__name')
    )

    count = defaulting.count()

    if count == 0:
        logger.info(f"[Band A Check {today}] All Band A feeders met the 20-hour SBT minimum.")
        return f"All Band A feeders met SBT minimum on {today}"

    # Build fault category lookup (one DB query for the whole map)
    fault_map = _build_fault_category_map()

    # For each defaulting feeder, pull today's interruptions and group by fault category
    feeder_detail_lines = []
    metadata_feeders = []

    for r in defaulting:
        district = getattr(r.feeder.business_district, 'name', '') if r.feeder.business_district else ''
        shortfall = float(BAND_A_MINIMUM_HOURS - r.hours_supplied)

        # Get today's interruptions for this feeder
        interruptions = FeederInterruption.objects.filter(
            feeder=r.feeder,
            occurred_at__date=today,
        ).values_list('interruption_type', flat=True)

        # Group by fault category and count occurrences
        fault_tally = {}
        for itype in interruptions:
            label = _classify_fault(itype, fault_map)
            fault_tally[label] = fault_tally.get(label, 0) + 1

        # Format fault breakdown: "DisCo Fault ×3, L/S (Load Shedding) ×1"
        if fault_tally:
            fault_summary = ', '.join(
                f"{label} ×{cnt}" for label, cnt in sorted(fault_tally.items())
            )
        else:
            fault_summary = 'No interruption records found'

        line = f"• {r.feeder.name}"
        if district:
            line += f" ({district})"
        line += (
            f" — {float(r.hours_supplied):.1f} hrs supplied, "
            f"{shortfall:.1f} hrs short of SBT | Faults: {fault_summary}"
        )
        feeder_detail_lines.append(line)

        metadata_feeders.append({
            'feeder_id': r.feeder.pk,
            'feeder_name': r.feeder.name,
            'hours_supplied': float(r.hours_supplied),
            'shortfall': shortfall,
            'fault_breakdown': fault_tally,
        })

    feeder_list_text = "\n".join(feeder_detail_lines)

    title = f"Band A SBT Breach — {count} feeder{'s' if count != 1 else ''} below 20-hour minimum ({today})"
    message = (
        f"{count} Band A feeder{'s' if count != 1 else ''} did not meet the 20-hour SBT minimum "
        f"for {today}.\n\n"
        f"{feeder_list_text}\n\n"
        f"Fault categories: L/S = Load Shedding, TCN = Transmission/Grid fault, "
        f"DisCo = fault under KEDCO's control."
    )

    NotificationService.notify(
        title=title,
        message=message,
        notification_type='action',
        category='technical',
        recipients=recipients,
        priority='urgent',
        send_email=True,
        action_url='/technical/feeders?band=A',
        metadata={
            'date': str(today),
            'defaulting_count': count,
            'minimum_hours': BAND_A_MINIMUM_HOURS,
            'defaulting_feeders': metadata_feeders,
        },
    )

    logger.info(f"[Band A Check {today}] {count} feeders below SBT — notification sent.")
    return f"{count} Band A feeders below SBT on {today}"


@shared_task
def daily_restoration_check():
    """
    Runs at end of day (23:30 Africa/Lagos via Celery Beat).

    Three outcomes:
      1. No interruption data entered at all for today → warns that data may be missing.
      2. All interruptions from today are resolved → silent success log only.
      3. Some feeders are still down → urgent notification listing each feeder,
         band, district, time it went down, duration so far, and the fault type
         (L/S / TCN / DisCo) so the team knows accountability.
    """
    from datetime import date

    from technical.models import FeederInterruption

    from .services import NotificationService

    today = date.today()
    recipients = _recipients_for_module('technical')

    # ── Check if any interruption data was entered at all today ───────────────
    all_today = FeederInterruption.objects.filter(occurred_at__date=today)

    if not all_today.exists():
        title = f"No Interruption Data Entered — {today}"
        message = (
            f"No feeder interruption records have been entered for {today}. "
            f"Please confirm whether this is accurate or if data upload is pending."
        )
        NotificationService.notify(
            title=title,
            message=message,
            notification_type='action',
            category='technical',
            recipients=recipients,
            priority='medium',
            send_email=False,   # In-app only — just a data-completeness nudge
            action_url='/technical/interruptions',
            metadata={'date': str(today), 'issue': 'no_data'},
        )
        logger.warning(f"[Restoration Check {today}] No interruption data found for today.")
        return f"No interruption data on {today}"

    # ── Find feeders still unrestored at end of day ───────────────────────────
    unrestored = (
        all_today
        .filter(restored_at__isnull=True)
        .select_related('feeder', 'feeder__band', 'feeder__business_district')
        .order_by('feeder__name')
    )

    count = unrestored.count()

    if count == 0:
        logger.info(f"[Restoration Check {today}] All interruptions from today have been restored.")
        return f"All feeders restored on {today}"

    # Build fault category lookup
    fault_map = _build_fault_category_map()

    feeder_detail_lines = []
    metadata_feeders = []

    for interruption in unrestored:
        feeder = interruption.feeder
        band_name = feeder.band.name if feeder.band else 'N/A'
        district = getattr(feeder.business_district, 'name', '') if feeder.business_district else ''
        duration_hrs = interruption.duration_hours or 0
        occurred_str = interruption.occurred_at.strftime('%H:%M')
        fault_category = _classify_fault(interruption.interruption_type, fault_map)

        line = f"• {feeder.name} (Band {band_name}"
        if district:
            line += f", {district}"
        line += (
            f") — down since {occurred_str}, {duration_hrs:.1f} hrs without supply"
            f" | Fault: {fault_category} ({interruption.interruption_type})"
        )
        feeder_detail_lines.append(line)

        metadata_feeders.append({
            'feeder_id': feeder.pk,
            'feeder_name': feeder.name,
            'band': band_name,
            'occurred_at': interruption.occurred_at.isoformat(),
            'duration_hours': round(duration_hrs, 2),
            'interruption_type': interruption.interruption_type,
            'fault_category': fault_category,
        })

    feeder_list_text = "\n".join(feeder_detail_lines)

    title = (
        f"Pending Restoration — {count} feeder{'s' if count != 1 else ''} "
        f"still down at end of day ({today})"
    )
    message = (
        f"{count} feeder{'s' if count != 1 else ''} had interruptions today and "
        f"have NOT been restored as of end of day ({today}):\n\n"
        f"{feeder_list_text}\n\n"
        f"Fault categories: L/S = Load Shedding, TCN = Transmission/Grid fault, "
        f"DisCo = fault under KEDCO's control."
    )

    NotificationService.notify(
        title=title,
        message=message,
        notification_type='action',
        category='technical',
        recipients=recipients,
        priority='urgent',
        send_email=True,
        action_url='/technical/interruptions?status=unresolved',
        metadata={
            'date': str(today),
            'unrestored_count': count,
            'unrestored_feeders': metadata_feeders,
        },
    )

    logger.info(f"[Restoration Check {today}] {count} feeders still unrestored — notification sent.")
    return f"{count} feeders unrestored on {today}"


@shared_task
def daily_dso_meter_reading_check():
    """
    Runs daily at 22:00 Africa/Lagos.

    Checks which feeders have NOT submitted a meter reading for today.
    Only checks feeders in Band A and Band B (high-priority feeders).
    Notifies admins/managers if any readings are missing or flagged as late.
    """
    from datetime import date

    from technical.models import CumulativeMeterReading

    from .services import NotificationService

    today = date.today()
    recipients = _recipients_for_module('technical')

    # Feeders with readings today
    submitted_today = set(
        CumulativeMeterReading.objects
        .filter(reading_date=today)
        .values_list('feeder_id', flat=True)
    )

    # Late readings submitted today
    late_today = list(
        CumulativeMeterReading.objects
        .filter(reading_date=today, is_late=True)
        .select_related('feeder', 'feeder__band', 'feeder__business_district')
        .order_by('feeder__name')
    )

    # Feeders that should have readings (Band A & B only — high-priority)
    from common.models import Feeder
    priority_feeders = list(
        Feeder.objects
        .filter(band__name__in=['A', 'B'])
        .select_related('band', 'business_district')
        .order_by('name')
    )

    missing = [f for f in priority_feeders if f.id not in submitted_today]

    notifications_sent = 0

    if missing:
        count = len(missing)
        lines = [
            f"• {f.name} (Band {getattr(f.band, 'name', '?')}"
            + (f", {f.business_district.name}" if f.business_district else "")
            + ")"
            for f in missing[:15]
        ]
        if count > 15:
            lines.append(f"… and {count - 15} more")

        NotificationService.notify(
            title=f"Missing Meter Readings — {count} Band A/B feeder{'s' if count != 1 else ''} ({today})",
            message=(
                f"{count} high-priority feeder{'s' if count != 1 else ''} have not submitted meter "
                f"readings for {today}:\n\n"
                + "\n".join(lines)
            ),
            notification_type='action',
            category='technical',
            recipients=recipients,
            priority='high',
            send_email=False,
            action_url='/technical/meter-readings',
            metadata={'date': str(today), 'missing_count': count},
        )
        notifications_sent += 1

    if late_today:
        lcount = len(late_today)
        late_lines = [
            f"• {r.feeder.name} (Band {getattr(r.feeder.band, 'name', '?')})"
            for r in late_today[:10]
        ]
        if lcount > 10:
            late_lines.append(f"… and {lcount - 10} more")

        NotificationService.notify(
            title=f"Late Meter Submissions — {lcount} feeder{'s' if lcount != 1 else ''} ({today})",
            message=(
                f"{lcount} feeder{'s' if lcount != 1 else ''} submitted meter readings late today ({today}):\n\n"
                + "\n".join(late_lines)
                + "\n\nLate submissions may indicate field officer compliance issues."
            ),
            notification_type='alert',
            category='technical',
            recipients=recipients,
            priority='medium',
            send_email=False,
            action_url='/technical/meter-readings?filter=late',
            metadata={'date': str(today), 'late_count': lcount},
        )
        notifications_sent += 1

    if not missing and not late_today:
        logger.info(f"[DSO Check {today}] All Band A/B meter readings submitted on time.")

    return f"DSO check {today}: {len(missing)} missing, {len(late_today)} late"


@shared_task
def monthly_ea_submission_check():
    """
    Runs on the 10th of each month at 18:00 Africa/Lagos.

    Checks Energy Account monthly returns for the previous month.
    Identifies stations that:
      1. Have NOT yet submitted (status='draft' — no return created or still draft)
      2. Submitted late (is_late=True)

    Sends an urgent notification to admins and finance managers.
    """
    import calendar
    from datetime import date

    from energy_account.models import EAMonthlyReturn

    from .services import NotificationService

    today = date.today()
    # Target: previous month
    if today.month == 1:
        target_month, target_year = 12, today.year - 1
    else:
        target_month, target_year = today.month - 1, today.year

    month_label = f"{calendar.month_name[target_month]} {target_year}"
    recipients = _recipients_for_module('energy_account')

    # All stations that should have monthly returns
    from common.models import InjectionSubstation
    all_stations = list(InjectionSubstation.objects.all().order_by('name'))

    if not all_stations:
        logger.info(f"[EA Submission Check {month_label}] No injection substations found.")
        return f"No substations found"

    # Returns that exist for the target month
    returns_this_month = {
        r.station_id: r
        for r in EAMonthlyReturn.objects.filter(month=target_month, year=target_year)
    }

    # Stations with no return at all (draft not yet created)
    no_return_stations = [s for s in all_stations if s.id not in returns_this_month]

    # Stations with a draft return (not yet submitted)
    draft_returns = [
        r for r in returns_this_month.values()
        if r.status == 'draft'
    ]

    # Stations with late submissions
    late_returns = [r for r in returns_this_month.values() if r.is_late]

    notifications_sent = 0

    if no_return_stations or draft_returns:
        unsubmitted_count = len(no_return_stations) + len(draft_returns)
        lines = []
        for s in no_return_stations[:10]:
            lines.append(f"• {s.name} — No return created")
        for r in draft_returns[:10]:
            lines.append(f"• {r.station.name if hasattr(r, 'station') else 'Unknown'} — Draft (not submitted)")
        if unsubmitted_count > 20:
            lines.append(f"… and {unsubmitted_count - 20} more")

        NotificationService.notify(
            title=f"EA Submission Alert: {unsubmitted_count} station{'s' if unsubmitted_count != 1 else ''} pending for {month_label}",
            message=(
                f"{unsubmitted_count} injection station{'s' if unsubmitted_count != 1 else ''} have not yet "
                f"submitted their Energy Account monthly return for {month_label}:\n\n"
                + "\n".join(lines)
                + "\n\nPlease follow up with the relevant stations immediately."
            ),
            notification_type='action',
            category='system',
            recipients=recipients,
            priority='high',
            send_email=True,
            action_url='/energy-account/monthly-returns',
            metadata={
                'month': target_month,
                'year': target_year,
                'unsubmitted_count': unsubmitted_count,
                'no_return_count': len(no_return_stations),
                'draft_count': len(draft_returns),
            },
        )
        notifications_sent += 1

    if late_returns:
        lcount = len(late_returns)
        lines = []
        for r in late_returns[:10]:
            station_name = r.station.name if hasattr(r, 'station') else 'Unknown'
            reason = r.late_reason or 'No reason provided'
            lines.append(f"• {station_name} — {reason[:80]}")
        if lcount > 10:
            lines.append(f"… and {lcount - 10} more")

        NotificationService.notify(
            title=f"EA Late Submissions: {lcount} station{'s' if lcount != 1 else ''} submitted late for {month_label}",
            message=(
                f"{lcount} station{'s' if lcount != 1 else ''} submitted late EA returns for {month_label}:\n\n"
                + "\n".join(lines)
            ),
            notification_type='alert',
            category='system',
            recipients=recipients,
            priority='medium',
            send_email=False,
            action_url='/energy-account/monthly-returns?filter=late',
            metadata={
                'month': target_month,
                'year': target_year,
                'late_count': lcount,
            },
        )
        notifications_sent += 1

    if not no_return_stations and not draft_returns and not late_returns:
        logger.info(f"[EA Submission Check {month_label}] All stations submitted on time.")
        return f"All stations submitted for {month_label}"

    logger.info(
        f"[EA Submission Check {month_label}] "
        f"{len(no_return_stations)} missing, {len(draft_returns)} draft, {len(late_returns)} late"
    )
    return f"EA check {month_label}: {len(no_return_stations)} missing, {len(draft_returns)} draft, {len(late_returns)} late"
