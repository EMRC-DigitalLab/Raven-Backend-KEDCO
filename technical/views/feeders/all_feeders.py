from rest_framework.views import APIView
from rest_framework.response import Response
from technical.serializers import FeederAvailabilitySerializer
from common.models import Feeder
from django.db.models import Q
from technical.models import HourlyLoad, FeederInterruption

def get_feeder_availability_summary(month=None, year=None, from_date=None, to_date=None, state=None, business_district=None):
    load_filters = Q()
    if month and year:
        load_filters &= Q(date__month=month, date__year=year)
    elif from_date and to_date:
        load_filters &= Q(date__range=[from_date, to_date])

    interruption_filters = Q()
    if month and year:
        interruption_filters &= Q(occurred_at__month=month, occurred_at__year=year)
    elif from_date and to_date:
        interruption_filters &= Q(occurred_at__date__range=[from_date, to_date])

    if business_district:
        feeders = Feeder.objects.filter(business_district__name=business_district)
    elif state:
        feeders = Feeder.objects.filter(business_district__state__name=state)
    else:
        feeders = Feeder.objects.all()

    result = []
    for feeder in feeders:
        load_data = HourlyLoad.objects.filter(feeder=feeder).filter(load_filters)
        interruption_data = FeederInterruption.objects.filter(feeder=feeder).filter(interruption_filters)

        # Compute daily hours with load > 0
        daily_hours = {}
        for entry in load_data:
            if entry.load_mw > 0:
                daily_hours.setdefault(entry.date, 0)
                daily_hours[entry.date] += 1

        avg_supply = round(sum(daily_hours.values()) / len(daily_hours), 2) if daily_hours else 0

        # Compute average duration of interruptions
        durations = [
            (i.restored_at - i.occurred_at).total_seconds() / 3600
            for i in interruption_data
            if i.occurred_at and i.restored_at
        ]
        avg_duration = round(sum(durations) / len(durations), 2) if durations else 0
        avg_turnaround = avg_duration

        result.append({
            "feeder_name": feeder.name,
            "voltage_level": feeder.voltage_level,
            "avg_hours_of_supply": avg_supply,
            "duration_of_interruptions": avg_duration,
            "turnaround_time": avg_turnaround,
            "ftc": interruption_data.count(),
        })

    return result



class FeederAvailabilityOverview(APIView):

    def get(self, request):
        month = request.GET.get("month")
        year = request.GET.get("year")
        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")
        state = request.GET.get("state")
        business_district = request.GET.get("business_district")

        data = get_feeder_availability_summary(
            month=month,
            year=year,
            from_date=from_date,
            to_date=to_date,
            state=state,
            business_district=business_district,
        )

        serializer = FeederAvailabilitySerializer(data, many=True)
        return Response(serializer.data)