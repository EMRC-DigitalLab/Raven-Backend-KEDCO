# technical/management/commands/substation_case_analysis.py
"""
Before-vs-after deployment case analysis for a single injection substation.

Pre-deployment  : first data date → 2025-07-31  (historical, before Raven)
Post-deployment : 2025-08-01     → today         (Raven live)

Outputs per-feeder comparison CSV + summary table to stdout.

Usage:
    python manage.py substation_case_analysis --substation "CLUB"
    python manage.py substation_case_analysis --substation "ZARIA ROAD" --out zaria_analysis.csv
"""
import csv
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Min
from django.utils import timezone

from common.models import Feeder, InjectionSubstation
from technical.models import EnergyDelivered
from technical.utils.compliance_utils import _get_category_codes

DEPLOY_DATE = date(2025, 8, 1)  # Raven deployment start


class Command(BaseCommand):
    help = "Before vs after Raven deployment case analysis for one injection substation."

    def add_arguments(self, parser):
        parser.add_argument('--substation', type=str, required=True, help="Injection substation name (exact)")
        parser.add_argument('--out', type=str, default=None)
        parser.add_argument('--voltage', type=str, default='11kv')

    def handle(self, *args, **opts):
        name = opts['substation']
        voltage = opts['voltage']

        sub = InjectionSubstation.objects.filter(name__iexact=name).first()
        if not sub:
            self.stdout.write(self.style.ERROR(f"Substation '{name}' not found."))
            return

        feeders = list(
            Feeder.objects
            .filter(substation=sub, is_onboarded=True, voltage_level=voltage)
            .select_related('business_district__state', 'band')
            .order_by('name')
        )
        if not feeders:
            self.stdout.write(self.style.ERROR(f"No onboarded {voltage} feeders under '{name}'."))
            return

        feeder_ids = [f.id for f in feeders]
        self.stdout.write(f"\nSubstation: {sub.name}  |  Feeders: {len(feeders)}")
        self.stdout.write(", ".join(f.name for f in feeders))

        # ── Determine pre-period start from earliest data ─────────────────────
        earliest = (
            EnergyDelivered.objects.filter(feeder_id__in=feeder_ids).aggregate(Min('date'))['date__min']
        )
        if not earliest:
            self.stdout.write(self.style.WARNING("No EnergyDelivered data found. Using 2023-01-01 as pre-period start."))
            earliest = date(2023, 1, 1)

        pre_from = earliest
        pre_to = DEPLOY_DATE - timedelta(days=1)          # 2025-07-31
        post_from = DEPLOY_DATE                            # 2025-08-01
        post_to = date.today()

        pre_days = (pre_to - pre_from).days + 1
        post_days = (post_to - post_from).days + 1

        self.stdout.write(f"\nPre-deployment : {pre_from} to {pre_to}  ({pre_days} days)")
        self.stdout.write(f"Post-deployment: {post_from} to {post_to}  ({post_days} days)")

        ls_codes, tx_codes = _get_category_codes()

        # ── Compute metrics for each period ───────────────────────────────────
        pre_supply   = self._daily_supply_hours(feeder_ids, pre_from,  pre_to)
        post_supply  = self._daily_supply_hours(feeder_ids, post_from, post_to)

        # Pre-period: cap individual event duration at 72h to exclude legacy records
        # where restored_at was set incorrectly during historical DataNest import.
        pre_int   = self._interruption_hours_by_type(feeder_ids, pre_from,  pre_to,  max_event_hours=72)
        post_int  = self._interruption_hours_by_type(feeder_ids, post_from, post_to)

        pre_energy  = self._daily_energy(feeder_ids, pre_from,  pre_to)
        post_energy = self._daily_energy(feeder_ids, post_from, post_to)

        rows = []
        for f in feeders:
            fid = f.id

            def feeder_stats(supply_map, int_map, energy_map, n_days, from_d, to_d):
                all_dates = [from_d + timedelta(days=i) for i in range(n_days)]
                day_map = supply_map.get(fid, {})
                daily_hrs = [day_map.get(d, 0.0) for d in all_dates]
                avg_supply = round(sum(daily_hrs) / n_days, 2)
                zero_supply_days = sum(1 for h in daily_hrs if h == 0.0)

                type_map = int_map.get(fid, {})
                ls_hrs   = sum(h for t, h in type_map.items() if t in ls_codes)
                tcn_hrs  = sum(h for t, h in type_map.items() if t in tx_codes)
                os_hrs   = type_map.get('O/S', 0.0)
                disco_hrs = sum(h for t, h in type_map.items() if t not in ls_codes and t not in tx_codes)
                fault_hrs = disco_hrs - os_hrs

                en_days = energy_map.get(fid, {})
                pos_days  = sum(1 for v in en_days.values() if v > 0)
                zero_days = sum(1 for v in en_days.values() if v <= 0)
                no_data_days = n_days - len(en_days)

                return {
                    'avg_supply_hrs_day': avg_supply,
                    'availability_pct': round(avg_supply / 24 * 100, 1),
                    'zero_supply_days': zero_supply_days,
                    'zero_supply_pct': round(zero_supply_days / n_days * 100, 1),
                    'fault_hrs_total': round(fault_hrs, 2),
                    'fault_hrs_per_day': round(fault_hrs / n_days, 2),
                    'os_hrs_total': round(os_hrs, 2),
                    'os_hrs_per_day': round(os_hrs / n_days, 2),
                    'ls_hrs_total': round(ls_hrs, 2),
                    'ls_hrs_per_day': round(ls_hrs / n_days, 2),
                    'tcn_hrs_total': round(tcn_hrs, 2),
                    'energy_positive_days': pos_days,
                    'energy_zero_neg_days': zero_days,
                    'energy_no_data_days': no_data_days,
                }

            pre  = feeder_stats(pre_supply,  pre_int,  pre_energy,  pre_days,  pre_from,  pre_to)
            post = feeder_stats(post_supply, post_int, post_energy, post_days, post_from, post_to)

            # change in avg supply hours/day
            supply_delta = round(post['avg_supply_hrs_day'] - pre['avg_supply_hrs_day'], 2)
            supply_delta_pct = round(
                (supply_delta / pre['avg_supply_hrs_day'] * 100) if pre['avg_supply_hrs_day'] > 0 else 0, 1
            )

            row = {'feeder': f.name, 'band': f.band.name if f.band else '', 'district': f.business_district.name if f.business_district else ''}
            for k, v in pre.items():
                row[f'PRE_{k}'] = v
            for k, v in post.items():
                row[f'POST_{k}'] = v
            row['supply_change_hrs'] = supply_delta
            row['supply_change_pct'] = supply_delta_pct
            rows.append(row)

        # ── Print summary table ───────────────────────────────────────────────
        self._print_summary(rows, pre_from, pre_to, pre_days, post_from, post_to, post_days, sub.name)

        out_path = opts['out'] or f"case_analysis_{name.lower().replace(' ', '_')}.csv"
        with open(out_path, 'w', newline='') as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        self.stdout.write(self.style.SUCCESS(f"\nCSV written to {out_path}"))

    def _print_summary(self, rows, pre_from, pre_to, pre_days, post_from, post_to, post_days, sub_name):
        n = len(rows)
        sep = "-" * 72

        def net_avg(key):
            return round(sum(r[key] for r in rows) / n, 2)

        self.stdout.write(f"\n{'='*72}")
        self.stdout.write(f"  CASE ANALYSIS — {sub_name}  ({n} feeders)")
        self.stdout.write(f"{'='*72}")
        self.stdout.write(f"  {'Metric':<38}  {'PRE':>10}  {'POST':>10}  {'Change':>8}")
        self.stdout.write(sep)

        def row(label, pre_val, post_val, fmt=".2f", suffix=""):
            delta = post_val - pre_val
            sign = "+" if delta >= 0 else ""
            self.stdout.write(
                f"  {label:<38}  {pre_val:>10{fmt}}{suffix}  {post_val:>10{fmt}}{suffix}  {sign}{delta:{fmt}}{suffix}"
            )

        self.stdout.write(f"  Period: {pre_from} to {pre_to}  ({pre_days}d) vs {post_from} to {post_to}  ({post_days}d)")
        self.stdout.write(sep)
        self.stdout.write("  SUPPLY")
        row("  Avg supply hrs/day (network)", net_avg('PRE_avg_supply_hrs_day'), net_avg('POST_avg_supply_hrs_day'))
        row("  Avg availability %", net_avg('PRE_availability_pct'), net_avg('POST_availability_pct'), fmt=".1f", suffix="%")
        pre_below6 = sum(1 for r in rows if r['PRE_avg_supply_hrs_day'] < 6)
        post_below6 = sum(1 for r in rows if r['POST_avg_supply_hrs_day'] < 6)
        self.stdout.write(f"  {'Feeders below 6 hrs/day':<38}  {pre_below6:>10}    {post_below6:>10}    {post_below6-pre_below6:>+8}")
        pre_zero = sum(1 for r in rows if r['PRE_zero_supply_days'] == (r.get('PRE_zero_supply_days', 0) and pre_days))
        # zero all month feeders
        pre_zero_all = [r['feeder'] for r in rows if r['PRE_zero_supply_pct'] == 100.0]
        post_zero_all = [r['feeder'] for r in rows if r['POST_zero_supply_pct'] == 100.0]
        self.stdout.write(f"  {'Feeders zero supply all period':<38}  {len(pre_zero_all):>10}    {len(post_zero_all):>10}    {len(post_zero_all)-len(pre_zero_all):>+8}")

        self.stdout.write(sep)
        self.stdout.write("  FAULTS (per day avg across network)")
        row("  DisCo fault hrs/day", net_avg('PRE_fault_hrs_per_day'), net_avg('POST_fault_hrs_per_day'))
        row("  O/S hrs/day", net_avg('PRE_os_hrs_per_day'), net_avg('POST_os_hrs_per_day'))
        row("  Load-shedding hrs/day", net_avg('PRE_ls_hrs_per_day'), net_avg('POST_ls_hrs_per_day'))
        row("  TCN/grid hrs/day (excl. from faults)", net_avg('PRE_tcn_hrs_total') / pre_days, net_avg('POST_tcn_hrs_total') / post_days)

        self.stdout.write(sep)
        self.stdout.write("  FAULT TOTALS (absolute, different period lengths — see per-day for fair comparison)")
        row("  Total DisCo fault hrs", net_avg('PRE_fault_hrs_total'), net_avg('POST_fault_hrs_total'), fmt=".0f")
        row("  Total L/S hrs", net_avg('PRE_ls_hrs_total'), net_avg('POST_ls_hrs_total'), fmt=".0f")

        self.stdout.write(sep)
        self.stdout.write("  ENERGY METER (avg days per feeder in each period)")
        row("  Avg positive-energy days", net_avg('PRE_energy_positive_days'), net_avg('POST_energy_positive_days'), fmt=".1f")
        row("  Avg zero/neg-energy days", net_avg('PRE_energy_zero_neg_days'), net_avg('POST_energy_zero_neg_days'), fmt=".1f")
        row("  Avg no-data days", net_avg('PRE_energy_no_data_days'), net_avg('POST_energy_no_data_days'), fmt=".1f")

        self.stdout.write(sep)
        self.stdout.write("  PER-FEEDER SUPPLY HOURS")
        self.stdout.write(f"  {'Feeder':<24} {'Band':>5}  {'PRE hrs/d':>10}  {'POST hrs/d':>10}  {'Change':>8}")
        self.stdout.write("  " + "-" * 60)
        for r in rows:
            sign = "+" if r['supply_change_hrs'] >= 0 else ""
            self.stdout.write(
                f"  {r['feeder']:<24} {r['band']:>5}  "
                f"{r['PRE_avg_supply_hrs_day']:>10.2f}  "
                f"{r['POST_avg_supply_hrs_day']:>10.2f}  "
                f"{sign}{r['supply_change_hrs']:>6.2f} ({sign}{r['supply_change_pct']:.1f}%)"
            )
        self.stdout.write(f"{'='*72}\n")

    # ── Data helpers ─────────────────────────────────────────────────────────

    def _daily_supply_hours(self, feeder_ids, from_date, to_date):
        placeholders = ','.join(['%s'] * len(feeder_ids))
        query = f"""
            SELECT feeder_id, date, COUNT(DISTINCT hour) AS hrs
            FROM technical_hourlyload
            WHERE feeder_id IN ({placeholders})
                AND date BETWEEN %s AND %s
                AND load_mw > 0
            GROUP BY feeder_id, date
        """
        result = {}
        with connection.cursor() as cursor:
            cursor.execute(query, list(feeder_ids) + [from_date, to_date])
            for fid, d, hrs in cursor.fetchall():
                result.setdefault(fid, {})[d] = min(float(hrs), 24.0)
        return result

    def _interruption_hours_by_type(self, feeder_ids, from_date, to_date, max_event_hours=None):
        now = timezone.now()
        today = now.date()
        start_of_period = timezone.make_aware(datetime.combine(from_date, datetime.min.time()))
        end_of_period = now if to_date >= today else timezone.make_aware(datetime.combine(to_date, datetime.max.time()))

        placeholders = ','.join(['%s'] * len(feeder_ids))
        duration_filter = (
            f"AND EXTRACT(EPOCH FROM (COALESCE(restored_at, %s) - occurred_at)) / 3600.0 <= {max_event_hours}"
            if max_event_hours else ""
        )
        duration_filter_params = [end_of_period] if max_event_hours else []

        query = f"""
            SELECT
                feeder_id,
                interruption_type,
                COALESCE(SUM(
                    GREATEST(
                        EXTRACT(EPOCH FROM (
                            LEAST(COALESCE(restored_at, %s), %s) - GREATEST(occurred_at, %s)
                        )) / 3600.0,
                        0
                    )
                ), 0) AS total_hours
            FROM technical_feederinterruption
            WHERE feeder_id IN ({placeholders})
                AND (
                    (occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
                    OR (occurred_at < %s AND (restored_at IS NULL OR restored_at >= %s))
                )
                {duration_filter}
            GROUP BY feeder_id, interruption_type
        """
        params = (
            [now, end_of_period, start_of_period]
            + list(feeder_ids)
            + [from_date, to_date, start_of_period, start_of_period]
            + duration_filter_params
        )
        result = {}
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            for fid, itype, hours in cursor.fetchall():
                result.setdefault(fid, {})[itype or 'Unknown'] = float(hours)
        return result

    def _daily_energy(self, feeder_ids, from_date, to_date):
        result = {}
        qs = (
            EnergyDelivered.objects
            .filter(feeder_id__in=feeder_ids, date__gte=from_date, date__lte=to_date)
            .values_list('feeder_id', 'date', 'energy_mwh')
        )
        for fid, d, mwh in qs:
            result.setdefault(fid, {})[d] = float(mwh)
        return result
