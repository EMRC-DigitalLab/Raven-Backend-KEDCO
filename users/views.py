# users/views.py
from django.contrib.auth import login, logout
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .models import (
    AccessLog,
    Permission,
    Section,
    TemporaryAccess,
    User,
    UserSectionAccess,
)
from .serializers import (
    CustomTokenObtainPairSerializer,
    LoginSerializer,
    PermissionSerializer,
    SectionSerializer,
    TemporaryAccessSerializer,
    UserListSerializer,
    UserPermissionsSerializer,
    UserSectionAccessSerializer,
    UserSerializer,
)


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

class IsAdminOrManager(permissions.BasePermission):
    """Custom permission for admin or managers"""
    
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated and 
            (request.user.role in ['super_admin', 'admin'] or 
             UserSectionAccess.objects.filter(user=request.user, is_manager=True).exists())
        )


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def login_view(request):
    """Login endpoint that returns JWT tokens"""
    serializer = CustomTokenObtainPairSerializer(data=request.data)
    try:
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def logout_view(request):
    """Logout endpoint - blacklist refresh token"""
    try:
        refresh_token = request.data.get('refresh')
        if refresh_token:
            token = RefreshToken(refresh_token)
            token.blacklist()
        return Response({'message': 'Logged out successfully'})
    except Exception as e:
        return Response({'message': 'Logout completed'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def current_user(request):
    """Get current user info with permissions - cached version"""
    # Add caching header to reduce redundant calls
    response = Response({
        'user': UserListSerializer(request.user).data,
        'permissions': UserPermissionsSerializer(request.user).data
    })
    # Cache for 5 minutes
    # response['Cache-Control'] = 'max-age=300'
    return response


class UserListCreateView(generics.ListCreateAPIView):
    queryset = User.objects.all()
    permission_classes = [IsAdminOrManager]
    
    def get_serializer_class(self):
        if self.request.method == 'POST':
            return UserSerializer
        return UserListSerializer
    
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class UserRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    queryset = User.objects.all()
    serializer_class = UserListSerializer
    permission_classes = [IsAdminOrManager]


class SectionListView(generics.ListAPIView):
    queryset = Section.objects.filter(is_active=True)
    serializer_class = SectionSerializer
    permission_classes = [permissions.IsAuthenticated]


class PermissionListView(generics.ListAPIView):
    queryset = Permission.objects.all()
    serializer_class = PermissionSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        queryset = Permission.objects.all()
        section = self.request.query_params.get('section', None)
        if section:
            queryset = queryset.filter(section__name=section)
        return queryset


class UserSectionAccessListCreateView(generics.ListCreateAPIView):
    queryset = UserSectionAccess.objects.all()
    serializer_class = UserSectionAccessSerializer
    permission_classes = [IsAdminOrManager]
    
    def get_queryset(self):
        queryset = UserSectionAccess.objects.select_related('user', 'section').prefetch_related('permissions')
        user_id = self.request.query_params.get('user', None)
        if user_id:
            queryset = queryset.filter(user_id=user_id)
        return queryset
    
    def perform_create(self, serializer):
        serializer.save(granted_by=self.request.user)
        
        # Log the access grant
        AccessLog.objects.create(
            user=serializer.instance.user,
            section=serializer.instance.section,
            action='granted',
            performed_by=self.request.user,
            details={'is_manager': serializer.instance.is_manager}
        )


class TemporaryAccessListCreateView(generics.ListCreateAPIView):
    queryset = TemporaryAccess.objects.all()
    serializer_class = TemporaryAccessSerializer
    permission_classes = [IsAdminOrManager]
    
    def get_queryset(self):
        queryset = TemporaryAccess.objects.select_related('user', 'section').prefetch_related('permissions')
        user_id = self.request.query_params.get('user', None)
        if user_id:
            queryset = queryset.filter(user_id=user_id)
        return queryset
    
    def perform_create(self, serializer):
        serializer.save(granted_by=self.request.user)
        
        # Log the temporary access grant
        AccessLog.objects.create(
            user=serializer.instance.user,
            section=serializer.instance.section,
            action='granted',
            performed_by=self.request.user,
            details={
                'access_type': 'temporary',
                'expires_at': serializer.instance.expires_at.isoformat(),
                'reason': serializer.instance.reason
            }
        )


@api_view(['DELETE'])
@permission_classes([IsAdminOrManager])
def revoke_access(request, user_id, section_id):
    """Revoke user access to a section"""
    try:
        access = UserSectionAccess.objects.get(user_id=user_id, section_id=section_id)
        access.is_active = False
        access.save()
        
        # Log the revocation
        AccessLog.objects.create(
            user=access.user,
            section=access.section,
            action='revoked',
            performed_by=request.user
        )
        
        return Response({'message': 'Access revoked successfully'})
    except UserSectionAccess.DoesNotExist:
        return Response({'error': 'Access not found'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['DELETE'])
@permission_classes([IsAdminOrManager])
def revoke_temporary_access(request, temp_access_id):
    """Revoke temporary access"""
    try:
        temp_access = TemporaryAccess.objects.get(id=temp_access_id)
        temp_access.is_active = False
        temp_access.save()
        
        # Log the revocation
        AccessLog.objects.create(
            user=temp_access.user,
            section=temp_access.section,
            action='revoked',
            performed_by=request.user,
            details={'access_type': 'temporary'}
        )
        
        return Response({'message': 'Temporary access revoked successfully'})
    except TemporaryAccess.DoesNotExist:
        return Response({'error': 'Temporary access not found'}, status=status.HTTP_404_NOT_FOUND)