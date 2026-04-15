from django.urls import path

from energy_account.views.overview.overview import ea_overview
from energy_account.views.states.all_states import all_states, single_state
from energy_account.views.districts.all_districts import all_districts, single_district
from energy_account.views.stations.all_stations import all_stations, single_station
from energy_account.views.feeders.all_feeders import all_feeders, single_feeder
from energy_account.views.compare.ea_compare import station_vs_feeders, kv33_vs_kv11, full_alignment

urlpatterns = [
    # Overview — full system
    path('overview/', ea_overview, name='ea-overview'),

    # State level
    path('states/', all_states, name='ea-states'),
    path('states/<slug:slug>/', single_state, name='ea-state-detail'),

    # Business district level
    path('districts/', all_districts, name='ea-districts'),
    path('districts/<slug:slug>/', single_district, name='ea-district-detail'),

    # Injection substation (station) level
    path('stations/', all_stations, name='ea-stations'),
    path('stations/<slug:slug>/', single_station, name='ea-station-detail'),

    # Feeder level
    path('feeders/', all_feeders, name='ea-feeders'),
    path('feeders/<slug:slug>/', single_feeder, name='ea-feeder-detail'),

    # ── Phase 3: Compare Engine ───────────────────────────────────────────────
    # Structural EA comparisons — live under the compare engine namespace.
    # Generic station-vs-station or feeder-vs-feeder comparisons go through
    # /api/analytics/compare/ with entity_type="station".

    # Transmission meter (Stream A) vs each 33kV feeder (Stream B) for one station
    path('compare/station-vs-feeders/<slug:slug>/', station_vs_feeders, name='ea-compare-station-vs-feeders'),

    # 33kV feeder energy tier vs 11kV feeder energy tier
    # Optional ?station=, ?state=, ?district= filters
    path('compare/kv33-vs-kv11/', kv33_vs_kv11, name='ea-compare-kv33-vs-kv11'),

    # Full Transformer → 33kV → 11kV cascade alignment with all loss layers
    # Optional ?station=, ?state=, ?district= filters
    path('compare/full-alignment/', full_alignment, name='ea-compare-full-alignment'),
]
