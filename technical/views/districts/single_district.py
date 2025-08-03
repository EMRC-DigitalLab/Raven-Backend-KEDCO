from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Avg, Count, Max, Sum
from django.core.cache import cache
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import hashlib
from analytics.models import MonthlyTechnicalSummary, DailyTechnicalSummary
from technical.models import HourlyLoad, FeederInterruption
from common.models import Feeder

def _get_day_label_for_period(date_obj, index):
    """Get day label for historical periods"""
    # Return day name: Mon, Tue, Wed, etc.
    return date_obj.strftime("%a")


def _get_week_label_for_period(index):
    """Get week label for historical periods"""
    return f"Wk{5-index}"


def _get_custom_label_for_period(index):
    """Get custom period label for historical periods"""
    if index == 1:
        return "Previous"
    else:
        return f"P{5-index}"


def _get_year_label_for_period(year):
    """Get year label for historical periods"""
    return str(year)


def _get_month_label_for_period(date_obj):
    """Get month label for historical periods"""
    return date_obj.strftime("%b")  # Jan, Feb, Mar, etc.


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


def get_month_range(year, month):
    """Legacy function maintained for backward compatibility"""
    start = datetime(year, month, 1)
    end = (start + relativedelta(months=1)) - timedelta(days=1)
    return start.date(), end.date()


def delta(current, previous):
    """Calculate percentage delta between current and previous values"""
    if previous == 0:
        return 0
    return round(((current - previous) / previous) * 100, 2)


def _get_district_cache_key(district_name, from_date, to_date, mode):
    """Generate cache key for district technical summary"""
    cache_str = f"district_tech_{district_name}_{mode}_{from_date}_{to_date}"
    return hashlib.md5(cache_str.encode()).hexdigest()[:16]


def get_metric_with_history_enhanced(calc_fn, feeder_ids, from_date, to_date, mode):
    """Enhanced metric calculation with history support for all modes"""
    
    if mode == "monthly":
        return get_metric_with_history_monthly(calc_fn, feeder_ids, from_date.year, from_date.month)
    elif mode == "yearly":
        return get_metric_with_history_yearly(calc_fn, feeder_ids, from_date.year)
    else:
        return get_metric_with_history_daily_range(calc_fn, feeder_ids, from_date, to_date, mode)


def get_metric_with_history_monthly(calc_fn, feeder_ids, year, month):
    """FIXED: Monthly history calculation with proper labels"""
    history = []
    for i in range(4, 0, -1):
        dt = datetime(year, month, 1) - relativedelta(months=i)
        start, end = get_month_range(dt.year, dt.month)
        val = calc_fn(start, end, feeder_ids)
        history.append({
            "month": _get_month_label_for_period(dt),  # Jan, Feb, Mar, etc.
            "value": round(val, 2)
        })

    current_start, current_end = get_month_range(year, month)
    current = calc_fn(current_start, current_end, feeder_ids)

    prev_month = datetime(year, month, 1) - relativedelta(months=1)
    prev_start, prev_end = get_month_range(prev_month.year, prev_month.month)
    previous = calc_fn(prev_start, prev_end, feeder_ids)

    return {
        "current": round(current, 2),
        "delta": delta(current, previous),
        "history": history,  # Now contains objects with month labels
    }


def get_metric_with_history_yearly(calc_fn, feeder_ids, year):
    """FIXED: Yearly history calculation with proper labels"""
    history = []
    for i in range(4, 0, -1):
        prev_year = year - i
        start = datetime(prev_year, 1, 1).date()
        end = datetime(prev_year, 12, 31).date()
        val = calc_fn(start, end, feeder_ids)
        history.append({
            "month": _get_year_label_for_period(prev_year),  # "2020", "2021", etc.
            "value": round(val, 2)
        })

    # Current year
    current_start = datetime(year, 1, 1).date()
    current_end = datetime(year, 12, 31).date()
    current = calc_fn(current_start, current_end, feeder_ids)

    # Previous year
    prev_year = year - 1
    prev_start = datetime(prev_year, 1, 1).date()
    prev_end = datetime(prev_year, 12, 31).date()
    previous = calc_fn(prev_start, prev_end, feeder_ids)

    return {
        "current": round(current, 2),
        "delta": delta(current, previous),
        "history": history,  # Now contains objects with year labels
    }


def get_metric_with_history_daily_range(calc_fn, feeder_ids, from_date, to_date, mode):
    """FIXED: Daily range history calculation with proper labels"""
    period_length = (to_date - from_date).days + 1
    history = []
    
    # Calculate 4 previous periods
    for i in range(4, 0, -1):
        if mode == "daily":
            # For daily mode, go back by individual days
            hist_date = from_date - timedelta(days=i)
            hist_start = hist_end = hist_date
            period_label = _get_day_label_for_period(hist_date, i)
        elif mode == "weekly":
            # For weekly mode, go back by weeks
            hist_end = from_date - timedelta(days=i * period_length)
            hist_start = hist_end - timedelta(days=period_length - 1)
            period_label = _get_week_label_for_period(i)
        else:
            # For custom mode, go back by period length
            hist_end = from_date - timedelta(days=i * period_length)
            hist_start = hist_end - timedelta(days=period_length - 1)
            period_label = _get_custom_label_for_period(i)
        
        val = calc_fn(hist_start, hist_end, feeder_ids)
        history.append({
            "month": period_label,  # Keep "month" for compatibility
            "value": round(val, 2)
        })

    # Current period
    current = calc_fn(from_date, to_date, feeder_ids)

    # Previous period
    if mode == "daily":
        prev_date = from_date - timedelta(days=1)
        prev_start = prev_end = prev_date
    else:
        prev_end = from_date - timedelta(days=period_length)
        prev_start = prev_end - timedelta(days=period_length - 1)
    
    previous = calc_fn(prev_start, prev_end, feeder_ids)

    return {
        "current": round(current, 2),
        "delta": delta(current, previous),
        "history": history,  # Now contains objects with proper labels
    }


def _get_district_metrics_from_summary(district_name, from_date, to_date, mode, feeder_ids):
    """Get district metrics from pre-calculated summary data based on mode"""
    try:
        from common.models import BusinessDistrict
        district = BusinessDistrict.objects.get(name=district_name)
        
        if mode == "monthly":
            return _get_monthly_summary_metrics_single_district(district, from_date, feeder_ids)
        elif mode == "yearly":
            return _get_yearly_summary_metrics_single_district(district, from_date, feeder_ids)
        else:
            return _get_daily_summary_metrics_single_district(district, from_date, to_date, mode, feeder_ids)
            
    except Exception as e:
        print(f"DEBUG: Error getting summary data for {district_name}: {str(e)}")
        return None


def _get_monthly_summary_metrics_single_district(district, from_date, feeder_ids):
    """Get metrics from monthly summaries with proper history labels"""
    target_month = from_date
    
    try:
        summary = MonthlyTechnicalSummary.objects.get(
            state=district.state,
            business_district=district,
            feeder__isnull=True,
            month=target_month,
            has_complete_data=True
        )
        
        # Get historical data (4 previous months) from summaries
        history_months = []
        for i in range(1, 5):
            hist_month = target_month - relativedelta(months=i)
            history_months.append(hist_month)
        
        historical_summaries = MonthlyTechnicalSummary.objects.filter(
            state=district.state,
            business_district=district,
            feeder__isnull=True,
            month__in=history_months,
            has_complete_data=True
        ).order_by('month')
        
        # Build history data with proper labels
        history_data = []
        for hist_summary in historical_summaries:
            history_data.append({
                "month": _get_month_label_for_period(hist_summary.month),  # Jan, Feb, etc.
                "avg_supply": float(hist_summary.avg_hours_of_supply),
                "duration": float(hist_summary.avg_interruption_duration),
                "interruptions": float(hist_summary.avg_daily_interruptions),
                "faults": hist_summary.total_interruptions,
                "feeder_count": hist_summary.active_feeder_count,
            })
        
        # Calculate deltas (current vs previous month)
        previous_month = target_month - relativedelta(months=1)
        try:
            prev_summary = MonthlyTechnicalSummary.objects.get(
                state=district.state,
                business_district=district,
                feeder__isnull=True,
                month=previous_month,
                has_complete_data=True
            )
            prev_data = {
                'avg_supply': float(prev_summary.avg_hours_of_supply),
                'duration': float(prev_summary.avg_interruption_duration),
                'interruptions': float(prev_summary.avg_daily_interruptions),
                'faults': prev_summary.total_interruptions,
                'feeder_count': prev_summary.active_feeder_count,
            }
        except MonthlyTechnicalSummary.DoesNotExist:
            prev_data = {}
        
        return {
            "avg_supply": {
                "current": round(float(summary.avg_hours_of_supply), 2),
                "delta": delta(float(summary.avg_hours_of_supply), prev_data.get('avg_supply', 0)),
                "history": history_data,  # Now contains objects with month labels
            },
            "duration": {
                "current": round(float(summary.avg_interruption_duration), 2),
                "delta": delta(float(summary.avg_interruption_duration), prev_data.get('duration', 0)),
                "history": history_data,  # Shared history data
            },
            "turnaround_time": {
                "current": round(float(summary.avg_fault_turnaround_time), 2),
                "delta": delta(float(summary.avg_fault_turnaround_time), prev_data.get('duration', 0)),
                "history": history_data,  # Shared history data
            },
            "interruptions": {
                "current": round(float(summary.avg_daily_interruptions), 2),
                "delta": delta(float(summary.avg_daily_interruptions), prev_data.get('interruptions', 0)),
                "history": history_data,  # Shared history data
            },
            "faults": {
                "current": summary.total_interruptions,
                "delta": delta(summary.total_interruptions, prev_data.get('faults', 0)),
                "history": history_data,  # Shared history data
            },
            "feeder_count": {
                "current": summary.active_feeder_count,
                "delta": delta(summary.active_feeder_count, prev_data.get('feeder_count', 0)),
                "history": history_data,  # Shared history data
            },
            "_source": "monthly_summary"
        }
        
    except MonthlyTechnicalSummary.DoesNotExist:
        return None


def _get_yearly_summary_metrics_single_district(district, from_date, feeder_ids):
    """FIXED: Get yearly metrics with proper history labels"""
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
        avg_daily_interruptions = year_summaries.aggregate(avg=Avg('avg_daily_interruptions'))['avg'] or 0
        
        # Get infrastructure metrics from latest summary
        latest_summary = year_summaries.order_by('-month').first()
        feeder_count = latest_summary.active_feeder_count if latest_summary else len(feeder_ids)
        
        # Create monthly breakdown data for current year
        monthly_history = []
        for summary in year_summaries:
            monthly_history.append({
                "month": _get_month_label_for_period(summary.month),  # Jan, Feb, etc.
                "avg_supply": float(summary.avg_hours_of_supply),
                "duration": float(summary.avg_interruption_duration),
                "interruptions": float(summary.avg_daily_interruptions),
                "faults": summary.total_interruptions,
                "feeder_count": summary.active_feeder_count,
            })
        
        # Calculate historical years (4 previous years)
        yearly_history = []
        for i in range(1, 5):
            prev_year = target_year - i
            prev_year_months = []
            for month in range(1, 13):
                prev_year_months.append(datetime(prev_year, month, 1).date())
            
            prev_summaries = MonthlyTechnicalSummary.objects.filter(
                state=district.state,
                business_district=district,
                feeder__isnull=True,
                month__in=prev_year_months,
                has_complete_data=True
            )
            
            if prev_summaries.count() == 12:  # Complete year
                prev_total_interruptions = prev_summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
                prev_avg_supply = prev_summaries.aggregate(avg=Avg('avg_hours_of_supply'))['avg'] or 0
                prev_avg_duration = prev_summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
                prev_avg_daily_interruptions = prev_summaries.aggregate(avg=Avg('avg_daily_interruptions'))['avg'] or 0
                prev_latest = prev_summaries.order_by('-month').first()
                prev_feeder_count = prev_latest.active_feeder_count if prev_latest else len(feeder_ids)
                
                yearly_history.append({
                    "month": _get_year_label_for_period(prev_year),  # "2020", "2021", etc.
                    "avg_supply": float(prev_avg_supply),
                    "duration": float(prev_avg_duration),
                    "interruptions": float(prev_avg_daily_interruptions),
                    "faults": int(prev_total_interruptions),
                    "feeder_count": prev_feeder_count,
                })
        
        # Reverse yearly history to get chronological order
        yearly_history.reverse()
        
        # Combine yearly and monthly history
        combined_history = yearly_history + monthly_history
        
        # Calculate deltas (current year vs previous year)
        if yearly_history:
            prev_data = yearly_history[-1]
        else:
            prev_data = {}
        
        return {
            "avg_supply": {
                "current": round(float(avg_supply), 2),
                "delta": delta(float(avg_supply), prev_data.get('avg_supply', 0)),
                "history": combined_history,  # Now contains objects with proper labels
            },
            "duration": {
                "current": round(float(avg_duration), 2),
                "delta": delta(float(avg_duration), prev_data.get('duration', 0)),
                "history": combined_history,  # Shared history data
            },
            "turnaround_time": {
                "current": round(float(avg_turnaround), 2),
                "delta": delta(float(avg_turnaround), prev_data.get('duration', 0)),
                "history": combined_history,  # Shared history data
            },
            "interruptions": {
                "current": round(float(avg_daily_interruptions), 2),
                "delta": delta(float(avg_daily_interruptions), prev_data.get('interruptions', 0)),
                "history": combined_history,  # Shared history data
            },
            "faults": {
                "current": int(total_interruptions),
                "delta": delta(total_interruptions, prev_data.get('faults', 0)),
                "history": combined_history,  # Shared history data
            },
            "feeder_count": {
                "current": feeder_count,
                "delta": delta(feeder_count, prev_data.get('feeder_count', 0)),
                "history": combined_history,  # Shared history data
            },
            "_source": "yearly_summary"
        }
        
    except Exception as e:
        print(f"DEBUG: Error getting yearly summaries for {district.name}: {str(e)}")
        return None


def _get_daily_summary_metrics_single_district(district, from_date, to_date, mode, feeder_ids):
    """FIXED: Get metrics from daily summaries with proper history labels"""
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
        
        # Calculate current period aggregates
        if mode == "daily":
            # For single day, just use the summary directly
            current_summary = summaries.first()
            current_avg_supply = float(current_summary.hours_of_supply)
            current_avg_duration = float(current_summary.avg_interruption_duration)
            current_avg_turnaround = float(current_summary.avg_fault_turnaround_time)
            current_total_interruptions = current_summary.total_interruptions
            current_daily_interruptions = float(current_summary.total_interruptions)
            current_feeder_count = current_summary.active_feeder_count
        else:
            # For weekly/custom, aggregate the summaries
            current_avg_supply = summaries.aggregate(avg=Avg('hours_of_supply'))['avg'] or 0
            current_avg_duration = summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
            current_avg_turnaround = summaries.aggregate(avg=Avg('avg_fault_turnaround_time'))['avg'] or 0
            current_total_interruptions = summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
            current_daily_interruptions = current_total_interruptions / len(dates) if len(dates) > 0 else 0
            latest_summary = summaries.order_by('-date').first()
            current_feeder_count = latest_summary.active_feeder_count if latest_summary else len(feeder_ids)
        
        # Calculate historical periods with proper labels
        period_length = (to_date - from_date).days + 1
        history_data = []
        
        for i in range(1, 5):
            if mode == "daily":
                # For daily mode, go back by individual days
                hist_date = from_date - timedelta(days=i)
                hist_start = hist_end = hist_date
                hist_dates = [hist_date]
                period_label = _get_day_label_for_period(hist_date, i)
            elif mode == "weekly":
                # For weekly mode, go back by weeks
                hist_end = from_date - timedelta(days=i * period_length)
                hist_start = hist_end - timedelta(days=period_length - 1)
                hist_dates = []
                current_date = hist_start
                while current_date <= hist_end:
                    hist_dates.append(current_date)
                    current_date += timedelta(days=1)
                period_label = _get_week_label_for_period(i)
            else:
                # For custom mode, go back by period length
                hist_end = from_date - timedelta(days=i * period_length)
                hist_start = hist_end - timedelta(days=period_length - 1)
                hist_dates = []
                current_date = hist_start
                while current_date <= hist_end:
                    hist_dates.append(current_date)
                    current_date += timedelta(days=1)
                period_label = _get_custom_label_for_period(i)
            
            hist_summaries = DailyTechnicalSummary.objects.filter(
                state=district.state,
                business_district=district,
                feeder__isnull=True,
                date__in=hist_dates,
                has_complete_data=True
            )
            
            if hist_summaries.count() == len(hist_dates):
                if mode == "daily":
                    hist_summary = hist_summaries.first()
                    hist_avg_supply = float(hist_summary.hours_of_supply)
                    hist_avg_duration = float(hist_summary.avg_interruption_duration)
                    hist_total_interruptions = hist_summary.total_interruptions
                    hist_daily_interruptions = float(hist_summary.total_interruptions)
                    hist_feeder_count = hist_summary.active_feeder_count
                else:
                    hist_avg_supply = hist_summaries.aggregate(avg=Avg('hours_of_supply'))['avg'] or 0
                    hist_avg_duration = hist_summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
                    hist_total_interruptions = hist_summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
                    hist_daily_interruptions = hist_total_interruptions / len(hist_dates) if len(hist_dates) > 0 else 0
                    hist_latest = hist_summaries.order_by('-date').first()
                    hist_feeder_count = hist_latest.active_feeder_count if hist_latest else len(feeder_ids)
                
                history_data.append({
                    "month": period_label,  # Keep "month" for compatibility
                    "avg_supply": float(hist_avg_supply),
                    "duration": float(hist_avg_duration),
                    "interruptions": float(hist_daily_interruptions),
                    "faults": int(hist_total_interruptions),
                    "feeder_count": hist_feeder_count,
                })
        
        # Reverse to get chronological order
        history_data.reverse()
        
        # Calculate deltas (current vs previous period)
        if history_data:
            prev_data = history_data[-1]
        else:
            prev_data = {}
        
        return {
            "avg_supply": {
                "current": round(float(current_avg_supply), 2),
                "delta": delta(float(current_avg_supply), prev_data.get('avg_supply', 0)),
                "history": history_data,  # Now contains objects with proper labels
            },
            "duration": {
                "current": round(float(current_avg_duration), 2),
                "delta": delta(float(current_avg_duration), prev_data.get('duration', 0)),
                "history": history_data,  # Shared history data
            },
            "turnaround_time": {
                "current": round(float(current_avg_turnaround), 2),
                "delta": delta(float(current_avg_turnaround), prev_data.get('duration', 0)),
                "history": history_data,  # Shared history data
            },
            "interruptions": {
                "current": round(float(current_daily_interruptions), 2),
                "delta": delta(float(current_daily_interruptions), prev_data.get('interruptions', 0)),
                "history": history_data,  # Shared history data
            },
            "faults": {
                "current": int(current_total_interruptions),
                "delta": delta(current_total_interruptions, prev_data.get('faults', 0)),
                "history": history_data,  # Shared history data
            },
            "feeder_count": {
                "current": current_feeder_count,
                "delta": delta(current_feeder_count, prev_data.get('feeder_count', 0)),
                "history": history_data,  # Shared history data
            },
            "_source": f"daily_summary_{mode}"
        }
        
    except Exception as e:
        print(f"DEBUG: Error getting daily summaries for {district.name}: {str(e)}")
        return None


# Original calculation functions maintained for backward compatibility
def calculate_avg_supply(from_date, to_date, feeder_ids):
    hours = HourlyLoad.objects.filter(
        date__range=(from_date, to_date), feeder_id__in=feeder_ids, load_mw__gt=0
    ).values("feeder", "date").annotate(hour_count=Count("hour")).aggregate(avg=Avg("hour_count"))
    return hours["avg"] or 0


def calculate_avg_interruption_duration(from_date, to_date, feeder_ids):
    interruptions = FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        restored_at__isnull=False,
        feeder_id__in=feeder_ids
    )
    total_duration = sum(i.duration_hours for i in interruptions)
    return total_duration / interruptions.count() if interruptions.exists() else 0


def calculate_avg_interruptions(from_date, to_date, feeder_ids):
    days = (to_date - from_date).days or 1
    total = FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        feeder_id__in=feeder_ids
    ).count()
    return total / days


def calculate_faults(from_date, to_date, feeder_ids):
    return FeederInterruption.objects.filter(
        occurred_at__date__range=(from_date, to_date),
        feeder_id__in=feeder_ids
    ).count()


def calculate_feeder_count(_, __, feeder_ids):
    return len(feeder_ids)


@api_view(["GET"])
def business_district_technical_summary(request):
    """
    Enhanced technical summary for a specific business district supporting multiple modes:
    - monthly: Traditional month-based filtering using MonthlyTechnicalSummary
    - yearly: Year-based filtering using MonthlyTechnicalSummary (aggregated)
    - daily: Single day filtering using DailyTechnicalSummary
    - weekly: Week range filtering using DailyTechnicalSummary
    - custom: Custom date range filtering using DailyTechnicalSummary
    - range: Legacy range mode (same as custom)
    
    Query Parameters:
    - district: Business district name (required)
    - mode: monthly, yearly, daily, weekly, custom, range
    - For monthly: year, month
    - For yearly: year
    - For others: from_date, to_date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
    
    IMPORTANT: Response structure maintained for backward compatibility!
    
    Examples:
    - ?district=Ikeja&mode=monthly&year=2024&month=8
    - ?district=Ikeja&mode=yearly&year=2024
    - ?district=Ikeja&mode=daily&from_date=2024-08-02T23:00:00.000Z&to_date=2024-08-02T23:00:00.000Z
    - ?district=Ikeja&mode=weekly&from_date=2024-08-05T00:00:00.000Z&to_date=2024-08-11T23:59:59.999Z
    - ?district=Ikeja&mode=custom&from_date=2024-08-01T00:00:00.000Z&to_date=2024-08-15T23:59:59.999Z
    """
    district = request.GET.get("district")
    if not district:
        return Response({"error": "District parameter is required"}, status=400)
    
    try:
        from_date, to_date, mode = get_date_range_and_mode_from_request(request)
    except ValueError as e:
        return Response({"error": str(e)}, status=400)
    
    # For backward compatibility - extract year and month for legacy functions
    if mode == "monthly":
        year = from_date.year
        month = from_date.month
    else:
        year = from_date.year
        month = from_date.month
    
    # Check cache
    cache_key = _get_district_cache_key(district, from_date, to_date, mode)
    cached_response = cache.get(cache_key)
    if cached_response:
        return Response(cached_response)
    
    feeders = Feeder.objects.filter(business_district__name=district)
    feeder_ids = list(feeders.values_list("id", flat=True))
    
    if not feeder_ids:
        return Response({"error": f"No feeders found for district {district}"}, status=404)
    
    # Try to get metrics from summary data first
    summary_metrics = _get_district_metrics_from_summary(district, from_date, to_date, mode, feeder_ids)
    
    if summary_metrics:
        # Use summary data
        metrics = summary_metrics
    else:
        # Fallback to real-time calculation using original functions
        print(f"DEBUG: Using real-time calculation for district {district}, mode: {mode}")
        metrics = {
            "avg_supply": get_metric_with_history_enhanced(calculate_avg_supply, feeder_ids, from_date, to_date, mode),
            "duration": get_metric_with_history_enhanced(calculate_avg_interruption_duration, feeder_ids, from_date, to_date, mode),
            "turnaround_time": get_metric_with_history_enhanced(calculate_avg_interruption_duration, feeder_ids, from_date, to_date, mode),
            "interruptions": get_metric_with_history_enhanced(calculate_avg_interruptions, feeder_ids, from_date, to_date, mode),
            "faults": get_metric_with_history_enhanced(calculate_faults, feeder_ids, from_date, to_date, mode),
            "feeder_count": get_metric_with_history_enhanced(calculate_feeder_count, feeder_ids, from_date, to_date, mode),
            "_source": f"realtime_{mode}"
        }
    
    # Top & Bottom Peak Feeders calculation (always real-time)
    peak_queryset = HourlyLoad.objects.filter(
        date__range=(from_date, to_date),
        feeder_id__in=feeder_ids
    ).values(
        "feeder__name", "feeder__voltage_level"
    ).annotate(peak=Max("load_mw")).order_by("-peak")
    
    top_feeders = [
        {
            "feeder": obj["feeder__name"],
            "voltage_level": obj["feeder__voltage_level"],
            "peak": round(float(obj["peak"] or 0), 2)
        } for obj in peak_queryset[:5]
    ]
    
    bottom_feeders = [
        {
            "feeder": obj["feeder__name"],
            "voltage_level": obj["feeder__voltage_level"],
            "peak": round(float(obj["peak"] or 0), 2)
        } for obj in list(peak_queryset.reverse())[:5]
    ]
    
    # MAINTAIN ORIGINAL RESPONSE STRUCTURE
    response_data = {
        "metrics": metrics,
        "top_feeders": top_feeders,
        "bottom_feeders": bottom_feeders
    }
    
    # Cache for different durations based on mode and whether it includes current data
    today = datetime.now().date()
    if to_date >= today:
        cache_timeout = 300  # 5 minutes for current data
    else:
        cache_timeout = 1800  # 30 minutes for historical data
    
    cache.set(cache_key, response_data, cache_timeout)
    
    return Response(response_data)