# technical/views/states/all_states.py
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Sum, Avg, Max, Q
from django.db import connection
from datetime import datetime, timedelta
from common.models import State, Feeder
from technical.models import HourlyLoad, FeederInterruption
from commercial.models import Customer
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from technical.constants import TURNAROUND_EXCLUSIONS

def get_date_range_from_request(request):
    """Enhanced date range parsing with support for multiple modes"""
    mode = request.GET.get("mode", "monthly")
    
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


def calculate_state_hours_of_supply_sql(state_id, from_date, to_date):
    """
    Calculate average hours of supply per day for a state using raw SQL.
    
    CORRECTED Logic:
    - Numerator: Sum of all hours supplied across all feeders with data in the state
    - Denominator: Total feeders in state × Days in period
    - This properly accounts for feeders with no data (they contribute 0)
    """
    period_days = (to_date - from_date).days + 1
    
    # Get total feeders in state
    feeder_count_query = """
        SELECT COUNT(DISTINCT f.id)
        FROM common_feeder f
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
    """
    
    # Get total hours supplied across all feeders
    hours_query = """
        SELECT 
            COUNT(DISTINCT CONCAT(hl.feeder_id, '-', hl.date, '-', hl.hour)) as total_hours
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
            AND hl.date BETWEEN %s AND %s
            AND hl.load_mw > 0
    """
    
    with connection.cursor() as cursor:
        # Get feeder count
        cursor.execute(feeder_count_query, [state_id])
        result = cursor.fetchone()
        total_feeders = result[0] if result and result[0] else 0
        
        if total_feeders == 0:
            return 0.0
        
        # Get total hours
        cursor.execute(hours_query, [state_id, from_date, to_date])
        result = cursor.fetchone()
        total_hours = result[0] if result and result[0] else 0
    
    # Average = Total hours / (Total feeders × Days)
    avg_hours_per_day = total_hours / (total_feeders * period_days)
    
    return round(min(avg_hours_per_day, 24.0), 2)


def calculate_state_interruption_metrics_sql(state_id, from_date, to_date, exclude_types=None):
    """
    Calculate average interruption duration per day for a state using raw SQL.
    
    CORRECTED Logic:
    - Numerator: Sum of all interruption hours across all feeders with interruptions
    - Denominator: Total feeders in state × Days in period
    - This properly accounts for feeders with no interruptions (they contribute 0)
    
    Returns:
        tuple: (avg_duration_per_day, total_interruption_count)
    """
    period_days = (to_date - from_date).days + 1
    
    end_of_period = timezone.make_aware(
        datetime.combine(to_date, datetime.max.time())
    )
    
    # Get total feeders in state
    feeder_count_query = """
        SELECT COUNT(DISTINCT f.id)
        FROM common_feeder f
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
    """
    
    # Build exclusion clause for turnaround time
    exclusion_clause = ""
    # FIXED: Correct parameter order - end_of_period twice, state_id, dates
    params = [end_of_period, end_of_period, state_id, from_date, to_date]
    
    if exclude_types:
        placeholders = ','.join(['%s'] * len(exclude_types))
        exclusion_clause = f"AND fi.interruption_type NOT IN ({placeholders})"
        params.extend(exclude_types)
    
    # Calculate total hours across ALL feeders
    interruption_query = f"""
        SELECT 
            COALESCE(SUM(
                CASE 
                    WHEN restored_at IS NOT NULL AND restored_at <= %s THEN
                        EXTRACT(EPOCH FROM (restored_at - occurred_at)) / 3600.0
                    ELSE
                        EXTRACT(EPOCH FROM (%s - occurred_at)) / 3600.0
                END
            ), 0) as total_hours,
            COUNT(*) as total_interruptions
        FROM technical_feederinterruption fi
        INNER JOIN common_feeder f ON fi.feeder_id = f.id
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
            AND DATE(fi.occurred_at) BETWEEN %s AND %s
            {exclusion_clause}
    """
    
    with connection.cursor() as cursor:
        # Get feeder count
        cursor.execute(feeder_count_query, [state_id])
        result = cursor.fetchone()
        total_feeders = result[0] if result and result[0] else 0
        
        if total_feeders == 0:
            return 0.0, 0
        
        # Get interruption metrics
        cursor.execute(interruption_query, params)
        result = cursor.fetchone()
        
        total_hours = result[0] if result and result[0] else 0
        total_interruptions = result[1] if result and result[1] else 0
    
    # Average = Total hours / (Total feeders × Days)
    avg_hours_per_day = total_hours / (total_feeders * period_days)
    
    return round(avg_hours_per_day, 2), int(total_interruptions)


def calculate_state_peak_load_sql(state_id, from_date, to_date):
    """
    Get the peak load within the date range for a state using raw SQL.
    """
    query = """
        SELECT 
            MAX(hl.load_mw) as peak_load
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        WHERE bd.state_id = %s
            AND hl.date BETWEEN %s AND %s
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [state_id, from_date, to_date])
        result = cursor.fetchone()
        peak_load = result[0] if result and result[0] else 0
    
    return round(float(peak_load), 2)


def get_state_infrastructure_counts_sql(state_id):
    """
    Get feeder count and customer population for a state using raw SQL.
    """
    query = """
        SELECT 
            COUNT(DISTINCT f.id) as feeder_count,
            COUNT(DISTINCT c.id) as customer_count
        FROM common_feeder f
        INNER JOIN common_businessdistrict bd ON f.business_district_id = bd.id
        LEFT JOIN common_distributiontransformer dt ON dt.feeder_id = f.id
        LEFT JOIN commercial_customer c ON c.transformer_id = dt.id
        WHERE bd.state_id = %s
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [state_id])
        result = cursor.fetchone()
        
        if result:
            return {
                'feeder_count': int(result[0] or 0),
                'customer_population': int(result[1] or 0)
            }
    
    return {'feeder_count': 0, 'customer_population': 0}


def calculate_state_metrics_optimized(state, from_date, to_date, mode):
    """
    Calculate state metrics using optimized SQL queries.
    All calculations use network-wide averaging (ALL feeders in state × Days).
    Falls back to ORM if SQL queries fail.
    """
    try:
        # 1. Average Supply Hours (per day) - CORRECTED
        avg_supply = calculate_state_hours_of_supply_sql(
            state.id, 
            from_date, 
            to_date
        )
    except Exception as e:
        print(f"DEBUG: SQL failed for supply hours, using ORM fallback: {str(e)}")
        # Fallback to ORM
        feeder_ids = list(Feeder.objects.filter(
            business_district__state=state
        ).values_list('id', flat=True))
        avg_supply = calculate_avg_supply_orm(state.id, from_date, to_date, feeder_ids)
    
    try:
        # 2. Interruption Duration (ALL interruptions) - CORRECTED
        avg_duration, ftc_all = calculate_state_interruption_metrics_sql(
            state.id,
            from_date,
            to_date
        )
    except Exception as e:
        print(f"DEBUG: SQL failed for interruption metrics, using ORM fallback: {str(e)}")
        # Fallback to ORM
        feeder_ids = list(Feeder.objects.filter(
            business_district__state=state
        ).values_list('id', flat=True))
        avg_duration, ftc_all = calculate_interruption_metrics_orm(
            state.id, from_date, to_date, feeder_ids
        )
    
    try:
        # 3. Turnaround Time (LOCAL faults only) - CORRECTED
        turnaround, ftc_local = calculate_state_interruption_metrics_sql(
            state.id,
            from_date,
            to_date,
            exclude_types=TURNAROUND_EXCLUSIONS
        )
    except Exception as e:
        print(f"DEBUG: SQL failed for turnaround time, using ORM fallback: {str(e)}")
        # Fallback to ORM
        feeder_ids = list(Feeder.objects.filter(
            business_district__state=state
        ).values_list('id', flat=True))
        turnaround, ftc_local = calculate_interruption_metrics_orm(
            state.id, from_date, to_date, feeder_ids, exclude_types=TURNAROUND_EXCLUSIONS
        )
    
    try:
        # 4. Peak Load (maximum load in the period)
        peak_load = calculate_state_peak_load_sql(
            state.id,
            from_date,
            to_date
        )
    except Exception as e:
        print(f"DEBUG: SQL failed for peak load, using ORM fallback: {str(e)}")
        # Fallback to ORM
        feeder_ids = list(Feeder.objects.filter(
            business_district__state=state
        ).values_list('id', flat=True))
        peak_data = HourlyLoad.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(from_date, to_date)
        ).aggregate(peak=Max("load_mw"))
        peak_load = round(float(peak_data["peak"] or 0), 2)
    
    try:
        # 5. Infrastructure counts
        infrastructure = get_state_infrastructure_counts_sql(state.id)
    except Exception as e:
        print(f"DEBUG: SQL failed for infrastructure counts, using ORM fallback: {str(e)}")
        # Fallback to ORM
        feeders = Feeder.objects.filter(business_district__state=state)
        feeder_count = feeders.count()
        customer_count = Customer.objects.filter(
            transformer__feeder__business_district__state=state
        ).count()
        infrastructure = {
            'feeder_count': feeder_count,
            'customer_population': customer_count
        }
    
    # Validation
    if avg_supply > 24:
        avg_supply = 24.0
    
    if avg_duration > 24:
        avg_duration = 24.0
    
    if turnaround > 24:
        turnaround = 24.0
    
    return {
        "avg_supply": avg_supply,
        "avg_duration": avg_duration,
        "turnaround": turnaround,
        "ftc": ftc_all,  # Total interruption count
        "feeder_count": infrastructure['feeder_count'],
        "peak_load": peak_load,
        "customer_population": infrastructure['customer_population'],
        "_source": f"optimized_sql_{mode}"
    }


@api_view(["GET"])
def all_states_technical_summary(request):
    """
    Optimized technical summary for all states supporting multiple modes.
    
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
    
    Key Metrics (CORRECTED):
    - avg_supply: Average hours per day of electricity supply across ALL feeders (0-24)
    - avg_duration: Average hours per day of interruptions across ALL feeders (0-24)
    - turnaround: Average hours per day of local faults across ALL feeders (0-24)
    - ftc: Feeder Tripping Count - total number of interruptions in period
    - peak_load: Maximum load (MW) recorded in the period
    - feeder_count: Number of active feeders in the state
    - customer_population: Total number of customers in the state
    """
    try:
        from_date, to_date, mode = get_date_range_from_request(request)
    except ValueError as e:
        return Response({"error": str(e)}, status=400)
    
    # Get all states with feeders using Django ORM (more reliable)
    states_with_feeders = State.objects.filter(
        districts__feeders__isnull=False
    ).distinct().order_by('name')
    
    overview = []
    
    for state in states_with_feeders:
        try:
            # Calculate metrics using optimized SQL
            state_metrics = calculate_state_metrics_optimized(
                state, 
                from_date, 
                to_date, 
                mode
            )
            
            # Include state even if metrics are zero (for consistency with old behavior)
            if state_metrics:
                overview.append({
                    "state": state.name,
                    "metrics": state_metrics
                })
                
        except Exception as e:
            print(f"ERROR: Error calculating metrics for state {state.name}: {str(e)}")
            import traceback
            traceback.print_exc()
            continue
    
    # Create mode-specific metadata
    metadata = {
        "mode": mode,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "period_days": (to_date - from_date).days + 1,
        "total_states": len(overview),
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
    
    CORRECTED: Uses network-wide averaging.
    """
    period_days = (to_date - from_date).days + 1
    total_feeders = len(feeder_ids)
    
    if total_feeders == 0:
        return 0.0
    
    # Count total hours supplied
    total_hours = HourlyLoad.objects.filter(
        date__range=(from_date, to_date), 
        load_mw__gt=0, 
        feeder_id__in=feeder_ids
    ).values('feeder_id', 'date', 'hour').distinct().count()
    
    # Average = Total hours / (Total feeders × Days)
    avg_hours_per_day = total_hours / (total_feeders * period_days)
    
    return round(min(avg_hours_per_day, 24.0), 2)


def calculate_interruption_metrics_orm(state_id, from_date, to_date, feeder_ids, exclude_types=None):
    """
    Calculate interruption metrics using Django ORM (slower but no raw SQL).
    Use this as fallback if raw SQL has issues.
    
    CORRECTED: Uses network-wide averaging.
    """
    period_days = (to_date - from_date).days + 1
    total_feeders = len(feeder_ids)
    
    if total_feeders == 0:
        return 0.0, 0
    
    end_of_period = timezone.make_aware(
        datetime.combine(to_date, datetime.max.time())
    )
    
    # Build query
    query = FeederInterruption.objects.filter(
        feeder_id__in=feeder_ids,
        occurred_at__date__range=(from_date, to_date)
    )
    
    # Exclude types if specified
    if exclude_types:
        query = query.exclude(interruption_type__in=exclude_types)
    
    # Get interruptions
    interruptions = query.select_related('feeder')
    
    # Calculate total hours across ALL interruptions
    total_hours = 0
    total_count = 0
    
    for interruption in interruptions:
        # Calculate duration
        if interruption.restored_at and interruption.restored_at <= end_of_period:
            duration = (interruption.restored_at - interruption.occurred_at).total_seconds() / 3600
        else:
            duration = (end_of_period - interruption.occurred_at).total_seconds() / 3600
        
        total_hours += duration
        total_count += 1
    
    # Average = Total hours / (Total feeders × Days)
    avg_hours_per_day = total_hours / (total_feeders * period_days)
    
    return round(avg_hours_per_day, 2), total_count