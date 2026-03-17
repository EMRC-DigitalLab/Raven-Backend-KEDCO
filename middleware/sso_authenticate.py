"""
middleware/sso_authenticate.py
==============================
Keycloak SSO middleware for Raven (Django / DRF).

Verifies Keycloak-issued RS256 JWTs and maps the `raven_role` claim
into Raven's existing RBAC model. No existing auth files are modified.

Supported raven_role values (8 roles):
  super_admin, management, technical, commercial, financial,
  human_resource, TMO, energy_accounting

Future sections (TMO, energy_accounting) are reserved with their own
section slugs so they slot straight in once those modules are built.

Usage
-----
    from middleware.sso_authenticate import require_sso, require_role

    @api_view(['GET'])
    @require_sso
    def my_view(request):
        user = request.sso_user   # dict — see _map_role() for keys
        ...

    @api_view(['GET'])
    @require_role('technical')   # includes require_sso automatically
    def technical_only(request):
        ...
"""

import os
from functools import lru_cache, wraps

import requests
from jose import JWTError, jwt
from rest_framework import status
from rest_framework.response import Response

# ── Config ─────────────────────────────────────────────────────────────────

# Prefer Django settings; fall back to env var for scripts run outside Django
try:
    from django.conf import settings as _django_settings
    KEYCLOAK_REALM_URL = getattr(
        _django_settings,
        'KEYCLOAK_REALM_URL',
        os.getenv('KEYCLOAK_REALM_URL', 'http://31.97.56.29:8083/realms/KEDCO'),
    )
except Exception:
    KEYCLOAK_REALM_URL = os.getenv(
        'KEYCLOAK_REALM_URL',
        'http://31.97.56.29:8083/realms/KEDCO',
    )

# Roles that bypass section checks and see everything in Raven
FULL_ACCESS_ROLES = {'management', 'super_admin'}


# ── Role → Raven mapping ────────────────────────────────────────────────────

# Each Keycloak raven_role maps to:
#   raven_role       → the closest Raven USER_ROLE (super_admin/admin/staff/viewer)
#   allowed_sections → list of Raven section slugs this role can access
#                      (use '__all__' string for full-access roles)
#
# NOTE: 'tmo' and 'energy_accounting' are their own dedicated Raven modules
# that are coming soon. Their section slugs are reserved here so when
# those sections land you only need to add the Section row to the DB —
# this mapping file needs zero changes.

_ROLE_MAP = {
    'super_admin': {
        'raven_role':       'super_admin',
        'allowed_sections': '__all__',
        'full_access':      True,
    },
    'management': {
        'raven_role':       'admin',
        'allowed_sections': '__all__',
        'full_access':      True,
    },
    'technical': {
        'raven_role':       'staff',
        'allowed_sections': ['technical'],
        'full_access':      False,
    },
    'commercial': {
        'raven_role':       'staff',
        'allowed_sections': ['commercial'],
        'full_access':      False,
    },
    'financial': {
        'raven_role':       'staff',
        'allowed_sections': ['financial'],
        'full_access':      False,
    },
    'human_resource': {
        'raven_role':       'staff',
        'allowed_sections': ['hr'],
        'full_access':      False,
    },
    # ── Coming-soon modules — reserved slugs ───────────────────────────────
    'TMO': {
        'raven_role':       'staff',
        'allowed_sections': ['tmo'],          # 'tmo' section — coming soon
        'full_access':      False,
    },
    'energy_accounting': {
        'raven_role':       'staff',
        'allowed_sections': ['energy_accounting'],  # coming soon
        'full_access':      False,
    },
}

# Fallback for unknown / missing raven_role claim
_NO_ACCESS = {
    'raven_role':       'viewer',
    'allowed_sections': [],
    'full_access':      False,
}


def _map_role(keycloak_role: str) -> dict:
    """
    Returns the Raven role mapping dict for the given Keycloak raven_role.
    Falls back to viewer / no sections for unrecognised roles.
    """
    return _ROLE_MAP.get(keycloak_role, _NO_ACCESS)


# ── Keycloak public key (cached after first fetch) ─────────────────────────

@lru_cache(maxsize=1)
def _get_keycloak_public_key() -> str:
    """
    Fetches the Keycloak realm public key and returns it in PEM format.
    Result is cached for the lifetime of the process.
    """
    resp = requests.get(KEYCLOAK_REALM_URL, timeout=5)
    resp.raise_for_status()
    raw_key = resp.json()['public_key']
    return f'-----BEGIN PUBLIC KEY-----\n{raw_key}\n-----END PUBLIC KEY-----'


def _decode_token(token: str) -> dict:
    """
    Decodes and validates a Keycloak RS256 JWT.
    Raises jose.JWTError on any validation failure.
    """
    key = _get_keycloak_public_key()
    return jwt.decode(
        token,
        key,
        algorithms=['RS256'],
        issuer=KEYCLOAK_REALM_URL,
        options={'verify_aud': False},
    )


# ── Decorators ─────────────────────────────────────────────────────────────

def require_sso(f):
    """
    DRF-compatible decorator. Validates the Keycloak Bearer token and
    attaches `request.sso_user` to the request for downstream use.

    request.sso_user keys:
        id              – Keycloak subject UUID
        email           – user email from token
        keycloak_role   – raw raven_role claim from token
        raven_role      – mapped Raven role string
        allowed_sections– list of section slugs, or '__all__'
        full_access     – True if management or super_admin
        sso             – always True (marks SSO-authenticated request)
    """
    @wraps(f)
    def decorated(request, *args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return Response(
                {'error': 'SSO: No token provided'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        token = auth_header[7:]
        try:
            payload = _decode_token(token)
        except JWTError as exc:
            return Response(
                {'error': f'SSO: {exc}'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        keycloak_role = payload.get('raven_role', '')
        mapping = _map_role(keycloak_role)

        request.sso_user = {
            'id':               payload['sub'],
            'email':            payload.get('email'),
            'keycloak_role':    keycloak_role,
            'raven_role':       mapping['raven_role'],
            'allowed_sections': mapping['allowed_sections'],
            'full_access':      mapping['full_access'],
            'sso':              True,
        }

        return f(request, *args, **kwargs)

    return decorated


def require_role(*allowed_roles):
    """
    Role-guard decorator. Wraps require_sso, so you only need this one.
    management and super_admin always pass regardless of allowed_roles.

    Example:
        @api_view(['GET'])
        @require_role('technical', 'commercial')
        def my_view(request):
            ...
    """
    def decorator(f):
        @wraps(f)
        @require_sso
        def decorated(request, *args, **kwargs):
            keycloak_role = request.sso_user['keycloak_role']
            if keycloak_role in FULL_ACCESS_ROLES or keycloak_role in allowed_roles:
                return f(request, *args, **kwargs)
            return Response(
                {'error': 'Forbidden — insufficient role'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return decorated
    return decorator
