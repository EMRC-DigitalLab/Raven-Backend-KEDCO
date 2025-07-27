"""
URL configuration for raven project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from common.views import *
from commercial.views import (
    CustomerViewSet,
    DailyEnergyDeliveredViewSet,
    MonthlyRevenueBilledViewSet,
    MonthlyEnergyBilledViewSet,
    MonthlyCustomerStatsViewSet,
    FeederMetricsView,
    SalesRepresentativeViewSet,
    SalesRepPerformanceViewSet,
    SalesRepMetricsView,
    DailyCollectionViewSet,
    OverviewAPIView,

    CommercialOverviewAPIView,
    commercial_all_states_view,
    commercial_state_metrics_view,
    commercial_all_business_districts_view,
    feeder_metrics,
    feeder_performance_view,
    feeders_by_location_view,
    CustomerBusinessMetricsView,
    ServiceBandMetricsView,
    transformer_metrics_by_feeder_view
)

from technical.views import (
    EnergyDeliveredViewSet,
    HourlyLoadViewSet,
    FeederInterruptionViewSet,
    DailyHoursOfSupplyViewSet,
    TechnicalMetricsView,
    TechnicalMonthlySummaryView,
    technical_overview_view,
    all_states_technical_summary,
    state_technical_summary,
    all_business_districts_technical_summary,
    business_district_technical_summary,
    FeederAvailabilityOverview,
    service_band_technical_metrics,
    TransformerAvailabilityOverview
)

from financial.views import (
    OpexCategoryViewSet,
    OpexViewSet,
    HQOpexViewSet,
    MonthlyRevenueBilledViewSet,
    FinancialSummaryView,
    SalaryPaymentViewSet,
    financial_overview_view,
    financial_feeder_view,
    sales_rep_performance_view,
    list_sales_reps,
    FinancialAllStatesView,
    FinancialAllBusinessDistrictsView,
    FinancialServiceBandMetricsView,
    DailyCollectionsByMonthView,
    financial_transformer_view
)
from financial.views import GLBreakdownViewSet

from regulatory.views import (
    MonthlyEnergyOfftakeViewSet,
    MonthlyRevenueRecoveryViewSet,
    MonthlyUSoASubmissionViewSet,
    MonthlyAPIStreamingRateViewSet,
    MonthlyEstimatedBillingCappingViewSet,
    MonthlyForumDecisionComplianceViewSet,
    MonthlyNERCComplaintResolutionViewSet,
)

from hr.views import DepartmentViewSet, RoleViewSet, StaffViewSet
from hr.views import StaffSummaryView
import analytics






router = DefaultRouter()
router.register(r'states', StateViewSet)
router.register(r'districts', BusinessDistrictViewSet)
router.register(r'substations', InjectionSubstationViewSet)
router.register(r'feeders', FeederViewSet)
router.register(r'transformers', DistributionTransformerViewSet)
router.register(r'bands', BandViewSet)

router.register(r'customers', CustomerViewSet, basename='customer')
router.register(r'daily-energy-delivered', DailyEnergyDeliveredViewSet, basename='daily-energy-delivered')
router.register(r'monthly-revenue-billed', MonthlyRevenueBilledViewSet, basename='monthly-revenue-billed')
router.register(r'daily-collections', DailyCollectionViewSet, basename='daily-collections')
router.register(r'monthly-energy-billed', MonthlyEnergyBilledViewSet, basename='monthly-energy-billed')
router.register(r'monthly-customer-stats', MonthlyCustomerStatsViewSet, basename='monthly-customer-stats')


router.register(r'technical/energy-delivered', EnergyDeliveredViewSet, basename='energy-delivered')
router.register(r'technical/hourly-load', HourlyLoadViewSet, basename='hourly-load')
router.register(r'technical/feeder-interruptions', FeederInterruptionViewSet, basename='interruption')
router.register(r'technical/hours-of-supply', DailyHoursOfSupplyViewSet, basename='hours-of-supply')

# financial district opex point  
router.register(r'financial/expense-categories', OpexCategoryViewSet, basename='expense-category')
router.register(r'financial/expenses', OpexViewSet, basename='expense')
router.register(r'financial/gl-breakdowns', GLBreakdownViewSet, basename='gl-breakdown')
router.register(r"financial/salary-payments", SalaryPaymentViewSet)

# finanacial HQ opex endpoint 
router.register(r'financial/hq/expenses', HQOpexViewSet, basename='hq-expense')

router.register(r'commercial/sales-reps', SalesRepresentativeViewSet, basename='sales-representative')
router.register(r'commercial/sales-rep-performance', SalesRepPerformanceViewSet, basename='sales-rep-performance')
router.register(r'commercial/collections', DailyCollectionViewSet, basename='daily-collection')


router.register(r'regulatory/energy-offtake', MonthlyEnergyOfftakeViewSet, basename='reg-energy-offtake')
router.register(r'regulatory/revenue-recovery', MonthlyRevenueRecoveryViewSet, basename='reg-revenue-recovery')
router.register(r'regulatory/usoa-submission', MonthlyUSoASubmissionViewSet, basename='reg-usoa')
router.register(r'regulatory/api-streaming', MonthlyAPIStreamingRateViewSet, basename='reg-api-streaming')
router.register(r'regulatory/estimated-capping', MonthlyEstimatedBillingCappingViewSet, basename='reg-capping')
router.register(r'regulatory/forum-compliance', MonthlyForumDecisionComplianceViewSet, basename='reg-forum')
router.register(r'regulatory/complaints-resolution', MonthlyNERCComplaintResolutionViewSet, basename='reg-complaints')

router.register(r'hr/departments', DepartmentViewSet, basename='hr-department')
router.register(r'hr/roles', RoleViewSet, basename='hr-role')
router.register(r'hr/staff', StaffViewSet, basename='hr-staff')


urlpatterns = [
    path('api/', include('analytics.urls')),
    path('admin/', admin.site.urls),
    path('api/', include(router.urls)),






    
    # path('api/overview/', OverviewAPIView.as_view(), name='overview'),
    path('api/metrics/feeder/', FeederMetricsView.as_view(), name='feeder-metrics'),
    path('api/metrics/technical-summary/', TechnicalMetricsView.as_view(), name='technical-summary'),
    path('api/metrics/technical-monthly/', TechnicalMonthlySummaryView.as_view(), name='technical-monthly-summary'),
    path('api/metrics/financial-summary/', FinancialSummaryView.as_view(), name='financial-summary'),
    path('api/metrics/sales-rep-summary/', SalesRepMetricsView.as_view(), name='sales-rep-summary'),

    path('api/metrics/staff-summary/', StaffSummaryView.as_view(), name='staff-summary'),

    path('api/metrics/commercial/overview/', CommercialOverviewAPIView.as_view(), name='commercial-overview'),
    path('api/metrics/commercial/all-states/',commercial_all_states_view, name='commercial-all-states-view'),
    path('api/metrics/commercial/state/',commercial_state_metrics_view, name='commercial-state-view'),
    path('api/metrics/commercial/business-districts/',commercial_all_business_districts_view, name='commercial-business-districts-view'),
    path('api/metrics/commercial/feeders/metrics/', feeder_metrics, name='feeder-metrics'),
    path("api/metrics/commercial/business-metrics/", CustomerBusinessMetricsView.as_view(), name="customer-business-metrics"),
    path("api/metrics/commercial/service-band-metrics/", ServiceBandMetricsView.as_view(), name="service-band-metrics"),
    path("api/metrics/commercial/transformers-metrics/", transformer_metrics_by_feeder_view),





    path("api/metrics/feeders/performance/", feeder_performance_view, name="feeder-performance"),
    path("api/metrics/feeders/list/", feeders_by_location_view, name="feeders-by-location"),

    path('api/financial/overview/', financial_overview_view, name='financial-overview'),
    path("api/financial/feeder/", financial_feeder_view),
    path("api/financial/sales-reps/<uuid:rep_id>/performance/", sales_rep_performance_view),
    path("api/financial/sales-reps/", list_sales_reps),
    path("api/financial/all-states-metrics/", FinancialAllStatesView.as_view(), name="financial-all-states"),
    path("api/financial/all-business-districts-metrics/", FinancialAllBusinessDistrictsView.as_view(), name="financial-business-districts"),
    path("api/financial/service-band-financial-metrics/", FinancialServiceBandMetricsView.as_view(), name="service-band-financial"),
    path("api/financial/daily-collections/", DailyCollectionsByMonthView.as_view(), name="daily-collections"),
    path('api/financial/transformer-metrics/', financial_transformer_view, name='financial-transformer-metrics'),






    path('api/technical/overview/', technical_overview_view, name='technical-overview'),
    path('api/technical/overview/all-states/', all_states_technical_summary, name='all-states-technical-summary'),
    path('api/technical/overview/state/', state_technical_summary, name='state-technical-summary'),
    path('api/technical/overview/business-districts/', all_business_districts_technical_summary, name='business-districts-technical-summary'),
    path('api/technical/overview/business-district/', business_district_technical_summary, name='business-district-technical-summary'),
    path('api/technical/feeder/', FeederAvailabilityOverview.as_view(), name='feeder-availability-overview'),
    path("api/technical/transformer/", TransformerAvailabilityOverview.as_view(), name="transformer-availability"),
    path('api/technical/service-band-technical-metrics/', service_band_technical_metrics, name='service-band-technical-metrics'),



]




