from django.urls import path

from .views import GridViewAPIView

app_name = 'grid_view'

urlpatterns = [
    path('', GridViewAPIView.as_view(), name='grid-view'),
]
