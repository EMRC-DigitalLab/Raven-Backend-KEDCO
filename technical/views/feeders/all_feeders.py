# technical/views/feeders/all_feeders.py
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db.models import Q, Avg, Count, Max
from django.db import connection
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from technical.serializers import FeederAvailabilitySerializer
from common.models import Feeder
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


def get_date_range_and_mode_from_request(request):
    """Enhanced date range parsing with support for multiple modes"""
    mode = request.GET.get("mode", "monthly")
    
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


def calculate_feeder_hours_of_supply_sql(feeder_id, from_date, to_date):
    """
    Calculate average hours of supply per day for a single feeder using raw SQL.
    Returns the average number of hours per day where load was supplied.
    """
    query = """
        SELECT 
            AVG(daily_hours) as avg_hours
        FROM (
            SELECT 
                date,
                COUNT(DISTINCT hour) as daily_hours
            FROM technical_hourlyload
            WHERE feeder_id = %s
                AND date BETWEEN %s AND %s
                AND load_mw > 0
            GROUP BY date
        ) daily_supply
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [feeder_id, from_date, to_date])
        result = cursor.fetchone()
        avg_hours = result[0] if result and result[0] else 0
    
    return round(min(float(avg_hours), 24.0), 2)


def calculate_feeder_interruption_metrics_sql(feeder_id, from_date, to_date, exclude_types=None):
    """
    Calculate average interruption duration per day for a single feeder using raw SQL.
    
    For a single feeder:
    - Sum all interruption hours in the period
    - Divide by number of days to get average hours per day
    
    Returns:
        tuple: (avg_duration_per_day, total_interruption_count)
    """
    end_of_period = timezone.make_aware(
        datetime.combine(to_date, datetime.max.time())
    )
    
    # Build exclusion clause
    exclusion_clause = ""
    params = [end_of_period, end_of_period, feeder_id, from_date, to_date]
    
    if exclude_types:
        placeholders = ','.join(['%s'] * len(exclude_types))
        exclusion_clause = f"AND interruption_type NOT IN ({placeholders})"
        params.extend(exclude_types)
    
    # Calculate total hours and count for this feeder
    query = f"""
        SELECT 
            SUM(
                CASE 
                    WHEN restored_at IS NOT NULL AND restored_at <= %s THEN
                        EXTRACT(EPOCH FROM (restored_at - occurred_at)) / 3600.0
                    ELSE
                        EXTRACT(EPOCH FROM (%s - occurred_at)) / 3600.0
                END
            ) as total_hours,
            COUNT(*) as interruption_count
        FROM technical_feederinterruption
        WHERE feeder_id = %s
            AND DATE(occurred_at) BETWEEN %s AND %s
            {exclusion_clause}
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        result = cursor.fetchone()
        
        total_hours = result[0] if result and result[0] else 0
        total_interruptions = result[1] if result and result[1] else 0
    
    # Calculate average per day
    period_days = (to_date - from_date).days + 1
    avg_hours_per_day = total_hours / period_days if period_days > 0 and total_hours else 0
    
    return round(avg_hours_per_day, 2), int(total_interruptions)


def calculate_feeder_metrics_optimized(feeder, from_date, to_date, mode):
    """
    Calculate feeder metrics using optimized SQL queries.
    
    For individual feeders, we calculate:
    - Average hours per day of supply
    - Average hours per day of interruptions
    - Average hours per day of local faults (turnaround)
    """
    try:
        # 1. Average Supply Hours (per day)
        avg_supply = calculate_feeder_hours_of_supply_sql(
            feeder.id, 
            from_date, 
            to_date
        )
    except Exception as e:
        print(f"DEBUG: SQL failed for supply hours on feeder {feeder.name}, using ORM: {str(e)}")
        # Fallback to ORM
        daily_supply = HourlyLoad.objects.filter(
            feeder_id=feeder.id,
            date__range=(from_date, to_date),
            load_mw__gt=0
        ).values('date').annotate(
            daily_hours=Count('hour', distinct=True)
        )
        
        if daily_supply.exists():
            avg_supply = daily_supply.aggregate(avg=Avg('daily_hours'))['avg'] or 0
            avg_supply = round(min(float(avg_supply), 24.0), 2)
        else:
            avg_supply = 0.0
    
    try:
        # 2. Interruption Duration (ALL interruptions, per day)
        avg_duration, ftc_all = calculate_feeder_interruption_metrics_sql(
            feeder.id,
            from_date,
            to_date
        )
    except Exception as e:
        print(f"DEBUG: SQL failed for interruption metrics on feeder {feeder.name}, using ORM: {str(e)}")
        # Fallback to ORM
        end_of_period = timezone.make_aware(
            datetime.combine(to_date, datetime.max.time())
        )
        
        interruptions = FeederInterruption.objects.filter(
            feeder_id=feeder.id,
            occurred_at__date__range=(from_date, to_date)
        )
        
        total_hours = 0
        ftc_all = interruptions.count()
        
        for interruption in interruptions:
            if interruption.restored_at and interruption.restored_at <= end_of_period:
                duration = (interruption.restored_at - interruption.occurred_at).total_seconds() / 3600
            else:
                duration = (end_of_period - interruption.occurred_at).total_seconds() / 3600
            total_hours += duration
        
        period_days = (to_date - from_date).days + 1
        avg_duration = round(total_hours / period_days, 2) if period_days > 0 else 0
    
    try:
        # 3. Turnaround Time (LOCAL faults only, per day)
        turnaround, ftc_local = calculate_feeder_interruption_metrics_sql(
            feeder.id,
            from_date,
            to_date,
            exclude_types=TURNAROUND_EXCLUSIONS
        )
    except Exception as e:
        print(f"DEBUG: SQL failed for turnaround time on feeder {feeder.name}, using ORM: {str(e)}")
        # Fallback to ORM
        end_of_period = timezone.make_aware(
            datetime.combine(to_date, datetime.max.time())
        )
        
        interruptions = FeederInterruption.objects.filter(
            feeder_id=feeder.id,
            occurred_at__date__range=(from_date, to_date)
        ).exclude(interruption_type__in=TURNAROUND_EXCLUSIONS)
        
        total_hours = 0
        ftc_local = interruptions.count()
        
        for interruption in interruptions:
            if interruption.restored_at and interruption.restored_at <= end_of_period:
                duration = (interruption.restored_at - interruption.occurred_at).total_seconds() / 3600
            else:
                duration = (end_of_period - interruption.occurred_at).total_seconds() / 3600
            total_hours += duration
        
        period_days = (to_date - from_date).days + 1
        turnaround = round(total_hours / period_days, 2) if period_days > 0 else 0
    
    # Validation
    if avg_supply > 24:
        avg_supply = 24.0
    
    if avg_duration > 24:
        avg_duration = 24.0
    
    if turnaround > 24:
        turnaround = 24.0
    
    return {
        "feeder_name": feeder.name,
        "feeder_slug": feeder.slug,
        "voltage_level": feeder.voltage_level,
        "avg_hours_of_supply": avg_supply,
        "duration_of_interruptions": avg_duration,
        "turnaround_time": turnaround,
        "ftc": ftc_all,  # Total interruption count
        "_source": f"optimized_sql_{mode}"
    }


def get_feeder_availability_summary_optimized(from_date, to_date, mode, state=None, business_district=None):
    """
    Optimized feeder availability summary using SQL queries.
    No caching, always calculates fresh data.
    """
    # Filter feeders based on location parameters
    feeders_query = Feeder.objects.select_related('business_district__state')
    
    if business_district:
        feeders_query = feeders_query.filter(business_district__name=business_district)
    elif state:
        feeders_query = feeders_query.filter(business_district__state__name=state)
    
    feeders = list(feeders_query)
    
    print(f"DEBUG: Processing {len(feeders)} feeders for mode: {mode}, dates: {from_date} to {to_date}")
    
    result = []
    
    for feeder in feeders:
        try:
            feeder_metrics = calculate_feeder_metrics_optimized(
                feeder, 
                from_date, 
                to_date, 
                mode
            )
            result.append(feeder_metrics)
        except Exception as e:
            print(f"ERROR: Error calculating metrics for feeder {feeder.name}: {str(e)}")
            import traceback
            traceback.print_exc()
            continue
    
    print(f"DEBUG: Successfully calculated metrics for {len(result)} feeders")
    return result


class FeederAvailabilityOverview(APIView):
    """
    Optimized feeder availability overview API supporting multiple modes.
    
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
    - state: State name for filtering (optional)
    - business_district: Business district name for filtering (optional)
    
    Examples:
    - ?mode=monthly&year=2024&month=8&state=Lagos
    - ?mode=yearly&year=2024&business_district=Ikeja
    - ?mode=daily&from_date=2024-08-02T23:00:00.000Z
    - ?mode=weekly&from_date=2024-08-05T00:00:00.000Z&to_date=2024-08-11T23:59:59.999Z&state=Lagos
    - ?mode=custom&from_date=2024-08-01T00:00:00.000Z&to_date=2024-08-15T23:59:59.999Z
    
    Legacy format still supported:
    - ?year=2024&month=8&state=Lagos (equivalent to monthly mode)
    - ?from_date=2024-08-01&to_date=2024-08-15&state=Lagos (equivalent to custom mode)
    
    Response Format:
    [
        {
            "feeder_name": "Feeder Name",
            "feeder_slug": "feeder-name",
            "voltage_level": "11kv",
            "avg_hours_of_supply": 18.5,        // Hours/day (0-24)
            "duration_of_interruptions": 3.2,   // Hours/day (0-24)
            "turnaround_time": 1.5,             // Hours/day (0-24)
            "ftc": 12                           // Total interruption count
        }
    ]
    
    Key Metrics:
    - avg_hours_of_supply: Average hours per day of electricity supply (0-24)
    - duration_of_interruptions: Average hours per day of ALL interruptions (0-24)
    - turnaround_time: Average hours per day of LOCAL faults only (0-24)
    - ftc: Feeder Tripping Count - total number of interruptions in period
    """

    def get(self, request):
        # Parse location filters
        state = request.GET.get("state")
        business_district = request.GET.get("business_district")
        
        # Check if this is a legacy request (old format)
        month = request.GET.get("month")
        year = request.GET.get("year")
        from_date_legacy = request.GET.get("from_date")
        to_date_legacy = request.GET.get("to_date")
        mode = request.GET.get("mode")
        
        # Handle legacy requests
        if not mode and (month and year):
            # Legacy monthly request
            try:
                year_int = int(year)
                month_int = int(month)
                from_date = datetime(year_int, month_int, 1).date()
                to_date = (datetime(year_int, month_int, 1) + relativedelta(months=1) - timedelta(days=1)).date()
                mode = "monthly"
            except (ValueError, TypeError):
                return Response({"error": "Invalid year or month"}, status=400)
                
        elif not mode and (from_date_legacy and to_date_legacy):
            # Legacy range request
            try:
                from_date = datetime.strptime(from_date_legacy, '%Y-%m-%d').date()
                to_date = datetime.strptime(to_date_legacy, '%Y-%m-%d').date()
                mode = "custom"
            except ValueError:
                return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)
                
        else:
            # Enhanced request - parse using new method
            try:
                from_date, to_date, mode = get_date_range_and_mode_from_request(request)
            except ValueError as e:
                return Response({"error": str(e)}, status=400)
        
        # Get feeder availability data using optimized method
        data = get_feeder_availability_summary_optimized(
            from_date=from_date,
            to_date=to_date,
            mode=mode,
            state=state,
            business_district=business_district,
        )
        
        # Remove internal source field for response
        clean_data = []
        for item in data:
            clean_item = {k: v for k, v in item.items() if not k.startswith('_')}
            clean_data.append(clean_item)
        
        # Serialize the data
        serializer = FeederAvailabilitySerializer(clean_data, many=True)
        
        return Response(serializer.data)


# Utility functions for backward compatibility

def get_feeder_availability_summary(month=None, year=None, from_date=None, to_date=None, state=None, business_district=None):
    """
    Legacy function maintained for backward compatibility.
    Now uses the optimized calculation method.
    """
    # Determine date range
    if month and year:
        from_date = datetime(year, month, 1).date()
        to_date = (datetime(year, month, 1) + relativedelta(months=1) - timedelta(days=1)).date()
        mode = "monthly"
    elif from_date and to_date:
        if isinstance(from_date, str):
            from_date = datetime.strptime(from_date, '%Y-%m-%d').date()
        if isinstance(to_date, str):
            to_date = datetime.strptime(to_date, '%Y-%m-%d').date()
        mode = "custom"
    else:
        # Default to current month
        now = datetime.now()
        from_date = datetime(now.year, now.month, 1).date()
        to_date = (datetime(now.year, now.month, 1) + relativedelta(months=1) - timedelta(days=1)).date()
        mode = "monthly"
    
    return get_feeder_availability_summary_optimized(
        from_date=from_date,
        to_date=to_date,
        mode=mode,
        state=state,
        business_district=business_district
    )