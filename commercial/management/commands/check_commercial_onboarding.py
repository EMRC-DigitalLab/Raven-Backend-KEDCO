"""
Diagnostic: check commercial_is_onboarded status vs is_onboarded,
and show which feeders have MDNI customers.

Usage:
  python manage.py check_commercial_onboarding
"""
from django.core.management.base import BaseCommand
from django.db.models import Count

from commercial.models import CommercialCustomer
from common.models import Feeder


class Command(BaseCommand):
    help = 'Check commercial_is_onboarded status for MDNI feeders'

    def handle(self, *args, **options):
        self.stdout.write('\n=== Feeder Onboarding Status ===')
        self.stdout.write(f'Feeders with commercial_is_onboarded=True: {Feeder.objects.filter(commercial_is_onboarded=True).count()}')
        self.stdout.write(f'Feeders with is_onboarded=True:            {Feeder.objects.filter(is_onboarded=True).count()}')
        self.stdout.write(f'Total feeders:                             {Feeder.objects.count()}')

        self.stdout.write('\n=== MDNI Customer Distribution ===')
        mdni_comm  = CommercialCustomer.objects.filter(customer_type='MDNI', feeder__commercial_is_onboarded=True).count()
        mdni_total = CommercialCustomer.objects.filter(customer_type='MDNI').count()
        self.stdout.write(f'MDNI customers on commercially onboarded feeders: {mdni_comm}')
        self.stdout.write(f'MDNI customers total:                             {mdni_total}')

        self.stdout.write('\n=== MDNI by feeder ===')
        by_feeder = (
            CommercialCustomer.objects
            .filter(customer_type='MDNI')
            .values('feeder__slug', 'feeder__commercial_is_onboarded', 'feeder__is_onboarded')
            .annotate(cnt=Count('id'))
            .order_by('feeder__slug')
        )
        for row in by_feeder:
            mark = 'OK' if row['feeder__commercial_is_onboarded'] else 'MISSING'
            self.stdout.write(
                f'  [{mark}] {row["feeder__slug"]}  '
                f'comm={row["feeder__commercial_is_onboarded"]}  '
                f'tech={row["feeder__is_onboarded"]}  '
                f'customers={row["cnt"]}'
            )

        self.stdout.write('\n=== Feeders with is_onboarded=True but commercial_is_onboarded=False ===')
        gap = Feeder.objects.filter(is_onboarded=True, commercial_is_onboarded=False)
        self.stdout.write(f'Count: {gap.count()}')
        for f in gap.order_by('slug'):
            mdni_ct = CommercialCustomer.objects.filter(feeder=f, customer_type='MDNI').count()
            mdi_ct  = CommercialCustomer.objects.filter(feeder=f, customer_type='MDI').count()
            self.stdout.write(f'  {f.slug}  MDI={mdi_ct}  MDNI={mdni_ct}')

        self.stdout.write(self.style.SUCCESS('\n=== Done ==='))
