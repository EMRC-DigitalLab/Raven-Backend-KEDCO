# commercial/views/feeders/top_bottom.py
from datetime import date

from dateutil.relativedelta import relativedelta  # type: ignore
from rest_framework.decorators import api_view
from rest_framework.response import Response

from common.models import BusinessDistrict, Feeder, State

from .utils import calculate_atcc_metrics


@api_view(["GET"])
def feeder_performance_view(request):
    year = int(request.query_params.get("year", date.today().year))
    month = int(request.query_params.get("month", date.today().month))
    state_name = request.query_params.get("state")
    district_name = request.query_params.get("business_district")
    
    start_date = date(year, month, 1)
    end_date = start_date + relativedelta(months=1)

    # Build feeder queryset with location filters
    # Business district takes precedence over state
    feeders_qs = Feeder.objects.select_related(
        'business_district', 
        'business_district__state', 
        'substation'
    )
    
    location_info = None
    
    if district_name:
        # Filter by business district (takes precedence)
        try:
            district = BusinessDistrict.objects.select_related('state').get(
                name__iexact=district_name
            )
            feeders_qs = feeders_qs.filter(business_district=district)
            location_info = {
                "type": "business_district",
                "name": district.name,
                "state": district.state.name
            }
        except BusinessDistrict.DoesNotExist:
            return Response({
                "error": f"Business district '{district_name}' not found"
            }, status=400)
            
    elif state_name:
        # Filter by state
        try:
            state = State.objects.get(name__iexact=state_name)
            feeders_qs = feeders_qs.filter(business_district__state=state)
            location_info = {
                "type": "state",
                "name": state.name
            }
        except State.DoesNotExist:
            return Response({
                "error": f"State '{state_name}' not found"
            }, status=400)
    else:
        # No filter - all feeders
        location_info = {
            "type": "all",
            "name": "All Feeders"
        }

    # Get feeders list
    feeders = list(feeders_qs)
    
    if not feeders:
        return Response({
            "error": "No feeders found for the specified criteria"
        }, status=404)

    # Calculate metrics for all feeders
    feeder_data = []
    for feeder in feeders:
        metrics = calculate_atcc_metrics(feeder, start_date, end_date)
        feeder_data.append(metrics)

    # Sort by AT&C losses (lower is better)
    sorted_by_atcc = sorted(feeder_data, key=lambda x: x["atcc"])
    
    # Get top 5 (lowest AT&C losses) and bottom 5 (highest AT&C losses)
    top_5_performers = sorted_by_atcc[:5]
    bottom_5_performers = sorted_by_atcc[-5:][::-1]  # Reverse to show highest first

    return Response({
        "top_5": top_5_performers,
        "bottom_5": bottom_5_performers
    })