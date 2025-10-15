# users/models.py
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from datetime import timedelta

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
    department = models.CharField(max_length=50, blank=True, null=True)
    employee_id = models.CharField(max_length=20, unique=True, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='created_users')
    
    def __str__(self):
        return f"{self.username} - {self.get_role_display()}"


class Section(models.Model):
    """Dashboard sections that users can have access to"""
    
    SECTION_CHOICES = (
        ('overview', 'Overview'),
        ('commercial', 'Commercial'),
        ('financial', 'Financial'),
        ('technical', 'Technical'),
        ('hr', 'Human Resource'),
        ('regulatory', 'Regulatory'),
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