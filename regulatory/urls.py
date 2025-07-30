from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (MonthlyEnergyOfftakeViewSet,
                    MonthlyRevenueRecoveryViewSet,
                    MonthlyUSoASubmissionViewSet,
                    MonthlyAPIStreamingRateViewSet,
                    MonthlyEstimatedBillingCappingViewSet,
                    MonthlyForumDecisionComplianceViewSet,
                    MonthlyNERCComplaintResolutionViewSet)


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
]
