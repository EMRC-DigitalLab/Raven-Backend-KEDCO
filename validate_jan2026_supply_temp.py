import os
import sys
from datetime import datetime
from decimal import Decimal

import django
from django.conf import settings
from django.db.models import Avg, Count, Sum

# Setup Django environment
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'raven.settings')
django.setup()

from analytics.utils.technical_calculations import TechnicalCalculator
from technical.models import DailyHoursOfSupply, FeederInterruption, HourlyLoad


def validate_jan2026_supply():
    print("--- Starting Validation for January 2026 ---")
    
    # Target Month: January 2026
    start_date = datetime(2026, 1, 1).date()
    end_date = datetime(2026, 1, 31).date()
    month_date = datetime(2026, 1, 1).date()
    
    print(f"Target Period: {start_date} to {end_date}")
    
    # 1. Check Raw Data Availability
    daily_supply_count = DailyHoursOfSupply.objects.filter(
        date__range=(start_date, end_date)
    ).count()
    
    hourly_load_count = HourlyLoad.objects.filter(
        date__range=(start_date, end_date)
    ).count()
    
    print(f"\n[Data Availability]")
    print(f"DailyHoursOfSupply records found: {daily_supply_count}")
    print(f"HourlyLoad records found: {hourly_load_count}")
    
    if daily_supply_count == 0 and hourly_load_count == 0:
        print("CRITICAL: No data found for Jan 2026. Calculation cannot proceed.")
        # Try checking for any data in 2026 to see if dates are shifted
        any_2026 = DailyHoursOfSupply.objects.filter(date__year=2026).count()
        print(f"Total DailyHoursOfSupply in 2026: {any_2026}")
        return

    # 2. Use TechnicalCalculator to get the 'official' metric
    print(f"\n[Official Calculation via TechnicalCalculator]")
    try:
        calc = TechnicalCalculator(month_date=month_date)
        metrics = calc.calculate_supply_hours()
        
        print(f"Official Avg Hours of Supply: {metrics['avg_hours_of_supply']}")
        print(f"Official Total Supply Hours:  {metrics['total_supply_hours']}")
    except Exception as e:
        print(f"Error running calculation: {e}")
    
    # 3. Manual Verification / Breakdown
    print(f"\n[Manual Verification Breakdown]")
    
    # Method 1 Logic: DailyHoursOfSupply
    if daily_supply_count > 0:
        print("Method 1 (DailyHoursOfSupply) is active.")
        qs = DailyHoursOfSupply.objects.filter(date__range=(start_date, end_date))
        
        avg_val = qs.aggregate(avg=Avg('hours_supplied'))['avg']
        sum_val = qs.aggregate(total=Sum('hours_supplied'))['total']
        
        print(f"  -> DB Avg: {avg_val}")
        print(f"  -> DB Sum: {sum_val}")
        
    else:
        print("Method 1 (DailyHoursOfSupply) is inactive (no data).")
        
    # Method 2 Logic: HourlyLoad
    if hourly_load_count > 0:
        print("Method 2 (HourlyLoad) fallback check.")
        
        # This matches the logic in TechnicalCalculator.calculate_supply_hours Method 2
        hourly_data = HourlyLoad.objects.filter(
            date__range=(start_date, end_date),
            load_mw__gt=0
        ).values('feeder', 'date').annotate(
            daily_hours=Count('hour')
        )
        
        if hourly_data.exists():
            avg_load_hours = hourly_data.aggregate(avg=Avg('daily_hours'))['avg']
            total_load_hours = hourly_data.aggregate(total=Sum('daily_hours'))['total']
            print(f"  -> Derived from Load Avg: {avg_load_hours}")
            print(f"  -> Derived from Load Sum: {total_load_hours}")
            print(f"  -> Count of Feeder-Days with Load > 0: {hourly_data.count()}")
        else:
            print("  -> No load > 0 found.")
            
    print("\n--- Validation Complete ---")

if __name__ == '__main__':
    # Redirect stdout to a file
    with open('validation_result.txt', 'w', encoding='utf-8') as f:
        original_stdout = sys.stdout
        sys.stdout = f
        try:
            validate_jan2026_supply()
        finally:
            sys.stdout = original_stdout
