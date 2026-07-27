# technical/permissions.py
from django.utils import timezone
from rest_framework.permissions import BasePermission

from users.models import TemporaryAccess, UserSectionAccess

ADMIN_ROLES = ('super_admin', 'admin')
MANAGER_ROLES = ('super_admin', 'admin', 'manager')


class HasTechnicalAccess(BasePermission):
    """
    Grants access to technical module endpoints.

    Passes if:
      - User is super_admin or admin
      - User has an active UserSectionAccess for 'technical'
      - User has a non-expired TemporaryAccess for 'technical'
    """
    message = 'You do not have access to the Technical module.'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if getattr(user, 'role', None) in ADMIN_ROLES:
            return True
        now = timezone.now()
        if UserSectionAccess.objects.filter(
            user=user, section__name='technical', is_active=True
        ).exists():
            return True
        if TemporaryAccess.objects.filter(
            user=user, section__name='technical',
            is_active=True, expires_at__gt=now,
        ).exists():
            return True
        return False


class HasTechnicalAdminAccess(BasePermission):
    """
    Stricter permission for technical write operations.

    Passes if:
      - User is super_admin, admin, or manager
      - User has an active UserSectionAccess for 'technical' with is_manager=True
    """
    message = 'Only technical managers or admins can perform this action.'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if getattr(user, 'role', None) in MANAGER_ROLES:
            return True
        if UserSectionAccess.objects.filter(
            user=user, section__name='technical', is_active=True, is_manager=True
        ).exists():
            return True
        return False
