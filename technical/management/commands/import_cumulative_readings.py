# -*- coding: utf-8 -*-
"""
Management command: import_cumulative_readings

Usage:
    python manage.py import_cumulative_readings \
        --file /path/to/energy_readings.xlsx \
        [--dry-run] \
        [--skip-existing]

The Excel must follow the exact layout of the uploaded file:
* Row 1 – title (ignored)
* Row 2 – month header + day numbers (1-31, then extra columns)
* Row 3+ – Feeder name in column A, cumulative MWh in columns C onward
"""

import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from common.models import Feeder
from technical.models import CumulativeMeterReading


class Command(BaseCommand):
    help = "Import cumulative meter readings from the supplied Excel file"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            required=True,
            type=str,
            help="Full path to the Excel file (energy_readings.xlsx)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse the file but do NOT write to DB",
        )
        parser.add_argument(
            "--skip-existing",
            action="store_true",
            help="Skip (feeder, reading_date) pairs that already exist",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        file_path = Path(options["file"])
        if not file_path.is_file():
            raise CommandError(f"File not found: {file_path}")

        # ------------------------------------------------------------------
        # 1. Load the sheet with pandas (keeps empty cells as NaN)
        # ------------------------------------------------------------------
        df = pd.read_excel(
            file_path,
            sheet_name=0,
            header=None,          # we will build our own header
            engine="openpyxl",
        )

        # ------------------------------------------------------------------
        # 2. Identify the three important rows
        # ------------------------------------------------------------------
        # Row 0 (0-based) – title (OCTOBER / NOVEMBER 2025 ...)
        title_row = df.iloc[0].astype(str).str.strip()
        # Row 1 – month header + day numbers
        month_header_row = df.iloc[1].astype(str).str.strip()
        # Row 2 – first feeder row (AHMADU BELLO …)
        first_data_row_idx = 2

        # ------------------------------------------------------------------
        # 3. Parse the two months from the title row
        # ------------------------------------------------------------------
        title_str = " ".join(title_row.dropna().tolist()).upper()
        if "OCTOBER" in title_str and "NOVEMBER" in title_str:
            month_oct = 10
            month_nov = 11
        else:
            raise CommandError(
                "Could not detect OCTOBER / NOVEMBER in the first row title."
            )

        # ------------------------------------------------------------------
        # 4. Build a list of (column_index, reading_date) for every day
        # ------------------------------------------------------------------
        day_columns = []          # list of (col_idx, date_obj)
        year = 2025
        
        # Track which month we're in based on day sequence
        last_day_seen = 0
        current_month = month_oct  # Start with October

        for col_idx, cell in enumerate(month_header_row):
            if pd.isna(cell) or cell == "nan":
                continue
            try:
                day = int(float(cell))
            except ValueError:
                # not a day number – skip (e.g. the "FEDDER" cell)
                continue

            # Detect month transition: if day number drops (e.g., 31 → 1), 
            # we've moved to the next month
            if day < last_day_seen:
                current_month = month_nov
            
            last_day_seen = day

            # Create the date for the current month
            try:
                reading_date = date(year, current_month, day)
                day_columns.append((col_idx, reading_date))
            except ValueError:
                # Invalid day for this month (e.g., day 31 in November)
                continue

        if not day_columns:
            raise CommandError("No day columns detected – check the header row.")

        self.stdout.write(
            self.style.SUCCESS(
                f"Detected {len(day_columns)} day-columns (Oct {month_oct} / Nov {month_nov} 2025)"
            )
        )

        # ------------------------------------------------------------------
        # 5. Iterate over feeder rows
        # ------------------------------------------------------------------
        created = 0
        skipped = 0
        errors = []

        for row_idx in range(first_data_row_idx, len(df)):
            row = df.iloc[row_idx]

            # ---- Feeder name (column A) ----
            feeder_name_raw = row.iloc[0]
            if pd.isna(feeder_name_raw):
                continue
            feeder_name = str(feeder_name_raw).strip().upper()

            # ---- Resolve Feeder object ----
            try:
                feeder = Feeder.objects.get(name__iexact=feeder_name)
            except Feeder.DoesNotExist:
                errors.append(f"Row {row_idx + 1}: Feeder '{feeder_name}' not in DB")
                continue
            except Feeder.MultipleObjectsReturned:
                errors.append(f"Row {row_idx + 1}: Multiple feeders named '{feeder_name}'")
                continue

            # ---- Process every day column ----
            for col_idx, reading_date in day_columns:
                raw_val = row.iloc[col_idx]
                if pd.isna(raw_val):
                    continue

                try:
                    cum_mwh = Decimal(str(raw_val).replace(",", ""))
                except InvalidOperation:
                    errors.append(
                        f"Row {row_idx + 1}, {reading_date}: invalid number '{raw_val}'"
                    )
                    continue

                # --------------------------------------------------------------
                # 6. Dry-run / skip-existing logic
                # --------------------------------------------------------------
                if options["skip_existing"]:
                    exists = CumulativeMeterReading.objects.filter(
                        feeder=feeder, reading_date=reading_date
                    ).exists()
                    if exists:
                        skipped += 1
                        continue

                if options["dry_run"]:
                    self.stdout.write(
                        f"[DRY-RUN] Would create: {feeder.name} – {reading_date} – {cum_mwh} MWh"
                    )
                    created += 1
                    continue

                # --------------------------------------------------------------
                # 7. Create / update the CumulativeMeterReading
                # --------------------------------------------------------------
                obj, was_created = CumulativeMeterReading.objects.update_or_create(
                    feeder=feeder,
                    reading_date=reading_date,
                    defaults={"cumulative_mwh": cum_mwh, "is_estimated": False},
                )
                if was_created:
                    created += 1
                else:
                    # updated – count as created for the summary
                    created += 1

        # ------------------------------------------------------------------
        # 8. Final report
        # ------------------------------------------------------------------
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS(f"Created/updated : {created} rows"))
        if skipped:
            self.stdout.write(self.style.WARNING(f"Skipped (existing) : {skipped} rows"))
        if errors:
            self.stdout.write(self.style.ERROR(f"{len(errors)} errors:"))
            for e in errors[:20]:          # limit output
                self.stdout.write(self.style.ERROR(f"  • {e}"))
            if len(errors) > 20:
                self.stdout.write(self.style.ERROR(f"  … and {len(errors) - 20} more"))
        self.stdout.write(self.style.SUCCESS("=" * 60))

        if options["dry_run"]:
            self.stdout.write(self.style.NOTICE("--- DRY-RUN MODE – NO DATA WRITTEN ---"))