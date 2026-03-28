# notifications/services.py
"""
NotificationService — the single entry point for creating notifications.

All signal handlers and views funnel through here. Never create Notification
records directly; always go through this service so fan-out, preference
checking, and email queuing happen consistently.
"""
import logging

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

logger = logging.getLogger(__name__)


class NotificationService:

    @staticmethod
    def notify(
        title: str,
        message: str,
        notification_type: str,
        category: str,
        recipients=None,        # Explicit list/queryset of User objects
        target_roles=None,      # ['admin', 'manager'] — resolved to users if recipients is None
        sender=None,            # User who triggered the event (None = system)
        priority: str = 'medium',
        action_url: str = '',
        send_email: bool = False,   # Overridden by user preferences; this is the default ask
        metadata: dict = None,
        related_object=None,    # Any model instance to attach via GenericFK
    ):
        """
        Core fan-out method.

        1. Resolves recipients (explicit list OR role-based query)
        2. Checks each user's NotificationPreference
        3. Bulk-creates one Notification per recipient
        4. Queues Celery email tasks for those that need email
        """
        from .models import Notification, NotificationPreference
        from .tasks import send_notification_email

        if metadata is None:
            metadata = {}

        # ── Resolve recipients ────────────────────────────────────────────────
        if recipients is None:
            if not target_roles:
                logger.warning("NotificationService.notify called with no recipients and no target_roles — skipped.")
                return []
            from django.contrib.auth import get_user_model
            User = get_user_model()
            recipients = User.objects.filter(role__in=target_roles, is_active=True)

        # ── Resolve GenericFK for related_object ──────────────────────────────
        content_type = None
        object_id = None
        if related_object is not None:
            try:
                content_type = ContentType.objects.get_for_model(related_object)
                object_id = related_object.pk
            except Exception:
                pass

        # ── Build notification records ────────────────────────────────────────
        notifications_to_create = []

        for user in recipients:
            prefs, _ = NotificationPreference.objects.get_or_create(user=user)

            if not prefs.wants_in_app(category):
                continue  # User has disabled in-app for this category

            should_email = send_email and prefs.wants_email(category)

            notifications_to_create.append(
                Notification(
                    recipient=user,
                    sender=sender,
                    notification_type=notification_type,
                    category=category,
                    priority=priority,
                    title=title,
                    message=message,
                    action_url=action_url,
                    content_type=content_type,
                    object_id=object_id,
                    metadata=metadata,
                    send_email=should_email,
                )
            )

        if not notifications_to_create:
            return []

        # ── Bulk create + queue email tasks ───────────────────────────────────
        with transaction.atomic():
            created = Notification.objects.bulk_create(notifications_to_create)

        # Queue email tasks outside the transaction so DB rows are committed first
        for notif in created:
            if notif.send_email:
                try:
                    send_notification_email.delay(notif.id)
                except Exception as e:
                    logger.error(f"Failed to queue email for notification {notif.id}: {e}")

        return created

    # ── Convenience wrappers ──────────────────────────────────────────────────

    @staticmethod
    def notify_role(title, message, category, roles, **kwargs):
        """Shorthand: notify all users with given roles."""
        return NotificationService.notify(
            title=title,
            message=message,
            notification_type='action',
            category=category,
            target_roles=roles,
            **kwargs,
        )

    @staticmethod
    def notify_user(title, message, category, user, **kwargs):
        """Shorthand: notify a single specific user."""
        return NotificationService.notify(
            title=title,
            message=message,
            notification_type='action',
            category=category,
            recipients=[user],
            **kwargs,
        )

    @staticmethod
    def broadcast_announcement(announcement):
        """
        Fan out an Announcement to all target users.
        Called after an Announcement is saved (via signal).
        Marks the announcement as dispatched to prevent duplicates.
        """
        if announcement.dispatched:
            return

        from django.contrib.auth import get_user_model
        User = get_user_model()

        if announcement.target_roles:
            users = User.objects.filter(role__in=announcement.target_roles, is_active=True)
        else:
            users = User.objects.filter(is_active=True)

        NotificationService.notify(
            title=announcement.title,
            message=announcement.message,
            notification_type='announcement',
            category='system',
            recipients=users,
            sender=announcement.created_by,
            priority='medium',
            metadata={'announcement_type': announcement.announcement_type},
        )

        announcement.dispatched = True
        announcement.save(update_fields=['dispatched'])

    @staticmethod
    def share_report(report_type, report_title, sender, recipient_ids,
                     message='', report_file_path='', report_object_id=''):
        """
        Share a report with one or more users.
        Creates a ReportRecipient record and in-app notification per recipient,
        then queues a Celery email task with the PDF attachment.
        """
        from django.contrib.auth import get_user_model
        from .models import ReportRecipient
        from .tasks import send_report_email

        User = get_user_model()
        recipients = User.objects.filter(id__in=recipient_ids, is_active=True)

        report_recipients = []
        for user in recipients:
            # Create in-app notification first
            notifications = NotificationService.notify(
                title=f"Report shared with you: {report_title}",
                message=message or f"{sender.get_full_name() or sender.username} shared the '{report_title}' report with you.",
                notification_type='report',
                category='report',
                recipients=[user],
                sender=sender,
                priority='high',
                send_email=True,
                action_url=f'/reports/{report_type}/{report_object_id}',
                metadata={
                    'report_type': report_type,
                    'report_object_id': report_object_id,
                },
            )

            notif = notifications[0] if notifications else None

            rr = ReportRecipient(
                report_type=report_type,
                report_object_id=report_object_id,
                report_title=report_title,
                report_file_path=report_file_path,
                sender=sender,
                recipient=user,
                message=message,
                notification=notif,
            )
            report_recipients.append(rr)

        with transaction.atomic():
            created = ReportRecipient.objects.bulk_create(report_recipients)

        for rr in created:
            try:
                send_report_email.delay(rr.id)
            except Exception as e:
                logger.error(f"Failed to queue report email for ReportRecipient {rr.id}: {e}")

        return created

    @staticmethod
    def notify_band_change(feeder_id, feeder_name, old_band, new_band):
        """
        Notify all users subscribed to a feeder that its band has changed.
        """
        from .models import BandSubscription

        subscriptions = BandSubscription.objects.filter(
            feeder_id=feeder_id, is_active=True
        ).select_related('user')

        for sub in subscriptions:
            NotificationService.notify(
                title=f"Band change: {feeder_name}",
                message=f"Feeder '{feeder_name}' has moved from Band {old_band} to Band {new_band}.",
                notification_type='band_alert',
                category='technical',
                recipients=[sub.user],
                priority='high',
                send_email=sub.notify_email,
                action_url=f'/technical/feeders/{feeder_id}',
                metadata={
                    'feeder_id': feeder_id,
                    'feeder_name': feeder_name,
                    'old_band': old_band,
                    'new_band': new_band,
                },
            )
