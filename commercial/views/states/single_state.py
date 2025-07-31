from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date
from dateutil.relativedelta import relativedelta # type: ignore
from django.db.models import Sum
from django.utils.dateparse import parse_date
from rest_framework.decorators import api_view
from rest_framework.response import Response
from commercial.models import *
from commercial.serializers import *
from common.models import Feeder, State, DistributionTransformer
from commercial.models import MonthlyCommercialSummary, MonthlyEnergyBilled
from technical.models import EnergyDelivered, FeederEnergyMonthly, FeederEnergyDaily
from datetime import timedelta


def round_two_places(val):
    return Decimal(val).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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


def compute_delta(current, previous):
    if previous in [0, None]:
        return None
    try:
        delta = ((Decimal(current) - Decimal(previous)) / Decimal(previous)) * 100
        return float(round_two_places(delta))
    except Exception:
        return None


def get_energy_delivered(state_feeders, period_start):
    """Get energy delivered using the most efficient method available"""
    try:
        # Try monthly aggregates first (most efficient)
        delivered_mwh = FeederEnergyMonthly.objects.filter(
            feeder__in=state_feeders,
            period=period_start,
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        if delivered_mwh:
            return Decimal(str(delivered_mwh))
        
        # Fallback to daily aggregation
        period_end = period_start + relativedelta(months=1) - timedelta(days=1)
        delivered_mwh = FeederEnergyDaily.objects.filter(
            feeder__in=state_feeders,
            date__gte=period_start,
            date__lte=period_end,
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        if delivered_mwh:
            return Decimal(str(delivered_mwh))
            
        # Final fallback to EnergyDelivered
        delivered_mwh = EnergyDelivered.objects.filter(
            feeder__in=state_feeders,
            date__year=period_start.year,
            date__month=period_start.month,
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        return Decimal(str(delivered_mwh or 0))
        
    except Exception:
        return Decimal(0)


def get_energy_billed(state_feeders, period_start):
    """Get energy billed for the given feeders and period"""
    try:
        billed_mwh = MonthlyEnergyBilled.objects.filter(
            feeder__in=state_feeders,
            month=period_start,
        ).aggregate(Sum("energy_mwh"))['energy_mwh__sum']
        
        return Decimal(str(billed_mwh or 0))
    except Exception:
        return Decimal(0)


def get_commercial_data(state_transformers, period_start):
    """Get commercial summary data for the given transformers and period"""
    try:
        summaries = MonthlyCommercialSummary.objects.filter(
            transformer__in=state_transformers,
            month=period_start,
        ).aggregate(
            revenue_collected=Sum("revenue_collected"),
            revenue_billed=Sum("revenue_billed"),
            customers_billed=Sum("customers_billed"),
            customers_responded=Sum("customers_responded"),
        )
        
        return {
            'revenue_billed': Decimal(str(summaries['revenue_billed'] or 0)),
            'revenue_collected': Decimal(str(summaries['revenue_collected'] or 0)),
            'customers_billed': summaries['customers_billed'] or 0,
            'customers_responded': summaries['customers_responded'] or 0,
        }
    except Exception:
        return {
            'revenue_billed': Decimal(0),
            'revenue_collected': Decimal(0),
            'customers_billed': 0,
            'customers_responded': 0,
        }


def calculate_metrics(delivered, billed, revenue_billed, revenue_collected, customers_billed, customers_responded):
    """Calculate all efficiency and performance metrics"""
    try:
        # Billing efficiency = (Energy Billed / Energy Delivered) * 100
        billing_eff = safe_divide(billed, delivered) * 100
        
        # Collection efficiency = (Revenue Collected / Revenue Billed) * 100
        collection_eff = safe_divide(revenue_collected, revenue_billed) * 100
        
        # AT&C losses = 100% - (Billing Efficiency * Collection Efficiency / 100)
        atcc_losses = Decimal(100) - (billing_eff * collection_eff / 100)
        
        # Energy collected = Energy Delivered * (Collection Efficiency / 100)
        energy_collected = delivered * (collection_eff / 100)
        
        # Customer response rate = (Customers Responded / Customers Billed) * 100
        response_rate = safe_divide(Decimal(customers_responded), Decimal(customers_billed)) * 100
        
        # Revenue per customer = Revenue Billed / Customers Billed
        revenue_per_cust = safe_divide(revenue_billed, Decimal(customers_billed))
        
        # Collections per customer = Revenue Collected / Customers Billed  
        collection_per_cust = safe_divide(revenue_collected, Decimal(customers_billed))
        
        # Customer response metric = Collections Per Customer / Revenue Billed Per Customer
        customer_response_metric = safe_divide(collection_per_cust, revenue_per_cust)
        
        return {
            'billing_efficiency': billing_eff,
            'collection_efficiency': collection_eff,
            'atcc_losses': atcc_losses,
            'energy_collected': energy_collected,
            'response_rate': response_rate,
            'revenue_per_cust': revenue_per_cust,
            'collection_per_cust': collection_per_cust,
            'customer_response_metric': customer_response_metric,
        }
        
    except Exception:
        return {
            'billing_efficiency': Decimal(0),
            'collection_efficiency': Decimal(0),
            'atcc_losses': Decimal(0),
            'energy_collected': Decimal(0),
            'response_rate': Decimal(0),
            'revenue_per_cust': Decimal(0),
            'collection_per_cust': Decimal(0),
            'customer_response_metric': Decimal(0),
        }


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

    selected_date = date(year, month, 1) if mode == "monthly" else parse_date(to_date) or date.today()
    months = generate_month_list(selected_date)

    # Pre-fetch all feeders and transformers for this state (optimize queries)
    state_feeders = list(Feeder.objects.filter(business_district__state=state))
    state_transformers = list(DistributionTransformer.objects.filter(
        feeder__business_district__state=state
    ))

    data = []
    previous_month_data = None
    current_month_index = len(months) - 1  # Index of the current (latest) month

    for idx, m in enumerate(months):
        period_start = date(m.year, m.month, 1)
        
        # Get all required data
        delivered_mwh = get_energy_delivered(state_feeders, period_start)
        billed_mwh = get_energy_billed(state_feeders, period_start)
        commercial_data = get_commercial_data(state_transformers, period_start)
        
        # Calculate metrics
        metrics = calculate_metrics(
            delivered_mwh,
            billed_mwh,
            commercial_data['revenue_billed'],
            commercial_data['revenue_collected'],
            commercial_data['customers_billed'],
            commercial_data['customers_responded']
        )

        current = {
            "month": m.strftime("%b"),
            "year": m.year,
            "energy_delivered": safe_round(delivered_mwh),
            "energy_billed": safe_round(billed_mwh),
            "energy_collected": safe_round(metrics['energy_collected']),
            "revenue_collected": safe_round(commercial_data['revenue_collected']),
            "billing_efficiency": safe_round(metrics['billing_efficiency']),
            "collection_efficiency": safe_round(metrics['collection_efficiency']),
            "atcc_losses": safe_round(metrics['atcc_losses']),
            "customer_response_rate": safe_round(metrics['response_rate']),
            "customer_response_metric": safe_round(metrics['customer_response_metric']),
            "revenue_billed_per_customer": safe_round(metrics['revenue_per_cust']),
            "collections_per_customer": safe_round(metrics['collection_per_cust']),
            "customers_billed": commercial_data['customers_billed'],
            "customers_responded": commercial_data['customers_responded'],
        }

        # Only add deltas for the current (latest) month for performance
        if idx == current_month_index and previous_month_data:
            current["deltas"] = {
                "energy_delivered": compute_delta(current["energy_delivered"], previous_month_data["energy_delivered"]),
                "energy_billed": compute_delta(current["energy_billed"], previous_month_data["energy_billed"]),
                "energy_collected": compute_delta(current["energy_collected"], previous_month_data["energy_collected"]),
                "revenue_collected": compute_delta(current["revenue_collected"], previous_month_data["revenue_collected"]),
                "billing_efficiency": compute_delta(current["billing_efficiency"], previous_month_data["billing_efficiency"]),
                "collection_efficiency": compute_delta(current["collection_efficiency"], previous_month_data["collection_efficiency"]),
                "atcc_losses": compute_delta(current["atcc_losses"], previous_month_data["atcc_losses"]),
                "customer_response_rate": compute_delta(current["customer_response_rate"], previous_month_data["customer_response_rate"]),
                "customer_response_metric": compute_delta(current["customer_response_metric"], previous_month_data["customer_response_metric"]),
                "revenue_billed_per_customer": compute_delta(current["revenue_billed_per_customer"], previous_month_data["revenue_billed_per_customer"]),
                "collections_per_customer": compute_delta(current["collections_per_customer"], previous_month_data["collections_per_customer"]),
            }

        previous_month_data = current.copy()
        data.append(current)

    return Response(data)