"""
Management command: import_feeder_segmentation

Reads Feeders Segmentation_030317.xlsx (3 columns: FEEDER | PL | Band) and
updates each Feeder record's pl_segment and band FK.

Excel feeder names are prefixed with '33KV ' or '11KV ' — stripped before matching.
PL values: MDI / MDNI / Region  →  stored as MDI / MDNI / Regions
Band values: A–E  →  matched to Band by name (single letter, case-insensitive)

Usage:
    python manage.py import_feeder_segmentation
    python manage.py import_feeder_segmentation --path /some/other/path.xlsx
    python manage.py import_feeder_segmentation --dry-run
"""

import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from common.models import Band, Feeder

DEFAULT_PATH = (
    r"C:\Users\TriumphAdeniran\Downloads\Feeders Segmentation_030317.xlsx"
)

# Excel PL → model pl_segment
PL_MAP = {
    'MDI':    'MDI',
    'MDNI':   'MDNI',
    'REGION': 'Regions',
}

# Feeder name overrides: excel name (upper, stripped) → DB name to search
NAME_OVERRIDES = {
    # 33KV
    'RUMMAWA':              'RUMAWA',
    "MAI'ADU'A":            'MAI ADUA',
    'MAIAIUA':              'MAI ADUA',
    'R/ZAKI':               'RIJIYAR ZAKI',
    'HON. ABUBAKAR KABIR':  'HON. ABUBAKAR',
    'RIJIYAR ZAKI':         'RIJIYAR ZAKI',
    # Newly resolved mismatches
    'RICE FIELD':           'FEEDER 3/RICE FIELD',
    'DAWANAU INDUSTRIAL':   'DAWANAU',
    'DR. JIMETA':           'DR JIMETA',
    'NB-CERAMIC':           'NB CERAMIC',
    'DUTSE GOVERNMENT HOUSE': 'GOVERNMENT HOUSE DUTSE',
    # 11KV
    'HASKE SOLAR':          'HASKE SOLAR',
    'CHALLAWA WATER PLANT': 'CHALAWA WATER PLANT',
    'INDUSTRIAL FUNTUA':    'INDUSTRIAL',
}

# One Excel row → multiple DB feeders (both get the same pl_segment + band)
MULTI_TARGETS = {
    'KARAYE-FALGORE': ['KARAYE', 'FALGORE'],
}


def _strip_voltage_prefix(name: str) -> str:
    """Remove leading '33KV ', '11KV ', '33 KV ', '11 KV ' (case-insensitive)."""
    return re.sub(r'^(33|11)\s*KV\s+', '', name, flags=re.IGNORECASE).strip()


def _normalise(name: str) -> str:
    return re.sub(r'\s+', ' ', name).upper().strip()


class Command(BaseCommand):
    help = "Import feeder P&L segmentation from Excel (pl_segment + band)"

    def add_arguments(self, parser):
        parser.add_argument('--path', default=DEFAULT_PATH, help='Path to segmentation Excel')
        parser.add_argument('--dry-run', action='store_true', help='Preview without saving')

    def handle(self, *args, **options):
        try:
            import pandas as pd
        except ImportError:
            raise CommandError("pandas is required: pip install pandas openpyxl")

        path = Path(options['path'])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        dry_run = options['dry_run']

        df = pd.read_excel(path, header=0, engine='openpyxl')
        df.columns = [str(c).strip() for c in df.columns]
        # Expect columns: FEEDER, PL, Band  (order may vary)
        col_feeder = next((c for c in df.columns if 'FEED' in c.upper()), df.columns[0])
        col_pl     = next((c for c in df.columns if c.upper() in ('PL', 'P&L', 'P/L')), df.columns[1])
        col_band   = next((c for c in df.columns if 'BAND' in c.upper()), df.columns[2])

        # Build band lookup: letter → Band object
        band_lookup = {b.name.upper().strip(): b for b in Band.objects.all()}

        # Build feeder lookup: normalised name → Feeder object (all feeders)
        feeder_lookup = {_normalise(f.name): f for f in Feeder.objects.select_related('band').all()}

        updated = 0
        skipped = 0
        not_found = []

        for _, row in df.iterrows():
            raw_name  = str(row[col_feeder]).strip()
            raw_pl    = str(row[col_pl]).strip()
            raw_band  = str(row[col_band]).strip()

            if not raw_name or raw_name.upper() == 'NAN':
                continue

            # Map PL
            pl_key = raw_pl.upper().replace(' ', '')
            # handle 'REGION' or 'REGIONS'
            if pl_key.startswith('REGION'):
                pl_key = 'REGION'
            pl_segment = PL_MAP.get(pl_key)
            if pl_segment is None:
                self.stdout.write(self.style.WARNING(f"  Unknown PL '{raw_pl}' for {raw_name} — skipped"))
                skipped += 1
                continue

            # Map Band
            band_letter = raw_band.upper().strip()
            band_obj = band_lookup.get(band_letter)
            if band_obj is None:
                self.stdout.write(self.style.WARNING(f"  Unknown band '{raw_band}' for {raw_name} — band not updated"))

            # Resolve feeder name
            clean_name = _strip_voltage_prefix(raw_name)
            norm_name  = _normalise(clean_name)

            # Multi-target: one Excel row maps to several DB feeders
            multi = MULTI_TARGETS.get(norm_name)
            if multi:
                for db_name in multi:
                    feeder_obj = feeder_lookup.get(_normalise(db_name))
                    if feeder_obj is None:
                        not_found.append(f"{db_name} (via {raw_name}, {pl_segment})")
                        skipped += 1
                        continue
                    changed = False
                    if feeder_obj.pl_segment != pl_segment:
                        feeder_obj.pl_segment = pl_segment
                        changed = True
                    if band_obj and feeder_obj.band_id != band_obj.pk:
                        feeder_obj.band = band_obj
                        changed = True
                    if changed:
                        if not dry_run:
                            feeder_obj.save(update_fields=['pl_segment', 'band'])
                        updated += 1
                        self.stdout.write(f"  {'[DRY] ' if dry_run else ''}Updated: {feeder_obj.name} → {pl_segment} / Band {raw_band}")
                    else:
                        skipped += 1
                continue

            # Single-target: normal 1:1 name resolution
            override = NAME_OVERRIDES.get(norm_name)
            lookup_name = _normalise(override) if override else norm_name
            feeder_obj = feeder_lookup.get(lookup_name)

            if feeder_obj is None:
                not_found.append(f"{raw_name} ({pl_segment})")
                skipped += 1
                continue

            changed = False
            if feeder_obj.pl_segment != pl_segment:
                feeder_obj.pl_segment = pl_segment
                changed = True
            if band_obj and feeder_obj.band_id != band_obj.pk:
                feeder_obj.band = band_obj
                changed = True

            if changed:
                if not dry_run:
                    feeder_obj.save(update_fields=['pl_segment', 'band'])
                updated += 1
                self.stdout.write(f"  {'[DRY] ' if dry_run else ''}Updated: {feeder_obj.name} → {pl_segment} / Band {raw_band}")
            else:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. updated={updated}, skipped/unchanged={skipped}"
        ))
        if not_found:
            self.stdout.write(self.style.WARNING(
                f"\nNot found in DB ({len(not_found)}):"
            ))
            for n in not_found:
                self.stdout.write(f"  - {n}")
