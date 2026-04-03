# hr/urls.py

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views.crud import DepartmentViewSet, RoleViewSet, StaffViewSet

# Import from the correct subdirectories
from .views.executive_kpis.overview_views import executive_kpi_overview, kpi_alerts
from .views.executive_kpis.performance_views import (
    bulk_update_performance,
    delete_kpi_performance,
    kpi_performance_history,
    update_kpi_performance,
)
from .views.executive_kpis.role_views import cco_kpis, cfo_kpis, chro_kpis, cto_kpis
from .views.dso_compliance.views import (
    dso_compliance_bands,
    dso_compliance_districts,
    dso_compliance_feeders,
    dso_compliance_overview,
    dso_compliance_states,
)
from .views.overview.overview_views import StaffSummaryView

router = DefaultRouter()
router.register(r'departments', DepartmentViewSet, basename='hr-department')
router.register(r'roles', RoleViewSet, basename='hr-role')
router.register(r'staff', StaffViewSet, basename='hr-staff')

urlpatterns = [
    path('', include(router.urls)),

    # Original Overview
    path('overview/', StaffSummaryView.as_view(), name='staff-summary'),

    # Executive KPI Overview & Alerts
    path('executive-kpis/overview/', executive_kpi_overview, name='executive-kpi-overview'),
    path('executive-kpis/alerts/', kpi_alerts, name='kpi-alerts'),
    
    # Role-specific KPI endpoints (matching your frontend component structure)
    path('executive-kpis/cto/', cto_kpis, name='cto-kpis'),
    path('executive-kpis/cco/', cco_kpis, name='cco-kpis'),
    path('executive-kpis/cfo/', cfo_kpis, name='cfo-kpis'),
    path('executive-kpis/chro/', chro_kpis, name='chro-kpis'),
    
    # KPI Performance Management endpoints
    path('executive-kpis/performance/update/', update_kpi_performance, name='update-kpi-performance'),
    path('executive-kpis/performance/bulk-update/', bulk_update_performance, name='bulk-update-kpi-performance'),
    path('executive-kpis/performance/<uuid:kpi_id>/history/', kpi_performance_history, name='kpi-performance-history'),
    path('executive-kpis/performance/<uuid:performance_id>/delete/', delete_kpi_performance, name='delete-kpi-performance'),

    # DSO Submission Compliance
    path('dso-compliance/overview/',   dso_compliance_overview,   name='dso-compliance-overview'),
    path('dso-compliance/states/',     dso_compliance_states,     name='dso-compliance-states'),
    path('dso-compliance/districts/',  dso_compliance_districts,  name='dso-compliance-districts'),
    path('dso-compliance/feeders/',    dso_compliance_feeders,    name='dso-compliance-feeders'),
    path('dso-compliance/bands/',      dso_compliance_bands,      name='dso-compliance-bands'),
]