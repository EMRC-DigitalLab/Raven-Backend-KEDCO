# notifications/models.py
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone


class Notification(models.Model):
    """
    Core notification record — one created per recipient per event.
    Fan-out happens at creation time inside NotificationService.
    """

    class Type(models.TextChoices):
        ACTION = 'action', 'Action'                    # Something happened in the system
        REPORT = 'report', 'Report'                    # Report generated or shared
        ANNOUNCEMENT = 'announcement', 'Announcement'  # Feature/changelog broadcast
        BAND_ALERT = 'band_alert', 'Band Alert'        # Feeder band change

    class Category(models.TextChoices):
        COMMERCIAL = 'commercial', 'Commercial'
        FINANCIAL = 'financial', 'Financial'
        TECHNICAL = 'technical', 'Technical'
        HR = 'hr', 'Human Resource'
        ANALYTICS = 'analytics', 'Analytics'
        SYSTEM = 'system', 'System'
        REPORT = 'report', 'Report'

    class Priority(models.TextChoices):
        LOW = 'low', 'Low'
        MEDIUM = 'medium', 'Medium'
        HIGH = 'high', 'High'
        URGENT = 'urgent', 'Urgent'

    # Core fields
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='notifications'
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='sent_notifications'
    )

    notification_type = models.CharField(max_length=20, choices=Type.choices)
    category = models.CharField(max_length=20, choices=Category.choices)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)

    title = models.CharField(max_length=255)
    message = models.TextField()

    # Optional deep-link for the frontend to navigate to the related object
    action_url = models.CharField(max_length=500, blank=True)

    # Generic FK — links notification to any model (report, feeder, invoice, etc.)
    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    related_object = GenericForeignKey('content_type', 'object_id')

    # Arbitrary extra data for the frontend
    metadata = models.JSONField(default=dict, blank=True)

    # Read tracking
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    # Email tracking
    send_email = models.BooleanField(default=False)
    email_sent = models.BooleanField(default=False)
    email_sent_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read']),
            models.Index(fields=['recipient', 'category']),
            models.Index(fields=['recipient', 'created_at']),
        ]

    def __str__(self):
        return f"[{self.category}] {self.title} → {self.recipient.username}"

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])


class NotificationPreference(models.Model):
    """
    Per-user preferences controlling which events trigger in-app notifications
    and which also send emails. Auto-created on first access via get_or_create.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='notification_preferences'
    )

    # Commercial
    commercial_in_app = models.BooleanField(default=True)
    commercial_email = models.BooleanField(default=False)

    # Financial
    financial_in_app = models.BooleanField(default=True)
    financial_email = models.BooleanField(default=False)

    # Technical
    technical_in_app = models.BooleanField(default=True)
    technical_email = models.BooleanField(default=False)

    # HR
    hr_in_app = models.BooleanField(default=True)
    hr_email = models.BooleanField(default=False)

    # Analytics
    analytics_in_app = models.BooleanField(default=True)
    analytics_email = models.BooleanField(default=False)

    # Reports — email on by default because reports are meant to be distributed
    report_in_app = models.BooleanField(default=True)
    report_email = models.BooleanField(default=True)

    # Announcements
    announcement_in_app = models.BooleanField(default=True)
    announcement_email = models.BooleanField(default=False)

    # Band alerts
    band_alert_in_app = models.BooleanField(default=True)
    band_alert_email = models.BooleanField(default=False)

    # Master email switch — user can turn off all emails globally
    email_enabled = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Preferences for {self.user.username}"

    def wants_in_app(self, category: str) -> bool:
        return getattr(self, f'{category}_in_app', True)

    def wants_email(self, category: str) -> bool:
        if not self.email_enabled:
            return False
        return getattr(self, f'{category}_email', False)


class Announcement(models.Model):
    """
    Admin-created broadcast notification (new features, bug fixes, maintenance).
    When saved, NotificationService fans it out to target users.
    """

    class AnnouncementType(models.TextChoices):
        FEATURE = 'feature', 'New Feature'
        BUGFIX = 'bugfix', 'Bug Fix'
        MAINTENANCE = 'maintenance', 'Maintenance'
        GENERAL = 'general', 'General'

    title = models.CharField(max_length=255)
    message = models.TextField()
    announcement_type = models.CharField(
        max_length=20, choices=AnnouncementType.choices, default=AnnouncementType.GENERAL
    )

    # Empty list = broadcast to ALL active users.
    # Populated list = only users whose role is in this list.
    target_roles = models.JSONField(
        default=list, blank=True,
        help_text="List of roles to target. Leave empty for all users."
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    # Tracks whether fan-out has already happened so we don't double-notify
    dispatched = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.announcement_type}] {self.title}"


class ReportRecipient(models.Model):
    """
    Tracks every user a report was shared with, including email delivery status.
    One record per (report, recipient) pair.
    """

    class EmailStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'

    # Flexible reference to the report — store type + ID so we're not tightly
    # coupled to a specific report model
    report_type = models.CharField(max_length=100)       # e.g. 'executive', 'commercial'
    report_object_id = models.CharField(max_length=100, blank=True)  # PK or slug of the report
    report_title = models.CharField(max_length=255)
    report_file_path = models.CharField(
        max_length=500, blank=True,
        help_text="Absolute path to the generated PDF for attachment"
    )

    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='shared_reports'
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='received_reports'
    )

    # Optional personal message included in the email
    message = models.TextField(blank=True)

    # Linked in-app notification record
    notification = models.ForeignKey(
        Notification, on_delete=models.SET_NULL, null=True, blank=True
    )

    email_status = models.CharField(
        max_length=10, choices=EmailStatus.choices, default=EmailStatus.PENDING
    )
    email_sent_at = models.DateTimeField(null=True, blank=True)

    # Whether the recipient has opened the in-app notification
    viewed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'email_status']),
        ]

    def __str__(self):
        return f"{self.report_title} → {self.recipient.username} ({self.email_status})"


class BandSubscription(models.Model):
    """
    Users subscribe to specific feeders and receive alerts when the band changes.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='band_subscriptions'
    )

    # Store feeder info directly — avoids cross-app model imports at module level
    feeder_id = models.PositiveIntegerField()
    feeder_name = models.CharField(max_length=255)

    notify_in_app = models.BooleanField(default=True)
    notify_email = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['user', 'feeder_id']
        ordering = ['feeder_name']

    def __str__(self):
        return f"{self.user.username} → {self.feeder_name}"
