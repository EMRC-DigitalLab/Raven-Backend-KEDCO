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
