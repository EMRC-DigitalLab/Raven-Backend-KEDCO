# notifications/serializers.py
from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import (
    Announcement,
    BandSubscription,
    Notification,
    NotificationPreference,
    ReportRecipient,
)

User = get_user_model()


class NotificationSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    notification_type_display = serializers.CharField(source='get_notification_type_display', read_only=True)
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)

    class Meta:
        model = Notification
        fields = [
            'id',
            'notification_type',
            'notification_type_display',
            'category',
            'category_display',
            'priority',
            'priority_display',
            'title',
            'message',
            'action_url',
            'metadata',
            'is_read',
            'read_at',
            'sender_name',
            'created_at',
        ]
        read_only_fields = fields

    def get_sender_name(self, obj):
        if obj.sender:
            return obj.sender.get_full_name() or obj.sender.username
        return 'System'


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        exclude = ['id', 'user', 'updated_at']


class AnnouncementSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    announcement_type_display = serializers.CharField(
        source='get_announcement_type_display', read_only=True
    )

    class Meta:
        model = Announcement
        fields = [
            'id',
            'title',
            'message',
            'announcement_type',
            'announcement_type_display',
            'target_roles',
            'created_by_name',
            'created_at',
            'is_active',
        ]
        read_only_fields = ['created_by_name', 'announcement_type_display', 'created_at']

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return 'System'

    def validate_target_roles(self, value):
        valid_roles = [r[0] for r in User.USER_ROLES] if hasattr(User, 'USER_ROLES') else [
            'super_admin', 'admin', 'manager', 'staff', 'viewer'
        ]
        for role in value:
            if role not in valid_roles:
                raise serializers.ValidationError(f"'{role}' is not a valid role.")
        return value


class ReportShareSerializer(serializers.Serializer):
    """Input serializer for POST /notifications/reports/share/"""
    report_type = serializers.CharField(max_length=100)
    report_title = serializers.CharField(max_length=255)
    report_object_id = serializers.CharField(max_length=100, required=False, default='')
    report_file_path = serializers.CharField(max_length=500, required=False, default='')
    recipient_ids = serializers.ListField(
        child=serializers.IntegerField(),
        min_length=1,
        help_text="List of user IDs to share the report with."
    )
    message = serializers.CharField(required=False, default='', allow_blank=True)

    def validate_recipient_ids(self, value):
        existing = User.objects.filter(id__in=value, is_active=True).values_list('id', flat=True)
        missing = set(value) - set(existing)
        if missing:
            raise serializers.ValidationError(f"User IDs not found or inactive: {sorted(missing)}")
        return value


class ReportRecipientSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    recipient_name = serializers.SerializerMethodField()

    class Meta:
        model = ReportRecipient
        fields = [
            'id',
            'report_type',
            'report_title',
            'report_object_id',
            'sender_name',
            'recipient_name',
            'message',
            'email_status',
            'email_sent_at',
            'viewed_at',
            'created_at',
        ]
        read_only_fields = fields

    def get_sender_name(self, obj):
        return obj.sender.get_full_name() or obj.sender.username

    def get_recipient_name(self, obj):
        return obj.recipient.get_full_name() or obj.recipient.username


class BandSubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = BandSubscription
        fields = [
            'id',
            'feeder_id',
            'feeder_name',
            'notify_in_app',
            'notify_email',
            'is_active',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def validate(self, attrs):
        user = self.context['request'].user
        feeder_id = attrs.get('feeder_id')
        if feeder_id and BandSubscription.objects.filter(user=user, feeder_id=feeder_id).exists():
            raise serializers.ValidationError("You are already subscribed to this feeder.")
        return attrs
