# analytics/management/commands/manage_analytics_signals.py
import json
from datetime import date, datetime

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.db import models

from analytics.signals import SummaryUpdateTracker, trigger_full_summary_refresh


class Command(BaseCommand):
    help = 'Manage analytics summary auto-update signals'

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            choices=['status', 'enable', 'disable', 'refresh', 'clear_pending', 'stats'],
            help='Action to perform'
        )
        
        parser.add_argument(
            '--start-date',
            type=str,
            help='Start date for refresh (YYYY-MM-DD format)'
        )
        
        parser.add_argument(
            '--end-date',
            type=str,
            help='End date for refresh (YYYY-MM-DD format)'
        )
        
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force action without confirmation'
        )

    def handle(self, *args, **options):
        action = options['action']
        
        if action == 'status':
            self.show_status()
        
        elif action == 'enable':
            self.enable_signals()
        
        elif action == 'disable':
            self.disable_signals()
        
        elif action == 'refresh':
            self.trigger_refresh(options)
        
        elif action == 'clear_pending':
            self.clear_pending_updates()
        
        elif action == 'stats':
            self.show_statistics()

    def show_status(self):
        """Show current signal status"""
        signals_enabled = not cache.get('analytics_signals_disabled', False)
        
        self.stdout.write("Analytics Signals Status:")
        self.stdout.write(f"  Enabled: {'✅ Yes' if signals_enabled else '❌ No'}")
        
        if not signals_enabled:
            disabled_at = cache.get('analytics_signals_disabled_at')
            if disabled_at:
                self.stdout.write(f"  Disabled at: {disabled_at}")
        
        # Show pending updates
        pending_overview = len(SummaryUpdateTracker._pending_updates['monthly_overview'])
        pending_monthly_tech = len(SummaryUpdateTracker._pending_updates['monthly_technical'])
        pending_daily_tech = len(SummaryUpdateTracker._pending_updates['daily_technical'])
        
        self.stdout.write(f"\nPending Updates:")
        self.stdout.write(f"  Monthly Overview: {pending_overview}")
        self.stdout.write(f"  Monthly Technical: {pending_monthly_tech}")
        self.stdout.write(f"  Daily Technical: {pending_daily_tech}")
        
        # Show Celery queue status (if available)
        try:
            from celery import current_app
            inspect = current_app.control.inspect()
            
            # Get active tasks
            active_tasks = inspect.active()
            if active_tasks:
                analytics_tasks = []
                for worker, tasks in active_tasks.items():
                    for task in tasks:
                        if 'analytics' in task.get('name', ''):
                            analytics_tasks.append(task)
                
                self.stdout.write(f"\nActive Celery Tasks: {len(analytics_tasks)}")
            
        except ImportError:
            self.stdout.write(f"\nCelery not available - tasks will run synchronously")

    def enable_signals(self):
        """Enable analytics signals"""
        cache.delete('analytics_signals_disabled')
        cache.delete('analytics_signals_disabled_at')
        
        self.stdout.write(
            self.style.SUCCESS('✅ Analytics signals enabled')
        )

    def disable_signals(self):
        """Disable analytics signals"""
        cache.set('analytics_signals_disabled', True, timeout=None)
        cache.set('analytics_signals_disabled_at', datetime.now().isoformat(), timeout=None)
        
        self.stdout.write(
            self.style.WARNING('⏸️  Analytics signals disabled')
        )
        
        self.stdout.write(
            "Note: Existing pending updates will still be processed. "
            "Use 'clear_pending' to cancel them."
        )

    def trigger_refresh(self, options):
        """Trigger a full refresh of summaries"""
        start_date = None
        end_date = None
        
        if options['start_date']:
            try:
                start_date = datetime.strptime(options['start_date'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('Invalid start date format. Use YYYY-MM-DD')
        
        if options['end_date']:
            try:
                end_date = datetime.strptime(options['end_date'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('Invalid end date format. Use YYYY-MM-DD')
        
        if not options['force']:
            if start_date and end_date:
                date_range = f"from {start_date} to {end_date}"
            elif start_date:
                date_range = f"from {start_date} to present"
            else:
                date_range = "for all available data"
            
            confirm = input(f"This will trigger a full summary refresh {date_range}. Continue? [y/N]: ")
            if confirm.lower() != 'y':
                self.stdout.write("Refresh cancelled")
                return
        
        self.stdout.write("Triggering full summary refresh...")
        trigger_full_summary_refresh(start_date, end_date)
        
        self.stdout.write(
            self.style.SUCCESS('✅ Full summary refresh triggered')
        )

    def clear_pending_updates(self):
        """Clear all pending updates"""
        overview_count = len(SummaryUpdateTracker._pending_updates['monthly_overview'])
        monthly_tech_count = len(SummaryUpdateTracker._pending_updates['monthly_technical'])
        daily_tech_count = len(SummaryUpdateTracker._pending_updates['daily_technical'])
        
        total_count = overview_count + monthly_tech_count + daily_tech_count
        
        if total_count == 0:
            self.stdout.write("No pending updates to clear")
            return
        
        self.stdout.write(f"Clearing {total_count} pending updates:")
        self.stdout.write(f"  Monthly Overview: {overview_count}")
        self.stdout.write(f"  Monthly Technical: {monthly_tech_count}")
        self.stdout.write(f"  Daily Technical: {daily_tech_count}")
        
        # Clear the tracking sets
        SummaryUpdateTracker._pending_updates['monthly_overview'].clear()
        SummaryUpdateTracker._pending_updates['monthly_technical'].clear()
        SummaryUpdateTracker._pending_updates['daily_technical'].clear()
        
        self.stdout.write(
            self.style.SUCCESS('✅ Pending updates cleared')
        )

    def show_statistics(self):
        """Show analytics summary statistics"""
        from datetime import timedelta

        from django.db.models import Avg, Count, Max, Min
        from django.utils import timezone

        from analytics.models import (
            DailyTechnicalSummary,
            MonthlyOverviewSummary,
            MonthlyTechnicalSummary,
        )
        
        self.stdout.write("Analytics Summary Statistics:")
        
        # Monthly Overview Summary stats
        overview_stats = MonthlyOverviewSummary.objects.aggregate(
            total=Count('id'),
            oldest=Min('month'),
            newest=Max('month'),
            avg_calc_time=Avg('calculation_duration'),
            complete_data=Count('id', filter=models.Q(has_complete_data=True))
        )
        
        self.stdout.write(f"\nMonthly Overview Summaries:")
        self.stdout.write(f"  Total records: {overview_stats['total']}")
        if overview_stats['oldest']:
            self.stdout.write(f"  Date range: {overview_stats['oldest']} to {overview_stats['newest']}")
        if overview_stats['avg_calc_time']:
            avg_seconds = overview_stats['avg_calc_time'].total_seconds()
            self.stdout.write(f"  Avg calculation time: {avg_seconds:.2f}s")
        self.stdout.write(f"  Complete data: {overview_stats['complete_data']}/{overview_stats['total']}")
        
        # Monthly Technical Summary stats
        monthly_tech_stats = MonthlyTechnicalSummary.objects.aggregate(
            total=Count('id'),
            oldest=Min('month'),
            newest=Max('month'),
            complete_data=Count('id', filter=models.Q(has_complete_data=True))
        )
        
        # Count by filter level
        monthly_tech_levels = {
            'National': MonthlyTechnicalSummary.objects.filter(
                state__isnull=True, business_district__isnull=True, feeder__isnull=True
            ).count(),
            'State': MonthlyTechnicalSummary.objects.filter(
                state__isnull=False, business_district__isnull=True, feeder__isnull=True
            ).count(),
            'District': MonthlyTechnicalSummary.objects.filter(
                business_district__isnull=False, feeder__isnull=True
            ).count(),
            'Feeder': MonthlyTechnicalSummary.objects.filter(
                feeder__isnull=False
            ).count(),
        }
        
        self.stdout.write(f"\nMonthly Technical Summaries:")
        self.stdout.write(f"  Total records: {monthly_tech_stats['total']}")
        if monthly_tech_stats['oldest']:
            self.stdout.write(f"  Date range: {monthly_tech_stats['oldest']} to {monthly_tech_stats['newest']}")
        self.stdout.write(f"  Complete data: {monthly_tech_stats['complete_data']}/{monthly_tech_stats['total']}")
        self.stdout.write(f"  By level:")
        for level, count in monthly_tech_levels.items():
            self.stdout.write(f"    {level}: {count}")
        
        # Daily Technical Summary stats
        daily_tech_stats = DailyTechnicalSummary.objects.aggregate(
            total=Count('id'),
            oldest=Min('date'),
            newest=Max('date'),
            complete_data=Count('id', filter=models.Q(has_complete_data=True))
        )
        
        # Count by filter level
        daily_tech_levels = {
            'National': DailyTechnicalSummary.objects.filter(
                state__isnull=True, business_district__isnull=True, feeder__isnull=True
            ).count(),
            'State': DailyTechnicalSummary.objects.filter(
                state__isnull=False, business_district__isnull=True, feeder__isnull=True
            ).count(),
            'District': DailyTechnicalSummary.objects.filter(
                business_district__isnull=False, feeder__isnull=True
            ).count(),
            'Feeder': DailyTechnicalSummary.objects.filter(
                feeder__isnull=False
            ).count(),
        }
        
        self.stdout.write(f"\nDaily Technical Summaries:")
        self.stdout.write(f"  Total records: {daily_tech_stats['total']}")
        if daily_tech_stats['oldest']:
            self.stdout.write(f"  Date range: {daily_tech_stats['oldest']} to {daily_tech_stats['newest']}")
        self.stdout.write(f"  Complete data: {daily_tech_stats['complete_data']}/{daily_tech_stats['total']}")
        self.stdout.write(f"  By level:")
        for level, count in daily_tech_levels.items():
            self.stdout.write(f"    {level}: {count}")
        
        # Recent activity
        last_24h = timezone.now() - timedelta(hours=24)
        recent_activity = {
            'overview': MonthlyOverviewSummary.objects.filter(calculated_at__gte=last_24h).count(),
            'monthly_tech': MonthlyTechnicalSummary.objects.filter(calculated_at__gte=last_24h).count(),
            'daily_tech': DailyTechnicalSummary.objects.filter(calculated_at__gte=last_24h).count(),
        }
        
        self.stdout.write(f"\nRecent Activity (last 24h):")
        self.stdout.write(f"  Overview updates: {recent_activity['overview']}")
        self.stdout.write(f"  Monthly tech updates: {recent_activity['monthly_tech']}")
        self.stdout.write(f"  Daily tech updates: {recent_activity['daily_tech']}")
        
        # Data quality metrics
        self.stdout.write(f"\nData Quality:")
        
        # Find months with missing overview summaries
        from datetime import date
        current_month = date.today().replace(day=1)
        months_to_check = []
        check_month = date(2020, 1, 1)
        while check_month <= current_month:
            months_to_check.append(check_month)
            check_month += relativedelta(months=1)
        
        existing_overview_months = set(
            MonthlyOverviewSummary.objects.values_list('month', flat=True)
        )
        missing_overview_months = [
            month for month in months_to_check 
            if month not in existing_overview_months
        ]
        
        if missing_overview_months:
            self.stdout.write(f"  Missing overview summaries: {len(missing_overview_months)} months")
            if len(missing_overview_months) <= 5:
                for month in missing_overview_months:
                    self.stdout.write(f"    - {month}")
            else:
                self.stdout.write(f"    First few: {', '.join(str(m) for m in missing_overview_months[:3])}")
        else:
            self.stdout.write(f"  ✅ All monthly overview summaries present")
        
        # Check for stale summaries (current month older than 24h)
        current_month_summary = MonthlyOverviewSummary.objects.filter(
            month=current_month
        ).first()
        
        if current_month_summary:
            age = timezone.now() - current_month_summary.calculated_at
            if age > timedelta(hours=24):
                self.stdout.write(
                    f"  ⚠️  Current month summary is {age.days} days old"
                )
            else:
                self.stdout.write(f"  ✅ Current month summary is up to date")
        else:
            self.stdout.write(f"  ❌ Current month summary missing")


# ==================== ADDITIONAL UTILITY FUNCTIONS ====================

def disable_signals_temporarily(func):
    """
    Decorator to temporarily disable analytics signals during bulk operations
    
    Usage:
    @disable_signals_temporarily
    def bulk_data_import():
        # Your bulk operation here
        pass
    """
    def wrapper(*args, **kwargs):
        # Set flag to disable signals
        cache.set('analytics_signals_disabled', True, timeout=3600)  # 1 hour timeout
        cache.set('analytics_signals_disabled_at', datetime.now().isoformat(), timeout=3600)
        
        try:
            result = func(*args, **kwargs)
        finally:
            # Re-enable signals
            cache.delete('analytics_signals_disabled')
            cache.delete('analytics_signals_disabled_at')
        
        return result
    return wrapper


def are_signals_enabled():
    """
    Check if analytics signals are currently enabled
    """
    return not cache.get('analytics_signals_disabled', False)


def get_signal_status():
    """
    Get detailed signal status information
    """
    signals_enabled = are_signals_enabled()
    disabled_at = cache.get('analytics_signals_disabled_at')
    
    return {
        'enabled': signals_enabled,
        'disabled_at': disabled_at,
        'pending_updates': {
            'monthly_overview': len(SummaryUpdateTracker._pending_updates['monthly_overview']),
            'monthly_technical': len(SummaryUpdateTracker._pending_updates['monthly_technical']),
            'daily_technical': len(SummaryUpdateTracker._pending_updates['daily_technical']),
        }
    }