# regulatory/urls.py
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    MonthlyAPIStreamingRateViewSet,
    MonthlyEnergyOfftakeViewSet,
    MonthlyEstimatedBillingCappingViewSet,
    MonthlyForumDecisionComplianceViewSet,
    MonthlyNERCComplaintResolutionViewSet,
    MonthlyRevenueRecoveryViewSet,
    MonthlyUSoASubmissionViewSet,
)
from .nerc_views import (
    api_feeder_streaming,
    capping_estimated_bills,
    revenue_recovery,
)

router = DefaultRouter()
router.register(r'regulatory/energy-offtake', MonthlyEnergyOfftakeViewSet, basename='reg-energy-offtake')
router.register(r'regulatory/revenue-recovery', MonthlyRevenueRecoveryViewSet, basename='reg-revenue-recovery')
router.register(r'regulatory/usoa-submission', MonthlyUSoASubmissionViewSet, basename='reg-usoa')
router.register(r'regulatory/api-streaming', MonthlyAPIStreamingRateViewSet, basename='reg-api-streaming')
router.register(r'regulatory/estimated-capping', MonthlyEstimatedBillingCappingViewSet, basename='reg-capping')
router.register(r'regulatory/forum-compliance', MonthlyForumDecisionComplianceViewSet, basename='reg-forum')
router.register(r'regulatory/complaints-resolution', MonthlyNERCComplaintResolutionViewSet, basename='reg-complaints')


urlpatterns = [
    path('', include(router.urls)),
    # Computed NERC endpoints — derived live from Raven DB
    path('nerc/capping-estimated-bills/', capping_estimated_bills, name='nerc-capping'),
    path('nerc/revenue-recovery/',        revenue_recovery,         name='nerc-revenue-recovery'),
    path('nerc/api-feeder-streaming/',    api_feeder_streaming,     name='nerc-api-streaming'),
]
