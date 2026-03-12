from datetime import date, timedelta

from dateutil.relativedelta import relativedelta  # type: ignore
from django.core.management.base import BaseCommand

from financial.models import SalaryPayment
from hr.models import Staff


def last_day_of_month(any_day):
    """
    Given a date, return the last calendar day of that same month.
    """
    next_month = any_day.replace(day=28) + timedelta(days=4)
    return next_month - timedelta(days=next_month.day)

class Command(BaseCommand):
    help = (
        "Populate SalaryPayment entries for every month "
        "from Jan 2022 through Jul 2025 based on each Staff's "
        "hire_date/exit_date and salary."
    )

    def handle(self, *args, **options):
        start_month = date(2022, 1, 1)
        end_month   = date(2025, 7, 1)

        current = start_month
        created = 0
        skipped = 0

        while current <= end_month:
            for staff in Staff.objects.all():
                hire = staff.hire_date        # DateField
                exit = staff.exit_date        # DateField or None

                # Skip if hired after this month
                if hire > current:
                    skipped += 1
                    continue

                # Skip if exited before this month
                if exit is not None and exit < current:
                    skipped += 1
                    continue

                # Determine payment_date = last day of 'current' month
                payment_dt = last_day_of_month(current)

                # District is required on SalaryPayment; staff.district per your model
                district = staff.district
                if district is None:
                    # Skip any staff without an assigned district
                    skipped += 1
                    continue

                # Create or skip if already exists
                obj, was_created = SalaryPayment.objects.get_or_create(
                    district=district,
                    month=current,
                    staff=staff,
                    defaults={
                        "payment_date": payment_dt,
                        "amount": staff.salary,
                    }
                )
                if was_created:
                    created += 1
                else:
                    skipped += 1

            # Next month
            current += relativedelta(months=1)

        self.stdout.write(
            self.style.SUCCESS(
                f"✓ Created {created} payments; skipped {skipped} entries."
            )
        )
