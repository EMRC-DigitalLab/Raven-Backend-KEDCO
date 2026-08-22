# users/serializers.py
from django.contrib.auth import authenticate
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import (
    AccessLog,
    Department,
    Permission,
    Role,
    RolePermission,
    Section,
    TemporaryAccess,
    User,
    UserSectionAccess,
    UserSession,
)


def _validate_role_value(value):
    if not Role.objects.filter(name=value).exists():
        raise serializers.ValidationError(f"'{value}' is not a recognized role. See GET /roles/ for valid values.")
    return value


def _validate_department_value(value):
    if not value:
        return None
    if not Department.objects.filter(name=value).exists():
        raise serializers.ValidationError(f"'{value}' is not a recognized department. See GET /departments/ for valid values.")
    return value


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    # Declared explicitly (rather than left to ModelSerializer's auto-generated
    # unique field) so blank input can be normalized to None before the
    # uniqueness check runs — otherwise a second user submitted with '' hits
    # a UNIQUE violation on the empty string.
    employee_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    # Plain CharField override: User.role keeps its model-level choices=
    # (the original 5, for get_role_display()/backward compat), but valid
    # *input* now comes from the Role catalog — the auto-generated
    # ChoiceField DRF would otherwise build from those choices= would reject
    # any newer role (tmo, cto, ...).
    role = serializers.CharField()
    department = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'phone_number',
                 'department', 'employee_id', 'role', 'is_active', 'profile_picture', 'created_at', 'password']
        extra_kwargs = {
            'password': {'write_only': True}
        }

    def validate_employee_id(self, value):
        if not value:
            return None
        qs = User.objects.filter(employee_id=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('A user with this employee ID already exists.')
        return value

    def validate_department(self, value):
        return _validate_department_value(value)

    def validate_role(self, value):
        request = self.context.get('request')
        requester = getattr(request, 'user', None) if request else None
        if value == 'super_admin' and (requester is None or requester.role != 'super_admin'):
            raise serializers.ValidationError('Only a super admin can assign the super_admin role.')
        return _validate_role_value(value)

    def create(self, validated_data):
        password = validated_data.pop('password')
        return User.objects.create_user(password=password, **validated_data)


class UserListSerializer(serializers.ModelSerializer):
    """Serializer for listing users without sensitive data"""

    role = serializers.CharField()

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name',
                 'department', 'employee_id', 'role', 'is_active', 'profile_picture', 'created_at']


class MyProfileSerializer(serializers.ModelSerializer):
    """Self-service profile: a user editing their own name/phone/picture.
    Deliberately excludes role/department/employee_id/is_active/username/email —
    those stay admin-controlled via UserSerializer (see UserViewSet)."""

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'phone_number',
                 'department', 'employee_id', 'role', 'profile_picture', 'created_at']
        read_only_fields = ['id', 'username', 'email', 'department', 'employee_id', 'role', 'created_at']


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ['id', 'name', 'display_name', 'description', 'is_system', 'created_by', 'created_at']
        read_only_fields = ['is_system', 'created_by', 'created_at']

    def validate_name(self, value):
        value = value.strip().lower().replace(' ', '_')
        if self.instance and self.instance.name != value:
            raise serializers.ValidationError('name cannot be changed after creation — create a new role instead.')
        return value


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ['id', 'name', 'description', 'created_at']
        read_only_fields = ['created_at']


class SectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Section
        fields = ['id', 'name', 'display_name', 'description', 'icon', 'is_active']


class PermissionSerializer(serializers.ModelSerializer):
    section_name = serializers.CharField(source='section.name', read_only=True)
    
    class Meta:
        model = Permission
        fields = ['id', 'section', 'section_name', 'name', 'codename', 'permission_type', 'description']


class UserSectionAccessSerializer(serializers.ModelSerializer):
    section_name = serializers.CharField(source='section.name', read_only=True)
    section_display_name = serializers.CharField(source='section.display_name', read_only=True)
    permissions = PermissionSerializer(many=True, read_only=True)
    permission_ids = serializers.ListField(child=serializers.IntegerField(), write_only=True, required=False)
    
    class Meta:
        model = UserSectionAccess
        fields = ['id', 'user', 'section', 'section_name', 'section_display_name', 
                 'permissions', 'permission_ids', 'is_manager', 'granted_by', 'granted_at', 'is_active']
        read_only_fields = ['granted_by', 'granted_at']
    
    def create(self, validated_data):
        permission_ids = validated_data.pop('permission_ids', [])
        access = UserSectionAccess.objects.create(**validated_data)
        if permission_ids:
            permissions = Permission.objects.filter(id__in=permission_ids)
            access.permissions.set(permissions)
        return access


class TemporaryAccessSerializer(serializers.ModelSerializer):
    section_name = serializers.CharField(source='section.name', read_only=True)
    section_display_name = serializers.CharField(source='section.display_name', read_only=True)
    permissions = PermissionSerializer(many=True, read_only=True)
    permission_ids = serializers.ListField(child=serializers.IntegerField(), write_only=True, required=False)
    is_expired = serializers.SerializerMethodField()
    
    class Meta:
        model = TemporaryAccess
        fields = ['id', 'user', 'section', 'section_name', 'section_display_name', 
                 'permissions', 'permission_ids', 'granted_by', 'expires_at', 'reason', 
                 'granted_at', 'is_active', 'is_expired']
        read_only_fields = ['granted_by', 'granted_at']
    
    def get_is_expired(self, obj):
        return obj.is_expired()
    
    def create(self, validated_data):
        permission_ids = validated_data.pop('permission_ids', [])
        temp_access = TemporaryAccess.objects.create(**validated_data)
        if permission_ids:
            permissions = Permission.objects.filter(id__in=permission_ids)
            temp_access.permissions.set(permissions)
        return temp_access


class RolePermissionSerializer(serializers.ModelSerializer):
    # SerializerMethodField rather than source='get_role_display' — RolePermission.role
    # keeps its own choices=User.USER_ROLES for the original 5, but this looks
    # the value up in the Role catalog first so custom roles (tmo, cto, ...)
    # also get a proper display_name instead of falling through to the raw slug.
    role_display = serializers.SerializerMethodField()
    role = serializers.CharField()
    section_name = serializers.CharField(source='section.name', read_only=True)
    section_display_name = serializers.CharField(source='section.display_name', read_only=True)
    permissions = PermissionSerializer(many=True, read_only=True)
    permission_ids = serializers.ListField(child=serializers.IntegerField(), write_only=True, required=False)

    class Meta:
        model = RolePermission
        fields = ['id', 'role', 'role_display', 'section', 'section_name', 'section_display_name',
                 'permissions', 'permission_ids', 'is_manager', 'updated_by', 'updated_at']
        read_only_fields = ['updated_by', 'updated_at']

    def get_role_display(self, obj):
        role = Role.objects.filter(name=obj.role).first()
        return role.display_name if role else obj.get_role_display()

    def validate_role(self, value):
        return _validate_role_value(value)

    def create(self, validated_data):
        permission_ids = validated_data.pop('permission_ids', [])
        role_permission = RolePermission.objects.create(**validated_data)
        if permission_ids:
            role_permission.permissions.set(Permission.objects.filter(id__in=permission_ids))
        return role_permission

    def update(self, instance, validated_data):
        permission_ids = validated_data.pop('permission_ids', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if permission_ids is not None:
            instance.permissions.set(Permission.objects.filter(id__in=permission_ids))
        return instance


class UserSessionSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    full_name = serializers.SerializerMethodField()
    role = serializers.CharField(source='user.role', read_only=True)

    class Meta:
        model = UserSession
        fields = ['id', 'user', 'username', 'full_name', 'role', 'ip_address', 'user_agent',
                 'device_label', 'created_at', 'last_seen_at', 'is_active']
        read_only_fields = fields

    def get_full_name(self, obj):
        return obj.user.get_full_name() or obj.user.username


class UserPermissionsSerializer(serializers.Serializer):
    """Serializer to return user's complete permissions structure"""
    sections = serializers.SerializerMethodField()
    
    def get_sections(self, user):
        # Role baseline — what everyone with this role gets, before any per-user grants
        role_access = RolePermission.objects.filter(
            role=user.role
        ).select_related('section').prefetch_related('permissions')

        # Get permanent access
        permanent_access = UserSectionAccess.objects.filter(
            user=user,
            is_active=True
        ).select_related('section').prefetch_related('permissions')

        # Get active temporary access
        temp_access = TemporaryAccess.objects.filter(
            user=user,
            is_active=True,
            expires_at__gt=timezone.now()
        ).select_related('section').prefetch_related('permissions')

        sections_data = {}

        # Process role baseline first
        for access in role_access:
            section_name = access.section.name
            sections_data[section_name] = {
                'name': section_name,
                'display_name': access.section.display_name,
                'icon': access.section.icon,
                'is_manager': access.is_manager,
                'permissions': [p.codename for p in access.permissions.all()],
                'access_type': 'role'
            }

        # Process permanent per-user access — adds to / overrides the role baseline
        for access in permanent_access:
            section_name = access.section.name
            user_permissions = [p.codename for p in access.permissions.all()]
            existing = sections_data.get(section_name)
            if existing:
                existing['permissions'] = list(set(existing['permissions']).union(user_permissions))
                existing['is_manager'] = existing['is_manager'] or access.is_manager
                existing['access_type'] = 'permanent'
            else:
                sections_data[section_name] = {
                    'name': section_name,
                    'display_name': access.section.display_name,
                    'icon': access.section.icon,
                    'is_manager': access.is_manager,
                    'permissions': user_permissions,
                    'access_type': 'permanent'
                }

        # Process temporary access (can override role/permanent)
        for access in temp_access:
            section_name = access.section.name
            if section_name not in sections_data:
                sections_data[section_name] = {
                    'name': section_name,
                    'display_name': access.section.display_name,
                    'icon': access.section.icon,
                    'is_manager': False,
                    'permissions': [],
                    'access_type': 'temporary',
                    'expires_at': access.expires_at
                }

            # Merge permissions from temporary access
            temp_permissions = [p.codename for p in access.permissions.all()]
            existing_permissions = set(sections_data[section_name]['permissions'])
            sections_data[section_name]['permissions'] = list(existing_permissions.union(set(temp_permissions)))

            if sections_data[section_name]['access_type'] in ('permanent', 'role'):
                sections_data[section_name]['has_temporary'] = True
                sections_data[section_name]['temp_expires_at'] = access.expires_at

        return list(sections_data.values())


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()  # This will accept username or email
    password = serializers.CharField()
    
    def validate(self, attrs):
        username_or_email = attrs.get('username')
        password = attrs.get('password')
        
        if not username_or_email or not password:
            raise serializers.ValidationError('Username/email and password are required.')
        
        # Normalize input to lowercase for case-insensitive lookup
        username_or_email_lower = username_or_email.lower().strip()
        
        # First, try to authenticate with the value as username (case-insensitive)
        user = None
        try:
            user_obj = User.objects.get(username__iexact=username_or_email_lower)
            user = authenticate(username=user_obj.username, password=password)
        except User.DoesNotExist:
            pass
        
        # If that fails and the input looks like an email, try to find user by email (case-insensitive)
        if not user and '@' in username_or_email:
            try:
                user_obj = User.objects.get(email__iexact=username_or_email_lower)
                # Now try to authenticate using the found user's username
                user = authenticate(username=user_obj.username, password=password)
            except User.DoesNotExist:
                pass
        
        if user:
            if not user.is_active:
                raise serializers.ValidationError('User account is disabled.')
            attrs['user'] = user
            return attrs
        else:
            raise serializers.ValidationError('Invalid username/email or password.')


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = 'username'  # Can be username or email
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Remove username validation to allow email
        self.fields[self.username_field] = serializers.CharField()
    
    def validate(self, attrs):
        username_or_email = attrs.get(self.username_field)
        password = attrs.get('password')
        
        if not username_or_email or not password:
            raise serializers.ValidationError('Username/email and password are required.')
        
        # Normalize input to lowercase for case-insensitive lookup
        username_or_email_lower = username_or_email.lower().strip()
        
        # First, try to authenticate with the value as username (case-insensitive)
        user = None
        try:
            user_obj = User.objects.get(username__iexact=username_or_email_lower)
            user = authenticate(username=user_obj.username, password=password)
        except User.DoesNotExist:
            pass
        
        # If that fails and the input looks like an email, try to find user by email (case-insensitive)
        if not user and '@' in username_or_email:
            try:
                user_obj = User.objects.get(email__iexact=username_or_email_lower)
                # Now try to authenticate using the found user's username
                user = authenticate(username=user_obj.username, password=password)
            except User.DoesNotExist:
                pass
        
        if not user:
            raise serializers.ValidationError('Invalid username/email or password.')
        
        if not user.is_active:
            raise serializers.ValidationError('User account is disabled.')
        
        # Get tokens
        refresh = self.get_token(user)

        # Track this login as a session (device/IP metadata for the Active Sessions screen)
        request = self.context.get('request')
        if request is not None:
            from .utils import create_user_session
            create_user_session(request, user, refresh)

        # Get user permissions
        permissions_serializer = UserPermissionsSerializer(user)

        return {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user': UserListSerializer(user, context=self.context).data,
            'permissions': permissions_serializer.data,
        }