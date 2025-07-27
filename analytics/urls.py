# analytics/urls.py
from django.urls import path
from .views import OptimizedOverviewAPIView, OverviewHealthAPIView

urlpatterns = [
    path('overview/', OptimizedOverviewAPIView.as_view(), name='overview'),
    path('overview/health/', OverviewHealthAPIView.as_view(), name='overview-health'),
]

