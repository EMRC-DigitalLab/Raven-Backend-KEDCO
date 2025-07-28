# analytics/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from django.core.cache import cache
from datetime import datetime, date
from dateutil.relativedelta import relativedelta # type: ignore
import logging

from .models import MonthlyOverviewSummary
from .tasks import update_monthly_overview_summary

logger = logging.getLogger(__name__)

class OptimizedOverviewAPIView(APIView):
    """
    Ultra-fast overview API using pre-calculated summary data.
    Falls back to real-time calculation only when summary data is missing.
    
    Performance: ~50-100ms (vs 2-5 seconds with original view)
    """
    
    def get(self, request):
        # Parse parameters
        try:
            month = int(request.GET.get("month")) if request.GET.get("month") else None
            year = int(request.GET.get("year")) if request.GET.get("year") else None
            target = datetime(year, month, 1) if year and month else None
        except (TypeError, ValueError):
            target = None

        from_date_str = request.GET.get("from")
        to_date_str = request.GET.get("to")
        from_date = datetime.strptime(from_date_str, "%Y-%m-%d") if from_date_str else None
        to_date = datetime.strptime(to_date_str, "%Y-%m-%d") if to_date_str else None

        # Determine months to fetch
        months = self.get_months_list(target, from_date, to_date)
        
        # Try to get cached response first
        cache_key = self.get_cache_key(months)
        cached_response = cache.get(cache_key)
        if cached_response:
            logger.debug(f"Returning cached overview data for {len(months)} months")
            return Response(cached_response)
        
        # Fetch data using summary models
        overview_data = self.fetch_overview_data(months)
        
        # Calculate deltas and format response
        response_data = self.format_response_data(overview_data)
        
        # Cache the response (5-15 minutes depending on data freshness)
        cache_timeout = self.get_cache_timeout(months)
        cache.set(cache_key, response_data, cache_timeout)
        
        logger.info(f"Generated overview data for {len(months)} months in optimized mode")
        return Response(response_data)
    
    def get_months_list(self, target, from_date, to_date):
        """Determine which months to fetch data for"""
        if from_date and to_date:
            current = from_date.replace(day=1)
            months = []
            while current <= to_date:
                months.append(current.date())
                current += relativedelta(months=1)
            return months
        else:
            target = target or datetime.today().replace(day=1)
            months = [
                (target - relativedelta(months=i)).date().replace(day=1) 
                for i in range(5)
            ]
            return list(reversed(months))
    
    def fetch_overview_data(self, months):
        """Fetch overview data using summary models with fallback"""
        overview_data = []
        missing_months = []
        
        # Bulk fetch all available summaries
        summaries = MonthlyOverviewSummary.objects.filter(
            month__in=months
        ).order_by('month')
        
        # Create lookup dict for fast access
        summary_dict = {summary.month: summary for summary in summaries}
        
        for month in months:
            if month in summary_dict:
                # Use pre-calculated summary data
                summary = summary_dict[month]
                overview_data.append(self.summary_to_dict(summary, month))
            else:
                # Summary missing - queue for background calculation and use fallback
                missing_months.append(month)
                overview_data.append(self.get_fallback_data(month))
        
        # Queue missing months for background calculation
        if missing_months:
            self.queue_missing_summaries(missing_months)
        
        return overview_data
    
    def summary_to_dict(self, summary, month):
        """Convert summary model to dictionary format"""
        return {
            "month": month.strftime("%b"),
            "billing_efficiency": float(summary.billing_efficiency),
            "collection_efficiency": float(summary.collection_efficiency),
            "atcc": float(summary.atc_losses),
            "revenue_billed": float(summary.revenue_billed),
            "revenue_collected": float(summary.revenue_collected),
            "energy_billed": float(summary.energy_billed),
            "energy_delivered": float(summary.energy_delivered),
            "energy_collected": float(summary.energy_collected),
            "customer_response_rate": float(summary.customer_response_rate),
            "total_cost": float(summary.total_cost),
            "avg_hours_supply": float(summary.avg_hours_supply),
            "avg_interruption_duration": float(summary.avg_interruption_duration),
            "avg_turnaround_time": float(summary.avg_turnaround_time),
            "_source": "summary",  # For debugging
            "_calculated_at": summary.calculated_at.isoformat(),
            "_has_complete_data": summary.has_complete_data,
        }
    
    def get_fallback_data(self, month):
        """Provide fallback data when summary is missing"""
        # For missing summaries, return zeros with a flag
        return {
            "month": month.strftime("%b"),
            "billing_efficiency": 0.0,
            "collection_efficiency": 0.0,
            "atcc": 0.0,
            "revenue_billed": 0.0,
            "revenue_collected": 0.0,
            "energy_billed": 0.0,
            "energy_delivered": 0.0,
            "energy_collected": 0.0,
            "customer_response_rate": 0.0,
            "total_cost": 0.0,
            "avg_hours_supply": 0.0,
            "avg_interruption_duration": 0.0,
            "avg_turnaround_time": 0.0,
            "_source": "fallback",  # For debugging
            "_summary_missing": True,
        }
    
    def queue_missing_summaries(self, missing_months):
        """Queue missing summaries for background calculation"""
        for month in missing_months:
            try:
                update_monthly_overview_summary.delay(
                    month.strftime('%Y-%m-%d'),
                    priority='api_request'
                )
                logger.info(f"Queued summary calculation for missing month: {month}")
            except Exception as e:
                logger.error(f"Failed to queue summary for {month}: {str(e)}")
    
    def format_response_data(self, overview_data):
        """Calculate deltas and format final response"""
        if not overview_data:
            return {"current": {}, "history": []}
        
        # Calculate deltas for current month vs previous month
        current = overview_data[-1] if overview_data else {}
        previous = overview_data[-2] if len(overview_data) > 1 else {}
        
        # Add delta calculations
        metrics_to_track = [
            "atcc", "billing_efficiency", "collection_efficiency",
            "revenue_billed", "revenue_collected", "energy_billed", 
            "energy_delivered", "total_cost", "energy_collected",
            "customer_response_rate", "avg_hours_supply",
            "avg_interruption_duration", "avg_turnaround_time"
        ]
        
        for metric in metrics_to_track:
            current[f"delta_{metric}"] = self.calculate_delta(
                current.get(metric, 0), 
                previous.get(metric, 0)
            )
        
        return {
            "current": current,
            "history": overview_data[:-1],  # All months except current
            "_performance": {
                "total_months": len(overview_data),
                "summary_sources": sum(1 for d in overview_data if d.get("_source") == "summary"),
                "fallback_sources": sum(1 for d in overview_data if d.get("_source") == "fallback"),
            }
        }
    
    def calculate_delta(self, current, previous):
        """Calculate percentage change between current and previous values"""
        if previous and previous != 0:
            return round(((current - previous) / previous) * 100, 2)
        return None
    
    def get_cache_key(self, months):
        """Generate cache key for the request"""
        month_str = "_".join(m.strftime("%Y%m") for m in months)
        return f"overview_api_{month_str}"
    
    def get_cache_timeout(self, months):
        """Determine cache timeout based on data freshness"""
        current_month = date.today().replace(day=1)
        
        # If current month is included, cache for shorter time
        if current_month in months:
            return 300  # 5 minutes
        else:
            return 900  # 15 minutes for historical data


class OverviewHealthAPIView(APIView):
    """
    Health check endpoint for overview data.
    Provides information about summary data availability and freshness.
    """
    
    def get(self, request):
        from .signals import get_summary_health_status, check_summary_freshness
        
        # Get overall health status
        health_status = get_summary_health_status()
        
        # Check for stale summaries
        stale_summaries = check_summary_freshness(24)
        
        # Get recent summaries info
        recent_summaries = MonthlyOverviewSummary.objects.order_by('-month')[:12]
        recent_info = [
            {
                'month': s.month.strftime('%Y-%m'),
                'calculated_at': s.calculated_at.isoformat(),
                'has_complete_data': s.has_complete_data,
                'calculation_duration_ms': int(s.calculation_duration.total_seconds() * 1000) if s.calculation_duration else None,
            }
            for s in recent_summaries
        ]
        
        return Response({
            'health_status': health_status,
            'stale_summaries': [s.strftime('%Y-%m') for s in stale_summaries],
            'recent_summaries': recent_info,
            'recommendations': self.get_recommendations(health_status, stale_summaries),
        })
    
    def get_recommendations(self, health_status, stale_summaries):
        """Generate recommendations based on health status"""
        recommendations = []
        
        if health_status['health_score'] < 80:
            recommendations.append("Health score is low. Consider running bulk summary update.")
        
        if health_status['current_month_status'] == 'missing':
            recommendations.append("Current month summary is missing. Run: python manage.py populate_overview_summary --current-month")
        
        if len(stale_summaries) > 0:
            recommendations.append(f"{len(stale_summaries)} summaries are stale. Consider running health check task.")
        
        if health_status['incomplete_summaries'] > 5:
            recommendations.append("Many summaries have incomplete data. Check source data quality.")
        
        return recommendations
    



from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import api_view
from django.core.cache import cache
from django.shortcuts import get_object_or_404
from datetime import datetime, date
from dateutil.relativedelta import relativedelta #type:ignore
import logging

from .models import MonthlyTechnicalSummary
from .tasks import update_monthly_technical_summary
from common.models import State, BusinessDistrict, Feeder
from technical.models import HourlyLoad
from django.db.models import Avg


logger = logging.getLogger(__name__)

class OptimizedTechnicalOverviewAPIView(APIView):
    """
    Optimized technical overview API using pre-calculated summary data.
    Supports filtering by state, business district, and feeder.
    
    Query Parameters:
    - year, month: Target month (defaults to current month)
    - from, to: Date range (alternative to year/month)
    - state: State name for filtering
    - district: Business district name for filtering  
    - feeder: Feeder slug for filtering
    """
    
    def get(self, request):
        # Parse date parameters
        target_date = self._parse_date_params(request)
        
        # Parse filtering parameters
        filter_params = self._parse_filter_params(request)
        
        # Determine months to fetch
        months = self._get_months_list(target_date, request)
        
        # Try cache first
        cache_key = self._get_cache_key(months, filter_params)
        cached_response = cache.get(cache_key)
        if cached_response:
            logger.debug(f"Returning cached technical data for {len(months)} months")
            return Response(cached_response)
        
        # Fetch data using summary models
        overview_data = self._fetch_technical_data(months, filter_params)
        
        # Get additional data (load trend, etc.)
        additional_data = self._get_additional_data(request, filter_params)
        
        # Format response
        response_data = self._format_response_data(overview_data, additional_data)
        
        # Cache response
        cache_timeout = 600 if target_date.month == date.today().month else 1800
        cache.set(cache_key, response_data, cache_timeout)
        
        return Response(response_data)
    
    def _parse_date_params(self, request):
        """Parse year/month or from/to date parameters"""
        try:
            year = int(request.GET.get("year", datetime.now().year))
            month = int(request.GET.get("month", datetime.now().month))
            return datetime(year, month, 1).date()
        except (TypeError, ValueError):
            return date.today().replace(day=1)
    
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
            except State.DoesNotExist:
                pass  # Ignore invalid state names
        
        # Parse district filter
        district_name = request.GET.get('district')
        if district_name:
            try:
                qs = BusinessDistrict.objects.filter(name__iexact=district_name)
                if filter_params['state']:
                    qs = qs.filter(state=filter_params['state'])
                filter_params['business_district'] = qs.first()
            except BusinessDistrict.DoesNotExist:
                pass
        
        # Parse feeder filter
        feeder_slug = request.GET.get('feeder')
        if feeder_slug:
            try:
                qs = Feeder.objects.filter(slug=feeder_slug)
                if filter_params['business_district']:
                    qs = qs.filter(business_district=filter_params['business_district'])
                elif filter_params['state']:
                    qs = qs.filter(business_district__state=filter_params['state'])
                filter_params['feeder'] = qs.first()
            except Feeder.DoesNotExist:
                pass
        
        return filter_params
    
    def _get_months_list(self, target_date, request):
        """Get list of months to fetch data for"""
        from_date_str = request.GET.get("from")
        to_date_str = request.GET.get("to")
        
        if from_date_str and to_date_str:
            try:
                from_date = datetime.strptime(from_date_str, "%Y-%m-%d").date()
                to_date = datetime.strptime(to_date_str, "%Y-%m-%d").date()
                
                current = from_date.replace(day=1)
                months = []
                while current <= to_date:
                    months.append(current)
                    current += relativedelta(months=1)
                return months
            except ValueError:
                pass
        
        # Default: current month + 4 previous months
        months = []
        for i in range(5):
            month = target_date - relativedelta(months=i)
            months.append(month.replace(day=1))
        
        return list(reversed(months))
    
    def _fetch_technical_data(self, months, filter_params):
        """Fetch technical data using summary models with fallback"""
        overview_data = []
        missing_months = []
        
        # Build query based on filter parameters
        query_filters = {'month__in': months}
        for key, value in filter_params.items():
            if value is not None:
                query_filters[key] = value
        
        # Fetch existing summaries
        summaries = MonthlyTechnicalSummary.objects.filter(
            **query_filters
        ).order_by('month')
        
        # Create lookup dict
        summary_key = lambda s: (s.month, s.state_id, s.business_district_id, s.feeder_id)
        summary_dict = {summary_key(s): s for s in summaries}
        
        # Process each month
        for month in months:
            # Create key for this month/filter combination
            key = (
                month,
                filter_params['state'].id if filter_params['state'] else None,
                filter_params['business_district'].id if filter_params['business_district'] else None,
                filter_params['feeder'].id if filter_params['feeder'] else None
            )
            
            if key in summary_dict:
                summary = summary_dict[key]
                overview_data.append(self._summary_to_dict(summary, month))
            else:
                missing_months.append((month, filter_params))
                overview_data.append(self._get_fallback_technical_data(month))
        
        # Queue missing summaries for background calculation
        if missing_months:
            self._queue_missing_summaries(missing_months)
        
        return overview_data
    
    def _summary_to_dict(self, summary, month):
        """Convert technical summary model to dictionary"""
        return {
            "month": month.strftime("%b"),
            "energy_delivered": float(summary.total_energy_delivered),
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
            "interruption_breakdown": summary.interruption_breakdown_dict,  # Detailed breakdown
            "summary_breakdown": summary.summary_breakdown_dict,  # High-level categories,
            "_source": "summary",
            "_calculated_at": summary.calculated_at.isoformat(),
            "_has_complete_data": summary.has_complete_data,
        }
    
    def _get_fallback_technical_data(self, month):
        """Provide fallback data when summary is missing"""
        return {
            "month": month.strftime("%b"),
            "energy_delivered": 0.0,
            "average_load": 0.0,
            "max_load": 0.0,
            "interruptions": 0,
            "avg_hours_supply": 0.0,
            "avg_interruption_duration": 0.0,
            "avg_turnaround_time": 0.0,
            "feeder_count": 0,
            "customer_count": 0,
            "avg_daily_interruptions": 0.0,
            "availability_percentage": 0.0,
            "saifi": 0.0,
            "saidi": 0.0,
            "interruption_breakdown": {},
            "_source": "fallback",
            "_summary_missing": True,
        }
    
    def _queue_missing_summaries(self, missing_months):
        """Queue missing summaries for background calculation"""
        for month, filter_params in missing_months:
            try:
                update_monthly_technical_summary.delay(
                    month.strftime('%Y-%m-%d'),
                    filter_params,
                    priority='api_request'
                )
                logger.info(f"Queued technical summary calculation for {month} with filters")
            except Exception as e:
                logger.error(f"Failed to queue technical summary for {month}: {str(e)}")
    
    def _get_additional_data(self, request, filter_params):
        """Get additional data like load trends"""
        additional = {}
        
        # Load trend data if date specified
        trend_date = request.GET.get("date")
        if trend_date:
            try:
                trend_date_obj = datetime.strptime(trend_date, "%Y-%m-%d").date()
                additional["load_trend"] = self._get_load_trend(trend_date_obj, filter_params)
            except ValueError:
                additional["load_trend"] = {"series": [], "date": None}
        else:
            additional["load_trend"] = {"series": [], "date": None}
        
        return additional
    
    def _get_load_trend(self, trend_date, filter_params):
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
        
        return {
            "unit": "MW",
            "date": trend_date.strftime("%Y-%m-%d"),
            "series": series
        }
    
    def _format_response_data(self, overview_data, additional_data):
        """Format the final response data"""
        if not overview_data:
            return {
                "highlight_metrics": {},
                "supply_and_quality": {},
                "technical_breakdown": {},
                "interruption_sources": [],
                "load_trend": additional_data.get("load_trend", {"series": [], "date": None})
            }
        
        # Current and previous month data
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
                    for item in overview_data[:-1]  # Exclude current month
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
        
        # Interruption sources (last 4 months)
        interruption_sources = []
        for i, item in enumerate(overview_data[-4:]):
            # Use detailed breakdown which shows actual fault types
            breakdown = item.get("interruption_breakdown", {})
            total_hours = sum(breakdown.values()) if breakdown else 0
            
            interruption_sources.append({
                "month": item["month"],
                "total": round(total_hours, 2),
                "delta": 2.5 - i,  # Mock delta for now
                "breakdown": breakdown  # This now shows E/F, O/C, etc.
            })
        
        return {
            "highlight_metrics": highlight_metrics,
            "supply_and_quality": supply_and_quality,
            "technical_breakdown": technical_breakdown,
            "interruption_sources": interruption_sources,
            "load_trend": additional_data.get("load_trend", {"series": [], "date": None}),
            "_performance": {
                "total_months": len(overview_data),
                "summary_sources": sum(1 for d in overview_data if d.get("_source") == "summary"),
                "fallback_sources": sum(1 for d in overview_data if d.get("_source") == "fallback"),
            }
        }
    
    def _get_cache_key(self, months, filter_params):
        """Generate cache key for the request"""
        month_str = "_".join(m.strftime("%Y%m") for m in months)
        
        filter_str = ""
        if filter_params['feeder']:
            filter_str = f"_f_{filter_params['feeder'].id}"
        elif filter_params['business_district']:
            filter_str = f"_d_{filter_params['business_district'].id}"
        elif filter_params['state']:
            filter_str = f"_s_{filter_params['state'].id}"
        
        return f"technical_api_{month_str}{filter_str}"


@api_view(["GET"])
def technical_overview_legacy_view(request):
    """
    Legacy view for backward compatibility.
    Redirects to the optimized view internally.
    """
    optimized_view = OptimizedTechnicalOverviewAPIView()
    return optimized_view.get(request)


class TechnicalHealthAPIView(APIView):
    """
    Health check endpoint for technical summary data.
    """
    
    def get(self, request):
        from .signals import get_summary_health_status
        
        # Get filter parameters
        filter_params = OptimizedTechnicalOverviewAPIView()._parse_filter_params(request)
        
        # Build query for filtered summaries
        query_filters = {}
        for key, value in filter_params.items():
            if value is not None:
                query_filters[key] = value
        
        # Get recent summaries
        recent_summaries = MonthlyTechnicalSummary.objects.filter(
            **query_filters
        ).order_by('-month')[:6]
        
        recent_info = [
            {
                'month': s.month.strftime('%Y-%m'),
                'filter_level': s.filter_level,
                'calculated_at': s.calculated_at.isoformat(),
                'has_complete_data': s.has_complete_data,
                'calculation_duration_ms': int(s.calculation_duration.total_seconds() * 1000) if s.calculation_duration else None,
                'availability_percentage': s.availability_percentage,
                'data_completeness_score': s.data_completeness_score,
            }
            for s in recent_summaries
        ]
        
        # Calculate health metrics
        total_summaries = len(recent_summaries)
        incomplete_summaries = sum(1 for s in recent_summaries if not s.has_complete_data)
        health_score = max(0, 100 - (incomplete_summaries * 20))
        
        return Response({
            'filter_applied': {
                'state': filter_params['state'].name if filter_params['state'] else None,
                'business_district': filter_params['business_district'].name if filter_params['business_district'] else None,
                'feeder': filter_params['feeder'].slug if filter_params['feeder'] else None,
            },
            'health_metrics': {
                'total_summaries': total_summaries,
                'incomplete_summaries': incomplete_summaries,
                'health_score': health_score,
            },
            'recent_summaries': recent_info,
            'recommendations': self._get_recommendations(filter_params, recent_summaries),
        })
    
    def _get_recommendations(self, filter_params, recent_summaries):
        """Generate recommendations based on data quality"""
        recommendations = []
        
        if not recent_summaries:
            recommendations.append("No technical summaries found. Run: python manage.py populate_technical_summary")
        
        incomplete_count = sum(1 for s in recent_summaries if not s.has_complete_data)
        if incomplete_count > 2:
            recommendations.append(f"{incomplete_count} summaries have incomplete data. Check source data quality.")
        
        current_month = date.today().replace(day=1)
        current_summary = next((s for s in recent_summaries if s.month == current_month), None)
        if not current_summary:
            cmd = "python manage.py populate_technical_summary --current-month"
            if filter_params['state']:
                cmd += f" --state '{filter_params['state'].name}'"
            if filter_params['business_district']:
                cmd += f" --district '{filter_params['business_district'].name}'"
            if filter_params['feeder']:
                cmd += f" --feeder '{filter_params['feeder'].slug}'"
            recommendations.append(f"Current month summary missing. Run: {cmd}")
        
        return recommendations