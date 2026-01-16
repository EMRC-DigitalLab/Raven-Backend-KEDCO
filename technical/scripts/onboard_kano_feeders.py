# onboard_kano_feeders.py - Run this as Django management command or standalone script
import os
import sys
from pathlib import Path
import django

# Add project root to path (3 levels up from this script: technical/scripts/onboard_kano_feeders.py)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

# Setup Django (if running as standalone script)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'raven.settings')
django.setup()

from common.models import Feeder

# Kano injection stations
KANO_STATIONS = [
    'IDH', 'MARIRI', 'RADIO HOUSE', 'WUDIL', 'ABATTOIR', 
    'FARM CENTRE', 'DAN AGUNDI', 'GONGONI', 'ZARIA ROAD', 
    'PRP', 'SMALL SCALE', 'NAIBAWA', 'JOGANA', 'CLUB', 
    'BATA', 'BRISCOE', 'KAWAJI'
]

def onboard_kano_feeders():
    """Set is_onboarded=True for all feeders in Kano stations"""
    
    print(f"🔍 Looking for feeders in {len(KANO_STATIONS)} Kano stations...")
    
    # Get all feeders whose substation name is in the list
    feeders = Feeder.objects.filter(
        substation__name__in=KANO_STATIONS
    )
    
    count = feeders.count()
    print(f"📊 Found {count} feeders to onboard")
    
    if count == 0:
        print("⚠️  No feeders found for these stations!")
        print("   Checking available station names...")
        from common.models import InjectionSubstation
        stations = InjectionSubstation.objects.all().values_list('name', flat=True)
        print(f"   Available stations: {list(stations)}")
        return
    
    # Show breakdown by station
    print("\n📋 Feeders by station:")
    for station in KANO_STATIONS:
        station_feeders = feeders.filter(substation__name=station)
        if station_feeders.exists():
            print(f"   {station}: {station_feeders.count()} feeders")
    
    # Update all to onboarded
    updated = feeders.update(is_onboarded=True)
    
    print(f"\n✅ Successfully onboarded {updated} Kano feeders!")
    
    # Verify
    onboarded_count = Feeder.objects.filter(
        substation__name__in=KANO_STATIONS,
        is_onboarded=True
    ).count()
    
    print(f"🎯 Verification: {onboarded_count} feeders now have is_onboarded=True")

if __name__ == '__main__':
    onboard_kano_feeders()