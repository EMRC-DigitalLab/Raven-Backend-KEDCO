# technical/views/service_bands/service_band_views.py
import logging
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from django.db import connection
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from common.models import Band, Feeder, State
from technical.constants import TURNAROUND_EXCLUSIONS
from technical.models import FeederInterruption, HourlyLoad
from technical.utils.energy_utils import calculate_energy_delivered

logger = logging.getLogger(__name__)


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
    today = datetime.now().date()
    
    if mode in ["daily", "weekly", "custom", "range"]:
        try:
            from_date_str = request.GET.get("from_date") or request.GET.get("from")
            to_date_str = request.GET.get("to_date") or request.GET.get("to")
            
            if not from_date_str or not to_date_str:
                raise ValueError("from_date and to_date are required for this mode")
            
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


def _parse_state_filter(request):
    """Parse and validate state filter parameter"""
    state_name = request.GET.get('state')
    if state_name:
        try:
            return State.objects.get(name__iexact=state_name)
        except State.DoesNotExist:
            pass
    return None


def calculate_band_hours_of_supply_sql(feeder_ids, from_date, to_date):
    """
    Calculate average hours of supply per day for feeders in a band.

    Denominator = feeders that transmitted ANY data in the period (not all
    feeder_ids passed in). Feeders with no rows are excluded so sync gaps
    don't deflate the average.

    Single-day: returns average hours per feeder for that day (max 24)
    Multi-day:  returns average hours per day per feeder (max 24)
    """
    if not feeder_ids:
        return 0.0

    placeholders = ','.join(['%s'] * len(feeder_ids))

    query = f"""
        SELECT
            COUNT(DISTINCT CASE WHEN hl.load_mw > 0 THEN CONCAT(hl.feeder_id, '-', hl.date, '-', hl.hour) END) AS supply_hours,
            COUNT(DISTINCT hl.feeder_id) AS feeders_with_data
        FROM technical_hourlyload hl
        WHERE hl.feeder_id IN ({placeholders})
            AND hl.date BETWEEN %s AND %s
    """

    with connection.cursor() as cursor:
        cursor.execute(query, list(feeder_ids) + [from_date, to_date])
        row = cursor.fetchone()
        total_hours = row[0] if row and row[0] else 0
        feeders_with_data = row[1] if row and row[1] else 0

    if feeders_with_data == 0:
        return 0.0

    if from_date == to_date:
        avg_hours = total_hours / feeders_with_data
    else:
        period_days = (to_date - from_date).days + 1
        avg_hours = total_hours / (feeders_with_data * period_days)

    return round(min(float(avg_hours), 24.0), 2)


def calculate_band_interruption_metrics_sql(feeder_ids, from_date, to_date, exclude_types=None):
    """
    Calculate average interruption duration per day for feeders in a band.
    
    UPDATED Logic:
    - Only considers ONBOARDED feeders (feeder_ids already filtered)
    - For single-day queries (especially today): Returns total hours (not daily average)
    - For multi-day queries: Returns average hours per day
    - Includes ALL interruptions active during the period
    - Calculates only hours within the filtered period boundaries
    - Uses timezone-aware datetime ranges for consistency
    - Caps per-feeder totals at 24 hours for single day, (24 × days) for multi-day
    
    Returns:
        tuple: (avg_duration_per_day, total_interruption_count)
    """
    if not feeder_ids:
        return 0.0, 0
    
    # ✅ Check for future dates
    now = timezone.now()
    today = now.date()
    
    if from_date > today:
        return 0.0, 0
    
    # Determine if single-day or multi-day query
    is_single_day = (from_date == to_date)
    
    if is_single_day:
        # Single day: cap per feeder at 24 hours
        total_feeders = len(feeder_ids)
        max_hours = 24.0
    else:
        # Multi-day: cap per feeder at (24 × days)
        period_days = (to_date - from_date).days + 1
        total_feeders = len(feeder_ids)
        max_hours = period_days * 24.0
    
    # ✅ Create timezone-aware datetime boundaries
    start_of_period = timezone.make_aware(
        datetime.combine(from_date, datetime.min.time())
    )
    end_of_period = timezone.make_aware(
        datetime.combine(to_date, datetime.max.time())
    )
    
    placeholders = ','.join(['%s'] * len(feeder_ids))
    
    # Build exclusion clause
    exclusion_clause = ""
    # ✅ Uses timezone-aware datetime ranges
    duration_params = [end_of_period, end_of_period, start_of_period, max_hours] + list(feeder_ids) + [start_of_period, end_of_period, start_of_period, start_of_period]
    count_params = list(feeder_ids) + [start_of_period, end_of_period]
    
    if exclude_types:
        type_placeholders = ','.join(['%s'] * len(exclude_types))
        exclusion_clause = f"AND fi.interruption_type NOT IN ({type_placeholders})"
        duration_params.extend(exclude_types)
        count_params.extend(exclude_types)
    
    # Calculate per-feeder totals, capped at max_hours
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
            WHERE fi.feeder_id IN ({placeholders})
                AND (
                    fi.occurred_at >= %s AND fi.occurred_at <= %s
                    OR (fi.occurred_at < %s AND (fi.restored_at IS NULL OR fi.restored_at >= %s))
                )
                {exclusion_clause}
            GROUP BY fi.feeder_id
        ) per_feeder_totals
    """
    
    # Count query (only interruptions that occurred in period)
    # ✅ Uses timezone-aware datetime ranges
    interruption_count_query = f"""
        SELECT COUNT(*) as total_interruptions
        FROM technical_feederinterruption fi
        WHERE fi.feeder_id IN ({placeholders})
            AND fi.occurred_at >= %s
            AND fi.occurred_at <= %s
            {exclusion_clause}
    """
    
    with connection.cursor() as cursor:
        # Get duration
        cursor.execute(interruption_duration_query, duration_params)
        result = cursor.fetchone()
        total_hours = float(result[0]) if result and result[0] else 0
        
        # Get count
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


def calculate_band_peak_load_sql(feeder_ids, from_date, to_date):
    """
    Calculate average peak load for feeders in a band
    
    UPDATED: Only considers ONBOARDED feeders (feeder_ids already filtered).
    """
    if not feeder_ids:
        return 0.0
    
    placeholders = ','.join(['%s'] * len(feeder_ids))
    
    query = f"""
        SELECT AVG(daily_peak) as avg_peak
        FROM (
            SELECT 
                feeder_id,
                date,
                MAX(load_mw) as daily_peak
            FROM technical_hourlyload
            WHERE feeder_id IN ({placeholders})
                AND date BETWEEN %s AND %s
            GROUP BY feeder_id, date
        ) daily_peaks
    """
    
    with connection.cursor() as cursor:
        cursor.execute(query, list(feeder_ids) + [from_date, to_date])
        result = cursor.fetchone()
        avg_peak = result[0] if result and result[0] else 0
    
    return round(float(avg_peak), 2)


def calculate_band_customer_count(feeder_ids, from_date):
    """Customer count - returning 0 as this data is not yet available"""
    return 0


def calculate_band_metrics(band, from_date, to_date, state_filter=None, voltage_level=None):
    """
    Calculate all metrics for a single band using direct SQL queries.
    No caching - always calculates fresh data.

    UPDATED:
    - Only considers ONBOARDED feeders for all calculations
    - Uses actual elapsed time for current periods (fractional days)
    - Uses timezone-aware datetime ranges for consistency
    """
    # Get ONBOARDED feeders for this band with optional state and voltage filtering
    feeders_query = Feeder.objects.filter(
        band=band,
        is_onboarded=True
    )
    if state_filter:
        feeders_query = feeders_query.filter(business_district__state=state_filter)
    if voltage_level:
        feeders_query = feeders_query.filter(voltage_level=voltage_level)
    
    feeder_ids = list(feeders_query.values_list('id', flat=True))
    feeder_count = len(feeder_ids)
    
    # Initialize default metrics
    if feeder_count == 0:
        return {
            "average_duration_of_supply": 0.0,
            "duration_of_interruption": 0.0,
            "turnaround_time": 0.0,
            "feeder_tripping_count": 0,
            "number_of_feeders": 0,
            "customer_count": 0,
            "average_peak_load": 0.0
        }
    
    # 1. Average hours of supply (ONBOARDED feeders only, fractional days)
    avg_supply = calculate_band_hours_of_supply_sql(feeder_ids, from_date, to_date)
    
    # 2. Interruption duration (ALL types, ONBOARDED feeders only)
    avg_duration, total_interruptions = calculate_band_interruption_metrics_sql(
        feeder_ids, from_date, to_date
    )
    
    # 3. Turnaround time (LOCAL faults only - exclude L/S, TCN, etc., ONBOARDED feeders only)
    turnaround_time, local_fault_count = calculate_band_interruption_metrics_sql(
        feeder_ids, from_date, to_date, exclude_types=TURNAROUND_EXCLUSIONS
    )
    
    # 4. Peak load (ONBOARDED feeders only)
    avg_peak_load = calculate_band_peak_load_sql(feeder_ids, from_date, to_date)

    # 5. Customer count
    customer_count = calculate_band_customer_count(feeder_ids, from_date)

    # 6. Energy delivered (smart: meter primary, system fallback per feeder)
    energy_result = calculate_energy_delivered(feeder_ids, from_date, to_date)

    avg_supply_capped = min(float(avg_supply), 24.0)
    duration_of_interruption = round(24.0 - avg_supply_capped, 2)  # always sums to 24

    return {
        "average_duration_of_supply": round(avg_supply_capped, 2),
        "duration_of_interruption": duration_of_interruption,
        "turnaround_time": float(turnaround_time),
        "feeder_tripping_count": int(local_fault_count),  # FTC = local faults only
        "number_of_feeders": int(feeder_count),
        "customer_count": int(customer_count),
        "average_peak_load": float(avg_peak_load),
        "energy_delivered": energy_result['total_mwh'],
        "energy_source_breakdown": {
            "meter_feeders": energy_result['meter_feeders'],
            "system_feeders": energy_result['system_feeders'],
        },
    }


def _format_period_label(from_date, to_date, mode):
    """Format period label based on mode"""
    if mode == "monthly":
        return from_date.strftime('%Y-%m')
    elif mode == "yearly":
        return str(from_date.year)
    elif mode == "daily":
        return from_date.strftime('%Y-%m-%d')
    else:  # weekly, custom
        return f"{from_date.strftime('%Y-%m-%d')} to {to_date.strftime('%Y-%m-%d')}"


@api_view(["GET"])
def technical_service_band_summary(request):
    """
    Technical summary for all service bands with optional state filtering.
    
    UPDATED: 
    - Only considers ONBOARDED feeders for all calculations
    - Uses actual elapsed time for current periods (fractional days)
    - Uses timezone-aware datetime ranges for consistency
    
    Returns metrics for each service band including:
    - Average duration of supply (daily average hours of electricity supply)
    - Duration of interruption (average hours including all active interruptions)
    - Turnaround time (average hours for local faults only)
    - Feeder tripping count (local fault interruptions that occurred in period)
    - Number of feeders in the band (ONBOARDED feeders only)
    - Customer count (from billing data)
    - Average peak load
    
    Supports multiple modes:
    - monthly: Month-based filtering (year, month params)
    - yearly: Year-based filtering (year param)
    - daily: Single day filtering (from_date, to_date params)
    - weekly: Week range filtering (from_date, to_date params)
    - custom: Custom date range filtering (from_date, to_date params)
    - range: Legacy range mode (same as custom)
    
    Query Parameters:
    - mode: monthly, yearly, daily, weekly, custom, range
    - For monthly: year, month
    - For yearly: year
    - For others: from_date, to_date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
    - state: State name for filtering (optional)
    
    Examples:
    - ?mode=monthly&year=2024&month=8&state=Lagos
    - ?mode=yearly&year=2024&state=Lagos
    - ?mode=daily&from_date=2024-08-02T23:00:00.000Z&to_date=2024-08-02T23:00:00.000Z
    - ?mode=weekly&from_date=2024-08-05T00:00:00.000Z&to_date=2024-08-11T23:59:59.999Z
    - ?mode=custom&from_date=2024-08-01T00:00:00.000Z&to_date=2024-08-15T23:59:59.999Z
    
    Legacy format still supported:
    - ?year=2024&month=8&state=Lagos (equivalent to monthly mode)
    - ?from=2024-08-01&to=2024-08-15&state=Lagos (equivalent to custom mode)
    
    Key Metrics (CORRECTED - ONBOARDED FEEDERS ONLY):
    - average_duration_of_supply: Average hours per day across ONBOARDED feeders (0-24)
      * Uses fractional days for current periods
    - duration_of_interruption: Average interruption hours per day across ONBOARDED feeders (0-24)
      * Includes ALL interruptions active during the period
      * Uses fractional days for current periods
    - turnaround_time: Average local fault hours per day across ONBOARDED feeders (0-24)
      * Includes ALL local faults active during the period
      * Uses fractional days for current periods
    - feeder_tripping_count: Count of local fault interruptions that occurred in period (ONBOARDED feeders only)
    - number_of_feeders: Number of ONBOARDED feeders in the band
    - customer_count: Total customers on ONBOARDED feeders (currently 0)
    - average_peak_load: Average peak load across ONBOARDED feeders
    
    NOTE: Only ONBOARDED feeders are included in all calculations.
    """
    
    # Parse state filter
    state_filter = _parse_state_filter(request)
    
    # Check if this is a legacy request or enhanced request
    mode = request.GET.get("mode")
    year = request.GET.get("year")
    month = request.GET.get("month")
    from_legacy = request.GET.get("from")
    to_legacy = request.GET.get("to")
    today = datetime.now().date()
    
    # Handle legacy requests
    if not mode and (year and month):
        # Legacy monthly request
        try:
            year_int = int(year)
            month_int = int(month)
            from_date = datetime(year_int, month_int, 1).date()
            to_date = (datetime(year_int, month_int, 1) + relativedelta(months=1) - timedelta(days=1)).date()
            
            # ✅ Cap at today
            if to_date >= today:
                to_date = today
            
            mode = "monthly"
        except (ValueError, TypeError):
            return Response({"error": "Invalid year or month"}, status=400)
            
    elif not mode and (from_legacy and to_legacy):
        # Legacy range request
        try:
            from_date = datetime.strptime(from_legacy, '%Y-%m-%d').date()
            to_date = datetime.strptime(to_legacy, '%Y-%m-%d').date()
            
            # ✅ Cap at today
            if to_date >= today:
                to_date = today
            
            mode = "custom"
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)
            
    else:
        # Enhanced request - parse using new method
        try:
            from_date, to_date, mode = get_date_range_and_mode_from_request(request)
        except ValueError as e:
            return Response({"error": str(e)}, status=400)
    
    # Parse voltage level filter
    feeder_type_param = request.GET.get("feeder_type", "")
    voltage_level = feeder_type_param if feeder_type_param in ("11kv", "33kv") else None

    # Get all service bands
    bands = Band.objects.all().order_by('name')

    band_data = []

    for band in bands:
        try:
            # Calculate metrics directly (no caching) - ONBOARDED feeders only
            band_metrics = calculate_band_metrics(band, from_date, to_date, state_filter, voltage_level=voltage_level)
            
            band_data.append({
                "band": band.name,
                "band_description": band.description,
                "metrics": band_metrics
            })
                
        except Exception as e:
            logger.error(f"Error calculating metrics for band {band.name}: {str(e)}")
            # Include band with zero metrics on error
            band_data.append({
                "band": band.name,
                "band_description": band.description,
                "metrics": {
                    "average_duration_of_supply": 0.0,
                    "duration_of_interruption": 0.0,
                    "turnaround_time": 0.0,
                    "feeder_tripping_count": 0,
                    "number_of_feeders": 0,
                    "customer_count": 0,
                    "average_peak_load": 0.0
                }
            })
    
    # Build response
    from technical.utils.compliance_utils import get_compliance_summary
    from technical.utils.explanations import SERVICE_BANDS
    response_data = {
        "period": _format_period_label(from_date, to_date, mode),
        "state_filter": state_filter.name if state_filter else None,
        "bands": band_data,
        "metadata": {
            "total_bands": len(band_data),
            "bands_with_data": len([b for b in band_data if any(v > 0 for v in b["metrics"].values() if isinstance(v, (int, float)))]),
            "bands_without_data": len([b for b in band_data if all(v == 0 for v in b["metrics"].values() if isinstance(v, (int, float)))]),
            "onboarded_feeders_only": True
        },
        "compliance": get_compliance_summary(
            from_date=from_date,
            to_date=to_date,
            state=state_filter.name if state_filter else None,
            voltage_level=voltage_level,
        ),
        "explanations": SERVICE_BANDS,
    }

    return Response(response_data)