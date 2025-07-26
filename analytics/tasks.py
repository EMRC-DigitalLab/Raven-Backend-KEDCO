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