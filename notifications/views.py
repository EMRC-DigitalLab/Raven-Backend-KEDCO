# notifications/views.py
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Announcement,
    BandSubscription,
    FaultAlertFeederWatch,
    FaultAlertRecipient,
    Notification,
    NotificationPreference,
    ReportRecipient,
)
from .serializers import (
    AnnouncementSerializer,
    BandSubscriptionSerializer,
    FaultAlertFeederWatchSerializer,
    FaultAlertRecipientSerializer,
    NotificationPreferenceSerializer,
    NotificationSerializer,
    ReportRecipientSerializer,
    ReportShareSerializer,
)
from .services import NotificationService


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_admin_or_above(user):
    return user.role in ('super_admin', 'admin')


# ══════════════════════════════════════════════════════════════════════════════
#  IN-APP NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════════════

class NotificationListView(APIView):
    """
    GET /api/notifications/
    Returns the authenticated user's notifications, newest first.
    Query params:
      - unread_only=true  → filter to unread only
      - category=hr       → filter by category
      - limit=20          → page size (default 20, max 100)
      - offset=0          → pagination offset
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = Notification.objects.filter(recipient=request.user).select_related('sender')

        # Filters
        if request.query_params.get('unread_only', '').lower() == 'true':
            qs = qs.filter(is_read=False)

        category = request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)

        notification_type = request.query_params.get('type')
        if notification_type:
            qs = qs.filter(notification_type=notification_type)

        # Pagination
        try:
            limit = min(int(request.query_params.get('limit', 20)), 100)
            offset = int(request.query_params.get('offset', 0))
        except ValueError:
            limit, offset = 20, 0

        total = qs.count()
        notifications = qs[offset: offset + limit]

        return Response({
            'count': total,
            'unread_count': Notification.objects.filter(
                recipient=request.user, is_read=False
            ).count(),
            'limit': limit,
            'offset': offset,
            'results': NotificationSerializer(notifications, many=True).data,
        })


class NotificationUnreadCountView(APIView):
    """
    GET /api/notifications/unread-count/
    Lightweight endpoint for the frontend bell badge — returns only the count.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        count = Notification.objects.filter(
            recipient=request.user, is_read=False
        ).count()
        return Response({'unread_count': count})


class NotificationDetailView(APIView):
    """
    GET  /api/notifications/<id>/   → retrieve single notification
    POST /api/notifications/<id>/read/ → mark as read
    DELETE /api/notifications/<id>/   → delete
    """
    permission_classes = [IsAuthenticated]

    def _get_notification(self, request, pk):
        try:
            return Notification.objects.get(pk=pk, recipient=request.user)
        except Notification.DoesNotExist:
            return None

    def get(self, request, pk):
        notif = self._get_notification(request, pk)
        if not notif:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(NotificationSerializer(notif).data)

    def delete(self, request, pk):
        notif = self._get_notification(request, pk)
        if not notif:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        notif.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class NotificationMarkReadView(APIView):
    """POST /api/notifications/<id>/read/"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            notif = Notification.objects.get(pk=pk, recipient=request.user)
        except Notification.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        notif.mark_read()
        return Response({'status': 'marked as read'})


class NotificationMarkAllReadView(APIView):
    """POST /api/notifications/mark-all-read/"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        category = request.data.get('category')
        qs = Notification.objects.filter(recipient=request.user, is_read=False)
        if category:
            qs = qs.filter(category=category)
        updated = qs.update(is_read=True, read_at=timezone.now())
        return Response({'marked_read': updated})


# ══════════════════════════════════════════════════════════════════════════════
#  NOTIFICATION PREFERENCES
# ══════════════════════════════════════════════════════════════════════════════

class NotificationPreferenceView(APIView):
    """
    GET  /api/notifications/preferences/   → retrieve user's preferences
    PATCH /api/notifications/preferences/  → update
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
        return Response(NotificationPreferenceSerializer(prefs).data)

    def patch(self, request):
        prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
        serializer = NotificationPreferenceSerializer(prefs, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ══════════════════════════════════════════════════════════════════════════════
#  ANNOUNCEMENTS
# ══════════════════════════════════════════════════════════════════════════════

class AnnouncementListView(APIView):
    """
    GET  /api/notifications/announcements/  → list active announcements visible to this user
    POST /api/notifications/announcements/  → create (admin+ only), triggers fan-out via signal
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Users see announcements targeting their role (or broadcast to all)
        qs = Announcement.objects.filter(is_active=True).order_by('-created_at')
        user_role = request.user.role

        # Include if target_roles is empty (all users) OR user's role is in the list
        filtered = [
            a for a in qs
            if not a.target_roles or user_role in a.target_roles
        ]

        limit = min(int(request.query_params.get('limit', 20)), 100)
        offset = int(request.query_params.get('offset', 0))
        page = filtered[offset: offset + limit]

        return Response({
            'count': len(filtered),
            'results': AnnouncementSerializer(page, many=True).data,
        })

    def post(self, request):
        if not _is_admin_or_above(request.user):
            return Response(
                {'detail': 'Only admins can create announcements.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = AnnouncementSerializer(data=request.data)
        if serializer.is_valid():
            announcement = serializer.save(created_by=request.user)
            # Fan-out is handled by the post_save signal in signals.py
            return Response(AnnouncementSerializer(announcement).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ══════════════════════════════════════════════════════════════════════════════
#  REPORT DISTRIBUTION
# ══════════════════════════════════════════════════════════════════════════════

class ReportShareView(APIView):
    """
    POST /api/notifications/reports/share/
    Share a report with one or more users (in-app notification + email with PDF).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ReportShareSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        created = NotificationService.share_report(
            report_type=data['report_type'],
            report_title=data['report_title'],
            sender=request.user,
            recipient_ids=data['recipient_ids'],
            message=data.get('message', ''),
            report_file_path=data.get('report_file_path', ''),
            report_object_id=data.get('report_object_id', ''),
        )

        return Response(
            {
                'shared_with': len(created),
                'recipients': ReportRecipientSerializer(created, many=True).data,
            },
            status=status.HTTP_201_CREATED,
        )


class ReceivedReportsView(APIView):
    """
    GET /api/notifications/reports/received/
    List all reports shared with the authenticated user.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = ReportRecipient.objects.filter(
            recipient=request.user
        ).select_related('sender', 'recipient').order_by('-created_at')

        # Mark as viewed if not already
        unviewed = qs.filter(viewed_at__isnull=True)
        unviewed.update(viewed_at=timezone.now())

        limit = min(int(request.query_params.get('limit', 20)), 100)
        offset = int(request.query_params.get('offset', 0))
        total = qs.count()

        return Response({
            'count': total,
            'results': ReportRecipientSerializer(qs[offset: offset + limit], many=True).data,
        })


class SentReportsView(APIView):
    """
    GET /api/notifications/reports/sent/
    List all reports the authenticated user has shared with others.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = ReportRecipient.objects.filter(
            sender=request.user
        ).select_related('sender', 'recipient').order_by('-created_at')

        limit = min(int(request.query_params.get('limit', 20)), 100)
        offset = int(request.query_params.get('offset', 0))

        return Response({
            'count': qs.count(),
            'results': ReportRecipientSerializer(qs[offset: offset + limit], many=True).data,
        })


# ══════════════════════════════════════════════════════════════════════════════
#  BAND SUBSCRIPTIONS
# ══════════════════════════════════════════════════════════════════════════════

class BandSubscriptionListView(APIView):
    """
    GET  /api/notifications/band-subscriptions/  → list user's subscriptions
    POST /api/notifications/band-subscriptions/  → subscribe to a feeder
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        subs = BandSubscription.objects.filter(user=request.user, is_active=True)
        return Response(BandSubscriptionSerializer(subs, many=True).data)

    def post(self, request):
        serializer = BandSubscriptionSerializer(
            data=request.data, context={'request': request}
        )
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class BandSubscriptionDetailView(APIView):
    """
    PATCH  /api/notifications/band-subscriptions/<id>/  → update (toggle email, in-app)
    DELETE /api/notifications/band-subscriptions/<id>/  → unsubscribe
    """
    permission_classes = [IsAuthenticated]

    def _get_subscription(self, request, pk):
        try:
            return BandSubscription.objects.get(pk=pk, user=request.user)
        except BandSubscription.DoesNotExist:
            return None

    def patch(self, request, pk):
        sub = self._get_subscription(request, pk)
        if not sub:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = BandSubscriptionSerializer(
            sub, data=request.data, partial=True, context={'request': request}
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        sub = self._get_subscription(request, pk)
        if not sub:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        sub.is_active = False
        sub.save(update_fields=['is_active'])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ══════════════════════════════════════════════════════════════════════════════
#  FAULT ALERTS — admin-managed feeder watchlist + recipient list for
#  real-time fault occurrence/restoration emails (hybrid DataNest + TMO sheet
#  fault_code source — see technical/tasks.py and tmo/sheet_sync.py)
# ══════════════════════════════════════════════════════════════════════════════

class FaultAlertFeederWatchListView(APIView):
    """
    GET  /api/notifications/fault-alerts/feeders/  → list watched feeders
         optional filters: ?segment=MDI|MDNI|Regions  ?voltage_level=11kv|33kv
    POST /api/notifications/fault-alerts/feeders/  → add a feeder to the watchlist (admin only)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = FaultAlertFeederWatch.objects.filter(is_active=True).select_related('feeder', 'added_by')
        segment = request.query_params.get('segment')
        voltage_level = request.query_params.get('voltage_level')
        if segment:
            qs = qs.filter(feeder__pl_segment__iexact=segment)
        if voltage_level:
            qs = qs.filter(feeder__voltage_level__iexact=voltage_level)
        return Response(FaultAlertFeederWatchSerializer(qs, many=True).data)

    def post(self, request):
        if not _is_admin_or_above(request.user):
            return Response(
                {'detail': 'Only admins can manage the fault alert watchlist.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = FaultAlertFeederWatchSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(added_by=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class FaultAlertAvailableFeedersView(APIView):
    """
    GET /api/notifications/fault-alerts/feeders/available/  → admin only
    Feeder directory for the "Add Feeder" picker — every feeder NOT already
    active on the watchlist, with its UUID id (the value the watchlist POST
    body needs). None of the other feeder-listing endpoints in the app
    expose the UUID (they're keyed by feeder_slug) or are appropriate here
    (TMO/commercial/technical feeder endpoints are heavy analytics views
    gated by unrelated permissions).

    Optional filters: ?segment=MDI|MDNI|Regions  ?voltage_level=11kv|33kv  ?q=<name search>
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _is_admin_or_above(request.user):
            return Response(
                {'detail': 'Only admins can manage the fault alert watchlist.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        from common.models import Feeder

        watched_ids = FaultAlertFeederWatch.objects.filter(is_active=True).values_list('feeder_id', flat=True)
        qs = Feeder.objects.exclude(id__in=watched_ids)

        segment = request.query_params.get('segment')
        voltage_level = request.query_params.get('voltage_level')
        q = request.query_params.get('q')
        if segment:
            qs = qs.filter(pl_segment__iexact=segment)
        if voltage_level:
            qs = qs.filter(voltage_level__iexact=voltage_level)
        if q:
            qs = qs.filter(name__icontains=q)

        return Response([
            {
                'id': str(f.id),
                'feeder_name': f.name,
                'feeder_slug': f.slug,
                'voltage_level': f.voltage_level,
                'segment': f.pl_segment,
            }
            for f in qs.order_by('name')
        ])


class FaultAlertFeederWatchDetailView(APIView):
    """
    DELETE /api/notifications/fault-alerts/feeders/<id>/  → remove from watchlist (admin only)
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        if not _is_admin_or_above(request.user):
            return Response(
                {'detail': 'Only admins can manage the fault alert watchlist.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            watch = FaultAlertFeederWatch.objects.get(pk=pk)
        except FaultAlertFeederWatch.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        watch.is_active = False
        watch.save(update_fields=['is_active'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class FaultAlertRecipientListView(APIView):
    """
    GET  /api/notifications/fault-alerts/recipients/  → list registered recipients
    POST /api/notifications/fault-alerts/recipients/  → register a user for fault alerts (admin only)
         body: {"user": <user_id>}
         To register a brand-new person who has no account yet, create the
         user first via POST /api/users/ (admin-only, existing endpoint),
         then register the returned user id here.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _is_admin_or_above(request.user):
            return Response(
                {'detail': 'Only admins can view the fault alert recipient list.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        qs = FaultAlertRecipient.objects.filter(is_active=True).select_related('user', 'added_by')
        return Response(FaultAlertRecipientSerializer(qs, many=True).data)

    def post(self, request):
        if not _is_admin_or_above(request.user):
            return Response(
                {'detail': 'Only admins can manage fault alert recipients.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = FaultAlertRecipientSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(added_by=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class FaultAlertRecipientDetailView(APIView):
    """
    DELETE /api/notifications/fault-alerts/recipients/<id>/  → remove recipient (admin only)
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        if not _is_admin_or_above(request.user):
            return Response(
                {'detail': 'Only admins can manage fault alert recipients.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            recipient = FaultAlertRecipient.objects.get(pk=pk)
        except FaultAlertRecipient.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        recipient.is_active = False
        recipient.save(update_fields=['is_active'])
        return Response(status=status.HTTP_204_NO_CONTENT)
