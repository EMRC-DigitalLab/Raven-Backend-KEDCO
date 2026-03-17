# users/urls.py
from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

urlpatterns = [
    # JWT Authentication
    path('login/', views.login_view, name='login'),
    path('token/', views.CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('logout/', views.logout_view, name='logout'),
    path('current-user/', views.current_user, name='current_user'),

    # Raven → DataNest handoff
    path('sso/handoff/', views.sso_handoff, name='sso_handoff'),  # Raven frontend calls this
    path('sso/redeem/',  views.sso_redeem,  name='sso_redeem'),   # DataNest frontend calls this

    # SSO token verify / current SSO user (accepts Keycloak Bearer token)
    path('sso/me/',     views.sso_me, name='sso_me'),
    path('sso/verify/', views.sso_me, name='sso_verify'),

    # DataNest → Raven SSO handoff
    # Frontend calls POST /api/auth/sso/exchange with {"code": "<one-time-code>"}
    path('sso/exchange/', views.sso_exchange, name='sso_exchange'),
    
    # User management
    path('users/', views.UserListCreateView.as_view(), name='user_list_create'),
    path('users/<int:pk>/', views.UserRetrieveUpdateDestroyView.as_view(), name='user_detail'),
    
    # Sections and permissions
    path('sections/', views.SectionListView.as_view(), name='section_list'),
    path('permissions/', views.PermissionListView.as_view(), name='permission_list'),
    
    # Access management
    path('user-access/', views.UserSectionAccessListCreateView.as_view(), name='user_access'),
    path('temporary-access/', views.TemporaryAccessListCreateView.as_view(), name='temporary_access'),
    path('revoke-access/<int:user_id>/<int:section_id>/', views.revoke_access, name='revoke_access'),
    path('revoke-temporary-access/<int:temp_access_id>/', views.revoke_temporary_access, name='revoke_temporary_access'),
]