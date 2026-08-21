# users/utils.py
import re


def get_client_ip(request):
    """Best-effort client IP: proxy header first, falls back to REMOTE_ADDR."""
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


_BROWSER_PATTERNS = [
    ('Edg/', 'Edge'),
    ('OPR/', 'Opera'),
    ('Chrome/', 'Chrome'),
    ('CriOS/', 'Chrome'),
    ('Firefox/', 'Firefox'),
    ('FxiOS/', 'Firefox'),
    ('Version/.*Safari/', 'Safari'),
]

_OS_PATTERNS = [
    ('Windows', 'Windows'),
    ('Mac OS X', 'macOS'),
    ('Android', 'Android'),
    ('iPhone|iPad|iOS', 'iOS'),
    ('Linux', 'Linux'),
]


def parse_device_label(user_agent: str) -> str:
    """Small regex-based UA parser producing a display label like 'Chrome on Windows'."""
    if not user_agent:
        return 'Unknown device'

    browser = next((name for pattern, name in _BROWSER_PATTERNS if re.search(pattern, user_agent)), 'Unknown browser')
    os_name = next((name for pattern, name in _OS_PATTERNS if re.search(pattern, user_agent)), 'Unknown OS')

    return f"{browser} on {os_name}"


def create_user_session(request, user, refresh_token):
    """Create/refresh a UserSession row for a freshly issued refresh token.

    Keyed by jti so a later token rotation (see CustomTokenRefreshView) can
    find and carry this same row forward instead of spawning a new one.
    """
    from .models import UserSession

    jti = refresh_token['jti']
    user_agent = request.META.get('HTTP_USER_AGENT', '')[:500]

    session, _ = UserSession.objects.update_or_create(
        jti=jti,
        defaults={
            'user': user,
            'ip_address': get_client_ip(request),
            'user_agent': user_agent,
            'device_label': parse_device_label(user_agent),
            'is_active': True,
            'revoked_at': None,
        },
    )
    return session
