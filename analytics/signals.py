# analytics/signals.py
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.core.cache import cache
from django.conf import settings
from django.db import transaction
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
import logging
from django.utils import timezone
from datetime import datetime
from technical.models import calculate_interruption_metrics

logger = logging.getLogger(__name__)

# Import source models
from commercial.models import MonthlyCommercialSummary, DailyCollection, MonthlyEnergyBilled
from financial.models import Opex, HQOpex, SalaryPayment, NBETInvoice, MOInvoice
from technical.models import EnergyDelivered, FeederEnergyDaily, FeederEnergyMonthly, HourlyLoad, FeederInterruption
from hr.models import Staff

# Import analytics models
from analytics.models import MonthlyOverviewSummary, MonthlyTechnicalSummary, DailyTechnicalSummary


def signals_enabled():
    """Check if analytics signals are currently enabled"""
    return not cache.get('analytics_signals_disabled', False)


def skip_if_signals_disabled(func):
    """Decorator to skip signal execution if signals are disabled"""
    def wrapper(*args, **kwargs):
        if not signals_enabled():
            logger.debug(f"Skipping {func.__name__} - signals disabled")
            return
        return func(*args, **kwargs)
    return wrapper


def is_celery_available():
    """Check if Celery is available and configured"""
    try:
        from celery import current_app
        # Simple check to see if we can import Celery
        return getattr(settings, 'ANALYTICS_USE_CELERY', False)
    except ImportError:
        return False


class SummaryUpdateTracker:
    """
    Tracks which summaries need to be updated to avoid duplicate tasks
    """
    _pending_updates = {
        'monthly_overview': set(),
        'monthly_technical': set(),
        'daily_technical': set(),
    }
    
    @classmethod
    def add_monthly_overview_update(cls, month_date):
        """Add a monthly overview update to the pending list"""
        cls._pending_updates['monthly_overview'].add(month_date)
    
    @classmethod
    def add_monthly_technical_update(cls, month_date, state_id=None, district_id=None, feeder_id=None):
        """Add a monthly technical update to the pending list"""
        key = (month_date, state_id, district_id, feeder_id)
        cls._pending_updates['monthly_technical'].add(key)
    
    @classmethod
    def add_daily_technical_update(cls, date_obj, state_id=None, district_id=None, feeder_id=None):
        """Add a daily technical update to the pending list"""
        key = (date_obj, state_id, district_id, feeder_id)
        cls._pending_updates['daily_technical'].add(key)
    
    @classmethod
    def get_and_clear_pending_updates(cls, summary_type):
        """Get pending updates and clear the list"""
        updates = cls._pending_updates[summary_type].copy()
        cls._pending_updates[summary_type].clear()
        return updates


# ==================== TASK FUNCTIONS ====================

def update_monthly_overview_summary_sync(month_date_str):
    """
    Synchronous version of monthly overview summary update
    UPDATED: Now includes unresolved interruptions in calculations
    """
    try:
        month_date = datetime.strptime(month_date_str, '%Y-%m-%d').date()
        
        from django.db.models import Sum, Count, Avg
        from commercial.models import MonthlyCommercialSummary
        from technical.models import EnergyDelivered, HourlyLoad, FeederInterruption
        from financial.models import Opex, HQOpex, SalaryPayment, NBETInvoice, MOInvoice
        from decimal import Decimal
        
        # Commercial data
        comm_data = MonthlyCommercialSummary.objects.filter(month=month_date).aggregate(
            revenue_billed=Sum("revenue_billed"),
            revenue_collected=Sum("revenue_collected"),
            customers_billed=Sum("customers_billed"),
            customers_responded=Sum("customers_responded"),
        )
        
        # Energy data from EnergyDelivered
        energy_delivered = EnergyDelivered.objects.filter(
            date__year=month_date.year,
            date__month=month_date.month
        ).aggregate(total=Sum("energy_mwh"))["total"] or Decimal("0")
        
        # Energy billed from MonthlyEnergyBilled
        try:
            from commercial.models import MonthlyEnergyBilled
            energy_billed = MonthlyEnergyBilled.objects.filter(
                month=month_date
            ).aggregate(total=Sum("energy_mwh"))["total"] or Decimal("0")
        except ImportError:
            energy_billed = Decimal("0")
        
        # Technical metrics - Average hours of supply
        hourly_loads = HourlyLoad.objects.filter(
            date__year=month_date.year,
            date__month=month_date.month,
            load_mw__gt=0
        ).values('feeder', 'date').annotate(
            daily_hours=Count('hour')
        )
        
        if hourly_loads.exists():
            avg_hours_supply = hourly_loads.aggregate(avg=Avg('daily_hours'))["avg"] or Decimal("0")
        else:
            avg_hours_supply = Decimal("0")
        
        # UPDATED: Interruption metrics - NOW INCLUDING UNRESOLVED
        interruptions = FeederInterruption.objects.filter(
            occurred_at__year=month_date.year,
            occurred_at__month=month_date.month
        )
        
        total_interruption_count = interruptions.count()
        
        if interruptions.exists():
            # Calculate duration including unresolved interruptions
            total_duration_hours = 0
            interruptions_with_duration = 0
            current_time = timezone.now()
            
            for interrupt in interruptions:
                if interrupt.restored_at:
                    # Resolved interruption - use actual duration
                    duration_hours = (interrupt.restored_at - interrupt.occurred_at).total_seconds() / 3600
                else:
                    # Unresolved interruption - use time since occurrence to now
                    duration_hours = (current_time - interrupt.occurred_at).total_seconds() / 3600
                
                total_duration_hours += duration_hours
                interruptions_with_duration += 1
            
            avg_interruption_duration = Decimal(str(round(total_duration_hours / interruptions_with_duration, 2))) if interruptions_with_duration > 0 else Decimal("0")
            avg_turnaround_time = avg_interruption_duration  # Same calculation for now
        else:
            avg_interruption_duration = Decimal("0")
            avg_turnaround_time = Decimal("0")
        
        # Financial data (unchanged)
        opex_costs = Opex.objects.filter(
            date__year=month_date.year,
            date__month=month_date.month
        ).aggregate(
            total=Sum("credit") + Sum("debit")
        )["total"] or Decimal("0")
        
        hq_opex_costs = HQOpex.objects.filter(
            date__year=month_date.year,
            date__month=month_date.month
        ).aggregate(
            total=Sum("credit") + Sum("debit")
        )["total"] or Decimal("0")
        
        salary_costs = SalaryPayment.objects.filter(
            month__year=month_date.year,
            month__month=month_date.month
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        nbet_costs = NBETInvoice.objects.filter(
            month__year=month_date.year,
            month__month=month_date.month
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        mo_costs = MOInvoice.objects.filter(
            month__year=month_date.year,
            month__month=month_date.month
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        
        # Extract values with defaults
        revenue_billed = comm_data['revenue_billed'] or Decimal("0")
        revenue_collected = comm_data['revenue_collected'] or Decimal("0")
        customers_billed = comm_data['customers_billed'] or 0
        customers_responded = comm_data['customers_responded'] or 0
        
        # Calculate efficiency metrics
        billing_eff = (energy_billed / energy_delivered * 100) if energy_delivered > 0 else Decimal("0")
        collection_eff = (revenue_collected / revenue_billed * 100) if revenue_billed > 0 else Decimal("0")
        atc_losses = Decimal("100") - (billing_eff * collection_eff / 100) if billing_eff and collection_eff else Decimal("100")
        energy_collected = energy_billed * (collection_eff / 100) if energy_billed > 0 else Decimal("0")
        customer_response_rate = (customers_responded / customers_billed * 100) if customers_billed > 0 else Decimal("0")
        
        total_cost = opex_costs + hq_opex_costs + salary_costs + nbet_costs + mo_costs
        
        # Create/update summary with calculated data
        from analytics.models import MonthlyOverviewSummary
        MonthlyOverviewSummary.objects.update_or_create(
            month=month_date,
            defaults={
                'revenue_billed': revenue_billed,
                'revenue_collected': revenue_collected,
                'customers_billed': customers_billed,
                'customers_responded': customers_responded,
                'energy_delivered': energy_delivered,
                'energy_billed': energy_billed,
                'energy_collected': energy_collected,
                'avg_hours_supply': avg_hours_supply,
                'avg_interruption_duration': avg_interruption_duration,
                'avg_turnaround_time': avg_turnaround_time,
                'total_cost': total_cost,
                'total_opex': opex_costs + hq_opex_costs,
                'total_salaries': salary_costs,
                'total_nbet': nbet_costs,
                'total_mo': mo_costs,
                'billing_efficiency': billing_eff,
                'collection_efficiency': collection_eff,
                'atc_losses': atc_losses,
                'customer_response_rate': customer_response_rate,
                'has_complete_data': True,
            }
        )
        
        logger.info(f"Updated monthly overview summary for {month_date} - Avg hours supply: {avg_hours_supply}, Total interruptions: {total_interruption_count}")
        return True
        
    except Exception as exc:
        logger.error(f"Failed to update monthly overview summary for {month_date_str}: {exc}")
        return False


def update_monthly_technical_summary_sync(month_date_str, state_id=None, district_id=None, feeder_id=None):
    """
    IMPROVED version using the new utility functions for interruption calculations
    """
    try:
        month_date = datetime.strptime(month_date_str, '%Y-%m-%d').date()
        
        from django.db.models import Sum, Count, Avg, Q
        from common.models import State, BusinessDistrict, Feeder
        from technical.models import EnergyDelivered, HourlyLoad, FeederInterruption
        from decimal import Decimal
        import calendar
        
        # Get filter objects
        state = State.objects.get(id=state_id) if state_id else None
        district = BusinessDistrict.objects.get(id=district_id) if district_id else None
        feeder = Feeder.objects.get(id=feeder_id) if feeder_id else None
        
        # Build filter for feeders based on hierarchy
        feeder_filter = Q()
        if feeder:
            feeder_filter = Q(feeder=feeder)
        elif district:
            feeder_filter = Q(feeder__business_district=district)
        elif state:
            feeder_filter = Q(feeder__business_district__state=state)
        
        # Energy delivered
        energy_delivered = EnergyDelivered.objects.filter(
            feeder_filter,
            date__year=month_date.year,
            date__month=month_date.month
        ).aggregate(total=Sum("energy_mwh"))["total"] or Decimal("0")
        
        # Average hours of supply calculation
        hourly_loads = HourlyLoad.objects.filter(
            feeder_filter,
            date__year=month_date.year,
            date__month=month_date.month,
            load_mw__gt=0
        ).values('feeder', 'date').annotate(
            daily_hours=Count('hour')
        )
        
        if hourly_loads.exists():
            avg_hours_supply = hourly_loads.aggregate(avg=Avg('daily_hours'))["avg"] or Decimal("0")
            total_supply_hours = sum(load['daily_hours'] for load in hourly_loads)
        else:
            avg_hours_supply = Decimal("0")
            total_supply_hours = 0
        
        # Peak load calculations
        load_data = HourlyLoad.objects.filter(
            feeder_filter,
            date__year=month_date.year,
            date__month=month_date.month
        ).aggregate(
            avg_peak=Avg('load_mw'),
            max_peak=Sum('load_mw')
        )
        
        avg_peak_load = load_data['avg_peak'] or Decimal("0")
        max_peak_load = load_data['max_peak'] or Decimal("0")
        
        # IMPROVED: Interruption calculations using utility function
        interruptions = FeederInterruption.objects.filter(
            feeder_filter,
            occurred_at__year=month_date.year,
            occurred_at__month=month_date.month
        )
        
        # Calculate end of month for reference time
        days_in_month = calendar.monthrange(month_date.year, month_date.month)[1]
        end_of_month = datetime(month_date.year, month_date.month, days_in_month, 23, 59, 59)
        end_of_month = timezone.make_aware(end_of_month) if timezone.is_naive(end_of_month) else end_of_month
        
        # Use utility function for comprehensive metrics
        metrics = calculate_interruption_metrics(
            interruptions, 
            reference_time=end_of_month
        )
        
        total_interruptions = metrics['total_interruptions']
        avg_daily_interruptions = Decimal(total_interruptions) / Decimal(days_in_month) if days_in_month > 0 else Decimal("0")
        avg_interruption_duration = Decimal(str(metrics['avg_duration_hours']))
        total_interruption_hours = Decimal(str(metrics['total_duration_hours']))
        avg_turnaround_time = avg_interruption_duration
        avg_fault_turnaround_time = Decimal(str(metrics['avg_fault_duration']))
        
        # Get breakdown data
        load_shedding_hours = Decimal(str(metrics['load_shedding_hours']))
        other_fault_hours = Decimal(str(metrics['fault_hours']))
        
        # Infrastructure metrics
        if feeder:
            active_feeder_count = 1
        elif district:
            active_feeder_count = Feeder.objects.filter(business_district=district).count()
        elif state:
            active_feeder_count = Feeder.objects.filter(business_district__state=state).count()
        else:
            active_feeder_count = Feeder.objects.count()
        
        # Build detailed interruption breakdown from utility function
        interruption_breakdown = {
            k: v['duration'] 
            for k, v in metrics['breakdown_by_type'].items()
        }
        
        summary_breakdown = {
            'Load Shedding': metrics['load_shedding_hours'],
            'Equipment Fault': 0,  # Could be enhanced to categorize further
            'Line Fault': 0,
            'Maintenance': 0,
            'Other': metrics['fault_hours'],
        }
        
        # Enhanced breakdown by categorizing fault types
        for fault_type, data in metrics['breakdown_by_type'].items():
            if 'MTCE' in fault_type or 'MTNC' in fault_type:
                summary_breakdown['Maintenance'] += data['duration']
                summary_breakdown['Other'] -= data['duration']
            elif any(term in fault_type for term in ['T/F', 'CB/F', 'B/F', 'O/S']):
                summary_breakdown['Equipment Fault'] += data['duration']
                summary_breakdown['Other'] -= data['duration']
            elif any(term in fault_type for term in ['L/F', 'E/F', 'O/C', 'S/C']):
                summary_breakdown['Line Fault'] += data['duration']
                summary_breakdown['Other'] -= data['duration']
        
        # Ensure no negative values
        for key in summary_breakdown:
            if summary_breakdown[key] < 0:
                summary_breakdown[key] = 0
        
        final_metrics = {
            'total_energy_delivered': energy_delivered,
            'avg_peak_load': avg_peak_load,
            'max_peak_load': max_peak_load,
            'avg_hours_of_supply': avg_hours_supply,
            'total_supply_hours': total_supply_hours,
            'total_interruptions': total_interruptions,
            'avg_daily_interruptions': avg_daily_interruptions,
            'avg_interruption_duration': avg_interruption_duration,
            'total_interruption_hours': total_interruption_hours,
            'avg_turnaround_time': avg_turnaround_time,
            'avg_fault_turnaround_time': avg_fault_turnaround_time,
            'active_feeder_count': active_feeder_count,
            'total_customer_count': 0,
            'load_shedding_hours': load_shedding_hours,
            'equipment_fault_hours': Decimal(str(summary_breakdown['Equipment Fault'])),
            'line_fault_hours': Decimal(str(summary_breakdown['Line Fault'])),
            'maintenance_hours': Decimal(str(summary_breakdown['Maintenance'])),
            'other_fault_hours': Decimal(str(summary_breakdown['Other'])),
            'saifi': Decimal("0"),
            'saidi': Decimal("0"),
            'has_complete_data': True,
            'interruption_breakdown_json': interruption_breakdown,
        }
        
        # Create or update summary
        from analytics.models import MonthlyTechnicalSummary
        MonthlyTechnicalSummary.objects.update_or_create(
            month=month_date,
            state=state,
            business_district=district,
            feeder=feeder,
            defaults=final_metrics
        )
        
        logger.info(f"Updated monthly technical summary for {month_date} - Interruptions: {total_interruptions} (resolved: {metrics['resolved_count']}, unresolved: {metrics['unresolved_count']})")
        return True
        
    except Exception as exc:
        logger.error(f"Failed to update monthly technical summary: {exc}")
        return False


def update_daily_technical_summary_sync(date_str, state_id=None, district_id=None, feeder_id=None):
    """
    IMPROVED version using the new utility functions for interruption calculations
    """
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        from django.db.models import Sum, Count, Avg, Q
        from common.models import State, BusinessDistrict, Feeder
        from technical.models import EnergyDelivered, HourlyLoad, FeederInterruption, calculate_interruption_metrics
        from decimal import Decimal
        
        # Get filter objects
        state = State.objects.get(id=state_id) if state_id else None
        district = BusinessDistrict.objects.get(id=district_id) if district_id else None
        feeder = Feeder.objects.get(id=feeder_id) if feeder_id else None
        
        # Build filter for feeders based on hierarchy
        feeder_filter = Q()
        if feeder:
            feeder_filter = Q(feeder=feeder)
        elif district:
            feeder_filter = Q(feeder__business_district=district)
        elif state:
            feeder_filter = Q(feeder__business_district__state=state)
        
        # Energy delivered for the day
        energy_delivered = EnergyDelivered.objects.filter(
            feeder_filter,
            date=target_date
        ).aggregate(total=Sum("energy_mwh"))["total"] or Decimal("0")
        
        # Hours of supply calculation
        hourly_loads = HourlyLoad.objects.filter(
            feeder_filter,
            date=target_date,
            load_mw__gt=0
        )
        
        if feeder:
            # For single feeder, count unique hours with load
            hours_of_supply = hourly_loads.values('hour').distinct().count()
        else:
            # For multiple feeders, average across feeders
            feeder_hours = hourly_loads.values('feeder').annotate(
                daily_hours=Count('hour', distinct=True)
            )
            if feeder_hours.exists():
                hours_of_supply = feeder_hours.aggregate(avg=Avg('daily_hours'))['avg'] or 0
            else:
                hours_of_supply = 0
        
        # Peak load calculations
        load_data = HourlyLoad.objects.filter(
            feeder_filter,
            date=target_date
        ).aggregate(
            avg_peak=Avg('load_mw'),
            max_peak=Sum('load_mw')  # Adjust based on your needs
        )
        
        avg_peak_load = load_data['avg_peak'] or Decimal("0")
        max_peak_load = load_data['max_peak'] or Decimal("0")
        
        # IMPROVED: Interruption calculations using utility function
        interruptions = FeederInterruption.objects.filter(
            feeder_filter,
            occurred_at__date=target_date
        )
        
        # Use end of day as reference time for consistent historical analysis
        end_of_day = datetime.combine(target_date, datetime.max.time())
        end_of_day = timezone.make_aware(end_of_day) if timezone.is_naive(end_of_day) else end_of_day
        
        # Use utility function for comprehensive metrics
        metrics = calculate_interruption_metrics(
            interruptions, 
            reference_time=end_of_day
        )
        
        total_interruptions = metrics['total_interruptions']
        avg_interruption_duration = Decimal(str(metrics['avg_duration_hours']))
        total_interruption_hours_decimal = Decimal(str(metrics['total_duration_hours']))
        avg_turnaround_time = avg_interruption_duration
        avg_fault_turnaround_time = Decimal(str(metrics['avg_fault_duration']))
        
        load_shedding_hours_decimal = Decimal(str(metrics['load_shedding_hours']))
        fault_hours_decimal = Decimal(str(metrics['fault_hours']))
        
        # Infrastructure metrics
        if feeder:
            active_feeder_count = 1
        elif district:
            active_feeder_count = Feeder.objects.filter(business_district=district).count()
        elif state:
            active_feeder_count = Feeder.objects.filter(business_district__state=state).count()
        else:
            active_feeder_count = Feeder.objects.count()
        
        # Build detailed interruption breakdown from utility function
        interruption_breakdown = {
            k: v['duration'] 
            for k, v in metrics['breakdown_by_type'].items()
        }
        
        # Build enhanced summary breakdown by categorizing fault types
        summary_breakdown = {
            'Load Shedding': metrics['load_shedding_hours'],
            'Equipment Fault': 0,
            'Line Fault': 0,
            'Maintenance': 0,
            'Other': metrics['fault_hours'],
        }
        
        # Enhanced categorization of fault types
        for fault_type, data in metrics['breakdown_by_type'].items():
            fault_duration = data['duration']
            
            # Skip load shedding as it's already categorized
            if 'L/S' in fault_type:
                continue
                
            # Categorize maintenance
            if any(term in fault_type for term in ['MTCE', 'MTNC', 'permit']):
                summary_breakdown['Maintenance'] += fault_duration
                summary_breakdown['Other'] -= fault_duration
                
            # Categorize equipment faults
            elif any(term in fault_type for term in ['T/F', 'CB/F', 'B/F', 'O/S', 'O/N', 'EM/D']):
                summary_breakdown['Equipment Fault'] += fault_duration
                summary_breakdown['Other'] -= fault_duration
                
            # Categorize line faults
            elif any(term in fault_type for term in ['L/F', 'E/F', 'O/C', 'S/C', 'O/E', 'P/O', 'O/F']):
                summary_breakdown['Line Fault'] += fault_duration
                summary_breakdown['Other'] -= fault_duration
        
        # Ensure no negative values in summary breakdown
        for key in summary_breakdown:
            if summary_breakdown[key] < 0:
                summary_breakdown[key] = 0
        
        # Calculate SAIFI and SAIDI if customer count available
        # You can enhance this based on your customer model
        total_customer_count = 0  # TODO: Calculate based on your customer model
        saifi = total_interruptions / total_customer_count if total_customer_count > 0 else Decimal("0")
        saidi = float(total_interruption_hours_decimal) / total_customer_count if total_customer_count > 0 else Decimal("0")
        
        final_metrics = {
            'total_energy_delivered': energy_delivered,
            'avg_peak_load': avg_peak_load,
            'max_peak_load': max_peak_load,
            'hours_of_supply': Decimal(str(hours_of_supply)),
            'total_supply_hours': Decimal(str(hours_of_supply * active_feeder_count)),
            'total_interruptions': total_interruptions,
            'avg_interruption_duration': avg_interruption_duration,
            'total_interruption_hours': total_interruption_hours_decimal,
            'avg_turnaround_time': avg_turnaround_time,
            'avg_fault_turnaround_time': avg_fault_turnaround_time,
            'active_feeder_count': active_feeder_count,
            'total_customer_count': total_customer_count,
            'load_shedding_hours': load_shedding_hours_decimal,
            'equipment_fault_hours': Decimal(str(summary_breakdown['Equipment Fault'])),
            'line_fault_hours': Decimal(str(summary_breakdown['Line Fault'])),
            'maintenance_hours': Decimal(str(summary_breakdown['Maintenance'])),
            'other_fault_hours': Decimal(str(summary_breakdown['Other'])),
            'saifi': Decimal(str(saifi)),
            'saidi': Decimal(str(saidi)),
            'has_complete_data': True,
            'interruption_breakdown_json': interruption_breakdown,
        }
        
        # Create or update summary
        from analytics.models import DailyTechnicalSummary
        DailyTechnicalSummary.objects.update_or_create(
            date=target_date,
            state=state,
            business_district=district,
            feeder=feeder,
            defaults=final_metrics
        )
        
        logger.info(
            f"Updated daily technical summary for {target_date} - "
            f"Interruptions: {total_interruptions} "
            f"(resolved: {metrics['resolved_count']}, unresolved: {metrics['unresolved_count']}) - "
            f"Total duration: {float(total_interruption_hours_decimal):.2f}h"
        )
        return True
        
    except Exception as exc:
        logger.error(f"Failed to update daily technical summary for {date_str}: {exc}")
        import traceback
        logger.error(traceback.format_exc())
        return False


# ==================== TASK EXECUTION ====================

def execute_update_task(task_func, *args, **kwargs):
    """
    Execute update task either async (Celery) or sync
    """
    if is_celery_available():
        try:
            # Try to execute with Celery
            from celery import current_app
            task_func.apply_async(args=args, countdown=getattr(settings, 'ANALYTICS_TASK_DELAY', 5))
            return
        except Exception as e:
            logger.warning(f"Celery execution failed: {e}. Falling back to sync execution.")
    
    # Execute synchronously
    try:
        task_func(*args)
    except Exception as e:
        logger.error(f"Sync task execution failed: {e}")


# ==================== CELERY TASKS (OPTIONAL) ====================

if is_celery_available():
    try:
        from celery import shared_task

        @shared_task(bind=True, max_retries=3)
        def update_monthly_overview_summary_task(self, month_date_str):
            """Celery task to update monthly overview summary"""
            try:
                return update_monthly_overview_summary_sync(month_date_str)
            except Exception as exc:
                logger.error(f"Celery task failed: {exc}")
                raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))

        @shared_task(bind=True, max_retries=3)
        def update_monthly_technical_summary_task(self, month_date_str, state_id=None, district_id=None, feeder_id=None):
            """Celery task to update monthly technical summary"""
            try:
                return update_monthly_technical_summary_sync(month_date_str, state_id, district_id, feeder_id)
            except Exception as exc:
                logger.error(f"Celery task failed: {exc}")
                raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))

        @shared_task(bind=True, max_retries=3)
        def update_daily_technical_summary_task(self, date_str, state_id=None, district_id=None, feeder_id=None):
            """Celery task to update daily technical summary"""
            try:
                return update_daily_technical_summary_sync(date_str, state_id, district_id, feeder_id)
            except Exception as exc:
                logger.error(f"Celery task failed: {exc}")
                raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))

    except ImportError:
        logger.info("Celery not available")


# ==================== SIGNAL HANDLERS ====================

@receiver([post_save, post_delete], sender=MonthlyCommercialSummary)
@skip_if_signals_disabled
def handle_monthly_commercial_summary_change(sender, instance, **kwargs):
    """Handle changes to MonthlyCommercialSummary"""
    if not instance.month:
        return
    
    SummaryUpdateTracker.add_monthly_overview_update(instance.month)
    
    def update_summary():
        update_monthly_overview_summary_sync(instance.month.strftime('%Y-%m-%d'))
    
    transaction.on_commit(update_summary)


@receiver([post_save, post_delete], sender=DailyCollection)
@skip_if_signals_disabled
def handle_daily_collection_change(sender, instance, **kwargs):
    """Handle changes to DailyCollection"""
    if not instance.date:
        return
    
    month_date = instance.date.replace(day=1)
    SummaryUpdateTracker.add_monthly_overview_update(month_date)
    
    def update_summary():
        update_monthly_overview_summary_sync(month_date.strftime('%Y-%m-%d'))
    
    transaction.on_commit(update_summary)


@receiver([post_save, post_delete], sender=MonthlyEnergyBilled)
@skip_if_signals_disabled
def handle_monthly_energy_billed_change(sender, instance, **kwargs):
    """Handle changes to MonthlyEnergyBilled"""
    if not instance.month:
        return
    
    SummaryUpdateTracker.add_monthly_overview_update(instance.month)
    
    def update_summary():
        update_monthly_overview_summary_sync(instance.month.strftime('%Y-%m-%d'))
    
    transaction.on_commit(update_summary)


@receiver([post_save, post_delete], sender=EnergyDelivered)
@skip_if_signals_disabled
def handle_energy_delivered_change(sender, instance, **kwargs):
    """Handle changes to EnergyDelivered"""
    if not instance.date or not hasattr(instance, 'feeder'):
        return
    
    month_date = instance.date.replace(day=1)
    
    # Update monthly overview
    SummaryUpdateTracker.add_monthly_overview_update(month_date)
    
    def update_summaries():
        update_monthly_overview_summary_sync(month_date.strftime('%Y-%m-%d'))
        
        # Update technical summaries for different levels
        feeder = instance.feeder
        district = feeder.business_district if feeder else None
        state = district.state if district else None
        
        # National level
        update_monthly_technical_summary_sync(month_date.strftime('%Y-%m-%d'))
        update_daily_technical_summary_sync(instance.date.strftime('%Y-%m-%d'))
        
        # State level
        if state:
            update_monthly_technical_summary_sync(month_date.strftime('%Y-%m-%d'), state_id=state.id)
            update_daily_technical_summary_sync(instance.date.strftime('%Y-%m-%d'), state_id=state.id)
        
        # District level
        if district:
            update_monthly_technical_summary_sync(month_date.strftime('%Y-%m-%d'), district_id=district.id)
            update_daily_technical_summary_sync(instance.date.strftime('%Y-%m-%d'), district_id=district.id)
        
        # Feeder level
        if feeder:
            update_monthly_technical_summary_sync(month_date.strftime('%Y-%m-%d'), feeder_id=feeder.id)
            update_daily_technical_summary_sync(instance.date.strftime('%Y-%m-%d'), feeder_id=feeder.id)
    
    transaction.on_commit(update_summaries)


@receiver([post_save, post_delete], sender=HourlyLoad)
@skip_if_signals_disabled
def handle_hourly_load_change(sender, instance, **kwargs):
    """Handle changes to HourlyLoad"""
    if not instance.date or not instance.feeder:
        return
    
    month_date = instance.date.replace(day=1)
    feeder = instance.feeder
    district = feeder.business_district if feeder else None
    state = district.state if district else None
    
    def update_summaries():
        # Update technical summaries for all levels
        update_daily_technical_summary_sync(instance.date.strftime('%Y-%m-%d'))
        update_monthly_technical_summary_sync(month_date.strftime('%Y-%m-%d'))
        
        if state:
            update_daily_technical_summary_sync(instance.date.strftime('%Y-%m-%d'), state_id=state.id)
            update_monthly_technical_summary_sync(month_date.strftime('%Y-%m-%d'), state_id=state.id)
        
        if district:
            update_daily_technical_summary_sync(instance.date.strftime('%Y-%m-%d'), district_id=district.id)
            update_monthly_technical_summary_sync(month_date.strftime('%Y-%m-%d'), district_id=district.id)
        
        if feeder:
            update_daily_technical_summary_sync(instance.date.strftime('%Y-%m-%d'), feeder_id=feeder.id)
            update_monthly_technical_summary_sync(month_date.strftime('%Y-%m-%d'), feeder_id=feeder.id)
    
    transaction.on_commit(update_summaries)


@receiver([post_save, post_delete], sender=FeederInterruption)
@skip_if_signals_disabled
def handle_feeder_interruption_change(sender, instance, **kwargs):
    """Handle changes to FeederInterruption"""
    if not instance.occurred_at or not instance.feeder:
        return
    
    interruption_date = instance.occurred_at.date()
    month_date = interruption_date.replace(day=1)
    feeder = instance.feeder
    district = feeder.business_district if feeder else None
    state = district.state if district else None
    
    def update_summaries():
        # Update technical summaries for all levels
        update_daily_technical_summary_sync(interruption_date.strftime('%Y-%m-%d'))
        update_monthly_technical_summary_sync(month_date.strftime('%Y-%m-%d'))
        
        if state:
            update_daily_technical_summary_sync(interruption_date.strftime('%Y-%m-%d'), state_id=state.id)
            update_monthly_technical_summary_sync(month_date.strftime('%Y-%m-%d'), state_id=state.id)
        
        if district:
            update_daily_technical_summary_sync(interruption_date.strftime('%Y-%m-%d'), district_id=district.id)
            update_monthly_technical_summary_sync(month_date.strftime('%Y-%m-%d'), district_id=district.id)
        
        if feeder:
            update_daily_technical_summary_sync(interruption_date.strftime('%Y-%m-%d'), feeder_id=feeder.id)
            update_monthly_technical_summary_sync(month_date.strftime('%Y-%m-%d'), feeder_id=feeder.id)
    
    transaction.on_commit(update_summaries)


# Financial signals
@receiver([post_save, post_delete], sender=Opex)
@skip_if_signals_disabled
def handle_opex_change(sender, instance, **kwargs):
    """Handle changes to Opex"""
    if not instance.date:
        return
    
    month_date = instance.date.replace(day=1)
    SummaryUpdateTracker.add_monthly_overview_update(month_date)
    
    def update_summary():
        update_monthly_overview_summary_sync(month_date.strftime('%Y-%m-%d'))
    
    transaction.on_commit(update_summary)


@receiver([post_save, post_delete], sender=HQOpex)
@skip_if_signals_disabled
def handle_hq_opex_change(sender, instance, **kwargs):
    """Handle changes to HQOpex"""
    if not instance.date:
        return
    
    month_date = instance.date.replace(day=1)
    SummaryUpdateTracker.add_monthly_overview_update(month_date)
    
    def update_summary():
        update_monthly_overview_summary_sync(month_date.strftime('%Y-%m-%d'))
    
    transaction.on_commit(update_summary)


@receiver([post_save, post_delete], sender=SalaryPayment)
@skip_if_signals_disabled
def handle_salary_payment_change(sender, instance, **kwargs):
    """Handle changes to SalaryPayment"""
    if not instance.month:
        return
    
    SummaryUpdateTracker.add_monthly_overview_update(instance.month)
    
    def update_summary():
        update_monthly_overview_summary_sync(instance.month.strftime('%Y-%m-%d'))
    
    transaction.on_commit(update_summary)


@receiver([post_save, post_delete], sender=NBETInvoice)
@skip_if_signals_disabled
def handle_nbet_invoice_change(sender, instance, **kwargs):
    """Handle changes to NBETInvoice"""
    if not instance.month:
        return
    
    SummaryUpdateTracker.add_monthly_overview_update(instance.month)
    
    def update_summary():
        update_monthly_overview_summary_sync(instance.month.strftime('%Y-%m-%d'))
    
    transaction.on_commit(update_summary)


@receiver([post_save, post_delete], sender=MOInvoice)
@skip_if_signals_disabled
def handle_mo_invoice_change(sender, instance, **kwargs):
    """Handle changes to MOInvoice"""
    if not instance.month:
        return
    
    SummaryUpdateTracker.add_monthly_overview_update(instance.month)
    
    def update_summary():
        update_monthly_overview_summary_sync(instance.month.strftime('%Y-%m-%d'))
    
    transaction.on_commit(update_summary)


@receiver([post_save, post_delete], sender=Staff)
@skip_if_signals_disabled
def handle_staff_change(sender, instance, **kwargs):
    """Handle changes to Staff records"""
    if not instance.hire_date:
        return
    
    start_month = instance.hire_date.replace(day=1)
    end_month = instance.exit_date.replace(day=1) if instance.exit_date else date.today().replace(day=1)
    
    def update_summaries():
        # Update all affected months
        current_month = start_month
        while current_month <= end_month:
            SummaryUpdateTracker.add_monthly_overview_update(current_month)
            update_monthly_overview_summary_sync(current_month.strftime('%Y-%m-%d'))
            current_month += relativedelta(months=1)
    
    transaction.on_commit(update_summaries)


# ==================== UTILITY FUNCTIONS ====================

def trigger_full_summary_refresh(start_date=None, end_date=None):
    """
    Utility function to trigger a full refresh of all summaries
    """
    if not start_date:
        start_date = date(2020, 1, 1)
    if not end_date:
        end_date = date.today()
    
    logger.info(f"Triggering full summary refresh from {start_date} to {end_date}")
    
    # Trigger monthly overview updates
    current_month = start_date.replace(day=1)
    while current_month <= end_date.replace(day=1):
        update_monthly_overview_summary_sync(current_month.strftime('%Y-%m-%d'))
        current_month += relativedelta(months=1)