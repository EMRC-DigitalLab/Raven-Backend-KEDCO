# technical/management/commands/backfill_na_fault_types.py
"""
Backfill: fix all FeederInterruption records where interruption_type = 'N/A'.

Three recovery strategies, applied in order per record:

  Strategy A — Duplicate cleanup (270 records expected):
    A correct non-N/A sibling already exists at the same (feeder, occurred_at).
    The N/A record is an orphan from an earlier sync run. Safe to delete.

  Strategy B — DataNest lookup (records whose feeder still exists in technicalenergyfault):
    The DataNest row may now have a fault_type_id set (DSO added it later).
    JOIN technicalenergyfault with fault_types on fault_type_id to get the
    correct fault_code. Update the N/A record in place.

  Strategy C — Stale orphans (DSO-deleted faults):
    The original DataNest row was soft-deleted then purged. The sync had already
    created an N/A record in Raven before the purge. Pass --delete-stale to
    remove these (they represent retracted/corrected DSO submissions).

Run against production (default):
    python manage.py backfill_na_fault_types --delete-stale

Run against staging:
    APP_ENV=staging python manage.py backfill_na_fault_types --delete-stale

Dry run (no writes, full report):
    python manage.py backfill_na_fault_types --dry-run --delete-stale
"""

from django.core.management.base import BaseCommand
from django.db import connections, connection, transaction

from technical.models import FeederInterruption
from technical.sync.fault_normalizer import FaultNormalizer


class Command(BaseCommand):
    help = 'Fix FeederInterruption records where interruption_type is N/A'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would change without writing anything',
        )
        parser.add_argument(
            '--delete-stale',
            action='store_true',
            help='Also delete N/A records whose DataNest rows were purged (stale orphans)',
        )

    def handle(self, *args, **options):
        dry_run      = options['dry_run']
        delete_stale = options['delete_stale']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be written.\n'))

        total_na = FeederInterruption.objects.filter(interruption_type='N/A').count()
        self.stdout.write(f'Total N/A records in Raven:  {total_na:,}')
        if total_na == 0:
            self.stdout.write(self.style.SUCCESS('Nothing to fix.'))
            return

        normaliser = FaultNormalizer()

        # ── Strategy A: delete N/A records that have a correct sibling ────────
        a_ids = self._find_strategy_a_ids()
        self.stdout.write(f'Strategy A (delete duplicates):      {len(a_ids):,}')

        # ── Strategy B: look up correct fault type from DataNest ─────────────
        datanest_map = self._build_datanest_map()
        self.stdout.write(f'DataNest rows with fault type set:   {len(datanest_map):,}')

        b_updates = []   # list of (FeederInterruption pk, new_code)
        c_ids     = []   # stale orphan PKs (DataNest rows were purged)

        na_qs = (
            FeederInterruption.objects
            .filter(interruption_type='N/A')
            .exclude(pk__in=a_ids)
            .select_related('feeder')
        )

        for intr in na_qs.iterator(chunk_size=500):
            occ_naive = str(intr.occurred_at.replace(tzinfo=None))
            lookup_key = (str(intr.feeder.slug), occ_naive)

            raw_code = datanest_map.get(lookup_key)
            if raw_code:
                correct_code = normaliser.normalise(raw_code)
                if correct_code != 'N/A':
                    b_updates.append((intr.pk, correct_code))
                    continue

            c_ids.append(intr.pk)

        self.stdout.write(f'Strategy B (update fault type):      {len(b_updates):,}')
        self.stdout.write(
            f'Strategy C (stale orphans to delete): {len(c_ids):,}'
            if delete_stale else
            f'Strategy C (stale orphans, skipped): {len(c_ids):,}'
        )
        self.stdout.write('')

        if dry_run:
            return

        # ── Apply Strategy A ──────────────────────────────────────────────────
        if a_ids:
            try:
                with transaction.atomic():
                    deleted, _ = FeederInterruption.objects.filter(pk__in=a_ids).delete()
                self.stdout.write(self.style.SUCCESS(f'[A] Deleted {deleted:,} duplicate N/A records.'))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'[A] Delete failed: {exc}'))

        # ── Apply Strategy B ──────────────────────────────────────────────────
        if b_updates:
            try:
                with transaction.atomic():
                    pks = [pk for pk, _ in b_updates]
                    intr_map = {i.pk: i for i in FeederInterruption.objects.filter(pk__in=pks)}
                    to_save = []
                    for pk, new_code in b_updates:
                        obj = intr_map.get(pk)
                        if obj:
                            if not FeederInterruption.objects.filter(
                                feeder_id=obj.feeder_id,
                                occurred_at=obj.occurred_at,
                                interruption_type=new_code,
                            ).exclude(pk=pk).exists():
                                obj.interruption_type = new_code
                                to_save.append(obj)
                    if to_save:
                        FeederInterruption.objects.bulk_update(
                            to_save, fields=['interruption_type'], batch_size=500
                        )
                self.stdout.write(self.style.SUCCESS(f'[B] Updated {len(to_save):,} records with correct fault type.'))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'[B] Update failed: {exc}'))

        # ── Apply Strategy C ──────────────────────────────────────────────────
        if delete_stale and c_ids:
            try:
                CHUNK = 1000
                total_deleted = 0
                with transaction.atomic():
                    for i in range(0, len(c_ids), CHUNK):
                        deleted, _ = FeederInterruption.objects.filter(
                            pk__in=c_ids[i:i + CHUNK]
                        ).delete()
                        total_deleted += deleted
                self.stdout.write(self.style.SUCCESS(
                    f'[C] Deleted {total_deleted:,} stale N/A orphan records.'
                ))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'[C] Delete stale failed: {exc}'))

        # Final count
        remaining = FeederInterruption.objects.filter(interruption_type='N/A').count()
        self.stdout.write(f'N/A records remaining: {remaining:,}')
        if normaliser.unrecognised:
            self.stdout.write(self.style.WARNING(
                f'Unrecognised raw codes: {", ".join(sorted(normaliser.unrecognised))}'
            ))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _find_strategy_a_ids(self):
        """
        Return PKs of N/A records where a non-N/A record already exists
        for the same (feeder, occurred_at). These are safe to delete.
        """
        with connection.cursor() as cur:
            cur.execute("""
                SELECT fi.id
                FROM technical_feederinterruption fi
                WHERE fi.interruption_type = 'N/A'
                  AND EXISTS (
                      SELECT 1
                      FROM technical_feederinterruption t2
                      WHERE t2.feeder_id   = fi.feeder_id
                        AND t2.occurred_at = fi.occurred_at
                        AND t2.interruption_type != 'N/A'
                  )
            """)
            return [row[0] for row in cur.fetchall()]

    def _build_datanest_map(self):
        """
        Return dict: (feeder_slug, occurred_at_naive_str) → resolved fault_code.
        Fetches ALL non-deleted technicalenergyfault rows and resolves the fault
        type via fault_types JOIN (primary) or fault_type column (fallback).
        Only includes rows where the fault type can actually be resolved.
        """
        result = {}
        conn = connections['external']
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    tef.Feeder_id,
                    tef.time_of_occurrence,
                    COALESCE(ft.fault_code, tef.fault_type) AS resolved_code
                FROM technicalenergyfault tef
                LEFT JOIN fault_types ft
                       ON tef.fault_type_id = ft.fault_type_id
                WHERE tef.deleted_at IS NULL
                  AND (
                      tef.fault_type_id IS NOT NULL
                      OR (tef.fault_type IS NOT NULL AND tef.fault_type != '' AND UPPER(tef.fault_type) != 'N/A')
                  )
            """)
            for feeder_slug, occurred_at, fault_code in cur.fetchall():
                if feeder_slug and occurred_at and fault_code:
                    key = (str(feeder_slug), str(occurred_at))
                    result[key] = str(fault_code)
        return result

