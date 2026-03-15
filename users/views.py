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


# ── Direct Keycloak login (Raven → Keycloak → Raven) ─────────────────────────

@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def sso_login(request):
    """
    Step 1 of direct Keycloak login.
    Redirects the browser to Keycloak's login page.

    Frontend sends the user here when they click "Login with Keycloak".
    After login, Keycloak redirects to sso_callback.
    """
    from urllib.parse import urlencode
    from django.http import HttpResponseRedirect

    params = urlencode({
        'client_id':     settings.KEYCLOAK_CLIENT_ID,
        'redirect_uri':  settings.KEYCLOAK_REDIRECT_URI,
        'response_type': 'code',
        'scope':         'openid email profile',
    })
    return HttpResponseRedirect(f"{settings.KEYCLOAK_AUTH_URL}?{params}")


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def sso_callback(request):
    """
    Step 2 of direct Keycloak login.
    Keycloak redirects here with ?code= after the user authenticates.

    Exchanges the code for a Keycloak token, validates it, then
    redirects the user to the Raven frontend with the token.

    Success redirect:
        {RAVEN_FRONTEND_URL}/auth/callback?token=<access_token>&refresh_token=<refresh_token>

    Error redirect:
        {RAVEN_FRONTEND_URL}/auth/callback?error=<reason>
    """
    from urllib.parse import urlencode
    from django.http import HttpResponseRedirect
    from jose import JWTError

    frontend_callback = f"{settings.RAVEN_FRONTEND_URL}/auth/callback"

    code = request.GET.get('code')
    if not code:
        return HttpResponseRedirect(f"{frontend_callback}?error=no_code")

    # Exchange authorization code for Keycloak tokens
    try:
        token_response = http_client.post(
            settings.KEYCLOAK_TOKEN_URL,
            data={
                'grant_type':    'authorization_code',
                'client_id':     settings.KEYCLOAK_CLIENT_ID,
                'client_secret': settings.KEYCLOAK_CLIENT_SECRET,
                'redirect_uri':  settings.KEYCLOAK_REDIRECT_URI,
                'code':          code,
            },
            timeout=10,
        )
    except http_client.exceptions.RequestException:
        return HttpResponseRedirect(f"{frontend_callback}?error=keycloak_unreachable")

    if not token_response.ok:
        return HttpResponseRedirect(f"{frontend_callback}?error=token_exchange_failed")

    token_data    = token_response.json()
    access_token  = token_data.get('access_token')
    refresh_token = token_data.get('refresh_token', '')

    # Validate the access token (RS256 via Keycloak public key)
    try:
        _decode_token(access_token)
    except JWTError:
        return HttpResponseRedirect(f"{frontend_callback}?error=invalid_token")

    # Send user to Raven frontend with the token
    params = urlencode({'token': access_token, 'refresh_token': refresh_token})
    return HttpResponseRedirect(f"{frontend_callback}?{params}")


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