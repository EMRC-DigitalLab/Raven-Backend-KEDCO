from django.core.management.base import BaseCommand

from common.models import Band

BAND_CONFIG = [
    {'name': 'A', 'minimum_hours': 20, 'priority_order': 1},
    {'name': 'B', 'minimum_hours': 16, 'priority_order': 2},
    {'name': 'C', 'minimum_hours': 12, 'priority_order': 3},
    {'name': 'D', 'minimum_hours':  8, 'priority_order': 4},
    {'name': 'E', 'minimum_hours':  4, 'priority_order': 5},
]


class Command(BaseCommand):
    help = 'Seeds NERC minimum service hours and priority order onto existing Band records.'

    def handle(self, *args, **options):
        updated = 0
        skipped = 0

        for config in BAND_CONFIG:
            try:
                band = Band.objects.get(name=config['name'])
                band.minimum_hours = config['minimum_hours']
                band.priority_order = config['priority_order']
                band.save(update_fields=['minimum_hours', 'priority_order'])
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  Band {config['name']} — {config['minimum_hours']} hrs/day | priority {config['priority_order']}"
                    )
                )
                updated += 1
            except Band.DoesNotExist:
                self.stdout.write(
                    self.style.WARNING(f"  Band '{config['name']}' not found — skipped.")
                )
                skipped += 1

        self.stdout.write(self.style.SUCCESS(f"\nDone. {updated} bands updated, {skipped} skipped."))
