# technical/views/districts/all_districts.py
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Sum, Avg, Max
from django.db import connection
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from common.models import Feeder, BusinessDistrict
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


def get_date_range_and_mode(request):
    """Enhanced date range parsing with support for multiple modes"""
    mode = request.GET.get("mode", "monthly")
    
    if mode in ["daily", "weekly", "custom", "range"]:
        try:
            from_date_str = request.GET.get("from_date")
            to_date_str = request.GET.get("to_date")
            
            if not from_date_str or not to_date_str:
                raise ValueError("from_date and to_date are required for this mode")
            
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

def calculate_district_hours_of_supply_sql(district_id, from_date, to_date):
    """
    Calculate average hours of supply per day for a district using raw SQL.
    
    CORRECTED Logic:
    - Numerator: Total hours supplied across all feeders in district
    - Denominator: Total feeders in district × Days
    """
    period_days = (to_date - from_date).days + 1
    
    # Get total feeders in district
    feeder_count_query = """
        SELECT COUNT(DISTINCT f.id)
        FROM common_feeder f
        WHERE f.business_district_id = %s
    """
    
    # Get total hours supplied
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
    
    return round(min(avg_hours_per_day, 24.0), 2)


def calculate_district_interruption_metrics_sql(district_id, from_date, to_date, exclude_types=None):
    """
    Calculate average interruption duration per day for a district using raw SQL.
    
    CORRECTED Logic:
    - Numerator: Total interruption hours across all feeders in district
    - Denominator: Total feeders in district × Days
    
    Returns:
        tuple: (avg_duration_per_day, total_interruption_count)
    """
    period_days = (to_date - from_date).days + 1
    
    end_of_period = timezone.make_aware(
        datetime.combine(to_date, datetime.max.time())
    )
    
    # Get total feeders in district
    feeder_count_query = """
        SELECT COUNT(DISTINCT f.id)
        FROM common_feeder f
        WHERE f.business_district_id = %s
    """
    
    # Build exclusion clause
    exclusion_clause = ""
    params = [end_of_period, end_of_period, district_id, from_date, to_date]
    
    if exclude_types:
        placeholders = ','.join(['%s'] * len(exclude_types))
        exclusion_clause = f"AND fi.interruption_type NOT IN ({placeholders})"
        params.extend(exclude_types)
    
    # Calculate total hours
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
        
        cursor.execute(interruption_query, params)
        result = cursor.fetchone()
        
        total_hours = result[0] if result and result[0] else 0
        total_interruptions = result[1] if result and result[1] else 0
    
    # Average = Total hours / (Total feeders × Days)
    avg_hours_per_day = total_hours / (total_feeders * period_days)
    
    return round(avg_hours_per_day, 2), int(total_interruptions)


def calculate_district_energy_sql(district_id, from_date, to_date):
    """Calculate total energy delivered for a district from HourlyLoad"""
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


def calculate_district_peak_load_sql(district_id, from_date, to_date):
    """Get peak load for a district"""
    query = """
        SELECT 
            MAX(hl.load_mw) as peak_load
        FROM technical_hourlyload hl
        INNER JOIN common_feeder f ON hl.feeder_id = f.id
        WHERE f.business_district_id = %s
            AND hl.date BETWEEN %s AND %s
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [district_id, from_date, to_date])
        result = cursor.fetchone()
        peak_load = result[0] if result and result[0] else 0
    
    return round(float(peak_load), 2)


def get_district_infrastructure_counts_sql(district_id):
    """Get feeder count and customer population for a district"""
    query = """
        SELECT 
            COUNT(DISTINCT f.id) as feeder_count,
            COUNT(DISTINCT c.id) as customer_count
        FROM common_feeder f
        LEFT JOIN common_distributiontransformer dt ON dt.feeder_id = f.id
        LEFT JOIN commercial_customer c ON c.transformer_id = dt.id
        WHERE f.business_district_id = %s
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
    """Calculate all metrics for a district using optimized SQL"""
    period_days = (to_date - from_date).days + 1
    
    try:
        # 1. Average Supply Hours
        avg_supply = calculate_district_hours_of_supply_sql(
            district.id, from_date, to_date
        )
    except Exception as e:
        print(f"Error calculating supply hours for {district.name}: {e}")
        avg_supply = 0.0
    
    try:
        # 2. Interruption Duration (all types)
        avg_duration, ftc = calculate_district_interruption_metrics_sql(
            district.id, from_date, to_date
        )
    except Exception as e:
        print(f"Error calculating interruption metrics for {district.name}: {e}")
        avg_duration, ftc = 0.0, 0
    
    try:
        # 3. Turnaround Time (exclude L/S and TCN)
        turnaround_time, _ = calculate_district_interruption_metrics_sql(
            district.id, from_date, to_date, exclude_types=TURNAROUND_EXCLUSIONS
        )
    except Exception as e:
        print(f"Error calculating turnaround time for {district.name}: {e}")
        turnaround_time = 0.0
    
    try:
        # 4. Peak Load
        peak_load = calculate_district_peak_load_sql(
            district.id, from_date, to_date
        )
    except Exception as e:
        print(f"Error calculating peak load for {district.name}: {e}")
        peak_load = 0.0
    
    try:
        # 5. Infrastructure counts
        infrastructure = get_district_infrastructure_counts_sql(district.id)
    except Exception as e:
        print(f"Error getting infrastructure counts for {district.name}: {e}")
        infrastructure = {'feeder_count': 0, 'customer_population': 0}
    
    try:
        # 6. Energy delivered
        energy_delivered = calculate_district_energy_sql(
            district.id, from_date, to_date
        )
    except Exception as e:
        print(f"Error calculating energy for {district.name}: {e}")
        energy_delivered = 0.0
    
    # Calculate daily interruptions
    feeder_count = infrastructure['feeder_count']
    if feeder_count > 0 and period_days > 0:
        daily_interruptions = ftc / (feeder_count * period_days)
    else:
        daily_interruptions = 0.0
    
    return {
        "avg_supply": avg_supply,
        "duration": avg_duration,
        "turnaround_time": turnaround_time,
        "ftc": ftc,
        "daily_interruptions": round(daily_interruptions, 2),
        "feeder_count": feeder_count,
        "peak_load": peak_load,
        "customer_population": infrastructure['customer_population'],
        "energy_delivered": energy_delivered,
        "_source": "optimized_sql"
    }


@api_view(["GET"])
def all_business_districts_technical_summary(request):
    """
    Technical summary for all business districts in a state.
    
    Query Parameters:
    - state: State name (required)
    - mode: monthly, yearly, daily, weekly, custom, range
    - For monthly: year, month
    - For yearly: year
    - For others: from_date, to_date (ISO format)
    
    Key Metrics (CORRECTED):
    - avg_supply: Average hours per day across all feeders in district (0-24)
    - duration: Average interruption hours per day across all feeders (0-24)
    - turnaround_time: Average local fault hours per day across all feeders (0-24)
    - daily_interruptions: Average interruptions per feeder per day
    - ftc: Total interruption count
    - energy_delivered: Total energy in MWh
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
    
    # Get all business districts in the state that have feeders
    districts = BusinessDistrict.objects.filter(
        state__name__iexact=state,
        feeders__isnull=False
    ).distinct().order_by('name')
    
    print(f"DEBUG: Found {districts.count()} districts with feeders in {state}")
    
    response_data = []
    
    for district in districts:
        print(f"DEBUG: Processing district: {district.name}")
        try:
            # Calculate metrics using SQL
            district_metrics = calculate_district_metrics(district, from_date, to_date)
            
            if district_metrics and district_metrics['feeder_count'] > 0:
                # Add FTC per feeder
                ftc_per_feeder = round(
                    district_metrics["ftc"] / district_metrics["feeder_count"], 2
                )
                district_metrics["ftc_per_feeder"] = ftc_per_feeder
                
                response_data.append({
                    "district": district.name,
                    "metrics": district_metrics
                })
                print(f"DEBUG: Added {district.name} to response")
            else:
                print(f"DEBUG: No metrics for {district.name}")
                
        except Exception as e:
            print(f"ERROR: Error for district {district.name}: {str(e)}")
            import traceback
            traceback.print_exc()
            continue
    
    final_response = {
        "districts": response_data
    }
    
    print(f"DEBUG: Final response has {len(response_data)} districts")
    
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