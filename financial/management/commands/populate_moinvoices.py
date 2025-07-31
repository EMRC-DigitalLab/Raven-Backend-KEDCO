# backend/financial/management/commands/populate_moinvoices.py

from django.core.management.base import BaseCommand
from datetime import date
from decimal import Decimal

from financial.models import MOInvoice

# Your original data (in billions of Naira)
INVOICE_DATA = {
    2022: {
        "Jan": "1.01", "Feb": "1.00", "Mar": "0.91", "Apr": "0.89",
        "May": "0.72", "Jun": "0.81", "Jul": "0.84", "Aug": "1.00",
        "Sep": "0.89", "Oct": "1.01", "Nov": "1.03", "Dec": "1.01",
    },
    2023: {
        "Jan": "1.04", "Feb": "0.94", "Mar": "1.08", "Apr": "0.91",
        "May": "0.81", "Jun": "0.71", "Jul": "0.78", "Aug": "0.82",
        "Sep": "0.89", "Oct": "0.53", "Nov": "0.95", "Dec": "1.04",
    },
    2024: {
        "Jan": "0.99", "Feb": "0.87", "Mar": "1.16", "Apr": "1.19",
        "May": "1.44", "Jun": "0.87", "Jul": "1.26", "Aug": "1.39",
        "Sep": "1.03", "Dec": "0.58"
    },
    2025: {
         "Feb": "0.90", "Mar": "1.41"
    },
}

MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3,  "Apr": 4,
    "May": 5, "Jun": 6, "Jul": 7,  "Aug": 8,
    "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# Multiplier to convert billions → Naira
BILLION = Decimal("1000000000")


class Command(BaseCommand):
    help = "Populate MOInvoice table for 2022–2023, converting billions to Naira and marking all as paid."

    def handle(self, *args, **options):
        created, updated, skipped = 0, 0, 0

        for year, months in INVOICE_DATA.items():
            for mon_abbr, amt_str in months.items():
                m = MONTH_MAP[mon_abbr]
                invoice_date = date(year, m, 1)

                # Convert from billions to full Naira
                amount_naira = (Decimal(amt_str) * BILLION).quantize(Decimal("1.00"))

                obj, was_created = MOInvoice.objects.get_or_create(
                    month=invoice_date,
                    defaults={"amount": amount_naira, "is_paid": True}
                )

                if was_created:
                    created += 1
                    self.stdout.write(
                        f"  • Created {invoice_date:%Y-%b}: ₦{amount_naira:,} (paid)"
                    )
                else:
                    changed = False
                    if obj.amount != amount_naira:
                        obj.amount = amount_naira
                        changed = True
                    if not obj.is_paid:
                        obj.is_paid = True
                        changed = True
                    if changed:
                        obj.save(update_fields=["amount", "is_paid"])
                        updated += 1
                        self.stdout.write(
                            f"  ↻ Updated {invoice_date:%Y-%b}: set amount=₦{amount_naira:,}, is_paid=True"
                        )
                    else:
                        skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f"✔ Done: {created} created, {updated} updated, {skipped} skipped."
        ))
