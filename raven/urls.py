# raven/urls.py (main project urls.py)
"""
URL configuration for raven project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Import an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    # Authentication and user management
    path('api/auth/', include('users.urls')),
    path('api/users/', include('users.urls')),
    # Existing app URLs
    path('api/analytics/', include('analytics.urls')),
    path('api/common/', include('common.urls')),
    path('api/commercial/', include('commercial.urls')),
    path('api/financial/', include('financial.urls')),
    path('api/technical/', include('technical.urls')),
    path('api/hr/', include('hr.urls')),
    path('api/regulatory/', include('regulatory.urls')),
    path('api/reports/', include('reports.urls', namespace='reports')),
    path('api/notifications/', include('notifications.urls', namespace='notifications')),
    path('api/grid-view/', include('grid_view.urls', namespace='grid_view')),
    path('api/energy-account/', include('energy_account.urls')),
    path('api/aria/', include('aria.urls', namespace='aria')),
]
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])