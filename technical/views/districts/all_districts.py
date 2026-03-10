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
from technical.utils.energy_utils import calculate_energy_delivered
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


def calculate_district_energy_delivered_sql(district_id, from_date, to_date, voltage_level=None):
    """
    Calculate total energy delivered for a district using hybrid approach.
    Optionally filtered by voltage_level ('11kv' or '33kv').
    """
    voltage_clause = "AND f.voltage_level = %s" if voltage_level else ""
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
            WHERE f.business_district_id = %s
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
    
    params = [from_date, to_date, district_id]
    if voltage_level:
        params.append(voltage_level)
    params += [from_date, to_date]
    
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        result = cursor.fetchone()
        total_energy = float(result[0]) if result and result[0] else 0.0
    
    return round(total_energy, 2)


def calculate_district_hours_of_supply_sql(district_id, from_date, to_date, voltage_level=None):
    """
    Calculate average hours of supply per day for a district using raw SQL.
    Optionally filtered by voltage_level ('11kv' or '33kv').
    """
    voltage_clause = "AND f.voltage_level = %s" if voltage_level else ""
    feeder_count_query = f"""
        SELECT COUNT(DISTINCT f.id)
        FROM common_feeder f
        WHERE f.business_district_id = %s
            AND f.is_onboarded = TRUE
            AND (f.onboarded_at IS NULL OR f.onboarded_at <= %s)
            {voltage_clause}
    """
    
    hours_query = f"""
        SELECT 
            COUNT(DISTINCT CONCAT(hl.feeder_id, '-', hl.date, '-', hl.hour)) as total_hours
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id
        WHERE f.business_district_id = %s
            AND f.is_onboarded = TRUE
            AND (f.onboarded_at IS NULL OR f.onboarded_at <= %s)
            {voltage_clause}
            AND hl.date BETWEEN %s AND %s
            AND hl.load_mw > 0
    """
    
    count_params = [district_id, to_date] + ([voltage_level] if voltage_level else [])
    hours_params = [district_id, to_date] + ([voltage_level] if voltage_level else []) + [from_date, to_date]
    
    with connection.cursor() as cursor:
        cursor.execute(feeder_count_query, count_params)
        result = cursor.fetchone()
        total_feeders = result[0] if result and result[0] else 0
        
        if total_feeders == 0:
            return 0.0
        
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


def calculate_district_interruption_metrics_sql(district_id, from_date, to_date, exclude_types=None, voltage_level=None):
    """
    Calculate average interruption duration per day for a district using raw SQL.
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
        WHERE f.business_district_id = %s
            AND f.is_onboarded = TRUE
            AND (f.onboarded_at IS NULL OR f.onboarded_at <= %s)
            {voltage_clause}
    """
    
    exclusion_clause = ""
    duration_params = [end_of_period, end_of_period, start_of_period, max_hours_per_feeder, district_id]
    if voltage_level:
        duration_params.append(voltage_level)
    duration_params += [start_of_period, end_of_period, start_of_period, start_of_period]
    
    count_params = [district_id]
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
            WHERE f.business_district_id = %s
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
        WHERE f.business_district_id = %s
            AND f.is_onboarded = TRUE
            {voltage_clause}
            AND fi.occurred_at >= %s
            AND fi.occurred_at <= %s
            {exclusion_clause}
    """
    
    feeder_count_params = [district_id, to_date] + ([voltage_level] if voltage_level else [])
    with connection.cursor() as cursor:
        cursor.execute(feeder_count_query, feeder_count_params)
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
    
    # ✅ CRITICAL: For single-day queries, return average hours per feeder (not daily average)
    # For multi-day queries, return average hours per day per feeder
    if is_single_day:
        # Single day: Average hours per feeder
        avg_hours = total_hours / total_feeders if total_feeders > 0 else 0
    else:
        # Multi-day: Average hours per day per feeder
        avg_hours = total_hours / (total_feeders * period_days) if (total_feeders * period_days) > 0 else 0
    
    # Ensure non-negative and cap at 24
    avg_hours = max(0, min(avg_hours, 24.0))
    
    return round(avg_hours, 2), int(total_interruptions)


def calculate_district_avg_interruption_duration_sql(district_id, from_date, to_date, voltage_level=None):
    """
    Calculate average duration per interruption event for a district using raw SQL.
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
        WHERE f.business_district_id = %s
            AND f.is_onboarded = TRUE
            {voltage_clause}
            AND (
                (fi.occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
                OR (fi.occurred_at < %s AND fi.restored_at IS NULL)
            )
    """
    
    params = [now, end_of_period, start_of_period, district_id]
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


def calculate_district_peak_load_sql(district_id, from_date, to_date, voltage_level=None):
    """
    Get peak load for a district. Optionally filtered by voltage_level.
    """
    voltage_clause = "AND f.voltage_level = %s" if voltage_level else ""
    query = f"""
        SELECT 
            MAX(hl.load_mw) as peak_load
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id
        WHERE f.business_district_id = %s
            AND f.is_onboarded = TRUE
            {voltage_clause}
            AND hl.date BETWEEN %s AND %s
    """
    
    params = [district_id] + ([voltage_level] if voltage_level else []) + [from_date, to_date]
    
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        result = cursor.fetchone()
        peak_load = result[0] if result and result[0] else 0
    
    return round(float(peak_load), 2)


def get_district_infrastructure_counts_sql(district_id, voltage_level=None):
    """
    Get ONBOARDED feeder count and customer population for a district.
    Optionally filtered by voltage_level ('11kv' or '33kv').
    """
    voltage_clause = "AND f.voltage_level = %s" if voltage_level else ""
    query = f"""
        SELECT 
            COUNT(DISTINCT f.id) as feeder_count,
            COUNT(DISTINCT c.id) as customer_count
        FROM common_feeder f
        LEFT JOIN common_distributiontransformer dt ON dt.feeder_id = f.id
        LEFT JOIN commercial_customer c ON c.transformer_id = dt.id
        WHERE f.business_district_id = %s
            AND f.is_onboarded = TRUE
            {voltage_clause}
    """
    
    params = [district_id] + ([voltage_level] if voltage_level else [])
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        result = cursor.fetchone()
        
        if result:
            return {
                'feeder_count': int(result[0] or 0),
                'customer_population': int(result[1] or 0)
            }
    
    return {'feeder_count': 0, 'customer_population': 0}


def calculate_district_metrics(district, from_date, to_date, voltage_level=None):
    """
    Calculate all metrics for a district using optimized SQL.
    Optionally scoped to feeders of a specific voltage_level ('11kv' or '33kv').
    """
    today = timezone.now().date()
    now = timezone.now()
    
    if to_date == today:
        full_days = (to_date - from_date).days
        current_hour = now.hour
        fractional_day = current_hour / 24.0
        period_days = full_days + fractional_day
    else:
        period_days = (to_date - from_date).days + 1
    
    try:
        avg_supply = float(calculate_district_hours_of_supply_sql(
            district.id, from_date, to_date, voltage_level=voltage_level
        ))
    except Exception as e:
        print(f"Error calculating supply hours for {district.name}: {e}")
        avg_supply = 0.0
    
    try:
        avg_duration, ftc = calculate_district_interruption_metrics_sql(
            district.id, from_date, to_date, voltage_level=voltage_level
        )
        avg_duration = float(avg_duration)
    except Exception as e:
        print(f"Error calculating interruption metrics for {district.name}: {e}")
        avg_duration, ftc = 0.0, 0
    
    try:
        turnaround, _ = calculate_district_interruption_metrics_sql(
            district.id, from_date, to_date, exclude_types=TURNAROUND_EXCLUSIONS, voltage_level=voltage_level
        )
        turnaround = float(turnaround)
    except Exception as e:
        print(f"Error calculating turnaround time for {district.name}: {e}")
        turnaround = 0.0
    
    try:
        avg_int_duration = float(calculate_district_avg_interruption_duration_sql(
            district.id, from_date, to_date, voltage_level=voltage_level
        ))
    except Exception as e:
        print(f"Error calculating avg interruption duration for {district.name}: {e}")
        avg_int_duration = 0.0
    
    try:
        peak_load = float(calculate_district_peak_load_sql(
            district.id, from_date, to_date, voltage_level=voltage_level
        ))
    except Exception as e:
        print(f"Error calculating peak load for {district.name}: {e}")
        peak_load = 0.0
    
    try:
        infrastructure = get_district_infrastructure_counts_sql(district.id, voltage_level=voltage_level)
    except Exception as e:
        print(f"Error getting infrastructure counts for {district.name}: {e}")
        infrastructure = {'feeder_count': 0, 'customer_population': 0}
    
    try:
        feeder_ids_for_energy = list(
            Feeder.objects.filter(
                business_district_id=district.id,
                is_onboarded=True,
                **({'voltage_level': voltage_level} if voltage_level else {})
            ).values_list('id', flat=True)
        )
        energy_delivered = calculate_energy_delivered(feeder_ids_for_energy, from_date, to_date)['total_mwh']
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
    avg_duration = round(24.0 - avg_supply, 2)  # always sums to 24 with supply
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
    
    # ✅ Parse voltage level filter
    feeder_type_param = request.GET.get("feeder_type", "")
    voltage_level = feeder_type_param if feeder_type_param in ("11kv", "33kv") else None
    
    districts = BusinessDistrict.objects.filter(
        state__name__iexact=state
    ).order_by('name')
    
    response_data = []
    
    for district in districts:
        try:
            district_metrics = calculate_district_metrics(district, from_date, to_date, voltage_level=voltage_level)
            
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