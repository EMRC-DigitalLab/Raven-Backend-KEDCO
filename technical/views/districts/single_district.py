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
    """Calculate average hours of supply per day for a district"""
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
        
        if total_hours == 0:
            return 0.0
    
    avg_hours_per_day = total_hours / (total_feeders * period_days)
    return round(min(avg_hours_per_day, 24.0), 2)


def calculate_district_interruption_metrics_sql(district_id, from_date, to_date, exclude_types=None):
    """Calculate average interruption duration per day for a district"""
    period_days = (to_date - from_date).days + 1
    
    end_of_period = timezone.make_aware(
        datetime.combine(to_date, datetime.max.time())
    )
    
    feeder_count_query = """
        SELECT COUNT(DISTINCT f.id)
        FROM common_feeder f
        WHERE f.business_district_id = %s
    """
    
    exclusion_clause = ""
    params = [end_of_period, end_of_period, district_id, from_date, to_date]
    
    if exclude_types:
        placeholders = ','.join(['%s'] * len(exclude_types))
        exclusion_clause = f"AND fi.interruption_type NOT IN ({placeholders})"
        params.extend(exclude_types)
    
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


def get_previous_periods(start_date, period_days, count=4):
    """Get previous periods for historical comparison"""
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
    """Calculate all metrics for a single period"""
    period_days = (to_date - from_date).days + 1
    
    # 1. Supply hours
    avg_supply = calculate_district_hours_of_supply_sql(district_id, from_date, to_date)
    
    # 2. Interruption duration (all types)
    avg_duration, total_interruptions = calculate_district_interruption_metrics_sql(
        district_id, from_date, to_date
    )
    
    # 3. Turnaround time (exclude L/S and TCN)
    turnaround_time, _ = calculate_district_interruption_metrics_sql(
        district_id, from_date, to_date, exclude_types=TURNAROUND_EXCLUSIONS
    )
    
    # 4. Energy delivered
    total_energy = calculate_district_energy_sql(district_id, from_date, to_date)
    
    # 5. Feeder count
    feeder_count = Feeder.objects.filter(business_district_id=district_id).count()
    
    # 6. Daily interruptions
    if feeder_count > 0 and period_days > 0:
        daily_interruptions = total_interruptions / (feeder_count * period_days)
    else:
        daily_interruptions = 0.0
    
    return {
        "avg_supply": avg_supply,
        "duration": avg_duration,  # Frontend expects 'duration' not 'avg_duration'
        "turnaround_time": turnaround_time,
        "interruptions": round(daily_interruptions, 2),  # Frontend expects daily avg as 'interruptions'
        "faults": total_interruptions,  # Frontend expects total count as 'faults'
        "energy_delivered": total_energy,
        "feeder_count": feeder_count
    }


def build_metrics_with_history(district, start_date, end_date, period_days):
    """Build metrics response with historical data"""
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
        if prev_val and prev_val != 0:
            return round(((current_val - prev_val) / prev_val) * 100, 2)
        return None
    
    # Build response
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
    """Get top 5 and bottom 5 feeders by peak load"""
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
    bottom_5 = formatted[-5:] if len(formatted) >= 5 else []
    
    return top_5, bottom_5


@api_view(["GET"])
def business_district_technical_summary(request):
    """
    Technical summary for a specific business district.
    
    Query Parameters:
    - district: Business district name (required)
    - mode: monthly, yearly, daily, weekly, custom, range
    - For monthly: year, month
    - For yearly: year
    - For others: from_date, to_date (ISO format)
    
    Key Metrics (CORRECTED):
    - avg_supply: Average hours per day across all feeders in district (0-24)
    - avg_duration: Average interruption hours per day across all feeders (0-24)
    - turnaround_time: Average local fault hours per day across all feeders (0-24)
    - daily_interruptions: Average interruptions per feeder per day
    - interruptions: Total interruption count
    - energy_delivered: Total energy in MWh
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
        "bottom_feeders": bottom_feeders
    }
    
    return Response(response_data)