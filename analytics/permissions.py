"""
analytics/permissions.py

DRF permission class for the GridLens module.
"""
from django.utils import timezone
from rest_framework.permissions import BasePermission

from users.models import TemporaryAccess, UserSectionAccess


class HasGridLensAccess(BasePermission):
    """
    Grants access to the GridLens loss decomposition module.

    Passes if:
      - User is super_admin or admin (always through)
      - User has an active UserSectionAccess for 'grid_lens'
      - User has a non-expired TemporaryAccess for 'grid_lens'
    """
    message = (
        'You do not have access to the GridLens module. '
        'Contact your administrator to request access.'
    )

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if getattr(user, 'role', None) in ('super_admin', 'admin'):
            return True
        now = timezone.now()
        if UserSectionAccess.objects.filter(
            user=user, section__name='grid_lens', is_active=True
        ).exists():
            return True
        if TemporaryAccess.objects.filter(
            user=user, section__name='grid_lens',
            is_active=True, expires_at__gt=now,
        ).exists():
            return True
        return False
