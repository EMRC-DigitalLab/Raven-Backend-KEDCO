# technical/views/districts/all_districts.py
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Sum, Avg, Max
from django.db import connection
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from common.models import Feeder, BusinessDistrict
from technical.models import HourlyLoad, FeederInterruption, EnergyDelivered
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


def get_date_range_and_mode(request):
    """Enhanced date range parsing with support for multiple modes"""
    mode = request.GET.get("mode", "monthly")
    today = datetime.now().date()
    
    if mode in ["daily", "weekly", "custom", "range"]:
        try:
            from_date_str = request.GET.get("from_date")
            to_date_str = request.GET.get("to_date")
            
            if not from_date_str or not to_date_str:
                raise ValueError("from_date and to_date are required for this mode")
            
            # Parse ISO datetime strings
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


def calculate_district_energy_delivered_sql(district_id, from_date, to_date):
    """
    Calculate total energy delivered for a district using hybrid approach.
    OPTIMIZED: Uses raw SQL with EnergyDelivered primary, HourlyLoad fallback.
    
    Priority:
    1. Use EnergyDelivered if available for a feeder-date combination
    2. Fall back to HourlyLoad sum for feeder-dates without EnergyDelivered
    
    Only considers ONBOARDED feeders.
    
    Returns:
        Total energy in MWh
    """
    # Use raw SQL for optimal performance
    query = """
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
            WHERE f.business_district_id = %s
                AND f.is_onboarded = TRUE
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
    
    params = [from_date, to_date, district_id, from_date, to_date]
    
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        result = cursor.fetchone()
        total_energy = float(result[0]) if result and result[0] else 0.0
    
    return round(total_energy, 2)


def calculate_district_hours_of_supply_sql(district_id, from_date, to_date):
    """
    Calculate average hours of supply per day for a district using raw SQL.
    
    UPDATED Logic:
    - Only considers ONBOARDED feeders
    - Uses actual elapsed time for current periods
    - Numerator: Total hours supplied across all ONBOARDED feeders in district
    - Denominator: Total ONBOARDED feeders in district × Days (fractional for current periods)
    """
    # ✅ Calculate actual elapsed time for current periods
    today = timezone.now().date()
    now = timezone.now()
    
    if to_date == today:
        # For current day, calculate fractional days based on current hour
        full_days = (to_date - from_date).days
        current_hour = now.hour
        fractional_day = current_hour / 24.0
        period_days = full_days + fractional_day
    else:
        # For past periods, use full days
        period_days = (to_date - from_date).days + 1
    
    # Get total ONBOARDED feeders in district
    feeder_count_query = """
        SELECT COUNT(DISTINCT f.id)
        FROM common_feeder f
        WHERE f.business_district_id = %s
            AND f.is_onboarded = TRUE
    """
    
    # Get total hours supplied (ONBOARDED feeders only)
    hours_query = """
        SELECT 
            COUNT(DISTINCT CONCAT(hl.feeder_id, '-', hl.date, '-', hl.hour)) as total_hours
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id
        WHERE f.business_district_id = %s
            AND f.is_onboarded = TRUE
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
    
    # Average = Total hours / (Total onboarded feeders × Days)
    avg_hours_per_day = total_hours / (total_feeders * period_days)
    
    return round(min(avg_hours_per_day, 24.0), 2)


def calculate_district_interruption_metrics_sql(district_id, from_date, to_date, exclude_types=None):
    """
    Calculate average interruption duration per day for a district using raw SQL.
    
    UPDATED Logic:
    - Only considers ONBOARDED feeders
    - Uses actual elapsed time for current periods
    - Includes ALL interruptions active during the period (not just those that started in the period)
    - Calculates only the hours that fall within the filtered period boundaries
    - Uses timezone-aware datetime ranges for consistency
    - Numerator: Total interruption hours across all ONBOARDED feeders in district
    - Denominator: Total ONBOARDED feeders in district × Days (fractional for current periods)
    
    Returns:
        tuple: (avg_duration_per_day, total_interruption_count)
            - avg_duration_per_day: Average interruption hours per day
            - total_interruption_count: COUNT of interruptions that occurred in period (for FTC)
    """
    # ✅ Check for future dates
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
    
    # Get total ONBOARDED feeders in district
    feeder_count_query = """
        SELECT COUNT(DISTINCT f.id)
        FROM common_feeder f
        WHERE f.business_district_id = %s
            AND f.is_onboarded = TRUE
    """
    
    # Build exclusion clause
    exclusion_clause = ""
    # Parameters for duration calculation (includes all active interruptions)
    max_hours = period_days * 24.0
    duration_params = [end_of_period, end_of_period, start_of_period, max_hours, district_id, start_of_period, end_of_period, start_of_period, start_of_period]
    
    # Parameters for count calculation (only interruptions that occurred in period)
    count_params = [district_id, start_of_period, end_of_period]
    
    if exclude_types:
        placeholders = ','.join(['%s'] * len(exclude_types))
        exclusion_clause = f"AND fi.interruption_type NOT IN ({placeholders})"
        duration_params.extend(exclude_types)
        count_params.extend(exclude_types)
    
    # Calculate per-feeder totals first, then cap each at (24 * period_days)
    # Only considers ONBOARDED feeders
    # ✅ Uses timezone-aware datetime ranges
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
                AND f.is_onboarded = TRUE
                AND (
                    fi.occurred_at >= %s AND fi.occurred_at <= %s
                    OR (fi.occurred_at < %s AND (fi.restored_at IS NULL OR fi.restored_at >= %s))
                )
                {exclusion_clause}
            GROUP BY fi.feeder_id
        ) per_feeder_totals
    """
    
    # Separate query for count (only interruptions that occurred in period, ONBOARDED feeders only)
    # ✅ Uses timezone-aware datetime ranges
    interruption_count_query = f"""
        SELECT COUNT(*) as total_interruptions
        FROM technical_feederinterruption fi
        INNER JOIN common_feeder f ON fi.feeder_id = f.id
        WHERE f.business_district_id = %s
            AND f.is_onboarded = TRUE
            AND fi.occurred_at >= %s
            AND fi.occurred_at <= %s
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
    
    # Average = Total hours / (Total onboarded feeders × Days)
    avg_hours_per_day = total_hours / (total_feeders * period_days)
    
    # Ensure non-negative and cap at 24
    avg_hours_per_day = max(0, min(avg_hours_per_day, 24.0))
    
    return round(avg_hours_per_day, 2), int(total_interruptions)


def calculate_district_avg_interruption_duration_sql(district_id, from_date, to_date):
    """
    Calculate average duration per interruption event for a district using raw SQL.
    
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
        WHERE f.business_district_id = %s
            AND f.is_onboarded = TRUE
            AND (
                (fi.occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
                OR (fi.occurred_at < %s AND fi.restored_at IS NULL)
            )
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [now, end_of_period, start_of_period, district_id, from_date, to_date, start_of_period])
        result = cursor.fetchone()
        interruption_count = result[0] if result else 0
        total_hours = float(result[1]) if result else 0
    
    # Calculate average
    avg_duration = total_hours / interruption_count if interruption_count > 0 else 0
    
    return round(avg_duration, 2)


def calculate_district_peak_load_sql(district_id, from_date, to_date):
    """
    Get peak load for a district
    
    UPDATED: Only considers ONBOARDED feeders.
    """
    query = """
        SELECT 
            MAX(hl.load_mw) as peak_load
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id
        WHERE f.business_district_id = %s
            AND f.is_onboarded = TRUE
            AND hl.date BETWEEN %s AND %s
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [district_id, from_date, to_date])
        result = cursor.fetchone()
        peak_load = result[0] if result and result[0] else 0
    
    return round(float(peak_load), 2)


def get_district_infrastructure_counts_sql(district_id):
    """
    Get ONBOARDED feeder count and customer population for a district
    
    UPDATED: Only counts onboarded feeders.
    """
    query = """
        SELECT 
            COUNT(DISTINCT f.id) as feeder_count,
            COUNT(DISTINCT c.id) as customer_count
        FROM common_feeder f
        LEFT JOIN common_distributiontransformer dt ON dt.feeder_id = f.id
        LEFT JOIN commercial_customer c ON c.transformer_id = dt.id
        WHERE f.business_district_id = %s
            AND f.is_onboarded = TRUE
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [district_id])
        result = cursor.fetchone()
        
        if result:
            return {
                'feeder_count': int(result[0] or 0),
                'customer_population': int(result[1] or 0)
            }
    
    return {'feeder_count': 0, 'customer_population': 0}


def calculate_district_metrics(district, from_date, to_date):
    """
    Calculate all metrics for a district using optimized SQL.
    
    UPDATED: 
    - Only considers ONBOARDED feeders for all calculations
    - Uses actual elapsed time for current periods (fractional days)
    - Uses timezone-aware datetime ranges for consistency
    - Uses hybrid energy calculation
    
    CORRECTED: Uses standardized field names and proper interruption calculation.
    """
    # ✅ Calculate actual elapsed time for current periods
    today = timezone.now().date()
    now = timezone.now()
    
    if to_date == today:
        # For current day, calculate fractional days based on current hour
        full_days = (to_date - from_date).days
        current_hour = now.hour
        fractional_day = current_hour / 24.0
        period_days = full_days + fractional_day
    else:
        # For past periods, use full days
        period_days = (to_date - from_date).days + 1
    
    try:
        # 1. Average Supply Hours (ONBOARDED feeders only, fractional days)
        avg_supply = float(calculate_district_hours_of_supply_sql(
            district.id, from_date, to_date
        ))
    except Exception as e:
        print(f"Error calculating supply hours for {district.name}: {e}")
        avg_supply = 0.0
    
    try:
        # 2. Interruption Duration (all types, includes ALL active interruptions, ONBOARDED feeders only)
        avg_duration, ftc = calculate_district_interruption_metrics_sql(
            district.id, from_date, to_date
        )
        avg_duration = float(avg_duration)
    except Exception as e:
        print(f"Error calculating interruption metrics for {district.name}: {e}")
        avg_duration, ftc = 0.0, 0
    
    try:
        # 3. Turnaround Time (exclude L/S and TCN, includes ALL active local faults, ONBOARDED feeders only)
        turnaround, _ = calculate_district_interruption_metrics_sql(
            district.id, from_date, to_date, exclude_types=TURNAROUND_EXCLUSIONS
        )
        turnaround = float(turnaround)
    except Exception as e:
        print(f"Error calculating turnaround time for {district.name}: {e}")
        turnaround = 0.0
    
    try:
        # 4. Average Interruption Duration (hours per interruption event, ONBOARDED feeders only)
        avg_int_duration = float(calculate_district_avg_interruption_duration_sql(
            district.id, from_date, to_date
        ))
    except Exception as e:
        print(f"Error calculating avg interruption duration for {district.name}: {e}")
        avg_int_duration = 0.0
    
    try:
        # 5. Peak Load (ONBOARDED feeders only)
        peak_load = float(calculate_district_peak_load_sql(
            district.id, from_date, to_date
        ))
    except Exception as e:
        print(f"Error calculating peak load for {district.name}: {e}")
        peak_load = 0.0
    
    try:
        # 6. Infrastructure counts (ONBOARDED feeders only)
        infrastructure = get_district_infrastructure_counts_sql(district.id)
    except Exception as e:
        print(f"Error getting infrastructure counts for {district.name}: {e}")
        infrastructure = {'feeder_count': 0, 'customer_population': 0}
    
    try:
        # 7. Energy delivered (Hybrid: EnergyDelivered + HourlyLoad fallback, ONBOARDED feeders only)
        energy_delivered = float(calculate_district_energy_delivered_sql(
            district.id, from_date, to_date
        ))
    except Exception as e:
        print(f"Error calculating energy for {district.name}: {e}")
        energy_delivered = 0.0
    
    # Calculate daily interruptions (average per feeder per day)
    feeder_count = infrastructure['feeder_count']
    if feeder_count > 0 and period_days > 0:
        avg_daily_interruptions = float(ftc) / (feeder_count * period_days)
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
        "avg_interruption_duration": round(avg_int_duration, 2),
        "ftc": int(ftc),
        "avg_daily_interruptions": round(avg_daily_interruptions, 2),
        "feeder_count": int(feeder_count),
        "peak_load": round(peak_load, 2),
        "customer_population": infrastructure['customer_population'],
        "energy_delivered": round(energy_delivered, 2),
        "_source": "optimized_sql"
    }


@api_view(["GET"])
def all_business_districts_technical_summary(request):
    """
    Technical summary for all business districts in a state.
    
    UPDATED: 
    - Only considers ONBOARDED feeders for all calculations
    - Uses actual elapsed time for current periods (fractional days)
    - Uses timezone-aware datetime ranges for consistency
    - Uses hybrid energy calculation (EnergyDelivered + HourlyLoad fallback)
    - Returns ALL districts in the state, even those with no onboarded feeders (metrics will be 0)
    
    Query Parameters:
    - state: State name (required)
    - mode: monthly, yearly, daily, weekly, custom, range
    - For monthly: year, month
    - For yearly: year
    - For others: from_date, to_date (ISO format)
    
    Key Metrics (CORRECTED - ONBOARDED FEEDERS ONLY):
    - avg_supply: Average hours per day across all ONBOARDED feeders in district (0-24)
      * Uses fractional days for current periods
    - avg_duration: Average interruption hours per day across all ONBOARDED feeders (0-24)
      * Includes ALL interruptions active during the period
      * Calculates only hours that fall within the period
      * Uses fractional days for current periods
    - turnaround: Average local fault hours per day across all ONBOARDED feeders (0-24)
      * Includes ALL local faults active during the period
      * Calculates only hours that fall within the period
      * Uses fractional days for current periods
    - avg_interruption_duration: Average hours per interruption event (not per day)
      * Includes interruptions that occurred in period AND ongoing ones from before
      * Formula: Total duration of all interruptions / Count of interruptions
    - avg_daily_interruptions: Average interruptions per ONBOARDED feeder per day
    - ftc: Feeder Tripping Count - total number of interruptions that OCCURRED in period (ONBOARDED feeders only)
    - energy_delivered: Total energy in MWh (hybrid calculation, ONBOARDED feeders only)
    - feeder_count: Number of ONBOARDED feeders
    - customer_population: Customers on ONBOARDED feeders
    """
    state = request.GET.get("state")
    if not state:
        return Response({"error": "State parameter is required"}, status=400)
    
    try:
        from_date, to_date, mode = get_date_range_and_mode(request)
    except ValueError as e:
        return Response({"error": str(e)}, status=400)
    
    print(f"DEBUG: Request params: {dict(request.GET)}")
    print(f"DEBUG: Date range: {from_date} to {to_date}, mode: {mode}")
    
    # Get ALL business districts in the state (not just those with onboarded feeders)
    districts = BusinessDistrict.objects.filter(
        state__name__iexact=state
    ).order_by('name')
    
    print(f"DEBUG: Found {districts.count()} districts in {state}")
    
    response_data = []
    
    for district in districts:
        print(f"DEBUG: Processing district: {district.name}")
        try:
            # Calculate metrics using SQL (ONBOARDED feeders only)
            # Will return zeros for districts with no onboarded feeders
            district_metrics = calculate_district_metrics(district, from_date, to_date)
            
            # Add FTC per feeder (handle division by zero)
            if district_metrics['feeder_count'] > 0:
                ftc_per_feeder = round(
                    district_metrics["ftc"] / district_metrics["feeder_count"], 2
                )
            else:
                ftc_per_feeder = 0.0
            
            district_metrics["ftc_per_feeder"] = ftc_per_feeder
            
            # Include ALL districts, even if they have no onboarded feeders (metrics will be 0)
            response_data.append({
                "district": district.name,
                "metrics": district_metrics
            })
            print(f"DEBUG: Added {district.name} to response (feeder_count: {district_metrics['feeder_count']})")
                
        except Exception as e:
            print(f"ERROR: Error for district {district.name}: {str(e)}")
            import traceback
            traceback.print_exc()
            
            # Include district with zero metrics on error
            response_data.append({
                "district": district.name,
                "metrics": {
                    "avg_supply": 0.0,
                    "avg_duration": 0.0,
                    "turnaround": 0.0,
                    "avg_interruption_duration": 0.0,
                    "ftc": 0,
                    "avg_daily_interruptions": 0.0,
                    "feeder_count": 0,
                    "peak_load": 0.0,
                    "customer_population": 0,
                    "energy_delivered": 0.0,
                    "ftc_per_feeder": 0.0,
                    "_source": "error_fallback",
                    "_error": str(e)
                }
            })
    
    final_response = {
        "districts": response_data,
        "_metadata": {
            "state": state,
            "mode": mode,
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "period_days": (to_date - from_date).days + 1,
            "total_districts": len(response_data),
            "districts_with_onboarded_feeders": sum(1 for d in response_data if d["metrics"]["feeder_count"] > 0),
            "onboarded_feeders_only": True  # Indicator that only onboarded feeders are counted
        }
    }
    
    print(f"DEBUG: Final response has {len(response_data)} districts ({final_response['_metadata']['districts_with_onboarded_feeders']} with onboarded feeders)")
    
    return Response(final_response)


# Legacy function for backward compatibility
def get_date_range(request):
    """Legacy function maintained for backward compatibility"""
    mode = request.GET.get("mode", "monthly")
    if mode == "range":
        from_date = datetime.strptime(request.GET.get("from_date"), "%Y-%m-%d").date()
        to_date = datetime.strptime(request.GET.get("to_date"), "%Y-%m-%d").date()
    else:
        year = int(request.GET.get("year", datetime.today().year))
        month = int(request.GET.get("month", datetime.today().month))
        from_date = datetime(year, month, 1).date()
        to_date = (from_date + relativedelta(months=1)) - timedelta(days=1)
    return from_date, to_date