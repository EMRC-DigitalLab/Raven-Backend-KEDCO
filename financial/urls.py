# financial/urls.py
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views.collections.monthly_views import DailyCollectionsByMonthView
from .views.crud import (
    GLBreakdownViewSet,
    HQOpexViewSet,
    OpexCategoryViewSet,
    OpexViewSet,
    SalaryPaymentViewSet,
)
from .views.districts.all_districts import FinancialAllBusinessDistrictsView
from .views.feeders.all_feeders import financial_feeder_view
from .views.overview.overview_views import financial_overview_view
from .views.sales_reps.all_sales_reps import list_sales_reps
from .views.sales_reps.single_sales_rep import sales_rep_performance_view
from .views.service_bands.service_band_views import FinancialServiceBandMetricsView
from .views.states.all_states import FinancialAllStatesView
from .views.transformers.transformer_views import financial_transformer_view

router = DefaultRouter()
router.register(r'expense-categories', OpexCategoryViewSet, basename='expense-category')
router.register(r'expenses', OpexViewSet, basename='expense')
router.register(r'gl-breakdowns', GLBreakdownViewSet, basename='gl-breakdown')
router.register(r"salary-payments", SalaryPaymentViewSet)
router.register(r'hq-expenses', HQOpexViewSet, basename='hq-expense')

urlpatterns = [
    path('', include(router.urls)),

    # Overview
    path('overview/', financial_overview_view, name='financial-overview'),

    path('collections/monthly/', DailyCollectionsByMonthView.as_view(), name="daily-collections"),

    # State Metrics
    path('states/all/', FinancialAllStatesView.as_view(), name="financial-all-states"),

    # District Metrics
    path('business-districts/all/', FinancialAllBusinessDistrictsView.as_view(), name="financial-business-districts"),

    # # Feeders
    path('feeders/all/', financial_feeder_view),

    # Service Bands
    path('service-bands/', FinancialServiceBandMetricsView.as_view(), name="service-band-financial"),

    # Transformer Metrics
    path('transformers/', financial_transformer_view, name='financial-transformer-metrics'),

    # Sales Reps
    path('sales-reps/', list_sales_reps),
    path("sales-reps/<uuid:rep_id>/", sales_rep_performance_view),
    
]
