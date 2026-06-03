# commercial/views/states/single_state.py
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from dateutil.relativedelta import relativedelta  # type: ignore
from django.db.models import Sum
from django.utils.dateparse import parse_date
from rest_framework.decorators import api_view
from rest_framework.response import Response

from commercial.models import *
from commercial.models import MonthlyCommercialSummary, MonthlyEnergyBilled
from commercial.serializers import *
from common.models import DistributionTransformer, Feeder, State
from technical.models import EnergyDelivered, FeederEnergyDaily, FeederEnergyMonthly


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
        energy_collected = billed * (collection_eff / 100)
        
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


def _get_periods(mode: str, year: int, month: int, from_date_str: str, to_date_str: str):
    """
    Build a list of 5 (start, end, label) tuples — current period last.

    mode='monthly' → 5 consecutive calendar months ending on selected month
    mode='weekly'  → 5 consecutive 7-day windows ending on the selected week
    mode='daily'   → 5 consecutive single days ending on the selected day
    """
    today = date.today()

    if mode == 'daily':
        anchor = parse_date(from_date_str or to_date_str) or today
        periods = []
        for i in range(4, -1, -1):
            d = anchor - timedelta(days=i)
            periods.append((d, d, d.strftime('%d %b')))
        return periods

    if mode == 'weekly':
        # Use to_date as the last day of the selected week
        anchor = parse_date(to_date_str) or today
        periods = []
        for i in range(4, -1, -1):
            w_end   = anchor - timedelta(weeks=i)
            w_start = w_end  - timedelta(days=6)
            periods.append((w_start, w_end, f"{w_start.strftime('%d %b')} – {w_end.strftime('%d %b')}"))
        return periods

    # default: monthly
    anchor = date(year, month, 1)
    periods = []
    for i in range(4, -1, -1):
        m_start = anchor - relativedelta(months=i)
        m_end   = m_start + relativedelta(months=1) - timedelta(days=1)
        periods.append((m_start, m_end, m_start.strftime('%b %Y')))
    return periods


def _fetch_period_data(state_feeders, state_transformers, p_start: date, p_end: date, mode: str):
    """
    Fetch and compute all commercial metrics for a single period window.
    Monthly mode uses the pre-aggregated monthly tables; daily/weekly use daily tables.
    """
    if mode == 'monthly':
        delivered_mwh   = get_energy_delivered(state_feeders, p_start)
        billed_mwh      = get_energy_billed(state_feeders, p_start)
        commercial_data = get_commercial_data(state_transformers, p_start)
    else:
        # Daily/weekly: aggregate FeederEnergyDaily for the date range
        delivered_mwh = Decimal(str(
            FeederEnergyDaily.objects.filter(
                feeder__in=state_feeders,
                date__gte=p_start,
                date__lte=p_end,
            ).aggregate(Sum('energy_mwh'))['energy_mwh__sum'] or 0
        ))

        # Billing and commercial data is inherently monthly; use the month the period falls in
        month_start = date(p_start.year, p_start.month, 1)
        billed_mwh      = get_energy_billed(state_feeders, month_start)
        commercial_data = get_commercial_data(state_transformers, month_start)

    return delivered_mwh, billed_mwh, commercial_data


@api_view(["GET"])
def commercial_state_metrics_view(request):
    state_name    = request.query_params.get("state")
    mode          = request.query_params.get("mode", "monthly")
    year          = int(request.query_params.get("year", date.today().year))
    month         = int(request.query_params.get("month", date.today().month))
    from_date_str = request.query_params.get("from_date")
    to_date_str   = request.query_params.get("to_date")

    state = State.objects.filter(name__iexact=state_name).first()
    if not state:
        return Response({"error": "Invalid state"}, status=400)

    state_feeders      = list(Feeder.objects.filter(business_district__state=state))
    state_transformers = list(DistributionTransformer.objects.filter(
        feeder__business_district__state=state
    ))

    # 5 periods: [oldest … newest] — current period is last
    periods = _get_periods(mode, year, month, from_date_str, to_date_str)

    all_periods = []
    for p_start, p_end, label in periods:
        delivered_mwh, billed_mwh, commercial_data = _fetch_period_data(
            state_feeders, state_transformers, p_start, p_end, mode
        )
        metrics = calculate_metrics(
            delivered_mwh,
            billed_mwh,
            commercial_data['revenue_billed'],
            commercial_data['revenue_collected'],
            commercial_data['customers_billed'],
            commercial_data['customers_responded'],
        )

        all_periods.append({
            "period_label":              label,
            "period_start":              str(p_start),
            "period_end":                str(p_end),
            "energy_delivered":          safe_round(delivered_mwh),
            "energy_billed":             safe_round(billed_mwh),
            "energy_collected":          safe_round(metrics['energy_collected']),
            "revenue_billed":            safe_round(commercial_data['revenue_billed']),
            "revenue_collected":         safe_round(commercial_data['revenue_collected']),
            "billing_efficiency":        safe_round(metrics['billing_efficiency']),
            "collection_efficiency":     safe_round(metrics['collection_efficiency']),
            "atcc_losses":               safe_round(metrics['atcc_losses']),
            "customer_response_rate":    safe_round(metrics['response_rate']),
            "customer_response_metric":  safe_round(metrics['customer_response_metric']),
            "revenue_billed_per_customer": safe_round(metrics['revenue_per_cust']),
            "collections_per_customer":  safe_round(metrics['collection_per_cust']),
            "customers_billed":          commercial_data['customers_billed'],
            "customers_responded":       commercial_data['customers_responded'],
        })

    current_period = all_periods[-1]
    trend_periods  = all_periods[:-1]   # 4 previous periods — bars in the metric cards

    if trend_periods:
        prev = trend_periods[-1]
        current_period["deltas"] = {
            "energy_delivered":            compute_delta(current_period["energy_delivered"],            prev["energy_delivered"]),
            "energy_billed":               compute_delta(current_period["energy_billed"],               prev["energy_billed"]),
            "energy_collected":            compute_delta(current_period["energy_collected"],            prev["energy_collected"]),
            "revenue_collected":           compute_delta(current_period["revenue_collected"],           prev["revenue_collected"]),
            "billing_efficiency":          compute_delta(current_period["billing_efficiency"],          prev["billing_efficiency"]),
            "collection_efficiency":       compute_delta(current_period["collection_efficiency"],       prev["collection_efficiency"]),
            "atcc_losses":                 compute_delta(current_period["atcc_losses"],                 prev["atcc_losses"]),
            "customer_response_rate":      compute_delta(current_period["customer_response_rate"],      prev["customer_response_rate"]),
            "revenue_billed_per_customer": compute_delta(current_period["revenue_billed_per_customer"], prev["revenue_billed_per_customer"]),
            "collections_per_customer":    compute_delta(current_period["collections_per_customer"],    prev["collections_per_customer"]),
        }

    return Response({
        "mode":    mode,
        "current": current_period,
        "trend":   trend_periods,
    })