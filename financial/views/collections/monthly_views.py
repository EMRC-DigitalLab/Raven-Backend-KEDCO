from datetime import date, timedelta
from django.db.models import Sum
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from financial.models import *
from financial.serializers import *
from commercial.models import MonthlyCommercialSummary
from django.db.models import Sum
from rest_framework.response import Response
from datetime import date
from commercial.models import MonthlyCommercialSummary
from datetime import timedelta
from rest_framework import status


class DailyCollectionsByMonthView(APIView):
    def get(self, request):
        try:
            year = int(request.GET.get("year"))
            month = int(request.GET.get("month"))
            start_date = date(year, month, 1)
        except (TypeError, ValueError):
            return Response({"error": "Valid 'year' and 'month' query parameters are required."},
                            status=status.HTTP_400_BAD_REQUEST)

        # Get last day of the month
        next_month = (start_date.replace(day=28) + timedelta(days=4)).replace(day=1)
        end_date = next_month - timedelta(days=1)

        results = []

        current_day = start_date
        while current_day <= end_date:
            total_collections = MonthlyCommercialSummary.objects.filter(
                month=current_day
            ).aggregate(
                total=Sum("revenue_collected")
            )["total"] or 0

            results.append({
                "day": current_day.day,
                "value": round(total_collections, 2)
            })

            current_day += timedelta(days=1)

        return Response(results, status=status.HTTP_200_OK)
