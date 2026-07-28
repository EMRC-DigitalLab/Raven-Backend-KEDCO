from django.core.management.base import BaseCommand
import re, openpyxl
from common.models import Feeder
from tmo.models import TMOFeederSupplyTarget


def normalise(name):
    return re.sub(r'\s+', ' ', name.strip().upper())

def strip_prefix(name):
    name = normalise(name)
    for prefix in ('33KV ', '11KV ', '33 KV ', '11 KV '):
        if name.startswith(prefix):
            return name[len(prefix):].strip()
    return name

ALIASES = {
    'NB CEREMIC': 'NB CERAMIC',
    'RUMMAWA': 'RUMAWA',
    'CHALLAWA WATER PLANT': 'CHALLAWA WATER WORKS',
    'BATA': 'SHARADA BATA',
}


class Command(BaseCommand):
    help = 'Load Feeders TARGET.xlsx into TMOFeederSupplyTarget for a given month'

    def add_arguments(self, parser):
        parser.add_argument('--month', type=str, required=True, help='YYYY-MM')

    def handle(self, *args, **options):
        EXCEL = r'C:\Users\TriumphAdeniran\Downloads\Feeders TARGET.xlsx'

        try:
            year, month = (int(x) for x in options['month'].split('-'))
        except ValueError:
            self.stderr.write('--month must be YYYY-MM')
            return

        feeder_lookup = {normalise(f.name): f for f in Feeder.objects.all()}

        wb = openpyxl.load_workbook(EXCEL, data_only=True)
        ws = wb.active

        created = updated = unmatched_count = 0
        unmatched = []

        for row in ws.iter_rows(min_row=2, values_only=True):
            raw_name, raw_target = row[0], row[1]
            if not raw_name or raw_target is None:
                continue
            stripped = strip_prefix(str(raw_name))
            feeder = feeder_lookup.get(stripped)
            if feeder is None:
                feeder = feeder_lookup.get(ALIASES.get(stripped, ''))
            if feeder is None:
                for suffix in (' AUTORECLOSER', ' AUTO RECLOSER', ' RECLOSER', ' AR'):
                    if stripped.endswith(suffix):
                        feeder = feeder_lookup.get(stripped[:-len(suffix)].strip())
                        if feeder:
                            break
            if feeder is None:
                unmatched.append(str(raw_name))
                unmatched_count += 1
                continue

            try:
                target_hours = round(float(raw_target), 2)
            except (TypeError, ValueError):
                continue

            _, was_created = TMOFeederSupplyTarget.objects.update_or_create(
                feeder=feeder, year=year, month=month,
                defaults={'target_hours': target_hours},
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Done: {created} created | {updated} updated | {unmatched_count} unmatched'
        ))
        if unmatched:
            self.stdout.write(self.style.WARNING('Unmatched: ' + ', '.join(unmatched)))

        total = TMOFeederSupplyTarget.objects.filter(year=year, month=month).count()
        self.stdout.write('Total in DB for ' + options['month'] + ': ' + str(total))
