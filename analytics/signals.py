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
from technical.models import (
    FeederEnergyDaily, FeederEnergyMonthly, HourlyLoad, 
    FeederInterruption, DailyHoursOfSupply
)
from datetime import date


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





# === TECHNICAL DATA SIGNALS ===

@receiver([post_save, post_delete], sender=FeederEnergyDaily)
def update_technical_summary_on_energy_daily_change(sender, instance, **kwargs):
    """Update technical summary when daily energy data changes"""
    if hasattr(instance, 'date'):
        month_date = instance.date.replace(day=1)
        
        if SummaryUpdateManager.should_update_summary(month_date):
            logger.info(f"Updating technical summary due to daily energy change: {month_date}")
            SummaryUpdateManager.queue_technical_summary_update(
                month_date, 
                feeder=instance.feeder,
                priority='normal'
            )


@receiver([post_save, post_delete], sender=FeederEnergyMonthly)
def update_technical_summary_on_energy_monthly_change(sender, instance, **kwargs):
    """Update technical summary when monthly energy data changes"""
    if hasattr(instance, 'period'):
        month_date = instance.period
        
        if SummaryUpdateManager.should_update_summary(month_date):
            logger.info(f"Updating technical summary due to monthly energy change: {month_date}")
            SummaryUpdateManager.queue_technical_summary_update(
                month_date,
                feeder=instance.feeder, 
                priority='high'
            )


@receiver([post_save, post_delete], sender=HourlyLoad)
def update_technical_summary_on_load_change(sender, instance, **kwargs):
    """Update technical summary when hourly load data changes"""
    if hasattr(instance, 'date'):
        month_date = instance.date.replace(day=1)
        
        # Only update current month for frequently changing load data
        current_month = datetime.now().date().replace(day=1)
        if month_date == current_month and SummaryUpdateManager.should_update_summary(month_date):
            logger.info(f"Updating technical summary due to load change: {month_date}")
            SummaryUpdateManager.queue_technical_summary_update(
                month_date,
                feeder=instance.feeder,
                priority='low'
            )


@receiver([post_save, post_delete], sender=FeederInterruption)
def update_technical_summary_on_interruption_change(sender, instance, **kwargs):
    """Update technical summary when interruption data changes"""
    if hasattr(instance, 'occurred_at'):
        month_date = instance.occurred_at.date().replace(day=1)
        
        if SummaryUpdateManager.should_update_summary(month_date):
            logger.info(f"Updating technical summary due to interruption change: {month_date}")
            SummaryUpdateManager.queue_technical_summary_update(
                month_date,
                feeder=instance.feeder,
                priority='normal'
            )


@receiver([post_save, post_delete], sender=DailyHoursOfSupply)
def update_technical_summary_on_supply_hours_change(sender, instance, **kwargs):
    """Update technical summary when supply hours data changes"""
    if hasattr(instance, 'date'):
        month_date = instance.date.replace(day=1)
        
        if SummaryUpdateManager.should_update_summary(month_date):
            logger.info(f"Updating technical summary due to supply hours change: {month_date}")
            SummaryUpdateManager.queue_technical_summary_update(
                month_date,
                feeder=instance.feeder,
                priority='normal'
            )


# === ENHANCED SUMMARY UPDATE MANAGER ===

class SummaryUpdateManager:
    """Enhanced manager with technical summary support"""
    
    @staticmethod
    def queue_technical_summary_update(month_date, feeder=None, priority='normal'):
        """
        Queue a technical summary update for specific filtering levels.
        Updates all relevant summary levels (national, state, district, feeder).
        """
        from .tasks import update_monthly_technical_summary
        
        try:
            # Always update national level
            update_monthly_technical_summary.delay(
                month_date.strftime('%Y-%m-%d'),
                filter_params={'state': None, 'business_district': None, 'feeder': None},
                priority=priority
            )
            
            if feeder:
                # Update feeder-specific summary
                update_monthly_technical_summary.delay(
                    month_date.strftime('%Y-%m-%d'),
                    filter_params={
                        'state': feeder.business_district.state if feeder.business_district else None,
                        'business_district': feeder.business_district,
                        'feeder': feeder
                    },
                    priority=priority
                )
                
                # Update district-level summary if feeder has district
                if feeder.business_district:
                    update_monthly_technical_summary.delay(
                        month_date.strftime('%Y-%m-%d'),
                        filter_params={
                            'state': feeder.business_district.state,
                            'business_district': feeder.business_district,
                            'feeder': None
                        },
                        priority=priority
                    )
                    
                    # Update state-level summary
                    update_monthly_technical_summary.delay(
                        month_date.strftime('%Y-%m-%d'),
                        filter_params={
                            'state': feeder.business_district.state,
                            'business_district': None,
                            'feeder': None
                        },
                        priority=priority
                    )
            
        except Exception as e:
            logger.error(f"Failed to queue technical summary update for {month_date}: {str(e)}")


# === BULK UPDATE UTILITIES FOR TECHNICAL DATA ===

def trigger_technical_summary_update(month_date, filter_params=None, force=False):
    """
    Manually trigger a technical summary update for specific filters.
    
    Args:
        month_date: Date object for the month to update
        filter_params: Dict with 'state', 'business_district', 'feeder' keys
        force: Whether to bypass caching and force update
    """
    filter_params = filter_params or {'state': None, 'business_district': None, 'feeder': None}
    
    if force:
        cache_key = f"technical_summary_update_pending_{month_date.strftime('%Y_%m')}_{hash(str(filter_params))}"
        cache.delete(cache_key)
    
    cache_key = f"technical_summary_update_pending_{month_date.strftime('%Y_%m')}_{hash(str(filter_params))}"
    if force or not cache.get(cache_key):
        cache.set(cache_key, True, 300)  # 5 minute cooldown
        
        from .tasks import update_monthly_technical_summary
        update_monthly_technical_summary.delay(
            month_date.strftime('%Y-%m-%d'),
            filter_params,
            priority='manual'
        )
        return True
    return False


def trigger_technical_summary_cascade_update(month_date, feeder=None, force=False):
    """
    Trigger technical summary updates for all relevant filtering levels.
    
    Args:
        month_date: Month to update
        feeder: Starting feeder (will update feeder -> district -> state -> national)
        force: Whether to force update even if cached
    """
    updated_levels = []
    
    # Update national level
    if trigger_technical_summary_update(month_date, None, force):
        updated_levels.append('national')
    
    if feeder and feeder.business_district:
        district = feeder.business_district
        state = district.state
        
        # Update state level
        if trigger_technical_summary_update(
            month_date, 
            {'state': state, 'business_district': None, 'feeder': None}, 
            force
        ):
            updated_levels.append(f'state:{state.name}')
        
        # Update district level  
        if trigger_technical_summary_update(
            month_date,
            {'state': state, 'business_district': district, 'feeder': None},
            force
        ):
            updated_levels.append(f'district:{district.name}')
        
        # Update feeder level
        if trigger_technical_summary_update(
            month_date,
            {'state': state, 'business_district': district, 'feeder': feeder},
            force
        ):
            updated_levels.append(f'feeder:{feeder.slug}')
    
    return updated_levels


def check_technical_summary_freshness(max_age_hours=24, filter_params=None):
    """
    Check which technical summaries are stale and need updating.
    
    Args:
        max_age_hours: Maximum age in hours before considering stale
        filter_params: Specific filter to check (None = check all levels)
        
    Returns:
        List of (month, filter_params) tuples that need updating
    """
    from .models import MonthlyTechnicalSummary
    from datetime import datetime, timedelta
    
    cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
    current_month = datetime.now().date().replace(day=1)
    
    query = MonthlyTechnicalSummary.objects.filter(
        month=current_month,
        calculated_at__lt=cutoff_time
    )
    
    if filter_params:
        for key, value in filter_params.items():
            if value is not None:
                query = query.filter(**{key: value})
    
    stale_summaries = []
    for summary in query:
        filter_dict = {
            'state': summary.state,
            'business_district': summary.business_district,
            'feeder': summary.feeder
        }
        stale_summaries.append((summary.month, filter_dict))
    
    return stale_summaries


def get_technical_summary_health_status(filter_params=None):
    """
    Get health status of technical summary data.
    
    Args:
        filter_params: Specific filter to check health for
        
    Returns:
        Dictionary with health metrics
    """
    from .models import MonthlyTechnicalSummary
    
    query = MonthlyTechnicalSummary.objects.all()
    if filter_params:
        for key, value in filter_params.items():
            if value is not None:
                query = query.filter(**{key: value})
    
    total_summaries = query.count()
    incomplete_summaries = query.filter(has_complete_data=False).count()
    
    current_month = date.today().replace(day=1)
    current_month_summaries = query.filter(month=current_month)
    
    stale_count = len(check_technical_summary_freshness(24, filter_params))
    
    # Calculate coverage by filter level
    coverage_stats = {
        'national': query.filter(
            state__isnull=True, 
            business_district__isnull=True, 
            feeder__isnull=True
        ).count(),
        'state': query.filter(
            state__isnull=False, 
            business_district__isnull=True, 
            feeder__isnull=True
        ).count(),
        'district': query.filter(
            business_district__isnull=False, 
            feeder__isnull=True
        ).count(),
        'feeder': query.filter(feeder__isnull=False).count(),
    }
    
    return {
        'total_summaries': total_summaries,
        'incomplete_summaries': incomplete_summaries,
        'current_month_summaries': current_month_summaries.count(),
        'stale_summaries_count': stale_count,
        'coverage_by_level': coverage_stats,
        'health_score': max(0, 100 - (incomplete_summaries * 10) - (stale_count * 15)),
        'filter_applied': filter_params
    }