from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.db.models import Sum
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from commercial.models import MonthlyCommercialSummary, DailyCollection
from common.models import State, BusinessDistrict, DistributionTransformer, Feeder


def safe_round(value, places=2):
    """Safely round decimal values"""
    try:
        if value is None:
            return 0.0
        return float(Decimal(str(value)).quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP))
    except Exception:
        return 0.0


class DailyCollectionsByMonthView(APIView):
    def get(self, request):
        try:
            year = int(request.GET.get("year"))
            month = int(request.GET.get("month"))
            start_date = date(year, month, 1)
        except (TypeError, ValueError):
            return Response({"error": "Valid 'year' and 'month' query parameters are required."},
                            status=status.HTTP_400_BAD_REQUEST)

        # Get filtering parameters
        state_name = request.GET.get("state")
        district_name = request.GET.get("business_district")
        feeder_slug = request.GET.get("feeder")

        # Get last day of the month
        next_month = (start_date.replace(day=28) + timedelta(days=4)).replace(day=1)
        end_date = next_month - timedelta(days=1)

        # Build transformer filter based on location parameters
        transformer_filter = {}
        location_info = {"type": "global"}

        if feeder_slug:
            # Feeder filtering takes highest precedence
            try:
                feeder = Feeder.objects.get(slug=feeder_slug)
                transformer_filter = {"transformer__feeder": feeder}
                location_info = {
                    "type": "feeder", 
                    "name": feeder.name,
                    "district": feeder.business_district.name if feeder.business_district else None,
                    "state": feeder.business_district.state.name if feeder.business_district else None
                }
            except Feeder.DoesNotExist:
                return Response({"error": f"Feeder with slug '{feeder_slug}' not found"}, 
                              status=status.HTTP_404_NOT_FOUND)
        elif district_name:
            # Business district filtering takes precedence over state
            try:
                district = BusinessDistrict.objects.get(name__iexact=district_name)
                transformer_filter = {"transformer__feeder__business_district": district}
                location_info = {"type": "district", "name": district.name, "state": district.state.name}
            except BusinessDistrict.DoesNotExist:
                return Response({"error": f"Business district '{district_name}' not found"}, 
                              status=status.HTTP_404_NOT_FOUND)
        elif state_name:
            # State filtering
            try:
                state = State.objects.get(name__iexact=state_name)
                transformer_filter = {"transformer__feeder__business_district__state": state}
                location_info = {"type": "state", "name": state.name}
            except State.DoesNotExist:
                return Response({"error": f"State '{state_name}' not found"}, 
                              status=status.HTTP_404_NOT_FOUND)

        results = []
        current_day = start_date

        while current_day <= end_date:
            # Try to get daily collections first (more granular and accurate)
            daily_collections = 0
            if transformer_filter:
                # Filtered by location (state, district, or feeder)
                daily_collections = DailyCollection.objects.filter(
                    date=current_day,
                    **transformer_filter
                ).aggregate(total=Sum("amount"))["total"] or 0
            else:
                # Global collections
                daily_collections = DailyCollection.objects.filter(
                    date=current_day
                ).aggregate(total=Sum("amount"))["total"] or 0

            # Fallback to MonthlyCommercialSummary if no daily data
            monthly_collections = 0
            if daily_collections == 0:
                if transformer_filter:
                    # Filtered by location (state, district, or feeder)
                    monthly_collections = MonthlyCommercialSummary.objects.filter(
                        month=date(current_day.year, current_day.month, 1),
                        **transformer_filter
                    ).aggregate(total=Sum("revenue_collected"))["total"] or 0
                else:
                    # Global collections
                    monthly_collections = MonthlyCommercialSummary.objects.filter(
                        month=date(current_day.year, current_day.month, 1)
                    ).aggregate(total=Sum("revenue_collected"))["total"] or 0
                
                # Show entire monthly value on the first day of the month only
                if current_day.day == 1:
                    daily_collections = monthly_collections
                else:
                    daily_collections = 0

            # Use daily collections if available, otherwise use monthly logic
            total_collections = daily_collections

            results.append({
                "day": current_day.day,
                "value": safe_round(total_collections)
            })

            current_day += timedelta(days=1)

        # Add metadata about filtering
        response_data = results

        return Response(response_data, status=status.HTTP_200_OK)