from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date
from dateutil.relativedelta import relativedelta  # type: ignore
from django.db.models import Sum
from django.utils.dateparse import parse_date
from rest_framework.decorators import api_view
from rest_framework.response import Response
from commercial.models import *
from commercial.serializers import *
from common.models import State
from commercial.models import MonthlyCommercialSummary, MonthlyEnergyBilled
from technical.models import EnergyDelivered


def round_two_places(val):
    return Decimal(val).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_realistic_target(metric_type, current_value):
    """
    Get realistic targets based on industry standards and NERC guidelines
    """
    try:
        current_value = Decimal(str(current_value))
        
        if metric_type == "billing_efficiency":
            # Target: 100% billing efficiency (ideal)
            # If current is above 95%, aim for 100%
            # If current is below 95%, aim for gradual improvement (current + 5-10%)
            if current_value >= 95:
                return 100.0
            else:
                improvement = min(Decimal("10"), Decimal("100") - current_value)
                return float(round_two_places(current_value + improvement))
        
        elif metric_type == "collection_efficiency":
            # Target: 100% collection efficiency (ideal)
            # Similar logic to billing efficiency
            if current_value >= 95:
                return 100.0
            else:
                improvement = min(Decimal("10"), Decimal("100") - current_value)
                return float(round_two_places(current_value + improvement))
        
        elif metric_type == "atcc":
            # Target: Minimize AT&C losses (aim for 0-15% based on NERC standards)
            # Good performance: < 15%, Acceptable: < 20%, Poor: > 20%
            if current_value <= 15:
                # Already good, aim to maintain or slightly improve
                return max(0.0, float(round_two_places(current_value - Decimal("2"))))
            elif current_value <= 25:
                # Moderate losses, aim for significant improvement
                return 15.0
            else:
                # High losses, aim for substantial reduction
                reduction = current_value * Decimal("0.2")  # 20% reduction
                return float(round_two_places(current_value - reduction))
        
        elif metric_type == "customer_response_rate":
            # Target: 100% customer response rate (ideal)
            # Similar to efficiency metrics
            if current_value >= 95:
                return 100.0
            else:
                improvement = min(Decimal("10"), Decimal("100") - current_value)
                return float(round_two_places(current_value + improvement))
        
        else:
            # Default: no target for other metrics
            return None
            
    except:
        return None


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

    def get_disco_aggregates(start_date, end_date):
        """Get aggregated data for the entire DisCo"""
        # Get revenue and customer data for entire DisCo
        disco_summary = MonthlyCommercialSummary.objects.filter(
            month__gte=start_date,
            month__lt=end_date,
        ).aggregate(
            revenue_billed=Sum("revenue_billed"),
            revenue_collected=Sum("revenue_collected"),
            customers_billed=Sum("customers_billed"),
            customers_responded=Sum("customers_responded"),
        )

        # Get energy data for entire DisCo
        disco_energy_delivered = EnergyDelivered.objects.filter(
            date__gte=start_date,
            date__lt=end_date
        ).aggregate(Sum("energy_mwh"))["energy_mwh__sum"] or Decimal(0)

        disco_energy_billed = MonthlyEnergyBilled.objects.filter(
            month__gte=start_date,
            month__lt=end_date
        ).aggregate(Sum("energy_mwh"))["energy_mwh__sum"] or Decimal(0)

        return {
            'revenue_billed': disco_summary["revenue_billed"] or Decimal(0),
            'revenue_collected': disco_summary["revenue_collected"] or Decimal(0),
            'customers_billed': disco_summary["customers_billed"] or 0,
            'customers_responded': disco_summary["customers_responded"] or 0,
            'energy_delivered': disco_energy_delivered,
            'energy_billed': disco_energy_billed,
        }

    def get_state_aggregates(state, start_date, end_date):
        """Get aggregated data for a specific state"""
        # Get revenue and customer data by filtering transformers through their feeders
        state_summary = MonthlyCommercialSummary.objects.filter(
            transformer__feeder__business_district__state=state,
            month__gte=start_date,
            month__lt=end_date,
        ).aggregate(
            revenue_billed=Sum("revenue_billed"),
            revenue_collected=Sum("revenue_collected"),
            customers_billed=Sum("customers_billed"),
            customers_responded=Sum("customers_responded"),
        )

        # Get energy data for the state
        state_energy_delivered = EnergyDelivered.objects.filter(
            feeder__business_district__state=state,
            date__gte=start_date,
            date__lt=end_date
        ).aggregate(Sum("energy_mwh"))["energy_mwh__sum"] or Decimal(0)

        state_energy_billed = MonthlyEnergyBilled.objects.filter(
            feeder__business_district__state=state,
            month__gte=start_date,
            month__lt=end_date
        ).aggregate(Sum("energy_mwh"))["energy_mwh__sum"] or Decimal(0)

        return {
            'revenue_billed': state_summary["revenue_billed"] or Decimal(0),
            'revenue_collected': state_summary["revenue_collected"] or Decimal(0),
            'customers_billed': state_summary["customers_billed"] or 0,
            'customers_responded': state_summary["customers_responded"] or 0,
            'energy_delivered': state_energy_delivered,
            'energy_billed': state_energy_billed,
        }

    def calc_metrics(data):
        """Calculate efficiency metrics from aggregated data"""
        try:
            revenue_billed = Decimal(str(data['revenue_billed']))
            revenue_collected = Decimal(str(data['revenue_collected']))
            energy_delivered = Decimal(str(data['energy_delivered']))
            energy_billed = Decimal(str(data['energy_billed']))
            customers_billed = data['customers_billed']
            customers_responded = data['customers_responded']

            # Calculate efficiencies
            billing_eff = (energy_billed / energy_delivered) * 100 if energy_delivered > 0 else Decimal(0)
            collection_eff = (revenue_collected / revenue_billed) * 100 if revenue_billed > 0 else Decimal(0)
            
            # Calculate energy collected using the corrected formula
            energy_collected = energy_billed * (collection_eff / 100) if energy_delivered > 0 else Decimal(0)
            
            # Calculate AT&C losses
            atcc = (Decimal(1) - ((billing_eff / 100) * (collection_eff / 100))) * 100
            
            # Calculate customer metrics
            customer_response_rate = (Decimal(customers_responded) / Decimal(customers_billed)) * 100 if customers_billed > 0 else Decimal(0)
            revenue_per_customer = revenue_billed / Decimal(customers_billed) if customers_billed > 0 else Decimal(0)
            collections_per_customer = revenue_collected / Decimal(customers_billed) if customers_billed > 0 else Decimal(0)

            return {
                'energy_delivered': energy_delivered,
                'energy_billed': energy_billed,
                'energy_collected': energy_collected,
                'revenue_billed': revenue_billed,
                'revenue_collected': revenue_collected,
                'billing_efficiency': billing_eff,
                'collection_efficiency': collection_eff,
                'atcc': atcc,
                'customer_response_rate': customer_response_rate,
                'revenue_per_customer': revenue_per_customer,
                'collections_per_customer': collections_per_customer,
            }
        except (InvalidOperation, ZeroDivisionError):
            return {
                'energy_delivered': Decimal(0),
                'energy_billed': Decimal(0),
                'energy_collected': Decimal(0),
                'revenue_billed': Decimal(0),
                'revenue_collected': Decimal(0),
                'billing_efficiency': Decimal(0),
                'collection_efficiency': Decimal(0),
                'atcc': Decimal(0),
                'customer_response_rate': Decimal(0),
                'revenue_per_customer': Decimal(0),
                'collections_per_customer': Decimal(0),
            }

    def percentage_delta(current, previous):
        """Calculate percentage change between current and previous values"""
        if previous and previous != 0:
            return round(float(((Decimal(str(current)) - Decimal(str(previous))) / Decimal(str(previous))) * 100), 2)
        return None

    # Get DisCo-wide data for current and previous periods
    current_disco = get_disco_aggregates(current_start, current_end)
    previous_disco = get_disco_aggregates(previous_start, previous_end)

    current_disco_metrics = calc_metrics(current_disco)
    previous_disco_metrics = calc_metrics(previous_disco)

    # Build DisCo-wide summary
    disco_summary = {
        "atcc": {
            "actual": float(round_two_places(current_disco_metrics['atcc'])),
            "delta": percentage_delta(current_disco_metrics['atcc'], previous_disco_metrics['atcc']),
            "target": get_realistic_target("atcc", current_disco_metrics['atcc'])
        },
        "billing_efficiency": {
            "actual": float(round_two_places(current_disco_metrics['billing_efficiency'])),
            "delta": percentage_delta(current_disco_metrics['billing_efficiency'], previous_disco_metrics['billing_efficiency']),
            "target": get_realistic_target("billing_efficiency", current_disco_metrics['billing_efficiency'])
        },
        "collection_efficiency": {
            "actual": float(round_two_places(current_disco_metrics['collection_efficiency'])),
            "delta": percentage_delta(current_disco_metrics['collection_efficiency'], previous_disco_metrics['collection_efficiency']),
            "target": get_realistic_target("collection_efficiency", current_disco_metrics['collection_efficiency'])
        },
        "customer_response_rate": {
            "actual": float(round_two_places(current_disco_metrics['customer_response_rate'])),
            "delta": percentage_delta(current_disco_metrics['customer_response_rate'], previous_disco_metrics['customer_response_rate']),
            "target": get_realistic_target("customer_response_rate", current_disco_metrics['customer_response_rate'])
        }
    }

    # Process each state
    state_results = []
    for state in State.objects.all():
        # Current period data
        current_state = get_state_aggregates(state, current_start, current_end)
        current_metrics = calc_metrics(current_state)

        # Previous period data
        previous_state = get_state_aggregates(state, previous_start, previous_end)
        previous_metrics = calc_metrics(previous_state)

        state_results.append({
            "state": state.name,
            "energy_delivered": {
                "actual": float(round_two_places(current_metrics['energy_delivered'])),
                "delta": percentage_delta(current_metrics['energy_delivered'], previous_metrics['energy_delivered'])
            },
            "energy_billed": {
                "actual": float(round_two_places(current_metrics['energy_billed'])),
                "delta": percentage_delta(current_metrics['energy_billed'], previous_metrics['energy_billed'])
            },
            "energy_collected": {
                "actual": float(round_two_places(current_metrics['energy_collected'])),
                "delta": percentage_delta(current_metrics['energy_collected'], previous_metrics['energy_collected'])
            },
            "atcc": {
                "actual": float(round_two_places(current_metrics['atcc'])),
                "delta": percentage_delta(current_metrics['atcc'], previous_metrics['atcc']),
                "target": get_realistic_target("atcc", current_metrics['atcc'])
            },
            "billing_efficiency": {
                "actual": float(round_two_places(current_metrics['billing_efficiency'])),
                "delta": percentage_delta(current_metrics['billing_efficiency'], previous_metrics['billing_efficiency']),
                "target": get_realistic_target("billing_efficiency", current_metrics['billing_efficiency'])
            },
            "collection_efficiency": {
                "actual": float(round_two_places(current_metrics['collection_efficiency'])),
                "delta": percentage_delta(current_metrics['collection_efficiency'], previous_metrics['collection_efficiency']),
                "target": get_realistic_target("collection_efficiency", current_metrics['collection_efficiency'])
            },
            "customer_response_rate": {
                "actual": float(round_two_places(current_metrics['customer_response_rate'])),
                "delta": percentage_delta(current_metrics['customer_response_rate'], previous_metrics['customer_response_rate']),
                "target": get_realistic_target("customer_response_rate", current_metrics['customer_response_rate'])
            },
            "revenue_billed_per_customer": {
                "actual": float(round_two_places(current_metrics['revenue_per_customer'])),
                "delta": percentage_delta(current_metrics['revenue_per_customer'], previous_metrics['revenue_per_customer'])
            },
            "collections_per_customer": {
                "actual": float(round_two_places(current_metrics['collections_per_customer'])),
                "delta": percentage_delta(current_metrics['collections_per_customer'], previous_metrics['collections_per_customer'])
            },
        })

    return Response({
        "disco_summary": disco_summary,
        "states": state_results
    })