from technical.models import EnergyDelivered
from commercial.models import MonthlyEnergyBilled
from commercial.models import MonthlyCommercialSummary
from decimal import Decimal, ROUND_HALF_UP
from django.db.models import (
    Sum
)


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