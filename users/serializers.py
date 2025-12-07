# users/serializers.py
from rest_framework import serializers
from django.contrib.auth import authenticate
from django.utils import timezone
from .models import User, Section, Permission, UserSectionAccess, TemporaryAccess, AccessLog
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'phone_number', 
                 'department', 'employee_id', 'role', 'is_active', 'created_at', 'password']
        extra_kwargs = {
            'password': {'write_only': True}
        }
    
    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User.objects.create_user(**validated_data)
        user.set_password(password)
        user.save()
        return user


class UserListSerializer(serializers.ModelSerializer):
    """Serializer for listing users without sensitive data"""
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 
                 'department', 'employee_id', 'role', 'is_active', 'created_at']


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


class UserPermissionsSerializer(serializers.Serializer):
    """Serializer to return user's complete permissions structure"""
    sections = serializers.SerializerMethodField()
    
    def get_sections(self, user):
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
        
        # Process permanent access
        for access in permanent_access:
            section_name = access.section.name
            sections_data[section_name] = {
                'name': section_name,
                'display_name': access.section.display_name,
                'icon': access.section.icon,
                'is_manager': access.is_manager,
                'permissions': [p.codename for p in access.permissions.all()],
                'access_type': 'permanent'
            }
        
        # Process temporary access (can override permanent)
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
            
            if sections_data[section_name]['access_type'] == 'permanent':
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
        
        # Get user permissions
        permissions_serializer = UserPermissionsSerializer(user)
        
        return {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user': UserListSerializer(user).data,
            'permissions': permissions_serializer.data,
        }