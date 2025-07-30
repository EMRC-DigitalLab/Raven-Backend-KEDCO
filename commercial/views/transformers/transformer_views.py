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