# technical/views/feeders/all_feeders.py
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from django.db import connection
from django.db.models import Avg, Count, Max, Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from common.models import Feeder
from technical.constants import TURNAROUND_EXCLUSIONS
from technical.models import FeederInterruption, HourlyLoad


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


def calculate_feeder_hours_of_supply_sql(feeder_id, from_date, to_date):
    """
    Calculate average hours of supply per day for a single feeder using raw SQL.

    Formula: total_supplied_hours / period_days
    Matches states/districts/overview: divide by ALL days in the period, not just
    days that had supply. A day with no supply contributes 0, not a skipped average.

    Single-day: returns total hours supplied that day (0-24).
    Multi-day: returns average hours per day over the entire period.
    """
    today = timezone.now().date()
    if from_date > today:
        return 0.0

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

    if from_date == to_date:
        avg_hours = float(total_hours)
    else:
        period_days = (to_date - from_date).days + 1
        avg_hours = total_hours / period_days if period_days > 0 else 0

    return round(min(avg_hours, 24.0), 2)


def calculate_feeder_interruption_metrics_sql(feeder_id, from_date, to_date, exclude_types=None):
    """
    Calculate average interruption duration per day for a single feeder using raw SQL.
    
    LOGIC:
    - Includes interruptions that STARTED within the date range
    - Includes interruptions that started BEFORE but are still ongoing (not resolved)
    - Includes interruptions that started BEFORE but ended DURING the period
    - Clips duration to only the hours that fall within the filtered period boundaries
    - Converts all timestamps to Lagos timezone before date comparison
    - SUMS all interruption durations (overlaps are counted separately)
    
    Returns:
        tuple: (avg_duration_per_day, total_interruption_count)
            - avg_duration_per_day: Average interruption hours per day
            - total_interruption_count: COUNT of interruptions that occurred in period (for FTC)
    """
    # ✨ CRITICAL: If querying future dates, return 0 (no data available yet)
    today = timezone.now().date()
    if from_date > today:
        return 0.0, 0
    
    period_days = (to_date - from_date).days + 1
    
    # For today's date, use current time; for past dates, use end of day
    now = timezone.now()
    
    start_of_period = timezone.make_aware(
        datetime.combine(from_date, datetime.min.time())
    )
    
    if to_date >= today:
        # Querying today - use current time as end boundary
        end_of_period = now
        print(f"DEBUG: Querying today/future - end_of_period set to NOW: {end_of_period}")
    else:
        # Querying past date - use end of day
        end_of_period = timezone.make_aware(
            datetime.combine(to_date, datetime.max.time())
        )
        print(f"DEBUG: Querying past date - end_of_period set to end of day: {end_of_period}")
    
    print(f"DEBUG FEEDER {feeder_id}: from_date={from_date}, to_date={to_date}, today={today}")
    print(f"DEBUG FEEDER {feeder_id}: start_of_period={start_of_period}, end_of_period={end_of_period}, now={now}")
    
    # Build exclusion clause
    exclusion_clause = ""
    # Parameters: now (for restored_at fallback), end_of_period (clip end), start_of_period (clip start), 
    #             feeder_id, from_date, to_date, start_of_period (for ongoing check < start), start_of_period (for ongoing check >= start)
    duration_params = [now, end_of_period, start_of_period, feeder_id, from_date, to_date, start_of_period, start_of_period]
    count_params = [feeder_id, from_date, to_date]
    
    if exclude_types:
        placeholders = ','.join(['%s'] * len(exclude_types))
        exclusion_clause = f"AND interruption_type NOT IN ({placeholders})"
        duration_params.extend(exclude_types)
        count_params.extend(exclude_types)
    
    # Simple SUM - include interruptions that started before but are still active during the period
    # Clip duration to period boundaries using LEAST/GREATEST
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
                (occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
                OR (occurred_at < %s AND (restored_at IS NULL OR restored_at >= %s))
            )
            {exclusion_clause}
    """
    
    # Count query: ONLY interruptions that STARTED in period (for FTC metric)
    count_query = f"""
        SELECT COUNT(*) as interruption_count
        FROM technical_feederinterruption
        WHERE feeder_id = %s
            AND (occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
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
    
    # Calculate average per day
    avg_hours_per_day = total_hours / period_days if period_days > 0 else 0
    
    print(f"DEBUG FEEDER {feeder_id}: avg_hours_per_day before cap = {avg_hours_per_day}")
    
    # Ensure non-negative and cap at 24
    avg_hours_per_day = max(0, min(avg_hours_per_day, 24.0))
    
    print(f"DEBUG FEEDER {feeder_id}: avg_hours_per_day after cap = {avg_hours_per_day}")
    
    return round(avg_hours_per_day, 2), int(total_interruptions)



def _bulk_supply_hours(feeder_ids, from_date, to_date):
    """
    Bulk supply hours for all feeder_ids in ONE query.
    Returns dict: {feeder_id: avg_hours_per_day}
    """
    if not feeder_ids:
        return {}

    placeholders = ','.join(['%s'] * len(feeder_ids))
    query = f"""
        SELECT
            feeder_id,
            COUNT(DISTINCT CONCAT(date, '-', hour)) as total_hours
        FROM technical_hourlyload
        WHERE feeder_id IN ({placeholders})
            AND date BETWEEN %s AND %s
            AND load_mw > 0
        GROUP BY feeder_id
    """
    params = list(feeder_ids) + [from_date, to_date]

    is_single_day = (from_date == to_date)
    period_days = 1 if is_single_day else (to_date - from_date).days + 1

    with connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()

    result = {}
    for feeder_id, total_hours in rows:
        avg = float(total_hours) if is_single_day else total_hours / period_days
        result[feeder_id] = round(min(avg, 24.0), 2)

    # feeders with no data at all default to 0
    return result


def _bulk_interruption_metrics(feeder_ids, from_date, to_date, exclude_types=None):
    """
    Bulk interruption duration + count for all feeder_ids in ONE query.
    Returns dict: {feeder_id: (avg_duration_per_day, ftc_count)}
    """
    if not feeder_ids:
        return {}

    now = timezone.now()
    today = now.date()

    start_of_period = timezone.make_aware(datetime.combine(from_date, datetime.min.time()))
    end_of_period = now if to_date >= today else timezone.make_aware(
        datetime.combine(to_date, datetime.max.time())
    )

    period_days = (to_date - from_date).days + 1
    is_single_day = (from_date == to_date)

    feeder_placeholders = ','.join(['%s'] * len(feeder_ids))

    exclusion_clause = ""
    extra_params = []
    if exclude_types:
        type_placeholders = ','.join(['%s'] * len(exclude_types))
        exclusion_clause = f"AND interruption_type NOT IN ({type_placeholders})"
        extra_params = list(exclude_types)

    # Duration query — grouped by feeder_id
    duration_query = f"""
        SELECT
            feeder_id,
            COALESCE(SUM(
                GREATEST(
                    EXTRACT(EPOCH FROM (
                        LEAST(COALESCE(restored_at, %s), %s) - GREATEST(occurred_at, %s)
                    )) / 3600.0,
                    0
                )
            ), 0) as total_hours
        FROM technical_feederinterruption
        WHERE feeder_id IN ({feeder_placeholders})
            AND (
                (occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
                OR (occurred_at < %s AND (restored_at IS NULL OR restored_at >= %s))
            )
            {exclusion_clause}
        GROUP BY feeder_id
    """
    duration_params = (
        [now, end_of_period, start_of_period]
        + list(feeder_ids)
        + [from_date, to_date, start_of_period, start_of_period]
        + extra_params
    )

    # Count query — only interruptions that STARTED in period (FTC)
    count_query = f"""
        SELECT
            feeder_id,
            COUNT(*) as interruption_count
        FROM technical_feederinterruption
        WHERE feeder_id IN ({feeder_placeholders})
            AND (occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
            {exclusion_clause}
        GROUP BY feeder_id
    """
    count_params = list(feeder_ids) + [from_date, to_date] + extra_params

    with connection.cursor() as cursor:
        cursor.execute(duration_query, duration_params)
        duration_rows = {row[0]: float(row[1]) for row in cursor.fetchall()}

        cursor.execute(count_query, count_params)
        count_rows = {row[0]: int(row[1]) for row in cursor.fetchall()}

    result = {}
    for fid in feeder_ids:
        total_hours = duration_rows.get(fid, 0.0)
        avg = total_hours / period_days if period_days > 0 else 0
        avg = max(0, min(avg, 24.0))
        result[fid] = (round(avg, 2), count_rows.get(fid, 0))

    return result


def get_feeder_availability_summary_optimized(from_date, to_date, mode, state=None, business_district=None, voltage_level=None):
    """
    HIGH-PERFORMANCE feeder availability summary.

    BEFORE: N feeders × 5 queries = 500 queries for 100 feeders.
    AFTER:  3 bulk queries total regardless of how many feeders.

    Still returns the same per-feeder structure as before.
    """
    today = timezone.now().date()
    if from_date > today:
        return []

    feeders_qs = (
        Feeder.objects
        .filter(is_onboarded=True)
        .select_related('business_district__state', 'substation')
    )
    if voltage_level:
        feeders_qs = feeders_qs.filter(voltage_level=voltage_level)
    if business_district:
        feeders_qs = feeders_qs.filter(business_district__name=business_district)
    elif state:
        feeders_qs = feeders_qs.filter(business_district__state__name=state)

    feeders = list(feeders_qs)
    if not feeders:
        return []

    feeder_ids = [f.id for f in feeders]

    # ── 3 bulk queries ──────────────────────────────────────────────────────
    supply_map = _bulk_supply_hours(feeder_ids, from_date, to_date)
    interrupt_map = _bulk_interruption_metrics(feeder_ids, from_date, to_date)
    turnaround_map = _bulk_interruption_metrics(
        feeder_ids, from_date, to_date, exclude_types=list(TURNAROUND_EXCLUSIONS)
    )
    # ────────────────────────────────────────────────────────────────────────

    result = []
    for feeder in feeders:
        fid = feeder.id
        avg_supply = round(min(supply_map.get(fid, 0.0), 24.0), 2)
        avg_duration = round(24.0 - avg_supply, 2)          # always sums to 24
        _, ftc = interrupt_map.get(fid, (0.0, 0))           # ftc count only
        turnaround, _ = turnaround_map.get(fid, (0.0, 0))

        result.append({
            "feeder_name": feeder.name,
            "feeder_slug": feeder.slug,
            "voltage_level": feeder.voltage_level,
            "substation_name": feeder.substation.name if feeder.substation else "",
            "avg_hours_of_supply": avg_supply,
            "duration_of_interruptions": avg_duration,
            "turnaround_time": round(min(turnaround, 24.0), 2),
            "ftc": ftc,
            "_source": f"bulk_sql_{mode}",
        })

    return result




class FeederAvailabilityOverview(APIView):
    """
    Optimized feeder availability overview API supporting multiple modes.
    
    UPDATED: Only considers ONBOARDED feeders for all calculations.
    
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
            "substation_name": "Substation Name",
            "avg_hours_of_supply": 18.5,        // Hours/day (0-24)
            "duration_of_interruptions": 3.2,   // Hours/day (0-24) - All active interruptions
            "turnaround_time": 1.5,             // Hours/day (0-24) - Local faults only
            "ftc": 12                           // Count of interruptions that occurred in period
        }
    ]
    
    Key Metrics (CORRECTED - ONBOARDED FEEDERS ONLY):
    - avg_hours_of_supply: Average hours per day of electricity supply (0-24)
    - duration_of_interruptions: Average hours per day of ALL interruptions active during period (0-24)
    - turnaround_time: Average hours per day of LOCAL faults active during period (0-24)
    - ftc: Feeder Tripping Count - total number of interruptions that OCCURRED in period
    - substation_name: Name of the injection substation to which the feeder belongs
    
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
        
        # Get feeder availability data using optimized method (ONBOARDED feeders only, voltage-filtered)
        feeder_type_param = request.GET.get("feeder_type", "")
        voltage_level = feeder_type_param if feeder_type_param in ("11kv", "33kv") else None
        
        data = get_feeder_availability_summary_optimized(
            from_date=from_date,
            to_date=to_date,
            mode=mode,
            state=state,
            business_district=business_district,
            voltage_level=voltage_level,
        )
        
        # Remove internal source field for response
        clean_data = []
        for item in data:
            clean_item = {k: v for k, v in item.items() if not k.startswith('_')}
            clean_data.append(clean_item)
        
        # Return data directly (bypassing serializer to include substation_name)
        return Response(clean_data)


# Utility functions for backward compatibility

def get_feeder_availability_summary(month=None, year=None, from_date=None, to_date=None, state=None, business_district=None):
    """
    Legacy function maintained for backward compatibility.
    Now uses the optimized calculation method.
    
    UPDATED: Only returns ONBOARDED feeders.
    """
    # Determine date range
    if month and year:
        from_date = datetime(year, month, 1).date()
        to_date = (datetime(year, month, 1) + relativedelta(months=1) - timedelta(days=1)).date()
        mode = "monthly"
    elif from_date and to_date:
        if isinstance(from_date, str):
            from_date = datetime.strptime(from_date, '%Y-%m-%d').date()
        if isinstance(to_date, str):
            to_date = datetime.strptime(to_date, '%Y-%m-%d').date()
        mode = "custom"
    else:
        # Default to current month
        now = datetime.now()
        from_date = datetime(now.year, now.month, 1).date()
        to_date = (datetime(now.year, now.month, 1) + relativedelta(months=1) - timedelta(days=1)).date()
        mode = "monthly"
    
    return get_feeder_availability_summary_optimized(
        from_date=from_date,
        to_date=to_date,
        mode=mode,
        state=state,
        business_district=business_district
    )