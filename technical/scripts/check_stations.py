import sys
from pathlib import Path
import os
import django

# Fix path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'raven.settings')
django.setup()

from common.models import InjectionSubstation

TARGETS = ['BICHI', 'DAN AGUNDI', 'WUDIL', 'BOKAVU', 'ABATTOIR', 'ADO BAYERO', 'BUK', 'CHALAWA']

print("Checking targets:")
found = []
all_stations = list(InjectionSubstation.objects.values_list('name', flat=True))

for t in TARGETS:
    if t in all_stations:
        print(f"✅ Found exact: {t}")
        found.append(t)
    else:
        print(f"❌ Not found: {t}")
        # Fuzzy check
        matches = [s for s in all_stations if t.lower() in s.lower()]
        if matches:
            print(f"   Possible matches: {matches}")

print("\n--- All Stations ---")
# print(all_stations)
