# technical/views/districts/single_districts.py
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Avg, Count, Max, Sum
from django.db import connection
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from technical.models import HourlyLoad, FeederInterruption
from common.models import Feeder, BusinessDistrict
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
            
            if not from_date_str or not to_date_str:
                raise ValueError("from_date and to_date are required for this mode")
            
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
    start = datetime(year, month, 1)
    end = (start + relativedelta(months=1)) - timedelta(days=1)
    return start.date(), end.date()


def calculate_district_hours_of_supply_sql(district_id, from_date, to_date):
    """
    Calculate average hours of supply per day for a district.
    
    CORRECTED Logic:
    - Numerator: Sum of all hours supplied across all feeders with data in the district
    - Denominator: Total feeders in district × Days in period
    - This properly accounts for feeders with no data (they contribute 0)
    
    Returns:
        float: Average hours per day (capped at 24.0)
    """
    period_days = (to_date - from_date).days + 1
    
    feeder_count_query = """
        SELECT COUNT(DISTINCT f.id)
        FROM common_feeder f
        WHERE f.business_district_id = %s
    """
    
    hours_query = """
        SELECT 
            COUNT(DISTINCT CONCAT(hl.feeder_id, '-', hl.date, '-', hl.hour)) as total_hours
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id
        WHERE f.business_district_id = %s
            AND hl.date BETWEEN %s AND %s
            AND hl.load_mw > 0
    """
    
    with connection.cursor() as cursor:
        cursor.execute(feeder_count_query, [district_id])
        result = cursor.fetchone()
        total_feeders = result[0] if result and result[0] else 0
        
        if total_feeders == 0:
            return 0.0
        
        cursor.execute(hours_query, [district_id, from_date, to_date])
        result = cursor.fetchone()
        total_hours = result[0] if result and result[0] else 0
    
    # Average = Total hours / (Total feeders × Days)
    avg_hours_per_day = total_hours / (total_feeders * period_days)
    
    # Cap at 24 hours maximum
    return round(min(avg_hours_per_day, 24.0), 2)


def calculate_district_interruption_metrics_sql(district_id, from_date, to_date, exclude_types=None):
    """
    Calculate average interruption duration per day for a district.
    
    CORRECTED Logic:
    - Includes ALL interruptions active during the period (not just those that started in the period)
    - Calculates only the hours that fall within the filtered period boundaries
    - Numerator: Sum of all interruption hours across all feeders with interruptions
    - Denominator: Total feeders in district × Days in period
    - This properly accounts for feeders with no interruptions (they contribute 0)
    
    Args:
        district_id: ID of the business district
        from_date: Start date
        to_date: End date
        exclude_types: List of interruption types to exclude (for turnaround time)
    
    Returns:
        tuple: (avg_hours_per_day, total_interruption_count)
            - avg_hours_per_day: Average interruption hours per day (capped at 24.0)
            - total_interruption_count: COUNT of interruptions that occurred in period (for FTC)
    """
    period_days = (to_date - from_date).days + 1
    
    start_of_period = timezone.make_aware(
        datetime.combine(from_date, datetime.min.time())
    )
    end_of_period = timezone.make_aware(
        datetime.combine(to_date, datetime.max.time())
    )
    
    feeder_count_query = """
        SELECT COUNT(DISTINCT f.id)
        FROM common_feeder f
        WHERE f.business_district_id = %s
    """
    
    exclusion_clause = ""
    # Parameters for duration calculation (includes all active interruptions)
    max_hours = period_days * 24.0
    duration_params = [end_of_period, end_of_period, start_of_period, max_hours, district_id, from_date, end_of_period, start_of_period, start_of_period]
    
    # Parameters for count calculation (only interruptions that occurred in period)
    count_params = [district_id, from_date, to_date]
    
    if exclude_types:
        placeholders = ','.join(['%s'] * len(exclude_types))
        exclusion_clause = f"AND fi.interruption_type NOT IN ({placeholders})"
        duration_params.extend(exclude_types)
        count_params.extend(exclude_types)
    
    # CORRECTED: Calculate per-feeder totals first, then cap each at (24 * period_days)
    # This prevents multiple overlapping interruptions from inflating the average
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
            WHERE f.business_district_id = %s
                AND (
                    DATE(fi.occurred_at) BETWEEN %s AND DATE(%s)
                    OR (fi.occurred_at < %s AND (fi.restored_at IS NULL OR fi.restored_at >= %s))
                )
                {exclusion_clause}
            GROUP BY fi.feeder_id
        ) per_feeder_totals
    """
    
    # Separate query for count (only interruptions that occurred in period)
    interruption_count_query = f"""
        SELECT COUNT(*) as total_interruptions
        FROM technical_feederinterruption fi
        INNER JOIN common_feeder f ON fi.feeder_id = f.id
        WHERE f.business_district_id = %s
            AND DATE(fi.occurred_at) BETWEEN %s AND %s
            {exclusion_clause}
    """
    
    with connection.cursor() as cursor:
        cursor.execute(feeder_count_query, [district_id])
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
    
    # Average = Total hours / (Total feeders × Days)
    avg_hours_per_day = total_hours / (total_feeders * period_days)
    
    # Ensure non-negative and cap at 24
    avg_hours_per_day = max(0, min(avg_hours_per_day, 24.0))
    
    return round(avg_hours_per_day, 2), int(total_interruptions)


def calculate_district_energy_sql(district_id, from_date, to_date):
    """
    Calculate total energy delivered for a district from HourlyLoad.
    Sum of all load_mw values (MW × 1 hour = MWh)
    
    Args:
        district_id: ID of the business district
        from_date: Start date
        to_date: End date
    
    Returns:
        float: Total energy in MWh
    """
    query = """
        SELECT 
            COALESCE(SUM(hl.load_mw), 0) as total_energy
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id
        WHERE f.business_district_id = %s
            AND hl.date BETWEEN %s AND %s
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [district_id, from_date, to_date])
        result = cursor.fetchone()
        total_energy = result[0] if result and result[0] else 0
    
    return round(float(total_energy), 2)


def get_previous_periods(start_date, period_days, count=4):
    """
    Get previous periods for historical comparison.
    
    Args:
        start_date: Current period start date
        period_days: Number of days in the period
        count: Number of historical periods to return
    
    Returns:
        list: List of dictionaries with start, end, and label for each period
    """
    periods = []
    
    for i in range(count, 0, -1):
        if period_days == 1:  # Daily
            period_start = start_date - timedelta(days=i)
            period_end = period_start
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


def calculate_district_metrics_for_period(district_id, from_date, to_date):
    """
    Calculate all metrics for a single period with proper validation.
    
    CORRECTED: All metrics follow network-wide daily averaging pattern.
    - avg_supply: Average hours per day across ALL feeders in district
    - avg_duration: Average interruption hours per day across ALL feeders
    - turnaround: Average local fault hours per day across ALL feeders
    - avg_daily_interruptions: Average interruptions per feeder per day
    - ftc: Total interruption count (Feeder Tripping Count)
    - energy_delivered: Total energy in MWh
    - feeder_count: Number of feeders in district
    
    Args:
        district_id: ID of the business district
        from_date: Start date
        to_date: End date
    
    Returns:
        dict: Dictionary of calculated metrics
    """
    period_days = (to_date - from_date).days + 1
    
    # 1. Supply hours (includes ALL feeders)
    avg_supply = float(calculate_district_hours_of_supply_sql(district_id, from_date, to_date))
    
    # 2. Interruption duration (all types, includes ALL feeders)
    avg_duration, total_interruptions = calculate_district_interruption_metrics_sql(
        district_id, from_date, to_date
    )
    avg_duration = float(avg_duration)
    
    # 3. Turnaround time (exclude L/S and TCN, includes ALL feeders)
    turnaround, _ = calculate_district_interruption_metrics_sql(
        district_id, from_date, to_date, exclude_types=TURNAROUND_EXCLUSIONS
    )
    turnaround = float(turnaround)
    
    # 4. Energy delivered
    total_energy = float(calculate_district_energy_sql(district_id, from_date, to_date))
    
    # 5. Feeder count
    feeder_count = Feeder.objects.filter(business_district_id=district_id).count()
    
    # 6. Daily interruptions (average per feeder per day)
    if feeder_count > 0 and period_days > 0:
        avg_daily_interruptions = float(total_interruptions) / (feeder_count * period_days)
    else:
        avg_daily_interruptions = 0.0
    
    # Validate all time-based metrics are capped at 24 hours
    avg_supply = min(avg_supply, 24.0)
    avg_duration = min(avg_duration, 24.0)
    turnaround = min(turnaround, 24.0)
    
    return {
        "avg_supply": round(avg_supply, 2),
        "avg_duration": round(avg_duration, 2),
        "turnaround": round(turnaround, 2),
        "avg_daily_interruptions": round(avg_daily_interruptions, 2),
        "ftc": int(total_interruptions),  # Total count (Feeder Tripping Count)
        "energy_delivered": round(total_energy, 2),
        "feeder_count": int(feeder_count)
    }


def build_metrics_with_history(district, start_date, end_date, period_days):
    """
    Build metrics response with historical data for comparison.
    
    Args:
        district: BusinessDistrict object
        start_date: Current period start date
        end_date: Current period end date
        period_days: Number of days in the period
    
    Returns:
        dict: Dictionary with current values, deltas, and historical data
    """
    # Get current period metrics
    current = calculate_district_metrics_for_period(district.id, start_date, end_date)
    
    # Get historical periods
    previous_periods = get_previous_periods(start_date, period_days)
    
    history_data = []
    for period in previous_periods:
        hist_metrics = calculate_district_metrics_for_period(
            district.id, 
            period["start"], 
            period["end"]
        )
        history_data.append({
            "month": period["label"],
            **hist_metrics
        })
    
    # Calculate deltas (current vs most recent historical)
    previous = history_data[-1] if history_data else {}
    
    def calc_delta(current_val, prev_val):
        """Calculate percentage change"""
        # Ensure both values are floats
        current_val = float(current_val) if current_val is not None else 0.0
        prev_val = float(prev_val) if prev_val is not None else 0.0
        
        if prev_val and prev_val != 0:
            return round(((current_val - prev_val) / prev_val) * 100, 2)
        elif current_val == 0 and prev_val == 0:
            return 0.0
        elif prev_val == 0 and current_val != 0:
            return 100.0  # Infinite increase represented as 100%
        return None
    
    # Build response with current, delta, and history for each metric
    metrics = {}
    for key, current_val in current.items():
        prev_val = previous.get(key, 0)
        metrics[key] = {
            "current": current_val,
            "delta": calc_delta(current_val, prev_val),
            "history": history_data
        }
    
    return metrics


def get_top_bottom_feeders_sql(district_id, from_date, to_date):
    """
    Get top 5 and bottom 5 feeders by peak load.
    
    Args:
        district_id: ID of the business district
        from_date: Start date
        to_date: End date
    
    Returns:
        tuple: (top_5_feeders, bottom_5_feeders)
            Each is a list of dictionaries with feeder details and peak load
    """
    query = """
        SELECT 
            f.name as feeder_name,
            f.slug as feeder_slug,
            f.voltage_level,
            MAX(hl.load_mw) as peak_load
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id
        WHERE f.business_district_id = %s
            AND hl.date BETWEEN %s AND %s
        GROUP BY f.id, f.name, f.slug, f.voltage_level
        ORDER BY peak_load DESC
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [district_id, from_date, to_date])
        results = cursor.fetchall()
    
    if not results:
        return [], []
    
    formatted = [
        {
            "feeder": row[0],
            "feeder_slug": row[1],
            "voltage_level": row[2],
            "peak": round(float(row[3] or 0), 2)
        }
        for row in results
    ]
    
    top_5 = formatted[:5]
    bottom_5 = list(reversed(formatted[-5:])) if len(formatted) >= 5 else []
    
    return top_5, bottom_5


@api_view(["GET"])
def business_district_technical_summary(request):
    """
    Technical summary for a specific business district.
    
    Query Parameters:
    - district: Business district name (required)
    - mode: monthly, yearly, daily, weekly, custom, range (default: monthly)
    - For monthly: year, month
    - For yearly: year
    - For others: from_date, to_date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
    
    Examples:
    - ?district=Abuja&mode=monthly&year=2024&month=8
    - ?district=Abuja&mode=yearly&year=2024
    - ?district=Abuja&mode=daily&from_date=2024-08-02T23:00:00.000Z&to_date=2024-08-02T23:00:00.000Z
    - ?district=Abuja&mode=custom&from_date=2024-08-01T00:00:00.000Z&to_date=2024-08-15T23:59:59.999Z
    
    Key Metrics (CORRECTED - Network-wide daily averaging):
    - avg_supply: Average hours per day across ALL feeders in district (0-24)
    - avg_duration: Average interruption hours per day across ALL feeders (0-24)
    - turnaround: Average local fault hours per day across ALL feeders (0-24)
    - avg_daily_interruptions: Average interruptions per feeder per day
    - ftc: Feeder Tripping Count - total number of interruptions in period
    - energy_delivered: Total energy in MWh
    - feeder_count: Number of feeders in district
    
    Response Structure:
    {
        "metrics": {
            "avg_supply": {
                "current": 12.5,
                "delta": 5.2,
                "history": [...]
            },
            ...
        },
        "top_feeders": [...],
        "bottom_feeders": [...]
    }
    """
    district_name = request.GET.get("district")
    if not district_name:
        return Response({"error": "District parameter is required"}, status=400)
    
    try:
        district = BusinessDistrict.objects.get(name__iexact=district_name)
    except BusinessDistrict.DoesNotExist:
        return Response({"error": f"District '{district_name}' not found"}, status=404)
    
    try:
        from_date, to_date, mode = get_date_range_and_mode_from_request(request)
    except ValueError as e:
        return Response({"error": str(e)}, status=400)
    
    print(f"DEBUG: District: {district_name}, Date range: {from_date} to {to_date}, mode: {mode}")
    
    # Calculate period days
    period_days = (to_date - from_date).days + 1
    
    # Get metrics with history
    metrics = build_metrics_with_history(district, from_date, to_date, period_days)
    
    # Get top and bottom feeders
    top_feeders, bottom_feeders = get_top_bottom_feeders_sql(district.id, from_date, to_date)
    
    response_data = {
        "metrics": metrics,
        "top_feeders": top_feeders,
        "bottom_feeders": bottom_feeders,
        "_metadata": {
            "district": district.name,
            "state": district.state.name if district.state else None,
            "mode": mode,
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "period_days": period_days
        }
    }
    
    return Response(response_data)