# tmo/management/commands/seed_daily_forecast.py
"""
Seeds the standard per-day energy forecast (GWh) into TMODailyAllocation
for a given month.

These are the baseline daily forecast values used by KEDCO TMO:
  Days  1–2 : 5.00 GWh
  Days  3–4 : 4.30 GWh
  Days  5–6 : 4.50 GWh
  Days  7–9 : 4.80 GWh
  Day  10   : 4.50 GWh
  Day  11   : 4.30 GWh
  Days 12–13: 4.50 GWh
  Days 14–18: 5.00 GWh
  Days 19–20: 4.70 GWh
  Days 21–22: 6.00 GWh
  Day  23   : 6.20 GWh
  Days 24–26: 6.60 GWh
  Days 27–31: 6.70 GWh  (day 31 extrapolated from the 27–30 pattern)

Usage:
    python manage.py seed_daily_forecast --month 2026-07
    python manage.py seed_daily_forecast --month 2026-07 --dry-run
    python manage.py seed_daily_forecast --month 2026-07 --force
"""
import calendar
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from tmo.constants import STANDARD_DAILY_FORECAST_GWH as _DAILY_FORECAST_GWH
from tmo.models import TMODailyAllocation


class Command(BaseCommand):
    help = 'Seed standard per-day energy forecast into TMODailyAllocation for a month'

    def add_arguments(self, parser):
        parser.add_argument('--month', type=str, required=True, help='YYYY-MM')
        parser.add_argument('--force',   action='store_true',
                            help='Overwrite existing manual rows (DataNest rows are never touched)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print what would be written without saving')

    def handle(self, *args, **options):
        try:
            year, month = (int(x) for x in options['month'].split('-'))
            date(year, month, 1)
        except (ValueError, TypeError):
            raise CommandError('--month must be YYYY-MM, e.g. 2026-07')

        days_in_month = calendar.monthrange(year, month)[1]
        force   = options['force']
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN — nothing will be written'))

        # Pre-load existing rows for this month
        month_start = date(year, month, 1)
        month_end   = date(year, month, days_in_month)
        existing = {
            a.date.day: a
            for a in TMODailyAllocation.objects.filter(
                date__gte=month_start, date__lte=month_end
            )
        }

        created = updated = skipped = 0

        for day in range(1, days_in_month + 1):
            forecast_gwh = _DAILY_FORECAST_GWH.get(day, 6.70)
            expected_mw  = round(forecast_gwh * 1000.0 / 24.0, 2)
            target_date  = date(year, month, day)
            existing_row = existing.get(day)

            if existing_row:
                if existing_row.source == 'datanest':
                    # Never overwrite DataNest data
                    self.stdout.write(
                        f'  Day {day:02d}: SKIP (DataNest row — {float(existing_row.expected_mw)*24/1000:.2f} GWh)'
                    )
                    skipped += 1
                    continue
                if not force:
                    self.stdout.write(
                        f'  Day {day:02d}: SKIP (manual row exists — use --force to overwrite)'
                    )
                    skipped += 1
                    continue
                # Overwrite existing manual row
                if not dry_run:
                    existing_row.expected_mw = expected_mw
                    existing_row.source = 'manual'
                    existing_row.save(update_fields=['expected_mw', 'source', 'updated_at'])
                self.stdout.write(f'  Day {day:02d}: UPDATED → {forecast_gwh} GWh')
                updated += 1
            else:
                if not dry_run:
                    TMODailyAllocation.objects.create(
                        date=target_date,
                        expected_mw=expected_mw,
                        source='manual',
                    )
                self.stdout.write(f'  Day {day:02d}: CREATED → {forecast_gwh} GWh')
                created += 1

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Done: {created} created | {updated} updated | {skipped} skipped'
        ))
        if not dry_run and (created + updated) > 0:
            self.stdout.write(self.style.SUCCESS(
                f'Daily forecast is now set for {year}-{month:02d}. '
                f'The energy chart will use these values instead of the flat monthly default.'
            ))
