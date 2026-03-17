# users/views.py
import requests as http_client

from django.conf import settings
from django.contrib.auth import login, logout
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from middleware.sso_authenticate import _decode_token, _map_role

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



# ── Raven → DataNest handoff ──────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def sso_handoff(request):
    """
    Raven → DataNest handoff.
    Raven frontend calls this with the Keycloak Bearer token.
    Stores the token against a one-time code (60s TTL).
    Returns a redirect_url pointing to DataNest frontend with ?code=

    Request:
        Authorization: Bearer <keycloak_token>

    Response:
        { "redirect_url": "<DATANEST_FRONTEND_URL>/home?code=<uuid>" }
    """
    import uuid
    from django.core.cache import cache

    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return Response({'error': 'SSO: No token provided'}, status=status.HTTP_401_UNAUTHORIZED)

    token = auth_header[7:]
    code = str(uuid.uuid4())
    cache.set(f'handoff:{code}', token, timeout=60)

    return Response({
        'redirect_url': f"{settings.DATANEST_FRONTEND_URL}/home?code={code}"
    })


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def sso_redeem(request):
    """
    Called by DataNest frontend after receiving ?code= from Raven.
    Exchanges the one-time code for the Keycloak token.
    Code is deleted immediately after use.

    Request:
        { "code": "<uuid>" }

    Response:
        { "token": "<keycloak_access_token>" }
    """
    from django.core.cache import cache

    code = request.data.get('code')
    if not code:
        return Response({'error': 'Missing code'}, status=status.HTTP_400_BAD_REQUEST)

    token = cache.get(f'handoff:{code}')
    if not token:
        return Response({'error': 'Invalid or expired code'}, status=status.HTTP_401_UNAUTHORIZED)

    cache.delete(f'handoff:{code}')
    return Response({'token': token})


# ── SSO: verify token / get current SSO user ─────────────────────────────────

@api_view(['GET', 'POST'])
@permission_classes([permissions.AllowAny])
def sso_me(request):
    """
    Accepts a Keycloak Bearer token and returns the mapped Raven user.
    Equivalent to DataNest's /api/auth/sso/verify and /api/auth/sso/me.

    Usage:
        GET  /api/auth/sso/me/
        POST /api/auth/sso/verify/
        Authorization: Bearer <keycloak_access_token>
    """
    from jose import JWTError
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return Response({'error': 'SSO: No token provided'}, status=status.HTTP_401_UNAUTHORIZED)

    token = auth_header[7:]
    try:
        payload = _decode_token(token)
    except JWTError as exc:
        return Response({'error': f'SSO: {exc}'}, status=status.HTTP_401_UNAUTHORIZED)

    keycloak_role = payload.get('raven_role', '')
    mapping = _map_role(keycloak_role)

    return Response({
        'valid': True,
        'user': {
            'id':               payload.get('sub'),
            'email':            payload.get('email'),
            'keycloak_role':    keycloak_role,
            'raven_role':       mapping['raven_role'],
            'allowed_sections': mapping['allowed_sections'],
            'full_access':      mapping['full_access'],
            'sso':              True,
        }
    })


# ── DataNest → Raven SSO handoff ─────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def sso_exchange(request):
    """
    DataNest → Raven SSO handoff.

    Frontend calls this when it detects ?code= in the URL after being
    redirected from DataNest.  We exchange the one-time code for a
    Keycloak access token, validate it, and return the user's role info.

    Request body:
        { "code": "<one-time-code>" }

    Success response:
        {
            "token": "<keycloak_access_token>",
            "user": {
                "id": "<keycloak-sub>",
                "email": "<email>",
                "keycloak_role": "<raven_role claim>",
                "raven_role": "<mapped raven role>",
                "allowed_sections": [...] | "__all__",
                "full_access": true|false
            }
        }

    Error responses:
        401 — code expired or invalid (redirect back to DataNest)
        400 — missing code
        502 — DataNest exchange service unreachable
    """
    code = request.data.get('code')
    if not code:
        return Response({'error': 'Missing code'}, status=status.HTTP_400_BAD_REQUEST)

    # Step 1: Exchange one-time code for Keycloak token via DataNest
    exchange_url = settings.DATANEST_SSO_URL
    try:
        datanest_response = http_client.post(
            exchange_url,
            json={'code': code},
            timeout=10,
        )
    except http_client.exceptions.RequestException:
        return Response(
            {'error': 'SSO exchange service unreachable'},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    if datanest_response.status_code == 401:
        return Response(
            {'error': 'SSO: Code expired or invalid'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if not datanest_response.ok:
        return Response(
            {'error': 'SSO exchange failed'},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    token = datanest_response.json().get('token')
    if not token:
        return Response(
            {'error': 'SSO: No token in exchange response'},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    # Step 2: Validate the Keycloak token (RS256 via realm public key)
    from jose import JWTError
    try:
        payload = _decode_token(token)
    except JWTError as exc:
        return Response(
            {'error': f'SSO: Token validation failed — {exc}'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    # Step 3: Map raven_role claim → Raven role + allowed sections
    keycloak_role = payload.get('raven_role', '')
    mapping = _map_role(keycloak_role)

    return Response({
        'token': token,
        'user': {
            'id':               payload['sub'],
            'email':            payload.get('email'),
            'keycloak_role':    keycloak_role,
            'raven_role':       mapping['raven_role'],
            'allowed_sections': mapping['allowed_sections'],
            'full_access':      mapping['full_access'],
        },
    }, status=status.HTTP_200_OK)