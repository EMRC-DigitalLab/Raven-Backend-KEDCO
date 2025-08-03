from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Sum, Avg, Max, Q
from django.core.cache import cache
from datetime import datetime, timedelta
import hashlib
from analytics.models import MonthlyTechnicalSummary, DailyTechnicalSummary
from common.models import State, Feeder
from technical.models import HourlyLoad, FeederInterruption
from commercial.models import Customer
from dateutil.relativedelta import relativedelta


def get_date_range_from_request(request):
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
    
    else:  # monthly mode
        try:
            year = int(request.GET.get("year", datetime.now().year))
            month = int(request.GET.get("month", datetime.now().month))
            from_date = datetime(year, month, 1).date()
            to_date = (datetime(year, month, 1) + relativedelta(months=1) - timedelta(days=1)).date()
            return from_date, to_date, "monthly"
        except (KeyError, ValueError):
            raise ValueError("Invalid or missing year or month for monthly mode")


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


def _get_states_cache_key(from_date, to_date, mode):
    """Generate cache key for states technical summary with mode"""
    date_str = f"{mode}_{from_date}_{to_date}"
    hash_key = hashlib.md5(date_str.encode()).hexdigest()[:8]
    return f"states_technical_{hash_key}"


def _get_state_metrics_from_summary(state, from_date, to_date, mode):
    """
    Get state metrics from pre-calculated summary data based on mode.
    Returns None if summary data is not available for the date range.
    """
    try:
        print(f"DEBUG: Getting summary data for state {state.name}, mode: {mode}")
        
        if mode == "monthly":
            return _get_monthly_summary_metrics(state, from_date, to_date)
        else:
            return _get_daily_summary_metrics(state, from_date, to_date, mode)
            
    except Exception as e:
        print(f"DEBUG: Error getting summary data for {state.name}: {str(e)}")
        return None


def _get_monthly_summary_metrics(state, from_date, to_date):
    """Get metrics from monthly summaries"""
    months = _get_months_in_range(from_date, to_date)
    print(f"DEBUG: Looking for monthly summary data for state {state.name}, months: {months}")
    
    # Get state-level summaries for these months
    state_summaries = MonthlyTechnicalSummary.objects.filter(
        state=state,
        business_district__isnull=True,
        feeder__isnull=True,
        month__in=months,
        has_complete_data=True
    )
    
    print(f"DEBUG: Found {state_summaries.count()} monthly summaries for {state.name}")
    
    # If we don't have summaries for all months, fall back to real-time
    if state_summaries.count() != len(months):
        print(f"DEBUG: Not enough monthly summary data for {state.name}, falling back to realtime")
        return None
    
    # Calculate weighted averages based on days in each month
    total_days = (to_date - from_date).days + 1
    weighted_supply_hours = 0
    weighted_interruption_hours = 0
    total_interruptions = 0
    total_weight = 0
    
    for month_date in months:
        summary = state_summaries.filter(month=month_date).first()
        if not summary:
            continue
            
        # Calculate days this month contributes to the date range
        month_start = max(month_date, from_date)
        month_end = min(
            (month_date + relativedelta(months=1) - timedelta(days=1)),
            to_date
        )
        days_in_period = (month_end - month_start).days + 1
        weight = days_in_period / total_days
        
        weighted_supply_hours += float(summary.avg_hours_of_supply) * weight
        weighted_interruption_hours += float(summary.avg_interruption_duration) * weight
        total_interruptions += summary.total_interruptions
        total_weight += weight
    
    if total_weight == 0:
        return None
    
    # Get the most recent summary for current values
    latest_summary = state_summaries.order_by('-month').first()
    
    return {
        "avg_supply": round(min(weighted_supply_hours, 24.0), 2),
        "avg_duration": round(weighted_interruption_hours, 2),
        "turnaround": round(float(latest_summary.avg_turnaround_time), 2),
        "ftc": int(total_interruptions),
        "feeder_count": latest_summary.active_feeder_count,
        "peak_load": float(latest_summary.max_peak_load),
        "customer_population": latest_summary.total_customer_count,
        "_source": "monthly_summary"
    }


def _get_daily_summary_metrics(state, from_date, to_date, mode):
    """Get metrics from daily summaries"""
    print(f"DEBUG: Looking for daily summary data for state {state.name}, date range: {from_date} to {to_date}")
    
    # Collect all dates in the range
    dates = []
    current = from_date
    while current <= to_date:
        dates.append(current)
        current += timedelta(days=1)
    
    # Get state-level daily summaries for these dates
    state_summaries = DailyTechnicalSummary.objects.filter(
        state=state,
        business_district__isnull=True,
        feeder__isnull=True,
        date__in=dates,
        has_complete_data=True
    )
    
    print(f"DEBUG: Found {state_summaries.count()} daily summaries for {state.name} out of {len(dates)} dates")
    
    # If we don't have summaries for all dates, fall back to real-time
    if state_summaries.count() != len(dates):
        print(f"DEBUG: Not enough daily summary data for {state.name}, falling back to realtime")
        return None
    
    # Calculate averages across the date range
    avg_supply = state_summaries.aggregate(avg=Avg('hours_of_supply'))['avg'] or 0
    avg_duration = state_summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
    avg_turnaround = state_summaries.aggregate(avg=Avg('avg_turnaround_time'))['avg'] or 0
    total_interruptions = state_summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
    
    # Get the most recent summary for infrastructure metrics
    latest_summary = state_summaries.order_by('-date').first()
    max_peak_load = state_summaries.aggregate(max=Max('max_peak_load'))['max'] or 0
    
    return {
        "avg_supply": round(min(float(avg_supply), 24.0), 2),
        "avg_duration": round(float(avg_duration), 2),
        "turnaround": round(float(avg_turnaround), 2),
        "ftc": int(total_interruptions),
        "feeder_count": latest_summary.active_feeder_count,
        "peak_load": float(max_peak_load),
        "customer_population": latest_summary.total_customer_count,
        "_source": f"daily_summary_{mode}"
    }


def _calculate_state_metrics_realtime(state, from_date, to_date, mode):
    """
    Calculate state metrics in real-time when summary data is not available.
    Enhanced to handle different modes properly.
    """
    print(f"DEBUG: Calculating realtime metrics for state {state.name}, mode: {mode}")
    
    # Get all feeders in this state
    feeders = Feeder.objects.filter(business_district__state=state)
    feeder_ids = list(feeders.values_list("id", flat=True))
    
    print(f"DEBUG: Found {len(feeder_ids)} feeders for state {state.name}")
    
    if not feeder_ids:
        print(f"DEBUG: No feeders found for state {state.name}")
        return None
    
    # 1. Average Supply Hours
    try:
        from technical.models import DailyHoursOfSupply
        daily_supply = DailyHoursOfSupply.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(from_date, to_date)
        )
        
        if daily_supply.exists():
            avg_supply = daily_supply.aggregate(avg=Avg('hours_supplied'))['avg'] or 0
        else:
            # Fallback: Calculate from hourly data
            daily_hours = HourlyLoad.objects.filter(
                feeder_id__in=feeder_ids,
                date__range=(from_date, to_date),
                load_mw__gt=0
            ).values('feeder', 'date').annotate(
                daily_hours=Count('hour')
            )
            
            if daily_hours.exists():
                avg_supply = daily_hours.aggregate(avg=Avg('daily_hours'))['avg'] or 0
            else:
                avg_supply = 0
            
    except ImportError:
        # DailyHoursOfSupply doesn't exist, use hourly method
        daily_hours = HourlyLoad.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(from_date, to_date),
            load_mw__gt=0
        ).values('feeder', 'date').annotate(
            daily_hours=Count('hour')
        )
        
        if daily_hours.exists():
            avg_supply = daily_hours.aggregate(avg=Avg('daily_hours'))['avg'] or 0
        else:
            avg_supply = 0
    
    # Cap at 24 hours
    avg_supply = min(float(avg_supply), 24.0)
    
    # 2. Interruption metrics
    interruptions = FeederInterruption.objects.filter(
        feeder_id__in=feeder_ids,
        occurred_at__date__range=(from_date, to_date)
    )
    
    ftc = interruptions.count()
    
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
    
    # 3. Peak Load
    peak_load_data = HourlyLoad.objects.filter(
        feeder_id__in=feeder_ids,
        date__range=(from_date, to_date)
    )
    peak_load = peak_load_data.aggregate(peak=Max("load_mw"))["peak"] or 0
    
    # 4. Customer Population
    customers = Customer.objects.filter(transformer__feeder_id__in=feeder_ids)
    customer_population = customers.count()
    
    # 5. Feeder Count
    feeder_count = len(feeder_ids)
    
    result = {
        "avg_supply": round(float(avg_supply), 2),
        "avg_duration": round(float(avg_duration), 2),
        "turnaround": round(float(avg_duration), 2),
        "ftc": ftc,
        "feeder_count": feeder_count,
        "peak_load": float(peak_load),
        "customer_population": customer_population,
        "_source": f"realtime_{mode}"
    }
    
    print(f"DEBUG: Final realtime metrics for {state.name}: {result}")
    return result


def _get_months_in_range(from_date, to_date):
    """Get list of first-of-month dates that fall within the date range"""
    months = []
    current = from_date.replace(day=1)
    
    while current <= to_date:
        months.append(current)
        # Move to next month
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    
    return months


@api_view(["GET"])
def all_states_technical_summary(request):
    """
    Enhanced technical summary for all states supporting multiple modes:
    - monthly: Traditional month-based filtering using MonthlyTechnicalSummary
    - daily: Single day filtering using DailyTechnicalSummary
    - weekly: Week range filtering using DailyTechnicalSummary
    - custom: Custom date range filtering using DailyTechnicalSummary
    - range: Legacy range mode (same as custom)
    
    Query Parameters:
    - mode: monthly, daily, weekly, custom, range
    - For monthly: year, month
    - For others: from_date, to_date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
    
    Examples:
    - ?mode=monthly&year=2024&month=8
    - ?mode=daily&from_date=2024-08-02T23:00:00.000Z&to_date=2024-08-02T23:00:00.000Z
    - ?mode=weekly&from_date=2024-08-05T00:00:00.000Z&to_date=2024-08-11T23:59:59.999Z
    - ?mode=custom&from_date=2024-08-01T00:00:00.000Z&to_date=2024-08-15T23:59:59.999Z
    """
    try:
        from_date, to_date, mode = get_date_range_from_request(request)
    except ValueError as e:
        print(f"DEBUG: Date range error: {str(e)}")
        return Response({"error": str(e)}, status=400)
    
    # Debug logging
    print(f"DEBUG: Request params: {dict(request.GET)}")
    print(f"DEBUG: Calculated date range: {from_date} to {to_date}, mode: {mode}")
    
    # Check cache
    cache_key = _get_states_cache_key(from_date, to_date, mode)
    print(f"DEBUG: Cache key: {cache_key}")
    
    cached_response = cache.get(cache_key)
    if cached_response:
        print("DEBUG: Returning cached response")
        return Response(cached_response)
    
    print("DEBUG: Calculating fresh data")
    
    # Get all states with feeders
    all_states = State.objects.all().order_by('name')
    print(f"DEBUG: Found {all_states.count()} total states")
    
    states_with_feeders = []
    for state in all_states:
        feeder_count = Feeder.objects.filter(business_district__state=state).count()
        if feeder_count > 0:
            states_with_feeders.append(state)
    
    print(f"DEBUG: Found {len(states_with_feeders)} states with feeders")
    
    overview = []
    
    for state in states_with_feeders:
        print(f"DEBUG: Processing state: {state.name}")
        try:
            # Try to use summary data first
            state_metrics = _get_state_metrics_from_summary(state, from_date, to_date, mode)
            
            if not state_metrics:
                # Fallback to real-time calculation
                state_metrics = _calculate_state_metrics_realtime(state, from_date, to_date, mode)
            
            if state_metrics:
                # Validation
                if state_metrics["avg_supply"] > 24:
                    print(f"WARNING: State {state.name} has avg_supply > 24 hours: {state_metrics['avg_supply']}")
                    state_metrics["avg_supply"] = 24.0
                
                overview.append({
                    "state": state.name,
                    "metrics": state_metrics
                })
                print(f"DEBUG: Added {state.name} to overview with source: {state_metrics.get('_source', 'unknown')}")
            else:
                print(f"DEBUG: No metrics found for {state.name}")
                
        except Exception as e:
            print(f"ERROR: Error calculating metrics for state {state.name}: {str(e)}")
            import traceback
            traceback.print_exc()
            continue
    
    print(f"DEBUG: Final overview has {len(overview)} states")
    
    response_data = {
        "overview": overview,
        "_metadata": {
            "mode": mode,
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "total_states": len(overview),
            "summary_sources": sum(1 for state in overview if "summary" in state["metrics"].get("_source", "")),
            "realtime_sources": sum(1 for state in overview if "realtime" in state["metrics"].get("_source", "")),
        }
    }
    
    # Cache for different durations based on mode and whether it includes current data
    today = datetime.now().date()
    if to_date >= today:
        cache_timeout = 300  # 5 minutes for current data
    else:
        cache_timeout = 1800  # 30 minutes for historical data
    
    cache.set(cache_key, response_data, cache_timeout)
    print(f"DEBUG: Cached response with key: {cache_key} for {cache_timeout} seconds")
    
    return Response(response_data)


# Legacy support function
def calculate_avg_supply(from_date, to_date, feeder_ids):
    """Legacy function for backward compatibility"""
    daily_supply = HourlyLoad.objects.filter(
        date__range=(from_date, to_date), 
        load_mw__gt=0, 
        feeder_id__in=feeder_ids
    ).values("feeder", "date").annotate(
        daily_hours=Count("hour")
    )
    
    if daily_supply.exists():
        avg_hours = daily_supply.aggregate(avg=Avg("daily_hours"))["avg"] or 0
        return round(min(avg_hours, 24.0), 2)
    return 0.0


def calculate_avg_interruption_duration(from_date, to_date, feeder_ids):
    """Legacy function for backward compatibility"""
    interruptions = FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        restored_at__isnull=False,
        feeder_id__in=feeder_ids
    )
    
    if not interruptions.exists():
        return 0.0
    
    total_hours = sum(
        (interruption.restored_at - interruption.occurred_at).total_seconds() / 3600
        for interruption in interruptions
    )
    count = interruptions.count()
    return round(total_hours / count, 2) if count else 0.0