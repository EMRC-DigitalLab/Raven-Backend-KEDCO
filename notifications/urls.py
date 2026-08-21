# notifications/urls.py
from django.urls import path

from .views import (
    AnnouncementListView,
    BandSubscriptionDetailView,
    BandSubscriptionListView,
    FaultAlertFeederWatchDetailView,
    FaultAlertFeederWatchListView,
    FaultAlertRecipientDetailView,
    FaultAlertRecipientListView,
    NotificationDetailView,
    NotificationListView,
    NotificationMarkAllReadView,
    NotificationMarkReadView,
    NotificationPreferenceView,
    NotificationUnreadCountView,
    ReceivedReportsView,
    ReportShareView,
    SentReportsView,
)

app_name = 'notifications'

urlpatterns = [
    # ── In-app notifications ──────────────────────────────────────────────────
    path('', NotificationListView.as_view(), name='list'),
    path('unread-count/', NotificationUnreadCountView.as_view(), name='unread-count'),
    path('mark-all-read/', NotificationMarkAllReadView.as_view(), name='mark-all-read'),
    path('<int:pk>/', NotificationDetailView.as_view(), name='detail'),
    path('<int:pk>/read/', NotificationMarkReadView.as_view(), name='mark-read'),

    # ── Preferences ───────────────────────────────────────────────────────────
    path('preferences/', NotificationPreferenceView.as_view(), name='preferences'),

    # ── Announcements ─────────────────────────────────────────────────────────
    path('announcements/', AnnouncementListView.as_view(), name='announcements'),

    # ── Report distribution ───────────────────────────────────────────────────
    path('reports/share/', ReportShareView.as_view(), name='report-share'),
    path('reports/received/', ReceivedReportsView.as_view(), name='reports-received'),
    path('reports/sent/', SentReportsView.as_view(), name='reports-sent'),

    # ── Band subscriptions ────────────────────────────────────────────────────
    path('band-subscriptions/', BandSubscriptionListView.as_view(), name='band-subscriptions'),
    path('band-subscriptions/<int:pk>/', BandSubscriptionDetailView.as_view(), name='band-subscription-detail'),

    # ── Fault alerts (admin-managed feeder watchlist + recipient list) ────────
    path('fault-alerts/feeders/', FaultAlertFeederWatchListView.as_view(), name='fault-alert-feeders'),
    path('fault-alerts/feeders/<int:pk>/', FaultAlertFeederWatchDetailView.as_view(), name='fault-alert-feeder-detail'),
    path('fault-alerts/recipients/', FaultAlertRecipientListView.as_view(), name='fault-alert-recipients'),
    path('fault-alerts/recipients/<int:pk>/', FaultAlertRecipientDetailView.as_view(), name='fault-alert-recipient-detail'),
]
