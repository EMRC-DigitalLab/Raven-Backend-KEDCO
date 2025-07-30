from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Sum, Avg, Max, Q
from django.core.cache import cache
from django.shortcuts import get_object_or_404
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta # type: ignore
import hashlib
from analytics.models import MonthlyTechnicalSummary
from common.models import State, Feeder
from technical.models import HourlyLoad, FeederInterruption, FeederEnergyDaily, FeederEnergyMonthly


@api_view(["GET"])
def state_technical_summary(request):
    """
    Optimized technical summary for a specific state using pre-calculated data where possible.
    Falls back to real-time calculation when summary data is missing.
    """
    state_name = request.GET.get("state")
    if not state_name:
        return Response({"error": "State parameter is required"}, status=400)
    
    # Get state object
    state = get_object_or_404(State, name__iexact=state_name)
    
    year = int(request.GET.get("year", datetime.now().year))
    month = int(request.GET.get("month", datetime.now().month))
    day = request.GET.get("date")
    
    # Try cache first
    cache_key = _get_state_cache_key(state_name, year, month, day)
    cached_response = cache.get(cache_key)
    if cached_response:
        return Response(cached_response)
    
    # Get feeders for this state
    feeders = Feeder.objects.filter(business_district__state=state).select_related(
        'substation', 'business_district'
    )
    feeder_ids = list(feeders.values_list("id", flat=True))
    
    if not feeder_ids:
        return Response({"error": f"No feeders found for state {state_name}"}, status=404)
    
    # Calculate month boundaries
    month_start, month_end = get_month_range(year, month)
    
    # Get top and bottom feeders (optimized)
    top_feeders, bottom_feeders = _get_top_bottom_feeders(feeder_ids, month_start, month_end)
    
    # Get load trend for specific day
    load_trend = _get_load_trend_for_day(feeder_ids, day) if day else []
    
    # Get metrics using optimized calculation
    metrics = _get_state_metrics_optimized(state, year, month, feeder_ids)
    
    response_data = {
        "state": state_name,
        "period": f"{year}-{month:02d}",
        "top_feeders": top_feeders,
        "bottom_feeders": bottom_feeders,
        "load_trend": {
            "date": day,
            "unit": "MW",
            "series": load_trend
        },
        "metrics": metrics
    }
    
    # Cache for 15 minutes (current month) or 1 hour (historical months)
    current_month = datetime.now().replace(day=1).date()
    target_month = datetime(year, month, 1).date()
    cache_timeout = 900 if target_month >= current_month else 3600
    
    cache.set(cache_key, response_data, cache_timeout)
    
    return Response(response_data)


def _get_state_cache_key(state_name, year, month, day=None):
    """Generate cache key for state technical summary"""
    day_str = f"_day_{day}" if day else ""
    cache_str = f"state_tech_{state_name}_{year}_{month}{day_str}"
    return hashlib.md5(cache_str.encode()).hexdigest()[:16]


def _get_top_bottom_feeders(feeder_ids, month_start, month_end):
    """Get top 5 and bottom 5 feeders by peak load - optimized query"""
    
    # Single query to get all feeder peaks
    peak_data = HourlyLoad.objects.filter(
        date__range=(month_start, month_end), 
        feeder_id__in=feeder_ids
    ).values(
        "feeder__name",
        "feeder__substation__name", 
        "feeder__voltage_level",
        "feeder_id"
    ).annotate(
        peak=Max("load_mw")
    ).order_by("-peak")
    
    # Convert to list for slicing
    peak_list = list(peak_data)
    
    if not peak_list:
        return [], []
    
    # Get top 5 and bottom 5
    top_5 = peak_list[:5]
    bottom_5 = peak_list[-5:] if len(peak_list) >= 5 else []
    
    def format_feeder_data(feeder_data):
        return [
            {
                "feeder": item["feeder__name"],
                "substation": item["feeder__substation__name"],
                "voltage_level": item["feeder__voltage_level"],
                "peak": round(float(item["peak"] or 0), 2),
                "feeder_id": item["feeder_id"]
            }
            for item in feeder_data
        ]
    
    return format_feeder_data(top_5), format_feeder_data(bottom_5)


def _get_load_trend_for_day(feeder_ids, day):
    """Get hourly load trend for a specific day - optimized"""
    if not day:
        return []
    
    try:
        # Parse date
        if isinstance(day, str):
            day_date = datetime.strptime(day, "%Y-%m-%d").date()
        else:
            day_date = day
        
        # Single optimized query
        trend_data = HourlyLoad.objects.filter(
            date=day_date, 
            feeder_id__in=feeder_ids
        ).values("hour").annotate(
            avg_load=Avg("load_mw")
        ).order_by("hour")
        
        return [
            {
                "hour": item["hour"], 
                "value": round(float(item["avg_load"] or 0), 2)
            }
            for item in trend_data
        ]
        
    except (ValueError, TypeError) as e:
        print(f"Error parsing day {day}: {str(e)}")
        return []


def _get_state_metrics_optimized(state, year, month, feeder_ids):
    """Get state metrics using summary data where possible, with history"""
    
    target_month = datetime(year, month, 1).date()
    
    # Try to get from summary first
    try:
        summary = MonthlyTechnicalSummary.objects.get(
            state=state,
            business_district__isnull=True,
            feeder__isnull=True,
            month=target_month,
            has_complete_data=True
        )
        
        # Get historical data (4 previous months) from summaries
        history_months = []
        for i in range(1, 5):
            hist_month = target_month - relativedelta(months=i)
            history_months.append(hist_month)
        
        historical_summaries = MonthlyTechnicalSummary.objects.filter(
            state=state,
            business_district__isnull=True,
            feeder__isnull=True,
            month__in=history_months,
            has_complete_data=True
        ).order_by('month')
        
        # Build metrics with history from summaries
        metrics = _build_metrics_from_summary(summary, historical_summaries, target_month)
        metrics['_source'] = 'summary'
        
        return metrics
        
    except MonthlyTechnicalSummary.DoesNotExist:
        # Fallback to real-time calculation
        return _calculate_state_metrics_realtime(state, year, month, feeder_ids)


def _build_metrics_from_summary(current_summary, historical_summaries, target_month):
    """Build metrics response from summary data"""
    
    # Create history data
    history_data = {}
    for summary in historical_summaries:
        month_name = summary.month.strftime("%b")
        history_data[summary.month] = {
            "month": month_name,
            "avg_supply": float(summary.avg_hours_of_supply),
            "avg_duration": float(summary.avg_interruption_duration),
            "turnaround_time": float(summary.avg_fault_turnaround_time),
            "interruptions": summary.total_interruptions,
            "energy_delivered": float(summary.total_energy_delivered),
            "feeder_count": summary.active_feeder_count,
        }
    
    # Sort history by month
    sorted_history = sorted(history_data.items(), key=lambda x: x[0])
    history_list = [data for _, data in sorted_history]
    
    # Calculate deltas (current vs previous month)
    previous_month = target_month - relativedelta(months=1)
    previous_data = history_data.get(previous_month, {})
    
    def calc_delta(current_val, prev_val):
        if prev_val and prev_val != 0:
            return round(((current_val - prev_val) / prev_val) * 100, 2)
        return None
    
    current_metrics = {
        "avg_supply": {
            "current": float(current_summary.avg_hours_of_supply),
            "delta": calc_delta(
                float(current_summary.avg_hours_of_supply),
                previous_data.get("avg_supply", 0)
            ),
            "history": history_list
        },
        "avg_duration": {
            "current": float(current_summary.avg_interruption_duration),
            "delta": calc_delta(
                float(current_summary.avg_interruption_duration),
                previous_data.get("avg_duration", 0)
            ),
            "history": history_list
        },
        "turnaround_time": {
            "current": float(current_summary.avg_fault_turnaround_time),
            "delta": calc_delta(
                float(current_summary.avg_fault_turnaround_time),
                previous_data.get("turnaround_time", 0)
            ),
            "history": history_list
        },
        "interruptions": {
            "current": current_summary.total_interruptions,
            "delta": calc_delta(
                current_summary.total_interruptions,
                previous_data.get("interruptions", 0)
            ),
            "history": history_list
        },
        "energy_delivered": {
            "current": float(current_summary.total_energy_delivered),
            "delta": calc_delta(
                float(current_summary.total_energy_delivered),
                previous_data.get("energy_delivered", 0)
            ),
            "history": history_list
        },
        "feeder_count": {
            "current": current_summary.active_feeder_count,
            "delta": calc_delta(
                current_summary.active_feeder_count,
                previous_data.get("feeder_count", 0)
            ),
            "history": history_list
        }
    }
    
    return current_metrics


def _calculate_state_metrics_realtime(state, year, month, feeder_ids):
    """Calculate state metrics in real-time when summary data unavailable"""
    
    target_month = datetime(year, month, 1).date()
    month_start, month_end = get_month_range(year, month)
    
    # Calculate current month metrics
    current_metrics = _calculate_single_month_metrics(feeder_ids, month_start, month_end)
    
    # Calculate historical metrics (4 previous months)
    history_data = []
    for i in range(1, 5):
        hist_date = target_month - relativedelta(months=i)
        hist_start, hist_end = get_month_range(hist_date.year, hist_date.month)
        hist_metrics = _calculate_single_month_metrics(feeder_ids, hist_start, hist_end)
        
        history_data.append({
            "month": hist_date.strftime("%b"),
            **{k: v for k, v in hist_metrics.items()}
        })
    
    # Reverse to get chronological order (oldest to newest)
    history_data.reverse()
    
    # Calculate deltas (current vs previous month)
    prev_metrics = history_data[-1] if history_data else {}
    
    def calc_delta(current_val, prev_val):
        if prev_val and prev_val != 0:
            return round(((current_val - prev_val) / prev_val) * 100, 2)
        return None
    
    # Format with history and deltas
    metrics = {}
    for key, current_val in current_metrics.items():
        prev_val = prev_metrics.get(key, 0)
        metrics[key] = {
            "current": current_val,
            "delta": calc_delta(current_val, prev_val),
            "history": history_data
        }
    
    metrics['_source'] = 'realtime'
    return metrics


def _calculate_single_month_metrics(feeder_ids, month_start, month_end):
    """Calculate metrics for a single month"""
    
    # Average supply hours
    hourly_supply = HourlyLoad.objects.filter(
        feeder_id__in=feeder_ids,
        date__range=(month_start, month_end),
        load_mw__gt=0
    ).values('feeder', 'date').annotate(
        daily_hours=Count('hour')
    )
    avg_supply = hourly_supply.aggregate(avg=Avg('daily_hours'))['avg'] or 0
    
    # Interruption metrics
    interruptions = FeederInterruption.objects.filter(
        feeder_id__in=feeder_ids,
        occurred_at__date__range=(month_start, month_end)
    )
    
    total_interruptions = interruptions.count()
    
    # Average duration for restored interruptions only
    restored_interruptions = interruptions.filter(restored_at__isnull=False)
    if restored_interruptions.exists():
        total_duration = sum(
            (int.restored_at - int.occurred_at).total_seconds() / 3600
            for int in restored_interruptions
        )
        avg_duration = total_duration / restored_interruptions.count()
    else:
        avg_duration = 0
    
    # Energy delivered
    try:
        # Try monthly aggregates first
        monthly_energy = FeederEnergyMonthly.objects.filter(
            feeder_id__in=feeder_ids,
            period=month_start
        ).aggregate(total=Sum('energy_mwh'))['total'] or 0
        
        if monthly_energy == 0:
            # Fallback to daily aggregation
            monthly_energy = FeederEnergyDaily.objects.filter(
                feeder_id__in=feeder_ids,
                date__range=(month_start, month_end)
            ).aggregate(total=Sum('energy_mwh'))['total'] or 0
            
    except Exception:
        monthly_energy = 0
    
    return {
        "avg_supply": round(float(avg_supply), 2),
        "avg_duration": round(float(avg_duration), 2),
        "turnaround_time": round(float(avg_duration), 2),  # Same as duration
        "interruptions": total_interruptions,
        "energy_delivered": float(monthly_energy),
        "feeder_count": len(feeder_ids),
    }


def get_month_range(year, month):
    """Get start and end dates for a given year/month"""
    start = datetime(year, month, 1).date()
    if month == 12:
        end = datetime(year + 1, 1, 1).date() - timedelta(days=1)
    else:
        end = datetime(year, month + 1, 1).date() - timedelta(days=1)
    return start, end