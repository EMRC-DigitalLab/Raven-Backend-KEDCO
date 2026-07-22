from django.urls import path

from .views import (
    TMOBillingEfficiencyView,
    TMOCollectionView,
    TMOComplianceSummaryView,
    TMODailyEnergyView,
    TMOEnergyBySegmentView,
    TMOEnergyByVoltageView,
    TMOFeederDetailView,
    TMOFeederDispatchView,
    TMOFeedersView,
    TMOGCRView,
    TMOIncidentsView,
    TMOMinigridsView,
    TMOOverviewView,
    TMOPEARView,
    TMOPnLTargetsView,
    TMOSupplyComplianceView,
    TMOVolatilityView,
)

app_name = 'tmo'

urlpatterns = [
    # Dashboard overview
    path('overview/',            TMOOverviewView.as_view(),         name='overview'),

    # Energy
    path('energy/daily/',        TMODailyEnergyView.as_view(),      name='energy-daily'),
    path('energy/dispatch/',     TMOFeederDispatchView.as_view(),    name='energy-dispatch'),
    path('energy/by-segment/',   TMOEnergyBySegmentView.as_view(),   name='energy-by-segment'),
    path('energy/by-voltage/',   TMOEnergyByVoltageView.as_view(),   name='energy-by-voltage'),

    # PEAR
    path('pear/',                TMOPEARView.as_view(),              name='pear'),

    # Supply
    path('supply/compliance/',        TMOSupplyComplianceView.as_view(),   name='supply-compliance'),
    path('supply/compliance/summary/', TMOComplianceSummaryView.as_view(), name='compliance-summary'),

    # Commercial
    path('collection/',          TMOCollectionView.as_view(),       name='collection'),
    path('billing/',             TMOBillingEfficiencyView.as_view(), name='billing'),

    # P&L
    path('pnl/',                 TMOPnLTargetsView.as_view(),       name='pnl'),

    # Minigrids
    path('minigrids/',           TMOMinigridsView.as_view(),        name='minigrids'),

    # P&L Mix Volatility Index
    path('volatility/',          TMOVolatilityView.as_view(),        name='volatility'),

    # GCR & Incidents
    path('gcr/',                 TMOGCRView.as_view(),               name='gcr'),
    path('incidents/',           TMOIncidentsView.as_view(),         name='incidents'),

    # Feeder list + detail
    path('feeders/',             TMOFeedersView.as_view(),          name='feeders'),
    path('feeders/<slug:feeder_slug>/', TMOFeederDetailView.as_view(), name='feeder-detail'),
]
