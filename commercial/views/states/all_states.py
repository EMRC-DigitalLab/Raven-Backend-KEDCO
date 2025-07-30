import random
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date
from dateutil.relativedelta import relativedelta  # type: ignore
from django.db.models import Sum
from django.utils.dateparse import parse_date
from rest_framework.decorators import  api_view
from rest_framework.response import Response
from commercial.models import *
from commercial.serializers import *
from common.models import State
from commercial.models import MonthlyCommercialSummary, MonthlyEnergyBilled
from technical.models import EnergyDelivered


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