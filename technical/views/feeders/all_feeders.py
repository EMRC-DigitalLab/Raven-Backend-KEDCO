# technical/views/feeders/all_feeders.py
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db.models import Q, Avg, Count, Max
from django.db import connection
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from technical.serializers import FeederAvailabilitySerializer
from common.models import Feeder
from technical.models import HourlyLoad, FeederInterruption
from technical.constants import TURNAROUND_EXCLUSIONS

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
    today = datetime.now().date()
    
    if mode in ["daily", "weekly", "custom", "range"]:
        try:
            from_date_str = request.GET.get("from_date")
            to_date_str = request.GET.get("to_date")
            
            if not from_date_str:
                if mode == "daily":
                    from_date = datetime.now().date()
                    to_date = from_date
                    return from_date, to_date, mode
                raise ValueError("from_date is required for this mode")
            
            if mode == "daily" and not to_date_str:
                to_date_str = from_date_str
            
            if not to_date_str:
                raise ValueError("to_date is required for this mode")
            
            from_date = _parse_iso_date(from_date_str)
            to_date = _parse_iso_date(to_date_str)
            
            # ✅ Cap end_date at today for current/future periods
            if to_date >= today:
                to_date = today
            
            return from_date, to_date, mode
            
        except (KeyError, ValueError) as e:
            raise ValueError(f"Invalid date format for {mode} mode: {str(e)}")
    
    elif mode == "yearly":
        try:
            year = int(request.GET.get("year", datetime.now().year))
            from_date = datetime(year, 1, 1).date()
            to_date = datetime(year, 12, 31).date()
            
            # ✅ Cap end_date at today for current/future years
            if to_date >= today:
                to_date = today
            
            return from_date, to_date, "yearly"
        except (KeyError, ValueError):
            raise ValueError("Invalid or missing year for yearly mode")
    
    else:  # monthly mode
        try:
            year = int(request.GET.get("year", datetime.now().year))
            month = int(request.GET.get("month", datetime.now().month))
            from_date = datetime(year, month, 1).date()
            to_date = (datetime(year, month, 1) + relativedelta(months=1) - timedelta(days=1)).date()
            
            # ✅ Cap end_date at today for current/future months
            if to_date >= today:
                to_date = today
            
            return from_date, to_date, "monthly"
        except (KeyError, ValueError):
            raise ValueError("Invalid or missing year or month for monthly mode")


def calculate_feeder_hours_of_supply_sql(feeder_id, from_date, to_date):
    """
    Calculate average hours of supply per day for a single feeder using raw SQL.
    
    UPDATED Logic:
    - Uses actual elapsed time for current periods
    - Includes ALL days in the period (days without supply count as 0)
    - Numerator: Total hours supplied across all days
    - Denominator: Total days in period (fractional for current periods)
    
    Returns the average number of hours per day where load was supplied.
    """
    # ✨ CRITICAL: If querying future dates, return 0 (no data available yet)
    today = timezone.now().date()
    now = timezone.now()
    
    if from_date > today:
        return 0.0
    
    # ✅ Calculate actual elapsed time for current periods
    if to_date == today:
        # For current day, calculate fractional days based on current hour
        full_days = (to_date - from_date).days
        current_hour = now.hour
        fractional_day = current_hour / 24.0
        period_days = full_days + fractional_day
    else:
        # For past periods, use full days
        period_days = (to_date - from_date).days + 1
    
    # Count total hours with supply
    query = """
        SELECT 
            COUNT(DISTINCT CONCAT(date, '-', hour)) as total_hours
        FROM technical_hourlyload
        WHERE feeder_id = %s
            AND date BETWEEN %s AND %s
            AND load_mw > 0
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [feeder_id, from_date, to_date])
        result = cursor.fetchone()
        total_hours = result[0] if result and result[0] else 0
    
    # Average = Total hours / Period days (includes days without supply)
    avg_hours = total_hours / period_days if period_days > 0 else 0
    
    return round(min(float(avg_hours), 24.0), 2)


def calculate_feeder_interruption_metrics_sql(feeder_id, from_date, to_date, exclude_types=None):
    """
    Calculate average interruption duration per day for a single feeder using raw SQL.
    
    UPDATED Logic:
    - Uses actual elapsed time for current periods
    - Includes ALL interruptions active during the period (not just those that started in the period)
    - Calculates only the hours that fall within the filtered period boundaries
    - Uses timezone-aware datetime ranges for consistency
    - SUMS all interruption durations (overlaps are counted separately)
    
    Returns:
        tuple: (avg_duration_per_day, total_interruption_count)
            - avg_duration_per_day: Average interruption hours per day
            - total_interruption_count: COUNT of interruptions that occurred in period (for FTC)
    """
    # ✨ CRITICAL: If querying future dates, return 0 (no data available yet)
    now = timezone.now()
    today = now.date()
    
    if from_date > today:
        return 0.0, 0
    
    # ✅ Calculate actual elapsed time for current periods
    if to_date == today:
        # For current day, calculate fractional days based on current hour
        full_days = (to_date - from_date).days
        current_hour = now.hour
        fractional_day = current_hour / 24.0
        period_days = full_days + fractional_day
    else:
        # For past periods, use full days
        period_days = (to_date - from_date).days + 1
    
    # ✅ Create timezone-aware datetime boundaries
    start_of_period = timezone.make_aware(
        datetime.combine(from_date, datetime.min.time())
    )
    end_of_period = timezone.make_aware(
        datetime.combine(to_date, datetime.max.time())
    )
    
    print(f"DEBUG FEEDER {feeder_id}: from_date={from_date}, to_date={to_date}, today={today}")
    print(f"DEBUG FEEDER {feeder_id}: start_of_period={start_of_period}, end_of_period={end_of_period}, now={now}")
    print(f"DEBUG FEEDER {feeder_id}: period_days={period_days}")
    
    # Build exclusion clause
    exclusion_clause = ""
    # Parameters for duration calculation (includes all active interruptions)
    # ✅ Uses timezone-aware datetime ranges
    duration_params = [now, end_of_period, start_of_period, feeder_id, start_of_period, end_of_period, start_of_period, start_of_period]
    
    # Parameters for count calculation (only interruptions that occurred in period)
    count_params = [feeder_id, start_of_period, end_of_period]
    
    if exclude_types:
        placeholders = ','.join(['%s'] * len(exclude_types))
        exclusion_clause = f"AND interruption_type NOT IN ({placeholders})"
        duration_params.extend(exclude_types)
        count_params.extend(exclude_types)
    
    # Simple SUM - include interruptions that started before but are still active during the period
    # Clip duration to period boundaries using LEAST/GREATEST
    # ✅ Uses timezone-aware datetime ranges
    duration_query = f"""
        SELECT 
            COALESCE(SUM(
                GREATEST(
                    EXTRACT(EPOCH FROM (
                        LEAST(COALESCE(restored_at, %s), %s) - GREATEST(occurred_at, %s)
                    )) / 3600.0,
                    0
                )
            ), 0) as total_hours
        FROM technical_feederinterruption
        WHERE feeder_id = %s
            AND (
                occurred_at >= %s AND occurred_at <= %s
                OR (occurred_at < %s AND (restored_at IS NULL OR restored_at >= %s))
            )
            {exclusion_clause}
    """
    
    # Count query: ONLY interruptions that STARTED in period (for FTC metric)
    # ✅ Uses timezone-aware datetime ranges
    count_query = f"""
        SELECT COUNT(*) as interruption_count
        FROM technical_feederinterruption
        WHERE feeder_id = %s
            AND occurred_at >= %s
            AND occurred_at <= %s
            {exclusion_clause}
    """
    
    with connection.cursor() as cursor:
        # Get duration (includes ongoing from before)
        cursor.execute(duration_query, duration_params)
        result = cursor.fetchone()
        total_hours = float(result[0]) if result and result[0] else 0
        
        print(f"DEBUG FEEDER {feeder_id}: total_hours from SQL = {total_hours}")
        
        # Get count (only those that started in period)
        cursor.execute(count_query, count_params)
        result = cursor.fetchone()
        total_interruptions = result[0] if result and result[0] else 0
    
    # Calculate average per day (using fractional days for current periods)
    avg_hours_per_day = total_hours / period_days if period_days > 0 else 0
    
    print(f"DEBUG FEEDER {feeder_id}: avg_hours_per_day before cap = {avg_hours_per_day}")
    
    # Ensure non-negative and cap at 24
    avg_hours_per_day = max(0, min(avg_hours_per_day, 24.0))
    
    print(f"DEBUG FEEDER {feeder_id}: avg_hours_per_day after cap = {avg_hours_per_day}")
    
    return round(avg_hours_per_day, 2), int(total_interruptions)


def calculate_feeder_metrics_optimized(feeder, from_date, to_date, mode):
    """
    Calculate feeder metrics using optimized SQL queries.
    
    UPDATED:
    - Uses actual elapsed time for current periods (fractional days)
    - Uses timezone-aware datetime ranges for consistency
    - Hours of supply includes ALL days (days without supply count as 0)
    
    CORRECTED: Interruption duration includes all active interruptions,
    but count only includes interruptions that occurred in period.
    
    For individual feeders, we calculate:
    - Average hours per day of supply (includes days without supply as 0)
    - Average hours per day of interruptions (all active during period)
    - Average hours per day of local faults (all active during period, excludes L/S and TCN)
    - Total interruption count (only occurred during period)
    """
    try:
        # 1. Average Supply Hours (per day, includes days without supply, fractional for current periods)
        avg_supply = float(calculate_feeder_hours_of_supply_sql(
            feeder.id, 
            from_date, 
            to_date
        ))
    except Exception as e:
        print(f"DEBUG: SQL failed for supply hours on feeder {feeder.name}, using ORM: {str(e)}")
        # Fallback to ORM
        today = timezone.now().date()
        now = timezone.now()
        
        # Calculate period days with fractional support
        if to_date == today:
            full_days = (to_date - from_date).days
            current_hour = now.hour
            fractional_day = current_hour / 24.0
            period_days = full_days + fractional_day
        else:
            period_days = (to_date - from_date).days + 1
        
        # Count total hours with supply
        total_hours = HourlyLoad.objects.filter(
            feeder_id=feeder.id,
            date__range=(from_date, to_date),
            load_mw__gt=0
        ).values('date', 'hour').distinct().count()
        
        avg_supply = total_hours / period_days if period_days > 0 else 0
        avg_supply = round(min(float(avg_supply), 24.0), 2)
    
    try:
        # 2. Interruption Duration (ALL interruptions active during period, per day) - CORRECTED
        avg_duration, ftc_all = calculate_feeder_interruption_metrics_sql(
            feeder.id,
            from_date,
            to_date
        )
        avg_duration = float(avg_duration)
    except Exception as e:
        print(f"DEBUG: SQL failed for interruption metrics on feeder {feeder.name}, using ORM: {str(e)}")
        # Fallback to ORM
        now = timezone.now()
        today = now.date()
        
        # Calculate period days with fractional support
        if to_date == today:
            full_days = (to_date - from_date).days
            current_hour = now.hour
            fractional_day = current_hour / 24.0
            period_days = full_days + fractional_day
        else:
            period_days = (to_date - from_date).days + 1
        
        start_of_period = timezone.make_aware(
            datetime.combine(from_date, datetime.min.time())
        )
        
        # CRITICAL: If querying today, use NOW instead of end of day
        if to_date >= today:
            end_of_period = now
        else:
            end_of_period = timezone.make_aware(
                datetime.combine(to_date, datetime.max.time())
            )
        
        # Get all interruptions active during period
        interruptions = FeederInterruption.objects.filter(
            feeder_id=feeder.id
        ).filter(
            Q(occurred_at__gte=start_of_period, occurred_at__lte=end_of_period) |
            Q(occurred_at__lt=start_of_period, restored_at__gte=start_of_period) |
            Q(occurred_at__lt=start_of_period, restored_at__isnull=True)
        )
        
        # Count only those that occurred in period
        ftc_all = FeederInterruption.objects.filter(
            feeder_id=feeder.id,
            occurred_at__gte=start_of_period,
            occurred_at__lte=end_of_period
        ).count()
        
        total_hours = 0
        for interruption in interruptions:
            # Calculate only hours within period
            start_time = max(interruption.occurred_at, start_of_period)
            end_time = min(
                interruption.restored_at if interruption.restored_at else now,
                end_of_period
            )
            
            if end_time > start_time:
                duration = (end_time - start_time).total_seconds() / 3600
                total_hours += duration
        
        avg_duration = round(total_hours / period_days, 2) if period_days > 0 else 0
    
    try:
        # 3. Turnaround Time (LOCAL faults only active during period, per day) - CORRECTED
        turnaround, ftc_local = calculate_feeder_interruption_metrics_sql(
            feeder.id,
            from_date,
            to_date,
            exclude_types=TURNAROUND_EXCLUSIONS
        )
        turnaround = float(turnaround)
    except Exception as e:
        print(f"DEBUG: SQL failed for turnaround time on feeder {feeder.name}, using ORM: {str(e)}")
        # Fallback to ORM
        now = timezone.now()
        today = now.date()
        
        # Calculate period days with fractional support
        if to_date == today:
            full_days = (to_date - from_date).days
            current_hour = now.hour
            fractional_day = current_hour / 24.0
            period_days = full_days + fractional_day
        else:
            period_days = (to_date - from_date).days + 1
        
        start_of_period = timezone.make_aware(
            datetime.combine(from_date, datetime.min.time())
        )
        
        # CRITICAL: If querying today, use NOW instead of end of day
        if to_date >= today:
            end_of_period = now
        else:
            end_of_period = timezone.make_aware(
                datetime.combine(to_date, datetime.max.time())
            )
        
        # Get all local fault interruptions active during period
        interruptions = FeederInterruption.objects.filter(
            feeder_id=feeder.id
        ).exclude(
            interruption_type__in=TURNAROUND_EXCLUSIONS
        ).filter(
            Q(occurred_at__gte=start_of_period, occurred_at__lte=end_of_period) |
            Q(occurred_at__lt=start_of_period, restored_at__gte=start_of_period) |
            Q(occurred_at__lt=start_of_period, restored_at__isnull=True)
        )
        
        # Count only those that occurred in period
        ftc_local = FeederInterruption.objects.filter(
            feeder_id=feeder.id,
            occurred_at__gte=start_of_period,
            occurred_at__lte=end_of_period
        ).exclude(interruption_type__in=TURNAROUND_EXCLUSIONS).count()
        
        total_hours = 0
        for interruption in interruptions:
            # Calculate only hours within period
            start_time = max(interruption.occurred_at, start_of_period)
            end_time = min(
                interruption.restored_at if interruption.restored_at else now,
                end_of_period
            )
            
            if end_time > start_time:
                duration = (end_time - start_time).total_seconds() / 3600
                total_hours += duration
        
        turnaround = round(total_hours / period_days, 2) if period_days > 0 else 0
    
    # Validation - cap at 24 hours
    avg_supply = min(avg_supply, 24.0)
    avg_duration = min(avg_duration, 24.0)
    turnaround = min(turnaround, 24.0)
    
    return {
        "feeder_name": feeder.name,
        "feeder_slug": feeder.slug,
        "voltage_level": feeder.voltage_level,
        "avg_hours_of_supply": round(avg_supply, 2),
        "duration_of_interruptions": round(avg_duration, 2),
        "turnaround_time": round(turnaround, 2),
        "ftc": ftc_all,  # Total interruption count (occurred in period)
        "_source": f"optimized_sql_{mode}"
    }


def get_feeder_availability_summary_optimized(from_date, to_date, mode, state=None, business_district=None):
    """
    Optimized feeder availability summary using SQL queries.
    No caching, always calculates fresh data.
    
    UPDATED: Only considers ONBOARDED feeders.
    """
    # Filter feeders based on location parameters - ONLY ONBOARDED
    feeders_query = Feeder.objects.filter(is_onboarded=True).select_related('business_district__state')
    
    if business_district:
        feeders_query = feeders_query.filter(business_district__name=business_district)
    elif state:
        feeders_query = feeders_query.filter(business_district__state__name=state)
    
    feeders = list(feeders_query)
    
    print(f"DEBUG: Processing {len(feeders)} onboarded feeders for mode: {mode}, dates: {from_date} to {to_date}")
    
    result = []
    
    for feeder in feeders:
        try:
            feeder_metrics = calculate_feeder_metrics_optimized(
                feeder, 
                from_date, 
                to_date, 
                mode
            )
            result.append(feeder_metrics)
        except Exception as e:
            print(f"ERROR: Error calculating metrics for feeder {feeder.name}: {str(e)}")
            import traceback
            traceback.print_exc()
            continue
    
    print(f"DEBUG: Successfully calculated metrics for {len(result)} onboarded feeders")
    return result


class FeederAvailabilityOverview(APIView):
    """
    Optimized feeder availability overview API supporting multiple modes.
    
    UPDATED: 
    - Only considers ONBOARDED feeders for all calculations
    - Uses actual elapsed time for current periods (fractional days)
    - Uses timezone-aware datetime ranges for consistency
    - Hours of supply includes ALL days (days without supply count as 0)
    
    Modes:
    - monthly: Month-based filtering (year, month params)
    - yearly: Year-based filtering (year param)
    - daily: Single day filtering (from_date param)
    - weekly: Week range filtering (from_date, to_date params)
    - custom: Custom date range filtering (from_date, to_date params)
    - range: Legacy range mode (same as custom)
    
    Query Parameters:
    - mode: monthly, yearly, daily, weekly, custom, range
    - For monthly: year, month
    - For yearly: year
    - For others: from_date, to_date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
    - state: State name for filtering (optional)
    - business_district: Business district name for filtering (optional)
    
    Examples:
    - ?mode=monthly&year=2024&month=8&state=Lagos
    - ?mode=yearly&year=2024&business_district=Ikeja
    - ?mode=daily&from_date=2024-08-02T23:00:00.000Z
    - ?mode=weekly&from_date=2024-08-05T00:00:00.000Z&to_date=2024-08-11T23:59:59.999Z&state=Lagos
    - ?mode=custom&from_date=2024-08-01T00:00:00.000Z&to_date=2024-08-15T23:59:59.999Z
    
    Legacy format still supported:
    - ?year=2024&month=8&state=Lagos (equivalent to monthly mode)
    - ?from_date=2024-08-01&to_date=2024-08-15&state=Lagos (equivalent to custom mode)
    
    Response Format:
    [
        {
            "feeder_name": "Feeder Name",
            "feeder_slug": "feeder-name",
            "voltage_level": "11kv",
            "avg_hours_of_supply": 18.5,        // Hours/day (0-24) - includes days without supply
            "duration_of_interruptions": 3.2,   // Hours/day (0-24) - All active interruptions
            "turnaround_time": 1.5,             // Hours/day (0-24) - Local faults only
            "ftc": 12                           // Count of interruptions that occurred in period
        }
    ]
    
    Key Metrics (CORRECTED - ONBOARDED FEEDERS ONLY):
    - avg_hours_of_supply: Average hours per day of electricity supply (0-24)
      * Includes ALL days in period (days without supply count as 0)
      * Uses fractional days for current periods
    - duration_of_interruptions: Average hours per day of ALL interruptions active during period (0-24)
      * Uses fractional days for current periods
    - turnaround_time: Average hours per day of LOCAL faults active during period (0-24)
      * Uses fractional days for current periods
    - ftc: Feeder Tripping Count - total number of interruptions that OCCURRED in period
    
    NOTE: Only ONBOARDED feeders are included in the results.
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
                
                # ✅ Cap at today
                today = datetime.now().date()
                if to_date >= today:
                    to_date = today
                
                mode = "monthly"
            except (ValueError, TypeError):
                return Response({"error": "Invalid year or month"}, status=400)
                
        elif not mode and (from_date_legacy and to_date_legacy):
            # Legacy range request
            try:
                from_date = datetime.strptime(from_date_legacy, '%Y-%m-%d').date()
                to_date = datetime.strptime(to_date_legacy, '%Y-%m-%d').date()
                
                # ✅ Cap at today
                today = datetime.now().date()
                if to_date >= today:
                    to_date = today
                
                mode = "custom"
            except ValueError:
                return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)
                
        else:
            # Enhanced request - parse using new method
            try:
                from_date, to_date, mode = get_date_range_and_mode_from_request(request)
            except ValueError as e:
                return Response({"error": str(e)}, status=400)
        
        # Get feeder availability data using optimized method (ONBOARDED feeders only)
        data = get_feeder_availability_summary_optimized(
            from_date=from_date,
            to_date=to_date,
            mode=mode,
            state=state,
            business_district=business_district,
        )
        
        # Remove internal source field for response
        clean_data = []
        for item in data:
            clean_item = {k: v for k, v in item.items() if not k.startswith('_')}
            clean_data.append(clean_item)
        
        # Serialize the data
        serializer = FeederAvailabilitySerializer(clean_data, many=True)
        
        return Response(serializer.data)


# Utility functions for backward compatibility

def get_feeder_availability_summary(month=None, year=None, from_date=None, to_date=None, state=None, business_district=None):
    """
    Legacy function maintained for backward compatibility.
    Now uses the optimized calculation method.
    
    UPDATED: Only returns ONBOARDED feeders.
    """
    today = datetime.now().date()
    
    # Determine date range
    if month and year:
        from_date = datetime(year, month, 1).date()
        to_date = (datetime(year, month, 1) + relativedelta(months=1) - timedelta(days=1)).date()
        
        # ✅ Cap at today
        if to_date >= today:
            to_date = today
        
        mode = "monthly"
    elif from_date and to_date:
        if isinstance(from_date, str):
            from_date = datetime.strptime(from_date, '%Y-%m-%d').date()
        if isinstance(to_date, str):
            to_date = datetime.strptime(to_date, '%Y-%m-%d').date()
        
        # ✅ Cap at today
        if to_date >= today:
            to_date = today
        
        mode = "custom"
    else:
        # Default to current month
        now = datetime.now()
        from_date = datetime(now.year, now.month, 1).date()
        to_date = (datetime(now.year, now.month, 1) + relativedelta(months=1) - timedelta(days=1)).date()
        
        # ✅ Cap at today
        if to_date >= today:
            to_date = today
        
        mode = "monthly"
    
    return get_feeder_availability_summary_optimized(
        from_date=from_date,
        to_date=to_date,
        mode=mode,
        state=state,
        business_district=business_district
    )