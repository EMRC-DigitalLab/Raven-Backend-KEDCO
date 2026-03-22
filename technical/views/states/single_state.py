# technical/views/states/single_state.py
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from django.db import connection
from django.db.models import Avg, Count, Max, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from commercial.models import Customer
from common.models import Feeder, State
from technical.constants import TURNAROUND_EXCLUSIONS
from technical.models import EnergyDelivered, FeederInterruption, HourlyLoad
from technical.utils.energy_utils import calculate_energy_delivered


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


def get_month_range(year, month):
    """Get start and end dates for a given year/month"""
    start = datetime(year, month, 1).date()
    end = (start + relativedelta(months=1) - timedelta(days=1))
    return start, end


def calculate_state_energy_delivered_sql(state_id, from_date, to_date, voltage_level=None):
    """
    Calculate total energy delivered for a state using hybrid approach.
    OPTIMIZED: Uses raw SQL with EnergyDelivered primary, HourlyLoad fallback.

    Priority:
    1. Use EnergyDelivered if available for a feeder-date combination
    2. Fall back to HourlyLoad sum for feeder-dates without EnergyDelivered

    Only considers ONBOARDED feeders. Optionally filtered by voltage_level ('11kv' or '33kv').

    Returns:
        Total energy in MWh
    """
    voltage_clause = "AND f.voltage_level = %s" if voltage_level else ""
    # Use raw SQL for optimal performance
    query = f"""
        WITH date_series AS (
            SELECT generate_series(
                %s::date,
                %s::date,
                '1 day'::interval
            )::date AS date
        ),
        onboarded_feeders AS (
            SELECT DISTINCT f.id as feeder_id
            FROM common_feeder f
            INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
            WHERE bd.state_id = %s
                AND f.is_onboarded = TRUE
                {voltage_clause}
        ),
        feeder_dates AS (
            SELECT 
                of.feeder_id,
                ds.date
            FROM onboarded_feeders of
            CROSS JOIN date_series ds
        ),
        energy_delivered_data AS (
            SELECT 
                fd.feeder_id,
                fd.date,
                ed.energy_mwh as delivered_energy
            FROM feeder_dates fd
            LEFT JOIN technical_energydelivered ed 
                ON ed.feeder_id = fd.feeder_id 
                AND ed.date = fd.date
        ),
        hourly_load_data AS (
            SELECT 
                feeder_id,
                date,
                SUM(load_mw) as hourly_energy
            FROM technical_hourlyload
            WHERE date BETWEEN %s AND %s
                AND feeder_id IN (SELECT feeder_id FROM onboarded_feeders)
            GROUP BY feeder_id, date
        )
        SELECT 
            COALESCE(
                SUM(COALESCE(ed.delivered_energy, hl.hourly_energy, 0)),
                0
            ) as total_energy
        FROM energy_delivered_data ed
        LEFT JOIN hourly_load_data hl 
            ON hl.feeder_id = ed.feeder_id 
            AND hl.date = ed.date
    """
    
    # params order: date_series(%s,%s), onboarded_feeders(state_id, [voltage_level]), hourly_load(from,to)
    params = [from_date, to_date, state_id] + ([voltage_level] if voltage_level else []) + [from_date, to_date]

    with connection.cursor() as cursor:
        cursor.execute(query, params)
        result = cursor.fetchone()
        total_energy = float(result[0]) if result and result[0] else 0.0
    
    return round(total_energy, 2)


def calculate_state_hours_of_supply_sql(state_id, from_date, to_date, voltage_level=None):
    """
    Calculate average hours of supply per day for a state.

    For single-day queries (especially today): Returns total hours per feeder (not daily average)
    For multi-day queries: Returns average hours per day per feeder

    UPDATED Logic:
    - Only considers ONBOARDED feeders
    - Numerator: Sum of all hours supplied across all ONBOARDED feeders with data in the state
    - Denominator: Total ONBOARDED feeders in state × Days (or just feeders for single day)
    - This properly accounts for onboarded feeders with no data (they contribute 0)
    """
    voltage_clause = "AND f.voltage_level = %s" if voltage_level else ""

    # Get total ONBOARDED feeders in state
    feeder_count_query = f"""
        SELECT COUNT(DISTINCT f.id)
        FROM common_feeder f
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
            AND f.is_onboarded = TRUE
            AND (f.onboarded_at IS NULL OR f.onboarded_at <= %s)
            {voltage_clause}
    """

    # Get total hours supplied across ONBOARDED feeders only
    hours_query = f"""
        SELECT
            COUNT(DISTINCT CONCAT(hl.feeder_id, '-', hl.date, '-', hl.hour)) as total_hours
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
            AND f.is_onboarded = TRUE
            AND (f.onboarded_at IS NULL OR f.onboarded_at <= %s)
            {voltage_clause}
            AND hl.date BETWEEN %s AND %s
            AND hl.load_mw > 0
    """

    feeder_count_params = [state_id, to_date] + ([voltage_level] if voltage_level else [])
    hours_params = [state_id, to_date] + ([voltage_level] if voltage_level else []) + [from_date, to_date]

    with connection.cursor() as cursor:
        # Get onboarded feeder count
        cursor.execute(feeder_count_query, feeder_count_params)
        result = cursor.fetchone()
        total_feeders = result[0] if result and result[0] else 0

        if total_feeders == 0:
            return 0.0

        # Get total hours
        cursor.execute(hours_query, hours_params)
        result = cursor.fetchone()
        total_hours = result[0] if result and result[0] else 0
    
    # ✅ CRITICAL: For single-day queries, return average hours per feeder (not daily average)
    # For multi-day queries, return average hours per day per feeder
    if from_date == to_date:
        # Single day: Average hours per feeder
        avg_hours = total_hours / total_feeders if total_feeders > 0 else 0
    else:
        # Multi-day: Average hours per day per feeder
        period_days = (to_date - from_date).days + 1
        avg_hours = total_hours / (total_feeders * period_days) if (total_feeders * period_days) > 0 else 0
    
    return round(min(avg_hours, 24.0), 2)


def calculate_state_interruption_metrics_sql(state_id, from_date, to_date, exclude_types=None, voltage_level=None):
    """
    Calculate average interruption duration per day for a state.
    Optionally filtered by voltage_level ('11kv' or '33kv').
    """
    now = timezone.now()
    today = now.date()
    
    if from_date > today:
        return 0.0, 0
    
    is_single_day = (from_date == to_date)
    
    if is_single_day:
        max_hours_per_feeder = 24.0
    else:
        period_days = (to_date - from_date).days + 1
        max_hours_per_feeder = 24.0 * period_days
    
    start_of_period = timezone.make_aware(
        datetime.combine(from_date, datetime.min.time())
    )
    end_of_period = timezone.make_aware(
        datetime.combine(to_date, datetime.max.time())
    )
    
    voltage_clause = "AND f.voltage_level = %s" if voltage_level else ""
    feeder_count_query = f"""
        SELECT COUNT(DISTINCT f.id)
        FROM common_feeder f
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
            AND f.is_onboarded = TRUE
            AND (f.onboarded_at IS NULL OR f.onboarded_at <= %s)
            {voltage_clause}
    """
    
    exclusion_clause = ""
    duration_params = [end_of_period, end_of_period, start_of_period, max_hours_per_feeder, state_id]
    if voltage_level:
        duration_params.append(voltage_level)
    duration_params += [start_of_period, end_of_period, start_of_period, start_of_period]
    
    count_params = [state_id]
    if voltage_level:
        count_params.append(voltage_level)
    count_params += [start_of_period, end_of_period]
    
    if exclude_types:
        placeholders = ','.join(['%s'] * len(exclude_types))
        exclusion_clause = f"AND fi.interruption_type NOT IN ({placeholders})"
        duration_params.extend(exclude_types)
        count_params.extend(exclude_types)
    
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
                {voltage_clause}
                AND (
                    fi.occurred_at >= %s AND fi.occurred_at <= %s
                    OR (fi.occurred_at < %s AND (fi.restored_at IS NULL OR fi.restored_at >= %s))
                )
                {exclusion_clause}
            GROUP BY fi.feeder_id
        ) per_feeder_totals
    """
    
    interruption_count_query = f"""
        SELECT COUNT(*) as total_interruptions
        FROM technical_feederinterruption fi
        INNER JOIN common_feeder f ON fi.feeder_id = f.id
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
            AND f.is_onboarded = TRUE
            {voltage_clause}
            AND fi.occurred_at >= %s
            AND fi.occurred_at <= %s
            {exclusion_clause}
    """
    
    feeder_count_params = [state_id, to_date] + ([voltage_level] if voltage_level else [])
    with connection.cursor() as cursor:
        cursor.execute(feeder_count_query, feeder_count_params)
        result = cursor.fetchone()
        total_feeders = result[0] if result and result[0] else 0
        
        if total_feeders == 0:
            return 0.0, 0
        
        cursor.execute(interruption_duration_query, duration_params)
        result = cursor.fetchone()
        total_hours = float(result[0]) if result and result[0] else 0
        
        cursor.execute(interruption_count_query, count_params)
        result = cursor.fetchone()
        total_interruptions = result[0] if result and result[0] else 0
    
    if is_single_day:
        avg_hours = total_hours / total_feeders if total_feeders > 0 else 0
    else:
        avg_hours = total_hours / (total_feeders * period_days) if (total_feeders * period_days) > 0 else 0
    
    avg_hours = max(0, min(avg_hours, 24.0))
    
    return round(avg_hours, 2), int(total_interruptions)


def calculate_state_avg_interruption_duration_sql(state_id, from_date, to_date, voltage_level=None):
    """
    Calculate average duration per interruption event for a state using raw SQL.
    Optionally filtered by voltage_level ('11kv' or '33kv').
    """
    now = timezone.now()
    today = now.date()
    
    if from_date > today:
        return 0.0
    
    start_of_period = timezone.make_aware(
        datetime.combine(from_date, datetime.min.time())
    )
    
    if to_date == today:
        end_of_period = now
    else:
        end_of_period = timezone.make_aware(
            datetime.combine(to_date, datetime.max.time())
        )
    
    voltage_clause = "AND f.voltage_level = %s" if voltage_level else ""
    query = f"""
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
            {voltage_clause}
            AND (
                (fi.occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
                OR (fi.occurred_at < %s AND fi.restored_at IS NULL)
            )
    """
    
    params = [now, end_of_period, start_of_period, state_id]
    if voltage_level:
        params.append(voltage_level)
    params += [from_date, to_date, start_of_period]
    
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        result = cursor.fetchone()
        interruption_count = result[0] if result else 0
        total_hours = float(result[1]) if result else 0
    
    avg_duration = total_hours / interruption_count if interruption_count > 0 else 0
    
    return round(avg_duration, 2)


def get_previous_periods(start_date, period_days, count=4):
    """Get previous periods for historical comparison"""
    periods = []
    
    for i in range(count, 0, -1):
        if period_days == 1:  # Daily
            period_start = start_date - timedelta(days=i)
            period_end = period_start
            # Always use day name format (Mon, Tue, Wed, etc.)
            label = period_start.strftime("%a")
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


def calculate_state_metrics_for_period(state_id, from_date, to_date, voltage_level=None):
    """
    Calculate all metrics for a single period.
    Optionally filtered by voltage_level ('11kv' or '33kv').
    """
    avg_supply = float(calculate_state_hours_of_supply_sql(state_id, from_date, to_date, voltage_level=voltage_level))

    avg_duration, total_interruptions = calculate_state_interruption_metrics_sql(
        state_id, from_date, to_date, voltage_level=voltage_level
    )
    avg_duration = float(avg_duration)
    
    turnaround_time, _ = calculate_state_interruption_metrics_sql(
        state_id, from_date, to_date, exclude_types=TURNAROUND_EXCLUSIONS, voltage_level=voltage_level
    )
    turnaround_time = float(turnaround_time)
    
    avg_int_duration = float(calculate_state_avg_interruption_duration_sql(
        state_id, from_date, to_date, voltage_level=voltage_level
    ))
    
    feeder_qs = Feeder.objects.filter(
        business_district__state_id=state_id,
        is_onboarded=True
    )
    if voltage_level:
        feeder_qs = feeder_qs.filter(voltage_level=voltage_level)
    feeder_ids = list(feeder_qs.values_list('id', flat=True))
    feeder_count = len(feeder_ids)

    total_energy = calculate_energy_delivered(feeder_ids, from_date, to_date)['total_mwh']
    
    avg_supply = min(avg_supply, 24.0)
    avg_duration = round(24.0 - avg_supply, 2)  # always sums to 24 with supply
    turnaround_time = min(turnaround_time, 24.0)

    return {
        "avg_supply": round(avg_supply, 2),
        "avg_duration": avg_duration,
        "turnaround_time": round(turnaround_time, 2),
        "avg_interruption_duration": round(avg_int_duration, 2),
        "interruptions": int(total_interruptions),
        "energy_delivered": round(total_energy, 2),
        "feeder_count": int(feeder_count)
    }


def build_metrics_with_history(state, start_date, end_date, period_days, voltage_level=None):
    """Build metrics response with historical data"""
    current = calculate_state_metrics_for_period(state.id, start_date, end_date, voltage_level=voltage_level)
    
    previous_periods = get_previous_periods(start_date, period_days)
    
    history_data = []
    for period in previous_periods:
        hist_metrics = calculate_state_metrics_for_period(
            state.id, 
            period["start"], 
            period["end"],
            voltage_level=voltage_level
        )
        history_data.append({
            "month": period["label"],
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


def get_top_bottom_feeders_sql(state_id, from_date, to_date, voltage_level=None):
    """
    Get top 5 and bottom 5 feeders by peak load.
    Only considers ONBOARDED feeders. Optionally filtered by voltage_level ('11kv' or '33kv').
    """
    voltage_clause = "AND f.voltage_level = %s" if voltage_level else ""
    query = f"""
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
            {voltage_clause}
            AND hl.date BETWEEN %s AND %s
        GROUP BY f.id, f.name, f.slug, s.name, f.voltage_level
        ORDER BY peak_load DESC
    """

    params = [state_id] + ([voltage_level] if voltage_level else []) + [from_date, to_date]
    with connection.cursor() as cursor:
        cursor.execute(query, params)
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


def get_load_trend_hourly_sql(state_id, day, voltage_level=None):
    """
    Get hourly load trend for a specific day.
    Optionally filtered by voltage_level ('11kv' or '33kv').
    """
    if not day:
        return []

    today = timezone.now().date()
    now = timezone.now()

    if day == today:
        max_hour = now.hour
    else:
        max_hour = 23

    try:
        qs = HourlyLoad.objects.filter(
            feeder__business_district__state_id=state_id,
            feeder__is_onboarded=True,
            date=day,
        )
        if voltage_level:
            qs = qs.filter(feeder__voltage_level=voltage_level)

        results = qs.values('hour').annotate(avg_load=Avg('load_mw')).order_by('hour')

        loads_dict = {
            row['hour']: round(float(row['avg_load'] or 0), 2)
            for row in results
        }

        series = [
            {
                "hour": hour,
                "value": loads_dict.get(hour, 0)
            }
            for hour in range(max_hour + 1)
        ]

        return series

    except Exception as e:
        print(f"Error getting hourly load trend: {str(e)}")
        return []


def get_load_trend_daily_sql(state_id, from_date, to_date, voltage_level=None):
    """
    Get daily load trend for a date range.
    Optionally filtered by voltage_level ('11kv' or '33kv').
    """
    today = timezone.now().date()
    effective_end = min(to_date, today)

    try:
        qs = HourlyLoad.objects.filter(
            feeder__business_district__state_id=state_id,
            feeder__is_onboarded=True,
            date__gte=from_date,
            date__lte=effective_end,
        )
        if voltage_level:
            qs = qs.filter(feeder__voltage_level=voltage_level)

        results = qs.values('date').annotate(avg_load=Avg('load_mw')).order_by('date')

        loads_dict = {
            row['date']: round(float(row['avg_load'] or 0), 2)
            for row in results
        }

        series = []
        current_date = from_date

        while current_date <= effective_end:
            series.append({
                "date": current_date.isoformat(),
                "value": loads_dict.get(current_date, 0)
            })
            current_date += timedelta(days=1)

        return series

    except Exception as e:
        print(f"Error getting daily load trend: {str(e)}")
        return []


def get_load_trend_adaptive(state_id, from_date, to_date, mode, specific_date=None, voltage_level=None):
    """
    Get load trend adapted to the query mode.
    Optionally filtered by voltage_level ('11kv' or '33kv').
    """
    if mode == "daily" or specific_date:
        trend_date = specific_date if specific_date else from_date
        series = get_load_trend_hourly_sql(state_id, trend_date, voltage_level=voltage_level)
        
        return {
            "unit": "MW",
            "mode": "hourly",
            "date": trend_date.isoformat() if trend_date else None,
            "series": series
        }
    else:
        series = get_load_trend_daily_sql(state_id, from_date, to_date, voltage_level=voltage_level)
        
        if mode == "monthly":
            date_label = f"{from_date.year}-{from_date.month:02d}"
        elif mode == "yearly":
            date_label = str(from_date.year)
        else:
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
    
    UPDATED: 
    - Only considers ONBOARDED feeders for all calculations
    - Uses actual elapsed time for current periods (fractional days)
    - Uses timezone-aware datetime ranges for consistency
    - Uses hybrid energy calculation (EnergyDelivered + HourlyLoad fallback)
    - Fills missing values in load trends with 0
    - Stops at current time for current periods
    
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
      * Uses fractional days for current periods
    - avg_duration: Average interruption hours per day across all ONBOARDED feeders in state (0-24)
      * Includes ALL interruptions active during the period
      * Calculates only hours that fall within the period
      * Uses fractional days for current periods
    - turnaround_time: Average local fault hours per day across all ONBOARDED feeders in state (0-24)
      * Includes ALL local faults active during the period
      * Calculates only hours that fall within the period
      * Uses fractional days for current periods
    - avg_interruption_duration: Average hours per interruption event (not per day)
      * Includes interruptions that occurred in period AND ongoing ones from before
      * Formula: Total duration of all interruptions / Count of interruptions
    - interruptions: Total interruption count (occurred in period, ONBOARDED feeders only)
    - energy_delivered: Total energy in MWh (hybrid calculation, ONBOARDED feeders only)
    - feeder_count: Number of ONBOARDED feeders in state
    
    Response maintains backward compatibility with original structure.
    """
    state_name = request.GET.get("state")
    if not state_name:
        return Response({"error": "State parameter is required"}, status=400)
    
    state = get_object_or_404(State, name__iexact=state_name)
    
    try:
        from_date, to_date, mode = get_date_range_and_mode_from_request(request)
    except ValueError as e:
        return Response({"error": str(e)}, status=400)
    
    # ✅ Parse voltage level filter
    feeder_type_param = request.GET.get("feeder_type", "")
    voltage_level = feeder_type_param if feeder_type_param in ("11kv", "33kv") else None
    
    day_param = request.GET.get("date")
    specific_date = None
    if day_param:
        try:
            specific_date = _parse_iso_date(day_param)
        except ValueError:
            specific_date = None
    
    period_days = (to_date - from_date).days + 1
    
    # Get metrics with history (filtered by voltage_level)
    metrics = build_metrics_with_history(state, from_date, to_date, period_days, voltage_level=voltage_level)
    
    # Top/bottom feeders filtered by voltage_level
    top_feeders, bottom_feeders = get_top_bottom_feeders_sql(state.id, from_date, to_date, voltage_level=voltage_level)
    
    # Load trend filtered by voltage_level
    load_trend = get_load_trend_adaptive(state.id, from_date, to_date, mode, specific_date, voltage_level=voltage_level)
    
    if mode == "monthly":
        period_label = f"{from_date.year}-{from_date.month:02d}"
    elif mode == "yearly":
        period_label = str(from_date.year)
    elif mode == "daily":
        period_label = from_date.strftime("%Y-%m-%d")
    else:
        period_label = f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}"
    
    from technical.utils.compliance_utils import get_compliance_summary
    response_data = {
        "state": state_name,
        "month": period_label,
        "top_feeders": top_feeders,
        "bottom_feeders": bottom_feeders,
        "load_trend": load_trend,
        "metrics": metrics,
        "compliance": get_compliance_summary(
            from_date=from_date,
            to_date=to_date,
            state=state_name,
            voltage_level=voltage_level,
        ),
    }

    return Response(response_data)