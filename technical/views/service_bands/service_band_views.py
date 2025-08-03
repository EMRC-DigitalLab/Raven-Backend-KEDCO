from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Sum, Avg, Max, Q, Count
from django.core.cache import cache
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import hashlib
import logging
from analytics.models import MonthlyTechnicalSummary, DailyTechnicalSummary
from common.models import State, Band, Feeder, DistributionTransformer
from technical.models import HourlyLoad, FeederInterruption, FeederEnergyDaily
from commercial.models import MonthlyCommercialSummary

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
    
    if mode in ["daily", "weekly", "custom", "range"]:
        try:
            from_date_str = request.GET.get("from_date") or request.GET.get("from")
            to_date_str = request.GET.get("to_date") or request.GET.get("to")
            
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


def _get_band_cache_key(from_date, to_date, mode, state_filter):
    """Generate cache key for band technical summary"""
    state_str = f"_state_{state_filter.id}" if state_filter else ""
    cache_str = f"band_tech_{mode}_{from_date}_{to_date}{state_str}"
    return hashlib.md5(cache_str.encode()).hexdigest()[:16]


def _parse_state_filter(request):
    """Parse and validate state filter parameter"""
    state_name = request.GET.get('state')
    if state_name:
        try:
            return State.objects.get(name__iexact=state_name)
        except State.DoesNotExist:
            pass
    return None


def _get_band_metrics_from_summary(band, from_date, to_date, mode, state_filter):
    """
    Try to get band metrics from pre-calculated summary data.
    Returns None if summary data is not available.
    """
    try:
        if mode == "monthly":
            return _get_monthly_summary_metrics_band(band, from_date, state_filter)
        elif mode == "yearly":
            return _get_yearly_summary_metrics_band(band, from_date, state_filter)
        else:
            return _get_daily_summary_metrics_band(band, from_date, to_date, mode, state_filter)
            
    except Exception as e:
        logger.error(f"Error getting summary data for band {band.name}: {str(e)}")
        return None


def _get_monthly_summary_metrics_band(band, from_date, state_filter):
    """Get band metrics from monthly summaries aggregated from feeder-level data"""
    target_month = from_date
    
    try:
        # Get all feeders for this band with optional state filtering
        feeders_query = Feeder.objects.filter(band=band)
        if state_filter:
            feeders_query = feeders_query.filter(business_district__state=state_filter)
        
        feeder_ids = list(feeders_query.values_list('id', flat=True))
        
        if not feeder_ids:
            return None
        
        # Get feeder-level summaries for this month
        feeder_summaries = MonthlyTechnicalSummary.objects.filter(
            feeder__in=feeders_query,
            business_district__isnull=False,  # Feeder-level summaries
            month=target_month,
            has_complete_data=True
        )
        
        # Check if we have summaries for all feeders
        if feeder_summaries.count() != len(feeder_ids):
            return None
        
        # Aggregate metrics across feeders
        total_interruptions = feeder_summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
        avg_supply = feeder_summaries.aggregate(avg=Avg('avg_hours_of_supply'))['avg'] or 0
        avg_duration = feeder_summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
        avg_turnaround = feeder_summaries.aggregate(avg=Avg('avg_fault_turnaround_time'))['avg'] or 0
        avg_peak_load = feeder_summaries.aggregate(avg=Avg('max_peak_load'))['avg'] or 0
        total_customers = feeder_summaries.aggregate(total=Sum('total_customer_count'))['total'] or 0
        
        return {
            "average_duration_of_supply": round(float(avg_supply), 2),
            "duration_of_interruption": round(float(avg_duration), 2),
            "turnaround_time": round(float(avg_turnaround), 2),
            "feeder_tripping_count": int(total_interruptions),
            "number_of_feeders": len(feeder_ids),
            "customer_count": int(total_customers),
            "average_peak_load": round(float(avg_peak_load), 2),
            "_source": "monthly_summary"
        }
        
    except Exception as e:
        logger.error(f"Error getting monthly summaries for band {band.name}: {str(e)}")
        return None


def _get_yearly_summary_metrics_band(band, from_date, state_filter):
    """Get yearly band metrics from aggregated monthly summaries"""
    target_year = from_date.year
    
    # Get all months in the year
    year_months = []
    for month in range(1, 13):
        year_months.append(datetime(target_year, month, 1).date())
    
    try:
        # Get all feeders for this band with optional state filtering
        feeders_query = Feeder.objects.filter(band=band)
        if state_filter:
            feeders_query = feeders_query.filter(business_district__state=state_filter)
        
        feeder_ids = list(feeders_query.values_list('id', flat=True))
        
        if not feeder_ids:
            return None
        
        # Get feeder-level summaries for the entire year
        year_summaries = MonthlyTechnicalSummary.objects.filter(
            feeder__in=feeders_query,
            business_district__isnull=False,  # Feeder-level summaries
            month__in=year_months,
            has_complete_data=True
        )
        
        # Check if we have complete data for the year (12 months * number of feeders)
        expected_summaries = len(feeder_ids) * 12
        if year_summaries.count() != expected_summaries:
            return None
        
        # Aggregate yearly metrics
        total_interruptions = year_summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
        avg_supply = year_summaries.aggregate(avg=Avg('avg_hours_of_supply'))['avg'] or 0
        avg_duration = year_summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
        avg_turnaround = year_summaries.aggregate(avg=Avg('avg_fault_turnaround_time'))['avg'] or 0
        avg_peak_load = year_summaries.aggregate(avg=Avg('max_peak_load'))['avg'] or 0
        
        # Get customer count from latest month
        latest_month_summaries = year_summaries.filter(month=year_months[-1])
        total_customers = latest_month_summaries.aggregate(total=Sum('total_customer_count'))['total'] or 0
        
        return {
            "average_duration_of_supply": round(float(avg_supply), 2),
            "duration_of_interruption": round(float(avg_duration), 2),
            "turnaround_time": round(float(avg_turnaround), 2),
            "feeder_tripping_count": int(total_interruptions),
            "number_of_feeders": len(feeder_ids),
            "customer_count": int(total_customers),
            "average_peak_load": round(float(avg_peak_load), 2),
            "_source": "yearly_summary"
        }
        
    except Exception as e:
        logger.error(f"Error getting yearly summaries for band {band.name}: {str(e)}")
        return None


def _get_daily_summary_metrics_band(band, from_date, to_date, mode, state_filter):
    """Get band metrics from daily summaries aggregated from feeder-level data"""
    # Collect all dates in the range
    dates = []
    current = from_date
    while current <= to_date:
        dates.append(current)
        current += timedelta(days=1)
    
    try:
        # Get all feeders for this band with optional state filtering
        feeders_query = Feeder.objects.filter(band=band)
        if state_filter:
            feeders_query = feeders_query.filter(business_district__state=state_filter)
        
        feeder_ids = list(feeders_query.values_list('id', flat=True))
        
        if not feeder_ids:
            return None
        
        # Get feeder-level daily summaries
        daily_summaries = DailyTechnicalSummary.objects.filter(
            feeder__in=feeders_query,
            business_district__isnull=False,  # Feeder-level summaries
            date__in=dates,
            has_complete_data=True
        )
        
        # Check if we have complete data
        expected_summaries = len(feeder_ids) * len(dates)
        if daily_summaries.count() != expected_summaries:
            return None
        
        # Aggregate metrics across feeders and dates
        if mode == "daily" and len(dates) == 1:
            # For single day, average across feeders
            avg_supply = daily_summaries.aggregate(avg=Avg('hours_of_supply'))['avg'] or 0
            avg_duration = daily_summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
            avg_turnaround = daily_summaries.aggregate(avg=Avg('avg_fault_turnaround_time'))['avg'] or 0
            total_interruptions = daily_summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
            avg_peak_load = daily_summaries.aggregate(avg=Avg('max_peak_load'))['avg'] or 0
        else:
            # For multi-day periods, average across time and feeders
            avg_supply = daily_summaries.aggregate(avg=Avg('hours_of_supply'))['avg'] or 0
            avg_duration = daily_summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
            avg_turnaround = daily_summaries.aggregate(avg=Avg('avg_fault_turnaround_time'))['avg'] or 0
            total_interruptions = daily_summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
            avg_peak_load = daily_summaries.aggregate(avg=Avg('max_peak_load'))['avg'] or 0
        
        # Get customer count from latest day
        latest_summaries = daily_summaries.filter(date=dates[-1])
        total_customers = latest_summaries.aggregate(total=Sum('total_customer_count'))['total'] or 0
        
        return {
            "average_duration_of_supply": round(float(avg_supply), 2),
            "duration_of_interruption": round(float(avg_duration), 2),
            "turnaround_time": round(float(avg_turnaround), 2),
            "feeder_tripping_count": int(total_interruptions),
            "number_of_feeders": len(feeder_ids),
            "customer_count": int(total_customers),
            "average_peak_load": round(float(avg_peak_load), 2),
            "_source": f"daily_summary_{mode}"
        }
        
    except Exception as e:
        logger.error(f"Error getting daily summaries for band {band.name}: {str(e)}")
        return None


def _calculate_band_metrics_realtime(band, from_date, to_date, mode, state_filter):
    """
    Calculate band metrics in real-time when summary data is not available.
    Always returns metrics object, with zeros when no data available.
    """
    
    # Get feeders for this band with optional state filtering
    feeders_query = Feeder.objects.filter(band=band)
    if state_filter:
        feeders_query = feeders_query.filter(business_district__state=state_filter)
    
    feeders = feeders_query.select_related('business_district__state')
    feeder_ids = list(feeders.values_list("id", flat=True))
    
    # Initialize default metrics (all zeros)
    default_metrics = {
        "average_duration_of_supply": 0.0,
        "duration_of_interruption": 0.0,
        "turnaround_time": 0.0,
        "feeder_tripping_count": 0,
        "number_of_feeders": len(feeder_ids),  # Always show feeder count
        "customer_count": 0,
        "average_peak_load": 0.0,
        "_source": f"realtime_{mode}"
    }
    
    # If no feeders, return default metrics
    if not feeder_ids:
        default_metrics["_source"] = "no_feeders"
        return default_metrics
    
    try:
        # 1. Average Duration of Supply Calculation
        avg_supply_duration = _calculate_band_average_supply_duration(feeder_ids, from_date, to_date)
        
        # 2. Interruption Metrics
        interruption_metrics = _calculate_band_interruption_metrics(feeder_ids, from_date, to_date)
        
        # 3. Infrastructure Metrics
        infrastructure_metrics = _calculate_band_infrastructure_metrics(feeder_ids, from_date, to_date, mode)
        
        return {
            "average_duration_of_supply": float(avg_supply_duration),
            "duration_of_interruption": interruption_metrics['avg_duration'],
            "turnaround_time": interruption_metrics['avg_turnaround_time'],
            "feeder_tripping_count": interruption_metrics['tripping_count'],
            "number_of_feeders": infrastructure_metrics['feeder_count'],
            "customer_count": infrastructure_metrics['customer_count'],
            "average_peak_load": infrastructure_metrics['avg_peak_load'],
            "_source": f"realtime_{mode}"
        }
        
    except Exception as e:
        logger.error(f"Error in band metrics calculation: {str(e)}")
        default_metrics["_source"] = "calculation_error"
        return default_metrics


def _calculate_band_average_supply_duration(feeder_ids, from_date, to_date):
    """Calculate average daily duration of supply for the band"""
    
    # Return 0 if no feeders
    if not feeder_ids:
        return 0.0
    
    try:
        # Try to use DailyHoursOfSupply if available
        try:
            from technical.models import DailyHoursOfSupply
            daily_supply = DailyHoursOfSupply.objects.filter(
                feeder_id__in=feeder_ids,
                date__range=(from_date, to_date)
            )
            
            if daily_supply.exists():
                avg_supply = daily_supply.aggregate(avg=Avg('hours_supplied'))['avg'] or 0
                return round(min(float(avg_supply), 24.0), 2)  # Cap at 24 hours
            
        except ImportError:
            pass  # DailyHoursOfSupply doesn't exist, use hourly method
        
        # Fallback: Calculate daily hours from HourlyLoad data
        daily_hours = HourlyLoad.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(from_date, to_date),
            load_mw__gt=0  # Only count hours with actual load
        ).values('feeder', 'date').annotate(
            daily_hours=Count('hour')
        )
        
        if daily_hours.exists():
            avg_supply = daily_hours.aggregate(avg=Avg('daily_hours'))['avg'] or 0
            return round(min(float(avg_supply), 24.0), 2)  # Cap at 24 hours
        else:
            return 0.0
            
    except Exception as e:
        logger.error(f"Error calculating band average supply duration: {str(e)}")
        return 0.0


def _calculate_band_interruption_metrics(feeder_ids, from_date, to_date):
    """Calculate interruption-related metrics for the band with improved logic"""
    from django.utils import timezone
    
    # Get all interruptions for the band's feeders
    all_interruptions = FeederInterruption.objects.filter(
        feeder_id__in=feeder_ids,
        occurred_at__date__range=(from_date, to_date)
    )
    
    # Filter out load shedding and maintenance for tripping rate
    load_shedding_types = ['L/S', 'L/S GS', '330KV L/S', 'T/LS']
    maintenance_types = ['MTNC', 'MTCE', '132KV MTCE', 'permit']
    excluded_types = load_shedding_types + maintenance_types
    
    fault_interruptions = all_interruptions.exclude(
        interruption_type__in=excluded_types
    )
    
    total_fault_interruptions = fault_interruptions.count()
    total_all_interruptions = all_interruptions.count()
    
    if total_all_interruptions == 0:
        return {
            'avg_duration': 0.0,
            'avg_turnaround_time': 0.0,
            'tripping_count': 0
        }
    
    # Calculate duration including ongoing interruptions
    total_duration_hours = 0.0
    interruption_count = 0
    
    # Create timezone-aware period end datetime
    period_end_naive = datetime.combine(to_date, datetime.max.time())
    period_end = timezone.make_aware(period_end_naive) if timezone.is_naive(period_end_naive) else period_end_naive
    
    for interruption in all_interruptions:
        try:
            if interruption.restored_at:
                # Resolved interruption - use actual duration
                duration = (interruption.restored_at - interruption.occurred_at).total_seconds() / 3600
            else:
                # Ongoing interruption - calculate duration to end of period
                # Ensure both datetimes have the same timezone awareness
                occurred_at = interruption.occurred_at
                if timezone.is_naive(occurred_at) and not timezone.is_naive(period_end):
                    occurred_at = timezone.make_aware(occurred_at)
                elif not timezone.is_naive(occurred_at) and timezone.is_naive(period_end):
                    period_end = timezone.make_aware(period_end)
                
                duration = (period_end - occurred_at).total_seconds() / 3600
            
            # Only include reasonable durations (not negative, not extremely long)
            if duration >= 0 and duration <= 8760:  # Max 1 year duration
                total_duration_hours += duration
                interruption_count += 1
                
        except Exception as e:
            logger.error(f"Error calculating duration for interruption {interruption.id}: {str(e)}")
            continue
    
    # Calculate average duration
    avg_duration = total_duration_hours / interruption_count if interruption_count > 0 else 0.0
    
    # Feeder tripping count = total fault interruptions (excludes load shedding and maintenance)
    tripping_count = total_fault_interruptions
    
    return {
        'avg_duration': round(avg_duration, 2),
        'avg_turnaround_time': round(avg_duration, 2),  # Same as duration for restoration
        'tripping_count': tripping_count
    }


def _calculate_band_infrastructure_metrics(feeder_ids, from_date, to_date, mode):
    """Calculate infrastructure-related metrics for the band using commercial data"""
    
    # Feeder count
    feeder_count = len(feeder_ids)
    
    # Get customer count from commercial data (customers actually billed)
    # Get all transformers connected to feeders in this band
    transformer_ids = DistributionTransformer.objects.filter(
        feeder_id__in=feeder_ids
    ).values_list('id', flat=True)
    
    # Get customer count based on mode
    if mode == "monthly":
        # Use monthly commercial summary
        month_date = from_date.replace(day=1)  # Ensure it's first day of month
        customer_count = MonthlyCommercialSummary.objects.filter(
            transformer_id__in=transformer_ids,
            month=month_date
        ).aggregate(
            total_customers=Sum('customers_billed')
        )['total_customers'] or 0
    else:
        # For daily/weekly/custom, try to get from the most recent month
        # Find the most recent month that falls within or before the date range
        if from_date.day == 1:
            # If from_date is first of month, use that month
            month_date = from_date
        else:
            # Otherwise, use the previous month
            month_date = from_date.replace(day=1)
            if month_date > from_date:
                month_date = month_date - relativedelta(months=1)
        
        customer_count = MonthlyCommercialSummary.objects.filter(
            transformer_id__in=transformer_ids,
            month=month_date
        ).aggregate(
            total_customers=Sum('customers_billed')
        )['total_customers'] or 0
    
    # Average peak load calculation
    peak_loads = HourlyLoad.objects.filter(
        feeder_id__in=feeder_ids,
        date__range=(from_date, to_date)
    ).values('feeder', 'date').annotate(
        daily_peak=Max('load_mw')
    )
    
    if peak_loads.exists():
        avg_peak_load = peak_loads.aggregate(
            avg=Avg('daily_peak')
        )['avg'] or 0.0
    else:
        avg_peak_load = 0.0
    
    return {
        'feeder_count': feeder_count,
        'customer_count': customer_count,
        'avg_peak_load': round(float(avg_peak_load), 2)
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
    Enhanced technical summary for all service bands with optional state filtering.
    
    Returns metrics for each service band including:
    - Average duration of supply (daily average hours of electricity supply)
    - Duration of interruption (average hours including ongoing outages)
    - Turnaround time (same as duration)
    - Feeder tripping count (total fault interruptions excluding load shedding/maintenance)
    - Number of feeders in the band
    - Customer count (from billing data)
    - Average peak load
    
    Enhanced to support multiple modes:
    - monthly: Traditional month-based filtering using MonthlyTechnicalSummary
    - yearly: Year-based filtering using MonthlyTechnicalSummary (aggregated)
    - daily: Single day filtering using DailyTechnicalSummary
    - weekly: Week range filtering using DailyTechnicalSummary
    - custom: Custom date range filtering using DailyTechnicalSummary
    - range: Legacy range mode (same as custom)
    
    Query Parameters:
    - mode: monthly, yearly, daily, weekly, custom, range
    - For monthly: year, month
    - For yearly: year
    - For others: from_date, to_date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
    - state: State name for filtering (optional)
    
    Legacy parameters still supported:
    - year, month: Specific month (defaults to current month)
    - from, to: Date range (alternative to from_date, to_date)
    
    IMPORTANT: Response structure maintained for backward compatibility!
    
    Examples:
    - ?mode=monthly&year=2024&month=8&state=Lagos
    - ?mode=yearly&year=2024&state=Lagos
    - ?mode=daily&from_date=2024-08-02T23:00:00.000Z&to_date=2024-08-02T23:00:00.000Z
    - ?mode=weekly&from_date=2024-08-05T00:00:00.000Z&to_date=2024-08-11T23:59:59.999Z&state=Lagos
    - ?mode=custom&from_date=2024-08-01T00:00:00.000Z&to_date=2024-08-15T23:59:59.999Z
    
    Legacy format still supported:
    - ?year=2024&month=8&state=Lagos (equivalent to monthly mode)
    - ?from=2024-08-01&to=2024-08-15&state=Lagos (equivalent to custom mode)
    """
    
    # Parse state filter
    state_filter = _parse_state_filter(request)
    
    # Check if this is a legacy request or enhanced request
    mode = request.GET.get("mode")
    year = request.GET.get("year")
    month = request.GET.get("month")
    from_legacy = request.GET.get("from")
    to_legacy = request.GET.get("to")
    
    # Handle legacy requests
    if not mode and (year and month):
        # Legacy monthly request
        try:
            year_int = int(year)
            month_int = int(month)
            from_date = datetime(year_int, month_int, 1).date()
            to_date = (datetime(year_int, month_int, 1) + relativedelta(months=1) - timedelta(days=1)).date()
            mode = "monthly"
        except (ValueError, TypeError):
            return Response({"error": "Invalid year or month"}, status=400)
            
    elif not mode and (from_legacy and to_legacy):
        # Legacy range request
        try:
            from_date = datetime.strptime(from_legacy, '%Y-%m-%d').date()
            to_date = datetime.strptime(to_legacy, '%Y-%m-%d').date()
            mode = "custom"
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)
            
    else:
        # Enhanced request - parse using new method
        try:
            from_date, to_date, mode = get_date_range_and_mode_from_request(request)
        except ValueError as e:
            return Response({"error": str(e)}, status=400)
    
    # Debug logging
    print(f"DEBUG: Request params: {dict(request.GET)}")
    print(f"DEBUG: Calculated date range: {from_date} to {to_date}, mode: {mode}")
    print(f"DEBUG: State filter: {state_filter.name if state_filter else 'None'}")
    
    # Try cache first
    cache_key = _get_band_cache_key(from_date, to_date, mode, state_filter)
    cached_response = cache.get(cache_key)
    if cached_response:
        print("DEBUG: Returning cached response")
        return Response(cached_response)
    
    # Get all service bands
    bands = Band.objects.all().order_by('name')
    print(f"DEBUG: Found {bands.count()} service bands")
    
    band_data = []
    summary_count = 0
    realtime_count = 0
    
    for band in bands:
        try:
            # Try to get from summary first
            band_metrics = _get_band_metrics_from_summary(band, from_date, to_date, mode, state_filter)
            
            if band_metrics:
                summary_count += 1
            else:
                # Fallback to real-time calculation
                realtime_count += 1
                band_metrics = _calculate_band_metrics_realtime(
                    band, from_date, to_date, mode, state_filter
                )
            
            # Always include the band, even with zero data
            # Remove internal _source field for response
            clean_metrics = {k: v for k, v in band_metrics.items() if not k.startswith('_')}
            
            band_data.append({
                "band": band.name,
                "band_description": band.description,
                "metrics": clean_metrics
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
    
    print(f"DEBUG: Used {summary_count} summaries, {realtime_count} real-time calculations")
    
    # MAINTAIN ORIGINAL RESPONSE STRUCTURE
    response_data = {
        "period": _format_period_label(from_date, to_date, mode),
        "state_filter": state_filter.name if state_filter else None,
        "bands": band_data,
        "metadata": {
            "total_bands": len(band_data),
            "bands_with_data": len([b for b in band_data if any(v > 0 for v in b["metrics"].values() if isinstance(v, (int, float)))]),
            "bands_without_data": len([b for b in band_data if all(v == 0 for v in b["metrics"].values() if isinstance(v, (int, float)))])
        }
    }
    
    # Cache for different durations based on mode and whether it includes current data
    today = datetime.now().date()
    if to_date >= today:
        cache_timeout = 300  # 5 minutes for current data
    else:
        cache_timeout = 1800  # 30 minutes for historical data
    
    cache.set(cache_key, response_data, cache_timeout)
    print(f"DEBUG: Cached response with key: {cache_key} for {cache_timeout} seconds")
    
    return Response(response_data)


# Legacy functions maintained for backward compatibility
def _parse_date_params(request):
    """Legacy function - parse date parameters and return target month date"""
    try:
        year = int(request.GET.get("year", datetime.now().year))
        month = int(request.GET.get("month", datetime.now().month))
        return datetime(year, month, 1).date()
    except (TypeError, ValueError):
        return datetime.now().date().replace(day=1)


def _get_month_range(year, month):
    """Legacy function - get start and end dates for a given year/month"""
    start = datetime(year, month, 1).date()
    if month == 12:
        end = datetime(year + 1, 1, 1).date() - timedelta(days=1)
    else:
        end = datetime(year, month + 1, 1).date() - timedelta(days=1)
    return start, end