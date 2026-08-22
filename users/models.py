# users/models.py
from datetime import timedelta

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """Extended User model with additional fields"""
    
    USER_ROLES = (
        ('super_admin', 'Super Admin'),
        ('admin', 'Admin'),
        ('manager', 'Manager'),
        ('staff', 'Staff'),
        ('viewer', 'Viewer'),
    )
    
    role = models.CharField(max_length=20, choices=USER_ROLES, default='staff')
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    profile_picture = models.ImageField(upload_to='profile_pictures/', blank=True, null=True)
    department = models.CharField(max_length=50, blank=True, null=True)
    employee_id = models.CharField(max_length=20, unique=True, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_users')
    
    def get_role_display(self):
        """Catalog-first: shows the Role model's display_name for custom
        roles (e.g. 'tmo' -> 'TMO'), falling back to Django's own
        choices-based lookup for the original 5 built-in roles."""
        role = Role.objects.filter(name=self.role).first()
        return role.display_name if role else super().get_role_display()

    def __str__(self):
        return f"{self.username} - {self.get_role_display()}"


class Role(models.Model):
    """Catalog of assignable roles. The original 5 (super_admin/admin/manager/
    staff/viewer) stay as User.role's model-level choices= for backward
    compatibility (get_role_display() fallback, RolePermission.role's own
    choices=) — this table is what actually governs which role strings are
    valid to assign going forward, including new ones (tmo, cto, cco, ...).
    """

    name = models.CharField(max_length=20, unique=True)
    display_name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_system = models.BooleanField(
        default=False,
        help_text="super_admin/admin/manager — depended on by permission checks across the codebase; cannot be renamed or deleted",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_roles')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.display_name


class Department(models.Model):
    """Catalog of assignable departments — replaces the frontend's hardcoded dropdown."""

    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Section(models.Model):
    """Dashboard sections that users can have access to"""
    
    SECTION_CHOICES = (
        ('overview', 'Overview'),
        ('commercial', 'Commercial'),
        ('financial', 'Financial'),
        ('technical', 'Technical'),
        ('hr', 'Human Resource'),
        ('regulatory', 'Regulatory'),
        ('energy_account', 'Energy Account'),
        ('grid_lens', 'GridLens'),
        ('tmo', 'TMO'),
        ('cto', 'CTO Dashboard'),
    )
    
    name = models.CharField(max_length=20, choices=SECTION_CHOICES, unique=True)
    display_name = models.CharField(max_length=50)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True, help_text="Icon class name")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return self.display_name


class Permission(models.Model):
    """Granular permissions within sections"""
    
    PERMISSION_TYPES = (
        ('view', 'View'),
        ('create', 'Create'),
        ('edit', 'Edit'),
        ('delete', 'Delete'),
        ('admin', 'Admin'),
    )
    
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='permissions')
    name = models.CharField(max_length=50)
    codename = models.CharField(max_length=100, unique=True)
    permission_type = models.CharField(max_length=10, choices=PERMISSION_TYPES)
    description = models.TextField(blank=True)
    
    class Meta:
        unique_together = ['section', 'codename']
    
    def __str__(self):
        return f"{self.section.display_name} - {self.name}"


class UserSectionAccess(models.Model):
    """User access to specific sections with permissions"""
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='section_access')
    section = models.ForeignKey(Section, on_delete=models.CASCADE)
    permissions = models.ManyToManyField(Permission, blank=True)
    is_manager = models.BooleanField(default=False, help_text="Can manage other users in this section")
    granted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='granted_access')
    granted_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        unique_together = ['user', 'section']
    
    def __str__(self):
        return f"{self.user.username} - {self.section.display_name}"


class TemporaryAccess(models.Model):
    """Temporary access grants with expiration"""
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='temporary_access')
    section = models.ForeignKey(Section, on_delete=models.CASCADE)
    permissions = models.ManyToManyField(Permission, blank=True)
    granted_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='granted_temp_access')
    expires_at = models.DateTimeField()
    reason = models.TextField(blank=True, help_text="Reason for temporary access")
    granted_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    
    def is_expired(self):
        return timezone.now() > self.expires_at
    
    def __str__(self):
        return f"Temp: {self.user.username} - {self.section.display_name} (expires: {self.expires_at})"


class RolePermission(models.Model):
    """Role-level section access — the baseline every user of that role gets.

    Distinct from UserSectionAccess (per-user grants/overrides layered on
    top of this baseline in UserPermissionsSerializer.get_sections()).
    """

    role = models.CharField(max_length=20, choices=User.USER_ROLES)
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='role_permissions')
    permissions = models.ManyToManyField(Permission, blank=True)
    is_manager = models.BooleanField(default=False, help_text="Users with this role can manage other users in this section")
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='updated_role_permissions')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['role', 'section']

    def __str__(self):
        return f"{self.get_role_display()} - {self.section.display_name}"


class UserSession(models.Model):
    """Human-readable session metadata, keyed by the current refresh token's jti.

    simplejwt's own OutstandingToken/BlacklistedToken (token_blacklist app)
    is the source of truth for whether a token is actually still valid; this
    model just adds the device/IP/label info needed to display and manage
    sessions, and survives token rotation (jti is updated in place on refresh
    so one login stays one row).
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sessions')
    jti = models.CharField(max_length=255, unique=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    device_label = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='revoked_sessions')

    class Meta:
        ordering = ['-last_seen_at']

    def __str__(self):
        return f"{self.user.username} - {self.device_label or 'Unknown device'} ({'active' if self.is_active else 'revoked'})"


class AccessLog(models.Model):
    """Log of access grants, revokes, and modifications"""
    
    ACTION_CHOICES = (
        ('granted', 'Granted'),
        ('revoked', 'Revoked'),
        ('modified', 'Modified'),
        ('expired', 'Expired'),
    )
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='access_logs')
    section = models.ForeignKey(Section, on_delete=models.CASCADE)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    performed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='performed_actions')
    details = models.JSONField(blank=True, null=True, help_text="Additional details about the action")
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    
    class Meta:
        ordering = ['-timestamp']
    
    def __str__(self):
        return f"{self.action.title()}: {self.user.username} - {self.section.display_name}"