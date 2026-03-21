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
from .views.compliance.bands import compliance_band_detail, compliance_bands_list
from .views.compliance.districts import compliance_district_detail, compliance_districts_list
from .views.compliance.feeders import compliance_feeder_detail, compliance_feeders_list
from .views.compliance.overview import compliance_overview
from .views.compliance.states import compliance_state_detail, compliance_states_list
from .views.feeders.all_feeders import FeederAvailabilityOverview
from .views.overview.overview_views import technical_overview_view
from .views.service_bands.service_band_views import technical_service_band_summary
from .views.states.all_states import all_states_technical_summary
from .views.states.single_state import state_technical_summary
from .views.transformers.transformer_views import TransformerAvailabilityOverview

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

    # # Feeders
    path('feeders/all/', FeederAvailabilityOverview.as_view(), name='feeder-availability-overview'),

    # Service Level Compliance (NERC KPI) — full drill-down
    path('compliance/overview/', compliance_overview, name='compliance-overview'),
    path('compliance/states/', compliance_states_list, name='compliance-states-list'),
    path('compliance/states/<slug:slug>/', compliance_state_detail, name='compliance-state-detail'),
    path('compliance/districts/', compliance_districts_list, name='compliance-districts-list'),
    path('compliance/districts/<slug:slug>/', compliance_district_detail, name='compliance-district-detail'),
    path('compliance/bands/', compliance_bands_list, name='compliance-bands-list'),
    path('compliance/bands/<slug:slug>/', compliance_band_detail, name='compliance-band-detail'),
    path('compliance/feeders/', compliance_feeders_list, name='compliance-feeders-list'),
    path('compliance/feeders/<slug:slug>/', compliance_feeder_detail, name='compliance-feeder-detail'),

    # Service Bands
    path('service-bands/', technical_service_band_summary, name='service-band-technical-metrics'),

    # Transformer Metrics
    path('transformers/', TransformerAvailabilityOverview.as_view(), name="transformer-availability"),


    
]
