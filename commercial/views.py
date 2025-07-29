# commercial/views.py
import random
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date, datetime
from dateutil.relativedelta import relativedelta  # type: ignore

from django.db.models import (
    Sum, F, FloatField, ExpressionWrapper, Q,
    Case, When, Value, Count, Avg, DurationField
)
from django.utils.dateparse import parse_date

from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import *
from .serializers import *
from .utils import get_filtered_feeders

from common.models import Feeder, State, BusinessDistrict, Band
from commercial.models import (
    DailyCollection, MonthlyCommercialSummary, MonthlyEnergyBilled
)
from commercial.date_filters import get_date_range_from_request
from commercial.mixins import FeederFilteredQuerySetMixin
from commercial.utils import get_filtered_customers
from commercial.metrics import (
    get_sales_rep_performance_summary
)
from commercial.analytics import get_commercial_overview_data

from technical.models import EnergyDelivered, HourlyLoad, FeederInterruption
from financial.models import MonthlyRevenueBilled, Opex, SalaryPayment, NBETInvoice, MOInvoice



from decimal import Decimal, ROUND_HALF_UP

def round_two_places(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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


# Aggregated Metrics Endpoint
from rest_framework.views import APIView

class FeederMetricsView(APIView):
    def get(self, request):
        month_from = request.GET.get('month_from')
        month_to = request.GET.get('month_to')
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')
        top_n = request.GET.get('top_n')
        bottom_n = request.GET.get('bottom_n')

        if month_from:
            month_from = datetime.strptime(month_from, "%Y-%m-%d").date()
        if month_to:
            month_to = datetime.strptime(month_to, "%Y-%m-%d").date()
        if date_from:
            date_from = datetime.strptime(date_from, "%Y-%m-%d").date()
        if date_to:
            date_to = datetime.strptime(date_to, "%Y-%m-%d").date()

        feeders = get_filtered_feeders(request)
        metrics = []

        for feeder in feeders:
            energy_billed = MonthlyEnergyBilled.objects.filter(
                feeder=feeder,
                month__range=(month_from, month_to)
            ).aggregate(total=Sum('energy_mwh'))['total'] or 0

            energy_delivered = DailyEnergyDelivered.objects.filter(
                feeder=feeder,
                date__range=(date_from, date_to)
            ).aggregate(total=Sum('energy_mwh'))['total'] or 0

            # revenue_collected = DailyRevenueCollected.objects.filter(
            #     feeder=feeder,
            #     date__range=(date_from, date_to)
            # ).aggregate(total=Sum('amount'))['total'] or 0
            revenue_collected=0

            revenue_billed = MonthlyRevenueBilled.objects.filter(
                feeder=feeder,
                month__range=(month_from, month_to)
            ).aggregate(total=Sum('amount'))['total'] or 0

            collection_eff = (revenue_collected / revenue_billed) * 100 if revenue_billed else 0
            billing_eff = (energy_billed / energy_delivered) * 100 if energy_delivered else 0
            atc_c = 100 - (billing_eff * collection_eff / 100)

            metrics.append({
                "feeder_id": feeder.id,
                "feeder": feeder.name,
                "energy_billed": energy_billed,
                "energy_delivered": energy_delivered,
                "revenue_collected": revenue_collected,
                "revenue_billed": revenue_billed,
                "collection_efficiency": round(collection_eff, 2),
                "billing_efficiency": round(billing_eff, 2),
                "atc_c": round(atc_c, 2),
            })

        # Sort and slice by ATC&C
        metrics.sort(key=lambda x: x['atc_c'])
        if top_n:
            metrics = metrics[:int(top_n)]
        elif bottom_n:
            metrics = metrics[-int(bottom_n):]

        return Response(metrics)    


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


class DailyCollectionViewSet(viewsets.ModelViewSet):
    serializer_class = DailyCollectionSerializer

    def get_queryset(self):
        feeders = get_filtered_feeders(self.request)
        date_from, date_to = get_date_range_from_request(self.request, 'date')

        qs = DailyCollection.objects.filter(feeder__in=feeders)

        if date_from and date_to:
            qs = qs.filter(date__range=(date_from, date_to))
        elif date_from:
            qs = qs.filter(date__gte=date_from)
        elif date_to:
            qs = qs.filter(date__lte=date_to)

        collection_type = self.request.GET.get('collection_type')
        if collection_type:
            qs = qs.filter(collection_type=collection_type)

        return qs




class CommercialOverviewAPIView(APIView):
    def get(self, request):
        # Parse query parameters
        mode = request.query_params.get('mode', 'monthly')
        year = int(request.query_params.get('year', datetime.today().year))
        month = int(request.query_params.get('month', datetime.today().month))
        from_date = request.query_params.get('from_date')
        to_date = request.query_params.get('to_date')

        # Delegate to analytics logic
        data = get_commercial_overview_data(
            mode=mode,
            year=year,
            month=month,
            from_date=from_date,
            to_date=to_date
        )

        return Response(data)



def round_two_places(val):
    return Decimal(val).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def smart_target(value, variation=0.1):
    try:
        value = Decimal(str(value))  # ensure input is Decimal
        percent_shift = Decimal(str(random.uniform(-variation, variation)))
        return float(round_two_places(value * (Decimal("1") + percent_shift)))
    except:
        return 0  


@api_view(["GET"])
def commercial_all_states_view(request):
    mode = request.query_params.get("mode", "monthly")
    year = int(request.query_params.get("year", date.today().year))
    month = int(request.query_params.get("month", date.today().month))
    from_date = request.query_params.get("from_date")
    to_date = request.query_params.get("to_date")

    if mode == "monthly":
        current_start = date(year, month, 1)
        current_end = current_start + relativedelta(months=1)
        previous_start = current_start - relativedelta(months=1)
        previous_end = current_start
    else:
        current_start = parse_date(from_date) or date.today().replace(day=1)
        current_end = parse_date(to_date) or date.today()
        previous_start = current_start - (current_end - current_start)
        previous_end = current_start

    results = []

    for state in State.objects.all():
        def summary_agg(start, end):
            summaries = MonthlyCommercialSummary.objects.filter(
                sales_rep__assigned_transformers__feeder__business_district__state=state,
                month__gte=start,
                month__lt=end,
            ).distinct()

            billed = summaries.aggregate(Sum("revenue_billed"))["revenue_billed__sum"] or Decimal(0)
            collected = summaries.aggregate(Sum("revenue_collected"))["revenue_collected__sum"] or Decimal(0)
            cust_billed = summaries.aggregate(Sum("customers_billed"))["customers_billed__sum"] or 0
            cust_resp = summaries.aggregate(Sum("customers_responded"))["customers_responded__sum"] or 0

            return billed, collected, cust_billed, cust_resp

        def delivered_agg(start, end):
            return EnergyDelivered.objects.filter(
                feeder__business_district__state=state,
                date__gte=start,
                date__lt=end
            ).aggregate(Sum("energy_mwh"))["energy_mwh__sum"] or Decimal(0)

        # Current
        revenue_billed, revenue_collected, cust_billed, cust_resp = summary_agg(current_start, current_end)
        energy_delivered = delivered_agg(current_start, current_end)
        energy_billed = MonthlyEnergyBilled.objects.filter(
            feeder__business_district__state=state,
            month__gte=current_start,
            month__lt=current_end
        ).aggregate(Sum("energy_mwh"))["energy_mwh__sum"] or Decimal(0)

        # Previous
        prev_billed, prev_collected, prev_cust_billed, prev_cust_resp = summary_agg(previous_start, previous_end)
        prev_delivered = delivered_agg(previous_start, previous_end)
        prev_energy_billed = MonthlyEnergyBilled.objects.filter(
            feeder__business_district__state=state,
            month__gte=previous_start,
            month__lt=previous_end
        ).aggregate(Sum("energy_mwh"))["energy_mwh__sum"] or Decimal(0)

        def calc_efficiencies(billed, collected, delivered, energy_billed):
            try:
                billing_eff = (Decimal(energy_billed) / Decimal(delivered)) * 100 if delivered else Decimal(0)
                collection_eff = (Decimal(collected) / Decimal(billed)) * 100 if billed else Decimal(0)
                atcc = (Decimal(1) - ((billing_eff / 100) * (collection_eff / 100))) * 100
            except (InvalidOperation, ZeroDivisionError):
                billing_eff = collection_eff = atcc = Decimal(0)
            return billing_eff, collection_eff, atcc

        billing_eff, collection_eff, atcc = calc_efficiencies(
            revenue_billed, revenue_collected, energy_delivered, energy_billed
        )
        prev_billing_eff, prev_collection_eff, prev_atcc = calc_efficiencies(
            prev_billed, prev_collected, prev_delivered, prev_energy_billed
        )

        def percentage_delta(current, previous):
            if previous and previous != 0:
                return round(float(((Decimal(current) - Decimal(previous)) / Decimal(previous)) * 100), 2)
            return None

        results.append({
            "state": state.name,
            "energy_delivered": {
                "actual": float(round_two_places(energy_delivered)),
                "delta": percentage_delta(energy_delivered, prev_delivered)
            },
            "energy_billed": {
                "actual": float(round_two_places(energy_billed)),
                "delta": percentage_delta(energy_billed, prev_energy_billed)
            },
            "energy_collected": {
                "actual": float(round_two_places(revenue_collected)),
                "delta": percentage_delta(revenue_collected, prev_collected)
            },
            "atcc": {
                "actual": float(round_two_places(atcc)),
                "delta": percentage_delta(atcc, prev_atcc),
                "target": smart_target(atcc)
            },
            "billing_efficiency": {
                "actual": float(round_two_places(billing_eff)),
                "delta": percentage_delta(billing_eff, prev_billing_eff),
                "target": smart_target(billing_eff)
            },
            "collection_efficiency": {
                "actual": float(round_two_places(collection_eff)),
                "delta": percentage_delta(collection_eff, prev_collection_eff),
                "target": smart_target(collection_eff)
            },
            "customer_response_rate": {
                "actual": float(round_two_places((cust_resp / cust_billed) * 100 if cust_billed else 0)),
                "delta": percentage_delta(
                    (cust_resp / cust_billed * 100 if cust_billed else 0),
                    (prev_cust_resp / prev_cust_billed * 100 if prev_cust_billed else 0)
                ),
                "target": smart_target((cust_resp / cust_billed * 100 if cust_billed else 0))
            },
            "revenue_billed_per_customer": {
                "actual": float(round_two_places(revenue_billed / cust_billed)) if cust_billed else 0,
                "delta": percentage_delta(
                    (revenue_billed / cust_billed) if cust_billed else 0,
                    (prev_billed / prev_cust_billed) if prev_cust_billed else 0
                )
            },
            "collections_per_customer": {
                "actual": float(round_two_places(revenue_collected / cust_billed)) if cust_billed else 0,
                "delta": percentage_delta(
                    (revenue_collected / cust_billed) if cust_billed else 0,
                    (prev_collected / prev_cust_billed) if prev_cust_billed else 0
                )
            },
        })

    return Response(results)



# def round_two_places(value):
#     return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def compute_delta(current, previous):
    if previous in [0, None]:
        return None
    try:
        delta = ((Decimal(current) - Decimal(previous)) / Decimal(previous)) * 100
        return float(round_two_places(delta))
    except Exception:
        return None


@api_view(["GET"])
def commercial_state_metrics_view(request):
    state_name = request.query_params.get("state")
    mode = request.query_params.get("mode", "monthly")
    year = int(request.query_params.get("year", date.today().year))
    month = int(request.query_params.get("month", date.today().month))
    from_date = request.query_params.get("from_date")
    to_date = request.query_params.get("to_date")

    state = State.objects.filter(name__iexact=state_name).first()
    if not state:
        return Response({"error": "Invalid state"}, status=400)

    def generate_month_list(reference_date):
        return [reference_date - relativedelta(months=i) for i in range(4, -1, -1)]

    def safe_round(value, places=2):
        """Safely round decimal values"""
        try:
            if value is None:
                return 0.0
            return float(Decimal(str(value)).quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP))
        except (InvalidOperation, TypeError):
            return 0.0

    def safe_divide(numerator, denominator):
        """Safely divide two numbers, return 0 if denominator is 0"""
        try:
            num = Decimal(str(numerator or 0))
            den = Decimal(str(denominator or 0))
            return (num / den) if den > 0 else Decimal(0)
        except (InvalidOperation, ZeroDivisionError):
            return Decimal(0)

    selected_date = date(year, month, 1) if mode == "monthly" else parse_date(to_date) or date.today()
    months = generate_month_list(selected_date)

    data = []
    previous = None

    for m in months:
        # Get all feeders in this state
        state_feeders = Feeder.objects.filter(business_district__state=state)
        
        # Get all transformers in this state
        state_transformers = DistributionTransformer.objects.filter(
            feeder__business_district__state=state
        )

        # Get commercial summaries directly through transformers - much more efficient!
        summaries = MonthlyCommercialSummary.objects.filter(
            transformer__in=state_transformers,
            month__year=m.year,
            month__month=m.month,
        )

        # Energy Delivered - use proper technical models
        period_start = date(m.year, m.month, 1)
        try:
            # Try monthly aggregates first
            delivered_mwh = FeederEnergyMonthly.objects.filter(
                feeder__in=state_feeders,
                period=period_start,
            ).aggregate(Sum("energy_mwh"))['energy_mwh__sum'] or Decimal(0)
            
            if delivered_mwh == 0:
                # Fallback to daily aggregation
                period_end = period_start + relativedelta(months=1) - timedelta(days=1)
                delivered_mwh = FeederEnergyDaily.objects.filter(
                    feeder__in=state_feeders,
                    date__gte=period_start,
                    date__lte=period_end,
                ).aggregate(Sum("energy_mwh"))['energy_mwh__sum'] or Decimal(0)
                
            # Additional fallback to EnergyDelivered if other models are empty
            if delivered_mwh == 0:
                delivered_mwh = EnergyDelivered.objects.filter(
                    feeder__in=state_feeders,
                    date__year=m.year,
                    date__month=m.month,
                ).aggregate(Sum("energy_mwh"))['energy_mwh__sum'] or Decimal(0)
        except Exception as e:
            print(f"Error calculating delivered energy for {state.name} {m}: {e}")
            delivered_mwh = Decimal(0)

        # Energy Billed - use commercial models
        try:
            billed_mwh = MonthlyEnergyBilled.objects.filter(
                feeder__in=state_feeders,
                month=period_start,
            ).aggregate(Sum("energy_mwh"))['energy_mwh__sum'] or Decimal(0)
        except Exception as e:
            print(f"Error calculating billed energy for {state.name} {m}: {e}")
            billed_mwh = Decimal(0)

        # Commercial summaries aggregation
        totals = summaries.aggregate(
            revenue_collected=Sum("revenue_collected"),
            revenue_billed=Sum("revenue_billed"),
            customers_billed=Sum("customers_billed"),
            customers_responded=Sum("customers_responded"),
        )

        revenue_billed = totals['revenue_billed'] or Decimal(0)
        revenue_collected = totals['revenue_collected'] or Decimal(0)
        cust_billed = totals['customers_billed'] or 0
        cust_resp = totals['customers_responded'] or 0

        # Correct metrics calculations
        try:
            # Billing efficiency = (Energy Billed / Energy Delivered) * 100
            billing_eff = safe_divide(billed_mwh, delivered_mwh) * 100
            
            # Collection efficiency = (Revenue Collected / Revenue Billed) * 100
            collection_eff = safe_divide(revenue_collected, revenue_billed) * 100
            
            # AT&C losses = 100% - (Billing Efficiency * Collection Efficiency / 100)
            atcc_losses = Decimal(100) - (billing_eff * collection_eff / 100)
            
            # Energy collected = Energy Delivered * (Collection Efficiency / 100)
            energy_collected = delivered_mwh * (collection_eff / 100)
            
            # Customer response rate = (Customers Responded / Customers Billed) * 100
            response_rate = safe_divide(Decimal(cust_resp), Decimal(cust_billed)) * 100
            
            # Revenue per customer = Revenue Billed / Customers Billed
            revenue_per_cust = safe_divide(revenue_billed, Decimal(cust_billed))
            
            # Collections per customer = Revenue Collected / Customers Billed  
            collection_per_cust = safe_divide(revenue_collected, Decimal(cust_billed))
            
        except Exception as e:
            print(f"Error in calculations for {state.name} {m}: {e}")
            billing_eff = collection_eff = atcc_losses = energy_collected = Decimal(0)
            response_rate = revenue_per_cust = collection_per_cust = Decimal(0)

        current = {
            "month": m.strftime("%b"),
            "year": m.year,
            "energy_delivered": safe_round(delivered_mwh),
            "energy_billed": safe_round(billed_mwh),
            "revenue_billed": safe_round(revenue_billed),
            "revenue_collected": safe_round(revenue_collected),
            "energy_collected": safe_round(energy_collected),
            "billing_efficiency": safe_round(billing_eff),
            "collection_efficiency": safe_round(collection_eff),
            "atcc_losses": safe_round(atcc_losses),  # Renamed for clarity
            "customer_response_rate": safe_round(response_rate),
            "revenue_billed_per_customer": safe_round(revenue_per_cust),
            "collections_per_customer": safe_round(collection_per_cust),
            "customers_billed": cust_billed,
            "customers_responded": cust_resp,
        }

        # Add deltas if we have previous month data
        if previous:
            current["deltas"] = {
                "energy_delivered": compute_delta(current["energy_delivered"], previous["energy_delivered"]),
                "energy_billed": compute_delta(current["energy_billed"], previous["energy_billed"]),
                "revenue_billed": compute_delta(current["revenue_billed"], previous["revenue_billed"]),
                "revenue_collected": compute_delta(current["revenue_collected"], previous["revenue_collected"]),
                "energy_collected": compute_delta(current["energy_collected"], previous["energy_collected"]),
                "billing_efficiency": compute_delta(current["billing_efficiency"], previous["billing_efficiency"]),
                "collection_efficiency": compute_delta(current["collection_efficiency"], previous["collection_efficiency"]),
                "atcc_losses": compute_delta(current["atcc_losses"], previous["atcc_losses"]),
                "customer_response_rate": compute_delta(current["customer_response_rate"], previous["customer_response_rate"]),
                "revenue_billed_per_customer": compute_delta(current["revenue_billed_per_customer"], previous["revenue_billed_per_customer"]),
                "collections_per_customer": compute_delta(current["collections_per_customer"], previous["collections_per_customer"]),
            }

        # Debug logging
        print(f"State: {state.name}, Month: {m}")
        print(f"  Energy delivered: {delivered_mwh}")
        print(f"  Energy billed: {billed_mwh}")
        print(f"  Revenue billed: {revenue_billed}")
        print(f"  Revenue collected: {revenue_collected}")
        print(f"  Billing efficiency: {billing_eff}%")
        print(f"  Collection efficiency: {collection_eff}%")
        print(f"  AT&C losses: {atcc_losses}%")
        print("---")

        previous = current.copy()  # Make a copy to avoid reference issues
        data.append(current)

    return Response(data)


from technical.models import FeederEnergyMonthly, FeederEnergyDaily
from datetime import timedelta

@api_view(["GET"])
def commercial_all_business_districts_view(request):
    state_name = request.query_params.get("state")
    year = int(request.query_params.get("year", date.today().year))
    month = int(request.query_params.get("month", date.today().month))

    period_start = date(year, month, 1)

    if not state_name:
        return Response({"error": "State parameter is required"}, status=400)

    districts = BusinessDistrict.objects.filter(state__name__iexact=state_name)
    result = []

    for district in districts:
        # Get all feeders in this business district specifically
        district_feeders = Feeder.objects.filter(business_district=district)
        
        # Get all transformers in this business district via feeders
        transformers = DistributionTransformer.objects.filter(
            feeder__in=district_feeders
        )
        
        # Get commercial summaries for transformers in this district
        summaries = MonthlyCommercialSummary.objects.filter(
            transformer__in=transformers,
            month=period_start  # Use exact month match
        )

        # Aggregate the commercial data
        totals = summaries.aggregate(
            revenue_billed=Sum("revenue_billed"),
            revenue_collected=Sum("revenue_collected"),
            customers_billed=Sum("customers_billed"),
            customers_responded=Sum("customers_responded"),
        )

        # Energy Delivered from technical models - use district_feeders specifically
        try:
            # Try monthly aggregates first
            delivered_mwh = FeederEnergyMonthly.objects.filter(
                feeder__in=district_feeders,  # Use specific feeders for this district
                period=period_start,
            ).aggregate(Sum("energy_mwh"))['energy_mwh__sum'] or Decimal(0)
            
            if delivered_mwh == 0:
                # Fallback to daily aggregation
                period_end = period_start + relativedelta(months=1) - timedelta(days=1)
                delivered_mwh = FeederEnergyDaily.objects.filter(
                    feeder__in=district_feeders,  # Use specific feeders for this district
                    date__gte=period_start,
                    date__lte=period_end,
                ).aggregate(Sum("energy_mwh"))['energy_mwh__sum'] or Decimal(0)
        except Exception as e:
            print(f"Error calculating delivered energy for {district.name}: {e}")
            delivered_mwh = Decimal(0)

        # Energy Billed from commercial models - use district_feeders specifically
        try:
            billed_mwh = MonthlyEnergyBilled.objects.filter(
                feeder__in=district_feeders,  # Use specific feeders for this district
                month=period_start,
            ).aggregate(Sum("energy_mwh"))['energy_mwh__sum'] or Decimal(0)
        except Exception as e:
            print(f"Error calculating billed energy for {district.name}: {e}")
            billed_mwh = Decimal(0)

        # Extract values with safe defaults
        revenue_billed = totals["revenue_billed"] or Decimal(0)
        revenue_collected = totals["revenue_collected"] or Decimal(0)
        customers_billed = totals["customers_billed"] or 0
        customers_responded = totals["customers_responded"] or 0

        # Calculate efficiency metrics with proper error handling
        try:
            # Billing efficiency = (Energy Billed / Energy Delivered) * 100
            billing_eff = (billed_mwh / delivered_mwh * 100) if delivered_mwh > 0 else Decimal(0)
            
            # Collection efficiency = (Revenue Collected / Revenue Billed) * 100
            collection_eff = (revenue_collected / revenue_billed * 100) if revenue_billed > 0 else Decimal(0)
            
            # AT&C losses = 100% - (Billing Efficiency * Collection Efficiency / 100)
            atcc = Decimal(100) - (billing_eff * collection_eff / 100)
            
            # Energy collected = Energy Delivered * Collection Efficiency / 100
            # This represents the energy equivalent of what was actually collected
            energy_collected = delivered_mwh * collection_eff / 100
            
        except (ZeroDivisionError, InvalidOperation) as e:
            print(f"Error calculating efficiency metrics for {district.name}: {e}")
            billing_eff = collection_eff = atcc = energy_collected = Decimal(0)

        # Customer metrics
        try:
            response_rate = (Decimal(customers_responded) / Decimal(customers_billed) * 100) if customers_billed > 0 else Decimal(0)
            revenue_per_customer = (revenue_billed / Decimal(customers_billed)) if customers_billed > 0 else Decimal(0)
            collections_per_customer = (revenue_collected / Decimal(customers_billed)) if customers_billed > 0 else Decimal(0)
        except (ZeroDivisionError, InvalidOperation) as e:
            print(f"Error calculating customer metrics for {district.name}: {e}")
            response_rate = revenue_per_customer = collections_per_customer = Decimal(0)

        # Round all decimal values appropriately
        def safe_round(value, places=2):
            try:
                return float(value.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP))
            except (AttributeError, InvalidOperation):
                return float(value) if value else 0.0

        # Debug logging to help identify issues
        print(f"District: {district.name}")
        print(f"  Feeders count: {district_feeders.count()}")
        print(f"  Energy delivered: {delivered_mwh}")
        print(f"  Energy billed: {billed_mwh}")
        print(f"  Billing efficiency: {billing_eff}")
        print(f"  Revenue billed: {revenue_billed}")
        print(f"  Revenue collected: {revenue_collected}")
        print("---")

        result.append({
            "name": district.name,
            "energy_delivered": safe_round(delivered_mwh),
            "energy_billed": safe_round(billed_mwh),
            "energy_collected": safe_round(energy_collected),
            "revenue_billed": safe_round(revenue_billed),
            "revenue_collected": safe_round(revenue_collected),
            "billing_efficiency": safe_round(billing_eff),
            "collection_efficiency": safe_round(collection_eff),
            "atcc": safe_round(atcc),
            "customer_response_rate": safe_round(response_rate),
            "revenue_billed_per_customer": safe_round(revenue_per_customer),
            "collections_per_customer": safe_round(collections_per_customer),
            "customers_billed": customers_billed,
            "customers_responded": customers_responded,
        })

    return Response(result)


# Helper
def NullIfZero(field):
    return Case(
        When(**{f'{field.name}': 0}, then=Value(None)),
        default=field,
        output_field=FloatField(),
    )



@api_view(['GET'])
def feeder_metrics(request):
    feeders = get_filtered_feeders(request)
    date_filter = get_date_range_from_request(request)

    queryset = feeders.filter(**date_filter).annotate(
        energy_delivered=Sum('dailyenergydelivered__energy_mwh'),
        energy_billed=Sum('monthlyenergybilled__energy_mwh'),
        energy_collected=Sum('dailyrevenuecollected__amount'),
        atcc=ExpressionWrapper(
            100 - (
                (100 - F('billing_efficiency')) *
                (100 - F('collection_efficiency')) / 100
            ),
            output_field=FloatField()
        )
    ).values('name', 'energy_delivered', 'energy_billed', 'energy_collected', 'atcc')

    return Response(queryset)



# from django.views.decorators.cache import cache_page
# from django.utils.decorators import method_decorator


# # @method_decorator(cache_page(60 * 5), name='dispatch')
class OverviewAPIView(APIView):
    def get(self, request):
        try:
            month = int(request.GET.get("month"))
            year = int(request.GET.get("year"))
            target = datetime(year, month, 1)
        except (TypeError, ValueError):
            target = None

        from_date_str = request.GET.get("from")
        to_date_str = request.GET.get("to")
        from_date = datetime.strptime(from_date_str, "%Y-%m-%d") if from_date_str else None
        to_date = datetime.strptime(to_date_str, "%Y-%m-%d") if to_date_str else None

        if from_date and to_date:
            current = from_date.replace(day=1)
            months = []
            while current <= to_date:
                months.append(current)
                current += relativedelta(months=1)
        else:
            target = target or datetime.today().replace(day=1)
            months = [(target - relativedelta(months=i)).replace(day=1) for i in range(5)][::-1]

        overview_data = []

        for m in months:
            # Commercial data aggregation
            comm = MonthlyCommercialSummary.objects.filter(month=m).aggregate(
                revenue_billed=Sum("revenue_billed"),
                revenue_collected=Sum("revenue_collected"),
                customers_billed=Sum("customers_billed"),
                customers_responded=Sum("customers_responded"),
            )
            
            # Energy data aggregation
            energy_delivered = EnergyDelivered.objects.filter(
                date__year=m.year, 
                date__month=m.month
            ).aggregate(energy=Sum("energy_mwh"))["energy"] or Decimal("0")
            
            energy_billed = MonthlyEnergyBilled.objects.filter(month=m).aggregate(
                energy=Sum("energy_mwh")
            )["energy"] or Decimal("0")

            # Extract commercial values first
            revenue_billed = comm['revenue_billed'] or Decimal("0")
            revenue_collected = comm['revenue_collected'] or Decimal("0")
            customers_billed = comm['customers_billed'] or 0
            customers_responded = comm['customers_responded'] or 0
            
            # Financial data - include all cost components
            opex_costs = Opex.objects.filter(
                date__year=m.year, 
                date__month=m.month
            ).aggregate(
                total_opex=Sum("credit") + Sum("debit")
            )["total_opex"] or Decimal("0")
            
            # Add other cost components - ensure proper date filtering
            salary_costs = SalaryPayment.objects.filter(
                month__year=m.year,
                month__month=m.month
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            
            nbet_costs = NBETInvoice.objects.filter(
                month__year=m.year,
                month__month=m.month
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            
            mo_costs = MOInvoice.objects.filter(
                month__year=m.year,
                month__month=m.month
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            
            total_cost = opex_costs + salary_costs + nbet_costs + mo_costs
            
            # Check if we have any actual operational data for this month
            has_operational_data = (
                energy_delivered > 0 or 
                revenue_billed > 0 or 
                customers_billed > 0
            )
            
            # If no operational data exists, you might want to set total_cost to 0
            # or handle it differently based on business logic
            if not has_operational_data and total_cost == 0:
                # This month truly has no data
                total_cost = Decimal("0")

            # Technical metrics - improved calculation
            # Calculate average hours of supply more accurately
            hourly_loads = HourlyLoad.objects.filter(
                date__year=m.year, 
                date__month=m.month,
                load_mw__gt=0  # Only count hours with actual load
            ).values('feeder', 'date').annotate(
                daily_hours=Count('hour')
            )
            
            if hourly_loads.exists():
                avg_hours_supply = hourly_loads.aggregate(
                    avg=Avg('daily_hours')
                )["avg"] or 0
            else:
                avg_hours_supply = 0

            # Calculate interruption metrics properly
            interruptions = FeederInterruption.objects.filter(
                occurred_at__year=m.year, 
                occurred_at__month=m.month,
                restored_at__isnull=False
            )
            
            if interruptions.exists():
                total_duration = sum(
                    (interrupt.restored_at - interrupt.occurred_at).total_seconds() / 3600 
                    for interrupt in interruptions
                )
                avg_interruption_duration = total_duration / interruptions.count()
                avg_turnaround_time = avg_interruption_duration  # Same as interruption duration
            else:
                avg_interruption_duration = 0
                avg_turnaround_time = 0

            # Calculate efficiency metrics without tariff
            # Billing efficiency = Energy Billed / Energy Delivered
            billing_eff = (energy_billed / energy_delivered) if energy_delivered > 0 else Decimal("0")
            
            # Collection efficiency = Revenue Collected / Revenue Billed
            collection_eff = (revenue_collected / revenue_billed) if revenue_billed > 0 else Decimal("0")
            
            # AT&C Losses = 100% - (Billing Efficiency × Collection Efficiency)
            atc_losses = Decimal("1") - (billing_eff * collection_eff)
            
            # Energy collected = Energy Delivered × Collection Efficiency
            # This represents the energy equivalent of what was actually collected
            energy_collected = energy_delivered * collection_eff
            
            # Customer response rate
            customer_response_rate = (customers_responded / customers_billed * 100) if customers_billed > 0 else 0

            overview_data.append({
                "month": m.strftime("%b"),
                "billing_efficiency": float(round(billing_eff * 100, 2)),
                "collection_efficiency": float(round(collection_eff * 100, 2)),
                "atcc": float(round(atc_losses * 100, 2)),  # AT&C losses as percentage
                "revenue_billed": float(revenue_billed),
                "revenue_collected": float(revenue_collected),
                "energy_billed": float(energy_billed),
                "energy_delivered": float(energy_delivered),
                "energy_collected": float(round(energy_collected, 2)),
                "customer_response_rate": round(customer_response_rate, 2),
                "total_cost": float(total_cost),
                "avg_hours_supply": round(avg_hours_supply, 2),
                "avg_interruption_duration": round(avg_interruption_duration, 2),
                "avg_turnaround_time": round(avg_turnaround_time, 2),
            })

        # Calculate deltas for current month vs previous month
        current = overview_data[-1] if overview_data else {}
        previous = overview_data[-2] if len(overview_data) > 1 else {}

        def calculate_delta(metric):
            """Calculate percentage change between current and previous values"""
            if (metric in current and metric in previous and 
                previous[metric] is not None and previous[metric] != 0):
                return round(((current[metric] - previous[metric]) / previous[metric]) * 100, 2)
            return None

        # Add delta calculations for all metrics
        metrics_to_track = [
            "atcc", "billing_efficiency", "collection_efficiency",
            "revenue_billed", "revenue_collected", "energy_billed", 
            "energy_delivered", "total_cost", "energy_collected",
            "customer_response_rate", "avg_hours_supply",
            "avg_interruption_duration", "avg_turnaround_time"
        ]

        for metric in metrics_to_track:
            current[f"delta_{metric}"] = calculate_delta(metric)

        return Response({
            "current": current,
            "history": overview_data[:-1]  # All months except current
        })


def calculate_atcc_metrics(feeder, start_date, end_date):
    delivered = EnergyDelivered.objects.filter(
        feeder=feeder,
        date__gte=start_date,
        date__lt=end_date
    ).aggregate(energy_mwh_sum=Sum("energy_mwh"))['energy_mwh_sum'] or Decimal(0)

    billed = MonthlyEnergyBilled.objects.filter(
        feeder=feeder,
        month__gte=start_date,
        month__lt=end_date
    ).aggregate(energy_mwh_sum=Sum("energy_mwh"))['energy_mwh_sum'] or Decimal(0)

    summaries = MonthlyCommercialSummary.objects.filter(
        sales_rep__assigned_transformers__feeder=feeder,
        month__gte=start_date,
        month__lt=end_date
    ).aggregate(
        revenue_collected_sum=Sum("revenue_collected"), 
        revenue_billed_sum=Sum("revenue_billed")
    )

    revenue_collected = summaries["revenue_collected_sum"] or Decimal(0)
    revenue_billed = summaries["revenue_billed_sum"] or Decimal(1)

    try:
        billing_eff = (billed / delivered * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if delivered else Decimal(0)
        collection_eff = (revenue_collected / revenue_billed * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if revenue_billed else Decimal(0)
        atcc = (Decimal(100) - (billing_eff * collection_eff / 100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        collected_energy = (delivered * collection_eff / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except:
        billing_eff = collection_eff = atcc = collected_energy = Decimal(0)

    return {
        "name": feeder.name,
        "energy_delivered": float(delivered),
        "energy_billed": float(billed),
        "energy_collected": float(collected_energy),
        "atcc": float(atcc),
        "voltage_level": feeder.voltage_level,
    }


@api_view(["GET"])
def feeder_performance_view(request):
    year = int(request.query_params.get("year", date.today().year))
    month = int(request.query_params.get("month", date.today().month))
    start_date = date(year, month, 1)
    end_date = start_date + relativedelta(months=1)

    feeders = Feeder.objects.all()
    feeder_data = []

    for feeder in feeders:
        metrics = calculate_atcc_metrics(feeder, start_date, end_date)
        feeder_data.append(metrics)

    sorted_by_atcc = sorted(feeder_data, key=lambda x: x["atcc"])
    return Response({
        "top_5": sorted_by_atcc[:5],
        "bottom_5": sorted_by_atcc[-5:][::-1]
    })


@api_view(["GET"])
def feeders_by_location_view(request):
    state_name = request.query_params.get("state")
    district_name = request.query_params.get("business_district")
    year = int(request.query_params.get("year", date.today().year))
    month = int(request.query_params.get("month", date.today().month))
    start_date = date(year, month, 1)
    end_date = start_date + relativedelta(months=1)

    filters = Q()

    if district_name:
        filters = Q(business_district__name__iexact=district_name)
    elif state_name:
        state = State.objects.filter(name__iexact=state_name).first()
        if not state:
            return Response({"error": "Invalid state"}, status=400)
        filters = Q(business_district__state=state)

    feeders = Feeder.objects.filter(filters)
    result = []

    for feeder in feeders:
        metrics = calculate_atcc_metrics(feeder, start_date, end_date)

        result.append({
            "name": feeder.name,
            "slug": feeder.slug,
            "voltage_level": feeder.voltage_level,
            "business_district": {
                "name": feeder.business_district.name if feeder.business_district else None,
                "slug": feeder.business_district.slug if feeder.business_district else None,
            },
            **metrics  # Unpack and merge the calculated metrics directly into the top-level dict
        })

    return Response(result)

from calendar import monthrange

@api_view(["GET"])
def transformer_metrics_by_feeder_view(request):
    feeder_slug = request.GET.get("feeder")
    if not feeder_slug:
        return Response({"error": "Missing feeder slug in query parameters"}, status=400)

    try:
        feeder = Feeder.objects.get(slug=feeder_slug)
    except Feeder.DoesNotExist:
        return Response({"error": "Feeder not found"}, status=404)

    transformers = DistributionTransformer.objects.filter(feeder=feeder)

    mode = request.GET.get("mode", "monthly")
    year = request.GET.get("year")
    month = request.GET.get("month")

    if mode == "monthly" and year and month:
        year = int(year)
        month = int(month)
        start_day = date(year, month, 1)
        end_day = date(year, month, monthrange(year, month)[1])
        date_from, date_to = start_day, end_day
    else:
        date_from, date_to = get_date_range_from_request(request, "date")

    result = []

    for transformer in transformers:
        sales_reps = SalesRepresentative.objects.filter(
            assigned_transformers=transformer
        ).distinct()

        summary = MonthlyCommercialSummary.objects.filter(
            sales_rep__in=sales_reps,
            month__range=(date_from, date_to)
        ).aggregate(
            revenue_billed=Sum("revenue_billed"),
            revenue_collected=Sum("revenue_collected")
        )

        revenue_billed = Decimal(summary["revenue_billed"] or 0)
        revenue_collected = Decimal(summary["revenue_collected"] or 0)

        energy_billed = MonthlyEnergyBilled.objects.filter(
            feeder=feeder,
            month__range=(date_from, date_to)
        ).aggregate(energy=Sum("energy_mwh"))["energy"] or 0

        energy_delivered = EnergyDelivered.objects.filter(
            feeder=feeder,
            date__range=(date_from, date_to)
        ).aggregate(energy=Sum("energy_mwh"))["energy"] or 0

        total_cost = Opex.objects.filter(
            district=transformer.feeder.business_district,
            date__range=(date_from, date_to)
        ).aggregate(total=Sum("credit"))["total"] or 0

        # Calculate ATCC
        try:
            billing_eff = Decimal(energy_billed) / Decimal(energy_delivered) if energy_delivered else Decimal(0)
            collection_eff = revenue_collected / revenue_billed if revenue_billed else Decimal(0)
            atcc = (1 - (billing_eff * collection_eff)) * 100
        except Exception:
            atcc = 0

        result.append({
            "name": transformer.name,
            "slug": transformer.slug,
            "total_cost": round(total_cost, 2),
            "revenue_billed": round(revenue_billed, 2),
            "revenue_collected": round(revenue_collected, 2),
            "atcc": round(atcc, 2),
        })

    return Response(result)


class CustomerBusinessMetricsView(APIView):
    def get(self, request):
        year = int(request.GET.get("year"))
        month = int(request.GET.get("month"))
        state = request.GET.get("state")
        district = request.GET.get("business_district")

        # Build list of 5 months: current + 4 previous
        base_date = date(year, month, 1)
        month_list = [base_date - relativedelta(months=i) for i in reversed(range(5))]

        # Filter queryset by location
        qs = MonthlyCommercialSummary.objects.filter(month__in=month_list)

        if district:
            qs = qs.filter(sales_rep__assigned_transformers__feeder__business_district__name=district)
        elif state:
            qs = qs.filter(sales_rep__assigned_transformers__feeder__business_district__state__name=state)

        results = {
            "customer_response_rate": [],
            "customer_response_metric": [],
            "revenue_billed_per_customer": [],
            "collections_per_customer": []
        }

        for month in month_list:
            data = qs.filter(month=month).aggregate(
                total_customers_billed=Sum("customers_billed"),
                total_customers_responded=Sum("customers_responded"),
                total_revenue_billed=Sum("revenue_billed"),
                total_collections=Sum("revenue_collected")
            )

            # Calculate metrics
            billed = data["total_customers_billed"] or 0
            responded = data["total_customers_responded"] or 0
            revenue = data["total_revenue_billed"] or 0
            collected = data["total_collections"] or 0

            response_rate = round((responded / billed) * 100, 2) if billed else 0
            response_metric = round(responded / billed, 2) if billed else 0
            revenue_per_customer = round(revenue / billed / 1000, 2) if billed else 0  # in '000
            collection_per_customer = round(collected / billed / 1000, 2) if billed else 0  # in '000

            results["customer_response_rate"].append({
                "month": month.strftime("%b"),
                "value": f"{response_rate}%"
            })

            results["customer_response_metric"].append({
                "month": month.strftime("%b"),
                "value": response_metric
            })

            results["revenue_billed_per_customer"].append({
                "month": month.strftime("%b"),
                "value": revenue_per_customer
            })

            results["collections_per_customer"].append({
                "month": month.strftime("%b"),
                "value": collection_per_customer
            })



            energy_data = {
                "energy_delivered": [],
                "energy_billed": [],
                "energy_collected": [],
                "atcc": [],
                "billing_efficiency": [],
                "collection_efficiency": []
            }

            previous_values = {}

            for month in month_list:
                # Get month boundaries
                month_start = month
                next_month = month + relativedelta(months=1)

                # Filter by district or state
                if district:
                    feeder_filter = {
                        "feeder__business_district__name": district
                    }
                elif state:
                    feeder_filter = {
                        "feeder__business_district__state__name": state
                    }
                else:
                    feeder_filter = {}

                # Energy Delivered (Daily)
                ed = EnergyDelivered.objects.filter(
                    date__gte=month_start, date__lt=next_month,
                    **feeder_filter
                ).aggregate(total=Sum("energy_mwh"))["total"] or 0

                # Energy Billed (Monthly)
                eb = MonthlyEnergyBilled.objects.filter(
                    month=month,
                    **feeder_filter
                ).aggregate(total=Sum("energy_mwh"))["total"] or 0

                # Revenue Billed & Collected (Daily)

                # Get the relevant sales reps first
                rep_filter = {}
                if district:
                    rep_filter["assigned_transformers__feeder__business_district__name"] = district
                elif state:
                    rep_filter["assigned_transformers__feeder__business_district__state__name"] = state

                sales_reps = SalesRepresentative.objects.filter(**rep_filter).distinct()

                # Then filter summaries by those reps and month
                revenue_billed = MonthlyCommercialSummary.objects.filter(
                    sales_rep__in=sales_reps,
                    month=month
                ).aggregate(
                    billed=Sum("revenue_billed"),
                    collected=Sum("revenue_collected")
                )

                rb = revenue_billed["billed"] or 0
                rc = revenue_billed["collected"] or 0

            

                # Efficiency Metrics
                billing_eff = (eb / ed) * 100 if ed else 0
                collection_eff = (rc / rb) * 100 if rb else 0
                atcc = 100 - (billing_eff * collection_eff / 100) if billing_eff and collection_eff else 100
                ec = (Decimal(collection_eff) / Decimal("100")) * Decimal(eb)

                def format_metric(metric_name, value):
                    month_str = month.strftime("%b")
                    prev = previous_values.get(metric_name)
                    delta = round(((value - prev) / prev) * 100, 2) if prev and prev != 0 else None
                    previous_values[metric_name] = value
                    return {
                        "month": month_str,
                        "value": round(value, 2),
                        "delta": delta
                    }

                energy_data["energy_delivered"].append(format_metric("energy_delivered", ed))
                energy_data["energy_billed"].append(format_metric("energy_billed", eb))
                energy_data["energy_collected"].append(format_metric("energy_collected", ec))
                energy_data["billing_efficiency"].append(format_metric("billing_efficiency", billing_eff))
                energy_data["collection_efficiency"].append(format_metric("collection_efficiency", collection_eff))
                energy_data["atcc"].append(format_metric("atcc", atcc))

        
        results.update(energy_data)
        return Response(results, status=status.HTTP_200_OK)

class ServiceBandMetricsView(APIView):
    def get(self, request):
        year = int(request.GET.get("year"))
        month = int(request.GET.get("month"))
        state = request.GET.get("state")

        # Target month
        month_start = date(year, month, 1)
        month_end = month_start + relativedelta(months=1)

        results = []

        for band in Band.objects.all().order_by("name"):
            # Shared filter across models
            band_filter = {"feeder__band": band}

            if state:
                band_filter["feeder__business_district__state__name"] = state


            # ENERGY DELIVERED (Daily)
            energy_delivered = EnergyDelivered.objects.filter(
                date__gte=month_start, date__lt=month_end,
                **band_filter
            ).aggregate(total=Sum("energy_mwh"))["total"] or Decimal("0")

            # ENERGY BILLED (Monthly)
            energy_billed = MonthlyEnergyBilled.objects.filter(
                month=month_start,
                **band_filter
            ).aggregate(total=Sum("energy_mwh"))["total"] or Decimal("0")

            # COMMERCIAL SUMMARY (Monthly)
            commercial_data = MonthlyCommercialSummary.objects.filter(
                month=month_start,
                sales_rep__assigned_transformers__feeder__band=band,
                sales_rep__assigned_transformers__feeder__business_district__state__name=state if state else None
            ).aggregate(
                revenue_billed=Sum("revenue_billed"),
                revenue_collected=Sum("revenue_collected"),
                customers_billed=Sum("customers_billed"),
                customers_responded=Sum("customers_responded")
            )

            revenue_billed = commercial_data["revenue_billed"] or Decimal("0")
            revenue_collected = commercial_data["revenue_collected"] or Decimal("0")
            customers_billed = commercial_data["customers_billed"] or 0
            customers_responded = commercial_data["customers_responded"] or 0

            # Derived Metrics
            billing_eff = (energy_billed / energy_delivered * 100) if energy_delivered else 0
            collection_eff = (revenue_collected / revenue_billed * 100) if revenue_billed else 0
            atc_c = 100 - (billing_eff * collection_eff / 100) if billing_eff and collection_eff else 100
            response_rate = (customers_responded / customers_billed * 100) if customers_billed else 0
            energy_collected = energy_billed * (Decimal(collection_eff) / Decimal("100")) if energy_billed else 0

            results.append({
                "band": band.name,
                "energy_delivered": round(energy_delivered, 2),
                "energy_billed": round(energy_billed, 2),
                "energy_collected": round(energy_collected, 2),
                "atc_c": round(atc_c, 2),
                "billing_efficiency": round(billing_eff, 2),
                "collection_efficiency": round(collection_eff, 2),
                "customer_response_rate": round(response_rate, 2)
            })

        return Response(results, status=status.HTTP_200_OK)
