from django.urls import path

from .views import (
    TMOBillingEfficiencyView,
    TMOCollectionView,
    TMOComplianceSummaryView,
    TMODailyAllocationView,
    TMODailyEnergyBySegmentView,
    TMODailyEnergyView,
    TMOEnergyBySegmentView,
    TMOEnergyByVoltageView,
    TMOFeederDetailView,
    TMOFeederDispatchView,
    TMOFeedersView,
    TMOGCRView,
    TMOIncidentsView,
    TMOMonitoredFeedersView,
    TMOMinigridsSSFView,
    TMOMinigridsView,
    TMONetworkConfigView,
    TMONetworkDispatchHourlyView,
    TMONetworkDispatchView,
    TMOOverviewView,
    TMOPEARView,
    TMOPnLTargetsView,
    TMOSegmentTargetsView,
    TMOSupplyComplianceView,
    TMOSupplyHoursTargetView,
    TMOVolatilityView,
)

app_name = 'tmo'

urlpatterns = [
    # Dashboard overview
    path('overview/',            TMOOverviewView.as_view(),         name='overview'),

    # Energy
    path('allocation/daily/',            TMODailyAllocationView.as_view(),        name='allocation-daily'),
    path('energy/daily/',                TMODailyEnergyView.as_view(),            name='energy-daily'),
    path('energy/daily/by-segment/',     TMODailyEnergyBySegmentView.as_view(),   name='energy-daily-by-segment'),
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
    path('minigrids/daily/',     TMOMinigridsSSFView.as_view(),     name='minigrids-daily'),

    # P&L Mix Volatility Index
    path('volatility/',          TMOVolatilityView.as_view(),        name='volatility'),

    # Network dispatch (33KV allocation vs DISCO offtake)
    path('network/dispatch/',         TMONetworkDispatchView.as_view(),        name='network-dispatch'),
    path('network/dispatch/hourly/',  TMONetworkDispatchHourlyView.as_view(),  name='network-dispatch-hourly'),

    # GCR & Incidents
    path('gcr/',                 TMOGCRView.as_view(),               name='gcr'),
    path('incidents/',           TMOIncidentsView.as_view(),         name='incidents'),

    # Settings — team fills these from the app each month
    path('settings/segment-targets/', TMOSegmentTargetsView.as_view(),   name='settings-segment-targets'),
    path('settings/network-config/',  TMONetworkConfigView.as_view(),    name='settings-network-config'),
    path('settings/supply-hours/',    TMOSupplyHoursTargetView.as_view(), name='settings-supply-hours'),

    # Feeder list + detail
    path('feeders/',                      TMOFeedersView.as_view(),          name='feeders'),
    path('feeders/monitored/',            TMOMonitoredFeedersView.as_view(), name='feeders-monitored'),
    path('feeders/<slug:feeder_slug>/',   TMOFeederDetailView.as_view(),     name='feeder-detail'),
]
