from technical.models import *
from technical.serializers import *
from rest_framework.response import Response
from django.db.models import Avg, Sum, Count
from rest_framework.decorators import api_view
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from django.utils.dateparse import parse_datetime
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


def parse_date_range(request):
    """Parse date parameters and determine filtering mode"""
    mode = request.GET.get("mode", "monthly")
    
    if mode == "monthly":
        year = int(request.GET.get("year", datetime.now().year))
        month = int(request.GET.get("month", datetime.now().month))
        start_date, end_date = get_month_range(year, month)
        return {
            "mode": "monthly",
            "start_date": start_date,
            "end_date": end_date,
            "period_days": (end_date - start_date).days + 1
        }
    
    elif mode == "daily":
        from_date_str = request.GET.get("from_date")
        if from_date_str:
            from_date = parse_datetime(from_date_str).date()
        else:
            from_date = datetime.now().date()
        
        return {
            "mode": "daily",
            "start_date": from_date,
            "end_date": from_date,
            "period_days": 1
        }
    
    else:  # custom range
        from_date_str = request.GET.get("from_date")
        to_date_str = request.GET.get("to_date")
        
        if from_date_str and to_date_str:
            from_date = parse_datetime(from_date_str).date()
            to_date = parse_datetime(to_date_str).date()
        else:
            # Default to current month if no dates provided
            from_date, to_date = get_month_range(datetime.now().year, datetime.now().month)
        
        period_days = (to_date - from_date).days + 1
        
        return {
            "mode": "custom",
            "start_date": from_date,
            "end_date": to_date,
            "period_days": period_days
        }


def get_period_label(start_date, period_days, period_index=0):
    """Generate appropriate period labels based on duration"""
    if period_days == 1:  # Daily
        target_date = start_date - timedelta(days=period_index)
        return target_date.strftime("%a")  # Mon, Tue, etc.
    
    elif period_days == 7:  # Weekly
        week_num = period_index + 1
        return f"Wk{week_num}"
    
    elif 28 <= period_days <= 31:  # Monthly
        target_date = start_date - relativedelta(months=period_index)
        return target_date.strftime("%b")  # Jan, Feb, etc.
    
    else:  # Custom cycles
        cycle_num = period_index + 1
        return f"C{cycle_num}"


def get_previous_periods(start_date, end_date, period_days, count=4):
    """Get previous periods for historical comparison"""
    periods = []
    
    for i in range(count, 0, -1):
        if period_days == 1:  # Daily
            period_start = start_date - timedelta(days=i)
            period_end = period_start
        elif period_days == 7:  # Weekly
            period_start = start_date - timedelta(weeks=i)
            period_end = period_start + timedelta(days=6)
        elif 28 <= period_days <= 31:  # Monthly
            temp_date = start_date - relativedelta(months=i)
            period_start, period_end = get_month_range(temp_date.year, temp_date.month)
        else:  # Custom cycles
            period_start = start_date - timedelta(days=period_days * i)
            period_end = period_start + timedelta(days=period_days - 1)
        
        periods.append({
            "start": period_start,
            "end": period_end,
            "label": get_period_label(start_date, period_days, i)
        })
    
    return periods


def calculate_hours_of_supply(from_date, to_date):
    """Calculate average hours of supply per day"""
    hours = HourlyLoad.objects.filter(
        date__range=(from_date, to_date),
        load_mw__gt=0
    ).values('feeder', 'date').annotate(
        count=Count('hour')
    ).aggregate(avg=Avg('count'))['avg'] or 0
    return round(hours, 2)


def get_avg_interruption_duration(from_date, to_date):
    """Calculate average interruption duration"""
    qs = FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        restored_at__isnull=False
    )
    if not qs.exists():
        return 0
    
    total_hours = sum(i.duration_hours for i in qs)
    count = qs.count()
    return round(total_hours / count, 2) if count else 0


def get_metric_with_history(calc_fn, start_date, end_date, period_days):
    """Get metric with historical data for comparison"""
    previous_periods = get_previous_periods(start_date, end_date, period_days)
    
    # Calculate historical values
    history = []
    for period in previous_periods:
        value = calc_fn(period["start"], period["end"])
        history.append({
            "month": period["label"],  # Keep as 'month' for frontend compatibility
            "value": round(value, 2)
        })
    
    # Calculate current and previous values
    current = calc_fn(start_date, end_date)
    
    # Get immediate previous period for delta calculation
    if period_days == 1:  # Daily
        prev_start = start_date - timedelta(days=1)
        prev_end = prev_start
    elif period_days == 7:  # Weekly
        prev_start = start_date - timedelta(weeks=1)
        prev_end = prev_start + timedelta(days=6)
    elif 28 <= period_days <= 31:  # Monthly
        temp_date = start_date - relativedelta(months=1)
        prev_start, prev_end = get_month_range(temp_date.year, temp_date.month)
    else:  # Custom cycles
        prev_start = start_date - timedelta(days=period_days)
        prev_end = prev_start + timedelta(days=period_days - 1)
    
    prev = calc_fn(prev_start, prev_end)
    
    return {
        "current": round(current, 2),
        "delta": delta(current, prev),
        "history": history
    }


def get_sum_metric(model, field, from_date, to_date):
    """Get sum metric for a date range"""
    return model.objects.filter(
        date__range=(from_date, to_date)
    ).aggregate(total=Sum(field))["total"] or 0


def get_avg_metric(model, field, from_date, to_date):
    """Get average metric for a date range"""
    return model.objects.filter(
        date__range=(from_date, to_date)
    ).aggregate(avg=Avg(field))["avg"] or 0


def get_count_metric(model, date_field, from_date, to_date):
    """Get count metric for a date range"""
    filter_kwargs = {f"{date_field}__date__range": (from_date, to_date)}
    return model.objects.filter(**filter_kwargs).count()


def get_interruption_breakdown(start_date, end_date, period_days, period_offset=0):
    """Get interruption breakdown for a specific period"""
    if period_days == 1:  # Daily
        target_start = start_date - timedelta(days=period_offset)
        target_end = target_start
        label = target_start.strftime("%A") if period_offset == 0 else get_period_label(start_date, period_days, period_offset)
    elif period_days == 7:  # Weekly
        target_start = start_date - timedelta(weeks=period_offset)
        target_end = target_start + timedelta(days=6)
        label = f"Week {period_offset + 1}" if period_offset == 0 else f"Wk{period_offset + 1}"
    elif 28 <= period_days <= 31:  # Monthly
        temp_date = start_date - relativedelta(months=period_offset)
        target_start, target_end = get_month_range(temp_date.year, temp_date.month)
        label = target_start.strftime("%B")
    else:  # Custom cycles
        target_start = start_date - timedelta(days=period_days * period_offset)
        target_end = target_start + timedelta(days=period_days - 1)
        label = f"Cycle {period_offset + 1}"
    
    interruptions = FeederInterruption.objects.filter(
        occurred_at__date__range=(target_start, target_end)
    )
    
    type_totals = {}
    for itype, _ in FeederInterruption.INTERRUPTION_TYPES:
        hours = sum(
            i.duration_hours
            for i in interruptions.filter(interruption_type=itype)
            if i.restored_at
        )
        type_totals[itype] = round(hours, 2)
    
    total_hours = sum(type_totals.values())
    
    return {
        "month": label,
        "total": round(total_hours, 2),
        "delta": 2.5 + period_offset,  # You may want to calculate actual delta
        "breakdown": type_totals
    }


@api_view(["GET"])
def technical_overview_view(request):
    # Parse date parameters
    date_info = parse_date_range(request)
    start_date = date_info["start_date"]
    end_date = date_info["end_date"]
    period_days = date_info["period_days"]
    mode = date_info["mode"]
    
    # Get previous period for delta calculations
    if period_days == 1:  # Daily
        prev_start = start_date - timedelta(days=1)
        prev_end = prev_start
    elif period_days == 7:  # Weekly
        prev_start = start_date - timedelta(weeks=1)
        prev_end = prev_start + timedelta(days=6)
    elif 28 <= period_days <= 31:  # Monthly
        temp_date = start_date - relativedelta(months=1)
        prev_start, prev_end = get_month_range(temp_date.year, temp_date.month)
    else:  # Custom cycles
        prev_start = start_date - timedelta(days=period_days)
        prev_end = prev_start + timedelta(days=period_days - 1)
    
    # Calculate highlight metrics
    energy_now = get_sum_metric(EnergyDelivered, "energy_mwh", start_date, end_date)
    energy_prev = get_sum_metric(EnergyDelivered, "energy_mwh", prev_start, prev_end)
    
    load_now = get_avg_metric(HourlyLoad, "load_mw", start_date, end_date)
    load_prev = get_avg_metric(HourlyLoad, "load_mw", prev_start, prev_end)
    
    interruptions_now = get_count_metric(FeederInterruption, "occurred_at", start_date, end_date)
    interruptions_prev = get_count_metric(FeederInterruption, "occurred_at", prev_start, prev_end)
    
    # Calculate supply and quality metrics with history
    supply_hours = get_metric_with_history(calculate_hours_of_supply, start_date, end_date, period_days)
    interruption_duration = get_metric_with_history(get_avg_interruption_duration, start_date, end_date, period_days)
    turnaround_time = interruption_duration  # Same as interruption duration as requested
    
    # Technical breakdown
    feeders_now = Feeder.objects.count()
    feeders_prev = 180  # mock - you may want to make this dynamic
    customer_count = 5_000_000  # mock - you may want to make this dynamic
    
    breakdown = {
        "feeder_count": {"value": feeders_now, "delta": delta(feeders_now, feeders_prev)},
        "avg_daily_interruptions": {"value": interruptions_now, "delta": delta(interruptions_now, interruptions_prev)},
        "avg_turnaround": {"value": turnaround_time["current"], "delta": turnaround_time["delta"]},
        "customer_count": {"value": customer_count, "delta": -5}
    }
    
    # Interruption sources for 4 periods
    interruptions_data = [
        get_interruption_breakdown(start_date, end_date, period_days, i) 
        for i in range(4)
    ]
    
    # Load trend (only for daily mode or when specific date is requested)
    trend_series = []
    trend_date = None
    
    if "date" in request.GET:
        trend_date = request.GET.get("date", start_date.isoformat())
        if isinstance(trend_date, str):
            try:
                trend_date = datetime.fromisoformat(trend_date.replace('Z', '+00:00')).date()
            except:
                trend_date = start_date
        
        trend_qs = HourlyLoad.objects.filter(
            date=trend_date
        ).values('hour').annotate(
            avg_load=Avg('load_mw')
        ).order_by('hour')
        
        trend_series = [
            {"hour": entry["hour"], "value": round(entry["avg_load"], 2)} 
            for entry in trend_qs
        ]
    
    return Response({
        "mode": mode,
        "period": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "days": period_days
        },
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
            "date": trend_date.isoformat() if trend_date else None,
            "series": trend_series
        }
    })