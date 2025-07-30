from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views.crud import (CustomerViewSet,
                         SalesRepresentativeViewSet,
                         SalesRepPerformanceViewSet,
                         DailyEnergyDeliveredViewSet,
                         MonthlyRevenueBilledViewSet,
                         DailyCollectionViewSet,
                         MonthlyEnergyBilledViewSet,
                         MonthlyCustomerStatsViewSet)

from .views.states.all_states import commercial_all_states_view
from .views.states.single_state import commercial_state_metrics_view
from .views.overview.overview_views import CommercialOverviewAPIView
from .views.districts.all_districts import commercial_all_business_districts_view
from .views.districts.single_district import CustomerBusinessMetricsView
from .views.feeders.all_feeders import feeders_by_location_view
from .views.feeders.top_bottom import feeder_performance_view
from .views.transformers.transformer_views import transformer_metrics_by_feeder_view
from .views.service_bands.service_band_views import ServiceBandMetricsView

router = DefaultRouter()
router.register(r'sales-reps', SalesRepresentativeViewSet, basename='sales-representative')
router.register(r'sales-rep-performance', SalesRepPerformanceViewSet, basename='sales-rep-performance')
router.register(r'collections', DailyCollectionViewSet, basename='daily-collection')
router.register(r'customers', CustomerViewSet, basename='customer')
router.register(r'daily-energy-delivered', DailyEnergyDeliveredViewSet, basename='daily-energy-delivered')
router.register(r'monthly-revenue-billed', MonthlyRevenueBilledViewSet, basename='monthly-revenue-billed')
router.register(r'daily-collections', DailyCollectionViewSet, basename='daily-collections')
router.register(r'monthly-energy-billed', MonthlyEnergyBilledViewSet, basename='monthly-energy-billed')
router.register(r'monthly-customer-stats', MonthlyCustomerStatsViewSet, basename='monthly-customer-stats')

urlpatterns = [
    path('', include(router.urls)),

    # Overview
    path('overview/', CommercialOverviewAPIView.as_view(), name='commercial-overview'),

    # State Metrics
    path('states/all/', commercial_all_states_view, name='commercial-all-states-view'),
    path('states/single/', commercial_state_metrics_view, name='commercial-state-view'),

    # District Metrics
    path('business-districts/all/', commercial_all_business_districts_view, name='commercial-business-districts-view'),
    path('business-districts/single/', CustomerBusinessMetricsView.as_view(), name="customer-business-metrics"),

    # # Feeders
    path('feeders/all/', feeders_by_location_view, name="feeders-by-location"),
    path('feeders/top-bottom/', feeder_performance_view, name="feeder-performance"),

    # Service Bands
    path('service-bands/', ServiceBandMetricsView.as_view(), name="service-band-metrics"),

    # Transformer Metrics
    path('transformers/', transformer_metrics_by_feeder_view),


    
]
