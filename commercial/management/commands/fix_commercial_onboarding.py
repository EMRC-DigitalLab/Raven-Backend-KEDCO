"""
Fix: set commercial_is_onboarded=True (and commercial_onboarded_at) for all
feeders that are technically onboarded (is_onboarded=True) but not yet
commercially onboarded, and that have CommercialCustomers assigned.

Usage:
  python manage.py fix_commercial_onboarding          # dry run
  python manage.py fix_commercial_onboarding --apply  # apply changes
"""
from django.core.management.base import BaseCommand
from commercial.models import CommercialCustomer
from common.models import Feeder


class Command(BaseCommand):
    help = 'Mark commercially-active feeders as commercial_is_onboarded=True'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            default=False,
            help='Actually apply the changes (default is dry-run only)',
        )

    def handle(self, *args, **options):
        apply = options['apply']

        # Feeders that are technically onboarded but not commercially onboarded
        gap_feeders = Feeder.objects.filter(
            is_onboarded=True,
            commercial_is_onboarded=False,
        )

        # Of those, only ones that actually have CommercialCustomers
        feeder_ids_with_customers = set(
            CommercialCustomer.objects
            .filter(feeder__in=gap_feeders)
            .values_list('feeder_id', flat=True)
            .distinct()
        )

        to_fix = [f for f in gap_feeders if f.id in feeder_ids_with_customers]

        self.stdout.write(f'\nFeeders with is_onboarded=True but commercial_is_onboarded=False: {gap_feeders.count()}')
        self.stdout.write(f'Of those, feeders with CommercialCustomers: {len(to_fix)}')

        if not to_fix:
            self.stdout.write(self.style.SUCCESS('Nothing to fix.'))
            return

        self.stdout.write('\nFeeders to be updated:')
        for f in sorted(to_fix, key=lambda x: x.slug):
            mdni_ct = CommercialCustomer.objects.filter(feeder=f, customer_type='MDNI').count()
            mdi_ct  = CommercialCustomer.objects.filter(feeder=f, customer_type='MDI').count()
            self.stdout.write(
                f'  {f.slug:<20}  MDI={mdi_ct:<5}  MDNI={mdni_ct:<5}  '
                f'onboarded_at={f.onboarded_at}'
            )

        if not apply:
            self.stdout.write(
                self.style.WARNING(
                    f'\nDry run — {len(to_fix)} feeders would be updated. '
                    'Run with --apply to apply changes.'
                )
            )
            return

        updated = 0
        for f in to_fix:
            f.commercial_is_onboarded = True
            # Use the same date as the technical onboarding date (onboarded_at is DateTimeField)
            if f.onboarded_at and not f.commercial_onboarded_at:
                oat = f.onboarded_at
                f.commercial_onboarded_at = oat.date() if hasattr(oat, 'date') else oat
            f.save(update_fields=['commercial_is_onboarded', 'commercial_onboarded_at'])
            updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'\nApplied: {updated} feeders now have commercial_is_onboarded=True'
            )
        )

        # Summary after fix
        self.stdout.write(f'\nPost-fix counts:')
        self.stdout.write(
            f'  Feeders with commercial_is_onboarded=True: '
            f'{Feeder.objects.filter(commercial_is_onboarded=True).count()}'
        )
        from commercial.models import MeterReading
        import datetime
        jul_start = datetime.date(2026, 7, 1)
        jul_end   = datetime.date(2026, 7, 21)
        mdni_july = MeterReading.objects.filter(
            reading_type='MDNI',
            reading_date__range=(jul_start, jul_end),
            customer__feeder__commercial_is_onboarded=True,
        ).count()
        self.stdout.write(f'  MDNI readings in July 2026 (commercial feeders): {mdni_july}')
