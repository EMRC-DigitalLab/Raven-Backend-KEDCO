"""
Revert the MDI feeders that were incorrectly added by fix_commercial_onboarding.
These feeders were NOT in the original 7 that had commercial_is_onboarded=True.

Usage:
  python manage.py revert_added_mdi_feeders          # dry run
  python manage.py revert_added_mdi_feeders --apply
"""
from django.core.management.base import BaseCommand

from common.models import Feeder

# Exact slugs added by fix_commercial_onboarding that should NOT have been onboarded
ADDED_MDI_SLUGS = [
    'JG-GAG-RIC',
    'JG-HAD-HAD',
    'KN-BOK-DAW',
    'KN-DAK-FLO',
    'KN-DAK-GAS',
    'KN-DAK-JOD',
    'KN-DAK-RUM',
    'KN-DAU-RIJ',
    'KN-GON-DAL',
    'KN-JOG-TOK',
    'KN-KUM-DAN',
    'KN-KUM-MAM',
    'KN-KUM-SPA',
    'KN-KUM-SPA2',
    'KN-SMA-RAN',
    'KN-TAM-DRJ',
    'KN-TAM-RIC',
    'KN-TEX-FUN',
    'KS-KAT-NA',
]


class Command(BaseCommand):
    help = 'Revert wrongly-added MDI feeders back to commercial_is_onboarded=False'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', default=False)

    def handle(self, *args, **options):
        apply = options['apply']

        to_revert = Feeder.objects.filter(slug__in=ADDED_MDI_SLUGS, commercial_is_onboarded=True)
        self.stdout.write(f'\nFeeders to revert: {to_revert.count()}')
        for f in to_revert.order_by('slug'):
            self.stdout.write(f'  {f.slug}  commercial_is_onboarded={f.commercial_is_onboarded}')

        if not apply:
            self.stdout.write(
                self.style.WARNING(
                    f'\nDry run — {to_revert.count()} feeders would be reverted. '
                    'Run with --apply to apply.'
                )
            )
            return

        reverted = to_revert.update(commercial_is_onboarded=False, commercial_onboarded_at=None)
        self.stdout.write(self.style.SUCCESS(f'\nReverted: {reverted} feeders'))

        self.stdout.write(
            f'\nFinal commercial_is_onboarded=True count: '
            f'{Feeder.objects.filter(commercial_is_onboarded=True).count()}'
        )
        self.stdout.write('\nRemaining commercially onboarded feeders:')
        for f in Feeder.objects.filter(commercial_is_onboarded=True).order_by('slug'):
            self.stdout.write(f'  {f.slug}')
