"""
raven/asgi.py

ASGI entrypoint — routes HTTP to Django and WebSocket to Channels.
"""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'raven.settings')

django_asgi_app = get_asgi_application()

from raven.routing import websocket_urlpatterns  # noqa: E402 — must import after Django setup
from raven.ws_auth import JWTAuthMiddleware       # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': AllowedHostsOriginValidator(
        JWTAuthMiddleware(
            URLRouter(websocket_urlpatterns)
        )
    ),
})
