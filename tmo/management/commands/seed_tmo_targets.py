"""
python manage.py seed_tmo_targets [--month YYYY-MM] [--clear]

Seeds dummy TMO targets for a given month so the dashboard has numbers to
compare actuals against. Safe to re-run (uses update_or_create). Remove all
dummy records with --clear.

Dummy ID range: 900000–999999 (well above any real DataNest tmo_id).
"""
import calendar
from datetime import date, timedelta

from django.core.management.base import BaseCommand

from commercial.models import TMOCollectionTarget, TMOFeederTarget
from common.models import Feeder
from tmo.models import TMOMonthlySegmentTarget, TMONetworkConfig

DUMMY_ID_BASE = 900_000   # all dummy tmo_ids start here


class Command(BaseCommand):
    help = "Seed dummy TMO targets for the dashboard (remove with --clear)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--month', default=None,
            help='Month to seed, format YYYY-MM (default: current month)',
        )
        parser.add_argument(
            '--clear', action='store_true',
            help='Delete all dummy records (tmo_id >= 900000) instead of seeding',
        )

    def handle(self, *args, **options):
        if options['clear']:
            self._clear()
            return

        if options['month']:
            year, month = map(int, options['month'].split('-'))
        else:
            today = date.today()
            year, month = today.year, today.month

        self.stdout.write(f"Seeding dummy TMO targets for {year}-{month:02d}…")

        self._seed_network_config(year, month)
        self._seed_segment_targets(year, month)
        self._seed_feeder_targets(year, month)
        self._seed_collection_targets(year, month)

        self.stdout.write(self.style.SUCCESS("Done. Run with --clear to remove dummy data."))

    # ── Network config ────────────────────────────────────────────────────────

    def _seed_network_config(self, year, month):
        obj, created = TMONetworkConfig.objects.update_or_create(
            year=year, month=month,
            defaults={
                'target_md_share_pct':      65.0,
                'monthly_energy_target_gwh': 45.0,   # 45 GWh/month across the network
            },
        )
        verb = "Created" if created else "Updated"
        self.stdout.write(f"  {verb} TMONetworkConfig {year}-{month:02d}")

    # ── Segment targets ───────────────────────────────────────────────────────

    def _seed_segment_targets(self, year, month):
        # Rough KEDCO figures:
        #   MDI   ~14 000 MWh/month, avg tariff ~₦155 000/MWh
        #   MDNI  ~18 000 MWh/month, avg tariff ~₦110 000/MWh
        #   Regions ~13 000 MWh/month, avg tariff ~₦85 000/MWh
        segments = [
            {
                'segment':                  'MDI',
                'target_energy_mwh':        28_000,
                'target_revenue_ngn':       4_340_000_000,   # ₦4.34B
                'target_collection_ngn':    3_906_000_000,   # 90% collection
                'average_tariff_ngn_per_mwh': 155_000,
            },
            {
                'segment':                  'MDNI',
                'target_energy_mwh':        18_000,
                'target_revenue_ngn':       1_980_000_000,
                'target_collection_ngn':    1_782_000_000,
                'average_tariff_ngn_per_mwh': 110_000,
            },
            {
                'segment':                  'Regions',
                'target_energy_mwh':        13_000,
                'target_revenue_ngn':       1_105_000_000,
                'target_collection_ngn':     994_500_000,
                'average_tariff_ngn_per_mwh': 85_000,
            },
        ]
        for s in segments:
            seg = s.pop('segment')
            obj, created = TMOMonthlySegmentTarget.objects.update_or_create(
                segment=seg, year=year, month=month,
                defaults=s,
            )
            verb = "Created" if created else "Updated"
            self.stdout.write(f"  {verb} TMOMonthlySegmentTarget — {seg}")

    # ── Per-feeder daily targets ──────────────────────────────────────────────

    def _seed_feeder_targets(self, year, month):
        feeders = list(
            Feeder.objects.filter(is_onboarded=True)
            .select_related('band')
            .only('id', 'slug', 'voltage_level')
        )

        days_in_month = calendar.monthrange(year, month)[1]
        month_start   = date(year, month, 1)

        created_count = updated_count = 0
        dummy_counter = DUMMY_ID_BASE

        for feeder in feeders:
            # Daily target: 33KV ≈ 45 MWh/day, 11KV ≈ 8 MWh/day
            if feeder.voltage_level == '33kv':
                daily_mwh = 45.0
            else:
                daily_mwh = 8.0

            for day_offset in range(days_in_month):
                target_date  = month_start + timedelta(days=day_offset)
                dummy_tmo_id = dummy_counter
                dummy_counter += 1

                _, created = TMOFeederTarget.objects.update_or_create(
                    tmo_id=dummy_tmo_id,
                    defaults={
                        'feeder':       feeder,
                        'feeder_code':  feeder.slug,
                        'target_date':  target_date,
                        'target_mwh':   daily_mwh,
                        'set_by_user_id': 'seed_script',
                    },
                )
                if created:
                    created_count += 1
                else:
                    updated_count += 1

        self.stdout.write(
            f"  TMOFeederTarget — {created_count} created, {updated_count} updated "
            f"({len(feeders)} feeders × {days_in_month} days)"
        )

    # ── Collection targets ────────────────────────────────────────────────────

    def _seed_collection_targets(self, year, month):
        period_month = date(year, month, 1)

        entries = [
            # segment_code, sub_segment, target_amount, actual_amount (0 = not yet known)
            ('MDI',    'MDI',         1_953_000_000, 0),
            ('MDNI',   'MDNI',        1_782_000_000, 0),
            ('Regions','Regions',      994_500_000,  0),
        ]

        dummy_counter = DUMMY_ID_BASE + 50_000   # offset to avoid collision with feeder targets

        for seg, sub, target, actual in entries:
            _, created = TMOCollectionTarget.objects.update_or_create(
                tmo_id=dummy_counter,
                defaults={
                    'segment_code':   seg,
                    'sub_segment':    sub,
                    'period_month':   period_month,
                    'target_amount':  target,
                    'actual_amount':  actual,
                    'set_by_user_id': 'seed_script',
                },
            )
            verb = "Created" if created else "Updated"
            self.stdout.write(f"  {verb} TMOCollectionTarget — {seg}")
            dummy_counter += 1

    # ── Clear ─────────────────────────────────────────────────────────────────

    def _clear(self):
        ft = TMOFeederTarget.objects.filter(tmo_id__gte=DUMMY_ID_BASE).delete()
        ct = TMOCollectionTarget.objects.filter(tmo_id__gte=DUMMY_ID_BASE).delete()
        self.stdout.write(f"Deleted dummy TMOFeederTarget rows:      {ft[0]}")
        self.stdout.write(f"Deleted dummy TMOCollectionTarget rows:  {ct[0]}")
        self.stdout.write(
            "NOTE: TMOMonthlySegmentTarget and TMONetworkConfig have no tmo_id — "
            "delete manually via admin if needed."
        )
        self.stdout.write(self.style.SUCCESS("Cleared."))
