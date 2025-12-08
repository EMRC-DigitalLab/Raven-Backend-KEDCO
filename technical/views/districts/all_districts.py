# technical/views/districts/all_districts.py
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db import connection
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from common.models import Feeder, BusinessDistrict
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


def get_month_range(year, month):
    """Get first and last day of a month"""
    start = datetime(year, month, 1)
    end = start + relativedelta(months=1) - timedelta(days=1)
    return start.date(), end.date()


def parse_date_range_districts(request):
    """Parse date parameters and determine filtering mode - SAME AS OVERVIEW"""
    mode = request.GET.get("mode", "monthly")
    today = datetime.now().date()
    
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
            from_date = _parse_iso_date(from_date_str)
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
            from_date = _parse_iso_date(from_date_str)
            to_date = _parse_iso_date(to_date_str)
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


def calculate_energy_delivered_district(district_id, from_date, to_date):
    """
    Calculate total energy delivered for a district using hybrid approach.
    FOLLOWS SAME PATTERN AS calculate_energy_delivered_network in overview_views.py
    
    Priority:
    1. Use EnergyDelivered if available for a feeder-date combination
    2. Fall back to HourlyLoad sum for feeder-dates without EnergyDelivered
    
    Only considers ONBOARDED feeders.
    """
    # Get IDs of onboarded feeders in this district
    onboarded_feeder_ids = list(
        Feeder.objects.filter(
            business_district_id=district_id,
            is_onboarded=True
        ).values_list('id', flat=True)
    )
    
    if not onboarded_feeder_ids:
        return 0.0
    
    feeder_placeholders = ','.join(['%s'] * len(onboarded_feeder_ids))
    
    # Use raw SQL for optimal performance (SAME PATTERN AS OVERVIEW)
    query = f"""
        WITH date_series AS (
            SELECT generate_series(
                %s::date,
                %s::date,
                '1 day'::interval
            )::date AS date
        ),
        feeder_dates AS (
            SELECT 
                f.id as feeder_id,
                ds.date
            FROM (
                SELECT unnest(ARRAY[{feeder_placeholders}]::uuid[]) as id
            ) f
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
                AND feeder_id IN ({feeder_placeholders})
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
    
    params = [from_date, to_date] + onboarded_feeder_ids + [from_date, to_date] + onboarded_feeder_ids
    
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        result = cursor.fetchone()
        total_energy = float(result[0]) if result and result[0] else 0.0
    
    return round(total_energy, 2)


def calculate_hours_of_supply_district(district_id, from_date, to_date):
    """
    Calculate average hours of supply per day for a district.
    FOLLOWS SAME PATTERN AS calculate_hours_of_supply_network in overview_views.py
    
    For single-day queries (especially today): Returns total hours per feeder (not daily average)
    For multi-day queries: Returns average hours per day per feeder
    
    CRITICAL: For today, only counts hours up to current hour
    """
    # Get IDs of onboarded feeders in this district
    onboarded_feeder_ids = list(
        Feeder.objects.filter(
            business_district_id=district_id,
            is_onboarded=True
        ).values_list('id', flat=True)
    )
    
    if not onboarded_feeder_ids:
        return 0.0
    
    total_feeders = len(onboarded_feeder_ids)
    
    # ✅ CRITICAL: Check if we're querying today
    today = timezone.now().date()
    now = timezone.now()
    
    placeholders = ','.join(['%s'] * len(onboarded_feeder_ids))
    
    # ✅ For today: Only count hours up to current hour
    if to_date == today:
        current_hour = now.hour
        query = f"""
            SELECT 
                COUNT(DISTINCT CONCAT(feeder_id, '-', date, '-', hour)) as total_hours
            FROM technical_hourlyload
            WHERE date BETWEEN %s AND %s
                AND load_mw > 0
                AND feeder_id IN ({placeholders})
                AND (
                    date < %s 
                    OR (date = %s AND hour <= %s)
                )
        """
        params = [from_date, to_date] + onboarded_feeder_ids + [to_date, to_date, current_hour]
    else:
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


def calculate_average_load_district(district_id, from_date, to_date):
    """
    Calculate average load per feeder per hour for a district.
    FOLLOWS SAME PATTERN AS calculate_average_load_network in overview_views.py
    
    For single-day queries: Uses actual elapsed hours
    For multi-day queries: Uses total hours in period
    
    Formula: Total Load / (Total ONBOARDED Feeders × Total Hours in Period)
    Uses actual elapsed hours for current periods.
    
    CRITICAL: For today, only sums load up to current hour
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
    
    # Get total feeders in district
    total_feeders = Feeder.objects.filter(
        business_district_id=district_id,
        is_onboarded=True
    ).count()
    
    if total_feeders == 0:
        return 0.0
    
    # ✅ For today: Only sum load up to current hour
    if to_date == today:
        current_hour = now.hour
        result = HourlyLoad.objects.filter(
            date__range=(from_date, to_date),
            feeder__business_district_id=district_id,
            feeder__is_onboarded=True
        ).filter(
            # Only include hours up to current hour for today
            models.Q(date__lt=to_date) | models.Q(date=to_date, hour__lte=current_hour)
        ).aggregate(total_load=Sum('load_mw'))
    else:
        result = HourlyLoad.objects.filter(
            date__range=(from_date, to_date),
            feeder__business_district_id=district_id,
            feeder__is_onboarded=True
        ).aggregate(total_load=Sum('load_mw'))
    
    total_load = float(result['total_load'] or 0)
    
    # Average = Total Load / (Total Onboarded Feeders × Total Hours)
    avg_load = total_load / (total_feeders * period_hours) if (total_feeders * period_hours) > 0 else 0
    
    return round(avg_load, 2)


def calculate_interruption_duration_district(district_id, from_date, to_date, exclude_types=None):
    """
    Calculate average interruption duration per day for a district.
    FOLLOWS SAME PATTERN AS calculate_interruption_duration_network in overview_views.py
    
    For single-day queries (especially today): Returns total hours per feeder (not daily average)
    For multi-day queries: Returns average hours per day per feeder
    
    CORRECTED Logic:
    - Includes ALL interruptions active during the period
    - Calculates only the hours that fall within the filtered period boundaries
    - Caps per-feeder totals to prevent overlap inflation
    
    Returns:
        tuple: (avg_duration_per_day, total_interruption_count)
    """
    # ✅ FIXED: Check for future dates
    now = timezone.now()
    today = now.date()
    
    if from_date > today:
        return 0.0, 0
    
    # Determine if single-day or multi-day query
    is_single_day = (from_date == to_date)
    
    if is_single_day:
        max_hours_per_feeder = 24.0
    else:
        period_days = (to_date - from_date).days + 1
        max_hours_per_feeder = 24.0 * period_days
    
    # Get IDs of onboarded feeders in this district
    onboarded_feeder_ids = list(
        Feeder.objects.filter(
            business_district_id=district_id,
            is_onboarded=True
        ).values_list('id', flat=True)
    )
    
    if not onboarded_feeder_ids:
        return 0.0, 0
    
    total_feeders = len(onboarded_feeder_ids)
    
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
    base_params = [end_of_period, end_of_period, start_of_period, max_hours_per_feeder, start_of_period, end_of_period, start_of_period, start_of_period]
    base_params.extend(onboarded_feeder_ids)
    
    count_params = [start_of_period, end_of_period] + onboarded_feeder_ids
    
    if exclude_types:
        placeholders = ','.join(['%s'] * len(exclude_types))
        exclusion_clause = f"AND interruption_type NOT IN ({placeholders})"
        base_params.extend(exclude_types)
        count_params.extend(exclude_types)
    
    # CORRECTED: Calculate per-feeder totals first, then cap each at max_hours_per_feeder
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
                    %s
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
    
    # Count query (only interruptions that occurred in period)
    count_query = f"""
        SELECT COUNT(*) as total_interruptions
        FROM technical_feederinterruption
        WHERE occurred_at >= %s
            AND occurred_at <= %s
            {feeder_filter}
            {exclusion_clause}
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, base_params)
        result = cursor.fetchone()
        total_hours_all_feeders = float(result[0]) if result and result[0] else 0
        
        cursor.execute(count_query, count_params)
        result = cursor.fetchone()
        total_interruptions = result[0] if result and result[0] else 0
    
    # ✅ CRITICAL: For single-day queries, return average hours per feeder (not daily average)
    # For multi-day queries, return average hours per day per feeder
    if is_single_day:
        # Single day: Average hours per feeder
        avg_hours = total_hours_all_feeders / total_feeders if total_feeders > 0 else 0
    else:
        # Multi-day: Average hours per day per feeder
        period_days = (to_date - from_date).days + 1
        avg_hours = total_hours_all_feeders / (total_feeders * period_days) if (total_feeders * period_days) > 0 else 0
    
    # Ensure non-negative and cap at 24
    avg_hours = max(0, min(avg_hours, 24.0))
    
    return round(avg_hours, 2), int(total_interruptions)


def calculate_average_interruption_duration_district(district_id, from_date, to_date):
    """
    Calculate average duration per interruption for a district.
    FOLLOWS SAME PATTERN AS calculate_average_interruption_duration_network in overview_views.py
    
    INCLUDES:
    1. Interruptions that OCCURRED within the period (resolved or ongoing)
    2. Interruptions that started BEFORE the period but are still ongoing (not resolved)
    
    CORRECTED: Only counts the hours that fall WITHIN the filtered period.
    
    For ongoing interruptions, uses NOW as the end time.
    """
    now = timezone.now()
    today = now.date()
    
    # ✨ CRITICAL: If querying future dates, return 0 (no data available yet)
    if from_date > today:
        return 0.0
    
    # Get IDs of onboarded feeders in this district
    onboarded_feeder_ids = list(
        Feeder.objects.filter(
            business_district_id=district_id,
            is_onboarded=True
        ).values_list('id', flat=True)
    )
    
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


def calculate_district_peak_load(district_id, from_date, to_date):
    """
    Get peak load for a district.
    Only considers ONBOARDED feeders.
    
    CRITICAL: For today, only looks at hours up to current hour
    """
    today = timezone.now().date()
    now = timezone.now()
    
    # ✅ For today: Only look at hours up to current hour
    if to_date == today:
        current_hour = now.hour
        result = HourlyLoad.objects.filter(
            feeder__business_district_id=district_id,
            feeder__is_onboarded=True,
            date__range=(from_date, to_date)
        ).filter(
            # Only include hours up to current hour for today
            models.Q(date__lt=to_date) | models.Q(date=to_date, hour__lte=current_hour)
        ).aggregate(peak_load=Max('load_mw'))
    else:
        result = HourlyLoad.objects.filter(
            feeder__business_district_id=district_id,
            feeder__is_onboarded=True,
            date__range=(from_date, to_date)
        ).aggregate(peak_load=Max('load_mw'))
    
    peak_load = result['peak_load'] if result and result['peak_load'] else 0
    return round(float(peak_load), 2)


def get_district_infrastructure_counts(district_id):
    """
    Get ONBOARDED feeder count and customer population for a district
    
    UPDATED: Only counts onboarded feeders.
    """
    from django.db.models import Count
    from common.models import DistributionTransformer
    from commercial.models import Customer
    
    # Count onboarded feeders
    feeder_count = Feeder.objects.filter(
        business_district_id=district_id,
        is_onboarded=True
    ).count()
    
    # Count customers on onboarded feeders
    customer_count = Customer.objects.filter(
        transformer__feeder__business_district_id=district_id,
        transformer__feeder__is_onboarded=True
    ).count()
    
    return {
        'feeder_count': feeder_count,
        'customer_population': customer_count
    }


def calculate_district_metrics(district, from_date, to_date):
    """
    Calculate all metrics for a district.
    FOLLOWS SAME PATTERN AND LOGIC AS technical_overview_view in overview_views.py
    
    Only considers ONBOARDED feeders for all calculations.
    Uses actual elapsed time for current periods.
    """
    # Get infrastructure counts first
    infrastructure = get_district_infrastructure_counts(district.id)
    feeder_count = infrastructure['feeder_count']
    
    # If no onboarded feeders, return zeros
    if feeder_count == 0:
        return {
            "energy_delivered": 0.0,
            "avg_supply": 0.0,
            "avg_load": 0.0,
            "avg_duration": 0.0,
            "turnaround": 0.0,
            "avg_interruption_duration": 0.0,
            "ftc": 0,
            "avg_daily_interruptions": 0.0,
            "feeder_count": 0,
            "peak_load": 0.0,
            "customer_population": 0,
        }
    
    # Calculate period days for daily averages
    if from_date == to_date:
        period_days = 1
    else:
        period_days = (to_date - from_date).days + 1
    
    # 1. Energy delivered (Hybrid: EnergyDelivered + HourlyLoad fallback)
    energy_delivered = calculate_energy_delivered_district(district.id, from_date, to_date)
    
    # 2. Average hours of supply
    avg_supply = calculate_hours_of_supply_district(district.id, from_date, to_date)
    
    # 3. Average load
    avg_load = calculate_average_load_district(district.id, from_date, to_date)
    
    # 4. Interruption duration (ALL types, includes ALL active interruptions)
    avg_duration, ftc = calculate_interruption_duration_district(district.id, from_date, to_date)
    
    # 5. Turnaround time (LOCAL faults only - exclude L/S, TCN, etc.)
    turnaround, _ = calculate_interruption_duration_district(
        district.id, from_date, to_date, exclude_types=TURNAROUND_EXCLUSIONS
    )
    
    # 6. Average interruption duration (hours per interruption event)
    avg_int_duration = calculate_average_interruption_duration_district(district.id, from_date, to_date)
    
    # 7. Peak load
    peak_load = calculate_district_peak_load(district.id, from_date, to_date)
    
    # Calculate daily interruptions (average per feeder per day)
    if feeder_count > 0 and period_days > 0:
        avg_daily_interruptions = float(ftc) / (feeder_count * period_days)
    else:
        avg_daily_interruptions = 0.0
    
    # Validate all time-based metrics are capped at 24 hours
    avg_supply = min(avg_supply, 24.0)
    avg_duration = min(avg_duration, 24.0)
    turnaround = min(turnaround, 24.0)
    
    return {
        "energy_delivered": round(energy_delivered, 2),
        "avg_supply": round(avg_supply, 2),
        "avg_load": round(avg_load, 2),
        "avg_duration": round(avg_duration, 2),
        "turnaround": round(turnaround, 2),
        "avg_interruption_duration": round(avg_int_duration, 2),
        "ftc": int(ftc),
        "avg_daily_interruptions": round(avg_daily_interruptions, 2),
        "feeder_count": int(feeder_count),
        "peak_load": round(peak_load, 2),
        "customer_population": infrastructure['customer_population'],
    }


@api_view(["GET"])
def all_business_districts_technical_summary(request):
    """
    Technical summary for all business districts in a state.
    FOLLOWS SAME PATTERN AND STRUCTURE AS technical_overview_view in overview_views.py
    
    Only considers ONBOARDED feeders for all calculations.
    Uses actual elapsed time for current periods.
    Returns ALL districts in the state, even those with no onboarded feeders (metrics will be 0).
    
    Query Parameters:
    - state: State name (required)
    - mode: monthly, daily, custom (default: monthly)
    - For monthly: year, month
    - For daily: from_date
    - For custom: from_date, to_date
    
    Key Metrics:
    - energy_delivered: Total energy in MWh (hybrid calculation)
    - avg_supply: Average hours per day across ONBOARDED feeders (0-24)
    - avg_load: Average load in MW
    - avg_duration: Average interruption hours per day (0-24, includes ALL types)
    - turnaround: Average local fault hours per day (0-24, excludes L/S & TCN)
    - avg_interruption_duration: Average hours per interruption event
    - ftc: Feeder Tripping Count (interruptions that occurred in period)
    - avg_daily_interruptions: Average interruptions per feeder per day
    - feeder_count: Number of ONBOARDED feeders
    - peak_load: Peak load in MW
    - customer_population: Customers on ONBOARDED feeders
    """
    # Get required state parameter
    state = request.GET.get("state")
    if not state:
        return Response({"error": "State parameter is required"}, status=400)
    
    # Parse date range (SAME AS OVERVIEW)
    try:
        date_info = parse_date_range_districts(request)
        from_date = date_info["start_date"]
        to_date = date_info["end_date"]
        period_days = date_info["period_days"]
        mode = date_info["mode"]
    except (ValueError, KeyError) as e:
        return Response({"error": str(e)}, status=400)
    
    # Get ALL business districts in the state
    districts = BusinessDistrict.objects.filter(
        state__name__iexact=state
    ).order_by('name')
    
    if not districts.exists():
        return Response({"error": f"No districts found for state: {state}"}, status=404)
    
    response_data = []
    
    for district in districts:
        try:
            # Calculate metrics (will return zeros for districts with no onboarded feeders)
            district_metrics = calculate_district_metrics(district, from_date, to_date)
            
            # Add FTC per feeder (handle division by zero)
            if district_metrics['feeder_count'] > 0:
                ftc_per_feeder = round(
                    district_metrics["ftc"] / district_metrics["feeder_count"], 2
                )
            else:
                ftc_per_feeder = 0.0
            
            district_metrics["ftc_per_feeder"] = ftc_per_feeder
            
            # Include ALL districts, even if they have no onboarded feeders
            response_data.append({
                "district": district.name,
                "metrics": district_metrics
            })
                
        except Exception as e:
            # Include district with zero metrics on error
            response_data.append({
                "district": district.name,
                "metrics": {
                    "energy_delivered": 0.0,
                    "avg_supply": 0.0,
                    "avg_load": 0.0,
                    "avg_duration": 0.0,
                    "turnaround": 0.0,
                    "avg_interruption_duration": 0.0,
                    "ftc": 0,
                    "avg_daily_interruptions": 0.0,
                    "feeder_count": 0,
                    "peak_load": 0.0,
                    "customer_population": 0,
                    "ftc_per_feeder": 0.0,
                    "_error": str(e)
                }
            })
    
    # Build response (SAME STRUCTURE AS OVERVIEW)
    final_response = {
        "state": state,
        "mode": mode,
        "period": {
            "start_date": from_date.isoformat(),
            "end_date": to_date.isoformat(),
            "days": period_days
        },
        "districts": response_data,
        "metadata": {
            "total_districts": len(response_data),
            "districts_with_onboarded_feeders": sum(
                1 for d in response_data if d["metrics"]["feeder_count"] > 0
            ),
            "onboarded_feeders_only": True
        }
    }
    
    return Response(final_response)