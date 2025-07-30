from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Avg, Count, Max
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta # type: ignore

from technical.models import HourlyLoad, FeederInterruption
from common.models import Feeder


def get_month_range(year, month):
    start = datetime(year, month, 1)
    end = (start + relativedelta(months=1)) - timedelta(days=1)
    return start.date(), end.date()


def delta(current, previous):
    if previous == 0:
        return 0
    return round(((current - previous) / previous) * 100, 2)


def get_metric_with_history(calc_fn, feeder_ids, year, month):
    history = []
    for i in range(4, 0, -1):
        dt = datetime(year, month, 1) - relativedelta(months=i)
        start, end = get_month_range(dt.year, dt.month)
        val = calc_fn(start, end, feeder_ids)
        history.append(round(val, 2))
        

    current_start, current_end = get_month_range(year, month)
    current = calc_fn(current_start, current_end, feeder_ids)

    prev_month = datetime(year, month, 1) - relativedelta(months=1)
    prev_start, prev_end = get_month_range(prev_month.year, prev_month.month)
    previous = calc_fn(prev_start, prev_end, feeder_ids)

    return {
        "current": round(current, 2),
        "delta": delta(current, previous),
        "history": history,
    }


def calculate_avg_supply(from_date, to_date, feeder_ids):
    hours = HourlyLoad.objects.filter(
        date__range=(from_date, to_date), feeder_id__in=feeder_ids, load_mw__gt=0
    ).values("feeder", "date").annotate(hour_count=Count("hour")).aggregate(avg=Avg("hour_count"))
    return hours["avg"] or 0


def calculate_avg_interruption_duration(from_date, to_date, feeder_ids):
    interruptions = FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        restored_at__isnull=False,
        feeder_id__in=feeder_ids
    )
    total_duration = sum(i.duration_hours for i in interruptions)
    return total_duration / interruptions.count() if interruptions.exists() else 0


def calculate_avg_interruptions(from_date, to_date, feeder_ids):
    days = (to_date - from_date).days or 1
    total = FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        feeder_id__in=feeder_ids
    ).count()
    return total / days


def calculate_faults(from_date, to_date, feeder_ids):
    return FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        feeder_id__in=feeder_ids
    ).count()


def calculate_feeder_count(_, __, feeder_ids):
    return len(feeder_ids)


@api_view(["GET"])
def business_district_technical_summary(request):
    district = request.GET.get("district")
    year = int(request.GET.get("year", datetime.now().year))
    month = int(request.GET.get("month", datetime.now().month))

    feeders = Feeder.objects.filter(business_district__name=district)
    feeder_ids = feeders.values_list("id", flat=True)

    start_date, end_date = get_month_range(year, month)

    # Top & Bottom Peak Feeders
    peak_queryset = HourlyLoad.objects.filter(
        date__range=(start_date, end_date),
        feeder_id__in=feeder_ids
    ).values(
        "feeder__name", "feeder__voltage_level"
    ).annotate(peak=Max("load_mw")).order_by("-peak")

    top_feeders = [
        {
            "feeder": obj["feeder__name"],
            "voltage_level": obj["feeder__voltage_level"],
            "peak": obj["peak"]
        } for obj in peak_queryset[:5]
    ]

    bottom_feeders = [
        {
            "feeder": obj["feeder__name"],
            "voltage_level": obj["feeder__voltage_level"],
            "peak": obj["peak"]
        } for obj in list(peak_queryset.reverse())[:5]
    ]

    return Response({
        "metrics": {
            "avg_supply": get_metric_with_history(calculate_avg_supply, feeder_ids, year, month),
            "duration": get_metric_with_history(calculate_avg_interruption_duration, feeder_ids, year, month),
            "turnaround_time": get_metric_with_history(calculate_avg_interruption_duration, feeder_ids, year, month),
            "interruptions": get_metric_with_history(calculate_avg_interruptions, feeder_ids, year, month),
            "faults": get_metric_with_history(calculate_faults, feeder_ids, year, month),
            "feeder_count": get_metric_with_history(calculate_feeder_count, feeder_ids, year, month),
        },
        "top_feeders": top_feeders,
        "bottom_feeders": bottom_feeders
    })
