"""
Sync feeder metadata from DataNest staging to Raven internal DB.

What this does:
  1. Updates each Raven feeder's band, feeder_class, and voltage_level
     from DataNest staging (feeders table).
  2. Adds any feeders that exist in DataNest but are missing in Raven.
  3. Reports a full summary of what changed.

Usage:
  python manage.py sync_feeder_metadata
  python manage.py sync_feeder_metadata --dry-run
"""

from django.core.management.base import BaseCommand
from django.db import connections, transaction

from common.models import Band, BusinessDistrict, Feeder, InjectionSubstation


VOLTAGE_MAP = {
    '33KV': '33kv',
    '11KV': '11kv',
    '33kV': '33kv',
    '11kV': '11kv',
}

# feeder_class cleanup — NDM is a known typo of NMD in DataNest
CLASS_CLEANUP = {
    'NDM': 'NMD',
    'A': 'NMD',   # malformed entry, treat as NMD
}


class Command(BaseCommand):
    help = 'Sync feeder band, class, and voltage_level from DataNest staging to Raven'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would change without saving anything',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be saved\n'))

        # Load DataNest feeders
        cursor = connections['external'].cursor()
        cursor.execute("""
            SELECT feeder_id, feeder_name, feeder_type, service_band, feeder_class,
                   district_id, injection_station_id
            FROM feeders
            WHERE status = 'active'
        """)
        dn_feeders = {
            row[0]: {
                'name':       row[1],
                'ftype':      row[2],
                'sband':      row[3],
                'fclass':     row[4],
                'district_id': row[5],
                'is_id':      row[6],
            }
            for row in cursor.fetchall()
        }

        # Load Raven feeders
        raven_feeders = {f.slug: f for f in Feeder.objects.select_related('band').all()}

        # Load band lookup
        band_lookup = {b.name: b for b in Band.objects.all()}

        stats = {
            'band_updated': 0,
            'class_updated': 0,
            'voltage_updated': 0,
            'name_updated': 0,
            'added': 0,
            'skipped_missing_sub': 0,
            'skipped_bad_band': 0,
        }

        self.stdout.write('=== Updating existing feeders ===')

        for slug, dn in dn_feeders.items():
            if slug not in raven_feeders:
                continue

            feeder = raven_feeders[slug]
            changed = False

            # 1. Band
            sband = dn['sband']
            if sband and sband not in ('undefined',) and sband in band_lookup:
                new_band = band_lookup[sband]
                if feeder.band != new_band:
                    self.stdout.write(
                        f'  BAND  {slug}: {feeder.band} → {sband}'
                    )
                    if not dry_run:
                        feeder.band = new_band
                    stats['band_updated'] += 1
                    changed = True

            # 2. Feeder class
            raw_class = dn['fclass'] or ''
            clean_class = CLASS_CLEANUP.get(raw_class, raw_class)
            if feeder.feeder_class != clean_class and clean_class:
                self.stdout.write(
                    f'  CLASS {slug}: {feeder.feeder_class} → {clean_class}'
                )
                if not dry_run:
                    feeder.feeder_class = clean_class
                stats['class_updated'] += 1
                changed = True

            # 3. Voltage level
            raw_voltage = dn['ftype'] or ''
            new_voltage = VOLTAGE_MAP.get(raw_voltage, '')
            if new_voltage and feeder.voltage_level != new_voltage:
                self.stdout.write(
                    f'  VOLT  {slug}: {feeder.voltage_level} → {new_voltage}'
                )
                if not dry_run:
                    feeder.voltage_level = new_voltage
                stats['voltage_updated'] += 1
                changed = True

            # 4. Name
            if feeder.name != dn['name'] and dn['name']:
                self.stdout.write(
                    f'  NAME  {slug}: {feeder.name} → {dn["name"]}'
                )
                if not dry_run:
                    feeder.name = dn['name']
                stats['name_updated'] += 1
                changed = True

            if changed and not dry_run:
                feeder.save()

        # Add missing feeders
        self.stdout.write('\n=== Adding missing feeders ===')
        for slug, dn in dn_feeders.items():
            if slug in raven_feeders:
                continue

            substation = InjectionSubstation.objects.filter(slug=dn['is_id']).first()
            district = BusinessDistrict.objects.filter(slug=dn['district_id']).first()

            if not substation:
                self.stdout.write(
                    self.style.WARNING(
                        f'  SKIP  {slug} — substation {dn["is_id"]} not found in Raven'
                    )
                )
                stats['skipped_missing_sub'] += 1
                continue

            sband = dn['sband']
            band = band_lookup.get(sband) if sband and sband != 'undefined' else None
            if not band:
                self.stdout.write(
                    self.style.WARNING(
                        f'  SKIP  {slug} — service_band "{sband}" not valid'
                    )
                )
                stats['skipped_bad_band'] += 1
                continue

            voltage = VOLTAGE_MAP.get(dn['ftype'] or '', '11kv')
            clean_class = CLASS_CLEANUP.get(dn['fclass'] or '', dn['fclass'] or 'NMD')

            self.stdout.write(
                f'  ADD   {slug}: {dn["name"]} | band={sband} | {voltage} | class={clean_class}'
            )

            if not dry_run:
                with transaction.atomic():
                    Feeder.objects.create(
                        slug=slug,
                        name=dn['name'],
                        band=band,
                        voltage_level=voltage,
                        feeder_class=clean_class,
                        substation=substation,
                        business_district=district,
                        status='active',
                    )
            stats['added'] += 1

        # Summary
        self.stdout.write('\n' + '=' * 50)
        self.stdout.write(self.style.SUCCESS('SYNC SUMMARY'))
        self.stdout.write('=' * 50)
        self.stdout.write(f'  Bands updated:       {stats["band_updated"]}')
        self.stdout.write(f'  Classes updated:     {stats["class_updated"]}')
        self.stdout.write(f'  Voltage updated:     {stats["voltage_updated"]}')
        self.stdout.write(f'  Names updated:       {stats["name_updated"]}')
        self.stdout.write(f'  Feeders added:       {stats["added"]}')
        self.stdout.write(f'  Skipped (no sub):    {stats["skipped_missing_sub"]}')
        self.stdout.write(f'  Skipped (bad band):  {stats["skipped_bad_band"]}')

        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN — nothing was saved'))
        else:
            self.stdout.write(self.style.SUCCESS('\nDone.'))
