from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta  # type: ignore
from django.db.models import Sum
from rest_framework import status
from rest_framework.response import Response
from rest_framework.decorators import api_view
from financial.models import *
from financial.serializers import *
from common.models import DistributionTransformer
from commercial.models import MonthlyCommercialSummary, SalesRepresentative
from django.db.models import Sum
from rest_framework.decorators import api_view
from rest_framework.response import Response
from dateutil.relativedelta import relativedelta # type: ignore
from commercial.models import MonthlyCommercialSummary
from commercial.models import SalesRepresentative
from datetime import datetime, timedelta
from rest_framework import status


def calculate_percentage_change(current_value, previous_value):
    """Calculate percentage change between two values"""
    if previous_value == 0:
        return 100 if current_value > 0 else 0
    return ((current_value - previous_value) / previous_value) * 100


@api_view(["GET"])
def sales_rep_performance_view(request, rep_id):
    try:
        rep = SalesRepresentative.objects.get(id=rep_id)
    except SalesRepresentative.DoesNotExist:
        return Response({"error": "Sales rep not found."}, status=status.HTTP_404_NOT_FOUND)

    mode = request.GET.get("mode", "monthly")
    year = int(request.GET.get("year", datetime.now().year))
    month = int(request.GET.get("month", datetime.now().month))
    transformer_name = request.GET.get("transformer")  # New parameter

    start_date = datetime(year, month, 1)
    end_date = (start_date + relativedelta(months=1)) - timedelta(days=1)
    
    # Previous month dates for delta calculations
    prev_month_start = start_date - relativedelta(months=1)
    prev_month_end = (prev_month_start + relativedelta(months=1)) - timedelta(days=1)

    # Base query filter for sales rep
    base_filter = {"sales_rep": rep}
    
    # If transformer is specified, add it to the filter
    transformer_obj = None
    if transformer_name:
        try:
            transformer_obj = DistributionTransformer.objects.get(
                name__iexact=transformer_name,
                id__in=rep.assigned_transformers.values_list('id', flat=True)
            )
            base_filter["transformer"] = transformer_obj
        except DistributionTransformer.DoesNotExist:
            return Response({
                "error": f"Transformer '{transformer_name}' not found or not assigned to this sales rep."
            }, status=status.HTTP_404_NOT_FOUND)
        except DistributionTransformer.MultipleObjectsReturned:
            return Response({
                "error": f"Multiple transformers found with name '{transformer_name}'. Please be more specific."
            }, status=status.HTTP_400_BAD_REQUEST)

    # Current month summary
    current_summary = MonthlyCommercialSummary.objects.filter(
        **base_filter,
        month__range=(start_date, end_date)
    ).aggregate(
        revenue_billed=Sum("revenue_billed"),
        revenue_collected=Sum("revenue_collected"),
        customers_billed=Sum("customers_billed"),
        customers_responded=Sum("customers_responded"),
    )

    # Previous month summary for delta calculations
    previous_summary = MonthlyCommercialSummary.objects.filter(
        **base_filter,
        month__range=(prev_month_start, prev_month_end)
    ).aggregate(
        revenue_billed=Sum("revenue_billed"),
        revenue_collected=Sum("revenue_collected"),
        customers_billed=Sum("customers_billed"),
        customers_responded=Sum("customers_responded"),
    )

    # Current month values
    revenue_billed = current_summary["revenue_billed"] or 0
    revenue_collected = current_summary["revenue_collected"] or 0
    customers_billed = current_summary["customers_billed"] or 0
    customers_responded = current_summary["customers_responded"] or 0
    outstanding_billed = revenue_billed - revenue_collected

    # Previous month values
    prev_revenue_billed = previous_summary["revenue_billed"] or 0
    prev_revenue_collected = previous_summary["revenue_collected"] or 0
    prev_customers_billed = previous_summary["customers_billed"] or 0
    prev_customers_responded = previous_summary["customers_responded"] or 0
    prev_outstanding_billed = prev_revenue_billed - prev_revenue_collected

    # Calculate additional metrics
    days_in_month = (end_date - start_date).days + 1
    daily_run_rate = revenue_collected / days_in_month if days_in_month > 0 else 0
    
    prev_days_in_month = (prev_month_end - prev_month_start).days + 1
    prev_daily_run_rate = prev_revenue_collected / prev_days_in_month if prev_days_in_month > 0 else 0
    
    collections_on_outstanding = 0  # Placeholder as requested
    prev_collections_on_outstanding = 0  # Placeholder
    
    # Using customers_billed as active accounts
    active_accounts = customers_billed
    prev_active_accounts = prev_customers_billed
    
    # Using customers_billed - customers_responded as suspended accounts
    suspended_accounts = max(0, customers_billed - customers_responded)
    prev_suspended_accounts = max(0, prev_customers_billed - prev_customers_responded)

    # Calculate deltas (percentage changes)
    revenue_billed_delta = calculate_percentage_change(revenue_billed, prev_revenue_billed)
    revenue_collected_delta = calculate_percentage_change(revenue_collected, prev_revenue_collected)
    outstanding_billed_delta = calculate_percentage_change(outstanding_billed, prev_outstanding_billed)
    daily_run_rate_delta = calculate_percentage_change(daily_run_rate, prev_daily_run_rate)
    collections_on_outstanding_delta = calculate_percentage_change(collections_on_outstanding, prev_collections_on_outstanding)
    active_accounts_delta = calculate_percentage_change(active_accounts, prev_active_accounts)
    suspended_accounts_delta = calculate_percentage_change(suspended_accounts, prev_suspended_accounts)

    # All-time summary (filtered by transformer if specified)
    all_time_summary = MonthlyCommercialSummary.objects.filter(**base_filter).aggregate(
        all_time_billed=Sum("revenue_billed"),
        all_time_collected=Sum("revenue_collected")
    )
    outstanding_all_time = (all_time_summary["all_time_billed"] or 0) - (all_time_summary["all_time_collected"] or 0)

    # ---- Previous 4 Months (excluding current month) ---- #
    monthly_summaries = []
    for i in range(1, 5):  # Start from 1 to exclude current month, go to 5 to get 4 months
        ref_date = start_date - relativedelta(months=i)
        month_start = ref_date.replace(day=1)
        month_end = (month_start + relativedelta(months=1)) - timedelta(days=1)

        summary = MonthlyCommercialSummary.objects.filter(
            **base_filter,
            month__range=(month_start, month_end)
        ).aggregate(
            revenue_billed=Sum("revenue_billed") or 0,
            revenue_collected=Sum("revenue_collected") or 0,
        )

        billed = summary["revenue_billed"] or 0
        collected = summary["revenue_collected"] or 0
        outstanding = billed - collected

        monthly_summaries.append({
            "month": month_start.strftime("%b"),
            "revenue_billed": billed,
            "revenue_collected": collected,
            "outstanding_billed": outstanding
        })

    monthly_summaries.reverse()  # Reverse to show oldest to newest

    # Prepare response data
    response_data = {
        "sales_rep": {
            "id": str(rep.id),
            "name": rep.name
        },
        "current": {
            "revenue_billed": {
                "value": revenue_billed,
                "delta": round(revenue_billed_delta, 2)
            },
            "revenue_collected": {
                "value": revenue_collected,
                "delta": round(revenue_collected_delta, 2)
            },
            "outstanding_billed": {
                "value": outstanding_billed,
                "delta": round(outstanding_billed_delta, 2)
            },
            "daily_run_rate": {
                "value": round(daily_run_rate, 2),
                "delta": round(daily_run_rate_delta, 2)
            },
            "collections_on_outstanding": {
                "value": collections_on_outstanding,
                "delta": round(collections_on_outstanding_delta, 2)
            },
            "active_accounts": {
                "value": active_accounts,
                "delta": round(active_accounts_delta, 2)
            },
            "suspended_accounts": {
                "value": suspended_accounts,
                "delta": round(suspended_accounts_delta, 2)
            }
        },
        "outstanding_all_time": outstanding_all_time,
        "previous_months": monthly_summaries
    }

    # Add transformer info to response if filtered
    if transformer_obj:
        response_data["filtered_by_transformer"] = {
            "id": str(transformer_obj.id),
            "name": transformer_obj.name,
            "feeder": transformer_obj.feeder.name if transformer_obj.feeder else None,
            "business_district": transformer_obj.feeder.business_district.name if transformer_obj.feeder and transformer_obj.feeder.business_district else None,
            "state": transformer_obj.feeder.business_district.state.name if transformer_obj.feeder and transformer_obj.feeder.business_district and transformer_obj.feeder.business_district.state else None
        }
    else:
        response_data["filtered_by_transformer"] = None

    return Response(response_data)