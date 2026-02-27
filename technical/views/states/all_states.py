# technical/views/states/all_states.py
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Sum, Avg, Max, Q
from django.db import connection
from datetime import datetime, timedelta
from common.models import State, Feeder
from technical.models import HourlyLoad, FeederInterruption, EnergyDelivered
from technical.utils.energy_utils import calculate_energy_delivered
from commercial.models import Customer
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from technical.constants import TURNAROUND_EXCLUSIONS

def get_date_range_from_request(request):
    """Enhanced date range parsing with support for multiple modes"""
    mode = request.GET.get("mode", "monthly")
    today = datetime.now().date()
    
    if mode in ["daily", "weekly", "custom", "range"]:
        try:
            from_date_str = request.GET.get("from_date")
            to_date_str = request.GET.get("to_date")
            
            if not from_date_str:
                # For daily mode, if only from_date is missing, use today
                if mode == "daily":
                    from_date = datetime.now().date()
                    to_date = from_date
                    return from_date, to_date, mode
                raise ValueError("from_date is required for this mode")
            
            if mode == "daily" and not to_date_str:
                # For daily mode, to_date defaults to from_date
                to_date_str = from_date_str
            
            if not to_date_str:
                raise ValueError("to_date is required for this mode")
            
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


def calculate_state_energy_delivered_sql(state_id, from_date, to_date, voltage_level=None):
    """
    Calculate total energy delivered for a state.
    Uses smart meter-primary / system-fallback logic per feeder.
    """
    feeder_qs = Feeder.objects.filter(
        business_district__state_id=state_id,
        is_onboarded=True,
    )
    if voltage_level:
        feeder_qs = feeder_qs.filter(voltage_level=voltage_level)
    feeder_ids = list(feeder_qs.values_list('id', flat=True))
    return calculate_energy_delivered(feeder_ids, from_date, to_date)['total_mwh']


def calculate_state_hours_of_supply_sql(state_id, from_date, to_date, voltage_level=None):
    """
    Calculate average hours of supply per day for a state using raw SQL.
    Optionally filtered by voltage_level ('11kv' or '33kv').
    """
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
    
    count_params = [state_id, to_date] + ([voltage_level] if voltage_level else [])
    hours_params = [state_id, to_date] + ([voltage_level] if voltage_level else []) + [from_date, to_date]
    
    with connection.cursor() as cursor:
        cursor.execute(feeder_count_query, count_params)
        result = cursor.fetchone()
        total_feeders = result[0] if result and result[0] else 0
        
        if total_feeders == 0:
            return 0.0
        
        cursor.execute(hours_query, hours_params)
        result = cursor.fetchone()
        total_hours = result[0] if result and result[0] else 0
    
    if from_date == to_date:
        avg_hours = total_hours / total_feeders if total_feeders > 0 else 0
    else:
        period_days = (to_date - from_date).days + 1
        avg_hours = total_hours / (total_feeders * period_days) if (total_feeders * period_days) > 0 else 0
    
    return round(min(avg_hours, 24.0), 2)


def calculate_state_interruption_metrics_sql(state_id, from_date, to_date, exclude_types=None, voltage_level=None):
    """
    Calculate average interruption duration per day for a state using raw SQL.
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


def calculate_state_peak_load_sql(state_id, from_date, to_date, voltage_level=None):
    """
    Get the peak load within the date range for a state using raw SQL.
    Optionally filtered by voltage_level ('11kv' or '33kv').
    """
    voltage_clause = "AND f.voltage_level = %s" if voltage_level else ""
    query = f"""
        SELECT 
            MAX(hl.load_mw) as peak_load
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
            AND f.is_onboarded = TRUE
            {voltage_clause}
            AND hl.date BETWEEN %s AND %s
    """
    
    params = [state_id] + ([voltage_level] if voltage_level else []) + [from_date, to_date]
    
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        result = cursor.fetchone()
        peak_load = result[0] if result and result[0] else 0
    
    return round(float(peak_load), 2)


def get_state_infrastructure_counts_sql(state_id, voltage_level=None):
    """
    Get ONBOARDED feeder count and customer population for a state using raw SQL.
    Optionally filtered by voltage_level ('11kv' or '33kv').
    """
    voltage_clause = "AND f.voltage_level = %s" if voltage_level else ""
    query = f"""
        SELECT 
            COUNT(DISTINCT f.id) as feeder_count,
            COUNT(DISTINCT c.id) as customer_count
        FROM common_feeder f
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        LEFT JOIN common_distributiontransformer dt ON dt.feeder_id = f.id
        LEFT JOIN commercial_customer c ON c.transformer_id = dt.id
        WHERE bd.state_id = %s
            AND f.is_onboarded = TRUE
            {voltage_clause}
    """
    
    params = [state_id] + ([voltage_level] if voltage_level else [])
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        result = cursor.fetchone()
        
        if result:
            return {
                'feeder_count': int(result[0] or 0),
                'customer_population': int(result[1] or 0)
            }
    
    return {'feeder_count': 0, 'customer_population': 0}


def calculate_state_metrics_optimized(state, from_date, to_date, mode, voltage_level=None):
    """
    Calculate state metrics using optimized SQL queries.
    All calculations use network-wide averaging (ALL ONBOARDED feeders in state × Days).
    Falls back to ORM if SQL queries fail.
    
    UPDATED: 
    - Only considers ONBOARDED feeders
    - Uses actual elapsed time for current periods (fractional days)
    - Uses timezone-aware datetime ranges for consistency
    
    CORRECTED: Interruption duration includes all active interruptions, 
    but FTC only counts interruptions that occurred within the period.
    
    Optionally scoped to feeders of a specific voltage_level ('11kv' or '33kv').
    """
    try:
        # 1. Average Supply Hours (per day) - ONBOARDED feeders only
        avg_supply = calculate_state_hours_of_supply_sql(
            state.id, 
            from_date, 
            to_date,
            voltage_level=voltage_level
        )
    except Exception as e:
        print(f"DEBUG: SQL failed for supply hours, using ORM fallback: {str(e)}")
        # Fallback to ORM
        feeder_ids = list(Feeder.objects.filter(
            business_district__state=state,
            is_onboarded=True
        ).values_list('id', flat=True))
        avg_supply = calculate_avg_supply_orm(state.id, from_date, to_date, feeder_ids)
    
    try:
        # 2. Interruption Duration (ALL interruptions active during period) - ONBOARDED feeders only
        avg_duration, ftc_all = calculate_state_interruption_metrics_sql(
            state.id,
            from_date,
            to_date,
            voltage_level=voltage_level
        )
    except Exception as e:
        print(f"DEBUG: SQL failed for interruption metrics, using ORM fallback: {str(e)}")
        # Fallback to ORM
        feeder_ids = list(Feeder.objects.filter(
            business_district__state=state,
            is_onboarded=True
        ).values_list('id', flat=True))
        avg_duration, ftc_all = calculate_interruption_metrics_orm(
            state.id, from_date, to_date, feeder_ids
        )
    
    try:
        # 3. Turnaround Time (LOCAL faults only, active during period) - ONBOARDED feeders only
        turnaround, ftc_local = calculate_state_interruption_metrics_sql(
            state.id,
            from_date,
            to_date,
            exclude_types=TURNAROUND_EXCLUSIONS,
            voltage_level=voltage_level
        )
    except Exception as e:
        print(f"DEBUG: SQL failed for turnaround time, using ORM fallback: {str(e)}")
        # Fallback to ORM
        feeder_ids = list(Feeder.objects.filter(
            business_district__state=state,
            is_onboarded=True
        ).values_list('id', flat=True))
        turnaround, ftc_local = calculate_interruption_metrics_orm(
            state.id, from_date, to_date, feeder_ids, exclude_types=TURNAROUND_EXCLUSIONS
        )
    
    try:
        # 4. Average Interruption Duration (hours per interruption event) - ONBOARDED feeders only
        avg_int_duration = calculate_state_avg_interruption_duration_sql(
            state.id,
            from_date,
            to_date,
            voltage_level=voltage_level
        )
    except Exception as e:
        print(f"DEBUG: SQL failed for avg interruption duration, using ORM fallback: {str(e)}")
        # Fallback to ORM
        feeder_ids = list(Feeder.objects.filter(
            business_district__state=state,
            is_onboarded=True
        ).values_list('id', flat=True))
        avg_int_duration = calculate_avg_interruption_duration_orm(
            state.id, from_date, to_date, feeder_ids
        )
    
    try:
        # 5. Peak Load (maximum load in the period) - ONBOARDED feeders only
        peak_load = calculate_state_peak_load_sql(
            state.id,
            from_date,
            to_date,
            voltage_level=voltage_level
        )
    except Exception as e:
        print(f"DEBUG: SQL failed for peak load, using ORM fallback: {str(e)}")
        # Fallback to ORM
        feeder_ids = list(Feeder.objects.filter(
            business_district__state=state,
            is_onboarded=True
        ).values_list('id', flat=True))
        peak_data = HourlyLoad.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(from_date, to_date)
        ).aggregate(peak=Max("load_mw"))
        peak_load = round(float(peak_data["peak"] or 0), 2)
    
    try:
        # 6. Infrastructure counts - ONBOARDED feeders only
        infrastructure = get_state_infrastructure_counts_sql(state.id, voltage_level=voltage_level)
    except Exception as e:
        print(f"DEBUG: SQL failed for infrastructure counts, using ORM fallback: {str(e)}")
        # Fallback to ORM
        feeders = Feeder.objects.filter(
            business_district__state=state,
            is_onboarded=True
        )
        if voltage_level:
            feeders = feeders.filter(voltage_level=voltage_level)
        feeder_count = feeders.count()
        customer_count = Customer.objects.filter(
            transformer__feeder__business_district__state=state,
            transformer__feeder__is_onboarded=True
        ).count()
        infrastructure = {
            'feeder_count': feeder_count,
            'customer_population': customer_count
        }
    
    # Validation
    if avg_supply > 24:
        avg_supply = 24.0

    avg_duration = round(24.0 - avg_supply, 2)  # always sums to 24 with supply

    if turnaround > 24:
        turnaround = 24.0

    return {
        "avg_supply": avg_supply,
        "avg_duration": avg_duration,
        "turnaround": turnaround,
        "avg_interruption_duration": avg_int_duration,
        "ftc": ftc_all,  # Total interruption count (occurred in period)
        "feeder_count": infrastructure['feeder_count'],
        "peak_load": peak_load,
        "customer_population": infrastructure['customer_population'],
        "_source": f"optimized_sql_{mode}"
    }


@api_view(["GET"])
def all_states_technical_summary(request):
    """
    Optimized technical summary for all states supporting multiple modes.
    
    UPDATED: 
    - Only considers ONBOARDED feeders for all calculations
    - Uses actual elapsed time for current periods (fractional days)
    - Uses timezone-aware datetime ranges for consistency
    - Includes hybrid energy calculation (EnergyDelivered + HourlyLoad fallback)
    - Returns ALL states, even those with no onboarded feeders (metrics will be 0)
    
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
    
    Examples:
    - ?mode=monthly&year=2024&month=8
    - ?mode=yearly&year=2024
    - ?mode=daily&from_date=2024-08-02T23:00:00.000Z
    - ?mode=weekly&from_date=2024-08-05T00:00:00.000Z&to_date=2024-08-11T23:59:59.999Z
    - ?mode=custom&from_date=2024-08-01T00:00:00.000Z&to_date=2024-08-15T23:59:59.999Z
    
    Key Metrics (CORRECTED - ONBOARDED FEEDERS ONLY):
    - avg_supply: Average hours per day of electricity supply across ALL ONBOARDED feeders (0-24)
    - avg_duration: Average hours per day of interruptions across ALL ONBOARDED feeders (0-24)
      * Includes ALL interruptions active during the period
      * Calculates only hours that fall within the period
      * Uses fractional days for current periods
    - turnaround: Average hours per day of local faults across ALL ONBOARDED feeders (0-24)
      * Includes ALL local faults active during the period
      * Calculates only hours that fall within the period
      * Uses fractional days for current periods
    - avg_interruption_duration: Average hours per interruption event (not per day)
      * Includes interruptions that occurred in period AND ongoing ones from before
      * Formula: Total duration of all interruptions / Count of interruptions
    - ftc: Feeder Tripping Count - total number of interruptions that OCCURRED in period (ONBOARDED feeders only)
    - peak_load: Maximum load (MW) recorded in the period (ONBOARDED feeders only)
    - feeder_count: Number of ONBOARDED feeders in the state
    - customer_population: Total number of customers on ONBOARDED feeders in the state
    """
    try:
        from_date, to_date, mode = get_date_range_from_request(request)
    except ValueError as e:
        return Response({"error": str(e)}, status=400)
    
    # ✅ Parse voltage level filter
    feeder_type_param = request.GET.get("feeder_type", "")
    voltage_level = feeder_type_param if feeder_type_param in ("11kv", "33kv") else None
    
    all_states = State.objects.all().order_by('name')
    
    overview = []
    
    for state in all_states:
        try:
            state_metrics = calculate_state_metrics_optimized(
                state, 
                from_date, 
                to_date, 
                mode,
                voltage_level=voltage_level
            )
            
            # Include ALL states, even if metrics are zero
            overview.append({
                "state": state.name,
                "metrics": state_metrics
            })
                
        except Exception as e:
            print(f"ERROR: Error calculating metrics for state {state.name}: {str(e)}")
            import traceback
            traceback.print_exc()
            
            # Include state with zero metrics on error
            overview.append({
                "state": state.name,
                "metrics": {
                    "avg_supply": 0.0,
                    "avg_duration": 0.0,
                    "turnaround": 0.0,
                    "avg_interruption_duration": 0.0,
                    "ftc": 0,
                    "feeder_count": 0,
                    "peak_load": 0.0,
                    "customer_population": 0,
                    "_source": "error_fallback",
                    "_error": str(e)
                }
            })
    
    # Create mode-specific metadata
    metadata = {
        "mode": mode,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "period_days": (to_date - from_date).days + 1,
        "total_states": len(overview),
        "states_with_onboarded_feeders": sum(1 for s in overview if s["metrics"]["feeder_count"] > 0),
        "onboarded_feeders_only": True  # Indicator that only onboarded feeders are counted
    }
    
    # Add mode-specific metadata
    if mode == "yearly":
        metadata["year"] = from_date.year
    elif mode == "monthly":
        metadata["year"] = from_date.year
        metadata["month"] = from_date.month
    
    response_data = {
        "overview": overview,
        "_metadata": metadata
    }
    
    return Response(response_data)


# Utility functions for backward compatibility or alternative calculations

def calculate_avg_supply_orm(state_id, from_date, to_date, feeder_ids):
    """
    Calculate average supply using Django ORM (slower but no raw SQL).
    Use this as fallback if raw SQL has issues.
    
    UPDATED: Uses network-wide averaging with ONBOARDED feeders only.
    Uses actual elapsed time for current periods.
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
    
    total_feeders = len(feeder_ids)
    
    if total_feeders == 0:
        return 0.0
    
    # Count total hours supplied (feeder_ids already filtered for onboarded)
    total_hours = HourlyLoad.objects.filter(
        date__range=(from_date, to_date), 
        load_mw__gt=0, 
        feeder_id__in=feeder_ids
    ).values('feeder_id', 'date', 'hour').distinct().count()
    
    # Average = Total hours / (Total onboarded feeders × Days)
    avg_hours_per_day = total_hours / (total_feeders * period_days)
    
    return round(min(avg_hours_per_day, 24.0), 2)


def calculate_avg_interruption_duration_orm(state_id, from_date, to_date, feeder_ids):
    """
    Calculate average interruption duration using Django ORM (slower but no raw SQL).
    Use this as fallback if raw SQL has issues.
    
    INCLUDES:
    1. Interruptions that OCCURRED within the period (resolved or ongoing)
    2. Interruptions that started BEFORE the period but are still ongoing (not resolved)
    
    CORRECTED: Only counts the hours that fall WITHIN the filtered period.
    
    feeder_ids already filtered for onboarded feeders.
    
    Formula: SUM(clipped interruption durations) / COUNT(interruptions)
    Result: Average hours per interruption event (not per day)
    """
    if not feeder_ids:
        return 0.0
    
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
    
    # Get interruptions (occurred in period OR ongoing from before)
    interruptions = FeederInterruption.objects.filter(
        feeder_id__in=feeder_ids
    ).filter(
        Q(occurred_at__date__range=(from_date, to_date)) |
        Q(occurred_at__lt=start_of_period, restored_at__isnull=True)
    )
    
    interruption_count = interruptions.count()
    
    if interruption_count == 0:
        return 0.0
    
    # Calculate total duration (clipped to period boundaries)
    total_hours = 0
    
    for interruption in interruptions:
        # Clip start time to period start
        start_time = max(interruption.occurred_at, start_of_period)
        
        # Clip end time to period end (or NOW if ongoing)
        if interruption.restored_at:
            end_time = min(interruption.restored_at, end_of_period)
        else:
            end_time = min(now, end_of_period)
        
        # Only count if end > start (interruption overlaps with period)
        if end_time > start_time:
            duration = (end_time - start_time).total_seconds() / 3600
            total_hours += duration
    
    # Calculate average
    avg_duration = total_hours / interruption_count
    
    return round(avg_duration, 2)


def calculate_interruption_metrics_orm(state_id, from_date, to_date, feeder_ids, exclude_types=None):
    """
    Calculate interruption metrics using Django ORM (slower but no raw SQL).
    Use this as fallback if raw SQL has issues.
    
    UPDATED:
    - Duration includes all active interruptions, calculates only hours within period
    - Uses actual elapsed time for current periods
    - Uses timezone-aware datetime ranges
    - Count only includes interruptions that occurred within period
    - feeder_ids already filtered for onboarded feeders
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
    
    total_feeders = len(feeder_ids)
    
    if total_feeders == 0:
        return 0.0, 0
    
    # ✅ Create timezone-aware datetime boundaries
    start_of_period = timezone.make_aware(
        datetime.combine(from_date, datetime.min.time())
    )
    end_of_period = timezone.make_aware(
        datetime.combine(to_date, datetime.max.time())
    )
    
    # Build query for interruptions active during period (feeder_ids already filtered for onboarded)
    duration_query = FeederInterruption.objects.filter(
        feeder_id__in=feeder_ids
    ).filter(
        Q(occurred_at__gte=start_of_period, occurred_at__lte=end_of_period) |
        Q(occurred_at__lt=start_of_period, restored_at__gte=start_of_period) |
        Q(occurred_at__lt=start_of_period, restored_at__isnull=True)
    )
    
    # Build query for count (only occurred in period) - ✅ timezone-aware
    count_query = FeederInterruption.objects.filter(
        feeder_id__in=feeder_ids,
        occurred_at__gte=start_of_period,
        occurred_at__lte=end_of_period
    )
    
    # Exclude types if specified
    if exclude_types:
        duration_query = duration_query.exclude(interruption_type__in=exclude_types)
        count_query = count_query.exclude(interruption_type__in=exclude_types)
    
    # Get interruptions for duration calculation
    interruptions = duration_query.select_related('feeder')
    
    # Calculate total hours across ALL interruptions (only hours within period)
    total_hours = 0
    
    for interruption in interruptions:
        # Calculate duration only within period boundaries
        start_time = max(interruption.occurred_at, start_of_period)
        end_time = min(
            interruption.restored_at if interruption.restored_at else end_of_period,
            end_of_period
        )
        
        if end_time > start_time:
            duration = (end_time - start_time).total_seconds() / 3600
            total_hours += duration
    
    # Get count
    total_count = count_query.count()
    
    # Average = Total hours / (Total onboarded feeders × Days)
    avg_hours_per_day = total_hours / (total_feeders * period_days)
    
    return round(min(avg_hours_per_day, 24.0), 2), total_count