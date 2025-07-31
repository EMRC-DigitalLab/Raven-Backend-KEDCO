from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from dateutil.relativedelta import relativedelta # type: ignore
from django.db.models import Sum
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from commercial.models import MonthlyCommercialSummary, MonthlyEnergyBilled
from technical.models import EnergyDelivered, FeederEnergyMonthly, FeederEnergyDaily
from common.models import Feeder
from datetime import timedelta


def safe_round(value, places=2):
    """Safely round decimal values"""
    try:
        if value is None:
            return 0.0
        return float(Decimal(str(value)).quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP))
    except Exception:
        return 0.0


def safe_divide(numerator, denominator):
    """Safely divide two numbers, return 0 if denominator is 0"""
    try:
        num = Decimal(str(numerator or 0))
        den = Decimal(str(denominator or 0))
        return (num / den) if den > 0 else Decimal(0)
    except Exception:
        return Decimal(0)


def calculate_delta(current, previous):
    """Calculate percentage change between current and previous values"""
    if previous in [0, None]:
        return None
    try:
        delta = ((Decimal(str(current)) - Decimal(str(previous))) / Decimal(str(previous))) * 100
        return safe_round(delta)
    except Exception:
        return None


def get_energy_delivered_for_month(feeders, period_start):
    """Get energy delivered using the most efficient method available"""
    try:
        # Try monthly aggregates first (most efficient)
        delivered_mwh = FeederEnergyMonthly.objects.filter(
            feeder__in=feeders,
            period=period_start,
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        if delivered_mwh:
            return Decimal(str(delivered_mwh))
        
        # Fallback to daily aggregation
        period_end = period_start + relativedelta(months=1) - timedelta(days=1)
        delivered_mwh = FeederEnergyDaily.objects.filter(
            feeder__in=feeders,
            date__gte=period_start,
            date__lte=period_end,
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        if delivered_mwh:
            return Decimal(str(delivered_mwh))
            
        # Final fallback to EnergyDelivered
        delivered_mwh = EnergyDelivered.objects.filter(
            feeder__in=feeders,
            date__year=period_start.year,
            date__month=period_start.month,
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        return Decimal(str(delivered_mwh or 0))
        
    except Exception:
        return Decimal(0)


class CustomerBusinessMetricsView(APIView):
    def get(self, request):
        year = int(request.GET.get("year"))
        month = int(request.GET.get("month"))
        state = request.GET.get("state")
        district = request.GET.get("business_district")

        # Build list of 5 months: current + 4 previous
        base_date = date(year, month, 1)
        month_list = [base_date - relativedelta(months=i) for i in reversed(range(5))]
        selected_month = month_list[-1]  # The current selected month
        previous_month = month_list[-2] if len(month_list) > 1 else None

        # Get feeders for the location filter - more efficient than going through sales reps
        feeder_filter = {}
        if district:
            feeder_filter["business_district__name"] = district
        elif state:
            feeder_filter["business_district__state__name"] = state
        
        location_feeders = list(Feeder.objects.filter(**feeder_filter))

        # Build transformer filter for commercial summaries
        transformer_filter = {}
        if district:
            transformer_filter["transformer__feeder__business_district__name"] = district
        elif state:
            transformer_filter["transformer__feeder__business_district__state__name"] = state

        results = {
            "customer_response_rate": [],
            "customer_response_metric": [],
            "revenue_billed_per_customer": [],
            "collections_per_customer": [],
            "energy_delivered": [],
            "energy_billed": [],
            "energy_collected": [],
            "atcc": [],
            "billing_efficiency": [],
            "collection_efficiency": []
        }

        # Store previous month data for delta calculation
        previous_data = {}

        for idx, month_date in enumerate(month_list):
            # === CUSTOMER METRICS ===
            # Get commercial summaries directly through transformers
            commercial_data = MonthlyCommercialSummary.objects.filter(
                month=month_date,
                **transformer_filter
            ).aggregate(
                total_customers_billed=Sum("customers_billed"),
                total_customers_responded=Sum("customers_responded"),
                total_revenue_billed=Sum("revenue_billed"),
                total_collections=Sum("revenue_collected")
            )

            billed = commercial_data["total_customers_billed"] or 0
            responded = commercial_data["total_customers_responded"] or 0
            revenue = commercial_data["total_revenue_billed"] or 0
            collected = commercial_data["total_collections"] or 0

            # Calculate customer metrics
            response_rate = safe_divide(Decimal(responded), Decimal(billed)) * 100
            revenue_per_customer = safe_divide(Decimal(revenue), Decimal(billed)) / 1000  # in '000
            collection_per_customer = safe_divide(Decimal(collected), Decimal(billed)) / 1000  # in '000
            
            # Customer response metric = Collections Per Customer / Revenue Billed Per Customer
            customer_response_metric = safe_divide(collection_per_customer * 1000, revenue_per_customer * 1000)

            # === ENERGY METRICS ===
            # Energy Delivered
            energy_delivered = get_energy_delivered_for_month(location_feeders, month_date)

            # Energy Billed
            energy_billed = MonthlyEnergyBilled.objects.filter(
                feeder__in=location_feeders,
                month=month_date
            ).aggregate(Sum("energy_mwh"))["energy_mwh__sum"] or 0
            energy_billed = Decimal(str(energy_billed))

            # Calculate efficiency metrics
            billing_eff = safe_divide(energy_billed, energy_delivered) * 100
            collection_eff = safe_divide(Decimal(collected), Decimal(revenue)) * 100
            atcc_losses = Decimal(100) - (billing_eff * collection_eff / 100)
            energy_collected = energy_billed * (collection_eff / 100)

            # Store current month data
            current_data = {
                "response_rate": response_rate,
                "customer_response_metric": customer_response_metric,
                "revenue_per_customer": revenue_per_customer,
                "collection_per_customer": collection_per_customer,
                "energy_delivered": energy_delivered,
                "energy_billed": energy_billed,
                "energy_collected": energy_collected,
                "billing_efficiency": billing_eff,
                "collection_efficiency": collection_eff,
                "atcc": atcc_losses
            }

            # Calculate deltas only for the selected month
            deltas = {}
            if month_date == selected_month and previous_data:
                deltas = {
                    "response_rate": calculate_delta(response_rate, previous_data.get("response_rate")),
                    "customer_response_metric": calculate_delta(customer_response_metric, previous_data.get("customer_response_metric")),
                    "revenue_per_customer": calculate_delta(revenue_per_customer, previous_data.get("revenue_per_customer")),
                    "collection_per_customer": calculate_delta(collection_per_customer, previous_data.get("collection_per_customer")),
                    "energy_delivered": calculate_delta(energy_delivered, previous_data.get("energy_delivered")),
                    "energy_billed": calculate_delta(energy_billed, previous_data.get("energy_billed")),
                    "energy_collected": calculate_delta(energy_collected, previous_data.get("energy_collected")),
                    "billing_efficiency": calculate_delta(billing_eff, previous_data.get("billing_efficiency")),
                    "collection_efficiency": calculate_delta(collection_eff, previous_data.get("collection_efficiency")),
                    "atcc": calculate_delta(atcc_losses, previous_data.get("atcc"))
                }

            # Build response data
            month_str = month_date.strftime("%b")
            is_selected_month = month_date == selected_month

            # Customer metrics
            results["customer_response_rate"].append({
                "month": month_str,
                "value": safe_round(response_rate),
                "delta": deltas.get("response_rate") if is_selected_month else None
            })

            results["customer_response_metric"].append({
                "month": month_str,
                "value": safe_round(customer_response_metric),
                "delta": deltas.get("customer_response_metric") if is_selected_month else None
            })

            results["revenue_billed_per_customer"].append({
                "month": month_str,
                "value": safe_round(revenue_per_customer),
                "delta": deltas.get("revenue_per_customer") if is_selected_month else None
            })

            results["collections_per_customer"].append({
                "month": month_str,
                "value": safe_round(collection_per_customer),
                "delta": deltas.get("collection_per_customer") if is_selected_month else None
            })

            # Energy metrics
            results["energy_delivered"].append({
                "month": month_str,
                "value": safe_round(energy_delivered),
                "delta": deltas.get("energy_delivered") if is_selected_month else None
            })

            results["energy_billed"].append({
                "month": month_str,
                "value": safe_round(energy_billed),
                "delta": deltas.get("energy_billed") if is_selected_month else None
            })

            results["energy_collected"].append({
                "month": month_str,
                "value": safe_round(energy_collected),
                "delta": deltas.get("energy_collected") if is_selected_month else None
            })

            results["billing_efficiency"].append({
                "month": month_str,
                "value": safe_round(billing_eff),
                "delta": deltas.get("billing_efficiency") if is_selected_month else None
            })

            results["collection_efficiency"].append({
                "month": month_str,
                "value": safe_round(collection_eff),
                "delta": deltas.get("collection_efficiency") if is_selected_month else None
            })

            results["atcc"].append({
                "month": month_str,
                "value": safe_round(atcc_losses),
                "delta": deltas.get("atcc") if is_selected_month else None
            })

            # Store data for next iteration's delta calculation
            previous_data = current_data.copy()

        return Response(results, status=status.HTTP_200_OK)