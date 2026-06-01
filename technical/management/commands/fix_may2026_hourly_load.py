"""
Fix May 2026 hourly load values that are clearly out of scale.

Correction rules (applied in priority order, highest first):
  load_mw > 1000  →  divide by 1000
  load_mw > 100   →  divide by 100
  load_mw > 10    →  divide by 10

Usage:
    python manage.py fix_may2026_hourly_load            # dry run — shows what would change
    python manage.py fix_may2026_hourly_load --apply    # writes corrections to the database
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from technical.models import HourlyLoad


MONTH_START = "2026-05-01"
MONTH_END = "2026-05-31"


def corrected_value(load_mw):
    if load_mw > 1000:
        return load_mw / Decimal("1000")
    if load_mw > 100:
        return load_mw / Decimal("100")
    if load_mw > 10:
        return load_mw / Decimal("10")
    return None  # no correction needed


class Command(BaseCommand):
    help = "Fix out-of-scale load_mw values in technical_hourlyload for May 2026"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            default=False,
            help="Actually write corrections to the database (default is dry run)",
        )

    def handle(self, *args, **options):
        apply = options["apply"]

        qs = HourlyLoad.objects.filter(
            date__gte=MONTH_START,
            date__lte=MONTH_END,
            load_mw__gt=10,
        ).select_related("feeder").order_by("date", "hour")

        if not qs.exists():
            self.stdout.write(self.style.SUCCESS("No values > 10 found for May 2026. Nothing to do."))
            return

        self.stdout.write(f"\n{'DRY RUN' if not apply else 'APPLYING CORRECTIONS'} — May 2026 hourly load\n")
        self.stdout.write(f"{'Feeder':<40} {'Date':<12} {'Hr':>3}  {'Current':>12}  {'Corrected':>12}  {'Rule'}")
        self.stdout.write("-" * 100)

        buckets = {">1000 (/1000)": [], ">100 (/100)": [], ">10 (/10)": []}
        to_update = []

        for record in qs:
            new_val = corrected_value(record.load_mw)
            if new_val is None:
                continue

            if record.load_mw > 1000:
                rule = ">1000 (/1000)"
            elif record.load_mw > 100:
                rule = ">100 (/100)"
            else:
                rule = ">10 (/10)"

            buckets[rule].append(record)
            to_update.append((record, new_val, rule))

            feeder_name = record.feeder.name if record.feeder else "N/A"
            self.stdout.write(
                f"{feeder_name:<40} {str(record.date):<12} {record.hour:>3}  "
                f"{record.load_mw:>12.4f}  {new_val:>12.4f}  {rule}"
            )

        self.stdout.write("-" * 100)
        self.stdout.write(
            f"\nSummary: {len(to_update)} records need correction  "
            f"({len(buckets['>1000 (/1000)'])} >1000, "
            f"{len(buckets['>100 (/100)'])} >100, "
            f"{len(buckets['>10 (/10)'])} >10)\n"
        )

        if not apply:
            self.stdout.write(self.style.WARNING(
                "DRY RUN complete. Review the values above, then run with --apply to save changes."
            ))
            return

        # --- Apply ---
        with transaction.atomic():
            updated = 0
            for record, new_val, _ in to_update:
                record.load_mw = new_val
                record.save(update_fields=["load_mw"])
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"Done. {updated} records updated in technical_hourlyload."))