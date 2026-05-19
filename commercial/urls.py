from django.urls import path

from commercial.views.overview.overview_views import commercial_overview
from commercial.views.states.all_states import all_states, single_state
from commercial.views.districts.all_districts import all_districts, single_district
from commercial.views.feeders.all_feeders import all_feeders, single_feeder
from commercial.views.customers.customer_views import customer_list, customer_detail, top_customers
from commercial.views.service_bands.service_band_views import all_bands, single_band
from commercial.views.trend.trend_views import commercial_trend
from commercial.views.sync_status import commercial_sync_status
from commercial.views.sync_backfill import trigger_backfill, backfill_status

urlpatterns = [
    path('sync-status/', commercial_sync_status, name='commercial-sync-status'),
    path('sync/backfill/', trigger_backfill, name='commercial-sync-backfill-trigger'),
    path('sync/backfill/<str:job_id>/', backfill_status, name='commercial-sync-backfill-status'),
    path('overview/', commercial_overview, name='commercial-overview'),
    path('trend/', commercial_trend, name='commercial-trend'),
    path('states/', all_states, name='commercial-states'),
    path('states/<slug:slug>/', single_state, name='commercial-state-detail'),
    path('districts/', all_districts, name='commercial-districts'),
    path('districts/<slug:slug>/', single_district, name='commercial-district-detail'),
    path('feeders/', all_feeders, name='commercial-feeders'),
    path('feeders/<slug:slug>/', single_feeder, name='commercial-feeder-detail'),
    path('customers/', customer_list, name='commercial-customers'),
    path('customers/top/', top_customers, name='commercial-customers-top'),
    path('customers/<int:pk>/', customer_detail, name='commercial-customer-detail'),
    path('bands/', all_bands, name='commercial-bands'),
    path('bands/<slug:slug>/', single_band, name='commercial-band-detail'),
]
