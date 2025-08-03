def _build_daily_range_metrics_from_summary(summaries, from_date, to_date, mode):
    """Build metrics response from daily summary data - maintain legacy structure"""
    
    # Calculate current period metrics
    current_avg_supply = summaries.aggregate(avg=Avg('hours_of_supply'))['avg'] or 0
    current_avg_duration = summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
    current_avg_turnaround = summaries.aggregate(avg=Avg('avg_fault_turnaround_time'))['avg'] or 0
    current_total_interruptions = summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
    current_total_energy = summaries.aggregate(total=Sum('total_energy_delivered'))['total'] or 0
    
    # Get infrastructure metrics from latest summary
    latest_summary = summaries.order_by('-date').first()
    current_feeder_count = latest_summary.active_feeder_count if latest_summary else 0
    
    # Calculate historical periods (4 previous periods of same length)
    period_length = (to_date - from_date).days + 1
    history_data = []
    
    for i in range(1, 5):
        if mode == "daily":
            # For daily mode, go back by individual days
            hist_date = from_date - timedelta(days=i)
            hist_start = hist_end = hist_date
            hist_dates = [hist_date]
        else:
            # For weekly/custom, go back by period length
            hist_end = from_date - timedelta(days=i * period_length)
            hist_start = hist_end - timedelta(days=period_length - 1)
            
            # Get all dates in the historical period
            hist_dates = []
            current = hist_start
            while current <= hist_end:
                hist_dates.append(current)
                current += timedelta(days=1)
        
        hist_summaries = DailyTechnicalSummary.objects.filter(
            state=summaries.first().state,
            business_district__isnull=True,
            feeder__isnull=True,
            date__in=hist_dates,
            has_complete_data=True
        )
        
        if hist_summaries.count() == len(hist_dates):
            # Complete data for this period
            if mode == "daily":
                # For daily, just get the single day's data
                hist_summary = hist_summaries.first()
                avg_supply = float(hist_summary.hours_of_supply)
                avg_duration = float(hist_summary.avg_interruption_duration)
                avg_turnaround = float(hist_summary.avg_fault_turnaround_time)
                total_interruptions = hist_summary.total_interruptions
                total_energy = float(hist_summary.total_energy_delivered)
                feeder_count = hist_summary.active_feeder_count
            else:
                # For weekly/custom, aggregate the period
                avg_supply = hist_summaries.aggregate(avg=Avg('hours_of_supply'))['avg'] or 0
                avg_duration = hist_summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
                avg_turnaround = hist_summaries.aggregate(avg=Avg('avg_fault_turnaround_time'))['avg'] or 0
                total_interruptions = hist_summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
                total_energy = hist_summaries.aggregate(total=Sum('total_energy_delivered'))['total'] or 0
                latest_hist = hist_summaries.order_by('-date').first()
                feeder_count = latest_hist.active_feeder_count if latest_hist else 0
            
            # Generate appropriate labels
            if mode == "daily":
                period_label = _get_day_label_for_period(hist_date, i)
            elif mode == "weekly":
                period_label = f"Wk{5-i}"
            else:  # custom
                period_label = f"C{5-i}"
            
            history_data.append({
                "month": period_label,  # Keep "month" field name for legacy compatibility
                "avg_supply": float(avg_supply),
                "avg_duration": float(avg_duration),
                "turnaround_time": float(avg_turnaround),
                "interruptions": int(total_interruptions),
                "energy_delivered": float(total_energy),
                "feeder_count": feeder_count,
            })
    
    # Reverse to get chronological order
    history_data.reverse()
    
    # Calculate deltas (current vs previous period)
    previous_data = history_data[-1] if history_data else {}
    
    def calc_delta(current_val, prev_val):
        if prev_val and prev_val != 0:
            return round(((current_val - prev_val) / prev_val) * 100, 2)
        return None
    
    # Build metrics maintaining legacy structure
    current_metrics = {
        "avg_supply": {
            "current": round(float(current_avg_supply), 2),
            "delta": calc_delta(
                float(current_avg_supply),
                previous_data.get("avg_supply", 0)
            ),
            "history": history_data
        },
        "avg_duration": {
            "current": round(float(current_avg_duration), 2),
            "delta": calc_delta(
                float(current_avg_duration),
                previous_data.get("avg_duration", 0)
            ),
            "history": history_data
        },
        "turnaround_time": {
            "current": round(float(current_avg_turnaround), 2),
            "delta": calc_delta(
                float(current_avg_turnaround),
                previous_data.get("turnaround_time", 0)
            ),
            "history": history_data
        },
        "interruptions": {
            "current": int(current_total_interruptions),
            "delta": calc_delta(
                current_total_interruptions,
                previous_data.get("interruptions", 0)
            ),
            "history": history_data
        },
        "energy_delivered": {
            "current": round(float(current_total_energy), 2),
            "delta": calc_delta(
                float(current_total_energy),
                previous_data.get("energy_delivered", 0)
            ),
            "history": history_data
        },
        "feeder_count": {
            "current": current_feeder_count,
            "delta": calc_delta(
                current_feeder_count,
                previous_data.get("feeder_count", 0)
            ),
            "history": history_data
        }
    }
    
    return current_metrics


def _get_day_label_for_period(date_obj, index):
    """Get day label for historical periods"""
    return date_obj.strftime("%a")  # Mon, Tue, Wed, etc.


def _calculate_daily_range_metrics_realtime(state, from_date, to_date, mode, feeder_ids):
    """Calculate daily range metrics in real-time when summary data unavailable"""
    
    # Calculate current period metrics
    current_metrics = _calculate_single_period_metrics(feeder_ids, from_date, to_date)
    
    # Calculate historical periods (4 previous periods of same length)
    period_length = (to_date - from_date).days + 1
    history_data = []
    
    for i in range(1, 5):
        if mode == "daily":
            # For daily mode, go back by individual days
            hist_date = from_date - timedelta(days=i)
            hist_start = hist_end = hist_date
        else:
            # For weekly/custom, go back by period length
            hist_end = from_date - timedelta(days=i * period_length)
            hist_start = hist_end - timedelta(days=period_length - 1)
        
        hist_metrics = _calculate_single_period_metrics(feeder_ids, hist_start, hist_end)
        
        # Generate appropriate labels
        if mode == "daily":
            if i == 1:
                period_label = "Yesterday"
            else:
                period_label = hist_date.strftime("%a")  # Mon, Tue, Wed, etc.
        elif mode == "weekly":
            period_label = f"Wk{5-i}"
        else:  # custom
            period_label = f"C{5-i}"
        
        history_data.append({
            "month": period_label,  # Keep "month" field name for legacy compatibility
            **{k: v for k, v in hist_metrics.items()}
        })
    
    # Reverse to get chronological order
    history_data.reverse()
    
    # Calculate deltas (current vs previous period)
    prev_metrics = history_data[-1] if history_data else {}
    
    def calc_delta(current_val, prev_val):
        if prev_val and prev_val != 0:
            return round(((current_val - prev_val) / prev_val) * 100, 2)
        return None
    
    # Format with history and deltas - maintain legacy structure
    metrics = {}
    for key, current_val in current_metrics.items():
        prev_val = prev_metrics.get(key, 0)
        metrics[key] = {
            "current": current_val,
            "delta": calc_delta(current_val, prev_val),
            "history": history_data
        }
    
    metrics['_source'] = f'realtime_{mode}'
    return metrics


def _build_monthly_metrics_from_summary(current_summary, historical_summaries, target_month):
    """Build metrics response from monthly summary data - maintain legacy structure"""
    
    # Create history data using month names (maintain legacy format)
    history_data = {}
    for summary in historical_summaries:
        month_name = summary.month.strftime("%b")  # Jan, Feb, etc.
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
    
    # Build metrics maintaining exact legacy structure
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

from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Sum, Avg, Max, Q
from django.core.cache import cache
from django.shortcuts import get_object_or_404
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import hashlib
from analytics.models import MonthlyTechnicalSummary, DailyTechnicalSummary
from common.models import State, Feeder
from technical.models import HourlyLoad, FeederInterruption, FeederEnergyDaily, FeederEnergyMonthly


def _parse_iso_date(date_str):
    """Parse ISO datetime string to date"""
    try:
        if 'T' in date_str:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            return dt.date()
        else:
            return datetime.strptime(date_str, '%Y-%m-%d').date()
    except:
        raise ValueError(f"Invalid date format: {date_str}")


def get_date_range_and_mode_from_request(request):
    """Enhanced date range parsing with support for multiple modes"""
    mode = request.GET.get("mode", "monthly")
    
    if mode in ["daily", "weekly", "custom", "range"]:
        try:
            from_date_str = request.GET.get("from_date")
            to_date_str = request.GET.get("to_date")
            
            if not from_date_str or not to_date_str:
                raise ValueError("from_date and to_date are required for this mode")
            
            # Parse ISO datetime strings
            from_date = _parse_iso_date(from_date_str)
            to_date = _parse_iso_date(to_date_str)
            
            return from_date, to_date, mode
            
        except (KeyError, ValueError) as e:
            raise ValueError(f"Invalid date format for {mode} mode: {str(e)}")
    
    elif mode == "yearly":
        try:
            year = int(request.GET.get("year", datetime.now().year))
            from_date = datetime(year, 1, 1).date()
            to_date = datetime(year, 12, 31).date()
            return from_date, to_date, "yearly"
        except (KeyError, ValueError):
            raise ValueError("Invalid or missing year for yearly mode")
    
    else:  # monthly mode
        try:
            year = int(request.GET.get("year", datetime.now().year))
            month = int(request.GET.get("month", datetime.now().month))
            from_date = datetime(year, month, 1).date()
            to_date = (datetime(year, month, 1) + relativedelta(months=1) - timedelta(days=1)).date()
            return from_date, to_date, "monthly"
        except (KeyError, ValueError):
            raise ValueError("Invalid or missing year or month for monthly mode")


@api_view(["GET"])
def state_technical_summary(request):
    """
    Enhanced technical summary for a specific state supporting multiple modes:
    - monthly: Traditional month-based filtering using MonthlyTechnicalSummary
    - yearly: Year-based filtering using MonthlyTechnicalSummary (aggregated)
    - daily: Single day filtering using DailyTechnicalSummary
    - weekly: Week range filtering using DailyTechnicalSummary
    - custom: Custom date range filtering using DailyTechnicalSummary
    - range: Legacy range mode (same as custom)
    
    Query Parameters:
    - state: State name (required)
    - mode: monthly, yearly, daily, weekly, custom, range
    - For monthly: year, month
    - For yearly: year
    - For others: from_date, to_date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
    - date: Specific date for load trend (optional, ISO format)
    
    IMPORTANT: Response structure maintained for backward compatibility!
    """
    state_name = request.GET.get("state")
    if not state_name:
        return Response({"error": "State parameter is required"}, status=400)
    
    # Get state object
    state = get_object_or_404(State, name__iexact=state_name)
    
    try:
        from_date, to_date, mode = get_date_range_and_mode_from_request(request)
    except ValueError as e:
        return Response({"error": str(e)}, status=400)
    
    # Parse specific date for load trend - for backward compatibility
    day = request.GET.get("date")
    if day:
        try:
            trend_date = _parse_iso_date(day)
        except ValueError:
            trend_date = None
    else:
        # Default to the last date in range for load trend
        trend_date = to_date
    
    # For backward compatibility - extract year and month for cache key
    if mode == "monthly":
        year = from_date.year
        month = from_date.month
    else:
        year = from_date.year
        month = from_date.month
    
    # Try cache first - maintain original cache key format for monthly
    if mode == "monthly":
        cache_key = _get_state_cache_key(state_name, year, month, day)
    else:
        cache_key = _get_state_cache_key_extended(state_name, from_date, to_date, mode, trend_date)
    
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
    
    # Get top and bottom feeders (optimized)
    top_feeders, bottom_feeders = _get_top_bottom_feeders(feeder_ids, from_date, to_date)
    
    # Get load trend for specific day
    load_trend = _get_load_trend_for_day(feeder_ids, trend_date) if trend_date else []
    
    # Get metrics using optimized calculation based on mode
    metrics = _get_state_metrics_optimized(state, from_date, to_date, mode, feeder_ids)
    
    # MAINTAIN ORIGINAL RESPONSE STRUCTURE
    response_data = {
        "state": state_name,
        "month": _format_period_label_legacy(from_date, to_date, mode),  # Keep original format
        "top_feeders": top_feeders,
        "bottom_feeders": bottom_feeders,
        "load_trend": {
            "date": day,  # Keep original field name and format
            "unit": "MW",
            "series": load_trend
        },
        "metrics": metrics
    }
    
    # Cache for different durations based on mode and whether it includes current data
    today = datetime.now().date()
    if to_date >= today:
        cache_timeout = 300  # 5 minutes for current data
    else:
        cache_timeout = 1800  # 30 minutes for historical data
    
    cache.set(cache_key, response_data, cache_timeout)
    
    return Response(response_data)


def _format_period_label_legacy(from_date, to_date, mode):
    """Format period label maintaining legacy format for backward compatibility"""
    if mode == "monthly":
        return f"{from_date.year}-{from_date.month:02d}"  # Original format: "2024-08"
    elif mode == "yearly":
        return str(from_date.year)  # "2024"
    elif mode == "daily":
        return from_date.strftime("%Y-%m-%d")  # "2024-08-02"
    else:  # weekly, custom, range
        return f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}"


def _get_state_cache_key_extended(state_name, from_date, to_date, mode, trend_date=None):
    """Generate cache key for enhanced modes"""
    trend_str = f"_trend_{trend_date}" if trend_date else ""
    cache_str = f"state_tech_{state_name}_{mode}_{from_date}_{to_date}{trend_str}"
    return hashlib.md5(cache_str.encode()).hexdigest()[:16]


def _get_state_cache_key(state_name, year, month, day=None):
    """Original cache key function - maintained for backward compatibility"""
    day_str = f"_day_{day}" if day else ""
    cache_str = f"state_tech_{state_name}_{year}_{month}{day_str}"
    return hashlib.md5(cache_str.encode()).hexdigest()[:16]


def _get_top_bottom_feeders(feeder_ids, from_date, to_date):
    """Get top 5 and bottom 5 feeders by peak load - optimized query"""
    
    # Single query to get all feeder peaks
    peak_data = HourlyLoad.objects.filter(
        date__range=(from_date, to_date), 
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
        # Single optimized query
        trend_data = HourlyLoad.objects.filter(
            date=day, 
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
        print(f"Error getting load trend for {day}: {str(e)}")
        return []


def _get_state_metrics_optimized(state, from_date, to_date, mode, feeder_ids):
    """Get state metrics using summary data where possible, with history based on mode"""
    
    if mode == "monthly":
        return _get_monthly_metrics(state, from_date, to_date, feeder_ids)
    elif mode == "yearly":
        return _get_yearly_metrics(state, from_date, to_date, feeder_ids)
    else:
        return _get_daily_range_metrics(state, from_date, to_date, mode, feeder_ids)


def _get_monthly_metrics(state, from_date, to_date, feeder_ids):
    """Get metrics for monthly mode using MonthlyTechnicalSummary"""
    
    target_month = from_date  # from_date is first day of month for monthly mode
    
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
        metrics = _build_monthly_metrics_from_summary(summary, historical_summaries, target_month)
        metrics['_source'] = 'monthly_summary'
        
        return metrics
        
    except MonthlyTechnicalSummary.DoesNotExist:
        # Fallback to real-time calculation
        return _calculate_monthly_metrics_realtime(state, from_date, to_date, feeder_ids)


def _get_daily_range_metrics(state, from_date, to_date, mode, feeder_ids):
    """Get metrics for daily/weekly/custom modes using DailyTechnicalSummary"""
    
    # Collect all dates in the range
    dates = []
    current = from_date
    while current <= to_date:
        dates.append(current)
        current += timedelta(days=1)
    
    # Try to get from daily summaries first
    try:
        summaries = DailyTechnicalSummary.objects.filter(
            state=state,
            business_district__isnull=True,
            feeder__isnull=True,
            date__in=dates,
            has_complete_data=True
        ).order_by('date')
        
        # Check if we have complete data
        if summaries.count() != len(dates):
            print(f"DEBUG: Not enough daily summaries for {state.name}, falling back to realtime")
            return _calculate_daily_range_metrics_realtime(state, from_date, to_date, mode, feeder_ids)
        
        # Calculate metrics from daily summaries
        metrics = _build_daily_range_metrics_from_summary(summaries, from_date, to_date, mode)
        metrics['_source'] = f'daily_summary_{mode}'
        
        return metrics
        
    except Exception as e:
        print(f"DEBUG: Error getting daily summaries for {state.name}: {str(e)}")
        return _calculate_daily_range_metrics_realtime(state, from_date, to_date, mode, feeder_ids)


def _get_yearly_metrics(state, from_date, to_date, feeder_ids):
    """Get metrics for yearly mode using MonthlyTechnicalSummary"""
    
    target_year = from_date.year
    
    # Get all months in the year
    year_months = []
    for month in range(1, 13):
        year_months.append(datetime(target_year, month, 1).date())
    
    # Try to get from monthly summaries first
    try:
        year_summaries = MonthlyTechnicalSummary.objects.filter(
            state=state,
            business_district__isnull=True,
            feeder__isnull=True,
            month__in=year_months,
            has_complete_data=True
        ).order_by('month')
        
        # Check if we have complete data for the year
        if year_summaries.count() != 12:
            print(f"DEBUG: Not enough monthly summaries for yearly {target_year}, falling back to realtime")
            return _calculate_yearly_metrics_realtime(state, from_date, to_date, feeder_ids)
        
        # Calculate yearly aggregates from monthly summaries
        metrics = _build_yearly_metrics_from_summary(year_summaries, target_year)
        metrics['_source'] = 'yearly_summary'
        
        return metrics
        
    except Exception as e:
        print(f"DEBUG: Error getting yearly summaries for {state.name}: {str(e)}")
        return _calculate_yearly_metrics_realtime(state, from_date, to_date, feeder_ids)


def _build_yearly_metrics_from_summary(year_summaries, target_year):
    """Build yearly metrics response from monthly summary data"""
    
    # Calculate yearly aggregates
    total_interruptions = year_summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
    avg_supply = year_summaries.aggregate(avg=Avg('avg_hours_of_supply'))['avg'] or 0
    avg_duration = year_summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
    avg_turnaround = year_summaries.aggregate(avg=Avg('avg_fault_turnaround_time'))['avg'] or 0
    total_energy = year_summaries.aggregate(total=Sum('total_energy_delivered'))['total'] or 0
    
    # Get infrastructure metrics from latest summary
    latest_summary = year_summaries.order_by('-month').first()
    feeder_count = latest_summary.active_feeder_count if latest_summary else 0
    
    # Create history data using month names (maintain legacy format)
    history_data = []
    for summary in year_summaries:
        history_data.append({
            "month": summary.month.strftime("%b"),  # Jan, Feb, etc.
            "avg_supply": float(summary.avg_hours_of_supply),
            "avg_duration": float(summary.avg_interruption_duration),
            "turnaround_time": float(summary.avg_fault_turnaround_time),
            "interruptions": summary.total_interruptions,
            "energy_delivered": float(summary.total_energy_delivered),
            "feeder_count": summary.active_feeder_count,
        })
    
    # Calculate historical years (4 previous years)
    previous_years_data = []
    for i in range(1, 5):
        prev_year = target_year - i
        prev_year_months = []
        for month in range(1, 13):
            prev_year_months.append(datetime(prev_year, month, 1).date())
        
        prev_summaries = MonthlyTechnicalSummary.objects.filter(
            state=year_summaries.first().state,
            business_district__isnull=True,
            feeder__isnull=True,
            month__in=prev_year_months,
            has_complete_data=True
        )
        
        if prev_summaries.count() == 12:  # Complete year
            prev_total_interruptions = prev_summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
            prev_avg_supply = prev_summaries.aggregate(avg=Avg('avg_hours_of_supply'))['avg'] or 0
            prev_avg_duration = prev_summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
            prev_avg_turnaround = prev_summaries.aggregate(avg=Avg('avg_fault_turnaround_time'))['avg'] or 0
            prev_total_energy = prev_summaries.aggregate(total=Sum('total_energy_delivered'))['total'] or 0
            prev_latest = prev_summaries.order_by('-month').first()
            prev_feeder_count = prev_latest.active_feeder_count if prev_latest else 0
            
            previous_years_data.append({
                "month": str(prev_year),  # Use year as "month" for yearly history
                "avg_supply": float(prev_avg_supply),
                "avg_duration": float(prev_avg_duration),
                "turnaround_time": float(prev_avg_turnaround),
                "interruptions": int(prev_total_interruptions),
                "energy_delivered": float(prev_total_energy),
                "feeder_count": prev_feeder_count,
            })
    
    # Reverse to get chronological order
    previous_years_data.reverse()
    
    # Calculate deltas (current year vs previous year)
    previous_year_data = previous_years_data[-1] if previous_years_data else {}
    
    def calc_delta(current_val, prev_val):
        if prev_val and prev_val != 0:
            return round(((current_val - prev_val) / prev_val) * 100, 2)
        return None
    
    # Build metrics maintaining legacy structure
    current_metrics = {
        "avg_supply": {
            "current": round(float(avg_supply), 2),
            "delta": calc_delta(
                float(avg_supply),
                previous_year_data.get("avg_supply", 0)
            ),
            "history": previous_years_data + history_data  # Include both yearly and monthly history
        },
        "avg_duration": {
            "current": round(float(avg_duration), 2),
            "delta": calc_delta(
                float(avg_duration),
                previous_year_data.get("avg_duration", 0)
            ),
            "history": previous_years_data + history_data
        },
        "turnaround_time": {
            "current": round(float(avg_turnaround), 2),
            "delta": calc_delta(
                float(avg_turnaround),
                previous_year_data.get("turnaround_time", 0)
            ),
            "history": previous_years_data + history_data
        },
        "interruptions": {
            "current": int(total_interruptions),
            "delta": calc_delta(
                total_interruptions,
                previous_year_data.get("interruptions", 0)
            ),
            "history": previous_years_data + history_data
        },
        "energy_delivered": {
            "current": round(float(total_energy), 2),
            "delta": calc_delta(
                float(total_energy),
                previous_year_data.get("energy_delivered", 0)
            ),
            "history": previous_years_data + history_data
        },
        "feeder_count": {
            "current": feeder_count,
            "delta": calc_delta(
                feeder_count,
                previous_year_data.get("feeder_count", 0)
            ),
            "history": previous_years_data + history_data
        }
    }
    
    return current_metrics


def _calculate_yearly_metrics_realtime(state, from_date, to_date, feeder_ids):
    """Calculate yearly metrics in real-time when summary data unavailable"""
    
    target_year = from_date.year
    
    # Calculate current year metrics
    current_metrics = _calculate_single_period_metrics(feeder_ids, from_date, to_date)
    
    # Calculate historical years (4 previous years)
    history_data = []
    for i in range(1, 5):
        prev_year = target_year - i
        prev_start = datetime(prev_year, 1, 1).date()
        prev_end = datetime(prev_year, 12, 31).date()
        prev_metrics = _calculate_single_period_metrics(feeder_ids, prev_start, prev_end)
        
        history_data.append({
            "month": str(prev_year),  # Use year as "month" for yearly history
            **{k: v for k, v in prev_metrics.items()}
        })
    
    # Reverse to get chronological order
    history_data.reverse()
    
    # Calculate deltas (current vs previous year)
    prev_metrics = history_data[-1] if history_data else {}
    
    def calc_delta(current_val, prev_val):
        if prev_val and prev_val != 0:
            return round(((current_val - prev_val) / prev_val) * 100, 2)
        return None
    
    # Format with history and deltas - maintain legacy structure
    metrics = {}
    for key, current_val in current_metrics.items():
        prev_val = prev_metrics.get(key, 0)
        metrics[key] = {
            "current": current_val,
            "delta": calc_delta(current_val, prev_val),
            "history": history_data
        }
    
    metrics['_source'] = 'realtime_yearly'
    return metrics
    """Build metrics response from monthly summary data"""
    
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


def _build_daily_range_metrics_from_summary(summaries, from_date, to_date, mode):
    """Build metrics response from daily summary data - maintain legacy structure"""
    
    # Calculate current period metrics
    current_avg_supply = summaries.aggregate(avg=Avg('hours_of_supply'))['avg'] or 0
    current_avg_duration = summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
    current_avg_turnaround = summaries.aggregate(avg=Avg('avg_fault_turnaround_time'))['avg'] or 0
    current_total_interruptions = summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
    current_total_energy = summaries.aggregate(total=Sum('total_energy_delivered'))['total'] or 0
    
    # Get infrastructure metrics from latest summary
    latest_summary = summaries.order_by('-date').first()
    current_feeder_count = latest_summary.active_feeder_count if latest_summary else 0
    
    # Calculate historical periods (4 previous periods of same length)
    period_length = (to_date - from_date).days + 1
    history_data = []
    
    for i in range(1, 5):
        if mode == "daily":
            # For daily mode, go back by individual days
            hist_date = from_date - timedelta(days=i)
            hist_start = hist_end = hist_date
            hist_dates = [hist_date]
        else:
            # For weekly/custom, go back by period length
            hist_end = from_date - timedelta(days=i * period_length)
            hist_start = hist_end - timedelta(days=period_length - 1)
            
            # Get all dates in the historical period
            hist_dates = []
            current = hist_start
            while current <= hist_end:
                hist_dates.append(current)
                current += timedelta(days=1)
        
        hist_summaries = DailyTechnicalSummary.objects.filter(
            state=summaries.first().state,
            business_district__isnull=True,
            feeder__isnull=True,
            date__in=hist_dates,
            has_complete_data=True
        )
        
        if hist_summaries.count() == len(hist_dates):
            # Complete data for this period
            if mode == "daily":
                # For daily, just get the single day's data
                hist_summary = hist_summaries.first()
                avg_supply = float(hist_summary.hours_of_supply)
                avg_duration = float(hist_summary.avg_interruption_duration)
                avg_turnaround = float(hist_summary.avg_fault_turnaround_time)
                total_interruptions = hist_summary.total_interruptions
                total_energy = float(hist_summary.total_energy_delivered)
                feeder_count = hist_summary.active_feeder_count
            else:
                # For weekly/custom, aggregate the period
                avg_supply = hist_summaries.aggregate(avg=Avg('hours_of_supply'))['avg'] or 0
                avg_duration = hist_summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
                avg_turnaround = hist_summaries.aggregate(avg=Avg('avg_fault_turnaround_time'))['avg'] or 0
                total_interruptions = hist_summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
                total_energy = hist_summaries.aggregate(total=Sum('total_energy_delivered'))['total'] or 0
                latest_hist = hist_summaries.order_by('-date').first()
                feeder_count = latest_hist.active_feeder_count if latest_hist else 0
            
            # Generate appropriate labels
            if mode == "daily":
                period_label = _get_day_label_for_period(hist_date, i)  # Use day labels: Yesterday, Mon, Tue, etc.
                print(f"DEBUG: Daily mode label for hist_date {hist_date}: {period_label}")  # Debug output
            elif mode == "weekly":
                period_label = f"Wk{5-i}"  # Wk4, Wk3, Wk2, Wk1
                print(f"DEBUG: Weekly mode label: {period_label}")  # Debug output
            else:  # custom
                period_label = f"C{5-i}"  # C4, C3, C2, C1
                print(f"DEBUG: Custom mode label: {period_label}")  # Debug output
            
            history_data.append({
                "month": period_label,  # Keep "month" field name for legacy compatibility
                "avg_supply": float(avg_supply),
                "avg_duration": float(avg_duration),
                "turnaround_time": float(avg_turnaround),
                "interruptions": int(total_interruptions),
                "energy_delivered": float(total_energy),
                "feeder_count": feeder_count,
            })
    
    # Reverse to get chronological order
    history_data.reverse()
    
    # Calculate deltas (current vs previous period)
    previous_data = history_data[-1] if history_data else {}
    
    def calc_delta(current_val, prev_val):
        if prev_val and prev_val != 0:
            return round(((current_val - prev_val) / prev_val) * 100, 2)
        return None
    
    # Build metrics maintaining legacy structure
    current_metrics = {
        "avg_supply": {
            "current": round(float(current_avg_supply), 2),
            "delta": calc_delta(
                float(current_avg_supply),
                previous_data.get("avg_supply", 0)
            ),
            "history": history_data
        },
        "avg_duration": {
            "current": round(float(current_avg_duration), 2),
            "delta": calc_delta(
                float(current_avg_duration),
                previous_data.get("avg_duration", 0)
            ),
            "history": history_data
        },
        "turnaround_time": {
            "current": round(float(current_avg_turnaround), 2),
            "delta": calc_delta(
                float(current_avg_turnaround),
                previous_data.get("turnaround_time", 0)
            ),
            "history": history_data
        },
        "interruptions": {
            "current": int(current_total_interruptions),
            "delta": calc_delta(
                current_total_interruptions,
                previous_data.get("interruptions", 0)
            ),
            "history": history_data
        },
        "energy_delivered": {
            "current": round(float(current_total_energy), 2),
            "delta": calc_delta(
                float(current_total_energy),
                previous_data.get("energy_delivered", 0)
            ),
            "history": history_data
        },
        "feeder_count": {
            "current": current_feeder_count,
            "delta": calc_delta(
                current_feeder_count,
                previous_data.get("feeder_count", 0)
            ),
            "history": history_data
        }
    }
    
    print(f"DEBUG: Final history_data labels: {[item['month'] for item in history_data]}")  # Debug output
    return current_metrics


def _calculate_monthly_metrics_realtime(state, from_date, to_date, feeder_ids):
    """Calculate monthly metrics in real-time when summary data unavailable"""
    
    target_month = from_date
    
    # Calculate current month metrics
    current_metrics = _calculate_single_month_metrics(feeder_ids, from_date, to_date)
    
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
    
    metrics['_source'] = 'realtime_monthly'
    return metrics


def _calculate_daily_range_metrics_realtime(state, from_date, to_date, mode, feeder_ids):
    """Calculate daily range metrics in real-time when summary data unavailable"""
    
    # Calculate current period metrics
    current_metrics = _calculate_single_period_metrics(feeder_ids, from_date, to_date)
    
    # Calculate historical periods (4 previous periods of same length)
    period_length = (to_date - from_date).days + 1
    history_data = []
    
    for i in range(1, 5):
        if mode == "daily":
            # For daily mode, go back by individual days
            hist_date = from_date - timedelta(days=i)
            hist_start = hist_end = hist_date
        else:
            # For weekly/custom, go back by period length
            hist_end = from_date - timedelta(days=i * period_length)
            hist_start = hist_end - timedelta(days=period_length - 1)
        
        hist_metrics = _calculate_single_period_metrics(feeder_ids, hist_start, hist_end)
        
        # Generate appropriate labels
        if mode == "daily":
            period_label = _get_day_label_for_period(hist_date, i)  # Use day labels: Yesterday, Mon, Tue, etc.
            print(f"DEBUG: Daily mode label for hist_date {hist_date}: {period_label}")  # Debug output
        elif mode == "weekly":
            period_label = f"Wk{5-i}"  # Wk4, Wk3, Wk2, Wk1
            print(f"DEBUG: Weekly mode label: {period_label}")  # Debug output
        else:  # custom
            period_label = f"C{5-i}"  # C4, C3, C2, C1
            print(f"DEBUG: Custom mode label: {period_label}")  # Debug output
        
        history_data.append({
            "month": period_label,  # Keep "month" field name for legacy compatibility
            **{k: v for k, v in hist_metrics.items()}
        })
    
    # Reverse to get chronological order
    history_data.reverse()
    
    # Calculate deltas (current vs previous period)
    prev_metrics = history_data[-1] if history_data else {}
    
    def calc_delta(current_val, prev_val):
        if prev_val and prev_val != 0:
            return round(((current_val - prev_val) / prev_val) * 100, 2)
        return None
    
    # Format with history and deltas - maintain legacy structure
    metrics = {}
    for key, current_val in current_metrics.items():
        prev_val = prev_metrics.get(key, 0)
        metrics[key] = {
            "current": current_val,
            "delta": calc_delta(current_val, prev_val),
            "history": history_data
        }
    
    metrics['_source'] = f'realtime_{mode}'
    print(f"DEBUG: Final history_data labels: {[item['month'] for item in history_data]}")  # Debug output
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
            (interruption.restored_at - interruption.occurred_at).total_seconds() / 3600
            for interruption in restored_interruptions
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


def _calculate_single_period_metrics(feeder_ids, period_start, period_end):
    """Calculate metrics for a single period (daily/weekly/custom range)"""
    
    # Average supply hours across the period
    hourly_supply = HourlyLoad.objects.filter(
        feeder_id__in=feeder_ids,
        date__range=(period_start, period_end),
        load_mw__gt=0
    ).values('feeder', 'date').annotate(
        daily_hours=Count('hour')
    )
    avg_supply = hourly_supply.aggregate(avg=Avg('daily_hours'))['avg'] or 0
    
    # Interruption metrics
    interruptions = FeederInterruption.objects.filter(
        feeder_id__in=feeder_ids,
        occurred_at__date__range=(period_start, period_end)
    )
    
    total_interruptions = interruptions.count()
    
    # Average duration for restored interruptions only
    restored_interruptions = interruptions.filter(restored_at__isnull=False)
    if restored_interruptions.exists():
        total_duration = sum(
            (interruption.restored_at - interruption.occurred_at).total_seconds() / 3600
            for interruption in restored_interruptions
        )
        avg_duration = total_duration / restored_interruptions.count()
    else:
        avg_duration = 0
    
    # Energy delivered (sum for the period)
    try:
        period_energy = FeederEnergyDaily.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(period_start, period_end)
        ).aggregate(total=Sum('energy_mwh'))['total'] or 0
    except Exception:
        period_energy = 0
    
    return {
        "avg_supply": round(float(avg_supply), 2),
        "avg_duration": round(float(avg_duration), 2),
        "turnaround_time": round(float(avg_duration), 2),  # Same as duration
        "interruptions": total_interruptions,
        "energy_delivered": float(period_energy),
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