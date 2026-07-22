"""
Diagnostic: why MDNI shows 0 for July 2026 in the commercial overview.

Usage:
  python manage.py check_datanest_alignment
"""
from django.core.management.base import BaseCommand
from django.db import connections

from commercial.models import CommercialCustomer, MeterReading


class Command(BaseCommand):
    help = 'Debug: MDNI July 2026 zero values'

    def handle(self, *args, **options):
        cursor = connections['external'].cursor()

        # ── 1. MDNI customers in Raven ────────────────────────────────────────
        self.stdout.write('\n=== MDNI Customers in Raven ===')
        mdni_customers = CommercialCustomer.objects.filter(customer_type='MDNI')
        self.stdout.write(f'Total MDNI customers: {mdni_customers.count()}')

        from django.db.models import Count
        by_feeder = (
            mdni_customers
            .values('feeder__slug', 'feeder__is_onboarded')
            .annotate(cnt=Count('id'))
            .order_by('feeder__slug')
        )
        self.stdout.write('By feeder:')
        for row in by_feeder:
            self.stdout.write(
                f'  {row["feeder__slug"]}  onboarded={row["feeder__is_onboarded"]}  customers={row["cnt"]}'
            )

        # ── 2. MDNI readings in Raven — all time ──────────────────────────────
        self.stdout.write('\n=== MDNI Readings in Raven (all time) ===')
        self.stdout.write('By reading_type field:')
        from django.db.models import Min, Max
        mdni_by_type = (
            MeterReading.objects
            .filter(reading_type='MDNI')
            .aggregate(cnt=Count('id'), earliest=Min('reading_date'), latest=Max('reading_date'))
        )
        self.stdout.write(
            f'  reading_type=MDNI: {mdni_by_type["cnt"]} readings  '
            f'({mdni_by_type["earliest"]} to {mdni_by_type["latest"]})'
        )

        self.stdout.write('By customer__customer_type:')
        mdni_by_cust = (
            MeterReading.objects
            .filter(customer__customer_type='MDNI')
            .aggregate(cnt=Count('id'), earliest=Min('reading_date'), latest=Max('reading_date'))
        )
        self.stdout.write(
            f'  customer_type=MDNI: {mdni_by_cust["cnt"]} readings  '
            f'({mdni_by_cust["earliest"]} to {mdni_by_cust["latest"]})'
        )

        # ── 3. MDNI readings in July 2026 ─────────────────────────────────────
        self.stdout.write('\n=== MDNI Readings in July 2026 ===')
        import datetime
        jul_start = datetime.date(2026, 7, 1)
        jul_end   = datetime.date(2026, 7, 21)

        july_mdni_type = MeterReading.objects.filter(
            reading_type='MDNI',
            reading_date__range=(jul_start, jul_end)
        ).count()
        july_mdni_cust = MeterReading.objects.filter(
            customer__customer_type='MDNI',
            reading_date__range=(jul_start, jul_end)
        ).count()
        self.stdout.write(f'reading_type=MDNI  in July: {july_mdni_type}')
        self.stdout.write(f'customer_type=MDNI in July: {july_mdni_cust}')

        # ── 4. What reading_types exist in DataNest meter_readings? ───────────
        self.stdout.write('\n=== DataNest reading_type breakdown ===')
        cursor.execute(
            'SELECT COALESCE(reading_type, "NULL"), COUNT(*) '
            'FROM meter_readings GROUP BY reading_type ORDER BY reading_type'
        )
        for rtype, cnt in cursor.fetchall():
            self.stdout.write(f'  {rtype}: {cnt}')

        # ── 5. DataNest MDNI readings in July 2026 ────────────────────────────
        self.stdout.write('\n=== DataNest MDNI readings in July 2026 ===')
        cursor.execute(
            'SELECT COUNT(*) FROM meter_readings mr '
            'JOIN customer_information ci ON mr.customer_id = ci.customer_id '
            'WHERE ci.customer_type = "MDNI" '
            'AND mr.reading_date BETWEEN "2026-07-01" AND "2026-07-21"'
        )
        self.stdout.write(f'MDNI readings (by customer_type) in July: {cursor.fetchone()[0]}')

        # ── 6. All readings in July 2026 in Raven ─────────────────────────────
        self.stdout.write('\n=== All Readings in July 2026 (Raven) ===')
        all_july = MeterReading.objects.filter(reading_date__range=(jul_start, jul_end))
        self.stdout.write(f'Total: {all_july.count()}')
        by_type = (
            all_july
            .values('reading_type')
            .annotate(cnt=Count('id'))
            .order_by('reading_type')
        )
        for row in by_type:
            self.stdout.write(f'  reading_type={row["reading_type"]}: {row["cnt"]}')

        self.stdout.write(self.style.SUCCESS('\n=== Diagnostic complete ==='))
