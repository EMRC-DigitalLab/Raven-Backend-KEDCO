from rest_framework import viewsets

from commercial.date_filters import get_date_range_from_request
from commercial.utils import get_filtered_feeders

from .models import *
from .serializers import *


class BaseFeederMonthFilterMixin:
    def filter_queryset(self, qs):
        feeders = get_filtered_feeders(self.request)
        if hasattr(qs.model, 'feeder') and feeders.exists():
            qs = qs.filter(feeder__in=feeders)

        month_from, month_to = get_date_range_from_request(self.request, 'month')
        if month_from and month_to:
            qs = qs.filter(month__range=(month_from, month_to))
        elif month_from:
            qs = qs.filter(month__gte=month_from)
        elif month_to:
            qs = qs.filter(month__lte=month_to)
        return qs


class MonthlyEnergyOfftakeViewSet(BaseFeederMonthFilterMixin, viewsets.ModelViewSet):
    serializer_class = MonthlyEnergyOfftakeSerializer

    def get_queryset(self):
        return self.filter_queryset(MonthlyEnergyOfftake.objects.all())


class MonthlyRevenueRecoveryViewSet(BaseFeederMonthFilterMixin, viewsets.ModelViewSet):
    serializer_class = MonthlyRevenueRecoverySerializer

    def get_queryset(self):
        return self.filter_queryset(MonthlyRevenueRecovery.objects.all())


class MonthlyUSoASubmissionViewSet(viewsets.ModelViewSet):
    serializer_class = MonthlyUSoASubmissionSerializer

    def get_queryset(self):
        month_from, month_to = get_date_range_from_request(self.request, 'month')
        qs = MonthlyUSoASubmission.objects.all()
        if month_from and month_to:
            qs = qs.filter(month__range=(month_from, month_to))
        elif month_from:
            qs = qs.filter(month__gte=month_from)
        elif month_to:
            qs = qs.filter(month__lte=month_to)
        return qs


class MonthlyAPIStreamingRateViewSet(viewsets.ModelViewSet):
    serializer_class = MonthlyAPIStreamingRateSerializer

    def get_queryset(self):
        month_from, month_to = get_date_range_from_request(self.request, 'month')
        qs = MonthlyAPIStreamingRate.objects.all()
        if month_from and month_to:
            qs = qs.filter(month__range=(month_from, month_to))
        elif month_from:
            qs = qs.filter(month__gte=month_from)
        elif month_to:
            qs = qs.filter(month__lte=month_to)
        return qs


class MonthlyEstimatedBillingCappingViewSet(viewsets.ModelViewSet):
    serializer_class = MonthlyEstimatedBillingCappingSerializer

    def get_queryset(self):
        month_from, month_to = get_date_range_from_request(self.request, 'month')
        qs = MonthlyEstimatedBillingCapping.objects.all()
        if month_from and month_to:
            qs = qs.filter(month__range=(month_from, month_to))
        elif month_from:
            qs = qs.filter(month__gte=month_from)
        elif month_to:
            qs = qs.filter(month__lte=month_to)
        return qs


class MonthlyForumDecisionComplianceViewSet(viewsets.ModelViewSet):
    serializer_class = MonthlyForumDecisionComplianceSerializer

    def get_queryset(self):
        month_from, month_to = get_date_range_from_request(self.request, 'month')
        qs = MonthlyForumDecisionCompliance.objects.all()
        if month_from and month_to:
            qs = qs.filter(month__range=(month_from, month_to))
        elif month_from:
            qs = qs.filter(month__gte=month_from)
        elif month_to:
            qs = qs.filter(month__lte=month_to)
        return qs


class MonthlyNERCComplaintResolutionViewSet(viewsets.ModelViewSet):
    serializer_class = MonthlyNERCComplaintResolutionSerializer

    def get_queryset(self):
        month_from, month_to = get_date_range_from_request(self.request, 'month')
        qs = MonthlyNERCComplaintResolution.objects.all()
        if month_from and month_to:
            qs = qs.filter(month__range=(month_from, month_to))
        elif month_from:
            qs = qs.filter(month__gte=month_from)
        elif month_to:
            qs = qs.filter(month__lte=month_to)
        return qs
