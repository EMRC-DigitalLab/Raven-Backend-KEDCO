"""
Revert commercial_is_onboarded=False for feeders that have MDNI customers,
while leaving MDI-only feeders untouched.

Only reverts feeders that were previously False (i.e. were changed by fix_commercial_onboarding).
Safe to re-run.

Usage:
  python manage.py revert_mdni_onboarding          # dry run
  python manage.py revert_mdni_onboarding --apply
"""
from django.core.management.base import BaseCommand
from django.db.models import Count

from commercial.models import CommercialCustomer
from common.models import Feeder


class Command(BaseCommand):
    help = 'Revert commercial_is_onboarded=False for MDNI feeders'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', default=False)

    def handle(self, *args, **options):
        apply = options['apply']

        # Feeders that are commercially onboarded AND have at least one MDNI customer
        mdni_feeder_ids = set(
            CommercialCustomer.objects
            .filter(customer_type='MDNI', feeder__commercial_is_onboarded=True)
            .values_list('feeder_id', flat=True)
            .distinct()
        )

        to_revert = Feeder.objects.filter(id__in=mdni_feeder_ids, commercial_is_onboarded=True)

        self.stdout.write(f'\nFeeders to revert (commercial_is_onboarded → False): {to_revert.count()}')
        for f in to_revert.order_by('slug'):
            mdi_ct  = CommercialCustomer.objects.filter(feeder=f, customer_type='MDI').count()
            mdni_ct = CommercialCustomer.objects.filter(feeder=f, customer_type='MDNI').count()
            self.stdout.write(f'  {f.slug:<22}  MDI={mdi_ct:<5}  MDNI={mdni_ct}')

        if not apply:
            self.stdout.write(
                self.style.WARNING(
                    f'\nDry run — {to_revert.count()} feeders would be reverted. '
                    'Run with --apply to apply.'
                )
            )
            return

        reverted = to_revert.update(commercial_is_onboarded=False)
        self.stdout.write(self.style.SUCCESS(f'\nReverted: {reverted} feeders set to commercial_is_onboarded=False'))

        self.stdout.write(f'\nPost-revert:')
        self.stdout.write(f'  commercial_is_onboarded=True: {Feeder.objects.filter(commercial_is_onboarded=True).count()} feeders')
        mdni_still = CommercialCustomer.objects.filter(
            customer_type='MDNI', feeder__commercial_is_onboarded=True
        ).count()
        self.stdout.write(f'  MDNI customers on commercial feeders: {mdni_still}')
