from django.urls import path

from .views import BenchmarkView, MethodologyView, SimulationRunDetailView, SimulationRunListView, SimulationRunView

app_name = 'simulator'

urlpatterns = [
    path('methodology/', MethodologyView.as_view(), name='methodology'),
    path('benchmarks/', BenchmarkView.as_view(), name='benchmarks'),
    path('run/', SimulationRunView.as_view(), name='run'),
    path('runs/', SimulationRunListView.as_view(), name='run-list'),
    path('runs/<uuid:pk>/', SimulationRunDetailView.as_view(), name='run-detail'),
]
