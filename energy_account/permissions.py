"""
energy_account/permissions.py

Access control for the Energy Account module.

Rules:
  - super_admin and admin always have access.
  - All other users must have an active UserSectionAccess row for the
    'energy_account' section, OR an active non-expired TemporaryAccess row.
  - Unauthenticated requests are always denied.
"""

from django.utils import timezone
from rest_framework.permissions import BasePermission

from users.models import TemporaryAccess, UserSectionAccess


class HasEAAccess(BasePermission):
    """
    DRF permission class — grants access to EA endpoints only for users
    who have been explicitly assigned the energy_account section.
    """

    message = (
        'You do not have access to the Energy Account module. '
        'Contact your administrator to request access.'
    )

    def has_permission(self, request, view):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        # Admins always have full access
        if getattr(user, 'role', None) in ('super_admin', 'admin'):
            return True

        now = timezone.now()

        # Permanent section access
        if UserSectionAccess.objects.filter(
            user=user,
            section__name='energy_account',
            is_active=True,
        ).exists():
            return True

        # Temporary access (not expired)
        if TemporaryAccess.objects.filter(
            user=user,
            section__name='energy_account',
            is_active=True,
            expires_at__gt=now,
        ).exists():
            return True

        return False
