# analytics/urls.py
from django.urls import path

from analytics.views.summary.general import (
    OptimizedOverviewAPIView,
    OverviewHealthAPIView,
)
from analytics.views.summary.technical.overview import (
    OptimizedTechnicalOverviewAPIView,
    TechnicalHealthAPIView,
)
from analytics.views.comparisons.compare_engine import (
    available_metrics,
    customer_compare,
    customer_search,
    run_compare,
)
from analytics.views.grid_lens.overview import grid_lens_overview
from analytics.views.grid_lens.states import all_states as gl_all_states, single_state as gl_single_state
from analytics.views.grid_lens.districts import all_districts as gl_all_districts, single_district as gl_single_district
from analytics.views.grid_lens.stations import all_stations as gl_all_stations, single_station as gl_single_station

urlpatterns = [
    path('summary/general/', OptimizedOverviewAPIView.as_view(), name='overview'),
    path('summary/general/health/', OverviewHealthAPIView.as_view(), name='overview-health'),

    path('summary/technical/overview/', OptimizedTechnicalOverviewAPIView.as_view(), name='technical-overview'),
    path('summary/technical/health/', TechnicalHealthAPIView.as_view(), name='technical-health'),

    # ── Compare Engine ────────────────────────────────────────────────────────
    path('compare/', run_compare, name='compare'),
    path('compare/available/', available_metrics, name='compare-available'),
    path('compare/customers/', customer_compare, name='compare-customers'),
    path('compare/customers/search/', customer_search, name='compare-customers-search'),

    # ── GridLens — Loss Decomposition ─────────────────────────────────────────
    path('grid-lens/',                          grid_lens_overview,  name='grid-lens-overview'),
    path('grid-lens/states/',                   gl_all_states,       name='grid-lens-states'),
    path('grid-lens/states/<slug:slug>/',       gl_single_state,     name='grid-lens-single-state'),
    path('grid-lens/districts/',                gl_all_districts,    name='grid-lens-districts'),
    path('grid-lens/districts/<slug:slug>/',    gl_single_district,  name='grid-lens-single-district'),
    path('grid-lens/stations/',                 gl_all_stations,     name='grid-lens-stations'),
    path('grid-lens/stations/<slug:slug>/',     gl_single_station,   name='grid-lens-single-station'),
]

