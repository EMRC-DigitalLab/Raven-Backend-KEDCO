# backend/financial/management/commands/populate_nbetinvoices.py

from django.core.management.base import BaseCommand
from datetime import date
from decimal import Decimal

from financial.models import NBETInvoice

# Your original data (in billions of Naira)
INVOICE_DATA = {
    2022: {
        "Jan": "5.47", "Feb": "5.00", "Mar": "4.55", "Apr": "4.77",
        "May": "4.40", "Jun": "4.04", "Jul": "4.39", "Aug": "4.72",
        "Sep": "4.56", "Oct": "5.06", "Nov": "5.21", "Dec": "5.41",
    },
    2023: {
        "Jan": "5.34", "Feb": "5.09", "Mar": "5.82", "Apr": "4.84",
        "May": "6.25", "Jun": "6.16", "Jul": "7.02", "Aug": "7.40",
        "Sep": "8.08", "Oct": "9.77", "Nov": "10.61", "Dec": "5.21",
    },

    2024: {
        "Jan": "0.79", "Feb": "0.69", "Mar": "0.76", "Apr": "6.11",
        "May": "7.37", "Jun": "6.82", "Jul": "8.33", "Aug": "7.62",
        "Sep": "4.12", "Oct": "2.33", "Nov": "4.74", "Dec": "5.91",
    },
    2025: {
        "Jan": "4.87", "Feb": "5.28", "Mar": "7.43",
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
    help = "Populate NBETInvoice table for 2022–2023, converting billions to Naira and marking all as paid."

    def handle(self, *args, **options):
        created = 0
        updated = 0
        skipped = 0

        for year, months in INVOICE_DATA.items():
            for mon_abbr, amt_str in months.items():
                m = MONTH_MAP[mon_abbr]
                invoice_date = date(year, m, 1)

                # Parse as Decimal and convert from billions to Naira
                amount_naira = (Decimal(amt_str) * BILLION).quantize(Decimal("1.00"))

                obj, was_created = NBETInvoice.objects.get_or_create(
                    month=invoice_date,
                    defaults={
                        "amount": amount_naira,
                        "is_paid": True,
                    }
                )

                if was_created:
                    created += 1
                    self.stdout.write(
                        f"  • Created {invoice_date:%Y-%b}: ₦{amount_naira:,} (paid)"
                    )
                else:
                    # Ensure amount & is_paid are current
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
