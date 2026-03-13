# analytics/management/commands/refresh_analytics_with_unresolved.py
from datetime import date, datetime, timedelta

from dateutil.relativedelta import relativedelta
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from analytics.models import DailyTechnicalSummary, MonthlyTechnicalSummary
from common.models import BusinessDistrict, Feeder, State
from technical.models import FeederInterruption, calculate_interruption_metrics


class Command(BaseCommand):
    help = 'Refresh analytics summaries to include unresolved interruptions'

    def add_arguments(self, parser):
        parser.add_argument(
            '--start-date',
            type=str,
            help='Start date for refresh (YYYY-MM-DD format). Defaults to 3 months ago'
        )
        
        parser.add_argument(
            '--end-date',
            type=str,
            help='End date for refresh (YYYY-MM-DD format). Defaults to today'
        )
        
        parser.add_argument(
            '--mode',
            choices=['monthly', 'daily', 'both'],
            default='both',
            help='Which summaries to refresh'
        )
        
        parser.add_argument(
            '--state',
            type=str,
            help='State name to filter refreshing (optional)'
        )
        
        parser.add_argument(
            '--district',
            type=str,
            help='Business district name to filter refreshing (optional)'
        )
        
        parser.add_argument(
            '--feeder',
            type=str,
            help='Feeder slug to filter refreshing (optional)'
        )
        
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes'
        )
        
        parser.add_argument(
            '--check-unresolved',
            action='store_true',
            help='Show current unresolved interruptions without updating'
        )

    def handle(self, *args, **options):
        if options['check_unresolved']:
            self.check_unresolved_interruptions()
            return
        
        # Parse dates
        if options['start_date']:
            try:
                start_date = datetime.strptime(options['start_date'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('Invalid start date format. Use YYYY-MM-DD')
        else:
            start_date = date.today() - relativedelta(months=3)
        
        if options['end_date']:
            try:
                end_date = datetime.strptime(options['end_date'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('Invalid end date format. Use YYYY-MM-DD')
        else:
            end_date = date.today()
        
        # Parse filter parameters
        filter_params = self.parse_filter_params(options)
        
        self.stdout.write(
            f"Refreshing analytics summaries from {start_date} to {end_date}"
        )
        if filter_params['state']:
            self.stdout.write(f"  State filter: {filter_params['state'].name}")
        if filter_params['business_district']:
            self.stdout.write(f"  District filter: {filter_params['business_district'].name}")
        if filter_params['feeder']:
            self.stdout.write(f"  Feeder filter: {filter_params['feeder'].name}")
        
        if options['dry_run']:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No changes will be made"))
        
        # Show current unresolved interruptions
        self.show_unresolved_summary(start_date, end_date, filter_params)
        
        if options['mode'] in ['monthly', 'both']:
            self.refresh_monthly_summaries(start_date, end_date, filter_params, options['dry_run'])
        
        if options['mode'] in ['daily', 'both']:
            self.refresh_daily_summaries(start_date, end_date, filter_params, options['dry_run'])

    def parse_filter_params(self, options):
        """Parse filtering parameters"""
        filter_params = {
            'state': None,
            'business_district': None,
            'feeder': None
        }
        
        if options['state']:
            try:
                filter_params['state'] = State.objects.get(name__iexact=options['state'])
            except State.DoesNotExist:
                raise CommandError(f"State not found: {options['state']}")
        
        if options['district']:
            try:
                qs = BusinessDistrict.objects.filter(name__iexact=options['district'])
                if filter_params['state']:
                    qs = qs.filter(state=filter_params['state'])
                filter_params['business_district'] = qs.first()
                if not filter_params['business_district']:
                    raise CommandError(f"Business district not found: {options['district']}")
            except Exception:
                raise CommandError(f"Error finding business district: {options['district']}")
        
        if options['feeder']:
            try:
                qs = Feeder.objects.filter(slug=options['feeder'])
                if filter_params['business_district']:
                    qs = qs.filter(business_district=filter_params['business_district'])
                elif filter_params['state']:
                    qs = qs.filter(business_district__state=filter_params['state'])
                
                filter_params['feeder'] = qs.first()
                if not filter_params['feeder']:
                    raise CommandError(f"Feeder not found: {options['feeder']}")
                    
                # Auto-set higher level filters for consistency
                if not filter_params['business_district']:
                    filter_params['business_district'] = filter_params['feeder'].business_district
                if not filter_params['state']:
                    filter_params['state'] = filter_params['feeder'].business_district.state
                    
            except Exception as e:
                raise CommandError(f"Error finding feeder: {options['feeder']} - {e}")
        
        return filter_params

    def check_unresolved_interruptions(self):
        """Show current unresolved interruptions"""
        self.stdout.write("Current Unresolved Interruptions:")
        
        unresolved = FeederInterruption.objects.filter(restored_at__isnull=True).order_by('-occurred_at')
        
        if not unresolved.exists():
            self.stdout.write("  ✅ No unresolved interruptions found")
            return
        
        self.stdout.write(f"  Found {unresolved.count()} unresolved interruptions:")
        
        for interruption in unresolved[:10]:  # Show first 10
            duration = interruption.duration_hours
            self.stdout.write(
                f"    - {interruption.feeder.name}: {interruption.interruption_type} "
                f"since {interruption.occurred_at} ({duration:.1f}h ago)"
            )
        
        if unresolved.count() > 10:
            self.stdout.write(f"    ... and {unresolved.count() - 10} more")

    def show_unresolved_summary(self, start_date, end_date, filter_params):
        """Show summary of unresolved interruptions in date range"""
        from django.db.models import Q

        # Build filter
        interruption_filter = Q(
            occurred_at__date__range=[start_date, end_date],
            restored_at__isnull=True
        )
        
        if filter_params['feeder']:
            interruption_filter &= Q(feeder=filter_params['feeder'])
        elif filter_params['business_district']:
            interruption_filter &= Q(feeder__business_district=filter_params['business_district'])
        elif filter_params['state']:
            interruption_filter &= Q(feeder__business_district__state=filter_params['state'])
        
        unresolved_in_range = FeederInterruption.objects.filter(interruption_filter)
        
        if unresolved_in_range.exists():
            self.stdout.write(
                self.style.WARNING(
                    f"Found {unresolved_in_range.count()} unresolved interruptions in date range"
                )
            )
            
            # Show breakdown by feeder
            feeder_counts = {}
            total_duration = 0
            
            for interruption in unresolved_in_range:
                feeder_name = interruption.feeder.name
                feeder_counts[feeder_name] = feeder_counts.get(feeder_name, 0) + 1
                total_duration += interruption.duration_hours
            
            self.stdout.write("  By feeder:")
            for feeder_name, count in sorted(feeder_counts.items()):
                self.stdout.write(f"    {feeder_name}: {count} interruptions")
            
            self.stdout.write(f"  Total combined duration: {total_duration:.1f} hours")
        else:
            self.stdout.write("  ✅ No unresolved interruptions in date range")

    def refresh_monthly_summaries(self, start_date, end_date, filter_params, dry_run):
        """Refresh monthly technical summaries"""
        self.stdout.write("\n" + "="*50)
        self.stdout.write("Refreshing Monthly Technical Summaries")
        self.stdout.write("="*50)
        
        # Generate list of months to refresh
        current_month = start_date.replace(day=1)
        end_month = end_date.replace(day=1)
        months_to_refresh = []
        
        while current_month <= end_month:
            months_to_refresh.append(current_month)
            current_month += relativedelta(months=1)
        
        self.stdout.write(f"Months to refresh: {len(months_to_refresh)}")
        
        updated_count = 0
        
        for month in months_to_refresh:
            self.stdout.write(f"\nProcessing {month.strftime('%Y-%m')}...")
            
            # Check if interruptions exist in this month
            from django.db.models import Q
            
            interruption_filter = Q(
                occurred_at__year=month.year,
                occurred_at__month=month.month
            )
            
            if filter_params['feeder']:
                interruption_filter &= Q(feeder=filter_params['feeder'])
            elif filter_params['business_district']:
                interruption_filter &= Q(feeder__business_district=filter_params['business_district'])
            elif filter_params['state']:
                interruption_filter &= Q(feeder__business_district__state=filter_params['state'])
            
            interruptions = FeederInterruption.objects.filter(interruption_filter)
            unresolved_count = interruptions.filter(restored_at__isnull=True).count()
            
            if interruptions.exists():
                self.stdout.write(f"  Found {interruptions.count()} interruptions ({unresolved_count} unresolved)")
                
                if not dry_run:
                    # Import the improved function
                    from analytics.signals import update_monthly_technical_summary_sync
                    
                    try:
                        success = update_monthly_technical_summary_sync(
                            month.strftime('%Y-%m-%d'),
                            state_id=filter_params['state'].id if filter_params['state'] else None,
                            district_id=filter_params['business_district'].id if filter_params['business_district'] else None,
                            feeder_id=filter_params['feeder'].id if filter_params['feeder'] else None
                        )
                        
                        if success:
                            updated_count += 1
                            self.stdout.write(f"  ✅ Updated summary for {month.strftime('%Y-%m')}")
                        else:
                            self.stdout.write(f"  ❌ Failed to update summary for {month.strftime('%Y-%m')}")
                    
                    except Exception as e:
                        self.stdout.write(f"  ❌ Error updating {month.strftime('%Y-%m')}: {e}")
                else:
                    self.stdout.write(f"  [DRY RUN] Would update summary for {month.strftime('%Y-%m')}")
                    updated_count += 1
            else:
                self.stdout.write(f"  No interruptions found for {month.strftime('%Y-%m')}")
        
        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(f"\n✅ Successfully updated {updated_count} monthly summaries")
            )
        else:
            self.stdout.write(
                self.style.WARNING(f"\n[DRY RUN] Would have updated {updated_count} monthly summaries")
            )

    def refresh_daily_summaries(self, start_date, end_date, filter_params, dry_run):
        """Refresh daily technical summaries"""
        self.stdout.write("\n" + "="*50)
        self.stdout.write("Refreshing Daily Technical Summaries")
        self.stdout.write("="*50)
        
        # Generate list of dates to refresh
        dates_to_refresh = []
        current_date = start_date
        
        while current_date <= end_date:
            dates_to_refresh.append(current_date)
            current_date += timedelta(days=1)
        
        self.stdout.write(f"Dates to refresh: {len(dates_to_refresh)}")
        
        updated_count = 0
        
        for target_date in dates_to_refresh:
            # Check if interruptions exist on this date
            from django.db.models import Q
            
            interruption_filter = Q(occurred_at__date=target_date)
            
            if filter_params['feeder']:
                interruption_filter &= Q(feeder=filter_params['feeder'])
            elif filter_params['business_district']:
                interruption_filter &= Q(feeder__business_district=filter_params['business_district'])
            elif filter_params['state']:
                interruption_filter &= Q(feeder__business_district__state=filter_params['state'])
            
            interruptions = FeederInterruption.objects.filter(interruption_filter)
            
            if interruptions.exists():
                unresolved_count = interruptions.filter(restored_at__isnull=True).count()
                self.stdout.write(f"{target_date}: {interruptions.count()} interruptions ({unresolved_count} unresolved)")
                
                if not dry_run:
                    # Import the improved function
                    from analytics.signals import update_daily_technical_summary_sync
                    
                    try:
                        success = update_daily_technical_summary_sync(
                            target_date.strftime('%Y-%m-%d'),
                            state_id=filter_params['state'].id if filter_params['state'] else None,
                            district_id=filter_params['business_district'].id if filter_params['business_district'] else None,
                            feeder_id=filter_params['feeder'].id if filter_params['feeder'] else None
                        )
                        
                        if success:
                            updated_count += 1
                    
                    except Exception as e:
                        self.stdout.write(f"  ❌ Error updating {target_date}: {e}")
                else:
                    updated_count += 1
        
        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(f"\n✅ Successfully updated {updated_count} daily summaries")
            )
        else:
            self.stdout.write(
                self.style.WARNING(f"\n[DRY RUN] Would have updated {updated_count} daily summaries")
            )