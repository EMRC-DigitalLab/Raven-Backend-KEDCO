from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Max, Avg
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta # type: ignore
from technical.models import HourlyLoad, FeederInterruption
from common.models import Feeder
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Sum, Avg, Max
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta # type: ignore
from common.models import Feeder
from technical.models import HourlyLoad, FeederInterruption, FeederEnergyDaily, FeederEnergyMonthly
from commercial.models import Customer
from technical.models import DailyHoursOfSupply


def get_date_range(request):
    mode = request.GET.get("mode", "monthly")
    if mode == "range":
        from_date = datetime.strptime(request.GET.get("from_date"), "%Y-%m-%d").date()
        to_date = datetime.strptime(request.GET.get("to_date"), "%Y-%m-%d").date()
    else:
        year = int(request.GET.get("year", datetime.today().year))
        month = int(request.GET.get("month", datetime.today().month))
        from_date = datetime(year, month, 1).date()
        to_date = (from_date + relativedelta(months=1)) - timedelta(days=1)
    return from_date, to_date

from common.models import BusinessDistrict
@api_view(["GET"])
def all_business_districts_technical_summary(request):
    state = request.GET.get("state")
    from_date, to_date = get_date_range(request)

    if not state:
        return Response({"error": "State parameter is required"}, status=400)

    # Get all business districts in the state that have feeders
    districts = BusinessDistrict.objects.filter(
        state__name__iexact=state,
        feeders__isnull=False  # Only districts that have feeders
    ).distinct()

    response_data = []

    for district in districts:
        # Get all feeders in this business district
        feeders = Feeder.objects.filter(business_district=district)
        feeder_ids = list(feeders.values_list("id", flat=True))
        
        if not feeder_ids:
            continue  # Skip districts with no feeders

        # Calculate average hours of supply
        # Method 1: Using DailyHoursOfSupply if available
        daily_supply_hours = DailyHoursOfSupply.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(from_date, to_date)
        ).aggregate(avg_hours=Avg("hours_supplied"))["avg_hours"]
        
        # Method 2: Fallback to HourlyLoad calculation if daily data not available
        if daily_supply_hours is None:
            hours_of_supply = HourlyLoad.objects.filter(
                date__range=(from_date, to_date),
                feeder_id__in=feeder_ids,
                load_mw__gt=0  # Only count hours where there was actual load
            ).values("feeder", "date").annotate(
                hours_count=Count("hour")
            ).aggregate(avg_hours=Avg("hours_count"))["avg_hours"] or 0
        else:
            hours_of_supply = daily_supply_hours

        # Get all interruptions in the date range
        interruptions = FeederInterruption.objects.filter(
            occurred_at__date__range=(from_date, to_date),
            feeder_id__in=feeder_ids
        )

        # Calculate duration metrics (only for restored interruptions)
        restored_interruptions = interruptions.filter(restored_at__isnull=False)
        
        if restored_interruptions.exists():
            # Calculate total duration and average
            total_duration = sum(i.duration_hours for i in restored_interruptions)
            interruption_count = restored_interruptions.count()
            avg_duration = round(total_duration / interruption_count, 2) if interruption_count else 0
        else:
            avg_duration = 0

        # Turnaround time calculation
        # This should be the average time to restore service after an interruption
        turnaround_time = avg_duration

        # Feeder Tripping Count (FTC) - Total number of interruptions/trips
        # This should be the actual count of interruptions, not a fixed value
        ftc = interruptions.count()
        
        # Alternative: FTC per feeder (normalized)
        ftc_per_feeder = round(ftc / len(feeder_ids), 2) if feeder_ids else 0

        # Get feeder count
        feeder_count = len(feeder_ids)

        # Calculate peak load for the district
        peak_load = HourlyLoad.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(from_date, to_date)
        ).aggregate(max_load=Max("load_mw"))["max_load"] or 0

        # Calculate customer population for this district
        customer_population = Customer.objects.filter(
            transformer__feeder__business_district=district
        ).count()

        # Calculate daily interruptions (average per day)
        date_range_days = (to_date - from_date).days + 1
        daily_interruptions = round(ftc / date_range_days, 2) if date_range_days > 0 else 0

        # Energy delivered for this district
        try:
            # Try monthly aggregates first
            energy_delivered = FeederEnergyMonthly.objects.filter(
                feeder_id__in=feeder_ids,
                period__range=(from_date.replace(day=1), to_date.replace(day=1))
            ).aggregate(total_energy=Sum("energy_mwh"))["total_energy"] or 0
            
            if energy_delivered == 0:
                # Fallback to daily aggregation
                energy_delivered = FeederEnergyDaily.objects.filter(
                    feeder_id__in=feeder_ids,
                    date__range=(from_date, to_date)
                ).aggregate(total_energy=Sum("energy_mwh"))["total_energy"] or 0
        except Exception as e:
            print(f"Error calculating energy delivered for {district.name}: {e}")
            energy_delivered = 0

        # Debug logging
        print(f"District: {district.name}")
        print(f"  Feeders: {feeder_count}")
        print(f"  Total interruptions: {ftc}")
        print(f"  Avg supply hours: {hours_of_supply}")
        print(f"  Peak load: {peak_load}")
        print("---")

        response_data.append({
            "district": district.name,
            "metrics": {
                "avg_supply": round(float(hours_of_supply), 2),
                "duration": avg_duration,
                "turnaround_time": turnaround_time,
                "ftc": ftc,  # Actual feeder tripping count
                "ftc_per_feeder": ftc_per_feeder,  # Normalized per feeder
                "daily_interruptions": daily_interruptions,
                "feeder_count": feeder_count,
                "peak_load": round(float(peak_load), 2),
                "customer_population": customer_population,
                "energy_delivered": round(float(energy_delivered), 2),
            }
        })

    return Response({"districts": response_data})