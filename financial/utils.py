from django.db.models import Sum
from commercial.models import MonthlyCommercialSummary, SalesRepresentative
from financial.models import Opex
from common.models import Feeder
from commercial.date_filters import get_date_range_from_request
from datetime import date
from calendar import monthrange


def get_financial_feeder_data(request):
    state = request.GET.get("state")
    bd = request.GET.get("business_district")
    mode = request.GET.get("mode", "monthly")  # Default to 'monthly'
    year = request.GET.get("year")
    month = request.GET.get("month")

    # Handle date filtering
    if mode == "monthly" and year and month:
        year = int(year)
        month = int(month)
        start_day = date(year, month, 1)
        end_day = date(year, month, monthrange(year, month)[1])
        date_from, date_to = start_day, end_day
    else:
        date_from, date_to = get_date_range_from_request(request, "date")

    # Apply filtering hierarchy: Business District > State
    feeders = Feeder.objects.all()
    if bd:
        feeders = feeders.filter(business_district__name=bd)
    elif state:
        feeders = feeders.filter(business_district__state__name=state)

    data = []

    for feeder in feeders:
        # Get sales reps linked to this feeder
        sales_reps = SalesRepresentative.objects.filter(
            assigned_transformers__feeder=feeder
        ).distinct()

        # Aggregate commercial summary
        summary = MonthlyCommercialSummary.objects.filter(
            sales_rep__in=sales_reps,
            month__range=(date_from, date_to)
        ).aggregate(
            revenue_billed=Sum("revenue_billed"),
            revenue_collected=Sum("revenue_collected")
        )

        revenue_billed = summary["revenue_billed"] or 0
        revenue_collected = summary["revenue_collected"] or 0

        # Aggregate cost from expenses in feeder's business district
        total_cost = Opex.objects.filter(
            district=feeder.business_district,
            date__range=(date_from, date_to)
        ).aggregate(total=Sum("credit"))["total"] or 0

        bd_obj = feeder.business_district

        data.append({
            "feeder": feeder.name,
            "slug": feeder.slug,
            "business_district": {
                "name": bd_obj.name if bd_obj else None,
                "slug": bd_obj.slug if bd_obj else None,
            },
            "total_cost": round(total_cost, 2),
            "revenue_billed": round(revenue_billed, 2),
            "revenue_collected": round(revenue_collected, 2),
        })

    return data
