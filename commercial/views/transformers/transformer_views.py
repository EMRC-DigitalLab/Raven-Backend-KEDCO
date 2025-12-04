# commercial/views/transformers/transformer_views.py
from decimal import Decimal
from datetime import date
from django.db.models import Sum
from rest_framework.decorators import api_view
from rest_framework.response import Response
from commercial.models import *
from commercial.serializers import *
from common.models import Feeder
from commercial.models import MonthlyCommercialSummary, MonthlyEnergyBilled
from commercial.date_filters import get_date_range_from_request
from technical.models import EnergyDelivered
from financial.models import Opex
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
            transformer=transformer,
            month__range=(date_from, date_to)
        ).aggregate(
            revenue_billed=Sum("revenue_billed"),
            revenue_collected=Sum("revenue_collected")
        )

        revenue_billed = Decimal(summary["revenue_billed"] or 0)
        revenue_collected = Decimal(summary["revenue_collected"] or 0)

        # Get energy billed for this specific transformer's feeder
        energy_billed = MonthlyEnergyBilled.objects.filter(
            feeder=feeder,
            month__range=(date_from, date_to)
        ).aggregate(energy=Sum("energy_mwh"))["energy"] or 0

        # Get energy delivered for this specific transformer's feeder
        energy_delivered = EnergyDelivered.objects.filter(
            feeder=feeder,
            date__range=(date_from, date_to)
        ).aggregate(energy=Sum("energy_mwh"))["energy"] or 0

        # Calculate efficiencies
        try:
            billing_eff = Decimal(energy_billed) / Decimal(energy_delivered) if energy_delivered else Decimal(0)
            collection_eff = revenue_collected / revenue_billed if revenue_billed else Decimal(0)
            
            # Cap efficiencies at 100%
            billing_eff = min(billing_eff, Decimal(1))
            collection_eff = min(collection_eff, Decimal(1))
            
            # Calculate energy collected: Energy Delivered × Collection Efficiency
            energy_collected = Decimal(energy_billed) * collection_eff if energy_delivered else Decimal(0)
            
            # Calculate ATCC: 1 - (Billing Efficiency × Collection Efficiency)
            atcc = (1 - (billing_eff * collection_eff)) * 100
            
        except Exception:
            billing_eff = collection_eff = Decimal(0)
            energy_collected = Decimal(0)
            atcc = 100  # 100% losses if calculation fails

        result.append({
            "name": transformer.name,
            "slug": transformer.slug,
            "energy_delivered": round(float(energy_delivered), 2),
            "energy_billed": round(float(energy_billed), 2), 
            "energy_collected": round(float(energy_collected), 2),
            "atcc": round(float(atcc), 2),
        })

    return Response(result)