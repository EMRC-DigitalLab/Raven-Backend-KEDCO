# analytics/urls.py
from django.urls import path
from .views import OptimizedOverviewAPIView, OverviewHealthAPIView, OptimizedTechnicalOverviewAPIView, TechnicalHealthAPIView, technical_overview_legacy_view

urlpatterns = [
        # Overview endpoints
    path('overview/', OptimizedOverviewAPIView.as_view(), name='overview'),
    path('overview/health/', OverviewHealthAPIView.as_view(), name='overview-health'),
    
    # Technical endpoints
    path('technical/', OptimizedTechnicalOverviewAPIView.as_view(), name='technical-overview'),
    path('technical/health/', TechnicalHealthAPIView.as_view(), name='technical-health'),
    path('technical/legacy/', technical_overview_legacy_view, name='technical-legacy'),
]

