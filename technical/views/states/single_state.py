# technical/views/states/single_state.py
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Sum, Avg, Max, Q
from django.db import connection
from django.shortcuts import get_object_or_404
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from common.models import State, Feeder
from technical.models import HourlyLoad, FeederInterruption
from commercial.models import Customer
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


def get_month_range(year, month):
    """Get start and end dates for a given year/month"""
    start = datetime(year, month, 1).date()
    end = (start + relativedelta(months=1) - timedelta(days=1))
    return start, end


def calculate_state_hours_of_supply_sql(state_id, from_date, to_date):
    """
    Calculate average hours of supply per day for a state.
    
    UPDATED Logic:
    - Only considers ONBOARDED feeders
    - Numerator: Sum of all hours supplied across all ONBOARDED feeders with data in the state
    - Denominator: Total ONBOARDED feeders in state × Days in period
    - This properly accounts for onboarded feeders with no data (they contribute 0)
    """
    period_days = (to_date - from_date).days + 1
    
    # Get total ONBOARDED feeders in state
    feeder_count_query = """
        SELECT COUNT(DISTINCT f.id)
        FROM common_feeder f
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
            AND f.is_onboarded = TRUE
    """
    
    # Get total hours supplied across ONBOARDED feeders only
    hours_query = """
        SELECT 
            COUNT(DISTINCT CONCAT(hl.feeder_id, '-', hl.date, '-', hl.hour)) as total_hours
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
            AND f.is_onboarded = TRUE
            AND hl.date BETWEEN %s AND %s
            AND hl.load_mw > 0
    """
    
    with connection.cursor() as cursor:
        # Get onboarded feeder count
        cursor.execute(feeder_count_query, [state_id])
        result = cursor.fetchone()
        total_feeders = result[0] if result and result[0] else 0
        
        if total_feeders == 0:
            return 0.0
        
        # Get total hours
        cursor.execute(hours_query, [state_id, from_date, to_date])
        result = cursor.fetchone()
        total_hours = result[0] if result and result[0] else 0
    
    # Average = Total hours / (Total onboarded feeders × Days)
    avg_hours_per_day = total_hours / (total_feeders * period_days)
    
    return round(min(avg_hours_per_day, 24.0), 2)


def calculate_state_interruption_metrics_sql(state_id, from_date, to_date, exclude_types=None):
    """
    Calculate average interruption duration per day for a state.
    
    UPDATED Logic:
    - Only considers ONBOARDED feeders
    - Includes ALL interruptions active during the period (not just those that started in the period)
    - Calculates only the hours that fall within the filtered period boundaries
    - Numerator: Sum of all interruption hours across all ONBOARDED feeders with interruptions
    - Denominator: Total ONBOARDED feeders in state × Days in period
    - This properly accounts for onboarded feeders with no interruptions (they contribute 0)
    
    Returns:
        tuple: (avg_duration_per_day, total_interruption_count)
            - avg_duration_per_day: Average interruption hours per day
            - total_interruption_count: COUNT of interruptions that occurred in period (for FTC)
    """
    period_days = (to_date - from_date).days + 1
    
    start_of_period = timezone.make_aware(
        datetime.combine(from_date, datetime.min.time())
    )
    end_of_period = timezone.make_aware(
        datetime.combine(to_date, datetime.max.time())
    )
    
    # Get total ONBOARDED feeders in state
    feeder_count_query = """
        SELECT COUNT(DISTINCT f.id)
        FROM common_feeder f
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
            AND f.is_onboarded = TRUE
    """
    
    # Build exclusion clause
    exclusion_clause = ""
    max_hours = period_days * 24.0
    duration_params = [end_of_period, end_of_period, start_of_period, max_hours, state_id, from_date, end_of_period, start_of_period, start_of_period]
    
    # Parameters for count calculation (only interruptions that occurred in period)
    count_params = [state_id, from_date, to_date]
    
    if exclude_types:
        placeholders = ','.join(['%s'] * len(exclude_types))
        exclusion_clause = f"AND fi.interruption_type NOT IN ({placeholders})"
        duration_params.extend(exclude_types)
        count_params.extend(exclude_types)
    
    # Calculate per-feeder totals first, then cap each at (24 * period_days)
    # Only considers ONBOARDED feeders
    interruption_duration_query = f"""
        SELECT 
            COALESCE(SUM(capped_hours), 0) as total_hours
        FROM (
            SELECT 
                fi.feeder_id,
                LEAST(
                    SUM(
                        GREATEST(
                            EXTRACT(EPOCH FROM (
                                LEAST(COALESCE(restored_at, %s), %s) - GREATEST(occurred_at, %s)
                            )) / 3600.0,
                            0
                        )
                    ),
                    %s
                ) as capped_hours
            FROM technical_feederinterruption fi
            INNER JOIN common_feeder f ON fi.feeder_id = f.id
            INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
            WHERE bd.state_id = %s
                AND f.is_onboarded = TRUE
                AND (
                    DATE(fi.occurred_at) BETWEEN %s AND DATE(%s)
                    OR (fi.occurred_at < %s AND (fi.restored_at IS NULL OR fi.restored_at >= %s))
                )
                {exclusion_clause}
            GROUP BY fi.feeder_id
        ) per_feeder_totals
    """
    
    # Separate query for count (only interruptions that occurred in period, ONBOARDED feeders only)
    interruption_count_query = f"""
        SELECT COUNT(*) as total_interruptions
        FROM technical_feederinterruption fi
        INNER JOIN common_feeder f ON fi.feeder_id = f.id
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
            AND f.is_onboarded = TRUE
            AND DATE(fi.occurred_at) BETWEEN %s AND %s
            {exclusion_clause}
    """
    
    with connection.cursor() as cursor:
        # Get onboarded feeder count
        cursor.execute(feeder_count_query, [state_id])
        result = cursor.fetchone()
        total_feeders = result[0] if result and result[0] else 0
        
        if total_feeders == 0:
            return 0.0, 0
        
        # Get interruption duration (all active during period)
        cursor.execute(interruption_duration_query, duration_params)
        result = cursor.fetchone()
        total_hours = float(result[0]) if result and result[0] else 0
        
        # Get interruption count (only those that occurred in period)
        cursor.execute(interruption_count_query, count_params)
        result = cursor.fetchone()
        total_interruptions = result[0] if result and result[0] else 0
    
    # Average = Total hours / (Total onboarded feeders × Days)
    avg_hours_per_day = total_hours / (total_feeders * period_days)
    
    # Ensure non-negative and cap at 24
    avg_hours_per_day = max(0, min(avg_hours_per_day, 24.0))
    
    return round(avg_hours_per_day, 2), int(total_interruptions)


def calculate_state_avg_interruption_duration_sql(state_id, from_date, to_date):
    """
    Calculate average duration per interruption event for a state using raw SQL.
    
    INCLUDES:
    1. Interruptions that OCCURRED within the period (resolved or ongoing)
    2. Interruptions that started BEFORE the period but are still ongoing (not resolved)
    
    CORRECTED: Only counts the hours that fall WITHIN the filtered period.
    - If interruption started before period: counts from period start
    - If interruption ongoing: counts to NOW (if today) or end of period
    - If interruption ended after period: counts to period end
    
    Only considers ONBOARDED feeders.
    
    Formula: SUM(clipped interruption durations) / COUNT(interruptions)
    Result: Average hours per interruption event (not per day)
    
    For ongoing interruptions, uses NOW as the end time.
    """
    now = timezone.now()
    today = now.date()
    
    # ✨ CRITICAL: If querying future dates, return 0 (no data available yet)
    if from_date > today:
        return 0.0
    
    start_of_period = timezone.make_aware(
        datetime.combine(from_date, datetime.min.time())
    )
    
    # CRITICAL: If filtering for today, use NOW instead of end of day
    if to_date == today:
        end_of_period = now  # Current time (e.g., 14:00)
    else:
        end_of_period = timezone.make_aware(
            datetime.combine(to_date, datetime.max.time())
        )
    
    query = """
        SELECT 
            COUNT(*) as interruption_count,
            COALESCE(SUM(
                EXTRACT(EPOCH FROM (
                    LEAST(COALESCE(fi.restored_at, %s), %s) - GREATEST(fi.occurred_at, %s)
                )) / 3600.0
            ), 0) as total_hours
        FROM technical_feederinterruption fi
        INNER JOIN common_feeder f ON fi.feeder_id = f.id
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
            AND f.is_onboarded = TRUE
            AND (
                (fi.occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
                OR (fi.occurred_at < %s AND fi.restored_at IS NULL)
            )
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [now, end_of_period, start_of_period, state_id, from_date, to_date, start_of_period])
        result = cursor.fetchone()
        interruption_count = result[0] if result else 0
        total_hours = float(result[1]) if result else 0
    
    # Calculate average
    avg_duration = total_hours / interruption_count if interruption_count > 0 else 0
    
    return round(avg_duration, 2)


def calculate_state_energy_sql(state_id, from_date, to_date):
    """
    Calculate total energy delivered for a state using HourlyLoad.
    Sum of MW × 1 hour = MWh
    
    UPDATED: Only considers ONBOARDED feeders.
    """
    query = """
        SELECT 
            COALESCE(SUM(hl.load_mw), 0) as total_energy
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
            AND f.is_onboarded = TRUE
            AND hl.date BETWEEN %s AND %s
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [state_id, from_date, to_date])
        result = cursor.fetchone()
        total_energy = result[0] if result and result[0] else 0
    
    return round(float(total_energy), 2)


def get_previous_periods(start_date, period_days, count=4):
    """Get previous periods for historical comparison"""
    periods = []
    
    for i in range(count, 0, -1):
        if period_days == 1:  # Daily
            period_start = start_date - timedelta(days=i)
            period_end = period_start
            label = period_start.strftime("%a") if i > 1 else "Yesterday"
        elif period_days == 7:  # Weekly
            period_start = start_date - timedelta(weeks=i)
            period_end = period_start + timedelta(days=6)
            label = f"Wk{count-i+1}"
        elif 28 <= period_days <= 31:  # Monthly
            temp_date = start_date - relativedelta(months=i)
            period_start, period_end = get_month_range(temp_date.year, temp_date.month)
            label = temp_date.strftime("%b")
        else:  # Custom cycles
            period_start = start_date - timedelta(days=period_days * i)
            period_end = period_start + timedelta(days=period_days - 1)
            label = f"C{count-i+1}"
        
        periods.append({
            "start": period_start,
            "end": period_end,
            "label": label
        })
    
    return periods


def calculate_state_metrics_for_period(state_id, from_date, to_date):
    """
    Calculate all metrics for a single period.
    
    UPDATED: Only considers ONBOARDED feeders.
    
    CORRECTED: Duration includes all active interruptions,
    count only includes interruptions that occurred in period.
    """
    # 1. Supply hours (ONBOARDED feeders only)
    avg_supply = float(calculate_state_hours_of_supply_sql(state_id, from_date, to_date))
    
    # 2. Interruption duration (all types, includes ALL active interruptions, ONBOARDED feeders only)
    avg_duration, total_interruptions = calculate_state_interruption_metrics_sql(
        state_id, from_date, to_date
    )
    avg_duration = float(avg_duration)
    
    # 3. Turnaround time (exclude L/S and TCN, includes ALL active local faults, ONBOARDED feeders only)
    turnaround_time, _ = calculate_state_interruption_metrics_sql(
        state_id, from_date, to_date, exclude_types=TURNAROUND_EXCLUSIONS
    )
    turnaround_time = float(turnaround_time)
    
    # 4. Average interruption duration (hours per interruption event, ONBOARDED feeders only)
    avg_int_duration = float(calculate_state_avg_interruption_duration_sql(
        state_id, from_date, to_date
    ))
    
    # 5. Energy delivered (from HourlyLoad, ONBOARDED feeders only)
    total_energy = float(calculate_state_energy_sql(state_id, from_date, to_date))
    
    # 6. Feeder count (ONBOARDED feeders only)
    feeder_count = Feeder.objects.filter(
        business_district__state_id=state_id,
        is_onboarded=True
    ).count()
    
    # Validate time-based metrics
    avg_supply = min(avg_supply, 24.0)
    avg_duration = min(avg_duration, 24.0)
    turnaround_time = min(turnaround_time, 24.0)
    
    return {
        "avg_supply": round(avg_supply, 2),
        "avg_duration": round(avg_duration, 2),
        "turnaround_time": round(turnaround_time, 2),
        "avg_interruption_duration": round(avg_int_duration, 2),
        "interruptions": int(total_interruptions),
        "energy_delivered": round(total_energy, 2),
        "feeder_count": int(feeder_count)
    }


def build_metrics_with_history(state, start_date, end_date, period_days):
    """Build metrics response with historical data"""
    # Get current period metrics
    current = calculate_state_metrics_for_period(state.id, start_date, end_date)
    
    # Get historical periods
    previous_periods = get_previous_periods(start_date, period_days)
    
    history_data = []
    for period in previous_periods:
        hist_metrics = calculate_state_metrics_for_period(
            state.id, 
            period["start"], 
            period["end"]
        )
        history_data.append({
            "month": period["label"],  # Keep "month" for backward compatibility
            **hist_metrics
        })
    
    # Calculate deltas (current vs most recent historical)
    previous = history_data[-1] if history_data else {}
    
    def calc_delta(current_val, prev_val):
        # Ensure both values are floats
        current_val = float(current_val) if current_val is not None else 0.0
        prev_val = float(prev_val) if prev_val is not None else 0.0
        
        if prev_val and prev_val != 0:
            return round(((current_val - prev_val) / prev_val) * 100, 2)
        elif current_val == 0 and prev_val == 0:
            return 0.0
        elif prev_val == 0 and current_val != 0:
            return 100.0
        return None
    
    # Build response maintaining backward compatibility
    metrics = {}
    for key, current_val in current.items():
        prev_val = previous.get(key, 0)
        metrics[key] = {
            "current": current_val,
            "delta": calc_delta(current_val, prev_val),
            "history": history_data
        }
    
    return metrics


def get_top_bottom_feeders_sql(state_id, from_date, to_date):
    """
    Get top 5 and bottom 5 feeders by peak load
    
    UPDATED: Only considers ONBOARDED feeders.
    """
    query = """
        SELECT 
            f.name as feeder_name,
            f.slug as feeder_slug,
            s.name as substation_name,
            f.voltage_level,
            MAX(hl.load_mw) as peak_load
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id
        INNER JOIN common_injectionsubstation s ON f.substation_id = s.id
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
            AND f.is_onboarded = TRUE
            AND hl.date BETWEEN %s AND %s
        GROUP BY f.id, f.name, f.slug, s.name, f.voltage_level
        ORDER BY peak_load DESC
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [state_id, from_date, to_date])
        results = cursor.fetchall()
    
    if not results:
        return [], []
    
    # Format results
    formatted = [
        {
            "feeder": row[0],
            "feeder_slug": row[1],
            "substation": row[2],
            "voltage_level": row[3],
            "peak": round(float(row[4] or 0), 2)
        }
        for row in results
    ]
    
    # Get top 5 and bottom 5
    top_5 = formatted[:5]
    bottom_5 = list(reversed(formatted[-5:])) if len(formatted) >= 5 else []
    
    return top_5, bottom_5


def get_load_trend_hourly_sql(state_id, day):
    """
    Get hourly load trend for a specific day (for daily mode)
    
    UPDATED: Only considers ONBOARDED feeders.
    
    Returns hourly breakdown (0-23) for the specified day
    """
    if not day:
        return []
    
    query = """
        SELECT 
            hl.hour,
            AVG(hl.load_mw) as avg_load
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
            AND f.is_onboarded = TRUE
            AND hl.date = %s
        GROUP BY hl.hour
        ORDER BY hl.hour
    """
    
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, [state_id, day])
            results = cursor.fetchall()
        
        return [
            {
                "hour": row[0],
                "value": round(float(row[1] or 0), 2)
            }
            for row in results
        ]
    except Exception as e:
        print(f"Error getting hourly load trend: {str(e)}")
        return []


def get_load_trend_daily_sql(state_id, from_date, to_date):
    """
    Get daily load trend for a date range (for monthly/weekly/custom modes)
    
    UPDATED: Only considers ONBOARDED feeders.
    
    Returns daily averages for each day in the range
    """
    query = """
        SELECT 
            hl.date,
            AVG(hl.load_mw) as avg_load
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
            AND f.is_onboarded = TRUE
            AND hl.date BETWEEN %s AND %s
        GROUP BY hl.date
        ORDER BY hl.date
    """
    
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, [state_id, from_date, to_date])
            results = cursor.fetchall()
        
        return [
            {
                "date": row[0].isoformat(),
                "value": round(float(row[1] or 0), 2)
            }
            for row in results
        ]
    except Exception as e:
        print(f"Error getting daily load trend: {str(e)}")
        return []


def get_load_trend_adaptive(state_id, from_date, to_date, mode, specific_date=None):
    """
    Get load trend adapted to the query mode
    
    Args:
        state_id: ID of the state
        from_date: Start date of the period
        to_date: End date of the period
        mode: Query mode (daily, monthly, weekly, custom, yearly)
        specific_date: Optional specific date for trend (overrides mode logic)
    
    Returns:
        dict: Load trend data with appropriate format for the mode
    """
    if mode == "daily" or specific_date:
        # For daily mode or when specific date is provided: show hourly breakdown
        trend_date = specific_date if specific_date else from_date
        series = get_load_trend_hourly_sql(state_id, trend_date)
        
        return {
            "unit": "MW",
            "mode": "hourly",
            "date": trend_date.isoformat() if trend_date else None,
            "series": series
        }
    else:
        # For monthly/weekly/custom/yearly: show daily averages
        series = get_load_trend_daily_sql(state_id, from_date, to_date)
        
        if mode == "monthly":
            date_label = f"{from_date.year}-{from_date.month:02d}"
        elif mode == "yearly":
            date_label = str(from_date.year)
        else:  # weekly, custom
            date_label = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}"
        
        return {
            "unit": "MW",
            "mode": "daily",
            "date": date_label,
            "series": series
        }


@api_view(["GET"])
def state_technical_summary(request):
    """
    Optimized technical summary for a specific state supporting multiple modes.
    
    UPDATED: Only considers ONBOARDED feeders for all calculations.
    
    Modes:
    - monthly: Month-based filtering (year, month params)
    - yearly: Year-based filtering (year param)
    - daily: Single day filtering (from_date param)
    - weekly: Week range filtering (from_date, to_date params)
    - custom: Custom date range filtering (from_date, to_date params)
    - range: Legacy range mode (same as custom)
    
    Query Parameters:
    - state: State name (required)
    - mode: monthly, yearly, daily, weekly, custom, range
    - For monthly: year, month
    - For yearly: year
    - For others: from_date, to_date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
    - date: Specific date for load trend (optional, ISO format)
    
    Examples:
    - ?state=Lagos&mode=monthly&year=2024&month=9
    - ?state=Lagos&mode=yearly&year=2024
    - ?state=Lagos&mode=daily&from_date=2024-09-15
    - ?state=Lagos&mode=weekly&from_date=2024-09-01T00:00:00.000Z&to_date=2024-09-07T23:59:59.999Z
    - ?state=Lagos&mode=custom&from_date=2024-09-01T00:00:00.000Z&to_date=2024-09-15T23:59:59.999Z
    
    Key Metrics (CORRECTED - ONBOARDED FEEDERS ONLY):
    - avg_supply: Average hours per day across all ONBOARDED feeders in state (0-24)
    - avg_duration: Average interruption hours per day across all ONBOARDED feeders in state (0-24)
      * Includes ALL interruptions active during the period
      * Calculates only hours that fall within the period
    - turnaround_time: Average local fault hours per day across all ONBOARDED feeders in state (0-24)
      * Includes ALL local faults active during the period
      * Calculates only hours that fall within the period
    - avg_interruption_duration: Average hours per interruption event (not per day)
      * Includes interruptions that occurred in period AND ongoing ones from before
      * Formula: Total duration of all interruptions / Count of interruptions
    - interruptions: Total interruption count (occurred in period, ONBOARDED feeders only)
    - energy_delivered: Total energy in MWh (calculated from HourlyLoad, ONBOARDED feeders only)
    - feeder_count: Number of ONBOARDED feeders in state
    
    Response maintains backward compatibility with original structure.
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
    
    # Parse specific date for load trend (optional override)
    day_param = request.GET.get("date")
    specific_date = None
    if day_param:
        try:
            specific_date = _parse_iso_date(day_param)
        except ValueError:
            specific_date = None
    
    # Calculate period days
    period_days = (to_date - from_date).days + 1
    
    # Get metrics with history (ONBOARDED feeders only)
    metrics = build_metrics_with_history(state, from_date, to_date, period_days)
    
    # Get top and bottom feeders (ONBOARDED feeders only)
    top_feeders, bottom_feeders = get_top_bottom_feeders_sql(state.id, from_date, to_date)
    
    # Get adaptive load trend (ONBOARDED feeders only)
    # - For daily mode: returns hourly breakdown
    # - For monthly/weekly/custom/yearly: returns daily averages
    load_trend = get_load_trend_adaptive(state.id, from_date, to_date, mode, specific_date)
    
    # Format period label for backward compatibility
    if mode == "monthly":
        period_label = f"{from_date.year}-{from_date.month:02d}"
    elif mode == "yearly":
        period_label = str(from_date.year)
    elif mode == "daily":
        period_label = from_date.strftime("%Y-%m-%d")
    else:  # weekly, custom
        period_label = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}"
    
    # Build response maintaining original structure
    response_data = {
        "state": state_name,
        "month": period_label,  # Keep original field name
        "top_feeders": top_feeders,
        "bottom_feeders": bottom_feeders,
        "load_trend": load_trend,
        "metrics": metrics
    }
    
    return Response(response_data)