"""
Diagnose inflated billing for commercially onboarded feeders in July 2026.
Checks for multi-read customers and billing computation.

Usage:
  python manage.py check_billing_inflation
"""
import datetime
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db.models import Count, Max, Min, Avg, Sum

from commercial.models import CommercialCustomer, MeterReading
from common.models import Feeder


class Command(BaseCommand):
    help = 'Diagnose billing inflation in commercial overview'

    def handle(self, *args, **options):
        jul_start = datetime.date(2026, 7, 1)
        jul_end   = datetime.date(2026, 7, 21)

        comm_feeders = Feeder.objects.filter(commercial_is_onboarded=True)
        self.stdout.write(f'\nCommercially onboarded feeders: {comm_feeders.count()}')
        for f in comm_feeders.order_by('slug'):
            ccount = CommercialCustomer.objects.filter(feeder=f).count()
            self.stdout.write(f'  {f.slug}  customers={ccount}')

        # ── Customers and readings on commercial feeders ───────────────────────
        comm_customers = CommercialCustomer.objects.filter(feeder__commercial_is_onboarded=True)
        total_customers = comm_customers.count()
        self.stdout.write(f'\nTotal commercial customers: {total_customers}')

        july_readings = MeterReading.objects.filter(
            customer__feeder__commercial_is_onboarded=True,
            reading_date__range=(jul_start, jul_end),
            billed_consumption__isnull=False,
        )
        total_readings = july_readings.count()
        unique_customers_read = july_readings.values('customer_id').distinct().count()
        self.stdout.write(f'July readings: {total_readings}')
        self.stdout.write(f'Unique customers read in July: {unique_customers_read}')
        self.stdout.write(f'Readings per customer (avg): {total_readings / unique_customers_read:.2f}' if unique_customers_read else 'N/A')

        # ── Customers with multiple readings in July ───────────────────────────
        multi_read = (
            july_readings
            .values('customer_id')
            .annotate(read_count=Count('id'))
            .filter(read_count__gt=1)
            .order_by('-read_count')
        )
        self.stdout.write(f'\nCustomers with >1 reading in July: {multi_read.count()}')
        for row in multi_read[:10]:
            c = CommercialCustomer.objects.get(id=row['customer_id'])
            self.stdout.write(f'  {c.external_id}  feeder={c.feeder.slug if c.feeder else "?"}  reads={row["read_count"]}')

        # ── Sample reading data ────────────────────────────────────────────────
        self.stdout.write('\nSample readings (first 10):')
        for r in july_readings.select_related('customer', 'customer__feeder').order_by('customer_id', 'reading_date')[:10]:
            self.stdout.write(
                f'  cust={r.customer.external_id[:12]:<12}  '
                f'feeder={r.customer.feeder.slug if r.customer.feeder else "?"}  '
                f'date={r.reading_date}  '
                f'billed_consumption={r.billed_consumption}  '
                f'tariff_rate={r.tariff_rate}'
            )

        # ── Raw billing sum (no normalisation) ────────────────────────────────
        raw_sum = july_readings.aggregate(
            total_kwh=Sum('billed_consumption'),
            avg_kwh=Avg('billed_consumption'),
            total_ec=Sum('billed_consumption'),
            min_kwh=Min('billed_consumption'),
            max_kwh=Max('billed_consumption'),
            avg_rate=Avg('tariff_rate'),
            min_rate=Min('tariff_rate'),
            max_rate=Max('tariff_rate'),
        )
        self.stdout.write(f'\nRaw billed_consumption stats (July):')
        self.stdout.write(f'  total: {raw_sum["total_kwh"]}')
        self.stdout.write(f'  avg:   {raw_sum["avg_kwh"]}')
        self.stdout.write(f'  min:   {raw_sum["min_kwh"]}')
        self.stdout.write(f'  max:   {raw_sum["max_kwh"]}')
        self.stdout.write(f'  avg tariff_rate: {raw_sum["avg_rate"]}')
        self.stdout.write(f'  min tariff_rate: {raw_sum["min_rate"]}')
        self.stdout.write(f'  max tariff_rate: {raw_sum["max_rate"]}')

        # ── Check if first-sync customers are inflating ───────────────────────
        self.stdout.write('\nFirst reading dates (for commercial customers):')
        first_reads = (
            MeterReading.objects
            .filter(customer__feeder__commercial_is_onboarded=True)
            .values('customer_id')
            .annotate(first_read=Min('reading_date'), last_read=Max('reading_date'), cnt=Count('id'))
            .order_by('first_read')[:10]
        )
        for row in first_reads:
            self.stdout.write(
                f'  cust_id={str(row["customer_id"])[:8]}...  '
                f'first={row["first_read"]}  last={row["last_read"]}  total_reads={row["cnt"]}'
            )

        self.stdout.write(self.style.SUCCESS('\n=== Done ==='))
