# users/views.py
import requests as http_client
from django_filters.rest_framework import DjangoFilterBackend

from django.conf import settings
from django.contrib.auth import login, logout
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, permissions, serializers, status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action, api_view, authentication_classes, permission_classes
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from middleware.sso_authenticate import _decode_token, _map_role

from .models import (
    AccessLog,
    Permission,
    RolePermission,
    Section,
    TemporaryAccess,
    User,
    UserSectionAccess,
    UserSession,
)
from .pagination import UserPagination
from .serializers import (
    CustomTokenObtainPairSerializer,
    LoginSerializer,
    MyProfileSerializer,
    PermissionSerializer,
    RolePermissionSerializer,
    SectionSerializer,
    TemporaryAccessSerializer,
    UserListSerializer,
    UserPermissionsSerializer,
    UserSectionAccessSerializer,
    UserSerializer,
    UserSessionSerializer,
)
from .utils import get_client_ip, parse_device_label


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


class CustomTokenRefreshView(TokenRefreshView):
    """Rotates the refresh token (SIMPLE_JWT.ROTATE_REFRESH_TOKENS=True) and
    carries the matching UserSession forward under its new jti, so one login
    stays one session row across the token's lifetime instead of spawning a
    fresh row every rotation."""

    def post(self, request, *args, **kwargs):
        old_jti = None
        old_refresh_str = request.data.get('refresh')
        if old_refresh_str:
            try:
                old_jti = RefreshToken(old_refresh_str)['jti']
            except Exception:
                old_jti = None

        response = super().post(request, *args, **kwargs)

        if response.status_code == 200 and old_jti:
            new_jti = old_jti
            new_refresh_str = response.data.get('refresh')
            if new_refresh_str:
                try:
                    new_jti = RefreshToken(new_refresh_str)['jti']
                except Exception:
                    new_jti = old_jti

            UserSession.objects.filter(jti=old_jti).update(
                jti=new_jti,
                last_seen_at=timezone.now(),
            )

        return response


class IsAdminOrManager(permissions.BasePermission):
    """Custom permission for admin or managers (system role, per-user grant, or role-level grant)"""

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.role in ['super_admin', 'admin']:
            return True
        if UserSectionAccess.objects.filter(user=request.user, is_manager=True, is_active=True).exists():
            return True
        return RolePermission.objects.filter(role=request.user.role, is_manager=True).exists()


class IsAdminOnly(permissions.BasePermission):
    """System-wide admin gate — used for actions with blast radius beyond one section
    (role-permission matrix, force-logout/block-user)."""

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ['super_admin', 'admin']


def _blacklist_jti(jti):
    """Blacklist a refresh token by its jti, independent of having the raw token string."""
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    try:
        outstanding = OutstandingToken.objects.get(jti=jti)
    except OutstandingToken.DoesNotExist:
        return
    BlacklistedToken.objects.get_or_create(token=outstanding)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def login_view(request):
    """Login endpoint that returns JWT tokens"""
    serializer = CustomTokenObtainPairSerializer(data=request.data, context={'request': request})
    try:
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def logout_view(request):
    """Logout endpoint - blacklists the refresh token and deactivates its session"""
    refresh_token = request.data.get('refresh')
    if not refresh_token:
        return Response({'message': 'Logged out successfully'})

    try:
        token = RefreshToken(refresh_token)
        jti = token['jti']
        token.blacklist()
    except TokenError:
        return Response({'error': 'Invalid or expired token'}, status=status.HTTP_400_BAD_REQUEST)

    UserSession.objects.filter(jti=jti).update(is_active=False, revoked_at=timezone.now())
    return Response({'message': 'Logged out successfully'})


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def current_user(request):
    """Get current user info with permissions - cached version"""
    # Add caching header to reduce redundant calls
    response = Response({
        'user': UserListSerializer(request.user, context={'request': request}).data,
        'permissions': UserPermissionsSerializer(request.user).data
    })
    # Cache for 5 minutes
    # response['Cache-Control'] = 'max-age=300'
    return response


class MyProfileView(generics.RetrieveUpdateAPIView):
    """Self-service 'my profile' — change your own name/phone/picture.

    GET  /api/users/me/   — full profile (including the admin-controlled read-only fields)
    PATCH /api/users/me/  — first_name, last_name, phone_number, profile_picture only

    Distinct from current-user/: that endpoint answers "who am I + what can I
    access" (used at app boot for permission gating); this one is the actual
    profile-editing screen.
    """
    serializer_class = MyProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class UserViewSet(viewsets.ModelViewSet):
    """Real search/filter/pagination + bulk actions over the user list.

    GET  /users/                search=, role=, is_active=, department=, ordering=, page=, page_size=
    PATCH /users/bulk_status/   {"user_ids": [...], "is_active": bool}
    DELETE /users/bulk_delete/  {"user_ids": [...]}
    """
    queryset = User.objects.all().order_by('-created_at')
    permission_classes = [IsAdminOrManager]
    pagination_class = UserPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['role', 'is_active', 'department']
    search_fields = ['username', 'email', 'first_name', 'last_name', 'employee_id']
    ordering_fields = ['created_at', 'username', 'last_name']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.request.method in ('POST', 'PUT', 'PATCH'):
            return UserSerializer
        return UserListSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def _guard_not_self(self, target_id, message):
        if int(target_id) == self.request.user.id:
            raise serializers.ValidationError(message)

    def perform_update(self, serializer):
        if serializer.validated_data.get('is_active') is False:
            self._guard_not_self(serializer.instance.id, 'You cannot deactivate your own account.')
        serializer.save()

    def perform_destroy(self, instance):
        self._guard_not_self(instance.id, 'You cannot delete your own account.')
        instance.delete()

    @action(detail=False, methods=['patch'])
    def bulk_status(self, request):
        user_ids = request.data.get('user_ids', [])
        is_active = request.data.get('is_active')
        if not user_ids or is_active is None:
            return Response({'error': 'user_ids and is_active are required'}, status=status.HTTP_400_BAD_REQUEST)

        updated, errors = [], []
        with transaction.atomic():
            for user_id in user_ids:
                if int(user_id) == request.user.id:
                    errors.append({'id': user_id, 'error': 'Cannot change your own active status'})
                    continue
                try:
                    user = User.objects.get(id=user_id)
                    user.is_active = is_active
                    user.save(update_fields=['is_active'])
                    updated.append(user.id)
                except User.DoesNotExist:
                    errors.append({'id': user_id, 'error': 'User not found'})

        response_data = {'updated': len(updated), 'errors': len(errors)}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['delete'])
    def bulk_delete(self, request):
        user_ids = request.data.get('user_ids', [])
        if not user_ids:
            return Response({'error': 'user_ids is required'}, status=status.HTTP_400_BAD_REQUEST)

        deleted, errors = 0, []
        with transaction.atomic():
            for user_id in user_ids:
                if int(user_id) == request.user.id:
                    errors.append({'id': user_id, 'error': 'Cannot delete your own account'})
                    continue
                try:
                    User.objects.get(id=user_id).delete()
                    deleted += 1
                except User.DoesNotExist:
                    errors.append({'id': user_id, 'error': 'User not found'})

        response_data = {'deleted': deleted, 'errors': len(errors)}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)


class UserSessionViewSet(viewsets.ReadOnlyModelViewSet):
    """Real Active Sessions backend, keyed off UserSession (see users/models.py).

    GET  /sessions/                       user=, search=
    POST /sessions/{id}/force_logout/     kill one session
    POST /sessions/block_user/            {"user_id": ...} — deactivate account + kill all its sessions
    """
    serializer_class = UserSessionSerializer
    permission_classes = [IsAdminOrManager]

    def get_queryset(self):
        queryset = UserSession.objects.filter(is_active=True).select_related('user').order_by('-last_seen_at')
        user_id = self.request.query_params.get('user')
        search = self.request.query_params.get('search')
        if user_id:
            queryset = queryset.filter(user_id=user_id)
        if search:
            queryset = queryset.filter(
                Q(user__username__icontains=search)
                | Q(user__email__icontains=search)
                | Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
            )
        return queryset

    @action(detail=True, methods=['post'], permission_classes=[IsAdminOnly])
    def force_logout(self, request, pk=None):
        session = self.get_object()
        _blacklist_jti(session.jti)
        session.is_active = False
        session.revoked_at = timezone.now()
        session.revoked_by = request.user
        session.save(update_fields=['is_active', 'revoked_at', 'revoked_by'])
        return Response({'message': 'Session logged out'})

    @action(detail=False, methods=['post'], permission_classes=[IsAdminOnly])
    def block_user(self, request):
        user_id = request.data.get('user_id')
        if not user_id:
            return Response({'error': 'user_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        if int(user_id) == request.user.id:
            return Response({'error': 'You cannot block your own account'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            target = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

        target.is_active = False
        target.save(update_fields=['is_active'])

        count = 0
        for session in UserSession.objects.filter(user=target, is_active=True):
            _blacklist_jti(session.jti)
            session.is_active = False
            session.revoked_at = timezone.now()
            session.revoked_by = request.user
            session.save(update_fields=['is_active', 'revoked_at', 'revoked_by'])
            count += 1

        return Response({'message': f'User blocked and {count} session(s) logged out'})


class RolePermissionViewSet(viewsets.ModelViewSet):
    """Role -> section permission matrix. Reads open to any authenticated user
    (the matrix screen needs to render); writes admin-only, since a role change
    here applies to every user with that role at once."""
    queryset = RolePermission.objects.select_related('section').prefetch_related('permissions')
    serializer_class = RolePermissionSerializer

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.IsAuthenticated()]
        return [IsAdminOnly()]

    def get_queryset(self):
        queryset = super().get_queryset()
        role = self.request.query_params.get('role')
        if role:
            queryset = queryset.filter(role=role)
        return queryset

    def perform_create(self, serializer):
        serializer.save(updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

    @action(detail=False, methods=['put'])
    def matrix(self, request):
        """Upsert the whole grid in one call: {"changes": [{"role", "section", "permission_ids", "is_manager"}, ...]}

        Admin-only via get_permissions() above (PUT is not a SAFE_METHOD)."""
        changes = request.data.get('changes', [])
        if not changes:
            return Response({'error': 'changes is required'}, status=status.HTTP_400_BAD_REQUEST)

        saved, errors = [], []
        with transaction.atomic():
            for idx, change in enumerate(changes):
                role = change.get('role')
                section_id = change.get('section')
                try:
                    section = Section.objects.get(pk=section_id)
                except (Section.DoesNotExist, TypeError, ValueError):
                    errors.append({'index': idx, 'error': f"Section '{section_id}' not found"})
                    continue

                instance, _ = RolePermission.objects.update_or_create(
                    role=role,
                    section=section,
                    defaults={
                        'is_manager': change.get('is_manager', False),
                        'updated_by': request.user,
                    },
                )
                instance.permissions.set(Permission.objects.filter(id__in=change.get('permission_ids', [])))
                saved.append(RolePermissionSerializer(instance).data)

        response_data = {'saved': len(saved), 'errors': len(errors), 'saved_data': saved}
        if errors:
            response_data['error_details'] = errors
        return Response(response_data, status=status.HTTP_200_OK)


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
@authentication_classes([])
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
@authentication_classes([])
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
@authentication_classes([])
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
@authentication_classes([])
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

    exchange_data  = datanest_response.json()
    token          = exchange_data.get('token')
    refresh_token  = exchange_data.get('refresh_token', '')
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
        'token':         token,
        'refresh_token': refresh_token,
        'user': {
            'id':               payload['sub'],
            'email':            payload.get('email'),
            'keycloak_role':    keycloak_role,
            'raven_role':       mapping['raven_role'],
            'allowed_sections': mapping['allowed_sections'],
            'full_access':      mapping['full_access'],
        },
    }, status=status.HTTP_200_OK)
