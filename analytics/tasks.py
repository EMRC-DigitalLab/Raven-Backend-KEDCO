# analytics/tasks.py
from celery import shared_task # type: ignore
from django.core.management import call_command
from datetime import datetime, date
import logging

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3)
def update_monthly_overview_summary(self, month_date_str, priority='normal'):
    """
    Celery task to update a single month's overview summary.
    
    Args:
        month_date_str: Month date as string (YYYY-MM-DD)
        priority: Priority level ('high', 'normal', 'low', 'bulk', 'manual')
    """
    try:
        # Convert string back to date if needed
        if isinstance(month_date_str, str):
            month_date = datetime.strptime(month_date_str, '%Y-%m-%d').date()
        else:
            month_date = month_date_str
        
        # Format for management command
        month_str = month_date.strftime('%Y-%m')
        
        logger.info(f"Updating overview summary for {month_str} (priority: {priority})")
        
        # Call the management command
        call_command(
            'populate_overview_summary',
            month=month_str,
            force=True,
            verbosity=1
        )
        
        logger.info(f"Successfully updated overview summary for {month_str}")
        return f"Updated summary for {month_str}"
        
    except Exception as exc:
        logger.error(f"Failed to update summary for {month_date_str}: {str(exc)}")
        
        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
        
        # Final failure
        raise exc


@shared_task
def update_current_month_summary():
    """
    Task to update the current month's summary.
    This is typically run daily or weekly.
    """
    current_month = date.today().replace(day=1)
    return update_monthly_overview_summary.delay(
        current_month.strftime('%Y-%m-%d'), 
        priority='scheduled'
    )


@shared_task
def bulk_update_summaries(start_year=None, end_year=None):
    """
    Task for bulk updating multiple months.
    Used for initial population or major recalculations.
    """
    current_year = datetime.now().year
    start_year = start_year or current_year
    end_year = end_year or current_year
    
    logger.info(f"Starting bulk update for years {start_year}-{end_year}")
    
    try:
        call_command(
            'populate_overview_summary',
            start_year=start_year,
            end_year=end_year,
            force=True,
            verbosity=1
        )
        
        return f"Bulk update completed for {start_year}-{end_year}"
        
    except Exception as exc:
        logger.error(f"Bulk update failed: {str(exc)}")
        raise exc


@shared_task
def health_check_summaries():
    """
    Periodic health check of summary data.
    Identifies and fixes stale or missing summaries.
    """
    from .signals import check_summary_freshness, get_summary_health_status
    
    # Get health status
    health_status = get_summary_health_status()
    logger.info(f"Summary health check: {health_status}")
    
    # Check for stale summaries
    stale_months = check_summary_freshness(24)  # 24 hours
    
    updated_count = 0
    for month_date in stale_months:
        update_monthly_overview_summary.delay(
            month_date.strftime('%Y-%m-%d'),
            priority='health_check'
        )
        updated_count += 1
    
    return {
        'health_status': health_status,
        'stale_summaries_found': len(stale_months),
        'updates_queued': updated_count
    }


# For non-Celery environments, provide synchronous versions
def update_monthly_overview_summary_sync(month_date, priority='normal'):
    """
    Synchronous version of the summary update for environments without Celery.
    This is what gets called by the signals when Celery is not available.
    """
    try:
        if isinstance(month_date, str):
            month_date = datetime.strptime(month_date, '%Y-%m-%d').date()
        
        month_str = month_date.strftime('%Y-%m')
        
        logger.info(f"Synchronously updating overview summary for {month_str}")
        
        call_command(
            'populate_overview_summary',
            month=month_str,
            force=True,
            verbosity=0  # Quiet for signal-triggered updates
        )
        
        return f"Updated summary for {month_str}"
        
    except Exception as exc:
        logger.error(f"Failed to update summary for {month_date}: {str(exc)}")
        raise exc


# Auto-detect Celery availability and use appropriate function
try:
    from celery import current_app # type: ignore
    # If Celery is available, use async tasks
    update_monthly_overview_summary = update_monthly_overview_summary
except ImportError:
    # If Celery is not available, use sync version
    logger.warning("Celery not available, using synchronous summary updates")
    update_monthly_overview_summary = update_monthly_overview_summary_sync


# analytics/tasks.py (add to existing file)

@shared_task(bind=True, max_retries=3)
def update_monthly_technical_summary(self, month_date_str, filter_params, priority='normal'):
    """
    Celery task to update a monthly technical summary for specific filters.
    
    Args:
        month_date_str: Month date as string (YYYY-MM-DD)
        filter_params: Dict with 'state', 'business_district', 'feeder' filters
        priority: Priority level ('high', 'normal', 'low', 'bulk', 'manual')
    """
    try:
        # Convert string to date
        if isinstance(month_date_str, str):
            month_date = datetime.strptime(month_date_str, '%Y-%m-%d').date()
        else:
            month_date = month_date_str
        
        # Parse filter objects from IDs/names
        parsed_filters = parse_filter_params(filter_params)
        
        logger.info(
            f"Updating technical summary for {month_date.strftime('%Y-%m')} "
            f"(filters: {get_filter_description(parsed_filters)}, priority: {priority})"
        )
        
        # Calculate and save summary
        summary = calculate_and_save_technical_summary(month_date, parsed_filters)
        
        logger.info(
            f"Successfully updated technical summary for {month_date.strftime('%Y-%m')} "
            f"(filter level: {summary.filter_level})"
        )
        
        return f"Updated technical summary for {month_date.strftime('%Y-%m')} - {summary.filter_level}"
        
    except Exception as exc:
        logger.error(f"Failed to update technical summary for {month_date_str}: {str(exc)}")
        
        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
        
        raise exc


@shared_task
def bulk_update_technical_summaries(start_year=None, end_year=None, filter_params=None):
    """
    Task for bulk updating technical summaries across multiple months.
    
    Args:
        start_year: Starting year (defaults to current year)
        end_year: Ending year (defaults to current year) 
        filter_params: Optional filter parameters to apply
    """
    current_year = datetime.now().year
    start_year = start_year or current_year
    end_year = end_year or current_year
    
    logger.info(
        f"Starting bulk technical summary update for years {start_year}-{end_year} "
        f"with filters: {get_filter_description(filter_params)}"
    )
    
    try:
        updated_count = 0
        
        for year in range(start_year, end_year + 1):
            for month in range(1, 13):
                month_date = date(year, month, 1)
                
                # Don't process future months
                if month_date > date.today().replace(day=1):
                    continue
                
                # Update summary for this month
                update_monthly_technical_summary.delay(
                    month_date.strftime('%Y-%m-%d'),
                    filter_params or {},
                    priority='bulk'
                )
                updated_count += 1
        
        return f"Queued {updated_count} technical summary updates for {start_year}-{end_year}"
        
    except Exception as exc:
        logger.error(f"Bulk technical summary update failed: {str(exc)}")
        raise exc


@shared_task
def cascade_update_technical_summaries(month_date_str, feeder_id=None):
    """
    Update all filtering levels for a given month (national -> state -> district -> feeder).
    Used when you want to ensure all summary levels are updated consistently.
    
    Args:
        month_date_str: Month to update
        feeder_id: Optional feeder ID to start cascade from (updates parent levels too)
    """
    try:
        month_date = datetime.strptime(month_date_str, '%Y-%m-%d').date()
        
        updated_summaries = []
        
        # Start with national level
        update_monthly_technical_summary.delay(
            month_date_str,
            {'state': None, 'business_district': None, 'feeder': None},
            priority='cascade'
        )
        updated_summaries.append('national')
        
        if feeder_id:
            from common.models import Feeder
            try:
                feeder = Feeder.objects.get(id=feeder_id)
                
                if feeder.business_district:
                    district = feeder.business_district
                    state = district.state
                    
                    # Update state level
                    update_monthly_technical_summary.delay(
                        month_date_str,
                        {'state': state.id, 'business_district': None, 'feeder': None},
                        priority='cascade'
                    )
                    updated_summaries.append(f'state:{state.name}')
                    
                    # Update district level
                    update_monthly_technical_summary.delay(
                        month_date_str,
                        {'state': state.id, 'business_district': district.id, 'feeder': None},
                        priority='cascade'
                    )
                    updated_summaries.append(f'district:{district.name}')
                    
                    # Update feeder level
                    update_monthly_technical_summary.delay(
                        month_date_str,
                        {'state': state.id, 'business_district': district.id, 'feeder': feeder.id},
                        priority='cascade'
                    )
                    updated_summaries.append(f'feeder:{feeder.slug}')
                    
            except Feeder.DoesNotExist:
                logger.warning(f"Feeder with ID {feeder_id} not found")
        
        return f"Cascade update queued for {month_date.strftime('%Y-%m')}: {', '.join(updated_summaries)}"
        
    except Exception as exc:
        logger.error(f"Cascade update failed: {str(exc)}")
        raise exc


@shared_task
def health_check_technical_summaries(filter_params=None):
    """
    Periodic health check of technical summary data.
    Identifies and fixes stale or missing summaries.
    
    Args:
        filter_params: Optional filter to check specific summary levels
    """
    from .signals import check_technical_summary_freshness, get_technical_summary_health_status
    
    # Get health status
    health_status = get_technical_summary_health_status(filter_params)
    logger.info(f"Technical summary health check: {health_status}")
    
    # Check for stale summaries
    stale_summaries = check_technical_summary_freshness(24, filter_params)
    
    updated_count = 0
    for month_date, stale_filter_params in stale_summaries:
        update_monthly_technical_summary.delay(
            month_date.strftime('%Y-%m-%d'),
            stale_filter_params,
            priority='health_check'
        )
        updated_count += 1
    
    return {
        'health_status': health_status,
        'stale_summaries_found': len(stale_summaries),
        'updates_queued': updated_count,
        'filter_applied': filter_params
    }


# === HELPER FUNCTIONS ===

def parse_filter_params(filter_params):
    """
    Parse filter parameters and convert IDs to objects.
    
    Args:
        filter_params: Dict with potential ID values or object instances
        
    Returns:
        Dict with actual model instances
    """
    from common.models import State, BusinessDistrict, Feeder
    
    parsed = {
        'state': None,
        'business_district': None, 
        'feeder': None
    }
    
    if not filter_params:
        return parsed
    
    # Parse state
    state_value = filter_params.get('state')
    if state_value:
        if isinstance(state_value, str) and state_value.isdigit():
            try:
                parsed['state'] = State.objects.get(id=state_value)
            except State.DoesNotExist:
                pass
        elif hasattr(state_value, 'id'):  # Model instance
            parsed['state'] = state_value
        elif isinstance(state_value, str):  # Name
            try:
                parsed['state'] = State.objects.get(name__iexact=state_value)
            except State.DoesNotExist:
                pass
    
    # Parse business district
    district_value = filter_params.get('business_district')
    if district_value:
        if isinstance(district_value, str) and district_value.isdigit():
            try:
                parsed['business_district'] = BusinessDistrict.objects.get(id=district_value)
            except BusinessDistrict.DoesNotExist:
                pass
        elif hasattr(district_value, 'id'):  # Model instance
            parsed['business_district'] = district_value
        elif isinstance(district_value, str):  # Name
            qs = BusinessDistrict.objects.filter(name__iexact=district_value)
            if parsed['state']:
                qs = qs.filter(state=parsed['state'])
            parsed['business_district'] = qs.first()
    
    # Parse feeder
    feeder_value = filter_params.get('feeder')
    if feeder_value:
        if isinstance(feeder_value, str) and feeder_value.isdigit():
            try:
                parsed['feeder'] = Feeder.objects.get(id=feeder_value)
            except Feeder.DoesNotExist:
                pass
        elif hasattr(feeder_value, 'id'):  # Model instance
            parsed['feeder'] = feeder_value
        elif isinstance(feeder_value, str):  # Slug
            qs = Feeder.objects.filter(slug=feeder_value)
            if parsed['business_district']:
                qs = qs.filter(business_district=parsed['business_district'])
            elif parsed['state']:
                qs = qs.filter(business_district__state=parsed['state'])
            parsed['feeder'] = qs.first()
    
    return parsed


def get_filter_description(filter_params):
    """Generate human-readable description of filter parameters"""
    if not filter_params:
        return "national"
    
    parsed = parse_filter_params(filter_params) if not all(
        hasattr(v, 'id') if v else True for v in filter_params.values()
    ) else filter_params
    
    if parsed.get('feeder'):
        return f"feeder:{parsed['feeder'].slug}"
    elif parsed.get('business_district'):
        return f"district:{parsed['business_district'].name}"
    elif parsed.get('state'):
        return f"state:{parsed['state'].name}"
    else:
        return "national"


def calculate_and_save_technical_summary(month_date, filter_params):
    """
    Calculate technical metrics and save to summary model.
    
    Args:
        month_date: Month to calculate for
        filter_params: Parsed filter parameters
        
    Returns:
        MonthlyTechnicalSummary instance
    """
    from .models import MonthlyTechnicalSummary
    from .utils.technical_calculations import TechnicalCalculator
    from django.utils import timezone

    
    start_time = timezone.now()
    
    # Initialize calculator with filters
    calculator = TechnicalCalculator(
        month_date=month_date,
        state=filter_params.get('state'),
        business_district=filter_params.get('business_district'),
        feeder=filter_params.get('feeder')
    )
    
    # Calculate all metrics
    metrics = calculator.calculate_all_metrics()
    
    # Get or create summary record
    summary, created = MonthlyTechnicalSummary.objects.get_or_create(
        month=month_date,
        state=filter_params.get('state'),
        business_district=filter_params.get('business_district'),
        feeder=filter_params.get('feeder'),
        defaults=metrics
    )
    
    if not created:
        # Update existing record
        for key, value in metrics.items():
            setattr(summary, key, value)
        summary.save()
    
    logger.info(
        f"{'Created' if created else 'Updated'} technical summary for "
        f"{month_date.strftime('%Y-%m')} - {summary.filter_level} "
        f"(calculation took {(timezone.now() - start_time).total_seconds():.2f}s)"
    )
    
    return summary


# === SYNCHRONOUS VERSIONS FOR NON-CELERY ENVIRONMENTS ===

def update_monthly_technical_summary_sync(month_date_str, filter_params, priority='normal'):
    """
    Synchronous version of technical summary update.
    """
    try:
        if isinstance(month_date_str, str):
            month_date = datetime.strptime(month_date_str, '%Y-%m-%d').date()
        else:
            month_date = month_date_str
        
        parsed_filters = parse_filter_params(filter_params)
        
        logger.info(
            f"Synchronously updating technical summary for {month_date.strftime('%Y-%m')} "
            f"(filters: {get_filter_description(parsed_filters)})"
        )
        
        summary = calculate_and_save_technical_summary(month_date, parsed_filters)
        
        return f"Updated technical summary for {month_date.strftime('%Y-%m')} - {summary.filter_level}"
        
    except Exception as exc:
        logger.error(f"Failed to update technical summary for {month_date_str}: {str(exc)}")
        raise exc


# Auto-detect Celery and use appropriate function
try:
    from celery import current_app # type: ignore
    # Celery is available, use async tasks
    pass
except ImportError:
    # Celery not available, use sync versions
    logger.warning("Celery not available, using synchronous technical summary updates")
    update_monthly_technical_summary = update_monthly_technical_summary_sync