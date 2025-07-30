from django.db.models import (
    Sum, Count, Avg
)
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from commercial.models import *
from commercial.serializers import *
from commercial.models import (
    DailyCollection, MonthlyEnergyBilled
)
from commercial.date_filters import get_date_range_from_request
from commercial.mixins import FeederFilteredQuerySetMixin
from commercial.utils import get_filtered_customers
from financial.models import MonthlyRevenueBilled


def get_sales_rep_performance_summary(request):
    sales_rep_slug = request.GET.get('sales_rep')
    feeder_slug = request.GET.get('feeder')
    month_from, month_to = get_date_range_from_request(request, 'month')

    qs = SalesRepPerformance.objects.all()

    if sales_rep_slug:
        qs = qs.filter(sales_rep__slug=sales_rep_slug)

    if month_from and month_to:
        qs = qs.filter(month__range=(month_from, month_to))
    elif month_from:
        qs = qs.filter(month__gte=month_from)
    elif month_to:
        qs = qs.filter(month__lte=month_to)

    if feeder_slug:
        qs = qs.filter(sales_rep__assigned_feeders__slug=feeder_slug)

    summary = qs.aggregate(
        total_outstanding_billed=Sum('outstanding_billed'),
        total_current_billed=Sum('current_billed'),
        total_collections=Sum('collections'),
        total_daily_run_rate=Sum('daily_run_rate'),
        total_collections_on_outstanding=Sum('collections_on_outstanding'),
        total_active_accounts=Sum('active_accounts'),
        total_suspended_accounts=Sum('suspended_accounts'),
    )

    return {key: round(value, 2) if isinstance(value, float) else (value or 0) for key, value in summary.items()}



class SalesRepresentativeViewSet(viewsets.ModelViewSet):
    queryset = SalesRepresentative.objects.all()
    serializer_class = SalesRepresentativeSerializer

class SalesRepPerformanceViewSet(viewsets.ModelViewSet):
    serializer_class = SalesRepPerformanceSerializer

    def get_queryset(self):
        qs = SalesRepPerformance.objects.all()
        sales_rep_slug = self.request.GET.get('sales_rep')

        if sales_rep_slug:
            qs = qs.filter(sales_rep__slug=sales_rep_slug)

        month_from, month_to = get_date_range_from_request(self.request, 'month')
        if month_from and month_to:
            qs = qs.filter(month__range=(month_from, month_to))
        elif month_from:
            qs = qs.filter(month__gte=month_from)
        elif month_to:
            qs = qs.filter(month__lte=month_to)

        return qs
    
class SalesRepMetricsView(APIView):
    def get(self, request):
        data = get_sales_rep_performance_summary(request)
        return Response(data)


class CustomerViewSet(viewsets.ViewSet):
    """
    Custom ViewSet to return either customer details or just counts
    """

    def list(self, request):
        customers = get_filtered_customers(request)

        # Show full data only if explicitly asked for
        if request.GET.get("details") == "true":
            serializer = CustomerSerializer(customers, many=True)
            return Response(serializer.data)
        else:
            count = customers.count()
            return Response({"count": count})


class DailyEnergyDeliveredViewSet(FeederFilteredQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = DailyEnergyDeliveredSerializer

    def get_queryset(self):
        queryset = DailyEnergyDelivered.objects.all()
        queryset = self.filter_by_location(queryset)
        date_from, date_to = get_date_range_from_request(self.request, 'date')

        if date_from and date_to:
            queryset = queryset.filter(date__range=(date_from, date_to))
        elif date_from:
            queryset = queryset.filter(date__gte=date_from)
        elif date_to:
            queryset = queryset.filter(date__lte=date_to)

        return queryset


class MonthlyRevenueBilledViewSet(viewsets.ModelViewSet):
    serializer_class = MonthlyRevenueBilledSerializer

    def get_queryset(self):
        queryset = MonthlyRevenueBilled.objects.all()
        
        # Custom location filtering for MonthlyRevenueBilled
        state_name = self.request.GET.get('state')
        district_name = self.request.GET.get('business_district')
        feeder_slug = self.request.GET.get('feeder')
        transformer_slug = self.request.GET.get('transformer')

        if transformer_slug:
            queryset = queryset.filter(transformer__slug=transformer_slug)
        elif feeder_slug:
            queryset = queryset.filter(feeder__slug=feeder_slug)
        elif district_name:
            queryset = queryset.filter(feeder__business_district__name__iexact=district_name)
        elif state_name:
            queryset = queryset.filter(feeder__business_district__state__name__iexact=state_name)

        # Date filtering
        month_from, month_to = get_date_range_from_request(self.request, 'month')

        if month_from and month_to:
            queryset = queryset.filter(month__range=(month_from, month_to))
        elif month_from:
            queryset = queryset.filter(month__gte=month_from)
        elif month_to:
            queryset = queryset.filter(month__lte=month_to)

        return queryset.select_related(
            'feeder', 'transformer', 'feeder__business_district',
            'feeder__business_district__state'
        )

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Get revenue billing summary with aggregations"""
        queryset = self.get_queryset()
        
        # Basic aggregations
        summary_data = queryset.aggregate(
            total_amount=Sum('amount'),
            total_records=Count('id'),
            avg_amount=Avg('amount')
        )

        # Group by feeder
        by_feeder = queryset.values(
            'feeder__name', 'feeder__slug'
        ).annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')

        # Group by transformer (if applicable)
        by_transformer = queryset.filter(
            transformer__isnull=False
        ).values(
            'transformer__name', 'transformer__slug'
        ).annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')

        # Group by business district
        by_district = queryset.values(
            'feeder__business_district__name'
        ).annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')

        # Group by state
        by_state = queryset.values(
            'feeder__business_district__state__name'
        ).annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')

        return Response({
            'summary': summary_data,
            'by_feeder': by_feeder,
            'by_transformer': by_transformer,
            'by_district': by_district,
            'by_state': by_state
        })
    

class DailyCollectionViewSet(FeederFilteredQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = DailyCollectionSerializer

    def get_queryset(self):
        queryset = DailyCollection.objects.all()
        queryset = self.filter_by_location(queryset)
        date_from, date_to = get_date_range_from_request(self.request, 'date')

        if date_from and date_to:
            queryset = queryset.filter(date__range=(date_from, date_to))
        elif date_from:
            queryset = queryset.filter(date__gte=date_from)
        elif date_to:
            queryset = queryset.filter(date__lte=date_to)

        # Additional filters
        collection_type = self.request.GET.get('collection_type')
        if collection_type:
            queryset = queryset.filter(collection_type=collection_type)

        vendor_name = self.request.GET.get('vendor_name')
        if vendor_name:
            queryset = queryset.filter(vendor_name=vendor_name)

        sales_rep_slug = self.request.GET.get('sales_rep')
        if sales_rep_slug:
            queryset = queryset.filter(sales_rep__slug=sales_rep_slug)

        transformer_slug = self.request.GET.get('transformer')
        if transformer_slug:
            queryset = queryset.filter(transformer__slug=transformer_slug)

        return queryset.select_related(
            'sales_rep', 'transformer', 'transformer__feeder', 
            'transformer__feeder__business_district', 
            'transformer__feeder__business_district__state'
        )

    def perform_create(self, serializer):
        """Override to add any additional logic during creation"""
        serializer.save()

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Get collection summary with aggregations"""
        queryset = self.get_queryset()
        
        # Basic aggregations
        summary_data = queryset.aggregate(
            total_amount=Sum('amount'),
            total_collections=Count('id'),
            avg_collection=Avg('amount')
        )

        # Group by collection type
        by_type = queryset.values('collection_type').annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')

        # Group by vendor
        by_vendor = queryset.values('vendor_name').annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')

        # Group by sales rep
        by_sales_rep = queryset.values(
            'sales_rep__name', 'sales_rep__slug'
        ).annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')

        return Response({
            'summary': summary_data,
            'by_collection_type': by_type,
            'by_vendor': by_vendor,
            'by_sales_rep': by_sales_rep
        })


class MonthlyEnergyBilledViewSet(FeederFilteredQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = MonthlyEnergyBilledSerializer

    def get_queryset(self):
        queryset = MonthlyEnergyBilled.objects.all()
        queryset = self.filter_by_location(queryset)
        month_from, month_to = get_date_range_from_request(self.request, 'month')

        if month_from and month_to:
            queryset = queryset.filter(month__range=(month_from, month_to))
        elif month_from:
            queryset = queryset.filter(month__gte=month_from)
        elif month_to:
            queryset = queryset.filter(month__lte=month_to)

        return queryset


class MonthlyCustomerStatsViewSet(FeederFilteredQuerySetMixin, viewsets.ModelViewSet):
    serializer_class = MonthlyCustomerStatsSerializer

    def get_queryset(self):
        queryset = MonthlyCustomerStats.objects.all()
        queryset = self.filter_by_location(queryset)
        month_from, month_to = get_date_range_from_request(self.request, 'month')

        if month_from and month_to:
            queryset = queryset.filter(month__range=(month_from, month_to))
        elif month_from:
            queryset = queryset.filter(month__gte=month_from)
        elif month_to:
            queryset = queryset.filter(month__lte=month_to)

        return queryset