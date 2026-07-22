from django.urls import path

from .views import (
    TMOBillingEfficiencyView,
    TMOCollectionView,
    TMOEnergyBySegmentView,
    TMOFeederDetailView,
    TMOFeederDispatchView,
    TMOFeedersView,
    TMOMinigridsView,
    TMOOverviewView,
    TMOPnLTargetsView,
    TMOSupplyComplianceView,
)

app_name = 'tmo'

urlpatterns = [
    # Dashboard overview
    path('overview/',            TMOOverviewView.as_view(),         name='overview'),

    # Energy
    path('energy/dispatch/',     TMOFeederDispatchView.as_view(),   name='energy-dispatch'),
    path('energy/by-segment/',   TMOEnergyBySegmentView.as_view(),  name='energy-by-segment'),

    # Supply
    path('supply/compliance/',   TMOSupplyComplianceView.as_view(), name='supply-compliance'),

    # Commercial
    path('collection/',          TMOCollectionView.as_view(),       name='collection'),
    path('billing/',             TMOBillingEfficiencyView.as_view(), name='billing'),

    # P&L
    path('pnl/',                 TMOPnLTargetsView.as_view(),       name='pnl'),

    # Minigrids
    path('minigrids/',           TMOMinigridsView.as_view(),        name='minigrids'),

    # Feeder list + detail
    path('feeders/',             TMOFeedersView.as_view(),          name='feeders'),
    path('feeders/<slug:feeder_slug>/', TMOFeederDetailView.as_view(), name='feeder-detail'),
]
