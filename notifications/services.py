# notifications/services.py
"""
NotificationService — the single entry point for creating notifications.

All signal handlers and views funnel through here. Never create Notification
records directly; always go through this service so fan-out, preference
checking, and email queuing happen consistently.
"""
import logging

from asgiref.sync import async_to_sync
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

logger = logging.getLogger(__name__)


def _ws_push(user_id: int, notification) -> None:
    """
    Push a notification to the user's WebSocket group via the channel layer.
    Called synchronously from within notify() after DB commit.
    Silently no-ops if channels/redis is unavailable.
    """
    try:
        from channels.layers import get_channel_layer
        layer = get_channel_layer()
        if layer is None:
            return
        group_name = f'notifications_user_{user_id}'
        payload = {
            'id':                str(notification.id),
            'title':             notification.title,
            'message':           notification.message,
            'notification_type': notification.notification_type,
            'category':          notification.category,
            'priority':          notification.priority,
            'action_url':        notification.action_url,
            'is_read':           notification.is_read,
            'created_at':        notification.created_at.isoformat(),
            'metadata':          notification.metadata or {},
        }
        async_to_sync(layer.group_send)(
            group_name,
            {'type': 'notification.push', 'payload': payload},
        )
    except Exception as exc:
        logger.warning("WS push failed for user %s: %s", user_id, exc)


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

        # Push via WebSocket + queue email tasks (outside transaction so rows are committed)
        for notif in created:
            _ws_push(notif.recipient_id, notif)
            if notif.send_email:
                try:
                    send_notification_email.delay(notif.id)
                except Exception as e:
                    logger.error(f"Failed to queue email for notification {notif.id}: {e}")

        return created

    # ── Convenience wrappers ──────────────────────────────────────────────────

    @staticmethod
    def notify_role(title, message, category, roles, **kwargs):
        """
        Notify all users who either:
          1. Have a role in `roles`, OR
          2. Have an active UserSectionAccess grant for the section matching `category`

        This ensures that any user with module access sees the notification,
        regardless of their system role.
        """
        from django.contrib.auth import get_user_model
        from django.db.models import Q
        from users.models import UserSectionAccess

        User = get_user_model()

        # Users matched by role
        role_q = Q(role__in=roles, is_active=True)

        # Users matched by active section access for this category's module
        section_q = Q(
            section_access__section__name=category,
            section_access__is_active=True,
            is_active=True,
        )

        recipients = User.objects.filter(role_q | section_q).distinct()

        return NotificationService.notify(
            title=title,
            message=message,
            notification_type='action',
            category=category,
            recipients=recipients,
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
                action_url='/technical/technical-feeder',
                metadata={
                    'feeder_id': feeder_id,
                    'feeder_name': feeder_name,
                    'old_band': old_band,
                    'new_band': new_band,
                },
            )

    # ── DataNest sync notifications ───────────────────────────────────────────

    _SYNC_LABELS: dict = {
        'technical_hourly_load':         'Technical Hourly Load',
        'technical_interruptions':       'Technical Interruptions',
        'technical_meter_readings':      'Technical Meter Readings',
        'commercial_readings':           'Commercial Meter Readings',
        'commercial_customers':          'Commercial Customers',
        'commercial_managers':           'Commercial Feeder Managers',
        'commercial_tariff_rates':       'Commercial Tariff Rates',
        'ea_nbet_rates':                 'EA: NBET Rates',
        'ea_settings':                   'EA: Settings',
        'ea_grid_meters':                'EA: Grid Meters',
        'ea_monthly_returns':            'EA: Monthly Returns',
        'ea_monthly_readings':           'EA: Monthly Readings',
        'ea_feeder_technical_energy':    'EA: Feeder Technical Energy',
        'ea_tcn_reconciliation':         'EA: TCN Reconciliation',
        'ea_tcn_reconciliation_notes':   'EA: TCN Reconciliation Notes',
        'ea_mo_reconciliation':          'EA: MO Reconciliation',
        'ea_weekly_readings':            'EA: Weekly Readings',
        'ea_station_assignments':        'EA: Station Assignments',
        'ea_meter_check_schedules':      'EA: Meter Check Schedules',
        'ea_meter_check_records':        'EA: Meter Check Records',
        'ea_coupling_log':               'EA: Coupling Log',
    }

    @staticmethod
    def notify_datasync(data_type: str, sync_status: str, log, notify_on_success: bool = True):
        """
        Fire a notification for a DataNest sync event.

        notify_on_success=False suppresses success messages for high-frequency syncs
        (e.g., every 5-15 minutes) to avoid inbox noise for admins.

        Error and partial notifications always fire regardless of the flag.
        Success fires only when there are new/updated records.
        """
        label = NotificationService._SYNC_LABELS.get(
            data_type, data_type.replace('_', ' ').title()
        )
        created   = getattr(log, 'records_created', 0) or 0
        updated   = getattr(log, 'records_updated', 0) or 0
        errored   = getattr(log, 'records_errored', 0) or 0
        error_msg = (getattr(log, 'error_message', '') or '').strip()

        if sync_status == 'error':
            NotificationService.notify_role(
                title=f"DataNest Sync Failed: {label}",
                message=(
                    f"The DataNest sync for '{label}' encountered an unhandled error "
                    f"and could not complete."
                    + (f"\n\nError: {error_msg[:400]}" if error_msg else "")
                ),
                category='system',
                roles=['super_admin', 'admin'],
                priority='high',
                send_email=True,
                action_url='/admin/dashboard',
                metadata={
                    'data_type': data_type,
                    'sync_status': sync_status,
                    'error': error_msg[:500],
                },
            )

        elif sync_status == 'partial':
            NotificationService.notify_role(
                title=f"DataNest Sync Completed with Errors: {label}",
                message=(
                    f"The '{label}' sync completed but encountered {errored} error(s). "
                    f"{created} new record{'s' if created != 1 else ''} added, {updated} updated."
                    + (f"\n\nErrors: {error_msg[:300]}" if error_msg else "")
                ),
                category='system',
                roles=['super_admin', 'admin'],
                priority='medium',
                send_email=False,
                action_url='/admin/dashboard',
                metadata={
                    'data_type': data_type,
                    'sync_status': sync_status,
                    'records_created': created,
                    'records_updated': updated,
                    'records_errored': errored,
                },
            )

        elif sync_status == 'success' and notify_on_success and (created + updated) > 0:
            NotificationService.notify_role(
                title=f"DataNest Sync Complete: {label}",
                message=(
                    f"DataNest sync for '{label}' completed successfully: "
                    f"{created} new record{'s' if created != 1 else ''} added, "
                    f"{updated} updated."
                ),
                category='system',
                roles=['super_admin', 'admin'],
                priority='low',
                send_email=False,
                action_url='/admin/dashboard',
                metadata={
                    'data_type': data_type,
                    'sync_status': sync_status,
                    'records_created': created,
                    'records_updated': updated,
                },
            )
