# technical/urls.py
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views.crud import (
    DailyHoursOfSupplyViewSet,
    EnergyDeliveredViewSet,
    FeederInterruptionViewSet,
    HourlyLoadViewSet,
)
from .views.districts.all_districts import all_business_districts_technical_summary
from .views.districts.single_district import business_district_technical_summary
from .views.feeders.all_feeders import FeederAvailabilityOverview
from .views.overview.overview_views import technical_overview_view
from .views.service_bands.service_band_views import technical_service_band_summary
from .views.states.all_states import all_states_technical_summary
from .views.states.single_state import state_technical_summary
from .views.transformers.transformer_views import TransformerAvailabilityOverview
from .views.sync_status import technical_sync_status
from .views.sync_backfill import trigger_backfill, backfill_status
from .views.fault_analytics import (
    CTOTCNInterruptionsView,
    CTOFeederComplianceView,
    CTOPeakLoadView,
    CTOFRIRankingsView,
    CTORiskDistributionView,
    CTOPenaltyDriversView,
    CTOChronicFaultFeedersView,
    CTOMonthlySummaryView,
)

router = DefaultRouter()

router.register(r'energy-delivered', EnergyDeliveredViewSet, basename='energy-delivered')
router.register(r'hourly-load', HourlyLoadViewSet, basename='hourly-load')
router.register(r'feeder-interruptions', FeederInterruptionViewSet, basename='interruption')
router.register(r'hours-of-supply', DailyHoursOfSupplyViewSet, basename='hours-of-supply')

urlpatterns = [
    path('', include(router.urls)),

    # Overview
    path('overview/', technical_overview_view, name='technical-overview'),

    # State Metrics
    path('states/all/', all_states_technical_summary, name='all-states-technical-summary'),
    path('states/single/', state_technical_summary, name='state-technical-summary'),

    # District Metrics
    path('business-districts/all/', all_business_districts_technical_summary, name='business-districts-technical-summary'),
    path('business-districts/single/',  business_district_technical_summary, name='business-district-technical-summary'),

    # Feeders
    path('feeders/all/', FeederAvailabilityOverview.as_view(), name='feeder-availability-overview'),

    # Service Bands
    path('service-bands/', technical_service_band_summary, name='service-band-technical-metrics'),

    # Transformer Metrics
    path('transformers/', TransformerAvailabilityOverview.as_view(), name="transformer-availability"),

    # DataNest Sync Status
    path('sync-status/', technical_sync_status, name='technical-sync-status'),

    # DataNest Backfill (trigger from frontend)
    path('sync/backfill/', trigger_backfill, name='technical-sync-backfill-trigger'),
    path('sync/backfill/<str:job_id>/', backfill_status, name='technical-sync-backfill-status'),

    # CTO Dashboard — fault analytics (FRI engine + TCN interruption breakdowns)
    path('cto/tcn-interruptions/', CTOTCNInterruptionsView.as_view(), name='cto-tcn-interruptions'),
    path('cto/feeder-compliance/', CTOFeederComplianceView.as_view(), name='cto-feeder-compliance'),
    path('cto/peak-load/', CTOPeakLoadView.as_view(), name='cto-peak-load'),
    path('cto/fri-rankings/', CTOFRIRankingsView.as_view(), name='cto-fri-rankings'),
    path('cto/risk-distribution/', CTORiskDistributionView.as_view(), name='cto-risk-distribution'),
    path('cto/penalty-drivers/', CTOPenaltyDriversView.as_view(), name='cto-penalty-drivers'),
    path('cto/chronic-fault-feeders/', CTOChronicFaultFeedersView.as_view(), name='cto-chronic-fault-feeders'),
    path('cto/monthly-summary/', CTOMonthlySummaryView.as_view(), name='cto-monthly-summary'),
]
