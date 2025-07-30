import random
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date
from dateutil.relativedelta import relativedelta  # type: ignore
from django.db.models import Sum
from django.utils.dateparse import parse_date
from rest_framework.decorators import api_view
from rest_framework.response import Response
from commercial.models import *
from commercial.serializers import *
from common.models import Feeder, State
from commercial.models import MonthlyCommercialSummary, MonthlyEnergyBilled
from technical.models import EnergyDelivered, FeederEnergyMonthly, FeederEnergyDaily
from datetime import timedelta

def round_two_places(val):
    return Decimal(val).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def smart_target(value, variation=0.1):
    try:
        value = Decimal(str(value))  # ensure input is Decimal
        percent_shift = Decimal(str(random.uniform(-variation, variation)))
        return float(round_two_places(value * (Decimal("1") + percent_shift)))
    except:
        return 0  
    
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