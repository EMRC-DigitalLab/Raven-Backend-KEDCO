# analytics/signals.py
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache
from datetime import datetime, timedelta
import logging

# Import models that affect overview summary
from commercial.models import MonthlyCommercialSummary, MonthlyEnergyBilled
from technical.models import EnergyDelivered, FeederInterruption, HourlyLoad
from financial.models import Opex, SalaryPayment, NBETInvoice, MOInvoice

logger = logging.getLogger(__name__)

class SummaryUpdateManager:
    """
    Manages when and how to update overview summaries.
    Prevents excessive updates and manages batching.
    """
    
    @staticmethod
    def should_update_summary(month_date):
        """
        Determine if we should update the summary for a given month.
        Uses caching to prevent excessive updates.
        """
        cache_key = f"summary_update_pending_{month_date.strftime('%Y_%m')}"
        
        # Check if update is already pending
        if cache.get(cache_key):
            return False
        
        # Set cache to prevent duplicate updates for 5 minutes
        cache.set(cache_key, True, 300)
        return True
    
    @staticmethod
    def queue_summary_update(month_date, priority='normal'):
        """
        Queue a summary update. In production, this would use Celery.
        For now, we'll use a direct call with error handling.
        """
        from .tasks import update_monthly_overview_summary
        
        try:
            # In production, use: update_monthly_overview_summary.delay(month_date, priority)
            update_monthly_overview_summary(month_date, priority)
        except Exception as e:
            logger.error(f"Failed to queue summary update for {month_date}: {str(e)}")
    
    @staticmethod
    def get_month_from_instance(instance, date_field='month'):
        """Extract month date from an instance"""
        try:
            if hasattr(instance, date_field):
                date_value = getattr(instance, date_field)
                if hasattr(date_value, 'replace'):
                    return date_value.replace(day=1)
            return None
        except Exception:
            return None


# === COMMERCIAL DATA SIGNALS ===

@receiver([post_save, post_delete], sender=MonthlyCommercialSummary)
def update_summary_on_commercial_change(sender, instance, **kwargs):
    """Update overview summary when commercial data changes"""
    month_date = SummaryUpdateManager.get_month_from_instance(instance, 'month')
    
    if month_date and SummaryUpdateManager.should_update_summary(month_date):
        logger.info(f"Updating overview summary due to commercial data change: {month_date}")
        SummaryUpdateManager.queue_summary_update(month_date, priority='high')


@receiver([post_save, post_delete], sender=MonthlyEnergyBilled)
def update_summary_on_energy_billed_change(sender, instance, **kwargs):
    """Update overview summary when energy billing data changes"""
    month_date = SummaryUpdateManager.get_month_from_instance(instance, 'month')
    
    if month_date and SummaryUpdateManager.should_update_summary(month_date):
        logger.info(f"Updating overview summary due to energy billing change: {month_date}")
        SummaryUpdateManager.queue_summary_update(month_date, priority='high')


# === TECHNICAL DATA SIGNALS ===

@receiver([post_save, post_delete], sender=EnergyDelivered)
def update_summary_on_energy_delivered_change(sender, instance, **kwargs):
    """Update overview summary when energy delivery data changes"""
    if hasattr(instance, 'date'):
        month_date = instance.date.replace(day=1)
        
        if SummaryUpdateManager.should_update_summary(month_date):
            logger.info(f"Updating overview summary due to energy delivery change: {month_date}")
            SummaryUpdateManager.queue_summary_update(month_date, priority='normal')


@receiver([post_save, post_delete], sender=FeederInterruption)
def update_summary_on_interruption_change(sender, instance, **kwargs):
    """Update overview summary when interruption data changes"""
    if hasattr(instance, 'occurred_at'):
        month_date = instance.occurred_at.date().replace(day=1)
        
        if SummaryUpdateManager.should_update_summary(month_date):
            logger.info(f"Updating overview summary due to interruption change: {month_date}")
            SummaryUpdateManager.queue_summary_update(month_date, priority='normal')


@receiver([post_save, post_delete], sender=HourlyLoad)
def update_summary_on_load_change(sender, instance, **kwargs):
    """Update overview summary when hourly load data changes"""
    if hasattr(instance, 'date'):
        month_date = instance.date.replace(day=1)
        
        # Only update if this affects current month (load data changes frequently)
        current_month = datetime.now().date().replace(day=1)
        if month_date == current_month and SummaryUpdateManager.should_update_summary(month_date):
            logger.info(f"Updating overview summary due to load data change: {month_date}")
            SummaryUpdateManager.queue_summary_update(month_date, priority='low')


# === FINANCIAL DATA SIGNALS ===

@receiver([post_save, post_delete], sender=Opex)
def update_summary_on_opex_change(sender, instance, **kwargs):
    """Update overview summary when OPEX data changes"""
    if hasattr(instance, 'date'):
        month_date = instance.date.replace(day=1)
        
        if SummaryUpdateManager.should_update_summary(month_date):
            logger.info(f"Updating overview summary due to OPEX change: {month_date}")
            SummaryUpdateManager.queue_summary_update(month_date, priority='normal')


@receiver([post_save, post_delete], sender=SalaryPayment)
def update_summary_on_salary_change(sender, instance, **kwargs):
    """Update overview summary when salary data changes"""
    month_date = SummaryUpdateManager.get_month_from_instance(instance, 'month')
    
    if month_date and SummaryUpdateManager.should_update_summary(month_date):
        logger.info(f"Updating overview summary due to salary change: {month_date}")
        SummaryUpdateManager.queue_summary_update(month_date, priority='normal')


@receiver([post_save, post_delete], sender=NBETInvoice)
def update_summary_on_nbet_change(sender, instance, **kwargs):
    """Update overview summary when NBET invoice changes"""
    month_date = SummaryUpdateManager.get_month_from_instance(instance, 'month')
    
    if month_date and SummaryUpdateManager.should_update_summary(month_date):
        logger.info(f"Updating overview summary due to NBET invoice change: {month_date}")
        SummaryUpdateManager.queue_summary_update(month_date, priority='normal')


@receiver([post_save, post_delete], sender=MOInvoice)
def update_summary_on_mo_change(sender, instance, **kwargs):
    """Update overview summary when MO invoice changes"""
    month_date = SummaryUpdateManager.get_month_from_instance(instance, 'month')
    
    if month_date and SummaryUpdateManager.should_update_summary(month_date):
        logger.info(f"Updating overview summary due to MO invoice change: {month_date}")
        SummaryUpdateManager.queue_summary_update(month_date, priority='normal')


# === BULK UPDATE HANDLING ===

class BulkUpdateTracker:
    """
    Track bulk operations and update summaries efficiently.
    Prevents individual signal firing during bulk operations.
    """
    
    def __init__(self):
        self.pending_months = set()
        self.is_bulk_operation = False
    
    def start_bulk_operation(self):
        """Mark the start of a bulk operation"""
        self.is_bulk_operation = True
        self.pending_months.clear()
    
    def add_month(self, month_date):
        """Add a month to pending updates"""
        if month_date:
            self.pending_months.add(month_date)
    
    def finish_bulk_operation(self):
        """Process all pending updates after bulk operation"""
        if self.is_bulk_operation:
            for month_date in self.pending_months:
                SummaryUpdateManager.queue_summary_update(month_date, priority='bulk')
            
            self.pending_months.clear()
            self.is_bulk_operation = False


# Global bulk tracker instance
bulk_tracker = BulkUpdateTracker()


# === SIGNAL OPTIMIZATION DECORATORS ===

def batch_summary_updates(func):
    """
    Decorator to batch summary updates during bulk operations.
    Use this when performing bulk data imports.
    """
    def wrapper(*args, **kwargs):
        bulk_tracker.start_bulk_operation()
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            bulk_tracker.finish_bulk_operation()
    return wrapper


# === UTILITY FUNCTIONS FOR MANUAL UPDATES ===

def trigger_summary_update(month_date, force=False):
    """
    Manually trigger a summary update for a specific month.
    
    Args:
        month_date: Date object for the month to update
        force: Whether to bypass caching and force update
    """
    if force:
        cache_key = f"summary_update_pending_{month_date.strftime('%Y_%m')}"
        cache.delete(cache_key)
    
    if force or SummaryUpdateManager.should_update_summary(month_date):
        SummaryUpdateManager.queue_summary_update(month_date, priority='manual')
        return True
    return False


def trigger_summary_updates_for_range(start_date, end_date, force=False):
    """
    Trigger summary updates for a range of months.
    
    Args:
        start_date: Start date (will be converted to first of month)
        end_date: End date (will be converted to first of month)
        force: Whether to bypass caching and force updates
    """
    from dateutil.relativedelta import relativedelta # type: ignore
    
    current = start_date.replace(day=1)
    end = end_date.replace(day=1)
    updated_months = []
    
    while current <= end:
        if trigger_summary_update(current, force):
            updated_months.append(current)
        current += relativedelta(months=1)
    
    return updated_months


# === HEALTH CHECK FUNCTIONS ===

def check_summary_freshness(max_age_hours=24):
    """
    Check which summaries are stale and need updating.
    
    Args:
        max_age_hours: Maximum age in hours before considering stale
        
    Returns:
        List of month dates that need updating
    """
    from .models import MonthlyOverviewSummary
    from datetime import datetime, timedelta
    
    cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
    current_month = datetime.now().date().replace(day=1)
    
    stale_summaries = MonthlyOverviewSummary.objects.filter(
        month=current_month,  # Only check current month for freshness
        calculated_at__lt=cutoff_time
    )
    
    return [summary.month for summary in stale_summaries]


def get_summary_health_status():
    """
    Get overall health status of summary data.
    
    Returns:
        Dictionary with health metrics
    """
    from .models import MonthlyOverviewSummary
    from datetime import datetime, date
    
    total_summaries = MonthlyOverviewSummary.objects.count()
    incomplete_summaries = MonthlyOverviewSummary.objects.filter(
        has_complete_data=False
    ).count()
    
    current_month = date.today().replace(day=1)
    try:
        current_summary = MonthlyOverviewSummary.objects.get(month=current_month)
        current_month_status = "exists"
        last_update = current_summary.calculated_at
    except MonthlyOverviewSummary.DoesNotExist:
        current_month_status = "missing"
        last_update = None
    
    stale_count = len(check_summary_freshness(24))
    
    return {
        'total_summaries': total_summaries,
        'incomplete_summaries': incomplete_summaries,
        'current_month_status': current_month_status,
        'current_month_last_update': last_update,
        'stale_summaries_count': stale_count,
        'health_score': max(0, 100 - (incomplete_summaries * 10) - (stale_count * 20)),
    }