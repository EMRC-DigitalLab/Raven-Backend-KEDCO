from rest_framework.views import APIView
from rest_framework.response import Response
from django.core.cache import cache
from django.db.models import Sum, Avg, Max, Count
from django.db.models.functions import Extract
from django.utils import timezone
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
import logging
import hashlib
from calendar import monthrange

from analytics.models import MonthlyTechnicalSummary, DailyTechnicalSummary
from common.models import State, BusinessDistrict, Feeder
from technical.models import HourlyLoad
from technical.constants import LOAD_SHEDDING_TYPES, TCN_TYPES

logger = logging.getLogger(__name__)


def fix_metrics_to_sum_24(data_dict, mode='daily', period_info=None):
    """
    Ensure avg_hours_supply + avg_interruption_duration + avg_turnaround_time = 24.
    Also recalculates energy_delivered using Average Load × Supply Hours formula.
    
    SIMPLE APPROACH:
    - Keep avg_hours_supply as-is (from hourly load data)
    - Keep avg_interruption_duration as-is (L/S + TCN hours)
    - Derive avg_turnaround_time = 24 - supply - interruption_duration
    - Recalculate energy_delivered = average_load × total_supply_hours
    
    This mathematically guarantees the three metrics sum to 24.
    """
    if not data_dict:
        return data_dict
    
    # Get hours of supply (time power is ON)
    supply_hours = float(data_dict.get('avg_hours_supply', 0))
    supply_hours = min(max(supply_hours, 0), 24)  # Clamp to 0-24
    
    # Get interruption duration (external outage hours)
    interruption_duration = float(data_dict.get('avg_interruption_duration', 0))
    interruption_duration = min(max(interruption_duration, 0), 24)  # Clamp to 0-24
    
    # Cap interruption at (24 - supply) to avoid negative turnaround
    max_interruption = 24 - supply_hours
    interruption_duration = min(interruption_duration, max_interruption)
    
    # Derive turnaround time (local fault hours) = 24 - supply - interruption
    # This GUARANTEES: supply + interruption + turnaround = 24
    turnaround_time = 24 - supply_hours - interruption_duration
    turnaround_time = max(turnaround_time, 0)  # Ensure non-negative
    
    # Update the metrics
    data_dict['avg_interruption_duration'] = round(interruption_duration, 2)
    data_dict['avg_turnaround_time'] = round(turnaround_time, 2)
    data_dict['avg_fault_turnaround_time'] = round(turnaround_time, 2)
    
    # =====================================================================
    # Recalculate Energy Delivered = Average Load × Total Supply Hours
    # =====================================================================
    avg_load = float(data_dict.get('average_load', 0))
    
    if mode == 'daily':
        # For daily mode: energy = avg_load × hours_of_supply
        total_supply_hours = supply_hours
    elif mode == 'monthly' and period_info:
        # For monthly mode: estimate total supply hours for the month
        import calendar
        if hasattr(period_info, 'year') and hasattr(period_info, 'month'):
            days_in_month = calendar.monthrange(period_info.year, period_info.month)[1]
        else:
            days_in_month = 30  # Approximate
        total_supply_hours = supply_hours * days_in_month
    elif mode == 'yearly':
        # For yearly mode: estimate total supply hours for the year
        total_supply_hours = supply_hours * 365
    else:
        # Default: estimate based on days in period
        total_supply_hours = supply_hours * 30  # Approximate month
    
    calculated_energy = round(avg_load * total_supply_hours, 2)
    data_dict['energy_delivered'] = calculated_energy
    
    return data_dict



class OptimizedTechnicalOverviewAPIView(APIView):
    """
    Fixed technical overview API using pre-calculated summary data.
    Properly aggregates data across multiple entities and handles filtering correctly.
    
    Supported Query Parameters:
    - mode: 'monthly', 'yearly', 'daily', 'weekly', 'custom' (auto-detected if not provided)
    - year: Year for monthly/yearly modes (default: current year)
    - month: Month for monthly mode (default: current month)
    - from_date: Start date for daily/weekly/custom modes (ISO format: YYYY-MM-DD)
    - to_date: End date for daily/weekly/custom modes (ISO format: YYYY-MM-DD)
    
    Geographic Filters (hierarchical):
    - state: State name (case-insensitive)
    - district: Business district name (case-insensitive)
    - feeder: Feeder slug (exact match)
    
    Examples:
    - National overview: /api/technical/overview/
    - State level: /api/technical/overview/?state=Lagos
    - District level: /api/technical/overview/?state=Lagos&district=Ikeja
    - Specific feeder: /api/technical/overview/?feeder=ikeja-feeder-01
    - Monthly view: /api/technical/overview/?mode=monthly&year=2024&month=8
    - Daily view: /api/technical/overview/?mode=daily&from_date=2024-08-06&to_date=2024-08-06
    - Weekly view: /api/technical/overview/?mode=weekly&from_date=2024-08-05&to_date=2024-08-11
    
    Response includes:
    - highlight_metrics: Key performance indicators with deltas
    - supply_and_quality: Supply hours, interruption duration, turnaround time with history
    - technical_breakdown: Detailed technical metrics
    - interruption_sources: Breakdown of interruption causes
    - load_trend: Time-series load data (always included, varies by mode)
    """
    
    def get(self, request):
        # Parse mode and date parameters
        mode = request.GET.get('mode')
        
        # Auto-detect mode based on date parameters if not explicitly set
        if not mode and (request.GET.get('from_date') or request.GET.get('to_date')):
            mode = self._determine_mode_from_dates(request)
        elif not mode:
            mode = 'monthly'  # Default mode
        
        # Parse filtering parameters
        filter_params = self._parse_filter_params(request)
        
        # Parse dates based on mode
        date_info = self._parse_dates_by_mode(request, mode)
        
        # Try cache first - REMOVED load trend date condition since we always return it
        cache_key = self._get_cache_key(mode, date_info, filter_params)
        cached_response = cache.get(cache_key)
        
        # if cached_response:
        #     logger.debug(f"Returning cached technical data for mode: {mode}")
        #     # Always get fresh load trend data (since it's fast and we want it current)
        #     additional_data = self._get_additional_data(request, filter_params)
        #     cached_response["load_trend"] = additional_data.get("load_trend", {"series": [], "date": None})
        #     return Response(cached_response)
        
        # Fetch data based on mode
        if mode == 'monthly':
            overview_data = self._fetch_monthly_data(date_info['periods'], filter_params)
        elif mode == 'yearly':
            overview_data = self._fetch_yearly_data(date_info['periods'], filter_params)
        else:
            overview_data = self._fetch_daily_data(date_info['periods'], filter_params, mode)
        
        # Get additional data (load trend, etc.) - now always included
        additional_data = self._get_additional_data(request, filter_params)
        
        # Format response
        response_data = self._format_response_data(overview_data, additional_data, mode, date_info)
        
        # Cache response (shorter cache for current periods)
        # cache_timeout = 300 if self._is_current_period(date_info) else 1800
        cache_timeout = 1 if self._is_current_period(date_info) else 1
        # Cache without load_trend since we always get it fresh
        cached_data = response_data.copy()
        cached_data.pop("load_trend", None)  # Remove load_trend before caching
        cache.set(cache_key, cached_data, cache_timeout)
        
        return Response(response_data)
    
    def _determine_mode_from_dates(self, request):
        """Determine mode based on date range if not explicitly provided"""
        from_date_str = request.GET.get("from_date")
        to_date_str = request.GET.get("to_date")
        
        if not from_date_str or not to_date_str:
            return 'daily'
        
        try:
            from_date = self._parse_iso_date(from_date_str)
            to_date = self._parse_iso_date(to_date_str)
            
            diff_days = (to_date - from_date).days + 1
            
            if diff_days == 1:
                return 'daily'
            elif diff_days == 7:
                return 'weekly'
            elif diff_days >= 28 and diff_days <= 31:
                # Check if it's a full month
                if (from_date.day == 1 and 
                    to_date == (from_date.replace(month=from_date.month+1 if from_date.month < 12 else 1, 
                                                year=from_date.year if from_date.month < 12 else from_date.year+1, day=1) - timedelta(days=1))):
                    return 'monthly'
                else:
                    return 'custom'
            elif diff_days >= 365 and diff_days <= 366:
                # Check if it's a full year (365 or 366 days for leap year)
                if (from_date.month == 1 and from_date.day == 1 and 
                    to_date.month == 12 and to_date.day == 31 and 
                    from_date.year == to_date.year):
                    return 'yearly'
                else:
                    return 'custom'
            else:
                return 'custom'
        except:
            return 'daily'
    
    def _parse_iso_date(self, date_str):
        """Parse ISO datetime string to date"""
        try:
            # Handle ISO format like "2024-08-01T23:00:00.000Z"
            if 'T' in date_str:
                dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                return dt.date()
            else:
                # Handle simple date format
                return datetime.strptime(date_str, '%Y-%m-%d').date()
        except:
            # Fallback to today
            return date.today()
    
    def _parse_filter_params(self, request):
        """Parse and validate filtering parameters"""
        filter_params = {
            'state': None,
            'business_district': None,
            'feeder': None
        }
        
        # Parse state filter
        state_name = request.GET.get('state')
        if state_name:
            try:
                filter_params['state'] = State.objects.get(name__iexact=state_name)
                logger.debug(f"State filter applied: {filter_params['state'].name}")
            except State.DoesNotExist:
                logger.warning(f"Invalid state name provided: {state_name}")
                pass  # Ignore invalid state names
        
        # Parse district filter
        district_name = request.GET.get('district')
        if district_name:
            try:
                qs = BusinessDistrict.objects.filter(name__iexact=district_name)
                if filter_params['state']:
                    qs = qs.filter(state=filter_params['state'])
                filter_params['business_district'] = qs.first()
                if filter_params['business_district']:
                    logger.debug(f"Business district filter applied: {filter_params['business_district'].name}")
                else:
                    logger.warning(f"Business district not found: {district_name}")
            except Exception as e:
                logger.warning(f"Error parsing district filter: {e}")
                pass
        
        # Parse feeder filter by slug
        feeder_slug = request.GET.get('feeder')
        if feeder_slug:
            try:
                qs = Feeder.objects.filter(slug=feeder_slug)
                
                # Apply hierarchical filtering to ensure consistency
                if filter_params['business_district']:
                    qs = qs.filter(business_district=filter_params['business_district'])
                elif filter_params['state']:
                    qs = qs.filter(business_district__state=filter_params['state'])
                
                filter_params['feeder'] = qs.first()
                
                if filter_params['feeder']:
                    logger.debug(f"Feeder filter applied: {filter_params['feeder'].name} (slug: {feeder_slug})")
                    # When feeder is specified, override higher-level filters for consistency
                    if not filter_params['business_district']:
                        filter_params['business_district'] = filter_params['feeder'].business_district
                    if not filter_params['state']:
                        filter_params['state'] = filter_params['feeder'].business_district.state
                else:
                    logger.warning(f"Feeder not found with slug: {feeder_slug}")
                    
            except Exception as e:
                logger.warning(f"Error parsing feeder filter: {e}")
                pass
        
        # Log final filter configuration
        filter_summary = []
        if filter_params['feeder']:
            filter_summary.append(f"Feeder: {filter_params['feeder'].name}")
        elif filter_params['business_district']:
            filter_summary.append(f"District: {filter_params['business_district'].name}")
        elif filter_params['state']:
            filter_summary.append(f"State: {filter_params['state'].name}")
        else:
            filter_summary.append("National")
            
        logger.info(f"Applied filters: {', '.join(filter_summary)}")
        
        return filter_params
    
    def _parse_dates_by_mode(self, request, mode):
        """Parse dates based on the specified mode"""
        if mode == 'monthly':
            return self._parse_monthly_dates(request)
        elif mode == 'yearly':
            return self._parse_yearly_dates(request)
        else:
            return self._parse_daily_dates(request, mode)
    
    def _parse_monthly_dates(self, request):
        """Parse dates for monthly mode"""
        try:
            year = int(request.GET.get("year", datetime.now().year))
            month = int(request.GET.get("month", datetime.now().month))
            target_date = datetime(year, month, 1).date()
        except (TypeError, ValueError):
            target_date = date.today().replace(day=1)
        
        # Get 5 months (current + 4 previous)
        periods = []
        for i in range(5):
            period = target_date - relativedelta(months=i)
            periods.append(period.replace(day=1))
        
        return {
            'periods': list(reversed(periods)),
            'target_date': target_date,
            'mode': 'monthly'
        }
    
    def _parse_yearly_dates(self, request):
        """Parse dates for yearly mode"""
        try:
            year = int(request.GET.get("year", datetime.now().year))
            target_date = datetime(year, 1, 1).date()
        except (TypeError, ValueError):
            target_date = date.today().replace(month=1, day=1)
        
        # Get 5 years (current + 4 previous)
        periods = []
        for i in range(5):
            period_year = target_date.year - i
            period_start = date(period_year, 1, 1)
            periods.append(period_start)
        
        return {
            'periods': list(reversed(periods)),
            'target_date': target_date,
            'mode': 'yearly'
        }
    
    def _parse_daily_dates(self, request, mode):
        """Parse dates for daily/weekly/custom modes"""
        from_date_str = request.GET.get("from_date")
        to_date_str = request.GET.get("to_date")
        
        if from_date_str and to_date_str:
            from_date = self._parse_iso_date(from_date_str)
            to_date = self._parse_iso_date(to_date_str)
        else:
            # Default to today for daily, or week for weekly
            if mode == 'weekly':
                to_date = date.today()
                # Get the start of the week (assuming Monday is start)
                from_date = to_date - timedelta(days=to_date.weekday())
                to_date = from_date + timedelta(days=6)
            else:
                from_date = to_date = date.today()
        
        # Calculate periods based on mode
        periods = self._calculate_periods_for_mode(from_date, to_date, mode)
        
        return {
            'periods': periods,
            'from_date': from_date,
            'to_date': to_date,
            'target_date': to_date,
            'mode': mode
        }
    
    def _calculate_periods_for_mode(self, from_date, to_date, mode):
        """Calculate periods based on mode and date range"""
        diff_days = (to_date - from_date).days + 1
        periods = []
        
        if mode == 'daily':
            # Current day + 4 previous days
            for i in range(5):
                period_date = from_date - timedelta(days=i)
                periods.append({
                    'start': period_date,
                    'end': period_date,
                    'label': self._get_day_label(period_date, i),
                    'type': 'day'
                })
        
        elif mode == 'weekly':
            # Current week + 4 previous weeks
            for i in range(5):
                # Calculate week start (subtract weeks, then adjust to Monday)
                week_end = to_date - timedelta(days=i*7)
                week_start = week_end - timedelta(days=6)
                
                periods.append({
                    'start': week_start,
                    'end': week_end,
                    'label': f"Wk{5-i}" if i > 0 else "This Wk",
                    'type': 'week'
                })
        
        elif mode == 'custom':
            # Current range + 4 previous cycles of the same length
            for i in range(5):
                cycle_end = to_date - timedelta(days=i*diff_days)
                cycle_start = cycle_end - timedelta(days=diff_days-1)
                
                periods.append({
                    'start': cycle_start,
                    'end': cycle_end,
                    'label': f"C{5-i}" if i > 0 else "Current",
                    'type': 'custom'
                })
        
        return list(reversed(periods))
    
    def _get_day_label(self, date_obj, index):
        """Get day label (Mon, Tue, etc.)"""
        if index == 0:
            return "Today"
        elif index == 1:
            return "Yesterday"
        else:
            return date_obj.strftime("%a")  # Mon, Tue, etc.
    
    def _fetch_monthly_data(self, periods, filter_params):
        """Fetch data using monthly summaries - FIXED AGGREGATION"""
        overview_data = []
        
        # Build base query filters for the aggregation level we want
        base_filters = {'month__in': periods}
        
        # Determine aggregation level based on filter params
        if filter_params['feeder']:
            # Single feeder - get exact match
            base_filters['feeder'] = filter_params['feeder']
            base_filters['state__isnull'] = True
            base_filters['business_district__isnull'] = True
        elif filter_params['business_district']:
            # Business district level - get summaries for this district only
            base_filters['business_district'] = filter_params['business_district']
            base_filters['state__isnull'] = True
            base_filters['feeder__isnull'] = True
        elif filter_params['state']:
            # State level - get summaries for this state only
            base_filters['state'] = filter_params['state']
            base_filters['business_district__isnull'] = True
            base_filters['feeder__isnull'] = True
        else:
            # National level - get all summaries with null filters (national aggregates)
            base_filters['state__isnull'] = True
            base_filters['business_district__isnull'] = True
            base_filters['feeder__isnull'] = True
        
        # If no summaries exist at the desired level, aggregate from lower levels
        summaries = MonthlyTechnicalSummary.objects.filter(**base_filters).order_by('month')
        
        if not summaries.exists() and not filter_params['feeder']:
            # No pre-aggregated summaries exist, aggregate from feeder level
            logger.info(f"No pre-aggregated summaries found, aggregating from feeder level")
            overview_data = self._aggregate_monthly_from_feeders(periods, filter_params)
        else:
            # Use existing summaries
            summary_dict = {s.month: s for s in summaries}
            
            for period in periods:
                if period in summary_dict:
                    summary = summary_dict[period]
                    overview_data.append(self._monthly_summary_to_dict(summary, period))
                else:
                    # Try to aggregate from feeder level for this specific period
                    aggregated = self._aggregate_single_month_from_feeders(period, filter_params)
                    if aggregated:
                        overview_data.append(aggregated)
                    else:
                        overview_data.append(self._get_fallback_data(period.strftime("%b"), 'monthly'))
        
        return overview_data
    
    def _fetch_yearly_data(self, periods, filter_params):
        """Fetch data using yearly aggregation of monthly summaries"""
        overview_data = []
        
        for period_start in periods:
            target_year = period_start.year
            
            # Get all months in this year
            year_months = []
            for month in range(1, 13):
                year_months.append(datetime(target_year, month, 1).date())
            
            # Build query filters for monthly summaries
            query_filters = {'month__in': year_months}
            for key, value in filter_params.items():
                if value is not None:
                    query_filters[key] = value
            
            # Get monthly summaries for this year
            monthly_summaries = MonthlyTechnicalSummary.objects.filter(**query_filters)
            
            if monthly_summaries.exists():
                # Aggregate yearly data from monthly summaries
                aggregated = self._aggregate_yearly_from_monthly(monthly_summaries, target_year)
                overview_data.append(aggregated)
            else:
                # Try to aggregate from feeder level for this year
                year_start = date(target_year, 1, 1)
                year_end = date(target_year, 12, 31)
                aggregated = self._aggregate_yearly_from_feeders(year_start, year_end, filter_params, target_year)
                if aggregated:
                    overview_data.append(aggregated)
                else:
                    overview_data.append(self._get_fallback_data(str(target_year), 'yearly'))
        
        return overview_data
    
    def _aggregate_yearly_from_monthly(self, monthly_summaries, target_year):
        """Aggregate yearly data from monthly summaries - FIXED aggregation logic"""
        # Calculate yearly aggregates
        total_interruptions = monthly_summaries.aggregate(total=Sum('total_interruptions'))['total'] or 0
        avg_supply = monthly_summaries.aggregate(avg=Avg('avg_hours_of_supply'))['avg'] or 0
        avg_duration = monthly_summaries.aggregate(avg=Avg('avg_interruption_duration'))['avg'] or 0
        avg_turnaround = monthly_summaries.aggregate(avg=Avg('avg_turnaround_time'))['avg'] or 0
        avg_fault_turnaround = monthly_summaries.aggregate(avg=Avg('avg_fault_turnaround_time'))['avg'] or 0
        avg_peak_load = monthly_summaries.aggregate(avg=Avg('avg_peak_load'))['avg'] or 0
        max_peak_load = monthly_summaries.aggregate(max=Max('max_peak_load'))['max'] or 0
        total_supply_hours = monthly_summaries.aggregate(total=Sum('total_supply_hours'))['total'] or 0
        
        # Calculate energy: Average Load × Total Supply Hours
        # If total_supply_hours not available, estimate from avg_hours_supply
        if float(total_supply_hours or 0) == 0:
            days_in_year = 366 if target_year % 4 == 0 and (target_year % 100 != 0 or target_year % 400 == 0) else 365
            total_supply_hours = float(avg_supply) * days_in_year
        
        calculated_energy = round(float(avg_peak_load) * float(total_supply_hours), 2)
        
        # FIXED: Get infrastructure metrics properly
        # For feeder count and customer count, we should use the maximum values seen
        # across all months, not just the latest month, since these can vary
        max_feeder_count = monthly_summaries.aggregate(max=Max('active_feeder_count'))['max'] or 0
        max_customer_count = monthly_summaries.aggregate(max=Max('total_customer_count'))['max'] or 0
        
        # Calculate daily averages for yearly period
        days_in_year = 366 if target_year % 4 == 0 and (target_year % 100 != 0 or target_year % 400 == 0) else 365
        avg_daily_interruptions = total_interruptions / days_in_year if days_in_year > 0 else 0
        
        # Aggregate interruption breakdowns
        combined_breakdown = {}
        combined_summary_breakdown = {}
        
        for summary in monthly_summaries:
            # Get breakdown data safely
            breakdown_data = summary.interruption_breakdown_dict if hasattr(summary, 'interruption_breakdown_dict') else {}
            summary_breakdown_data = summary.summary_breakdown_dict if hasattr(summary, 'summary_breakdown_dict') else {}
            
            for fault_type, hours in breakdown_data.items():
                combined_breakdown[fault_type] = combined_breakdown.get(fault_type, 0) + float(hours)
            
            for category, hours in summary_breakdown_data.items():
                combined_summary_breakdown[category] = combined_summary_breakdown.get(category, 0) + float(hours)
        
        # Calculate reliability indices using max customer count
        saifi = total_interruptions / max_customer_count if max_customer_count > 0 else 0
        total_interruption_hours = sum(combined_breakdown.values()) if combined_breakdown else 0
        saidi = total_interruption_hours / max_customer_count if max_customer_count > 0 else 0
        
        return {
            "month": str(target_year),  # Use year as "month" for compatibility
            "energy_delivered": calculated_energy,  # Use calculated energy
            "average_load": float(avg_peak_load),
            "max_load": float(max_peak_load),
            "interruptions": total_interruptions,
            "avg_hours_supply": float(avg_supply),
            "avg_interruption_duration": float(avg_duration),
            "avg_turnaround_time": float(avg_turnaround),
            "avg_fault_turnaround_time": float(avg_fault_turnaround),
            "feeder_count": max_feeder_count,  # FIXED: Use max instead of latest
            "customer_count": max_customer_count,  # FIXED: Use max instead of latest
            "avg_daily_interruptions": round(avg_daily_interruptions, 2),
            "availability_percentage": round((float(avg_supply) / 24) * 100, 2),
            "saifi": round(saifi, 4),
            "saidi": round(saidi, 4),
            "interruption_breakdown": combined_breakdown,
            "summary_breakdown": combined_summary_breakdown,
            "_source": f"yearly_monthly_aggregation_{monthly_summaries.count()}_months",
            "_year": target_year,
            "_debug_months": list(monthly_summaries.values_list('month', flat=True)),
        }
    
    def _aggregate_yearly_from_feeders(self, year_start, year_end, filter_params, target_year):
        """Aggregate yearly data from feeder-level monthly summaries - FIXED logic"""
        # Get all months in the year
        year_months = []
        for month in range(1, 13):
            year_months.append(datetime(target_year, month, 1).date())
        
        # Build query to get feeder-level summaries for this year
        feeder_filters = {
            'month__in': year_months,
            'feeder__isnull': False,
            'state__isnull': True,
            'business_district__isnull': True,
        }
        
        # Apply geographic filters
        if filter_params['feeder']:
            feeder_filters['feeder'] = filter_params['feeder']
        elif filter_params['business_district']:
            feeder_filters['feeder__business_district'] = filter_params['business_district']
        elif filter_params['state']:
            feeder_filters['feeder__business_district__state'] = filter_params['state']
        
        feeder_summaries = MonthlyTechnicalSummary.objects.filter(**feeder_filters)
        
        if not feeder_summaries.exists():
            return None
        
        # FIXED: Get unique feeders and their data
        unique_feeders = set()
        feeder_yearly_data = {}
        
        for summary in feeder_summaries:
            feeder_id = summary.feeder.id
            unique_feeders.add(feeder_id)
            
            if feeder_id not in feeder_yearly_data:
                feeder_yearly_data[feeder_id] = {
                    'total_energy': 0,
                    'total_interruptions': 0,
                    'total_interruption_hours': 0,
                    'supply_hours_sum': 0,
                    'duration_sum': 0,
                    'turnaround_sum': 0,
                    'month_count': 0,
                    'peak_loads': [],
                    'latest_summary': None,
                    'customer_counts': [],
                }
            
            data = feeder_yearly_data[feeder_id]
            data['total_energy'] += float(summary.total_energy_delivered)
            data['total_interruptions'] += summary.total_interruptions
            data['total_interruption_hours'] += float(summary.total_interruption_hours) if hasattr(summary, 'total_interruption_hours') else 0
            data['supply_hours_sum'] += float(summary.avg_hours_of_supply)
            data['duration_sum'] += float(summary.avg_interruption_duration)
            data['turnaround_sum'] += float(summary.avg_turnaround_time) if hasattr(summary, 'avg_turnaround_time') else float(summary.avg_fault_turnaround_time)
            data['month_count'] += 1
            data['peak_loads'].append(float(summary.max_peak_load))
            data['customer_counts'].append(summary.total_customer_count)
            
            if not data['latest_summary'] or summary.month > data['latest_summary'].month:
                data['latest_summary'] = summary
        
        # Now aggregate across all feeders
        total_energy = sum(data['total_energy'] for data in feeder_yearly_data.values())
        total_interruptions = sum(data['total_interruptions'] for data in feeder_yearly_data.values())
        
        # Calculate weighted averages
        total_feeder_months = sum(data['month_count'] for data in feeder_yearly_data.values())
        if total_feeder_months > 0:
            avg_supply = sum(data['supply_hours_sum'] for data in feeder_yearly_data.values()) / total_feeder_months
            total_interruption_hours = sum(data['total_interruption_hours'] for data in feeder_yearly_data.values())
            weighted_avg_duration = total_interruption_hours / total_interruptions if total_interruptions > 0 else 0
            avg_turnaround = sum(data['turnaround_sum'] for data in feeder_yearly_data.values()) / total_feeder_months
        else:
            avg_supply = avg_turnaround = weighted_avg_duration = 0
        
        # FIXED: Infrastructure metrics
        unique_feeder_count = len(unique_feeders)  # This is the correct feeder count
        all_peak_loads = [load for data in feeder_yearly_data.values() for load in data['peak_loads']]
        max_peak_load = max(all_peak_loads) if all_peak_loads else 0
        avg_peak_load = sum(all_peak_loads) / len(all_peak_loads) if all_peak_loads else 0
        
        # FIXED: Customer count - use max across all feeders and months
        all_customer_counts = [count for data in feeder_yearly_data.values() for count in data['customer_counts']]
        total_customer_count = max(all_customer_counts) if all_customer_counts else 0
        
        # Calculate daily averages
        days_in_year = 366 if target_year % 4 == 0 and (target_year % 100 != 0 or target_year % 400 == 0) else 365
        avg_daily_interruptions = total_interruptions / days_in_year if days_in_year > 0 else 0
        
        return {
            "month": str(target_year),
            "energy_delivered": round(total_energy, 2),
            "average_load": round(avg_peak_load, 2),
            "max_load": round(max_peak_load, 2),
            "interruptions": total_interruptions,
            "avg_hours_supply": round(avg_supply, 2),
            "avg_interruption_duration": round(weighted_avg_duration, 2),
            "avg_turnaround_time": round(avg_turnaround, 2),
            "avg_fault_turnaround_time": round(avg_turnaround, 2),
            "feeder_count": unique_feeder_count,  # FIXED: Use unique feeder count
            "customer_count": total_customer_count,  # FIXED: Use max customer count
            "avg_daily_interruptions": round(avg_daily_interruptions, 2),
            "availability_percentage": round((avg_supply / 24) * 100, 2),
            "saifi": round(total_interruptions / total_customer_count if total_customer_count > 0 else 0, 4),
            "saidi": round(total_interruption_hours / total_customer_count if total_customer_count > 0 else 0, 4),
            "interruption_breakdown": {},  # Could be calculated if needed
            "summary_breakdown": {},
            "_source": f"yearly_feeder_aggregation_{unique_feeder_count}_feeders",
            "_year": target_year,
            "_debug_feeder_summaries": feeder_summaries.count(),
            "_debug_unique_feeders": list(unique_feeders),
        }
    
    def _aggregate_monthly_from_feeders(self, periods, filter_params):
        """Aggregate monthly data from feeder-level summaries"""
        overview_data = []
        
        for period in periods:
            aggregated = self._aggregate_single_month_from_feeders(period, filter_params)
            if aggregated:
                overview_data.append(aggregated)
            else:
                overview_data.append(self._get_fallback_data(period.strftime("%b"), 'monthly'))
        
        return overview_data
    
    def _aggregate_single_month_from_feeders(self, period, filter_params):
        """Aggregate data for a single month from feeder-level summaries"""
        # Build query to get all feeder-level summaries for this period
        feeder_filters = {
            'month': period,
            'feeder__isnull': False,  # Only feeder-level summaries
            'state__isnull': True,
            'business_district__isnull': True,
        }
        
        # Apply geographic filters to the feeder relationship
        if filter_params['feeder']:
            feeder_filters['feeder'] = filter_params['feeder']
        elif filter_params['business_district']:
            feeder_filters['feeder__business_district'] = filter_params['business_district']
        elif filter_params['state']:
            feeder_filters['feeder__business_district__state'] = filter_params['state']
        
        feeder_summaries = MonthlyTechnicalSummary.objects.filter(**feeder_filters)
        
        if not feeder_summaries.exists():
            return None
        
        # Aggregate the data
        aggregated_data = feeder_summaries.aggregate(
            total_energy_delivered=Sum('total_energy_delivered'),
            avg_peak_load=Avg('avg_peak_load'),
            max_peak_load=Max('max_peak_load'),
            total_interruptions=Sum('total_interruptions'),
            avg_hours_of_supply=Avg('avg_hours_of_supply'),
            total_interruption_hours=Sum('total_interruption_hours'),
            feeder_count=Count('feeder', distinct=True),
            total_customer_count=Sum('total_customer_count'),
            total_supply_hours=Sum('total_supply_hours'),
        )
        
        # Calculate weighted averages for some metrics
        total_interruptions = aggregated_data['total_interruptions'] or 0
        total_interruption_hours = aggregated_data['total_interruption_hours'] or 0
        total_customer_count = aggregated_data['total_customer_count'] or 0
        
        avg_interruption_duration = 0
        if total_interruptions > 0:
            avg_interruption_duration = total_interruption_hours / total_interruptions
        
        # Calculate SAIFI and SAIDI
        saifi = 0
        saidi = 0
        if total_customer_count > 0:
            saifi = total_interruptions / total_customer_count
            saidi = total_interruption_hours / total_customer_count
        
        # Aggregate interruption breakdowns
        combined_breakdown = {}
        combined_summary_breakdown = {}
        
        for summary in feeder_summaries:
            for fault_type, hours in summary.interruption_breakdown_dict.items():
                combined_breakdown[fault_type] = combined_breakdown.get(fault_type, 0) + float(hours)
            
            for category, hours in summary.summary_breakdown_dict.items():
                combined_summary_breakdown[category] = combined_summary_breakdown.get(category, 0) + float(hours)
        
        # Calculate average turnaround times (weighted by number of interruptions)
        total_weighted_turnaround = 0
        total_weighted_fault_turnaround = 0
        total_interruptions_for_avg = 0
        
        for summary in feeder_summaries:
            if summary.total_interruptions > 0:
                total_weighted_turnaround += float(summary.avg_turnaround_time) * summary.total_interruptions
                total_weighted_fault_turnaround += float(summary.avg_fault_turnaround_time) * summary.total_interruptions
                total_interruptions_for_avg += summary.total_interruptions
        
        avg_turnaround_time = 0
        avg_fault_turnaround_time = 0
        if total_interruptions_for_avg > 0:
            avg_turnaround_time = total_weighted_turnaround / total_interruptions_for_avg
            avg_fault_turnaround_time = total_weighted_fault_turnaround / total_interruptions_for_avg
        
        # Calculate average daily interruptions
        import calendar
        days_in_month = calendar.monthrange(period.year, period.month)[1]
        avg_daily_interruptions = total_interruptions / days_in_month if days_in_month > 0 else 0
        
        # Calculate energy: Average Load × Total Supply Hours
        avg_load = float(aggregated_data['avg_peak_load'] or 0)
        total_supply_hours = float(aggregated_data['total_supply_hours'] or 0)
        
        # If total_supply_hours not available, estimate from avg_hours_supply
        if total_supply_hours == 0:
            avg_hours_supply = float(aggregated_data['avg_hours_of_supply'] or 0)
            total_supply_hours = avg_hours_supply * days_in_month
        
        calculated_energy = round(avg_load * total_supply_hours, 2)
        
        return {
            "month": period.strftime("%b"),
            "energy_delivered": calculated_energy,  # Use calculated energy
            "average_load": float(aggregated_data['avg_peak_load'] or 0),
            "max_load": float(aggregated_data['max_peak_load'] or 0),
            "interruptions": total_interruptions,
            "avg_hours_supply": float(aggregated_data['avg_hours_of_supply'] or 0),
            "avg_interruption_duration": round(avg_interruption_duration, 2),
            "avg_turnaround_time": round(avg_turnaround_time, 2),
            "avg_fault_turnaround_time": round(avg_fault_turnaround_time, 2),
            "feeder_count": aggregated_data['feeder_count'] or 0,
            "customer_count": total_customer_count,
            "avg_daily_interruptions": round(avg_daily_interruptions, 2),
            "availability_percentage": round((float(aggregated_data['avg_hours_of_supply'] or 0) / 24) * 100, 2),
            "saifi": round(saifi, 4),
            "saidi": round(saidi, 4),
            "interruption_breakdown": combined_breakdown,
            "summary_breakdown": combined_summary_breakdown,
            "_source": "aggregated_monthly_feeders",
            "_feeder_count": aggregated_data['feeder_count'],
        }
    
    def _fetch_daily_data(self, periods, filter_params, mode):
        """Fetch data using daily summaries - FIXED AGGREGATION"""
        overview_data = []
        
        # Collect all dates we need
        all_dates = []
        for period in periods:
            if period['type'] == 'day':
                all_dates.append(period['start'])
            else:
                # For weeks/custom ranges, collect all dates in range
                current = period['start']
                while current <= period['end']:
                    all_dates.append(current)
                    current += timedelta(days=1)
        
        # Build query filters similar to monthly
        base_filters = {'date__in': all_dates}
        
        # Apply same aggregation logic as monthly
        if filter_params['feeder']:
            base_filters['feeder'] = filter_params['feeder']
            base_filters['state__isnull'] = True
            base_filters['business_district__isnull'] = True
        elif filter_params['business_district']:
            base_filters['business_district'] = filter_params['business_district']
            base_filters['state__isnull'] = True
            base_filters['feeder__isnull'] = True
        elif filter_params['state']:
            base_filters['state'] = filter_params['state']
            base_filters['business_district__isnull'] = True
            base_filters['feeder__isnull'] = True
        else:
            base_filters['state__isnull'] = True
            base_filters['business_district__isnull'] = True
            base_filters['feeder__isnull'] = True
        
        # Fetch daily summaries
        summaries = DailyTechnicalSummary.objects.filter(**base_filters).order_by('date')
        summary_dict = {s.date: s for s in summaries}
        
        # If no pre-aggregated summaries, try to aggregate from feeder level
        if not summaries.exists() and not filter_params['feeder']:
            logger.info("No pre-aggregated daily summaries found, aggregating from feeder level")
            return self._aggregate_daily_from_feeders(periods, filter_params, mode)
        
        # Process each period
        for period in periods:
            if period['type'] == 'day':
                # Single day
                if period['start'] in summary_dict:
                    summary = summary_dict[period['start']]
                    overview_data.append(self._daily_summary_to_dict(summary, period['label']))
                else:
                    # Try to aggregate from feeder level for this date
                    aggregated = self._aggregate_single_day_from_feeders(period['start'], filter_params)
                    if aggregated:
                        aggregated['month'] = period['label']  # Update label
                        overview_data.append(aggregated)
                    else:
                        overview_data.append(self._get_fallback_data(period['label'], 'daily'))
            else:
                # Aggregate multiple days
                period_summaries = []
                current = period['start']
                while current <= period['end']:
                    if current in summary_dict:
                        period_summaries.append(summary_dict[current])
                    current += timedelta(days=1)
                
                if period_summaries:
                    aggregated = self._aggregate_daily_summaries(period_summaries, period['label'])
                    overview_data.append(aggregated)
                else:
                    # Try to aggregate from feeder level for this period
                    aggregated = self._aggregate_period_from_daily_feeders(period, filter_params)
                    if aggregated:
                        overview_data.append(aggregated)
                    else:
                        overview_data.append(self._get_fallback_data(period['label'], mode))
        
        return overview_data
    
    def _aggregate_daily_from_feeders(self, periods, filter_params, mode):
        """Aggregate daily data from feeder-level summaries"""
        overview_data = []
        
        for period in periods:
            if period['type'] == 'day':
                aggregated = self._aggregate_single_day_from_feeders(period['start'], filter_params)
                if aggregated:
                    aggregated['month'] = period['label']
                    overview_data.append(aggregated)
                else:
                    overview_data.append(self._get_fallback_data(period['label'], 'daily'))
            else:
                aggregated = self._aggregate_period_from_daily_feeders(period, filter_params)
                if aggregated:
                    overview_data.append(aggregated)
                else:
                    overview_data.append(self._get_fallback_data(period['label'], mode))
        
        return overview_data
    
    def _aggregate_single_day_from_feeders(self, date_obj, filter_params):
        """Aggregate data for a single day from feeder-level summaries"""
        feeder_filters = {
            'date': date_obj,
            'feeder__isnull': False,
            'state__isnull': True,
            'business_district__isnull': True,
        }
        
        # Apply geographic filters
        if filter_params['feeder']:
            feeder_filters['feeder'] = filter_params['feeder']
        elif filter_params['business_district']:
            feeder_filters['feeder__business_district'] = filter_params['business_district']
        elif filter_params['state']:
            feeder_filters['feeder__business_district__state'] = filter_params['state']
        
        feeder_summaries = DailyTechnicalSummary.objects.filter(**feeder_filters)
        
        if not feeder_summaries.exists():
            return None
        
        # Similar aggregation logic as monthly but for daily data
        aggregated_data = feeder_summaries.aggregate(
            total_energy_delivered=Sum('total_energy_delivered'),
            avg_peak_load=Avg('avg_peak_load'),
            max_peak_load=Max('max_peak_load'),
            total_interruptions=Sum('total_interruptions'),
            avg_hours_of_supply=Avg('hours_of_supply'),
            total_interruption_hours=Sum('total_interruption_hours'),
            feeder_count=Count('feeder', distinct=True),
            total_customer_count=Sum('total_customer_count'),
        )
        
        # Calculate derived metrics (similar to monthly aggregation)
        total_interruptions = aggregated_data['total_interruptions'] or 0
        total_interruption_hours = aggregated_data['total_interruption_hours'] or 0
        total_customer_count = aggregated_data['total_customer_count'] or 0
        
        avg_interruption_duration = 0
        if total_interruptions > 0:
            avg_interruption_duration = total_interruption_hours / total_interruptions
        
        saifi = total_interruptions / total_customer_count if total_customer_count > 0 else 0
        saidi = total_interruption_hours / total_customer_count if total_customer_count > 0 else 0
        
        # Aggregate breakdowns
        combined_breakdown = {}
        combined_summary_breakdown = {}
        
        for summary in feeder_summaries:
            for fault_type, hours in summary.interruption_breakdown_dict.items():
                combined_breakdown[fault_type] = combined_breakdown.get(fault_type, 0) + float(hours)
            
            for category, hours in summary.summary_breakdown_dict.items():
                combined_summary_breakdown[category] = combined_summary_breakdown.get(category, 0) + float(hours)
        
        # Weighted turnaround times
        total_weighted_turnaround = 0
        total_weighted_fault_turnaround = 0
        total_interruptions_for_avg = 0
        
        for summary in feeder_summaries:
            if summary.total_interruptions > 0:
                total_weighted_turnaround += float(summary.avg_turnaround_time) * summary.total_interruptions
                total_weighted_fault_turnaround += float(summary.avg_fault_turnaround_time) * summary.total_interruptions
                total_interruptions_for_avg += summary.total_interruptions
        
        avg_turnaround_time = total_weighted_turnaround / total_interruptions_for_avg if total_interruptions_for_avg > 0 else 0
        avg_fault_turnaround_time = total_weighted_fault_turnaround / total_interruptions_for_avg if total_interruptions_for_avg > 0 else 0
        
        return {
            "energy_delivered": float(aggregated_data['total_energy_delivered'] or 0),
            "average_load": float(aggregated_data['avg_peak_load'] or 0),
            "max_load": float(aggregated_data['max_peak_load'] or 0),
            "interruptions": total_interruptions,
            "avg_hours_supply": float(aggregated_data['avg_hours_of_supply'] or 0),
            "avg_interruption_duration": round(avg_interruption_duration, 2),
            "avg_turnaround_time": round(avg_turnaround_time, 2),
            "avg_fault_turnaround_time": round(avg_fault_turnaround_time, 2),
            "feeder_count": aggregated_data['feeder_count'] or 0,
            "customer_count": total_customer_count,
            "avg_daily_interruptions": float(total_interruptions),  # For daily, this is total interruptions
            "availability_percentage": round((float(aggregated_data['avg_hours_of_supply'] or 0) / 24) * 100, 2),
            "saifi": round(saifi, 4),
            "saidi": round(saidi, 4),
            "interruption_breakdown": combined_breakdown,
            "summary_breakdown": combined_summary_breakdown,
            "_source": "aggregated_daily_feeders",
            "_feeder_count": aggregated_data['feeder_count'],
        }
    
    def _aggregate_period_from_daily_feeders(self, period, filter_params):
        """Aggregate data for a period from daily feeder summaries"""
        # Collect all dates in the period
        all_dates = []
        current = period['start']
        while current <= period['end']:
            all_dates.append(current)
            current += timedelta(days=1)
        
        # Get all daily summaries for these dates
        daily_aggregates = []
        for date_obj in all_dates:
            daily_agg = self._aggregate_single_day_from_feeders(date_obj, filter_params)
            if daily_agg:
                daily_aggregates.append(daily_agg)
        
        if not daily_aggregates:
            return self._get_fallback_data(period['label'], 'custom')
        
        # Now aggregate the daily aggregates into a period summary
        count = len(daily_aggregates)
        
        total_energy = sum(agg['energy_delivered'] for agg in daily_aggregates)
        avg_load = sum(agg['average_load'] for agg in daily_aggregates) / count
        max_load = max(agg['max_load'] for agg in daily_aggregates)
        total_interruptions = sum(agg['interruptions'] for agg in daily_aggregates)
        avg_supply_hours = sum(agg['avg_hours_supply'] for agg in daily_aggregates) / count
        
        # Weighted average for interruption duration
        total_interruption_hours = sum(agg['interruptions'] * agg['avg_interruption_duration'] for agg in daily_aggregates)
        weighted_avg_duration = total_interruption_hours / total_interruptions if total_interruptions > 0 else 0
        
        # Average turnaround times
        avg_turnaround = sum(agg['avg_turnaround_time'] for agg in daily_aggregates) / count
        avg_fault_turnaround = sum(agg['avg_fault_turnaround_time'] for agg in daily_aggregates) / count
        
        # Take the latest values for infrastructure metrics
        latest = daily_aggregates[-1]
        
        # Sum interruption breakdowns
        combined_breakdown = {}
        combined_summary_breakdown = {}
        
        for agg in daily_aggregates:
            for fault_type, hours in agg['interruption_breakdown'].items():
                combined_breakdown[fault_type] = combined_breakdown.get(fault_type, 0) + hours
            
            for category, hours in agg['summary_breakdown'].items():
                combined_summary_breakdown[category] = combined_summary_breakdown.get(category, 0) + hours
        
        return {
            "month": period['label'],
            "energy_delivered": round(total_energy, 2),
            "average_load": round(avg_load, 2),
            "max_load": round(max_load, 2),
            "interruptions": total_interruptions,
            "avg_hours_supply": round(avg_supply_hours, 2),
            "avg_interruption_duration": round(weighted_avg_duration, 2),
            "avg_turnaround_time": round(avg_turnaround, 2),
            "avg_fault_turnaround_time": round(avg_fault_turnaround, 2),
            "feeder_count": latest['feeder_count'],
            "customer_count": latest['customer_count'],
            "avg_daily_interruptions": round(total_interruptions / count, 2),
            "availability_percentage": round((avg_supply_hours / 24) * 100, 2),
            "saifi": round(sum(agg['saifi'] for agg in daily_aggregates) / count, 4),
            "saidi": round(sum(agg['saidi'] for agg in daily_aggregates) / count, 4),
            "interruption_breakdown": combined_breakdown,
            "summary_breakdown": combined_summary_breakdown,
            "_source": "aggregated_period_from_daily_feeders",
            "_period_days": count,
        }
    
    def _monthly_summary_to_dict(self, summary, month):
        """Convert monthly summary to dict
        
        NOTE: Recalculates energy_delivered using Average Load × Total Supply Hours
        to ensure mathematically correct values.
        """
        # Recalculate energy: Average Load × Total Supply Hours
        avg_load = float(summary.avg_peak_load)  # Using avg_peak_load as proxy for avg_load
        total_supply_hours = float(getattr(summary, 'total_supply_hours', 0) or 0)
        
        # If total_supply_hours not available, estimate from avg_hours_supply
        if total_supply_hours == 0:
            import calendar
            days_in_month = calendar.monthrange(month.year, month.month)[1]
            total_supply_hours = float(summary.avg_hours_of_supply) * days_in_month
        
        calculated_energy = round(avg_load * total_supply_hours, 2)
        
        return {
            "month": month.strftime("%b"),
            "energy_delivered": calculated_energy,  # Use calculated energy
            "average_load": float(summary.avg_peak_load),
            "max_load": float(summary.max_peak_load),
            "interruptions": summary.total_interruptions,
            "avg_hours_supply": float(summary.avg_hours_of_supply),
            "avg_interruption_duration": float(summary.avg_interruption_duration),
            "avg_turnaround_time": float(summary.avg_turnaround_time),
            "avg_fault_turnaround_time": float(summary.avg_fault_turnaround_time),
            "feeder_count": summary.active_feeder_count,
            "customer_count": summary.total_customer_count,
            "avg_daily_interruptions": float(summary.avg_daily_interruptions),
            "availability_percentage": summary.availability_percentage,
            "saifi": float(summary.saifi),
            "saidi": float(summary.saidi),
            "interruption_breakdown": summary.interruption_breakdown_dict,
            "summary_breakdown": summary.summary_breakdown_dict,
            "_source": "monthly_summary",
            "_calculated_at": summary.calculated_at.isoformat(),
            "_has_complete_data": summary.has_complete_data,
        }
    
    def _daily_summary_to_dict(self, summary, label):
        """Convert daily summary to dict"""
        return {
            "month": label,  # Using 'month' for compatibility with frontend
            "energy_delivered": float(summary.total_energy_delivered),
            "average_load": float(summary.avg_peak_load),
            "max_load": float(summary.max_peak_load),
            "interruptions": summary.total_interruptions,
            "avg_hours_supply": float(summary.hours_of_supply),
            "avg_interruption_duration": float(summary.avg_interruption_duration),
            "avg_turnaround_time": float(summary.avg_turnaround_time),
            "avg_fault_turnaround_time": float(summary.avg_fault_turnaround_time),
            "feeder_count": summary.active_feeder_count,
            "customer_count": summary.total_customer_count,
            "avg_daily_interruptions": float(summary.total_interruptions),  # For daily, this is total interruptions
            "availability_percentage": summary.availability_percentage,
            "saifi": float(summary.saifi),
            "saidi": float(summary.saidi),
            "interruption_breakdown": summary.interruption_breakdown_dict,
            "summary_breakdown": summary.summary_breakdown_dict,
            "_source": "daily_summary",
            "_calculated_at": summary.calculated_at.isoformat(),
            "_has_complete_data": summary.has_complete_data,
        }
    
    def _aggregate_daily_summaries(self, summaries, label):
        """Aggregate multiple daily summaries into one period"""
        if not summaries:
            return self._get_fallback_data(label, 'aggregated')
        
        count = len(summaries)
        
        # Sum/average the metrics appropriately
        total_energy = sum(float(s.total_energy_delivered) for s in summaries)
        avg_load = sum(float(s.avg_peak_load) for s in summaries) / count
        max_load = max(float(s.max_peak_load) for s in summaries)
        total_interruptions = sum(s.total_interruptions for s in summaries)
        
        # Average supply hours across days
        avg_supply_hours = sum(float(s.hours_of_supply) for s in summaries) / count
        
        # For interruption duration, calculate weighted average
        total_interruption_hours = sum(float(s.total_interruption_hours) for s in summaries)
        weighted_avg_duration = total_interruption_hours / total_interruptions if total_interruptions > 0 else 0
        
        # Average turnaround times
        avg_turnaround = sum(float(s.avg_turnaround_time) for s in summaries) / count
        avg_fault_turnaround = sum(float(s.avg_fault_turnaround_time) for s in summaries) / count
        
        # Take the latest values for infrastructure metrics
        latest = max(summaries, key=lambda s: s.calculated_at)
        
        # Sum interruption breakdowns (total hours across all days)
        combined_breakdown = {}
        combined_summary_breakdown = {}
        
        for summary in summaries:
            for fault_type, hours in summary.interruption_breakdown_dict.items():
                combined_breakdown[fault_type] = combined_breakdown.get(fault_type, 0) + hours
            
            for category, hours in summary.summary_breakdown_dict.items():
                combined_summary_breakdown[category] = combined_summary_breakdown.get(category, 0) + hours
        
        return {
            "month": label,
            "energy_delivered": round(total_energy, 2),
            "average_load": round(avg_load, 2),
            "max_load": round(max_load, 2),
            "interruptions": total_interruptions,
            "avg_hours_supply": round(avg_supply_hours, 2),
            "avg_interruption_duration": round(weighted_avg_duration, 2),
            "avg_turnaround_time": round(avg_turnaround, 2),
            "avg_fault_turnaround_time": round(avg_fault_turnaround, 2),
            "feeder_count": latest.active_feeder_count,
            "customer_count": latest.total_customer_count,
            "avg_daily_interruptions": round(total_interruptions / count, 2),
            "availability_percentage": round((avg_supply_hours / 24) * 100, 2),
            "saifi": round(sum(float(s.saifi) for s in summaries) / count, 4),
            "saidi": round(sum(float(s.saidi) for s in summaries) / count, 4),
            "interruption_breakdown": combined_breakdown,
            "summary_breakdown": combined_summary_breakdown,
            "_source": "aggregated_daily",
            "_period_days": count,
        }
    
    def _get_fallback_data(self, label, source_type):
        """Provide fallback data when summary is missing"""
        return {
            "month": label,
            "energy_delivered": 0.0,
            "average_load": 0.0,
            "max_load": 0.0,
            "interruptions": 0,
            "avg_hours_supply": 0.0,
            "avg_interruption_duration": 0.0,
            "avg_turnaround_time": 0.0,
            "avg_fault_turnaround_time": 0.0,
            "feeder_count": 0,
            "customer_count": 0,
            "avg_daily_interruptions": 0.0,
            "availability_percentage": 0.0,
            "saifi": 0.0,
            "saidi": 0.0,
            "interruption_breakdown": {},
            "summary_breakdown": {},
            "_source": f"fallback_{source_type}",
            "_summary_missing": True,
        }
    
    def _get_additional_data(self, request, filter_params):
        """Get additional data like load trends - MODIFIED to always return load trend"""
        additional = {}
        
        # Parse mode and date parameters to determine what load trend to return
        mode = request.GET.get('mode')
        if not mode and (request.GET.get('from_date') or request.GET.get('to_date')):
            mode = self._determine_mode_from_dates(request)
        elif not mode:
            mode = 'monthly'  # Default mode
        
        # Parse dates based on mode to get the target date for load trend
        date_info = self._parse_dates_by_mode(request, mode)
        
        # Always get load trend data based on mode and date configuration
        additional["load_trend"] = self._get_load_trend_for_mode(mode, date_info, filter_params)
        
        return additional

    def _get_load_trend_for_mode(self, mode, date_info, filter_params):
        """Get load trend data based on mode and date configuration"""
        
        if mode == 'monthly':
            # For monthly mode, return load trend for the target month
            target_month = date_info.get('target_date', date.today().replace(day=1))
            return self._get_monthly_load_trend(target_month, filter_params)
        
        elif mode == 'yearly':
            # For yearly mode, return monthly averages for the target year
            target_year = date_info.get('target_date', date.today().replace(month=1, day=1))
            return self._get_yearly_load_trend(target_year, filter_params)
        
        elif mode in ['daily', 'weekly', 'custom']:
            # For daily/weekly/custom modes, return hourly data for the target date
            target_date = date_info.get('target_date', date.today())
            if mode == 'weekly':
                # For weekly, use the last day of the week
                target_date = date_info.get('to_date', date.today())
            return self._get_daily_load_trend(target_date, filter_params)
        
        else:
            # Fallback
            return {"series": [], "date": None, "unit": "MW"}

    def _get_daily_load_trend(self, trend_date, filter_params):
        """Get hourly load trend for a specific date"""
        # Build feeder filter
        feeder_filter = {}
        if filter_params['feeder']:
            feeder_filter['feeder'] = filter_params['feeder']
        elif filter_params['business_district']:
            feeder_filter['feeder__business_district'] = filter_params['business_district']
        elif filter_params['state']:
            feeder_filter['feeder__business_district__state'] = filter_params['state']
        
        # Get hourly data for the specified date
        hourly_data = HourlyLoad.objects.filter(
            date=trend_date,
            **feeder_filter
        ).values('hour').annotate(
            avg_load=Avg('load_mw')
        ).order_by('hour')
        
        series = [
            {"hour": entry["hour"], "value": round(float(entry["avg_load"] or 0), 2)}
            for entry in hourly_data
        ]
        
        # Fill missing hours with 0 if needed
        hours_dict = {entry["hour"]: entry["value"] for entry in series}
        complete_series = [
            {"hour": hour, "value": hours_dict.get(hour, 0)}
            for hour in range(24)
        ]
        
        return {
            "unit": "MW",
            "date": trend_date.strftime("%Y-%m-%d"),
            "series": complete_series,
            "type": "hourly"
        }

    def _get_monthly_load_trend(self, target_month, filter_params):
        """Get daily average load trend for a specific month"""
        # Build feeder filter
        feeder_filter = {}
        if filter_params['feeder']:
            feeder_filter['feeder'] = filter_params['feeder']
        elif filter_params['business_district']:
            feeder_filter['feeder__business_district'] = filter_params['business_district']
        elif filter_params['state']:
            feeder_filter['feeder__business_district__state'] = filter_params['state']
        
        # Calculate the date range for the month
        days_in_month = monthrange(target_month.year, target_month.month)[1]
        month_start = target_month
        month_end = target_month.replace(day=days_in_month)
        
        # Get daily average loads for the month
        daily_data = HourlyLoad.objects.filter(
            date__range=(month_start, month_end),
            **feeder_filter
        ).values('date').annotate(
            avg_load=Avg('load_mw')
        ).order_by('date')
        
        series = [
            {"day": entry["date"].day, "value": round(float(entry["avg_load"] or 0), 2)}
            for entry in daily_data
        ]
        
        # Fill missing days with 0 if needed
        days_dict = {entry["day"]: entry["value"] for entry in series}
        complete_series = [
            {"day": day, "value": days_dict.get(day, 0)}
            for day in range(1, days_in_month + 1)
        ]
        
        return {
            "unit": "MW",
            "date": target_month.strftime("%Y-%m"),
            "series": complete_series,
            "type": "daily_in_month"
        }

    def _get_yearly_load_trend(self, target_year, filter_params):
        """Get monthly average load trend for a specific year"""
        # Build feeder filter
        feeder_filter = {}
        if filter_params['feeder']:
            feeder_filter['feeder'] = filter_params['feeder']
        elif filter_params['business_district']:
            feeder_filter['feeder__business_district'] = filter_params['business_district']
        elif filter_params['state']:
            feeder_filter['feeder__business_district__state'] = filter_params['state']
        
        # Calculate the date range for the year
        year_start = target_year.replace(month=1, day=1)
        year_end = target_year.replace(month=12, day=31)
        
        # Get monthly average loads for the year
        monthly_data = HourlyLoad.objects.filter(
            date__range=(year_start, year_end),
            **feeder_filter
        ).annotate(
            month=Extract('date__month')
        ).values('month').annotate(
            avg_load=Avg('load_mw')
        ).order_by('month')
        
        series = [
            {"month": entry["month"], "value": round(float(entry["avg_load"] or 0), 2)}
            for entry in monthly_data
        ]
        
        # Fill missing months with 0 if needed
        months_dict = {entry["month"]: entry["value"] for entry in series}
        complete_series = [
            {"month": month, "value": months_dict.get(month, 0)}
            for month in range(1, 13)
        ]
        
        return {
            "unit": "MW",
            "date": str(target_year.year),
            "series": complete_series,
            "type": "monthly_in_year"
        }
    
    def _format_response_data(self, overview_data, additional_data, mode, date_info):
        """Format the final response data
        
        IMPORTANT: Ensures avg_hours_supply + avg_interruption_duration + avg_turnaround_time = 24
        by applying fix_metrics_to_sum_24 to all data items.
        """
        if not overview_data:
            return {
                "highlight_metrics": {},
                "supply_and_quality": {},
                "technical_breakdown": {},
                "interruption_sources": [],
                "load_trend": additional_data.get("load_trend", {"series": [], "date": None}),
                "_mode": mode,
                "_date_info": date_info
            }
        
        # =====================================================================
        # FIXED: Apply fix_metrics_to_sum_24 to all items to ensure sum = 24
        # Also recalculates energy_delivered = average_load × supply_hours
        # =====================================================================
        # Get period info from date_info for energy calculation
        period_info = date_info.get('target_date', None) if date_info else None
        overview_data = [fix_metrics_to_sum_24(item, mode=mode, period_info=period_info) for item in overview_data]
        
        # Current and previous period data
        current = overview_data[-1] if overview_data else {}
        previous = overview_data[-2] if len(overview_data) > 1 else {}
        
        # Calculate deltas
        def calc_delta(metric):
            if (metric in current and metric in previous and 
                previous[metric] is not None and previous[metric] != 0):
                return round(((current[metric] - previous[metric]) / previous[metric]) * 100, 2)
            return None
        
        # Format highlight metrics
        highlight_metrics = {
            "energy_delivered": {
                "value": current.get("energy_delivered", 0),
                "delta": calc_delta("energy_delivered")
            },
            "average_load": {
                "value": current.get("average_load", 0),
                "delta": calc_delta("average_load")
            },
            "interruptions": {
                "value": current.get("interruptions", 0),
                "delta": calc_delta("interruptions")
            }
        }
        
        # Supply and quality metrics with history
        supply_and_quality = {
            "supply_hours": {
                "current": current.get("avg_hours_supply", 0),
                "delta": calc_delta("avg_hours_supply"),
                "history": [
                    {
                        "month": item["month"],
                        "value": item.get("avg_hours_supply", 0)
                    }
                    for item in overview_data[:-1]  # Exclude current period
                ]
            },
            "interruption_duration": {
                "current": current.get("avg_interruption_duration", 0),
                "delta": calc_delta("avg_interruption_duration"),
                "history": [
                    {
                        "month": item["month"],
                        "value": item.get("avg_interruption_duration", 0)
                    }
                    for item in overview_data[:-1]
                ]
            },
            "turnaround_time": {
                "current": current.get("avg_turnaround_time", 0),
                "delta": calc_delta("avg_turnaround_time"),
                "history": [
                    {
                        "month": item["month"],
                        "value": item.get("avg_turnaround_time", 0)
                    }
                    for item in overview_data[:-1]
                ]
            }
        }
        
        # Technical breakdown
        technical_breakdown = {
            "feeder_count": {
                "value": current.get("feeder_count", 0),
                "delta": calc_delta("feeder_count")
            },
            "avg_daily_interruptions": {
                "value": current.get("avg_daily_interruptions", 0),
                "delta": calc_delta("avg_daily_interruptions")
            },
            "avg_turnaround": {
                "value": current.get("avg_turnaround_time", 0),
                "delta": calc_delta("avg_turnaround_time")
            },
            "customer_count": {
                "value": current.get("customer_count", 0),
                "delta": calc_delta("customer_count")
            },
            "availability_percentage": {
                "value": current.get("availability_percentage", 0),
                "delta": calc_delta("availability_percentage")
            },
            "saifi": {
                "value": current.get("saifi", 0),
                "delta": calc_delta("saifi")
            },
            "saidi": {
                "value": current.get("saidi", 0),
                "delta": calc_delta("saidi")
            }
        }
        
        # Interruption sources (last 4 periods)
        interruption_sources = []
        for i, item in enumerate(overview_data[-4:]):
            breakdown = item.get("interruption_breakdown", {})
            total_hours = sum(breakdown.values()) if breakdown else 0
            
            interruption_sources.append({
                "month": item["month"],
                "total": round(total_hours, 2),
                "delta": None,  # Calculate if needed
                "breakdown": breakdown
            })
        
        return {
            "highlight_metrics": highlight_metrics,
            "supply_and_quality": supply_and_quality,
            "technical_breakdown": technical_breakdown,
            "interruption_sources": interruption_sources,
            "load_trend": additional_data.get("load_trend", {"series": [], "date": None}),
            "_mode": mode,
            "_date_info": {
                "from_date": date_info.get('from_date', '').isoformat() if date_info.get('from_date') else None,
                "to_date": date_info.get('to_date', '').isoformat() if date_info.get('to_date') else None,
                "target_date": date_info.get('target_date', '').isoformat() if date_info.get('target_date') else None,
            },
            "_performance": {
                "total_periods": len(overview_data),
                "summary_sources": sum(1 for d in overview_data if "summary" in d.get("_source", "")),
                "fallback_sources": sum(1 for d in overview_data if "fallback" in d.get("_source", "")),
                "aggregated_sources": sum(1 for d in overview_data if "aggregated" in d.get("_source", "")),
            }
        }
    
    def _is_current_period(self, date_info):
        """Check if the date range includes current period"""
        today = date.today()
        
        if date_info['mode'] == 'monthly':
            current_month = today.replace(day=1)
            return current_month in date_info['periods']
        elif date_info['mode'] == 'yearly':
            current_year_start = today.replace(month=1, day=1)
            return current_year_start in date_info['periods']
        else:
            # For daily modes, check if target date is today or in the future
            target_date = date_info.get('target_date', today)
            return target_date >= today
    
    def _get_cache_key(self, mode, date_info, filter_params):
        """Generate cache key for the request - FIXED to handle all modes"""
        if mode == 'monthly':
            date_str = "_".join(m.strftime("%Y%m") for m in date_info['periods'])
        elif mode == 'yearly':
            date_str = "_".join(str(p.year) for p in date_info['periods'])
        else:
            # For daily, weekly, custom modes
            from_date = date_info.get('from_date')
            to_date = date_info.get('to_date')
            if from_date and to_date:
                date_str = f"{from_date}_{to_date}"
            else:
                # Fallback: use target_date if available
                target_date = date_info.get('target_date', date.today())
                date_str = f"{target_date}_{target_date}"
        
        filter_str = ""
        if filter_params['feeder']:
            filter_str = f"_f_{filter_params['feeder'].id}"
        elif filter_params['business_district']:
            filter_str = f"_d_{filter_params['business_district'].id}"
        elif filter_params['state']:
            filter_str = f"_s_{filter_params['state'].id}"
        
        # NOTE: We're not including load trend date in cache key 
        # because we handle that separately in the main get() method
        return f"technical_api_v2_{mode}_{date_str}{filter_str}"


# Add this helper function for yearly calculations that might be used elsewhere
def _calculate_single_period_metrics(feeder_ids, period_start, period_end):
    """Calculate metrics for a single period (used by yearly and other modes)"""
    from technical.models import HourlyLoad, FeederInterruption
    from commercial.models import Customer
    
    # Average supply hours across the period
    hourly_supply = HourlyLoad.objects.filter(
        feeder_id__in=feeder_ids,
        date__range=(period_start, period_end),
        load_mw__gt=0
    ).values('feeder', 'date').annotate(
        daily_hours=Count('hour')
    )
    avg_supply = hourly_supply.aggregate(avg=Avg('daily_hours'))['avg'] or 0
    
    # Interruption metrics
    interruptions = FeederInterruption.objects.filter(
        feeder_id__in=feeder_ids,
        occurred_at__date__range=(period_start, period_end)
    )
    
    total_interruptions = interruptions.count()
    
    # Average duration for restored interruptions only
    restored_interruptions = interruptions.filter(restored_at__isnull=False)
    if restored_interruptions.exists():
        total_duration = sum(
            (interruption.restored_at - interruption.occurred_at).total_seconds() / 3600
            for interruption in restored_interruptions
        )
        avg_duration = total_duration / restored_interruptions.count()
    else:
        avg_duration = 0
    
    # Energy delivered (sum for the period)
    try:
        from technical.models import FeederEnergyDaily
        period_energy = FeederEnergyDaily.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(period_start, period_end)
        ).aggregate(total=Sum('energy_mwh'))['total'] or 0
    except Exception:
        period_energy = 0
    
    # Customer count
    customer_count = Customer.objects.filter(transformer__feeder_id__in=feeder_ids).count()
    
    return {
        "avg_supply": round(float(avg_supply), 2),
        "avg_duration": round(float(avg_duration), 2),
        "turnaround_time": round(float(avg_duration), 2),  # Same as duration
        "interruptions": total_interruptions,
        "energy_delivered": float(period_energy),
        "feeder_count": len(feeder_ids),
        "customer_count": customer_count,
    }


class TechnicalHealthAPIView(APIView):
    """
    Health check endpoint for technical summary data.
    """
    
    def get(self, request):
        # Get filter parameters
        filter_params = OptimizedTechnicalOverviewAPIView()._parse_filter_params(request)
        
        # Build query for filtered summaries
        query_filters = {}
        for key, value in filter_params.items():
            if value is not None:
                query_filters[key] = value
        
        # Get recent monthly summaries
        recent_monthly_summaries = MonthlyTechnicalSummary.objects.filter(
            **query_filters
        ).order_by('-month')[:6]
        
        # Get recent daily summaries
        recent_daily_summaries = DailyTechnicalSummary.objects.filter(
            **query_filters
        ).order_by('-date')[:7]
        
        monthly_info = [
            {
                'month': s.month.strftime('%Y-%m'),
                'filter_level': s.filter_level,
                'calculated_at': s.calculated_at.isoformat(),
                'has_complete_data': s.has_complete_data,
                'calculation_duration_ms': int(s.calculation_duration.total_seconds() * 1000) if s.calculation_duration else None,
                'availability_percentage': s.availability_percentage,
            }
            for s in recent_monthly_summaries
        ]
        
        daily_info = [
            {
                'date': s.date.strftime('%Y-%m-%d'),
                'filter_level': s.filter_level,
                'calculated_at': s.calculated_at.isoformat(),
                'has_complete_data': s.has_complete_data,
                'calculation_duration_ms': int(s.calculation_duration.total_seconds() * 1000) if s.calculation_duration else None,
                'availability_percentage': s.availability_percentage,
            }
            for s in recent_daily_summaries
        ]
        
        # Calculate health metrics
        total_monthly = len(recent_monthly_summaries)
        total_daily = len(recent_daily_summaries)
        incomplete_monthly = sum(1 for s in recent_monthly_summaries if not s.has_complete_data)
        incomplete_daily = sum(1 for s in recent_daily_summaries if not s.has_complete_data)
        
        monthly_health_score = max(0, 100 - (incomplete_monthly * 20)) if total_monthly > 0 else 0
        daily_health_score = max(0, 100 - (incomplete_daily * 15)) if total_daily > 0 else 0
        
        return Response({
            'filter_applied': {
                'state': filter_params['state'].name if filter_params['state'] else None,
                'business_district': filter_params['business_district'].name if filter_params['business_district'] else None,
                'feeder': filter_params['feeder'].slug if filter_params['feeder'] else None,
            },
            'monthly_summaries': {
                'total_summaries': total_monthly,
                'incomplete_summaries': incomplete_monthly,
                'health_score': monthly_health_score,
                'recent_summaries': monthly_info,
            },
            'daily_summaries': {
                'total_summaries': total_daily,
                'incomplete_summaries': incomplete_daily,
                'health_score': daily_health_score,
                'recent_summaries': daily_info,
            },
            'recommendations': self._get_recommendations(filter_params, recent_monthly_summaries, recent_daily_summaries),
        })
    
    def _get_recommendations(self, filter_params, recent_monthly, recent_daily):
        """Generate recommendations based on data quality"""
        recommendations = []
        
        if not recent_monthly:
            recommendations.append("No monthly technical summaries found. Run: python manage.py populate_monthly_technical_summary")
        
        if not recent_daily:
            recommendations.append("No daily technical summaries found. Run: python manage.py populate_daily_technical_summary")
        
        incomplete_monthly = sum(1 for s in recent_monthly if not s.has_complete_data)
        if incomplete_monthly > 2:
            recommendations.append(f"{incomplete_monthly} monthly summaries have incomplete data. Check source data quality.")
        
        incomplete_daily = sum(1 for s in recent_daily if not s.has_complete_data)
        if incomplete_daily > 3:
            recommendations.append(f"{incomplete_daily} daily summaries have incomplete data. Check source data quality.")
        
        current_month = date.today().replace(day=1)
        current_monthly = next((s for s in recent_monthly if s.month == current_month), None)
        if not current_monthly:
            cmd = "python manage.py populate_monthly_technical_summary --current-month"
            if filter_params['state']:
                cmd += f" --state '{filter_params['state'].name}'"
            if filter_params['business_district']:
                cmd += f" --district '{filter_params['business_district'].name}'"
            if filter_params['feeder']:
                cmd += f" --feeder '{filter_params['feeder'].slug}'"
            recommendations.append(f"Current month summary missing. Run: {cmd}")
        
        today = date.today()
        current_daily = next((s for s in recent_daily if s.date == today), None)
        if not current_daily:
            cmd = f"python manage.py populate_daily_technical_summary --today"
            if filter_params['state']:
                cmd += f" --state '{filter_params['state'].name}'"
            if filter_params['business_district']:
                cmd += f" --district '{filter_params['business_district'].name}'"
            if filter_params['feeder']:
                cmd += f" --feeder '{filter_params['feeder'].slug}'"
            recommendations.append(f"Today's daily summary missing. Run: {cmd}")
        
        return recommendations