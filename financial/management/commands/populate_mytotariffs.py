from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand

from common.models import Band
from financial.models import MYTOTariff

YEARS = [2022, 2023]
DEFAULT_RATE = Decimal("60.00")  # ₦60 per kWh

class Command(BaseCommand):
    help = (
        "Populate MYTOTariff with a default ₦60/kWh for all bands "
        "effective Jan 1 of 2022 and 2023."
    )

    def handle(self, *args, **options):
        created = 0
        updated = 0
        skipped = 0

        for year in YEARS:
            eff_date = date(year, 1, 1)
            for band in Band.objects.all():
                obj, was_created = MYTOTariff.objects.get_or_create(
                    band=band,
                    effective_date=eff_date,
                    defaults={"rate_per_kwh": DEFAULT_RATE},
                )

                if was_created:
                    created += 1
                    self.stdout.write(
                        f"  • Created {band.name} @ {eff_date:%Y-%m-%d}: ₦{DEFAULT_RATE}/kWh"
                    )
                else:
                    if obj.rate_per_kwh != DEFAULT_RATE:
                        obj.rate_per_kwh = DEFAULT_RATE
                        obj.save(update_fields=["rate_per_kwh"])
                        updated += 1
                        self.stdout.write(
                            f"  ↻ Updated {band.name} @ {eff_date:%Y-%m-%d} → ₦{DEFAULT_RATE}/kWh"
                        )
                    else:
                        skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f"✔ Done: {created} created, {updated} updated, {skipped} skipped."
        ))
