# common/urls.py
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import OverviewAPIView

router = DefaultRouter()

urlpatterns = [
    path('', include(router.urls)),
    path('overview/', OverviewAPIView.as_view(), name='overview'),
]
