from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Count, Sum, Avg, Max
from django.core.cache import cache
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import hashlib
from analytics.models import MonthlyTechnicalSummary, DailyTechnicalSummary
from common.models import Feeder, BusinessDistrict
from technical.models import HourlyLoad, FeederInterruption, FeederEnergyDaily, FeederEnergyMonthly, DailyHoursOfSupply
from commercial.models import Customer


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


def _get_districts_cache_key(state_name, from_date, to_date, mode):
    """Generate cache key for districts technical summary"""
    cache_str = f"districts_tech_{state_name}_{mode}_{from_date}_{to_date}"
    return hashlib.md5(cache_str.encode()).hexdigest()[:16]


def _get_district_metrics_from_summary(district, from_date, to_date, mode):
    """Get district metrics from pre-calculated summary data based on mode"""
    try:
        print(f"DEBUG: Getting summary data for district {district.name}, mode: {mode}")
        
        if mode == "monthly":
            return _get_monthly_summary_metrics_district(district, from_date, to_date)
        elif mode == "yearly":
            return _get_yearly_summary_metrics_district(district, from_date, to_date)
        else:
            return _get_daily_summary_metrics_district(district, from_date, to_date, mode)
            
    except Exception as e:
        print(f"DEBUG: Error getting summary data for {district.name}: {str(e)}")
        return None


def _get_monthly_summary_metrics_district(district, from_date, to_date):
    """Get metrics from monthly summaries for district"""
    target_month = from_date  # from_date is first day of month for monthly mode
    
    try:
        summary = MonthlyTechnicalSummary.objects.get(
            state=district.state,
            business_district=district,
            feeder__isnull=True,
            month=target_month,
            has_complete_data=True
        )
        
        return {
            "avg_supply": float(summary.avg_hours_of_supply),
            "duration": float(summary.avg_interruption_duration),
            "turnaround_time": float(summary.avg_fault_turnaround_time),
            "ftc": summary.total_interruptions,
            "daily_interruptions": float(summary.avg_daily_interruptions),
            "feeder_count": summary.active_feeder_count,
            "peak_load": float(summary.max_peak_load),
            "customer_population": summary.total_customer_count,
            "energy_delivered": float(summary.total_energy_delivered),
            "_source": "monthly_summary"
        }
        
    except MonthlyTechnicalSummary.DoesNotExist:
        return None


def _get_yearly_summary_metrics_district(district, from_date, to_date):
    """Get metrics from yearly aggregation of monthly summaries for district"""
    target_year = from_date.year
    
    # Get all months in the year
    year_months = []
    for month in range(1, 13):
        year_months.append(datetime(target_year, month, 1).date())
    
    try:
        year_summaries = MonthlyTechnicalSummary.objects.filter(
            state=district.state,
            business_district=district,
            feeder__isnull=True,
            month__in=year_months,
            has_complete_data=True
        )
        
        # Check if we have complete data for the year
        if year_summaries.count() != 12:
            return None
        
        # Calculate yearly aggregates
        total_interruptions = year_summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
        avg_supply = year_summaries.aggregate(avg=Avg('avg_hours_of_supply'))['avg'] or 0
        avg_duration = year_summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
        avg_turnaround = year_summaries.aggregate(avg=Avg('avg_fault_turnaround_time'))['avg'] or 0
        total_energy = year_summaries.aggregate(total=Sum('total_energy_delivered'))['total'] or 0
        max_peak_load = year_summaries.aggregate(max=Max('max_peak_load'))['max'] or 0
        
        # Get infrastructure metrics from latest summary
        latest_summary = year_summaries.order_by('-month').first()
        
        # Calculate daily interruptions for the year
        days_in_year = (to_date - from_date).days + 1
        daily_interruptions = total_interruptions / days_in_year if days_in_year > 0 else 0
        
        return {
            "avg_supply": round(float(avg_supply), 2),
            "duration": round(float(avg_duration), 2),
            "turnaround_time": round(float(avg_turnaround), 2),
            "ftc": int(total_interruptions),
            "daily_interruptions": round(float(daily_interruptions), 2),
            "feeder_count": latest_summary.active_feeder_count,
            "peak_load": round(float(max_peak_load), 2),
            "customer_population": latest_summary.total_customer_count,
            "energy_delivered": round(float(total_energy), 2),
            "_source": "yearly_summary"
        }
        
    except Exception as e:
        print(f"DEBUG: Error getting yearly summaries for {district.name}: {str(e)}")
        return None


def _get_daily_summary_metrics_district(district, from_date, to_date, mode):
    """Get metrics from daily summaries for district"""
    # Collect all dates in the range
    dates = []
    current = from_date
    while current <= to_date:
        dates.append(current)
        current += timedelta(days=1)
    
    try:
        summaries = DailyTechnicalSummary.objects.filter(
            state=district.state,
            business_district=district,
            feeder__isnull=True,
            date__in=dates,
            has_complete_data=True
        )
        
        # Check if we have complete data
        if summaries.count() != len(dates):
            return None
        
        # Calculate aggregates
        avg_supply = summaries.aggregate(avg=Avg('hours_of_supply'))['avg'] or 0
        avg_duration = summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
        avg_turnaround = summaries.aggregate(avg=Avg('avg_fault_turnaround_time'))['avg'] or 0
        total_interruptions = summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
        total_energy = summaries.aggregate(total=Sum('total_energy_delivered'))['total'] or 0
        max_peak_load = summaries.aggregate(max=Max('max_peak_load'))['max'] or 0
        
        # Get infrastructure metrics from latest summary
        latest_summary = summaries.order_by('-date').first()
        
        # Calculate daily interruptions
        days_in_period = len(dates)
        daily_interruptions = total_interruptions / days_in_period if days_in_period > 0 else 0
        
        return {
            "avg_supply": round(float(avg_supply), 2),
            "duration": round(float(avg_duration), 2),
            "turnaround_time": round(float(avg_turnaround), 2),
            "ftc": int(total_interruptions),
            "daily_interruptions": round(float(daily_interruptions), 2),
            "feeder_count": latest_summary.active_feeder_count,
            "peak_load": round(float(max_peak_load), 2),
            "customer_population": latest_summary.total_customer_count,
            "energy_delivered": round(float(total_energy), 2),
            "_source": f"daily_summary_{mode}"
        }
        
    except Exception as e:
        print(f"DEBUG: Error getting daily summaries for {district.name}: {str(e)}")
        return None


def _calculate_district_metrics_realtime(district, from_date, to_date, mode):
    """Calculate district metrics in real-time when summary data is not available"""
    print(f"DEBUG: Calculating realtime metrics for district {district.name}, mode: {mode}")
    
    # Get all feeders in this business district
    feeders = Feeder.objects.filter(business_district=district)
    feeder_ids = list(feeders.values_list("id", flat=True))
    
    if not feeder_ids:
        return None
    
    # Calculate average hours of supply
    try:
        daily_supply_hours = DailyHoursOfSupply.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(from_date, to_date)
        ).aggregate(avg_hours=Avg("hours_supplied"))["avg_hours"]
        
        if daily_supply_hours is None:
            # Fallback to HourlyLoad calculation
            hours_of_supply = HourlyLoad.objects.filter(
                date__range=(from_date, to_date),
                feeder_id__in=feeder_ids,
                load_mw__gt=0
            ).values("feeder", "date").annotate(
                hours_count=Count("hour")
            ).aggregate(avg_hours=Avg("hours_count"))["avg_hours"] or 0
        else:
            hours_of_supply = daily_supply_hours
    except Exception:
        hours_of_supply = 0
    
    # Get all interruptions in the date range
    interruptions = FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        feeder_id__in=feeder_ids
    )
    
    # Calculate duration metrics (only for restored interruptions)
    restored_interruptions = interruptions.filter(restored_at__isnull=False)
    
    if restored_interruptions.exists():
        total_duration = sum(
            (interruption.restored_at - interruption.occurred_at).total_seconds() / 3600
            for interruption in restored_interruptions
        )
        interruption_count = restored_interruptions.count()
        avg_duration = round(total_duration / interruption_count, 2) if interruption_count else 0
    else:
        avg_duration = 0
    
    # Turnaround time calculation
    turnaround_time = avg_duration
    
    # Feeder Tripping Count (FTC)
    ftc = interruptions.count()
    
    # Get feeder count
    feeder_count = len(feeder_ids)
    
    # Calculate peak load for the district
    peak_load = HourlyLoad.objects.filter(
        feeder_id__in=feeder_ids,
        date__range=(from_date, to_date)
    ).aggregate(max_load=Max("load_mw"))["max_load"] or 0
    
    # Calculate customer population for this district
    customer_population = Customer.objects.filter(
        transformer__feeder__business_district=district
    ).count()
    
    # Calculate daily interruptions (average per day)
    date_range_days = (to_date - from_date).days + 1
    daily_interruptions = round(ftc / date_range_days, 2) if date_range_days > 0 else 0
    
    # Energy delivered for this district
    try:
        if mode == "monthly":
            # Try monthly aggregates first
            energy_delivered = FeederEnergyMonthly.objects.filter(
                feeder_id__in=feeder_ids,
                period__range=(from_date.replace(day=1), to_date.replace(day=1))
            ).aggregate(total_energy=Sum("energy_mwh"))["total_energy"] or 0
        else:
            # Use daily aggregation for other modes
            energy_delivered = FeederEnergyDaily.objects.filter(
                feeder_id__in=feeder_ids,
                date__range=(from_date, to_date)
            ).aggregate(total_energy=Sum("energy_mwh"))["total_energy"] or 0
            
        if energy_delivered == 0:
            # Fallback to daily aggregation
            energy_delivered = FeederEnergyDaily.objects.filter(
                feeder_id__in=feeder_ids,
                date__range=(from_date, to_date)
            ).aggregate(total_energy=Sum("energy_mwh"))["total_energy"] or 0
    except Exception as e:
        print(f"Error calculating energy delivered for {district.name}: {e}")
        energy_delivered = 0
    
    return {
        "avg_supply": round(float(hours_of_supply), 2),
        "duration": avg_duration,
        "turnaround_time": turnaround_time,
        "ftc": ftc,
        "daily_interruptions": daily_interruptions,
        "feeder_count": feeder_count,
        "peak_load": round(float(peak_load), 2),
        "customer_population": customer_population,
        "energy_delivered": round(float(energy_delivered), 2),
        "_source": f"realtime_{mode}"
    }


@api_view(["GET"])
def all_business_districts_technical_summary(request):
    """
    Enhanced technical summary for all business districts in a state supporting multiple modes:
    - monthly: Traditional month-based filtering using MonthlyTechnicalSummary
    - yearly: Year-based filtering using MonthlyTechnicalSummary (aggregated)
    - daily: Single day filtering using DailyTechnicalSummary
    - weekly: Week range filtering using DailyTechnicalSummary
    - custom: Custom date range filtering using DailyTechnicalSummary
    - range: Legacy range mode (same as custom)
    
    Query Parameters:
    - state: State name (required)
    - mode: monthly, yearly, daily, weekly, custom, range
    - For monthly: year, month
    - For yearly: year
    - For others: from_date, to_date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
    
    IMPORTANT: Response structure maintained for backward compatibility!
    
    Examples:
    - ?state=Lagos&mode=monthly&year=2024&month=8
    - ?state=Lagos&mode=yearly&year=2024
    - ?state=Lagos&mode=daily&from_date=2024-08-02T23:00:00.000Z&to_date=2024-08-02T23:00:00.000Z
    - ?state=Lagos&mode=weekly&from_date=2024-08-05T00:00:00.000Z&to_date=2024-08-11T23:59:59.999Z
    - ?state=Lagos&mode=custom&from_date=2024-08-01T00:00:00.000Z&to_date=2024-08-15T23:59:59.999Z
    """
    state = request.GET.get("state")
    if not state:
        return Response({"error": "State parameter is required"}, status=400)
    
    try:
        from_date, to_date, mode = get_date_range_and_mode(request)
    except ValueError as e:
        return Response({"error": str(e)}, status=400)
    
    # Debug logging
    print(f"DEBUG: Request params: {dict(request.GET)}")
    print(f"DEBUG: Calculated date range: {from_date} to {to_date}, mode: {mode}")
    
    # Check cache
    cache_key = _get_districts_cache_key(state, from_date, to_date, mode)
    cached_response = cache.get(cache_key)
    if cached_response:
        print("DEBUG: Returning cached response")
        return Response(cached_response)
    
    # Get all business districts in the state that have feeders
    districts = BusinessDistrict.objects.filter(
        state__name__iexact=state,
        feeders__isnull=False  # Only districts that have feeders
    ).distinct().order_by('name')
    
    print(f"DEBUG: Found {districts.count()} districts with feeders in {state}")
    
    response_data = []
    
    for district in districts:
        print(f"DEBUG: Processing district: {district.name}")
        try:
            # Try to use summary data first
            district_metrics = _get_district_metrics_from_summary(district, from_date, to_date, mode)
            
            if not district_metrics:
                # Fallback to real-time calculation
                district_metrics = _calculate_district_metrics_realtime(district, from_date, to_date, mode)
            
            if district_metrics:
                # Add FTC per feeder calculation
                ftc_per_feeder = round(district_metrics["ftc"] / district_metrics["feeder_count"], 2) if district_metrics["feeder_count"] > 0 else 0
                district_metrics["ftc_per_feeder"] = ftc_per_feeder
                
                response_data.append({
                    "district": district.name,
                    "metrics": district_metrics
                })
                print(f"DEBUG: Added {district.name} to response with source: {district_metrics.get('_source', 'unknown')}")
            else:
                print(f"DEBUG: No metrics found for {district.name}")
                
        except Exception as e:
            print(f"ERROR: Error calculating metrics for district {district.name}: {str(e)}")
            import traceback
            traceback.print_exc()
            continue
    
    # MAINTAIN ORIGINAL RESPONSE STRUCTURE
    final_response = {
        "districts": response_data
    }
    
    print(f"DEBUG: Final response has {len(response_data)} districts")
    
    # Cache for different durations based on mode and whether it includes current data
    today = datetime.now().date()
    if to_date >= today:
        cache_timeout = 300  # 5 minutes for current data
    else:
        cache_timeout = 1800  # 30 minutes for historical data
    
    cache.set(cache_key, final_response, cache_timeout)
    print(f"DEBUG: Cached response with key: {cache_key} for {cache_timeout} seconds")
    
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