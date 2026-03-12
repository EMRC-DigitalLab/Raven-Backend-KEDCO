from calendar import monthrange
from datetime import date

from django.db.models import Sum
from rest_framework.decorators import api_view
from rest_framework.response import Response

from commercial.date_filters import get_date_range_from_request
from commercial.models import (
    MonthlyCommercialSummary,
    SalesRepresentative,
)
from common.models import Feeder
from financial.models import *
from financial.models import Opex
from financial.serializers import *


@api_view(['GET'])
def financial_transformer_view(request):
    feeder_slug = request.GET.get("feeder")
    mode = request.GET.get("mode", "monthly")
    year = request.GET.get("year")
    month = request.GET.get("month")

    if not feeder_slug:
        return Response({"error": "Missing feeder slug."}, status=400)

    try:
        feeder = Feeder.objects.get(slug=feeder_slug)
    except Feeder.DoesNotExist:
        return Response({"error": "Feeder not found."}, status=404)

    # Handle date filters
    if mode == "monthly" and year and month:
        year = int(year)
        month = int(month)
        start_day = date(year, month, 1)
        end_day = date(year, month, monthrange(year, month)[1])
        date_from, date_to = start_day, end_day
    else:
        date_from, date_to = get_date_range_from_request(request, "date")

    transformer_data = []
    for transformer in feeder.transformers.all():
        reps = SalesRepresentative.objects.filter(
            assigned_transformers=transformer
        ).distinct()

        summary = MonthlyCommercialSummary.objects.filter(
            sales_rep__in=reps,
            month__range=(date_from, date_to)
        ).aggregate(
            revenue_billed=Sum("revenue_billed"),
            revenue_collected=Sum("revenue_collected")
        )

        revenue_billed = summary["revenue_billed"] or 0
        revenue_collected = summary["revenue_collected"] or 0

        total_cost = Opex.objects.filter(
            district=feeder.business_district,
            date__range=(date_from, date_to)
        ).aggregate(total=Sum("credit"))["total"] or 0

        transformer_data.append({
            "transformer": transformer.name,
            "slug": transformer.slug,
            "total_cost": round(total_cost, 2),
            "revenue_billed": round(revenue_billed, 2),
            "revenue_collected": round(revenue_collected, 2),
            "atcc": 6
        })

    return Response({
        "feeder": feeder.name,
        "slug": feeder.slug,
        "transformers": transformer_data
    })