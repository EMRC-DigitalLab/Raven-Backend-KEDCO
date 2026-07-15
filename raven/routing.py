"""
raven/routing.py

WebSocket URL routing.
"""

from django.urls import path
from notifications.consumers import NotificationConsumer
from aria.consumers import ARIAConsumer

websocket_urlpatterns = [
    path('ws/notifications/', NotificationConsumer.as_asgi()),
    path('ws/aria/', ARIAConsumer.as_asgi()),
]
