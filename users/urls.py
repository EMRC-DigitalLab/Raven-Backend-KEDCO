# users/urls.py
from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r'users', views.UserViewSet, basename='users')
router.register(r'sessions', views.UserSessionViewSet, basename='sessions')
router.register(r'role-permissions', views.RolePermissionViewSet, basename='role-permissions')

urlpatterns = [
    # JWT Authentication
    path('login/', views.login_view, name='login'),
    path('token/', views.CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', views.CustomTokenRefreshView.as_view(), name='token_refresh'),
    path('logout/', views.logout_view, name='logout'),
    path('current-user/', views.current_user, name='current_user'),
    path('me/', views.MyProfileView.as_view(), name='my_profile'),

    # Raven → DataNest handoff
    path('sso/handoff/', views.sso_handoff, name='sso_handoff'),  # Raven frontend calls this
    path('sso/redeem/',  views.sso_redeem,  name='sso_redeem'),   # DataNest frontend calls this

    # SSO token verify / current SSO user (accepts Keycloak Bearer token)
    path('sso/me/',     views.sso_me, name='sso_me'),
    path('sso/verify/', views.sso_me, name='sso_verify'),

    # DataNest → Raven SSO handoff
    # Frontend calls POST /api/auth/sso/exchange with {"code": "<one-time-code>"}
    path('sso/exchange/', views.sso_exchange, name='sso_exchange'),

    # Sections and permissions
    path('sections/', views.SectionListView.as_view(), name='section_list'),
    path('permissions/', views.PermissionListView.as_view(), name='permission_list'),

    # Access management (per-user grants — distinct from the role-permissions matrix above)
    path('user-access/', views.UserSectionAccessListCreateView.as_view(), name='user_access'),
    path('temporary-access/', views.TemporaryAccessListCreateView.as_view(), name='temporary_access'),
    path('revoke-access/<int:user_id>/<int:section_id>/', views.revoke_access, name='revoke_access'),
    path('revoke-temporary-access/<int:temp_access_id>/', views.revoke_temporary_access, name='revoke_temporary_access'),

    # User management, sessions, role-permissions matrix (router-based ViewSets)
    # -> users/, users/{id}/, users/bulk_status/, users/bulk_delete/
    # -> sessions/, sessions/{id}/force_logout/, sessions/block_user/
    # -> role-permissions/, role-permissions/{id}/, role-permissions/matrix/
] + router.urls
