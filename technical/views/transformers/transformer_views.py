# technical/views/transformers/transformer_views.py
from common.models import DistributionTransformer, Feeder
from technical.models import HourlyLoad, FeederInterruption
from django.db.models import Q
from rest_framework.views import APIView
from rest_framework.response import Response

def get_transformer_availability_summary(feeder_slug=None, month=None, year=None, from_date=None, to_date=None):
    if not feeder_slug:
        return []

    try:
        feeder = Feeder.objects.get(slug=feeder_slug)
    except Feeder.DoesNotExist:
        return []

    transformers = DistributionTransformer.objects.filter(feeder=feeder)

    load_filters = Q()
    interruption_filters = Q()

    if month and year:
        load_filters &= Q(date__month=month, date__year=year)
        interruption_filters &= Q(occurred_at__month=month, occurred_at__year=year)
    elif from_date and to_date:
        load_filters &= Q(date__range=[from_date, to_date])
        interruption_filters &= Q(occurred_at__date__range=[from_date, to_date])

    result = []

    for transformer in transformers:
        load_data = HourlyLoad.objects.filter(transformer=transformer).filter(load_filters)
        interruption_data = FeederInterruption.objects.filter(transformer=transformer).filter(interruption_filters)

        # Daily load hours > 0
        daily_hours = {}
        for entry in load_data:
            if entry.load_mw > 0:
                daily_hours.setdefault(entry.date, 0)
                daily_hours[entry.date] += 1

        avg_supply = round(sum(daily_hours.values()) / len(daily_hours), 2) if daily_hours else 0

        durations = [
            (i.restored_at - i.occurred_at).total_seconds() / 3600
            for i in interruption_data
            if i.occurred_at and i.restored_at
        ]
        avg_duration = round(sum(durations) / len(durations), 2) if durations else 0

        result.append({
            "transformer_name": transformer.name,
            "slug": transformer.slug,
            "avg_hours_of_supply": avg_supply,
            "duration_of_interruptions": avg_duration,
            "turnaround_time": avg_duration,
            "ftc": interruption_data.count(),
        })

    return result


class TransformerAvailabilityOverview(APIView):
    def get(self, request):
        feeder_slug = request.GET.get("feeder")
        month = request.GET.get("month")
        year = request.GET.get("year")
        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")

        data = get_transformer_availability_summary(
            feeder_slug=feeder_slug,
            month=month,
            year=year,
            from_date=from_date,
            to_date=to_date,
        )
        return Response(data)