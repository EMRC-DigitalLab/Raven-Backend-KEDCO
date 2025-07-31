from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Sum, Avg, Max, Q
from django.core.cache import cache
from datetime import datetime, timedelta
import hashlib
from analytics.models import MonthlyTechnicalSummary
from common.models import State, Feeder
from technical.models import HourlyLoad, FeederInterruption
from commercial.models import Customer
from dateutil.relativedelta import relativedelta # type: ignore


def get_date_range_from_request(request):
    mode = request.GET.get("mode", "monthly")
    if mode == "range":
        try:
            from_date = datetime.strptime(request.GET["from_date"], "%Y-%m-%d").date()
            to_date = datetime.strptime(request.GET["to_date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            raise ValueError("Invalid or missing from_date or to_date for range mode")
    else:
        try:
            year = int(request.GET["year"])
            month = int(request.GET["month"])
            from_date = datetime(year, month, 1).date()
            to_date = (datetime(year, month, 1) + relativedelta(months=1) - timedelta(days=1)).date()
        except (KeyError, ValueError):
            raise ValueError("Invalid or missing year or month for monthly mode")

    return from_date, to_date


def calculate_avg_supply(from_date, to_date, feeder_ids):
    """Calculate average daily supply hours across all feeders in the date range"""
    # Get daily supply hours for each feeder-date combination
    daily_supply = HourlyLoad.objects.filter(
        date__range=(from_date, to_date), 
        load_mw__gt=0, 
        feeder_id__in=feeder_ids
    ).values("feeder", "date").annotate(
        daily_hours=Count("hour")
    )
    
    if daily_supply.exists():
        # Calculate average across all feeder-date combinations
        avg_hours = daily_supply.aggregate(avg=Avg("daily_hours"))["avg"] or 0
        return round(min(avg_hours, 24.0), 2)  # Cap at 24 hours per day
    return 0.0


def calculate_avg_interruption_duration(from_date, to_date, feeder_ids):
    """Calculate average interruption duration in hours"""
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


def _get_states_cache_key(from_date, to_date):
    """Generate cache key for states technical summary"""
    date_str = f"{from_date}_{to_date}"
    hash_key = hashlib.md5(date_str.encode()).hexdigest()[:8]
    return f"states_technical_{hash_key}"


def _get_state_metrics_from_summary(state, from_date, to_date):
    """
    Try to get state metrics from pre-calculated summary data.
    Returns None if summary data is not available for the date range.
    """
    try:
        # Convert date range to months
        months = _get_months_in_range(from_date, to_date)
        print(f"DEBUG: Looking for summary data for state {state.name}, months: {months}")
        
        # Get state-level summaries for these months
        state_summaries = MonthlyTechnicalSummary.objects.filter(
            state=state,
            business_district__isnull=True,  # State-level only
            feeder__isnull=True,
            month__in=months,
            has_complete_data=True
        )
        
        print(f"DEBUG: Found {state_summaries.count()} summaries for {state.name}")
        
        # If we don't have summaries for all months, fall back to real-time
        if state_summaries.count() != len(months):
            print(f"DEBUG: Not enough summary data for {state.name}, falling back to realtime")
            return None
        
        # Calculate total days in the period
        total_days = (to_date - from_date).days + 1
        
        # Calculate weighted averages based on days in each month
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
            
            # Calculate actual days in the summary month
            summary_month_end = (month_date + relativedelta(months=1) - timedelta(days=1))
            days_in_summary_month = summary_month_end.day
            
            # Weight by days in period
            weight = days_in_period / total_days
            
            # Add weighted values - use avg_hours_of_supply directly since it's already daily average
            weighted_supply_hours += float(summary.avg_hours_of_supply) * weight
            
            # Use the pre-calculated average interruption duration
            weighted_interruption_hours += float(summary.avg_interruption_duration) * weight
            total_interruptions += summary.total_interruptions
            total_weight += weight
        
        if total_weight == 0:
            return None
        
        # Get the most recent summary for current values
        latest_summary = state_summaries.order_by('-month').first()
        
        if not latest_summary:
            return None
        
        # Calculate final averages
        avg_supply = min(weighted_supply_hours, 24.0)  # Cap at 24 hours
        avg_duration = weighted_interruption_hours
        
        print(f"DEBUG: Summary calculation for {state.name}: avg_supply={avg_supply}, avg_duration={avg_duration}")
        
        return {
            "avg_supply": round(float(avg_supply), 2),
            "avg_duration": round(float(avg_duration), 2),
            "turnaround": round(float(latest_summary.avg_turnaround_time), 2),  # Use turnaround time from summary
            "ftc": int(total_interruptions),
            "feeder_count": latest_summary.active_feeder_count,
            "peak_load": float(latest_summary.max_peak_load),
            "customer_population": latest_summary.total_customer_count,
            "_source": "summary"
        }
    except Exception as e:
        print(f"DEBUG: Error getting summary data for {state.name}: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def _calculate_state_metrics_realtime(state, from_date, to_date):
    """
    Calculate state metrics in real-time when summary data is not available.
    This is the corrected version with proper daily averaging.
    """
    print(f"DEBUG: Calculating realtime metrics for state {state.name}")
    
    # Get all feeders in this state
    feeders = Feeder.objects.filter(business_district__state=state)
    feeder_ids = list(feeders.values_list("id", flat=True))
    
    print(f"DEBUG: Found {len(feeder_ids)} feeders for state {state.name}")
    
    if not feeder_ids:
        print(f"DEBUG: No feeders found for state {state.name}")
        return None
    
    # 1. Average Supply Hours - Calculate properly as daily average
    try:
        from technical.models import DailyHoursOfSupply
        daily_supply = DailyHoursOfSupply.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(from_date, to_date)
        )
        
        print(f"DEBUG: Found {daily_supply.count()} daily supply records for {state.name}")
        
        if daily_supply.exists():
            avg_supply = daily_supply.aggregate(avg=Avg('hours_supplied'))['avg'] or 0
        else:
            # Fallback: Calculate daily hours for each feeder-date, then average
            daily_hours = HourlyLoad.objects.filter(
                feeder_id__in=feeder_ids,
                date__range=(from_date, to_date),
                load_mw__gt=0
            ).values('feeder', 'date').annotate(
                daily_hours=Count('hour')
            )
            
            print(f"DEBUG: Found {daily_hours.count()} daily hour records for {state.name}")
            
            if daily_hours.exists():
                avg_supply = daily_hours.aggregate(avg=Avg('daily_hours'))['avg'] or 0
            else:
                avg_supply = 0
            
    except ImportError:
        print(f"DEBUG: DailyHoursOfSupply not available, using hourly method for {state.name}")
        # DailyHoursOfSupply doesn't exist, use hourly method
        daily_hours = HourlyLoad.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(from_date, to_date),
            load_mw__gt=0
        ).values('feeder', 'date').annotate(
            daily_hours=Count('hour')
        )
        
        print(f"DEBUG: Found {daily_hours.count()} daily hour records for {state.name}")
        
        if daily_hours.exists():
            avg_supply = daily_hours.aggregate(avg=Avg('daily_hours'))['avg'] or 0
        else:
            avg_supply = 0
    
    # Cap at 24 hours (should never exceed this)
    avg_supply = min(float(avg_supply), 24.0)
    
    # 2. Average Interruption Duration & FTC
    interruptions = FeederInterruption.objects.filter(
        feeder_id__in=feeder_ids,
        occurred_at__date__range=(from_date, to_date)
    )
    
    print(f"DEBUG: Found {interruptions.count()} interruptions for {state.name}")
    
    # Total interruptions count (FTC - Feeder Tripping Count)
    ftc = interruptions.count()
    
    # Average duration for restored interruptions only
    restored_interruptions = interruptions.filter(restored_at__isnull=False)
    
    print(f"DEBUG: Found {restored_interruptions.count()} restored interruptions for {state.name}")
    
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
    
    print(f"DEBUG: Found {peak_load_data.count()} hourly load records for peak calculation for {state.name}")
    
    peak_load = peak_load_data.aggregate(peak=Max("load_mw"))["peak"] or 0
    
    # 4. Customer Population
    customers = Customer.objects.filter(transformer__feeder_id__in=feeder_ids)
    customer_population = customers.count()
    
    print(f"DEBUG: Found {customer_population} customers for {state.name}")
    
    # 5. Feeder Count
    feeder_count = len(feeder_ids)
    
    result = {
        "avg_supply": round(float(avg_supply), 2),
        "avg_duration": round(float(avg_duration), 2),
        "turnaround": round(float(avg_duration), 2),  # Same as duration
        "ftc": ftc,
        "feeder_count": feeder_count,
        "peak_load": float(peak_load),
        "customer_population": customer_population,
        "_source": "realtime"
    }
    
    print(f"DEBUG: Final metrics for {state.name}: {result}")
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
    Optimized technical summary for all states using pre-calculated data where possible.
    Falls back to real-time calculation when summary data is missing.
    """
    try:
        from_date, to_date = get_date_range_from_request(request)
    except ValueError as e:
        print(f"DEBUG: Date range error: {str(e)}")
        return Response({"error": str(e)}, status=400)
    
    # Debug logging
    print(f"DEBUG: Request params: {dict(request.GET)}")
    print(f"DEBUG: Calculated date range: {from_date} to {to_date}")
    
    # Disable cache temporarily for debugging
    # cache_key = _get_states_cache_key(from_date, to_date)
    # print(f"DEBUG: Cache key: {cache_key}")
    
    # cached_response = cache.get(cache_key)
    # if cached_response:
    #     print("DEBUG: Returning cached response")
    #     return Response(cached_response)
    
    print("DEBUG: Calculating fresh data")
    
    # Get all states first
    all_states = State.objects.all().order_by('name')
    print(f"DEBUG: Found {all_states.count()} total states")
    
    # Get states that have feeders (more detailed check)
    states_with_feeders = []
    for state in all_states:
        feeder_count = Feeder.objects.filter(business_district__state=state).count()
        print(f"DEBUG: State {state.name} has {feeder_count} feeders")
        if feeder_count > 0:
            states_with_feeders.append(state)
    
    print(f"DEBUG: Found {len(states_with_feeders)} states with feeders")
    
    overview = []
    
    for state in states_with_feeders:
        print(f"DEBUG: Processing state: {state.name}")
        try:
            # Try to use summary data first
            state_metrics = _get_state_metrics_from_summary(state, from_date, to_date)
            
            if not state_metrics:
                # Fallback to real-time calculation
                state_metrics = _calculate_state_metrics_realtime(state, from_date, to_date)
            
            if state_metrics:  # Only include states with data
                # Additional validation to ensure reasonable values
                if state_metrics["avg_supply"] > 24:
                    print(f"WARNING: State {state.name} has avg_supply > 24 hours: {state_metrics['avg_supply']}")
                    state_metrics["avg_supply"] = 24.0
                
                overview.append({
                    "state": state.name,
                    "metrics": state_metrics
                })
                print(f"DEBUG: Added {state.name} to overview")
            else:
                print(f"DEBUG: No metrics found for {state.name}")
                
        except Exception as e:
            # Log error but continue with other states
            print(f"ERROR: Error calculating metrics for state {state.name}: {str(e)}")
            import traceback
            traceback.print_exc()
            continue
    
    print(f"DEBUG: Final overview has {len(overview)} states")
    
    response_data = {"overview": overview}
    
    # Cache for 10 minutes (disabled for debugging)
    # cache.set(cache_key, response_data, 600)
    # print(f"DEBUG: Cached response with key: {cache_key}")
    
    return Response(response_data)