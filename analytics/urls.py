# analytics/urls.py
from django.urls import path

from analytics.views.summary.general import (
    OptimizedOverviewAPIView,
    OverviewHealthAPIView,
)
from analytics.views.summary.technical.overview import (
    OptimizedTechnicalOverviewAPIView,
    TechnicalHealthAPIView,
)

urlpatterns = [
    path('summary/general/', OptimizedOverviewAPIView.as_view(), name='overview'),
    path('summary/general/health/', OverviewHealthAPIView.as_view(), name='overview-health'),

    path('summary/technical/overview/', OptimizedTechnicalOverviewAPIView.as_view(), name='technical-overview'),
    path('summary/technical/health/', TechnicalHealthAPIView.as_view(), name='technical-health'),

]

