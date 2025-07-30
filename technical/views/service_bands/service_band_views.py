from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.db.models import Sum, Avg, Max, Q
from django.core.cache import cache
from datetime import datetime, timedelta
import hashlib
import logging
from common.models import State, Band, Feeder
from technical.models import HourlyLoad, FeederInterruption, FeederEnergyDaily
from financial.models import NBETInvoice, MOInvoice

logger = logging.getLogger(__name__)


@api_view(["GET"])
def technical_service_band_summary(request):
    """
    Technical summary for all service bands with optional state filtering.
    
    Returns metrics for each service band including:
    - Total cost (NBET + MO allocation based on energy delivered)
    - Duration of interruption (average hours including ongoing outages)
    - Turnaround time (same as duration)
    - Feeder tripping count (total fault interruptions excluding load shedding/maintenance)
    - Number of feeders in the band
    - Customer count (from billing data)
    - Average peak load
    
    Query Parameters:
    - year, month: Specific month (defaults to current month)
    - from, to: Date range (alternative to year/month)
    - state: State name for filtering (optional)
    """
    
    # Parse date parameters
    target_date = _parse_date_params(request)
    
    # Parse state filter
    state_filter = _parse_state_filter(request)
    
    # Try cache first
    cache_key = _get_band_cache_key(target_date, state_filter)
    cached_response = cache.get(cache_key)
    if cached_response:
        return Response(cached_response)
    
    # Calculate month boundaries
    month_start, month_end = _get_month_range(target_date.year, target_date.month)
    
    # Get all service bands
    bands = Band.objects.all().order_by('name')
    
    band_data = []
    
    for band in bands:
        try:
            # Try to get from summary first
            band_metrics = _get_band_metrics_from_summary(band, target_date, state_filter)
            
            if not band_metrics:
                # Fallback to real-time calculation
                band_metrics = _calculate_band_metrics_realtime(
                    band, month_start, month_end, state_filter
                )
            
            # Always include the band, even with zero data
            band_data.append({
                "band": band.name,
                "band_description": band.description,
                "metrics": band_metrics or {
                    "total_cost": 0.0,
                    "duration_of_interruption": 0.0,
                    "turnaround_time": 0.0,
                    "feeder_tripping_rate": 0.0,
                    "number_of_feeders": 0,
                    "customer_count": 0,
                    "average_peak_load": 0.0,
                    "_source": "no_data"
                }
            })
                
        except Exception as e:
            logger.error(f"Error calculating metrics for band {band.name}: {str(e)}")
            # Include band with zero metrics on error
            band_data.append({
                "band": band.name,
                "band_description": band.description,
                "metrics": {
                    "total_cost": 0.0,
                    "duration_of_interruption": 0.0,
                    "turnaround_time": 0.0,
                    "feeder_tripping_count": 0,
                    "number_of_feeders": 0,
                    "customer_count": 0,
                    "average_peak_load": 0.0,
                    "_source": "error"
                }
            })
    
    response_data = {
        "period": f"{target_date.strftime('%Y-%m')}",
        "state_filter": state_filter.name if state_filter else None,
        "bands": band_data,
        "metadata": {
            "total_bands": len(band_data),
            "bands_with_data": len([b for b in band_data if b["metrics"]["_source"] not in ["no_data", "no_feeders", "error", "calculation_error"]]),
            "bands_without_data": len([b for b in band_data if b["metrics"]["_source"] in ["no_data", "no_feeders", "error", "calculation_error"]])
        }
    }
    
    # Cache for 10 minutes (current month) or 1 hour (historical)
    current_month = datetime.now().date().replace(day=1)
    cache_timeout = 600 if target_date >= current_month else 3600
    
    cache.set(cache_key, response_data, cache_timeout)
    
    return Response(response_data)


def _parse_date_params(request):
    """Parse date parameters and return target month date"""
    try:
        year = int(request.GET.get("year", datetime.now().year))
        month = int(request.GET.get("month", datetime.now().month))
        return datetime(year, month, 1).date()
    except (TypeError, ValueError):
        return datetime.now().date().replace(day=1)


def _parse_state_filter(request):
    """Parse and validate state filter parameter"""
    state_name = request.GET.get('state')
    if state_name:
        try:
            return State.objects.get(name__iexact=state_name)
        except State.DoesNotExist:
            pass
    return None


def _get_band_cache_key(target_date, state_filter):
    """Generate cache key for band technical summary"""
    state_str = f"_state_{state_filter.id}" if state_filter else ""
    cache_str = f"band_tech_{target_date.strftime('%Y_%m')}{state_str}"
    return hashlib.md5(cache_str.encode()).hexdigest()[:16]


def _get_month_range(year, month):
    """Get start and end dates for a given year/month"""
    start = datetime(year, month, 1).date()
    if month == 12:
        end = datetime(year + 1, 1, 1).date() - timedelta(days=1)
    else:
        end = datetime(year, month + 1, 1).date() - timedelta(days=1)
    return start, end


def _get_band_metrics_from_summary(band, target_date, state_filter):
    """
    Try to get band metrics from pre-calculated summary data.
    Returns None if summary data is not available.
    """
    # For now, we don't have band-level summaries, so return None
    # This would be implemented when we create MonthlyBandTechnicalSummary model
    return None


def _calculate_band_metrics_realtime(band, month_start, month_end, state_filter):
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
        "total_cost": 0.0,
        "duration_of_interruption": 0.0,
        "turnaround_time": 0.0,
        "feeder_tripping_count": 0,
        "number_of_feeders": len(feeder_ids),  # Always show feeder count
        "customer_count": 0,
        "average_peak_load": 0.0,
        "_source": "realtime"
    }
    
    # If no feeders, return default metrics
    if not feeder_ids:
        default_metrics["_source"] = "no_feeders"
        return default_metrics
    
    try:
        # 1. Total Cost Calculation
        total_cost = _calculate_band_total_cost(feeder_ids, month_start, month_end, state_filter)
        
        # 2. Interruption Metrics
        interruption_metrics = _calculate_band_interruption_metrics(feeder_ids, month_start, month_end)
        
        # 3. Infrastructure Metrics
        infrastructure_metrics = _calculate_band_infrastructure_metrics(feeder_ids, month_start, month_end)
        
        return {
            "total_cost": float(total_cost),
            "duration_of_interruption": interruption_metrics['avg_duration'],
            "turnaround_time": interruption_metrics['avg_turnaround_time'],
            "feeder_tripping_count": interruption_metrics['tripping_count'],
            "number_of_feeders": infrastructure_metrics['feeder_count'],
            "customer_count": infrastructure_metrics['customer_count'],
            "average_peak_load": infrastructure_metrics['avg_peak_load'],
            "_source": "realtime"
        }
        
    except Exception as e:
        logger.error(f"Error in band metrics calculation: {str(e)}")
        default_metrics["_source"] = "calculation_error"
        return default_metrics


def _calculate_band_total_cost(feeder_ids, month_start, month_end, state_filter):
    """Calculate total cost for the band using energy-based allocation"""
    from decimal import Decimal
    
    # Return 0 if no feeders
    if not feeder_ids:
        return Decimal('0')
    
    try:
        # Get total energy delivered by all feeders (for calculating shares)
        if state_filter:
            # Get all feeders in the state for total energy calculation
            all_state_feeders = Feeder.objects.filter(
                business_district__state=state_filter
            ).values_list('id', flat=True)
            
            total_energy_query = FeederEnergyDaily.objects.filter(
                feeder_id__in=all_state_feeders,
                date__range=(month_start, month_end)
            )
        else:
            # National level - all feeders
            total_energy_query = FeederEnergyDaily.objects.filter(
                date__range=(month_start, month_end)
            )
        
        total_energy_delivered = total_energy_query.aggregate(
            total=Sum('energy_mwh')
        )['total'] or Decimal('0')
        
        # If no total energy data, return 0 (don't fail)
        if total_energy_delivered == 0:
            return Decimal('0')
        
        # Get energy delivered by feeders in this band
        band_energy_delivered = FeederEnergyDaily.objects.filter(
            feeder_id__in=feeder_ids,
            date__range=(month_start, month_end)
        ).aggregate(
            total=Sum('energy_mwh')
        )['total'] or Decimal('0')
        
        # Calculate band's share of total energy
        if total_energy_delivered > 0 and band_energy_delivered > 0:
            band_energy_share = band_energy_delivered / total_energy_delivered
        else:
            return Decimal('0')
        
        # Get NBET costs for the month
        nbet_costs = NBETInvoice.objects.filter(
            month__year=month_start.year,
            month__month=month_start.month
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        # Get MO costs for the month
        mo_costs = MOInvoice.objects.filter(
            month__year=month_start.year,
            month__month=month_start.month
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        # Allocate costs based on energy share
        total_cost = (nbet_costs + mo_costs) * band_energy_share
        
        return total_cost
        
    except Exception as e:
        logger.error(f"Error calculating band total cost: {str(e)}")
        return Decimal('0')


def _calculate_band_interruption_metrics(feeder_ids, month_start, month_end):
    """Calculate interruption-related metrics for the band with improved logic"""
    
    # Get all interruptions for the band's feeders
    all_interruptions = FeederInterruption.objects.filter(
        feeder_id__in=feeder_ids,
        occurred_at__date__range=(month_start, month_end)
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
    
    period_end = datetime.combine(month_end, datetime.max.time())
    
    for interruption in all_interruptions:
        if interruption.restored_at:
            # Resolved interruption - use actual duration
            duration = (interruption.restored_at - interruption.occurred_at).total_seconds() / 3600
        else:
            # Ongoing interruption - calculate duration to end of period
            duration = (period_end - interruption.occurred_at).total_seconds() / 3600
        
        total_duration_hours += duration
        interruption_count += 1
    
    # Calculate average duration
    avg_duration = total_duration_hours / interruption_count if interruption_count > 0 else 0.0
    
    # Feeder tripping count = total fault interruptions (excludes load shedding and maintenance)
    tripping_count = total_fault_interruptions
    
    return {
        'avg_duration': round(avg_duration, 2),
        'avg_turnaround_time': round(avg_duration, 2),  # Same as duration for restoration
        'tripping_count': tripping_count
    }


def _calculate_band_infrastructure_metrics(feeder_ids, month_start, month_end):
    """Calculate infrastructure-related metrics for the band using commercial data"""
    from commercial.models import MonthlyCommercialSummary
    
    # Feeder count
    feeder_count = len(feeder_ids)
    
    # Get customer count from commercial data (customers actually billed)
    # Get all transformers connected to feeders in this band
    from common.models import DistributionTransformer
    
    transformer_ids = DistributionTransformer.objects.filter(
        feeder_id__in=feeder_ids
    ).values_list('id', flat=True)
    
    # Get customer count from monthly commercial summary
    month_date = month_start.replace(day=1)  # Ensure it's first day of month
    
    customer_count = MonthlyCommercialSummary.objects.filter(
        transformer_id__in=transformer_ids,
        month=month_date
    ).aggregate(
        total_customers=Sum('customers_billed')
    )['total_customers'] or 0
    
    # Average peak load calculation
    peak_loads = HourlyLoad.objects.filter(
        feeder_id__in=feeder_ids,
        date__range=(month_start, month_end)
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