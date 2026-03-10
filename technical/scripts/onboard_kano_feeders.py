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

import csv

def onboard_kano_feeders():
    """
    Syncs is_onboarded status based on kn_feeders_with_names.csv.
    1. Onboard feeders in the CSV.
    2. Disonboard feeders NOT in the CSV (if they were previously onboarded).
    3. Report missing feeders.
    """
    csv_path = Path(__file__).parent / 'kn_feeders_with_names.csv'
    
    if not csv_path.exists():
        print(f"❌ Error: Could not find {csv_path}")
        return

    print(f"📂 Reading feeders from {csv_path.name}...")
    
    target_names = set()
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Use 'Feeder Name' as the key since 'Feeder ID' is not in our model
            name = row.get('Feeder Name', '').strip()
            if name:
                target_names.add(name)
    
    print(f"📋 Found {len(target_names)} unique feeder names in CSV.")
    
    # 1. Onboard target feeders
    # We filter by name. Note: This assumes names are unique enough or we want ALL feeders with these names.
    existing_feeders = Feeder.objects.filter(name__in=target_names)
    existing_names = set(existing_feeders.values_list('name', flat=True))
    
    # Update to True
    updated_count = existing_feeders.update(is_onboarded=True)
    print(f"✅ Onboarded/Updated {updated_count} feeders matching names in the list.")
    
    # Check for missing
    missing_names = target_names - existing_names
    if missing_names:
        print(f"\n⚠️  {len(missing_names)} Feeder names in CSV but NOT found in DB (Skipped):")
        for mname in sorted(missing_names):
            print(f"   - {mname}")
    
    # 2. Disonboard others
    # Disonboard feeders that are currently onboarded BUT their name is NOT in our target list.
    # WARNING: This affects ALL feeders in the database.
    to_disonboard = Feeder.objects.filter(is_onboarded=True).exclude(name__in=target_names)
    disonboard_count = to_disonboard.count()
    
    if disonboard_count > 0:
        print(f"\n📉 Dis-onboarding {disonboard_count} feeders (names not in CSV list)...")
        # List a few for visibility
        # for f in to_disonboard[:5]:
        #     print(f"   - {f.name}")
        to_disonboard.update(is_onboarded=False)
        print(f"   Done.")
    else:
        print("\n✨ No feeders needed to be dis-onboarded.")

    # Final Verification
    total_onboarded = Feeder.objects.filter(is_onboarded=True).count()
    print(f"\n🎯 Final DB Verification: {total_onboarded} total feeders are now is_onboarded=True.")

if __name__ == '__main__':
    onboard_kano_feeders()