"""
tmo/permissions.py

DRF permission classes for the TMO (Technical Management Operations) module.
"""
from django.utils import timezone
from rest_framework.permissions import BasePermission

from users.models import TemporaryAccess, UserSectionAccess

ADMIN_ROLES = ('super_admin', 'admin')
MANAGER_ROLES = ('super_admin', 'admin', 'manager')


class HasTMOAccess(BasePermission):
    """
    Grants read access to the TMO dashboard module.

    Passes if:
      - User is super_admin or admin (always through)
      - User has an active UserSectionAccess for 'tmo'
      - User has a non-expired TemporaryAccess for 'tmo'
    """
    message = (
        'You do not have access to the TMO module. '
        'Contact your administrator to request access.'
    )

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if getattr(user, 'role', None) in ADMIN_ROLES:
            return True
        now = timezone.now()
        if UserSectionAccess.objects.filter(
            user=user, section__name='tmo', is_active=True
        ).exists():
            return True
        if TemporaryAccess.objects.filter(
            user=user, section__name='tmo',
            is_active=True, expires_at__gt=now,
        ).exists():
            return True
        return False


class HasTMOAdminAccess(BasePermission):
    """
    Stricter permission for TMO settings endpoints (POST only).
    Passes if:
      - User is super_admin, admin, or manager
      - User has an active UserSectionAccess for 'tmo' with is_manager=True
    """
    message = (
        'Only TMO managers or admins can modify TMO settings.'
    )

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if getattr(user, 'role', None) in MANAGER_ROLES:
            return True
        if UserSectionAccess.objects.filter(
            user=user, section__name='tmo', is_active=True
        ).exists():
            return True
        return False
