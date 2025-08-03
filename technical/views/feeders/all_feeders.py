from rest_framework.views import APIView
from rest_framework.response import Response
from django.core.cache import cache
from django.db.models import Q, Avg, Count, Sum
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import hashlib
from analytics.models import MonthlyTechnicalSummary, DailyTechnicalSummary
from technical.serializers import FeederAvailabilitySerializer
from common.models import Feeder
from technical.models import HourlyLoad, FeederInterruption


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


def _get_feeder_cache_key(from_date, to_date, mode, state, business_district):
    """Generate cache key for feeder availability summary"""
    filter_str = f"{state}_{business_district}" if state or business_district else "all"
    cache_str = f"feeder_avail_{mode}_{from_date}_{to_date}_{filter_str}"
    return hashlib.md5(cache_str.encode()).hexdigest()[:16]


def _get_feeder_metrics_from_summary(feeder, from_date, to_date, mode):
    """Get feeder metrics from pre-calculated summary data based on mode"""
    try:
        if mode == "monthly":
            return _get_monthly_summary_metrics_feeder(feeder, from_date)
        elif mode == "yearly":
            return _get_yearly_summary_metrics_feeder(feeder, from_date)
        else:
            return _get_daily_summary_metrics_feeder(feeder, from_date, to_date, mode)
            
    except Exception as e:
        print(f"DEBUG: Error getting summary data for feeder {feeder.name}: {str(e)}")
        return None


def _get_monthly_summary_metrics_feeder(feeder, from_date):
    """Get metrics from monthly summaries for single feeder"""
    target_month = from_date
    
    try:
        summary = MonthlyTechnicalSummary.objects.get(
            state=feeder.business_district.state,
            business_district=feeder.business_district,
            feeder=feeder,
            month=target_month,
            has_complete_data=True
        )
        
        return {
            "feeder_name": feeder.name,
            "voltage_level": feeder.voltage_level,
            "avg_hours_of_supply": round(float(summary.avg_hours_of_supply), 2),
            "duration_of_interruptions": round(float(summary.avg_interruption_duration), 2),
            "turnaround_time": round(float(summary.avg_fault_turnaround_time), 2),
            "ftc": summary.total_interruptions,
            "_source": "monthly_summary"
        }
        
    except MonthlyTechnicalSummary.DoesNotExist:
        return None


def _get_yearly_summary_metrics_feeder(feeder, from_date):
    """Get yearly metrics from aggregated monthly summaries for single feeder"""
    target_year = from_date.year
    
    # Get all months in the year
    year_months = []
    for month in range(1, 13):
        year_months.append(datetime(target_year, month, 1).date())
    
    try:
        year_summaries = MonthlyTechnicalSummary.objects.filter(
            state=feeder.business_district.state,
            business_district=feeder.business_district,
            feeder=feeder,
            month__in=year_months,
            has_complete_data=True
        )
        
        # Check if we have complete data for the year
        if year_summaries.count() != 12:
            return None
        
        # Calculate yearly aggregates
        total_interruptions = year_summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
        avg_supply = year_summaries.aggregate(avg=Avg('avg_hours_of_supply'))['avg'] or 0
        avg_duration = year_summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
        avg_turnaround = year_summaries.aggregate(avg=Avg('avg_fault_turnaround_time'))['avg'] or 0
        
        return {
            "feeder_name": feeder.name,
            "voltage_level": feeder.voltage_level,
            "avg_hours_of_supply": round(float(avg_supply), 2),
            "duration_of_interruptions": round(float(avg_duration), 2),
            "turnaround_time": round(float(avg_turnaround), 2),
            "ftc": int(total_interruptions),
            "_source": "yearly_summary"
        }
        
    except Exception as e:
        print(f"DEBUG: Error getting yearly summaries for feeder {feeder.name}: {str(e)}")
        return None


def _get_daily_summary_metrics_feeder(feeder, from_date, to_date, mode):
    """Get metrics from daily summaries for single feeder"""
    # Collect all dates in the range
    dates = []
    current = from_date
    while current <= to_date:
        dates.append(current)
        current += timedelta(days=1)
    
    try:
        summaries = DailyTechnicalSummary.objects.filter(
            state=feeder.business_district.state,
            business_district=feeder.business_district,
            feeder=feeder,
            date__in=dates,
            has_complete_data=True
        )
        
        # Check if we have complete data
        if summaries.count() != len(dates):
            return None
        
        # Calculate aggregates based on mode
        if mode == "daily" and len(dates) == 1:
            # For single day, just use the summary directly
            summary = summaries.first()
            avg_supply = float(summary.hours_of_supply)
            avg_duration = float(summary.avg_interruption_duration)
            avg_turnaround = float(summary.avg_fault_turnaround_time)
            total_interruptions = summary.total_interruptions
        else:
            # For multi-day periods, aggregate the summaries
            avg_supply = summaries.aggregate(avg=Avg('hours_of_supply'))['avg'] or 0
            avg_duration = summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
            avg_turnaround = summaries.aggregate(avg=Avg('avg_fault_turnaround_time'))['avg'] or 0
            total_interruptions = summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
        
        return {
            "feeder_name": feeder.name,
            "voltage_level": feeder.voltage_level,
            "avg_hours_of_supply": round(float(avg_supply), 2),
            "duration_of_interruptions": round(float(avg_duration), 2),
            "turnaround_time": round(float(avg_turnaround), 2),
            "ftc": int(total_interruptions),
            "_source": f"daily_summary_{mode}"
        }
        
    except Exception as e:
        print(f"DEBUG: Error getting daily summaries for feeder {feeder.name}: {str(e)}")
        return None


def get_feeder_availability_summary_enhanced(from_date, to_date, mode, state=None, business_district=None):
    """Enhanced feeder availability summary with multi-mode support and smart data sourcing"""
    
    print(f"DEBUG: Getting feeder availability summary for mode: {mode}, dates: {from_date} to {to_date}")
    
    # Filter feeders based on location parameters
    if business_district:
        feeders = Feeder.objects.filter(business_district__name=business_district)
    elif state:
        feeders = Feeder.objects.filter(business_district__state__name=state)
    else:
        feeders = Feeder.objects.all()
    
    feeders = feeders.select_related('business_district__state')
    print(f"DEBUG: Found {feeders.count()} feeders to process")
    
    result = []
    summary_count = 0
    realtime_count = 0
    
    for feeder in feeders:
        # Try to get metrics from summary data first
        feeder_metrics = _get_feeder_metrics_from_summary(feeder, from_date, to_date, mode)
        
        if feeder_metrics:
            summary_count += 1
            result.append(feeder_metrics)
        else:
            # Fallback to real-time calculation
            realtime_count += 1
            realtime_metrics = get_feeder_availability_realtime(feeder, from_date, to_date, mode)
            if realtime_metrics:
                result.append(realtime_metrics)
    
    print(f"DEBUG: Used {summary_count} summaries, {realtime_count} real-time calculations")
    return result


def get_feeder_availability_realtime(feeder, from_date, to_date, mode):
    """Calculate feeder availability metrics in real-time when summary data unavailable"""
    
    # Build filters for the date range
    load_filters = Q(date__range=[from_date, to_date])
    interruption_filters = Q(occurred_at__date__range=[from_date, to_date])
    
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
    
    return {
        "feeder_name": feeder.name,
        "voltage_level": feeder.voltage_level,
        "avg_hours_of_supply": avg_supply,
        "duration_of_interruptions": avg_duration,
        "turnaround_time": avg_turnaround,
        "ftc": interruption_data.count(),
        "_source": f"realtime_{mode}"
    }


# Legacy function maintained for backward compatibility
def get_feeder_availability_summary(month=None, year=None, from_date=None, to_date=None, state=None, business_district=None):
    """Legacy function maintained for backward compatibility"""
    
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
    """
    Enhanced feeder availability overview API supporting multiple modes:
    - monthly: Traditional month-based filtering using MonthlyTechnicalSummary
    - yearly: Year-based filtering using MonthlyTechnicalSummary (aggregated)
    - daily: Single day filtering using DailyTechnicalSummary
    - weekly: Week range filtering using DailyTechnicalSummary
    - custom: Custom date range filtering using DailyTechnicalSummary
    - range: Legacy range mode (same as custom)
    
    Query Parameters:
    - mode: monthly, yearly, daily, weekly, custom, range
    - For monthly: year, month
    - For yearly: year
    - For others: from_date, to_date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
    - state: State name for filtering (optional)
    - business_district: Business district name for filtering (optional)
    
    IMPORTANT: Response structure maintained for backward compatibility!
    
    Examples:
    - ?mode=monthly&year=2024&month=8&state=Lagos
    - ?mode=yearly&year=2024&business_district=Ikeja
    - ?mode=daily&from_date=2024-08-02T23:00:00.000Z&to_date=2024-08-02T23:00:00.000Z
    - ?mode=weekly&from_date=2024-08-05T00:00:00.000Z&to_date=2024-08-11T23:59:59.999Z&state=Lagos
    - ?mode=custom&from_date=2024-08-01T00:00:00.000Z&to_date=2024-08-15T23:59:59.999Z
    
    Legacy format still supported:
    - ?year=2024&month=8&state=Lagos (equivalent to monthly mode)
    - ?from_date=2024-08-01&to_date=2024-08-15&state=Lagos (equivalent to custom mode)
    """

    def get(self, request):
        # Parse location filters
        state = request.GET.get("state")
        business_district = request.GET.get("business_district")
        
        # Check if this is a legacy request (old format)
        month = request.GET.get("month")
        year = request.GET.get("year")
        from_date_legacy = request.GET.get("from_date")
        to_date_legacy = request.GET.get("to_date")
        mode = request.GET.get("mode")
        
        # Handle legacy requests
        if not mode and (month and year):
            # Legacy monthly request
            try:
                year_int = int(year)
                month_int = int(month)
                from_date = datetime(year_int, month_int, 1).date()
                to_date = (datetime(year_int, month_int, 1) + relativedelta(months=1) - timedelta(days=1)).date()
                mode = "monthly"
            except (ValueError, TypeError):
                return Response({"error": "Invalid year or month"}, status=400)
                
        elif not mode and (from_date_legacy and to_date_legacy):
            # Legacy range request
            try:
                from_date = datetime.strptime(from_date_legacy, '%Y-%m-%d').date()
                to_date = datetime.strptime(to_date_legacy, '%Y-%m-%d').date()
                mode = "custom"
            except ValueError:
                return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)
                
        else:
            # Enhanced request - parse using new method
            try:
                from_date, to_date, mode = get_date_range_and_mode_from_request(request)
            except ValueError as e:
                return Response({"error": str(e)}, status=400)
        
        # Debug logging
        print(f"DEBUG: Request params: {dict(request.GET)}")
        print(f"DEBUG: Calculated date range: {from_date} to {to_date}, mode: {mode}")
        print(f"DEBUG: Filters - state: {state}, business_district: {business_district}")
        
        # Check cache
        cache_key = _get_feeder_cache_key(from_date, to_date, mode, state, business_district)
        cached_response = cache.get(cache_key)
        if cached_response:
            print("DEBUG: Returning cached response")
            return Response(cached_response)
        
        # Get feeder availability data
        if mode and mode != "legacy":
            # Use enhanced method with smart data sourcing
            data = get_feeder_availability_summary_enhanced(
                from_date=from_date,
                to_date=to_date,
                mode=mode,
                state=state,
                business_district=business_district,
            )
        else:
            # Use legacy method for backward compatibility
            data = get_feeder_availability_summary(
                month=month,
                year=year,
                from_date=from_date_legacy,
                to_date=to_date_legacy,
                state=state,
                business_district=business_district,
            )
        
        print(f"DEBUG: Found {len(data)} feeders with availability data")
        
        # Remove internal source field for response
        clean_data = []
        for item in data:
            clean_item = {k: v for k, v in item.items() if not k.startswith('_')}
            clean_data.append(clean_item)
        
        # Serialize the data
        serializer = FeederAvailabilitySerializer(clean_data, many=True)
        response_data = serializer.data
        
        # Cache for different durations based on mode and whether it includes current data
        today = datetime.now().date()
        if to_date >= today:
            cache_timeout = 300  # 5 minutes for current data
        else:
            cache_timeout = 1800  # 30 minutes for historical data
        
        cache.set(cache_key, response_data, cache_timeout)
        print(f"DEBUG: Cached response with key: {cache_key} for {cache_timeout} seconds")
        
        return Response(response_data)