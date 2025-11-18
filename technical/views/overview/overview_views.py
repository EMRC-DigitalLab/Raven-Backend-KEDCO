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
    
    if mode == "monthly":
        year = int(request.GET.get("year", datetime.now().year))
        month = int(request.GET.get("month", datetime.now().month))
        start_date, end_date = get_month_range(year, month)
        return {
            "mode": "monthly",
            "start_date": start_date,
            "end_date": end_date,
            "period_days": (end_date - start_date).days + 1
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
            "period_days": 1
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
        
        period_days = (to_date - from_date).days + 1
        
        return {
            "mode": "custom",
            "start_date": from_date,
            "end_date": to_date,
            "period_days": period_days
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


def calculate_hours_of_supply_feeder(feeder_id, from_date, to_date):
    """
    Calculate average hours of supply per day for a single feeder.
    
    Logic:
    - Sum all distinct hours where load > 0 across all days
    - Divide by number of days in period
    - This gives average hours per day for THIS feeder
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
    
    # Calculate average per day
    period_days = (to_date - from_date).days + 1
    avg_hours_per_day = total_hours / period_days if period_days > 0 else 0
    
    return round(min(avg_hours_per_day, 24.0), 2)


def calculate_hours_of_supply_network(from_date, to_date):
    """
    Calculate average hours of supply per day across ALL feeders in database.
    
    CORRECTED Logic:
    - Numerator: Sum of all hours supplied across all feeders with data
    - Denominator: Total feeders in DB × Days in period
    - This properly accounts for feeders with no data (they contribute 0)
    
    Example: 200 feeders, 30 days, 150 feeders have data totaling 72,000 hours
    - Old (wrong): 72,000 / (150 feeders × 30 days) = 16 hours/day
    - New (correct): 72,000 / (200 feeders × 30 days) = 12 hours/day
    """
    period_days = (to_date - from_date).days + 1
    total_feeders = Feeder.objects.count()
    
    if total_feeders == 0:
        return 0.0
    
    # Get total hours supplied across ALL feeders
    query = """
        SELECT 
            COUNT(DISTINCT CONCAT(feeder_id, '-', date, '-', hour)) as total_hours
        FROM technical_hourlyload
        WHERE date BETWEEN %s AND %s
            AND load_mw > 0
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [from_date, to_date])
        result = cursor.fetchone()
        total_hours_all_feeders = result[0] if result and result[0] else 0
    
    # Average = Total hours / (Total feeders × Days)
    avg_hours_per_day = total_hours_all_feeders / (total_feeders * period_days)
    
    return round(min(avg_hours_per_day, 24.0), 2)


def calculate_interruption_duration_feeder(feeder_id, from_date, to_date, exclude_types=None):
    """
    Calculate average interruption duration per day for a single feeder.
    
    Args:
        feeder_id: ID of the feeder
        from_date: Start date
        to_date: End date
        exclude_types: List of interruption types to exclude (for turnaround time)
    
    Returns:
        Average hours of interruption per day for this feeder
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
    
    # Calculate total hours for this feeder
    query = f"""
        SELECT 
            COALESCE(SUM(
                CASE 
                    WHEN restored_at IS NOT NULL AND restored_at <= %s THEN
                        EXTRACT(EPOCH FROM (restored_at - occurred_at)) / 3600.0
                    ELSE
                        EXTRACT(EPOCH FROM (%s - occurred_at)) / 3600.0
                END
            ), 0) as total_hours
        FROM technical_feederinterruption
        WHERE feeder_id = %s
            AND DATE(occurred_at) BETWEEN %s AND %s
            {exclusion_clause}
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        result = cursor.fetchone()
        total_hours = result[0] if result and result[0] else 0
    
    # Calculate average per day
    period_days = (to_date - from_date).days + 1
    avg_hours_per_day = total_hours / period_days if period_days > 0 else 0
    
    return round(avg_hours_per_day, 2)


def calculate_interruption_duration_network(from_date, to_date, exclude_types=None):
    """
    Calculate average interruption duration per day across ALL feeders in database.
    
    CORRECTED Logic:
    - Numerator: Sum of all interruption hours across all feeders with interruptions
    - Denominator: Total feeders in DB × Days in period
    - This properly accounts for feeders with no interruptions (they contribute 0)
    
    Example: 200 feeders, 30 days, 50 feeders had interruptions totaling 1,200 hours
    - Old (wrong): 1,200 / (50 feeders × 30 days) = 0.8 hours/day
    - New (correct): 1,200 / (200 feeders × 30 days) = 0.2 hours/day
    """
    period_days = (to_date - from_date).days + 1
    total_feeders = Feeder.objects.count()
    
    if total_feeders == 0:
        return 0.0
    
    end_of_period = timezone.make_aware(
        datetime.combine(to_date, datetime.max.time())
    )
    
    # Build exclusion clause
    exclusion_clause = ""
    params = [end_of_period, end_of_period, from_date, to_date]
    
    if exclude_types:
        placeholders = ','.join(['%s'] * len(exclude_types))
        exclusion_clause = f"AND interruption_type NOT IN ({placeholders})"
        params.extend(exclude_types)
    
    # Calculate total hours across ALL feeders
    query = f"""
        SELECT 
            COALESCE(SUM(
                CASE 
                    WHEN restored_at IS NOT NULL AND restored_at <= %s THEN
                        EXTRACT(EPOCH FROM (restored_at - occurred_at)) / 3600.0
                    ELSE
                        EXTRACT(EPOCH FROM (%s - occurred_at)) / 3600.0
                END
            ), 0) as total_hours
        FROM technical_feederinterruption
        WHERE DATE(occurred_at) BETWEEN %s AND %s
            {exclusion_clause}
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        result = cursor.fetchone()
        total_hours_all_feeders = result[0] if result and result[0] else 0
    
    # Average = Total hours / (Total feeders × Days)
    avg_hours_per_day = total_hours_all_feeders / (total_feeders * period_days)
    
    return round(avg_hours_per_day, 2)


def get_interruption_breakdown_feeder(feeder_id, start_date, end_date, period_days, period_offset=0):
    """
    Get interruption breakdown for a single feeder.
    Returns average hours per day for each interruption type for THIS feeder.
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
    
    end_of_period = timezone.make_aware(
        datetime.combine(target_end, datetime.max.time())
    )
    
    # Calculate hours for each interruption type for this feeder
    query = """
        SELECT 
            COALESCE(interruption_type, 'Unknown') as itype,
            COALESCE(SUM(
                CASE 
                    WHEN restored_at IS NOT NULL AND restored_at <= %s THEN
                        EXTRACT(EPOCH FROM (restored_at - occurred_at)) / 3600.0
                    ELSE
                        EXTRACT(EPOCH FROM (%s - occurred_at)) / 3600.0
                END
            ), 0) as total_hours
        FROM technical_feederinterruption
        WHERE feeder_id = %s
            AND DATE(occurred_at) BETWEEN %s AND %s
        GROUP BY interruption_type
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [end_of_period, end_of_period, feeder_id, target_start, target_end])
        results = cursor.fetchall()
    
    # Process results
    type_totals = {}
    total_hours = 0
    
    for itype, hours in results:
        type_totals[itype or 'Unknown'] = hours if hours else 0
        total_hours += hours if hours else 0
    
    # Calculate averages per day
    num_days = (target_end - target_start).days + 1
    type_averages = {k: round(v / num_days, 2) for k, v in type_totals.items()}
    avg_total_per_day = round(total_hours / num_days, 2) if num_days > 0 else 0
    
    return {
        "month": label,
        "total": avg_total_per_day,
        "delta": 0,
        "breakdown": type_averages
    }


def get_interruption_breakdown_network(start_date, end_date, period_days, period_offset=0):
    """
    Get interruption breakdown across ALL feeders in database.
    
    CORRECTED Logic:
    - Returns average hours per day per feeder for each interruption type
    - Denominator includes ALL feeders in database, not just those with interruptions
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
    
    num_days = (target_end - target_start).days + 1
    total_feeders = Feeder.objects.count()
    
    if total_feeders == 0:
        return {
            "month": label,
            "total": 0,
            "delta": 0,
            "breakdown": {}
        }
    
    end_of_period = timezone.make_aware(
        datetime.combine(target_end, datetime.max.time())
    )
    
    # Calculate total hours for each interruption type across ALL feeders
    query = """
        SELECT 
            COALESCE(interruption_type, 'Unknown') as itype,
            COALESCE(SUM(
                CASE 
                    WHEN restored_at IS NOT NULL AND restored_at <= %s THEN
                        EXTRACT(EPOCH FROM (restored_at - occurred_at)) / 3600.0
                    ELSE
                        EXTRACT(EPOCH FROM (%s - occurred_at)) / 3600.0
                END
            ), 0) as total_hours
        FROM technical_feederinterruption
        WHERE DATE(occurred_at) BETWEEN %s AND %s
        GROUP BY interruption_type
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, [end_of_period, end_of_period, target_start, target_end])
        results = cursor.fetchall()
    
    # Process results
    type_totals = {}
    total_hours = 0
    
    for itype, hours in results:
        type_totals[itype or 'Unknown'] = hours if hours else 0
        total_hours += hours if hours else 0
    
    # Calculate averages: Total hours / (Total feeders × Days)
    type_averages = {k: round(v / (total_feeders * num_days), 2) for k, v in type_totals.items()}
    avg_total_per_day = round(total_hours / (total_feeders * num_days), 2)
    
    return {
        "month": label,
        "total": avg_total_per_day,
        "delta": 0,
        "breakdown": type_averages
    }


def get_load_trend_optimized(start_date, end_date, mode, feeder_id=None):
    """
    Get load trend data optimized for the selected mode.
    For monthly mode: returns daily averages for each day of the month
    For daily mode: returns hourly averages for that specific day
    
    Args:
        start_date: Start date
        end_date: End date
        mode: Mode (monthly, daily, custom, etc.)
        feeder_id: Optional feeder ID to filter by specific feeder
    """
    # Build base query with optional feeder filter
    base_filter = {'date__range': (start_date, end_date)}
    if feeder_id:
        base_filter['feeder_id'] = feeder_id
    
    if mode == "monthly":
        # Get average load for each day of the month
        daily_loads = HourlyLoad.objects.filter(
            **base_filter
        ).values('date').annotate(
            avg_load=Avg('load_mw')
        ).order_by('date')
        
        series = [
            {
                "day": entry["date"].day,
                "value": round(float(entry["avg_load"] or 0), 2)
            }
            for entry in daily_loads
        ]
        
        return {
            "unit": "MW",
            "date": start_date.strftime("%Y-%m"),
            "series": series
        }
    
    elif mode == "daily":
        # Get hourly loads for the specific day
        base_filter['date'] = start_date
        hourly_loads = HourlyLoad.objects.filter(
            **base_filter
        ).values('hour').annotate(
            avg_load=Avg('load_mw')
        ).order_by('hour')
        
        series = [
            {
                "hour": entry["hour"],
                "value": round(float(entry["avg_load"] or 0), 2)
            }
            for entry in hourly_loads
        ]
        
        return {
            "unit": "MW",
            "date": start_date.isoformat(),
            "series": series
        }
    
    else:  # custom range
        # For custom ranges, show daily averages
        daily_loads = HourlyLoad.objects.filter(
            **base_filter
        ).values('date').annotate(
            avg_load=Avg('load_mw')
        ).order_by('date')
        
        series = [
            {
                "date": entry["date"].isoformat(),
                "value": round(float(entry["avg_load"] or 0), 2)
            }
            for entry in daily_loads
        ]
        
        return {
            "unit": "MW",
            "date": f"{start_date.isoformat()} to {end_date.isoformat()}",
            "series": series
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
    # Energy delivered: Calculate from HourlyLoad (Sum of MW × 1 hour = MWh)
    energy_result = HourlyLoad.objects.filter(
        date__range=(start_date, end_date),
        **feeder_filter
    ).aggregate(total=Sum('load_mw'))
    energy_now = float(energy_result['total'] or 0)
    
    # Previous period energy from HourlyLoad
    energy_prev_result = HourlyLoad.objects.filter(
        date__range=(prev_start, prev_end),
        **feeder_filter
    ).aggregate(total=Sum('load_mw'))
    energy_prev = float(energy_prev_result['total'] or 0)
    
    # Average load - daily average for the period with feeder filter
    load_result = HourlyLoad.objects.filter(
        date__range=(start_date, end_date),
        **feeder_filter
    ).aggregate(avg_load=Avg('load_mw'))
    load_now = float(load_result['avg_load'] or 0)
    
    load_prev_result = HourlyLoad.objects.filter(
        date__range=(prev_start, prev_end),
        **feeder_filter
    ).aggregate(avg_load=Avg('load_mw'))
    load_prev = float(load_prev_result['avg_load'] or 0)
    
    # Interruption count with feeder filter
    interruptions_now = FeederInterruption.objects.filter(
        occurred_at__date__range=(start_date, end_date),
        **feeder_filter
    ).count()
    
    interruptions_prev = FeederInterruption.objects.filter(
        occurred_at__date__range=(prev_start, prev_end),
        **feeder_filter
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
    else:
        # For all feeders, use network-wide calculation (CORRECTED)
        supply_hours = get_metric_with_history(
            calculate_hours_of_supply_network, 
            start_date, 
            end_date, 
            period_days
        )
        
        # Interruption duration (includes all types) - NETWORK-WIDE (CORRECTED)
        interruption_duration = get_metric_with_history(
            lambda s, e: calculate_interruption_duration_network(s, e),
            start_date,
            end_date,
            period_days
        )
        
        # Turnaround time (excludes L/S and TCN types) - NETWORK-WIDE (CORRECTED)
        turnaround_time = get_metric_with_history(
            lambda s, e: calculate_interruption_duration_network(s, e, exclude_types=TURNAROUND_EXCLUSIONS),
            start_date,
            end_date,
            period_days
        )
    
    # Technical breakdown
    if feeder_slug:
        # For single feeder
        feeders_now = 1
        feeders_prev = 1
        customer_count = 0
    else:
        # For all feeders
        feeders_now = Feeder.objects.count()
        feeders_prev = feeders_now  # You may want to track this historically
        customer_count = 5_000_000  # Replace with actual query if available
    
    breakdown = {
        "feeder_count": {
            "value": feeders_now,
            "delta": delta(feeders_now, feeders_prev)
        },
        "avg_daily_interruptions": {
            "value": round(interruptions_now / period_days, 2),
            "delta": delta(
                interruptions_now / period_days,
                interruptions_prev / ((prev_end - prev_start).days + 1)
            )
        },
        "avg_turnaround": {
            "value": turnaround_time["current"],
            "delta": turnaround_time["delta"]
        },
        "customer_count": {
            "value": customer_count,
            "delta": 0  # Replace with actual calculation
        }
    }
    
    # Interruption sources for 4 periods - CORRECTED
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
    
    # Load trend - OPTIMIZED with feeder filter
    load_trend = get_load_trend_optimized(start_date, end_date, mode, feeder_id=feeder.id if feeder_slug else None)
    
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
            "interruptions": {
                "value": interruptions_now,
                "delta": delta(interruptions_now, interruptions_prev)
            },
        },
        "supply_and_quality": {
            "supply_hours": supply_hours,
            "interruption_duration": interruption_duration,
            "turnaround_time": turnaround_time
        },
        "technical_breakdown": breakdown,
        "interruption_sources": interruptions_data,
        "load_trend": load_trend
    }
    
    # Add feeder info to response if filtered
    if feeder_slug:
        response_data["feeder"] = {
            "name": feeder_name,
            "slug": feeder_slug,
            "voltage_level": feeder.voltage_level
        }
    
    return Response(response_data)