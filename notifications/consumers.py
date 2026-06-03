"""
notifications/consumers.py

WebSocket consumer for real-time notification push.

Each authenticated user connects to their own private group:
    notifications_user_<user_id>

When NotificationService.notify() creates a DB record it also calls
push_notification_to_user() which sends a message to that group.
Any browser tab the user has open will receive it instantly.

URL: ws://<host>/ws/notifications/
Auth: JWT token passed as query param  ?token=<access_token>
      OR in the Authorization header (handled by JWTAuthMiddleware).
"""

import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class NotificationConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        user = self.scope.get('user')

        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        self.user_id   = str(user.id)
        self.group_name = f'notifications_user_{self.user_id}'

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        logger.debug("WS connected: user=%s group=%s", self.user_id, self.group_name)

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
            logger.debug("WS disconnected: user=%s code=%s", self.user_id, close_code)

    # The browser can send {"type": "ping"} to keep the connection alive.
    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data or '{}')
        except (ValueError, TypeError):
            return
        if data.get('type') == 'ping':
            await self.send(text_data=json.dumps({'type': 'pong'}))

    # ── Message handler called by channel layer ────────────────────────────────
    # Type must match the key used in group_send: "notification.push"
    # Channels converts dots to underscores when routing to the handler.
    async def notification_push(self, event):
        """Forward a notification payload to the WebSocket client."""
        await self.send(text_data=json.dumps({
            'type':    'notification',
            'payload': event.get('payload', {}),
        }))
