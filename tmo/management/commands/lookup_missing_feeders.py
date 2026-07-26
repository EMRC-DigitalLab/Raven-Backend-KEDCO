from django.core.management.base import BaseCommand
from common.models import Feeder

MISSING = [
    "Rice Field",
    "Challawa Water Plant",
    "Industrial Funtua",
    "Dawanau Industrial",
    "Dr. Jimeta",
    "Dantanoma",
    "NB-Ceramic",
    "Karaye-Falgore",
    "Dutse Government House",
]


class Command(BaseCommand):
    help = "Fuzzy lookup for missing segmentation feeders"

    def handle(self, *args, **options):
        all_feeders = list(Feeder.objects.values('id', 'name', 'voltage_level', 'pl_segment'))

        for missing in MISSING:
            self.stdout.write(f"\n=== '{missing}' ===")
            words = [w.strip().lower() for w in missing.replace('-', ' ').replace('.', ' ').split() if len(w.strip()) >= 2]
            hits = []
            for f in all_feeders:
                fname = f['name'].lower()
                score = sum(1 for w in words if w in fname)
                if score >= 1:
                    hits.append((score, f['name'], f['voltage_level'], f['pl_segment'] or ''))
            hits.sort(key=lambda x: x[0], reverse=True)
            if hits:
                for score, name, vlt, seg in hits[:5]:
                    self.stdout.write(f"  [{score}/{len(words)}] {name!r} ({vlt}) pl_segment={seg}")
            else:
                self.stdout.write("  ** NO MATCH **")
