from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Sum, Avg, Max
from django.core.cache import cache
from datetime import datetime, timedelta
import hashlib
from analytics.models import MonthlyTechnicalSummary
from common.models import State
from technical.models import HourlyLoad, FeederInterruption
from commercial.models import Customer
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Avg, Sum, Max
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta # type: ignore
from technical.models import HourlyLoad, FeederInterruption
from common.models import Feeder


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
    hours = HourlyLoad.objects.filter(
        date__range=(from_date, to_date), load_mw__gt=0, feeder_id__in=feeder_ids
    ).values("feeder", "date").annotate(count=Count("hour")).aggregate(avg=Avg("count"))
    return round(hours["avg"] or 0, 2)


def calculate_avg_interruption_duration(from_date, to_date, feeder_ids):
    interruptions = FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        restored_at__isnull=False,
        feeder_id__in=feeder_ids
    )
    total_hours = sum(i.duration_hours for i in interruptions)
    count = interruptions.count()
    return round(total_hours / count, 2) if count else 0


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
    # Convert date range to months
    months = _get_months_in_range(from_date, to_date)
    
    # Get state-level summaries for these months
    state_summaries = MonthlyTechnicalSummary.objects.filter(
        state=state,
        business_district__isnull=True,  # State-level only
        feeder__isnull=True,
        month__in=months,
        has_complete_data=True
    )
    
    # If we don't have summaries for all months, fall back to real-time
    if state_summaries.count() != len(months):
        return None
    
    # Aggregate across months (weighted by days in month where applicable)
    total_days = (to_date - from_date).days + 1
    
    # Calculate weighted averages and totals
    total_supply_hours = state_summaries.aggregate(
        total=Sum('total_supply_hours')
    )['total'] or 0
    
    total_interruption_hours = state_summaries.aggregate(
        total=Sum('total_interruption_hours')
    )['total'] or 0
    
    total_interruptions = state_summaries.aggregate(
        total=Sum('total_interruptions')
    )['total'] or 0
    
    # Get the most recent summary for current values (feeder count, customer count, peak load)
    latest_summary = state_summaries.order_by('-month').first()
    
    if not latest_summary:
        return None
    
    # Calculate averages
    avg_supply = total_supply_hours / len(months) if len(months) > 0 else 0
    avg_duration = total_interruption_hours / total_interruptions if total_interruptions > 0 else 0
    
    # Feeder Tripping Count (FTC) - total interruptions in the period
    ftc = total_interruptions
    
    return {
        "avg_supply": round(float(avg_supply), 2),
        "avg_duration": round(float(avg_duration), 2),
        "turnaround": round(float(avg_duration), 2),  # Same as duration for restoration
        "ftc": int(ftc),
        "feeder_count": latest_summary.active_feeder_count,
        "peak_load": float(latest_summary.max_peak_load),
        "customer_population": latest_summary.total_customer_count,
        "_source": "summary"
    }


def _calculate_state_metrics_realtime(state, from_date, to_date):
    """
    Calculate state metrics in real-time when summary data is not available.
    This is the corrected version of the original calculation logic.
    """
    # Get all feeders in this state
    feeders = Feeder.objects.filter(business_district__state=state)
    feeder_ids = list(feeders.values_list("id", flat=True))
    
    if not feeder_ids:
        return None
    
    # 1. Average Supply Hours
    # Use DailyHoursOfSupply if available, otherwise calculate from HourlyLoad
    try:
        from technical.models import DailyHoursOfSupply
        daily_supply = DailyHoursOfSupply.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(from_date, to_date)
        )
        
        if daily_supply.exists():
            avg_supply = daily_supply.aggregate(avg=Avg('hours_supplied'))['avg'] or 0
        else:
            # Fallback: count hours with load > 0
            hourly_supply = HourlyLoad.objects.filter(
                feeder_id__in=feeder_ids,
                date__range=(from_date, to_date),
                load_mw__gt=0
            ).values('feeder', 'date').annotate(
                daily_hours=Count('hour')
            )
            avg_supply = hourly_supply.aggregate(avg=Avg('daily_hours'))['avg'] or 0
            
    except ImportError:
        # DailyHoursOfSupply doesn't exist, use hourly method
        hourly_supply = HourlyLoad.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(from_date, to_date),
            load_mw__gt=0
        ).values('feeder', 'date').annotate(
            daily_hours=Count('hour')
        )
        avg_supply = hourly_supply.aggregate(avg=Avg('daily_hours'))['avg'] or 0
    
    # 2. Average Interruption Duration & FTC
    interruptions = FeederInterruption.objects.filter(
        feeder_id__in=feeder_ids,
        occurred_at__date__range=(from_date, to_date)
    )
    
    # Total interruptions count (FTC - Feeder Tripping Count)
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
    peak_load = HourlyLoad.objects.filter(
        feeder_id__in=feeder_ids,
        date__range=(from_date, to_date)
    ).aggregate(peak=Max("load_mw"))["peak"] or 0
    
    # 4. Customer Population
    customer_population = Customer.objects.filter(
        transformer__feeder_id__in=feeder_ids
    ).count()
    
    # 5. Feeder Count
    feeder_count = len(feeder_ids)
    
    return {
        "avg_supply": round(float(avg_supply), 2),
        "avg_duration": round(float(avg_duration), 2),
        "turnaround": round(float(avg_duration), 2),  # Same as duration
        "ftc": ftc,
        "feeder_count": feeder_count,
        "peak_load": float(peak_load),
        "customer_population": customer_population,
        "_source": "realtime"
    }

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
    from_date, to_date = get_date_range_from_request(request)
    
    # Debug logging
    print(f"DEBUG: Request params: {dict(request.GET)}")
    print(f"DEBUG: Calculated date range: {from_date} to {to_date}")
    
    # Try cache first
    cache_key = _get_states_cache_key(from_date, to_date)
    print(f"DEBUG: Cache key: {cache_key}")
    
    cached_response = cache.get(cache_key)
    if cached_response:
        print("DEBUG: Returning cached response")
        return Response(cached_response)
    
    print("DEBUG: No cache found, calculating fresh data")
    
    # Get states that have feeders (exclude states with no infrastructure)
    states_with_feeders = State.objects.filter(
        districts__feeders__isnull=False
    ).distinct().order_by('name')
    
    overview = []
    
    for state in states_with_feeders:
        try:
            # Try to use summary data first
            state_metrics = _get_state_metrics_from_summary(state, from_date, to_date)
            
            if not state_metrics:
                # Fallback to real-time calculation
                state_metrics = _calculate_state_metrics_realtime(state, from_date, to_date)
            
            if state_metrics:  # Only include states with data
                overview.append({
                    "state": state.name,
                    "metrics": state_metrics
                })
                
        except Exception as e:
            # Log error but continue with other states
            print(f"Error calculating metrics for state {state.name}: {str(e)}")
            continue
    
    response_data = {"overview": overview}
    
    # Cache for 10 minutes (shorter than monthly summaries since this aggregates across months)
    cache.set(cache_key, response_data, 600)
    print(f"DEBUG: Cached response with key: {cache_key}")
    
    return Response(response_data)