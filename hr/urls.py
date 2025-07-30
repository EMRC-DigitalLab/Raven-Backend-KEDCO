from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views.crud import (DepartmentViewSet,
                         RoleViewSet,
                         StaffViewSet)
from .views.overview.overview_views import StaffSummaryView


router = DefaultRouter()
router.register(r'departments', DepartmentViewSet, basename='hr-department')
router.register(r'roles', RoleViewSet, basename='hr-role')
router.register(r'staff', StaffViewSet, basename='hr-staff')


urlpatterns = [
    path('', include(router.urls)),

    # Overview
    path('overview/', StaffSummaryView.as_view(), name='staff-summary'),
    
]
