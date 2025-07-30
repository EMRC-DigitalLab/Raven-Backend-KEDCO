from technical.models import *
from technical.serializers import *
from rest_framework.response import Response
from django.db.models import Avg
from rest_framework.response import Response
from datetime import timedelta
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Avg, Sum, Count
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta # type: ignore
from technical.models import EnergyDelivered, HourlyLoad, FeederInterruption
from common.models import Feeder

def get_month_range(year, month):
    start = datetime(year, month, 1)
    end = start + relativedelta(months=1) - timedelta(days=1)
    return start.date(), end.date()


def delta(current, previous):
    if previous == 0:
        return 0
    return round(((current - previous) / previous) * 100, 2)


def get_metric_with_history(model_fn, feeder_ids, year, month):
    history = []
    for i in range(4, 0, -1):
        dt = datetime(year, month, 1) - relativedelta(months=i)
        m_start, m_end = get_month_range(dt.year, dt.month)
        val = model_fn(m_start, m_end, feeder_ids)
        history.append(round(val, 2))

    current_start, current_end = get_month_range(year, month)
    current = model_fn(current_start, current_end, feeder_ids)
    prev = model_fn(*get_month_range((datetime(year, month, 1) - relativedelta(months=1)).year, (datetime(year, month, 1) - relativedelta(months=1)).month), feeder_ids)

    return {
        "current": round(current, 2),
        "delta": delta(current, prev),
        "history": history[::-1] + [round(current, 2)]
    }


def calculate_avg_supply(from_date, to_date, feeder_ids):
    hours = HourlyLoad.objects.filter(
        date__range=(from_date, to_date), load_mw__gt=0, feeder_id__in=feeder_ids
    ).values("feeder", "date").annotate(count=Count("hour")).aggregate(avg=Avg("count"))
    return hours["avg"] or 0


def calculate_avg_interruption_duration(from_date, to_date, feeder_ids):
    interruptions = FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        restored_at__isnull=False,
        feeder_id__in=feeder_ids
    )
    total_hours = sum(i.duration_hours for i in interruptions)
    count = interruptions.count()
    return total_hours / count if count else 0


def calculate_interruptions(from_date, to_date, feeder_ids):
    return FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        feeder_id__in=feeder_ids
    ).count()


def calculate_energy_delivered(from_date, to_date, feeder_ids):
    return EnergyDelivered.objects.filter(
        date__range=(from_date, to_date),
        feeder_id__in=feeder_ids
    ).aggregate(total=Sum("energy_mwh"))['total'] or 0


def calculate_feeder_count(_, __, feeder_ids):
    return len(feeder_ids)

def calculate_hours_of_supply(from_date, to_date):
    hours = HourlyLoad.objects.filter(
        date__range=(from_date, to_date),
        load_mw__gt=0
    ).values('feeder', 'date').annotate(
        count=Count('hour')
    ).aggregate(avg=Avg('count'))['avg'] or 0
    return round(hours, 2)


def get_avg_interruption_duration(from_date, to_date):
    qs = FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        restored_at__isnull=False
    )
    total_hours = sum(i.duration_hours for i in qs)
    count = qs.count()
    return round(total_hours / count, 2) if count else 0




@api_view(["GET"])
def technical_overview_view(request):
    year = int(request.GET.get("year", datetime.now().year))
    month = int(request.GET.get("month", datetime.now().month))
    start_date, end_date = get_month_range(year, month)
    prev_dt = datetime(year, month, 1) - relativedelta(months=1)
    prev_start, prev_end = get_month_range(prev_dt.year, prev_dt.month)

    def get_avg(model, field, from_date, to_date):
        return model.objects.filter(date__range=(from_date, to_date)).aggregate(avg=Avg(field))["avg"] or 0

    def get_sum(model, field, from_date, to_date):
        return model.objects.filter(date__range=(from_date, to_date)).aggregate(total=Sum(field))["total"] or 0

    def get_metric_with_history(calc_fn):
        history = []
        for i in range(4, 0, -1):
            dt = datetime(year, month, 1) - relativedelta(months=i)
            m_start, m_end = get_month_range(dt.year, dt.month)
            value = calc_fn(m_start, m_end)
            history.append({"month": m_start.strftime("%b"), "value": value})
        current = calc_fn(start_date, end_date)
        prev = calc_fn(prev_start, prev_end)
        return {
            "current": current,
            "delta": delta(current, prev),
            "history": history[::-1]
        }

    energy_now = get_sum(EnergyDelivered, "energy_mwh", start_date, end_date)
    energy_prev = get_sum(EnergyDelivered, "energy_mwh", prev_start, prev_end)

    load_now = get_avg(HourlyLoad, "load_mw", start_date, end_date)
    load_prev = get_avg(HourlyLoad, "load_mw", prev_start, prev_end)

    interruptions_now = FeederInterruption.objects.filter(
        occurred_at__date__range=(start_date, end_date)
    ).count()
    interruptions_prev = FeederInterruption.objects.filter(
        occurred_at__date__range=(prev_start, prev_end)
    ).count()

    supply_hours = get_metric_with_history(calculate_hours_of_supply)
    interruption_duration = get_metric_with_history(get_avg_interruption_duration)
    turnaround_time = interruption_duration  # Same as requested

    feeders_now = Feeder.objects.count()
    feeders_prev = 180  # mock
    customer_count = 5_000_000  # mock

    breakdown = {
        "feeder_count": {"value": feeders_now, "delta": delta(feeders_now, feeders_prev)},
        "avg_daily_interruptions": {"value": interruptions_now, "delta": delta(interruptions_now, interruptions_prev)},
        "avg_turnaround": {"value": turnaround_time["current"], "delta": turnaround_time["delta"]},
        "customer_count": {"value": customer_count, "delta": -5}
    }

    def interruption_breakdown_for(month_offset):
        dt = datetime(year, month, 1) - relativedelta(months=month_offset)
        m_start, m_end = get_month_range(dt.year, dt.month)
        interruptions = FeederInterruption.objects.filter(
            occurred_at__date__range=(m_start, m_end)
        )
        type_totals = {}
        for itype, _ in FeederInterruption.INTERRUPTION_TYPES:
            hours = sum(
                i.duration_hours
                for i in interruptions.filter(interruption_type=itype)
                if i.restored_at
            )
            type_totals[itype] = round(hours, 2)
        return {
            "month": m_start.strftime("%B"),
            "total": round(sum(type_totals.values()), 2),
            "delta": 2.5 + month_offset,
            "breakdown": type_totals
        }

    interruptions_data = [interruption_breakdown_for(i) for i in range(4)]

    trend_series = []
    if "date" in request.GET:
        trend_date = request.GET["date"]
        trend_qs = HourlyLoad.objects.filter(date=trend_date).values('hour').annotate(
            avg_load=Avg('load_mw')
        ).order_by('hour')
        trend_series = [{"hour": entry["hour"], "value": round(entry["avg_load"], 2)} for entry in trend_qs]

    return Response({
        "highlight_metrics": {
            "energy_delivered": {"value": float(energy_now), "delta": delta(energy_now, energy_prev)},
            "average_load": {"value": float(load_now), "delta": delta(load_now, load_prev)},
            "interruptions": {"value": interruptions_now, "delta": delta(interruptions_now, interruptions_prev)},
        },
        "supply_and_quality": {
            "supply_hours": supply_hours,
            "interruption_duration": interruption_duration,
            "turnaround_time": turnaround_time
        },
        "technical_breakdown": breakdown,
        "interruption_sources": interruptions_data,
        "load_trend": {
            "unit": "MW",
            "date": request.GET.get("date"),
            "series": trend_series
        }
    })