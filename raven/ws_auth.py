"""
raven/ws_auth.py

ASGI middleware that authenticates WebSocket connections using a JWT token.

The token can be passed as:
  - Query param: ws://host/ws/notifications/?token=<jwt>
  - Authorization header: not available in most browsers for WS,
    so query param is the standard approach.
"""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser


@database_sync_to_async
def _get_user_from_token(token: str):
    try:
        from rest_framework_simplejwt.tokens import AccessToken
        from django.contrib.auth import get_user_model
        User = get_user_model()
        access = AccessToken(token)
        return User.objects.get(id=access['user_id'])
    except Exception:
        return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    """Resolve JWT from ?token= query param and attach user to scope."""

    async def __call__(self, scope, receive, send):
        query_string = scope.get('query_string', b'').decode()
        params       = parse_qs(query_string)
        token_list   = params.get('token', [])

        if token_list:
            scope['user'] = await _get_user_from_token(token_list[0])
        else:
            scope['user'] = AnonymousUser()

        return await super().__call__(scope, receive, send)
