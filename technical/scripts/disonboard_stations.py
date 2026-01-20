import sys
from pathlib import Path
import os
import django

# Fix path to include project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'raven.settings')
django.setup()

from common.models import Feeder, InjectionSubstation

def disonboard_stations():
    TARGET_STATIONS = [
        'BICHI', 
        'DAN AGUNDI', 
        'WUDIL', 
        'BOKAVU', 
        'ABATTOIR', 
        'ADO BAYERO', 
        'BUK', 
        'CHALAWA'
    ]

    print(f"🛑 Preparing to dis-onboard feeders for {len(TARGET_STATIONS)} stations...")
    print(f"   Targets: {TARGET_STATIONS}")

    # 1. Verify stations exist
    existing_stations = InjectionSubstation.objects.filter(name__in=TARGET_STATIONS)
    found_names = set(existing_stations.values_list('name', flat=True))
    missing = set(TARGET_STATIONS) - found_names
    
    if missing:
        print(f"⚠️  Warning: Could not find these stations in DB: {missing}")
        print("   Proceeding with found stations only.")

    if not found_names:
        print("❌ No valid stations found. Aborting.")
        return

    # 2. Get feeders
    feeders_to_update = Feeder.objects.filter(
        substation__name__in=found_names,
        is_onboarded=True
    )
    
    count = feeders_to_update.count()
    print(f"📉 Found {count} ONBOARDED feeders in these stations.")

    if count == 0:
        print("✨ No feeders to dis-onboard.")
        return

    # 3. Update
    # Show breakdown
    for station in found_names:
        c = feeders_to_update.filter(substation__name=station).count()
        if c > 0:
            print(f"   - {station}: {c} feeders")

    updated = feeders_to_update.update(is_onboarded=False)
    print(f"\n✅ Successfully dis-onboarded {updated} feeders.")
    
    # Verify
    remaining = Feeder.objects.filter(
        substation__name__in=found_names,
        is_onboarded=True
    ).count()
    print(f"🎯 Verification: {remaining} feeders remain onboarded for these stations (Should be 0).")

if __name__ == '__main__':
    disonboard_stations()
