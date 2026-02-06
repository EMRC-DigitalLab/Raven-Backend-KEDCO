# technical/views/overview/overview_views.py
from technical.models import *
from rest_framework.response import Response
from django.db.models import Avg, Sum, Count, Q, F, FloatField, ExpressionWrapper, Case, When, DecimalField
from django.db.models.functions import Coalesce
from rest_framework.decorators import api_view
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.db import connection
from common.models import Feeder
from technical.constants import TURNAROUND_EXCLUSIONS


def get_month_range(year, month):
    """Get first and last day of a month"""
    start = datetime(year, month, 1)
    end = start + relativedelta(months=1) - timedelta(days=1)
    return start.date(), end.date()


def delta(current, previous):
    """Calculate percentage change"""
    if previous == 0:
        return 0 if current == 0 else 100
    return round(((current - previous) / previous) * 100, 2)


def parse_date_range(request):
    """Parse date parameters and determine filtering mode"""
    mode = request.GET.get("mode", "monthly")
    today = datetime.now().date()
    now = timezone.now()
    
    if mode == "monthly":
        year = int(request.GET.get("year", datetime.now().year))
        month = int(request.GET.get("month", datetime.now().month))
        start_date, end_date = get_month_range(year, month)
        
        # ✅ Cap end_date at today for current/future months
        if end_date >= today:
            end_date = today
        
        # Calculate actual period days (whole days completed)
        period_days = (end_date - start_date).days + 1
        
        return {
            "mode": "monthly",
            "start_date": start_date,
            "end_date": end_date,
            "period_days": period_days,
            "is_current_period": end_date == today
        }
    
    elif mode == "daily":
        from_date_str = request.GET.get("from_date")
        if from_date_str:
            from_date = parse_datetime(from_date_str).date()
        else:
            from_date = datetime.now().date()
        
        return {
            "mode": "daily",
            "start_date": from_date,
            "end_date": from_date,
            "period_days": 1,
            "is_current_period": from_date == today
        }
    
    else:  # custom range
        from_date_str = request.GET.get("from_date")
        to_date_str = request.GET.get("to_date")
        
        if from_date_str and to_date_str:
            from_date = parse_datetime(from_date_str).date()
            to_date = parse_datetime(to_date_str).date()
        else:
            # Default to current month if no dates provided
            from_date, to_date = get_month_range(datetime.now().year, datetime.now().month)
        
        # ✅ Cap end_date at today for current/future periods
        if to_date >= today:
            to_date = today
        
        period_days = (to_date - from_date).days + 1
        
        return {
            "mode": "custom",
            "start_date": from_date,
            "end_date": to_date,
            "period_days": period_days,
            "is_current_period": to_date == today
        }


def get_period_label(start_date, period_days, period_index=0):
    """Generate appropriate period labels based on duration"""
    if period_days == 1:  # Daily
        target_date = start_date - timedelta(days=period_index)
        return target_date.strftime("%a")  # Mon, Tue, etc.
    
    elif period_days == 7:  # Weekly
        week_num = period_index + 1
        return f"Wk{week_num}"
    
    elif 28 <= period_days <= 31:  # Monthly
        target_date = start_date - relativedelta(months=period_index)
        return target_date.strftime("%b")  # Jan, Feb, etc.
    
    else:  # Custom cycles
        cycle_num = period_index + 1
        return f"C{cycle_num}"


def get_previous_periods(start_date, end_date, period_days, count=4):
    """Get previous periods for historical comparison"""
    periods = []
    
    for i in range(count, 0, -1):
        if period_days == 1:  # Daily
            period_start = start_date - timedelta(days=i)
            period_end = period_start
        elif period_days == 7:  # Weekly
            period_start = start_date - timedelta(weeks=i)
            period_end = period_start + timedelta(days=6)
        elif 28 <= period_days <= 31:  # Monthly
            temp_date = start_date - relativedelta(months=i)
            period_start, period_end = get_month_range(temp_date.year, temp_date.month)
        else:  # Custom cycles
            period_start = start_date - timedelta(days=period_days * i)
            period_end = period_start + timedelta(days=period_days - 1)
        
        periods.append({
            "start": period_start,
            "end": period_end,
            "label": get_period_label(start_date, period_days, i)
        })
    
    return periods


def calculate_energy_delivered_feeder(feeder_id, from_date, to_date):
    """
    Calculate total energy delivered for a single feeder.
    
    CORRECT FORMULA: Feeder_Avg_Load × Feeder_Supply_Hours
    
    - avg_load = AVG(load_mw) where load_mw > 0
    - supply_hours = COUNT(hours where load_mw > 0)
    - energy = avg_load × supply_hours
    
    Returns:
        Total energy in MWh
    """
    query = """
        SELECT 
            COALESCE(
                AVG(load_mw) * COUNT(*),
                0
            ) as feeder_energy
        FROM technical_hourlyload
        WHERE feeder_id = %s
            AND date BETWEEN %s AND %s
            AND load_mw > 0
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [feeder_id, from_date, to_date])
        result = cursor.fetchone()
        total_energy = float(result[0]) if result and result[0] else 0.0
    
    return round(total_energy, 2)


def calculate_energy_delivered_network(from_date, to_date):
    """
    Calculate total energy delivered across all ONBOARDED feeders.
    
    CORRECT FORMULA: SUM over all feeders of (Feeder_Avg_Load × Feeder_Supply_Hours)
    
    For each feeder:
      - Get average load (MW) = AVG(load_mw) where load_mw > 0
      - Get supply hours = COUNT(hours where load_mw > 0)
      - Feeder energy = avg_load × supply_hours
    
    Total network energy = SUM of all feeder energies
    
    Returns:
        Total energy in MWh
    Returns:
        Total energy in MWh
    """
    # Get IDs of onboarded feeders (SEASONALITY AWARE)
    onboarded_feeders = Feeder.objects.filter(is_onboarded=True).filter(
        Q(onboarded_at__lte=to_date) | Q(onboarded_at__isnull=True)
    )
    onboarded_feeder_ids = list(onboarded_feeders.values_list('id', flat=True))
    
    if not onboarded_feeder_ids:
        return 0.0
    
    feeder_placeholders = ','.join(['%s'] * len(onboarded_feeder_ids))
    
    # Per-feeder calculation: AVG(load_mw) × COUNT(hours with load > 0)
    query = f"""
        SELECT 
            COALESCE(
                SUM(feeder_energy),
                0
            ) as total_energy
        FROM (
            SELECT 
                feeder_id,
                AVG(load_mw) * COUNT(*) as feeder_energy
            FROM technical_hourlyload
            WHERE date BETWEEN %s AND %s
                AND feeder_id IN ({feeder_placeholders})
                AND load_mw > 0
            GROUP BY feeder_id
        ) feeder_energies
    """
    
    params = [from_date, to_date] + onboarded_feeder_ids
    
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        result = cursor.fetchone()
        total_energy = float(result[0]) if result and result[0] else 0.0
    
    return round(total_energy, 2)


def calculate_hours_of_supply_feeder(feeder_id, from_date, to_date):
    """
    Calculate average hours of supply per day for a single feeder.
    
    For single-day queries (especially today): Returns total hours supplied (not daily average)
    For multi-day queries: Returns average hours per day
    """
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
    
    # ✅ CRITICAL: For single-day queries, return total hours (not daily average)
    # For multi-day queries, return daily average
    if from_date == to_date:
        # Single day: Return total hours supplied
        avg_hours = float(total_hours)
    else:
        # Multi-day: Return average hours per day
        period_days = (to_date - from_date).days + 1
        avg_hours = total_hours / period_days if period_days > 0 else 0
    
    return round(min(avg_hours, 24.0), 2)


def calculate_hours_of_supply_network(from_date, to_date):
    """
    Calculate average hours of supply per day across ONBOARDED feeders for the period.
    
    SEASONALITY FIX:
    - Only include feeders that were onboarded ON or BEFORE the end date.
    - This prevents historical averages (e.g., last Sept) from being diluted by 
      feeders onboarded recently (e.g., today).
    
    Logic:
    - Numerator: Sum of all hours supplied across valid feeders
    - Denominator: Count of valid feeders × Days
    """
    # Filter feeders that were onboarded by the end of the period
    # If onboarded_at is NULL, assumes it's an old feeder (include it)
    # Also ensures we only look at currently 'is_onboarded=True' feeders to be safe, 
    # or you might want to remove is_onboarded=True check if you want to include historically active ones.
    # For now, we assume we only care about currently active ones that were active back then.
    
    # Needs Q object
    
    onboarded_feeders = Feeder.objects.filter(is_onboarded=True).filter(
        Q(onboarded_at__lte=to_date) | Q(onboarded_at__isnull=True)
    )
    
    total_feeders = onboarded_feeders.count()
    
    if total_feeders == 0:
        return 0.0
    
    # Get IDs of these valid feeders
    onboarded_feeder_ids = list(onboarded_feeders.values_list('id', flat=True))
    
    if not onboarded_feeder_ids:
        return 0.0
    
    # Get total hours supplied across these feeders only
    placeholders = ','.join(['%s'] * len(onboarded_feeder_ids))
    query = f"""
        SELECT 
            COUNT(DISTINCT CONCAT(feeder_id, '-', date, '-', hour)) as total_hours
        FROM technical_hourlyload
        WHERE date BETWEEN %s AND %s
            AND load_mw > 0
            AND feeder_id IN ({placeholders})
    """
    
    params = [from_date, to_date] + onboarded_feeder_ids
    
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        result = cursor.fetchone()
        total_hours_all_feeders = result[0] if result and result[0] else 0
    
    # ✅ CRITICAL: For single-day queries, return average hours per feeder (not daily average)
    # For multi-day queries, return average hours per day per feeder
    if from_date == to_date:
        # Single day: Average hours per feeder
        avg_hours = total_hours_all_feeders / total_feeders if total_feeders > 0 else 0
    else:
        # Multi-day: Average hours per day per feeder
        period_days = (to_date - from_date).days + 1
        avg_hours = total_hours_all_feeders / (total_feeders * period_days) if (total_feeders * period_days) > 0 else 0
    
    return round(min(avg_hours, 24.0), 2)


def calculate_average_load_network(from_date, to_date):
    """
    Calculate average load per feeder per hour across ONBOARDED feeders only.
    
    For single-day queries: Uses actual elapsed hours
    For multi-day queries: Uses total hours in period
    
    Formula: Total Load / (Total ONBOARDED Feeders × Total Hours in Period)
    Uses actual elapsed hours for current periods.
    """
    today = timezone.now().date()
    now = timezone.now()
    
    # Calculate period hours
    if to_date == today:
        # For current day, calculate actual elapsed hours
        full_days = (to_date - from_date).days
        current_hour = now.hour
        current_minute = now.minute
        
        # Include minutes for precision
        hours_elapsed = current_hour + (current_minute / 60.0)
        period_hours = (full_days * 24) + hours_elapsed
        
        # ✅ CRITICAL: Ensure minimum period to avoid division by zero
        if period_hours == 0:
            period_hours = 1  # 1 hour minimum
    else:
        # For past periods, use full hours
        period_days = (to_date - from_date).days + 1
        period_hours = period_days * 24
    
    total_feeders = Feeder.objects.filter(is_onboarded=True).count()
    
    if total_feeders == 0:
        return 0.0
    
    # Sum all load_mw values for ONBOARDED feeders only
    result = HourlyLoad.objects.filter(
        date__range=(from_date, to_date),
        feeder__is_onboarded=True
    ).aggregate(total_load=Sum('load_mw'))
    
    total_load = float(result['total_load'] or 0)
    
    # Average = Total Load / (Total Onboarded Feeders × Total Hours)
    avg_load = total_load / (total_feeders * period_hours) if (total_feeders * period_hours) > 0 else 0
    
    return round(avg_load, 2)


def calculate_average_load_feeder(feeder_id, from_date, to_date):
    """
    Calculate average load for a single feeder.
    
    Formula: Total Load / Total Hours in Period
    Uses actual elapsed hours for current periods.
    """
    today = timezone.now().date()
    now = timezone.now()
    
    if to_date == today:
        # For current day, calculate actual elapsed hours
        full_days = (to_date - from_date).days
        current_hour = now.hour
        current_minute = now.minute
        
        # Include minutes for precision
        hours_elapsed = current_hour + (current_minute / 60.0)
        period_hours = (full_days * 24) + hours_elapsed
        
        # ✅ CRITICAL: Ensure minimum period to avoid division by zero
        if period_hours == 0:
            period_hours = 1  # 1 hour minimum
    else:
        # For past periods, use full hours
        period_days = (to_date - from_date).days + 1
        period_hours = period_days * 24
    
    result = HourlyLoad.objects.filter(
        feeder_id=feeder_id,
        date__range=(from_date, to_date)
    ).aggregate(total_load=Sum('load_mw'))
    
    total_load = float(result['total_load'] or 0)
    avg_load = total_load / period_hours if period_hours > 0 else 0
    
    return round(avg_load, 2)


def calculate_interruption_duration_feeder(feeder_id, from_date, to_date, exclude_types=None):
    """
    Calculate average interruption duration per day for a single feeder.
    
    For single-day queries (especially today): Returns total hours (not daily average)
    For multi-day queries: Returns average hours per day
    
    CORRECTED Logic:
    - Includes ALL interruptions active during the period (not just those that started in the period)
    - Calculates only the hours that fall within the filtered period boundaries
    - Caps total interruption hours at 24 for single day, (24 × period_days) for multi-day
    
    Args:
        feeder_id: ID of the feeder
        from_date: Start date
        to_date: End date
        exclude_types: List of interruption types to exclude (for turnaround time)
    
    Returns:
        Average hours of interruption per day for this feeder
    """
    # ✅ FIXED: Check for future dates
    now = timezone.now()
    today = now.date()
    
    if from_date > today:
        return 0.0
    
    # Determine if single-day or multi-day query
    is_single_day = (from_date == to_date)
    
    if is_single_day:
        max_possible_hours = 24.0
    else:
        period_days = (to_date - from_date).days + 1
        max_possible_hours = 24.0 * period_days
    
    start_of_period = timezone.make_aware(
        datetime.combine(from_date, datetime.min.time())
    )
    end_of_period = timezone.make_aware(
        datetime.combine(to_date, datetime.max.time())
    )
    
    # Build exclusion clause
    exclusion_clause = ""
    params = [end_of_period, end_of_period, start_of_period, feeder_id, start_of_period, end_of_period, start_of_period, start_of_period]
    
    if exclude_types:
        placeholders = ','.join(['%s'] * len(exclude_types))
        exclusion_clause = f"AND interruption_type NOT IN ({placeholders})"
        params.extend(exclude_types)
    
    # CORRECTED: Sum all interruption hours but cap at max possible
    # ✅ Uses timezone-aware datetime ranges
    query = f"""
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
    
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        result = cursor.fetchone()
        total_hours = float(result[0]) if result and result[0] else 0
    
    # Cap total hours at maximum possible (handles overlapping interruptions)
    total_hours = min(total_hours, max_possible_hours)
    
    # ✅ CRITICAL: For single-day queries, return total hours (not daily average)
    # For multi-day queries, return daily average
    if is_single_day:
        # Single day: Return total hours
        avg_hours = total_hours
    else:
        # Multi-day: Return average hours per day
        avg_hours = total_hours / period_days if period_days > 0 else 0
    
    # Ensure non-negative and cap at 24
    avg_hours = max(0, min(avg_hours, 24.0))
    
    return round(avg_hours, 2)


def calculate_interruption_duration_network(from_date, to_date, exclude_types=None):
    """
    Calculate average interruption duration per day across ALL ONBOARDED feeders.
    
    For single-day queries (especially today): Returns total hours per feeder (not daily average)
    For multi-day queries: Returns average hours per day per feeder
    
    UPDATED Logic:
    - Includes ALL interruptions active during the period (not just those that started in the period)
    - Calculates only the hours that fall within the filtered period boundaries
    - Numerator: Sum of all interruption hours across all ONBOARDED feeders with interruptions
    - Denominator: Total ONBOARDED feeders × Days (or just feeders for single day)
    - This properly accounts for onboarded feeders with no interruptions (they contribute 0)
    
    NOTE: For feeders with multiple overlapping interruptions, we cap each feeder's
    daily interruption at 24 hours to avoid double-counting.
    """
    # ✅ FIXED: Check for future dates
    now = timezone.now()
    today = now.date()
    
    if from_date > today:
        return 0.0
    
    # Determine if single-day or multi-day query
    is_single_day = (from_date == to_date)
    
    if is_single_day:
        max_hours_per_feeder = 24.0
    else:
        period_days = (to_date - from_date).days + 1
        max_hours_per_feeder = 24.0 * period_days
    
    total_feeders = Feeder.objects.filter(is_onboarded=True).count()
    
    if total_feeders == 0:
        return 0.0
    
    # Get IDs of onboarded feeders
    onboarded_feeder_ids = list(Feeder.objects.filter(is_onboarded=True).values_list('id', flat=True))
    
    if not onboarded_feeder_ids:
        return 0.0
    
    start_of_period = timezone.make_aware(
        datetime.combine(from_date, datetime.min.time())
    )
    end_of_period = timezone.make_aware(
        datetime.combine(to_date, datetime.max.time())
    )
    
    # Build feeder filter
    feeder_placeholders = ','.join(['%s'] * len(onboarded_feeder_ids))
    feeder_filter = f"AND feeder_id IN ({feeder_placeholders})"
    
    # Build exclusion clause
    exclusion_clause = ""
    # ✅ Uses timezone-aware datetime ranges
    base_params = [end_of_period, end_of_period, start_of_period, start_of_period, end_of_period, start_of_period, start_of_period]
    base_params.extend(onboarded_feeder_ids)
    
    if exclude_types:
        placeholders = ','.join(['%s'] * len(exclude_types))
        exclusion_clause = f"AND interruption_type NOT IN ({placeholders})"
        base_params.extend(exclude_types)
    
    # CORRECTED: Calculate per-feeder totals first, then cap each at max_hours_per_feeder
    # This prevents multiple overlapping interruptions from inflating the average
    # ✅ Uses timezone-aware datetime ranges
    query = f"""
        SELECT 
            COALESCE(SUM(capped_hours), 0) as total_hours
        FROM (
            SELECT 
                feeder_id,
                LEAST(
                    SUM(
                        GREATEST(
                            EXTRACT(EPOCH FROM (
                                LEAST(COALESCE(restored_at, %s), %s) - GREATEST(occurred_at, %s)
                            )) / 3600.0,
                            0
                        )
                    ),
                    {max_hours_per_feeder}
                ) as capped_hours
            FROM technical_feederinterruption
            WHERE (
                occurred_at >= %s AND occurred_at <= %s
                OR (occurred_at < %s AND (restored_at IS NULL OR restored_at >= %s))
            )
            {feeder_filter}
            {exclusion_clause}
            GROUP BY feeder_id
        ) per_feeder_totals
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, base_params)
        result = cursor.fetchone()
        total_hours_all_feeders = float(result[0]) if result and result[0] else 0
    
    # ✅ CRITICAL: For single-day queries, return average hours per feeder (not daily average)
    # For multi-day queries, return average hours per day per feeder
    if is_single_day:
        # Single day: Average hours per feeder
        avg_hours = total_hours_all_feeders / total_feeders if total_feeders > 0 else 0
    else:
        # Multi-day: Average hours per day per feeder
        avg_hours = total_hours_all_feeders / (total_feeders * period_days) if (total_feeders * period_days) > 0 else 0
    
    # Ensure non-negative and cap at 24
    avg_hours = max(0, min(avg_hours, 24.0))
    
    return round(avg_hours, 2)
    avg_hours_per_day = max(0, min(avg_hours_per_day, 24.0))
    
    return round(avg_hours_per_day, 2)


def calculate_average_interruption_duration_network(from_date, to_date):
    """
    Calculate average duration per interruption for ONBOARDED feeders.
    
    INCLUDES:
    1. Interruptions that OCCURRED within the period (resolved or ongoing)
    2. Interruptions that started BEFORE the period but are still ongoing (not resolved)
    
    CORRECTED: Only counts the hours that fall WITHIN the filtered period.
    - If interruption started before period: counts from period start
    - If interruption ongoing: counts to NOW (if today) or end of period
    - If interruption ended after period: counts to period end
    
    Formula: SUM(clipped interruption durations) / COUNT(interruptions)
    Result: Average hours per interruption event (not per day)
    
    For ongoing interruptions, uses NOW as the end time.
    """
    now = timezone.now()
    today = now.date()
    
    # ✨ CRITICAL: If querying future dates, return 0 (no data available yet)
    if from_date > today:
        return 0.0
    
    # Get IDs of onboarded feeders
    onboarded_feeder_ids = list(Feeder.objects.filter(is_onboarded=True).values_list('id', flat=True))
    
    if not onboarded_feeder_ids:
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
    
    # Get interruptions that are active during the period
    feeder_placeholders = ','.join(['%s'] * len(onboarded_feeder_ids))
    
    query = f"""
        SELECT 
            COUNT(*) as interruption_count,
            COALESCE(SUM(
                EXTRACT(EPOCH FROM (
                    LEAST(COALESCE(fi.restored_at, %s), %s) - GREATEST(fi.occurred_at, %s)
                )) / 3600.0
            ), 0) as total_hours
        FROM technical_feederinterruption fi
        WHERE (
            (fi.occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
            OR (fi.occurred_at < %s AND fi.restored_at IS NULL)
        )
        AND fi.feeder_id IN ({feeder_placeholders})
    """
    
    params = [now, end_of_period, start_of_period, from_date, to_date, start_of_period] + onboarded_feeder_ids
    
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        result = cursor.fetchone()
        interruption_count = result[0] if result else 0
        total_hours = float(result[1]) if result else 0
    
    # Calculate average
    avg_duration = total_hours / interruption_count if interruption_count > 0 else 0
    
    return round(avg_duration, 2)


def calculate_average_interruption_duration_feeder(feeder_id, from_date, to_date):
    """
    Calculate average duration per interruption for a single feeder.
    
    INCLUDES:
    1. Interruptions that OCCURRED within the period (resolved or ongoing)
    2. Interruptions that started BEFORE the period but are still ongoing (not resolved)
    
    CORRECTED: Only counts the hours that fall WITHIN the filtered period.
    - If interruption started before period: counts from period start
    - If interruption ongoing: counts to NOW (if today) or end of period
    - If interruption ended after period: counts to period end
    
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
                    LEAST(COALESCE(restored_at, %s), %s) - GREATEST(occurred_at, %s)
                )) / 3600.0
            ), 0) as total_hours
        FROM technical_feederinterruption
        WHERE (
            (occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
            OR (occurred_at < %s AND restored_at IS NULL)
        )
        AND feeder_id = %s
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [now, end_of_period, start_of_period, from_date, to_date, start_of_period, feeder_id])
        result = cursor.fetchone()
        interruption_count = result[0] if result else 0
        total_hours = float(result[1]) if result else 0
    
    # Calculate average
    avg_duration = total_hours / interruption_count if interruption_count > 0 else 0
    
    return round(avg_duration, 2)


def get_ongoing_interruptions_info_network(from_date):
    """
    Get information about ongoing interruptions for ONBOARDED feeders.
    
    ONLY includes interruptions that:
    - Started BEFORE the period (occurred_at < from_date)
    - Are still ongoing (restored_at IS NULL)
    
    Returns:
        dict with count, avg_age_hours, oldest_hours
    """
    # Get IDs of onboarded feeders
    onboarded_feeder_ids = list(Feeder.objects.filter(is_onboarded=True).values_list('id', flat=True))
    
    if not onboarded_feeder_ids:
        return {
            'count': 0,
            'avg_age_hours': 0,
            'oldest_hours': 0
        }
    
    now = timezone.now()
    start_of_period = timezone.make_aware(
        datetime.combine(from_date, datetime.min.time())
    )
    
    # Get ongoing interruptions that started before the period
    feeder_placeholders = ','.join(['%s'] * len(onboarded_feeder_ids))
    
    query = f"""
        SELECT 
            COUNT(*) as ongoing_count,
            COALESCE(AVG(
                EXTRACT(EPOCH FROM (%s - occurred_at)) / 3600.0
            ), 0) as avg_age,
            COALESCE(MAX(
                EXTRACT(EPOCH FROM (%s - occurred_at)) / 3600.0
            ), 0) as max_age
        FROM technical_feederinterruption
        WHERE occurred_at < %s
            AND restored_at IS NULL
            AND feeder_id IN ({feeder_placeholders})
    """
    
    params = [now, now, start_of_period] + onboarded_feeder_ids
    
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        result = cursor.fetchone()
        ongoing_count = result[0] if result else 0
        avg_age = float(result[1]) if result else 0
        max_age = float(result[2]) if result else 0
    
    return {
        'count': ongoing_count,
        'avg_age_hours': round(avg_age, 2),
        'oldest_hours': round(max_age, 2)
    }


def get_ongoing_interruptions_info_feeder(feeder_id, from_date):
    """
    Get information about ongoing interruptions for a single feeder.
    
    ONLY includes interruptions that:
    - Started BEFORE the period (occurred_at < from_date)
    - Are still ongoing (restored_at IS NULL)
    
    Returns:
        dict with count, avg_age_hours, oldest_hours
    """
    now = timezone.now()
    start_of_period = timezone.make_aware(
        datetime.combine(from_date, datetime.min.time())
    )
    
    query = """
        SELECT 
            COUNT(*) as ongoing_count,
            COALESCE(AVG(
                EXTRACT(EPOCH FROM (%s - occurred_at)) / 3600.0
            ), 0) as avg_age,
            COALESCE(MAX(
                EXTRACT(EPOCH FROM (%s - occurred_at)) / 3600.0
            ), 0) as max_age
        FROM technical_feederinterruption
        WHERE occurred_at < %s
            AND restored_at IS NULL
            AND feeder_id = %s
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [now, now, start_of_period, feeder_id])
        result = cursor.fetchone()
        ongoing_count = result[0] if result else 0
        avg_age = float(result[1]) if result else 0
        max_age = float(result[2]) if result else 0
    
    return {
        'count': ongoing_count,
        'avg_age_hours': round(avg_age, 2),
        'oldest_hours': round(max_age, 2)
    }


def get_interruption_breakdown_feeder(feeder_id, start_date, end_date, period_days, period_offset=0):
    """
    Get interruption COUNT breakdown for a single feeder.
    
    UPDATED Logic:
    - Counts ONLY interruptions that occurred within the period
    - Does NOT include interruptions that started before (even if still ongoing)
    - Uses timezone-aware datetime ranges for consistency
    
    Returns count of interruptions by type for THIS feeder in the period.
    """
    # Calculate target period
    if period_days == 1:
        target_start = start_date - timedelta(days=period_offset)
        target_end = target_start
        label = target_start.strftime("%A") if period_offset == 0 else get_period_label(start_date, period_days, period_offset)
    elif period_days == 7:
        target_start = start_date - timedelta(weeks=period_offset)
        target_end = target_start + timedelta(days=6)
        label = f"Week {period_offset + 1}" if period_offset == 0 else f"Wk{period_offset + 1}"
    elif 28 <= period_days <= 31:
        temp_date = start_date - relativedelta(months=period_offset)
        target_start, target_end = get_month_range(temp_date.year, temp_date.month)
        label = target_start.strftime("%B")
    else:
        target_start = start_date - timedelta(days=period_days * period_offset)
        target_end = target_start + timedelta(days=period_days - 1)
        label = f"Cycle {period_offset + 1}"
    
    # ✅ Create timezone-aware datetime boundaries
    start_datetime = timezone.make_aware(
        datetime.combine(target_start, datetime.min.time())
    )
    end_datetime = timezone.make_aware(
        datetime.combine(target_end, datetime.max.time())
    )
    
    # Count interruptions that OCCURRED within the period using timezone-aware ranges
    query = """
        SELECT 
            COALESCE(interruption_type, 'Unknown') as itype,
            COUNT(*) as count
        FROM technical_feederinterruption
        WHERE feeder_id = %s
            AND occurred_at >= %s
            AND occurred_at <= %s
        GROUP BY interruption_type
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [feeder_id, start_datetime, end_datetime])
        results = cursor.fetchall()
    
    # Process results
    type_counts = {}
    total_count = 0
    
    for itype, count in results:
        count_val = int(count) if count else 0
        type_counts[itype or 'Unknown'] = count_val
        total_count += count_val
    
    return {
        "month": label,
        "total": total_count,
        "delta": 0,
        "breakdown": type_counts
    }


def get_interruption_breakdown_network(start_date, end_date, period_days, period_offset=0):
    """
    Get interruption COUNT breakdown across ALL ONBOARDED feeders.
    
    UPDATED Logic:
    - Counts ONLY interruptions that occurred within the period
    - Does NOT include interruptions that started before (even if still ongoing)
    - Only considers interruptions from ONBOARDED feeders
    - Uses timezone-aware datetime ranges for consistency
    
    Returns count of interruptions by type across all onboarded feeders in the period.
    """
    # Calculate target period
    if period_days == 1:
        target_start = start_date - timedelta(days=period_offset)
        target_end = target_start
        label = target_start.strftime("%A") if period_offset == 0 else get_period_label(start_date, period_days, period_offset)
    elif period_days == 7:
        target_start = start_date - timedelta(weeks=period_offset)
        target_end = target_start + timedelta(days=6)
        label = f"Week {period_offset + 1}" if period_offset == 0 else f"Wk{period_offset + 1}"
    elif 28 <= period_days <= 31:
        temp_date = start_date - relativedelta(months=period_offset)
        target_start, target_end = get_month_range(temp_date.year, temp_date.month)
        label = target_start.strftime("%B")
    else:
        target_start = start_date - timedelta(days=period_days * period_offset)
        target_end = target_start + timedelta(days=period_days - 1)
        label = f"Cycle {period_offset + 1}"
    
    # Get IDs of onboarded feeders
    onboarded_feeder_ids = list(Feeder.objects.filter(is_onboarded=True).values_list('id', flat=True))
    
    if not onboarded_feeder_ids:
        return {
            "month": label,
            "total": 0,
            "delta": 0,
            "breakdown": {}
        }
    
    # ✅ Create timezone-aware datetime boundaries
    start_datetime = timezone.make_aware(
        datetime.combine(target_start, datetime.min.time())
    )
    end_datetime = timezone.make_aware(
        datetime.combine(target_end, datetime.max.time())
    )
    
    # Count interruptions that OCCURRED within the period only, for ONBOARDED feeders
    feeder_placeholders = ','.join(['%s'] * len(onboarded_feeder_ids))
    query = f"""
        SELECT 
            COALESCE(interruption_type, 'Unknown') as itype,
            COUNT(*) as count
        FROM technical_feederinterruption
        WHERE occurred_at >= %s
            AND occurred_at <= %s
            AND feeder_id IN ({feeder_placeholders})
        GROUP BY interruption_type
    """
    
    params = [start_datetime, end_datetime] + onboarded_feeder_ids
    
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        results = cursor.fetchall()
    
    # Process results
    type_counts = {}
    total_count = 0
    
    for itype, count in results:
        count_val = int(count) if count else 0
        type_counts[itype or 'Unknown'] = count_val
        total_count += count_val
    
    return {
        "month": label,
        "total": total_count,
        "delta": 0,
        "breakdown": type_counts
    }


def calculate_trend_analytics(series, mode, target_energy=None):
    """
    Calculate statistical analytics for load trend data to help spot discrepancies and anomalies.
    
    Args:
        series: List of dicts with 'value' key (e.g., [{"hour": 0, "value": 12.5}, ...])
        mode: Query mode ('daily', 'monthly', 'custom')
        target_energy: Optional target energy offtake in MWh for comparison
    
    Returns:
        Dictionary with analytics metrics including peak, average, min, variance, anomalies, and target comparison
    """
    import statistics
    
    if not series:
        return {
            "peak": 0,
            "peak_time": None,
            "average": 0,
            "min": 0,
            "min_time": None,
            "std_deviation": 0,
            "variance": 0,
            "range": 0,
            "anomaly_count": 0
        }
    
    # Extract values and find peak/min
    values = [item["value"] for item in series]
    non_zero_values = [v for v in values if v > 0]
    
    # Basic statistics
    peak = max(values)
    min_val = min(values)
    average = statistics.mean(values) if values else 0
    
    # Find peak and min times
    peak_idx = values.index(peak)
    min_idx = values.index(min_val)
    
    # Format time labels based on mode
    if mode == "daily":
        peak_time = f"{series[peak_idx].get('hour', peak_idx):02d}:00"
        min_time = f"{series[min_idx].get('hour', min_idx):02d}:00"
    elif mode == "monthly":
        peak_time = f"Day {series[peak_idx].get('day', peak_idx + 1)}"
        min_time = f"Day {series[min_idx].get('day', min_idx + 1)}"
    else:  # custom
        peak_time = series[peak_idx].get('date', f"Period {peak_idx + 1}")
        min_time = series[min_idx].get('date', f"Period {min_idx + 1}")
    
    # Calculate variance and std deviation
    variance = statistics.variance(values) if len(values) > 1 else 0
    std_deviation = statistics.stdev(values) if len(values) > 1 else 0
    
    # Anomaly detection (values > 2 standard deviations from mean)
    anomaly_threshold = average + (2 * std_deviation) if std_deviation > 0 else float('inf')
    anomaly_count = sum(1 for v in values if v > anomaly_threshold and v > 0)
    
    # Add anomaly flags to series
    for item in series:
        if item["value"] > anomaly_threshold and item["value"] > 0:
            item["is_anomaly"] = True
    
    analytics = {
        "peak": round(peak, 2),
        "peak_time": peak_time,
        "average": round(average, 2),
        "min": round(min_val, 2),
        "min_time": min_time,
        "std_deviation": round(std_deviation, 2),
        "variance": round(variance, 2),
        "range": round(peak - min_val, 2),
        "anomaly_count": anomaly_count
    }
    
    # Energy offtake target comparison (if provided)
    if target_energy is not None and target_energy > 0:
        # Calculate actual cumulative energy (sum of all load values)
        actual_energy = sum(values)
        
        # Expected rate per time unit
        expected_rate = target_energy / len(series) if series else 0
        
        # Variance percentage
        variance_pct = ((actual_energy - target_energy) / target_energy * 100) if target_energy > 0 else 0
        
        # Projection based on current average
        # If we're partway through the period, project what the final value will be
        hours_remaining = len([v for v in values if v == 0])  # Rough estimate
        projected_final = actual_energy + (average * hours_remaining) if hours_remaining > 0 else actual_energy
        
        # Check if on track (within ±5% tolerance)
        tolerance = 5.0
        on_track = abs(variance_pct) <= tolerance
        
        analytics["offtake_target"] = {
            "target_energy": round(target_energy, 2),
            "actual_energy": round(actual_energy, 2),
            "expected_rate": round(expected_rate, 2),
            "variance_pct": round(variance_pct, 2),
            "projected_final": round(projected_final, 2),
            "on_track": on_track,
            "status": "On Track" if on_track else ("Over" if variance_pct > 0 else "Under")
        }
    
    return analytics


def get_load_trend_optimized(start_date, end_date, mode, feeder_id=None, target_energy=None):
    """
    Get load trend data optimized for the selected mode.
    For monthly mode: returns daily averages for each day of the month
    For daily mode: returns hourly averages for that specific day
    
    UPDATED: 
    - Only considers ONBOARDED feeders (when not filtering by specific feeder)
    - Returns 0 for missing values up to current time
    - Does not return future time slots
    - Includes analytics (peak, min, avg, variance, anomalies)
    - Optional energy offtake target comparison
    
    Args:
        start_date: Start date
        end_date: End date
        mode: Mode (monthly, daily, custom, etc.)
        feeder_id: Optional feeder ID to filter by specific feeder
        target_energy: Optional target energy offtake in MWh for comparison
    """
    today = timezone.now().date()
    now = timezone.now()
    
    # Build base query with optional feeder filter
    base_filter = {'date__range': (start_date, end_date)}
    if feeder_id:
        base_filter['feeder_id'] = feeder_id
    else:
        # Only consider onboarded feeders
        base_filter['feeder__is_onboarded'] = True
    
    if mode == "monthly":
        # Get average load for each day of the month
        daily_loads = HourlyLoad.objects.filter(
            **base_filter
        ).values('date').annotate(
            avg_load=Avg('load_mw')
        ).order_by('date')
        
        # Create a dictionary for quick lookup
        loads_dict = {
            entry["date"]: round(float(entry["avg_load"] or 0), 2)
            for entry in daily_loads
        }
        
        # Generate series with all days up to today (or end_date if in the past)
        series = []
        current_date = start_date
        effective_end = min(end_date, today)
        
        while current_date <= effective_end:
            series.append({
                "day": current_date.day,
                "value": loads_dict.get(current_date, 0)
            })
            current_date += timedelta(days=1)
        
        # Calculate analytics
        analytics = calculate_trend_analytics(series, mode, target_energy)
        
        return {
            "unit": "MW",
            "date": start_date.strftime("%Y-%m"),
            "series": series,
            "analytics": analytics
        }
    
    elif mode == "daily":
        # Get hourly loads for the specific day
        base_filter['date'] = start_date
        hourly_loads = HourlyLoad.objects.filter(
            **base_filter
        ).values('hour').annotate(
            avg_load=Avg('load_mw')
        ).order_by('hour')
        
        # Create a dictionary for quick lookup
        loads_dict = {
            entry["hour"]: round(float(entry["avg_load"] or 0), 2)
            for entry in hourly_loads
        }
        
        # Determine max hour to return
        if start_date == today:
            # For current day, only return up to current hour
            max_hour = now.hour
        else:
            # For past days, return all 24 hours
            max_hour = 23
        
        # Generate series with all hours from 0 to max_hour
        series = [
            {
                "hour": hour,
                "value": loads_dict.get(hour, 0)
            }
            for hour in range(max_hour + 1)
        ]
        
        # Calculate analytics
        analytics = calculate_trend_analytics(series, mode, target_energy)
        
        return {
            "unit": "MW",
            "date": start_date.isoformat(),
            "series": series,
            "analytics": analytics
        }
    
    else:  # custom range
        # For custom ranges, show daily averages
        daily_loads = HourlyLoad.objects.filter(
            **base_filter
        ).values('date').annotate(
            avg_load=Avg('load_mw')
        ).order_by('date')
        
        # Create a dictionary for quick lookup
        loads_dict = {
            entry["date"]: round(float(entry["avg_load"] or 0), 2)
            for entry in daily_loads
        }
        
        # Generate series with all days up to today (or end_date if in the past)
        series = []
        current_date = start_date
        effective_end = min(end_date, today)
        
        while current_date <= effective_end:
            series.append({
                "date": current_date.isoformat(),
                "value": loads_dict.get(current_date, 0)
            })
            current_date += timedelta(days=1)
        
        # Calculate analytics
        analytics = calculate_trend_analytics(series, mode, target_energy)
        
        return {
            "unit": "MW",
            "date": f"{start_date.isoformat()} to {end_date.isoformat()}",
            "series": series,
            "analytics": analytics
        }



def calculate_average_station_load_network(from_date, to_date):
    """
    Calculate average STATION load (Sum of all feeder loads) per hour across period.
    
    Formula: Average of (Sum of Feeder Loads per Hour)
    """
    today = timezone.now().date()
    now = timezone.now()
    
    # Only consider ONBOARDED feeders
    base_filter = {
        'feeder__is_onboarded': True,
        'date__range': (from_date, to_date)
    }
    
    # Calculate Station Hourly Totals
    station_hourly = HourlyLoad.objects.filter(
        **base_filter
    ).values('date', 'hour').annotate(
        station_total=Sum('load_mw')
    )
    
    # Calculate Average of these totals
    result = station_hourly.aggregate(avg_station_load=Avg('station_total'))
    
    return round(float(result['avg_station_load'] or 0), 2)


def get_station_load_trend(start_date, end_date, mode):
    """
    Get STATION TOTAL load trend.
    For daily mode: returns Station Total (Sum of feeders) for each hour
    For monthly mode: returns Average Station Load for each day
    """
    today = timezone.now().date()
    now = timezone.now()
    
    # Only consider onboarded feeders
    base_filter = {
        'feeder__is_onboarded': True,
        'date__range': (start_date, end_date)
    }

    if mode == "monthly":
        # Calculate Hourly Station Totals first
        station_hourly = HourlyLoad.objects.filter(
            **base_filter
        ).values('date', 'hour').annotate(
            station_total=Sum('load_mw')
        )
        
        # Now group by date to get Average Daily Station Load
        # We can't do a second aggregation easily in Django ORM without subqueries or Python processing
        # Using Python processing for simplicity as dataset is small (30 days x 24 hours)
        
        # Organize by date
        daily_totals = {}
        for item in station_hourly:
            d = item['date']
            load = float(item['station_total'] or 0)
            if d not in daily_totals:
                daily_totals[d] = []
            daily_totals[d].append(load)
            
        # Calculate averages
        final_daily_avgs = {}
        for d, loads in daily_totals.items():
            if loads:
                final_daily_avgs[d] = round(sum(loads) / len(loads), 2)
        
        # Generate series
        series = []
        current_date = start_date
        effective_end = min(end_date, today)
        
        while current_date <= effective_end:
            series.append({
                "day": current_date.day,
                "value": final_daily_avgs.get(current_date, 0)
            })
            current_date += timedelta(days=1)
            
        # Calculate analytics
        analytics = calculate_trend_analytics(series, mode)
        
        return {
            "unit": "MW",
            "date": start_date.strftime("%Y-%m"),
            "series": series,
            "analytics": analytics
        }

    elif mode == "daily":
        # Calculate Hourly Station Totals
        base_filter['date'] = start_date
        
        station_hourly = HourlyLoad.objects.filter(
            **base_filter
        ).values('hour').annotate(
            station_total=Sum('load_mw')
        ).order_by('hour')
        
        loads_dict = {
            entry["hour"]: round(float(entry["station_total"] or 0), 2)
            for entry in station_hourly
        }
        
        # Determine max hour
        if start_date == today:
            max_hour = now.hour
        else:
            max_hour = 23
            
        series = [
            {
                "hour": hour,
                "value": loads_dict.get(hour, 0)
            }
            for hour in range(max_hour + 1)
        ]
        
        # Calculate analytics
        analytics = calculate_trend_analytics(series, mode)
        
        return {
            "unit": "MW",
            "date": start_date.isoformat(),
            "series": series,
            "analytics": analytics
        }

    else: # Custom
        # Similar to monthly, Average Daily Station Load
        station_hourly = HourlyLoad.objects.filter(
            **base_filter
        ).values('date', 'hour').annotate(
            station_total=Sum('load_mw')
        )
        
        daily_totals = {}
        for item in station_hourly:
            d = item['date']
            load = float(item['station_total'] or 0)
            if d not in daily_totals:
                daily_totals[d] = []
            daily_totals[d].append(load)
            
        final_daily_avgs = {}
        for d, loads in daily_totals.items():
            if loads:
                final_daily_avgs[d] = round(sum(loads) / len(loads), 2)
                
        series = []
        current_date = start_date
        effective_end = min(end_date, today)
        
        while current_date <= effective_end:
            series.append({
                "date": current_date.isoformat(),
                "value": final_daily_avgs.get(current_date, 0)
            })
            current_date += timedelta(days=1)
            
        # Calculate analytics
        analytics = calculate_trend_analytics(series, mode)
        
        return {
            "unit": "MW",
            "date": f"{start_date.isoformat()} to {end_date.isoformat()}",
            "series": series,
            "analytics": analytics
        }


def get_metric_with_history(calc_fn, start_date, end_date, period_days):
    """Get metric with historical data for comparison"""
    previous_periods = get_previous_periods(start_date, end_date, period_days)
    
    # Calculate historical values
    history = []
    for period in previous_periods:
        value = calc_fn(period["start"], period["end"])
        history.append({
            "month": period["label"],
            "value": round(value, 2)
        })
    
    # Calculate current value
    current = calc_fn(start_date, end_date)
    
    # Get immediate previous period for delta calculation
    if period_days == 1:  # Daily
        prev_start = start_date - timedelta(days=1)
        prev_end = prev_start
    elif period_days == 7:  # Weekly
        prev_start = start_date - timedelta(weeks=1)
        prev_end = prev_start + timedelta(days=6)
    elif 28 <= period_days <= 31:  # Monthly
        temp_date = start_date - relativedelta(months=1)
        prev_start, prev_end = get_month_range(temp_date.year, temp_date.month)
    else:  # Custom cycles
        prev_start = start_date - timedelta(days=period_days)
        prev_end = prev_start + timedelta(days=period_days - 1)
    
    prev = calc_fn(prev_start, prev_end)
    
    return {
        "current": round(current, 2),
        "delta": delta(current, prev),
        "history": history
    }


@api_view(["GET"])
def technical_overview_view(request):
    # Parse date parameters
    date_info = parse_date_range(request)
    start_date = date_info["start_date"]
    end_date = date_info["end_date"]
    period_days = date_info["period_days"]
    mode = date_info["mode"]
    
    # Parse feeder filter (optional)
    feeder_slug = request.GET.get("feeder")
    feeder_filter = {}
    feeder_name = None
    feeder = None
    
    if feeder_slug:
        try:
            feeder = Feeder.objects.get(slug=feeder_slug)
            feeder_filter = {'feeder': feeder}
            feeder_name = feeder.name
            print(f"DEBUG: Filtering by feeder: {feeder_name} ({feeder_slug})")
        except Feeder.DoesNotExist:
            return Response(
                {"error": f"Feeder with slug '{feeder_slug}' not found"},
                status=400
            )
    else:
        # When not filtering by specific feeder, only consider ONBOARDED feeders
        feeder_filter = {'feeder__is_onboarded': True}
    
    # Parse optional target_energy parameter for offtake comparison
    target_energy = request.GET.get("target_energy")
    if target_energy:
        try:
            target_energy = float(target_energy)
        except (ValueError, TypeError):
            target_energy = None
    
    # Get previous period for delta calculations
    if period_days == 1:  # Daily
        prev_start = start_date - timedelta(days=1)
        prev_end = prev_start
    elif period_days == 7:  # Weekly
        prev_start = start_date - timedelta(weeks=1)
        prev_end = prev_start + timedelta(days=6)
    elif 28 <= period_days <= 31:  # Monthly
        temp_date = start_date - relativedelta(months=1)
        prev_start, prev_end = get_month_range(temp_date.year, temp_date.month)
    else:  # Custom cycles
        prev_start = start_date - timedelta(days=period_days)
        prev_end = prev_start + timedelta(days=period_days - 1)
    
    # Calculate highlight metrics - OPTIMIZED with feeder filter
    # =====================================================================
    # FIXED: Energy = Average Station Load (MW) × Supply Hours/Day × Days
    # This formula is mathematically correct: Energy (MWh) = Power (MW) × Time (h)
    # =====================================================================
    # Note: We'll calculate energy AFTER we have station_load and supply_hours
    
    # Average load - network-wide or single feeder
    if feeder_slug:
        load_now = calculate_average_load_feeder(feeder.id, start_date, end_date)
        load_prev = calculate_average_load_feeder(feeder.id, prev_start, prev_end)
        
        # For single feeder, Station Load is same as Feeder Load
        station_load_now = load_now
        station_load_prev = load_prev
    else:
        load_now = calculate_average_load_network(start_date, end_date)
        load_prev = calculate_average_load_network(prev_start, prev_end)
        
        # Calculate Station Load Average (Scalar)
        station_load_now = calculate_average_station_load_network(start_date, end_date)
        station_load_prev = calculate_average_station_load_network(prev_start, prev_end)
    
    # UPDATED: Interruption count - ONLY count those that OCCURRED within period
    if feeder_slug:
        interruptions_now = FeederInterruption.objects.filter(
            occurred_at__date__range=(start_date, end_date),
            feeder=feeder
        ).count()
        
        interruptions_prev = FeederInterruption.objects.filter(
            occurred_at__date__range=(prev_start, prev_end),
            feeder=feeder
        ).count()
    else:
        # Only count interruptions from ONBOARDED feeders
        interruptions_now = FeederInterruption.objects.filter(
            occurred_at__date__range=(start_date, end_date),
            feeder__is_onboarded=True
        ).count()
        
        interruptions_prev = FeederInterruption.objects.filter(
            occurred_at__date__range=(prev_start, prev_end),
            feeder__is_onboarded=True
        ).count()
    
    # Calculate supply and quality metrics with history and feeder filter
    if feeder_slug:
        # For single feeder, use feeder-specific calculation
        supply_hours = get_metric_with_history(
            lambda s, e: calculate_hours_of_supply_feeder(feeder.id, s, e),
            start_date, 
            end_date, 
            period_days
        )
        
        # Interruption duration (includes all types) - FOR SINGLE FEEDER
        interruption_duration = get_metric_with_history(
            lambda s, e: calculate_interruption_duration_feeder(feeder.id, s, e),
            start_date,
            end_date,
            period_days
        )
        
        # Turnaround time (excludes L/S and TCN types) - FOR SINGLE FEEDER
        turnaround_time = get_metric_with_history(
            lambda s, e: calculate_interruption_duration_feeder(feeder.id, s, e, exclude_types=TURNAROUND_EXCLUSIONS),
            start_date,
            end_date,
            period_days
        )
        
        # Average interruption duration (hours per interruption event)
        # Includes interruptions that started in period AND ongoing ones from before
        avg_int_duration = get_metric_with_history(
            lambda s, e: calculate_average_interruption_duration_feeder(feeder.id, s, e),
            start_date,
            end_date,
            period_days
        )
    else:
        # For all ONBOARDED feeders, use network-wide calculation
        supply_hours = get_metric_with_history(
            calculate_hours_of_supply_network, 
            start_date, 
            end_date, 
            period_days
        )
        
        # Interruption duration (includes all types) - NETWORK-WIDE (ONBOARDED only)
        interruption_duration = get_metric_with_history(
            lambda s, e: calculate_interruption_duration_network(s, e),
            start_date,
            end_date,
            period_days
        )
        
        # Turnaround time (excludes L/S and TCN types) - NETWORK-WIDE (ONBOARDED only)
        turnaround_time = get_metric_with_history(
            lambda s, e: calculate_interruption_duration_network(s, e, exclude_types=TURNAROUND_EXCLUSIONS),
            start_date,
            end_date,
            period_days
        )
        
        # Average interruption duration (hours per interruption event)
        # Includes interruptions that started in period AND ongoing ones from before
        avg_int_duration = get_metric_with_history(
            lambda s, e: calculate_average_interruption_duration_network(s, e),
            start_date,
            end_date,
            period_days
        )
    
    # =====================================================================
    # CALCULATE ENERGY: SUM of (Feeder_Avg_Load × Feeder_Supply_Hours)
    # This is the mathematically correct per-feeder approach
    # =====================================================================
    if feeder_slug:
        energy_now = calculate_energy_delivered_feeder(feeder.id, start_date, end_date)
        energy_prev = calculate_energy_delivered_feeder(feeder.id, prev_start, prev_end)
    else:
        energy_now = calculate_energy_delivered_network(start_date, end_date)
        energy_prev = calculate_energy_delivered_network(prev_start, prev_end)
    
    # Technical breakdown
    if feeder_slug:
        # For single feeder
        feeders_now = 1
        feeders_prev = 1
    else:
        # For all ONBOARDED feeders
        feeders_now = Feeder.objects.filter(is_onboarded=True).count()
        feeders_prev = feeders_now  # You may want to track this historically
    
    breakdown = {
        "feeder_count": {
            "value": feeders_now,
            "delta": delta(feeders_now, feeders_prev)
        },
        "interruption_count": {  # CHANGED from avg_daily_interruptions
            "value": interruptions_now,  # Total count, not average
            "delta": delta(interruptions_now, interruptions_prev)
        },
        "avg_turnaround": {
            "value": turnaround_time["current"],
            "delta": turnaround_time["delta"]
        },
        "customer_count": {
            "value": 0,  # SET TO 0 as requested
            "delta": 0
        }
    }
    
    # Interruption sources for 4 periods - UPDATED to show COUNTS not durations
    if feeder_slug:
        interruptions_data = [
            get_interruption_breakdown_feeder(feeder.id, start_date, end_date, period_days, i) 
            for i in range(4)
        ]
    else:
        interruptions_data = [
            get_interruption_breakdown_network(start_date, end_date, period_days, i) 
            for i in range(4)
        ]
    
    # Load trend - OPTIMIZED with feeder filter (ONBOARDED only when network-wide)
    load_trend = get_load_trend_optimized(start_date, end_date, mode, feeder_id=feeder.id if feeder_slug else None, target_energy=target_energy)
    
    # Station Load Trend & Metrics
    if feeder_slug:
        # For single feeder, station trend is same as load trend
        station_load_trend = load_trend
    else:
        station_load_trend = get_station_load_trend(start_date, end_date, mode)
    
    response_data = {
        "mode": mode,
        "period": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "days": period_days
        },
        "highlight_metrics": {
            "energy_delivered": {
                "value": round(energy_now, 2),
                "delta": delta(energy_now, energy_prev)
            },
            "average_load": {
                "value": round(load_now, 2),
                "delta": delta(load_now, load_prev)
            },
            "average_station_load": {
                "value": round(station_load_now, 2),
                "delta": delta(station_load_now, station_load_prev)
            },
            "interruptions": {
                "value": interruptions_now,
                "delta": delta(interruptions_now, interruptions_prev)
            },
        },
        "supply_and_quality": {
            "supply_hours": supply_hours,
            "interruption_duration": interruption_duration,
            "turnaround_time": turnaround_time,
            "avg_interruption_duration": avg_int_duration
        },
        "technical_breakdown": breakdown,
        "interruption_sources": interruptions_data,
        "load_trend": load_trend,
        "station_load_trend": station_load_trend
    }
    
    # =====================================================================
    # FIX: Interruption Duration = 24 - Supply Hours
    # (averaged across all onboarded feeders)
    # Apply to current + history values
    # =====================================================================
    def derive_interruption(supply):
        """Derive interruption duration = 24 - supply"""
        supply = min(max(supply, 0), 24)
        return round(24 - supply, 2)
    
    # Fix current value
    supply_curr = response_data["supply_and_quality"]["supply_hours"]["current"]
    response_data["supply_and_quality"]["interruption_duration"]["current"] = derive_interruption(supply_curr)
    
    # Fix history values
    supply_hist = response_data["supply_and_quality"]["supply_hours"].get("history", [])
    interrupt_hist = response_data["supply_and_quality"]["interruption_duration"].get("history", [])
    
    for i in range(len(interrupt_hist)):
        if i < len(supply_hist):
            s = supply_hist[i].get("value", 0)
            interrupt_hist[i]["value"] = derive_interruption(s)
    
    # Add feeder info to response if filtered
    if feeder_slug:
        response_data["feeder"] = {
            "name": feeder_name,
            "slug": feeder_slug,
            "voltage_level": feeder.voltage_level
        }
    
    return Response(response_data)