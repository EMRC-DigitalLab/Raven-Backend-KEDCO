# technical/management/commands/investor_feeder_report.py
"""
Read-only export for the investor report: per-feeder supply/fault/O-S/load-shedding
hours and energy variance, for direct comparison against the January 2024 Excel baseline.

Usage:
    python manage.py investor_feeder_report --year 2026 --month 5 --out investor_may2026.csv
"""
import csv
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from django.core.management.base import BaseCommand
from django.db import connection

from common.models import Feeder
from technical.utils.compliance_utils import _get_category_codes


class Command(BaseCommand):
    help = "Export per-feeder supply/fault/O-S/load-shedding hours and energy variance for a given month."

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, required=True)
        parser.add_argument('--month', type=int, required=True)
        parser.add_argument('--out', type=str, default=None, help='CSV output path')
        parser.add_argument('--voltage', type=str, default='11kv', help="Feeder voltage_level filter, default '11kv'")

    def handle(self, *args, **opts):
        year, month = opts['year'], opts['month']
        from_date = date(year, month, 1)
        to_date = from_date + relativedelta(months=1) - timedelta(days=1)
        period_days = (to_date - from_date).days + 1
        voltage = opts['voltage']

        feeders = list(
            Feeder.objects
            .filter(is_onboarded=True, voltage_level=voltage)
            .select_related('business_district__state', 'band')
            .order_by('name')
        )
        feeder_ids = [f.id for f in feeders]
        self.stdout.write(f"Feeders in scope (onboarded, voltage={voltage}): {len(feeders)}")

        if not feeder_ids:
            self.stdout.write(self.style.ERROR("No feeders found for this scope."))
            return

        ls_codes, tx_codes = _get_category_codes()

        # ── Per-day supply hours, all calendar days in month, 0 where missing ──
        supply_by_feeder_day = self._daily_supply_hours(feeder_ids, from_date, to_date)

        # ── Interruption hours per feeder, per interruption_type ───────────────
        type_hours = self._interruption_hours_by_type(feeder_ids, from_date, to_date)

        # ── Energy delivered per feeder per day ─────────────────────────────────
        energy_by_feeder_day = self._daily_energy(feeder_ids, from_date, to_date)

        rows = []
        for f in feeders:
            fid = f.id
            day_map = supply_by_feeder_day.get(fid, {})
            all_days = [from_date + timedelta(days=i) for i in range(period_days)]
            daily_hours = [day_map.get(d, 0.0) for d in all_days]
            avg_supply = round(sum(daily_hours) / period_days, 2)
            zero_supply_days = sum(1 for h in daily_hours if h == 0.0)

            type_map = type_hours.get(fid, {})
            ls_hours = sum(h for t, h in type_map.items() if t in ls_codes)
            tcn_hours = sum(h for t, h in type_map.items() if t in tx_codes)
            os_hours = type_map.get('O/S', 0.0)
            disco_hours = sum(h for t, h in type_map.items() if t not in ls_codes and t not in tx_codes)
            fault_hours = disco_hours - os_hours  # DisCo faults excluding O/S (reported separately)

            energy_days = energy_by_feeder_day.get(fid, {})
            positive_days = sum(1 for v in energy_days.values() if v > 0)
            nonpositive_days = sum(1 for v in energy_days.values() if v <= 0)
            no_data_days = period_days - len(energy_days)

            rows.append({
                'feeder_name': f.name,
                'feeder_slug': f.slug,
                'state': f.business_district.state.name if f.business_district and f.business_district.state else '',
                'district': f.business_district.name if f.business_district else '',
                'band': f.band.name if f.band else '',
                'avg_supply_hours_per_day': avg_supply,
                'zero_supply_days': zero_supply_days,
                'fault_hours_per_day': round(fault_hours / period_days, 2),
                'fault_hours_total': round(fault_hours, 2),
                'os_hours_per_day': round(os_hours / period_days, 2),
                'os_hours_total': round(os_hours, 2),
                'load_shedding_hours_per_day': round(ls_hours / period_days, 2),
                'load_shedding_hours_total': round(ls_hours, 2),
                'tcn_hours_total_excluded': round(tcn_hours, 2),
                'energy_positive_days': positive_days,
                'energy_zero_or_negative_days': nonpositive_days,
                'energy_no_data_days': no_data_days,
            })

        # ── Network summary, mirroring the Jan 2024 baseline format ────────────
        total_feeders = len(rows)
        network_avg_supply = round(sum(r['avg_supply_hours_per_day'] for r in rows) / total_feeders, 2)
        below_6 = sum(1 for r in rows if r['avg_supply_hours_per_day'] < 6)
        zero_all_month = [r['feeder_name'] for r in rows if r['zero_supply_days'] == period_days]
        total_fault_hours = round(sum(r['fault_hours_total'] for r in rows), 2)
        total_os_hours = round(sum(r['os_hours_total'] for r in rows), 2)
        total_ls_hours = round(sum(r['load_shedding_hours_total'] for r in rows), 2)
        total_zero_energy_days = sum(r['energy_zero_or_negative_days'] for r in rows)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"=== {from_date:%B %Y} network summary ({total_feeders} feeders) ==="))
        self.stdout.write(f"Network average supply: {network_avg_supply} hrs/day ({round(network_avg_supply/24*100,1)}%)")
        self.stdout.write(f"Feeders below 6 hrs/day: {below_6}")
        self.stdout.write(f"Feeders with zero supply all month: {len(zero_all_month)} -> {zero_all_month}")
        self.stdout.write(f"Total DisCo fault hours (excl. O/S, L/S, TCN): {total_fault_hours}")
        self.stdout.write(f"Total O/S hours: {total_os_hours}")
        self.stdout.write(f"Total load-shedding hours: {total_ls_hours}")
        self.stdout.write(f"Total zero/negative-energy days: {total_zero_energy_days}")

        out_path = opts['out'] or f"investor_report_{year}_{month:02d}.csv"
        with open(out_path, 'w', newline='') as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        self.stdout.write(self.style.SUCCESS(f"\nCSV written to {out_path}"))

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _daily_supply_hours(self, feeder_ids, from_date, to_date):
        """Returns {feeder_id: {date: hours_supplied_that_day}}."""
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

    def _interruption_hours_by_type(self, feeder_ids, from_date, to_date):
        """
        Returns {feeder_id: {interruption_type: total_hours}} clipped to the period,
        including interruptions that started before the period but are still ongoing.
        """
        from django.utils import timezone
        from datetime import datetime as dt

        now = timezone.now()
        today = now.date()
        start_of_period = timezone.make_aware(dt.combine(from_date, dt.min.time()))
        end_of_period = now if to_date >= today else timezone.make_aware(dt.combine(to_date, dt.max.time()))

        placeholders = ','.join(['%s'] * len(feeder_ids))
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
                ), 0) as total_hours
            FROM technical_feederinterruption
            WHERE feeder_id IN ({placeholders})
                AND (
                    (occurred_at AT TIME ZONE 'Africa/Lagos')::date BETWEEN %s AND %s
                    OR (occurred_at < %s AND (restored_at IS NULL OR restored_at >= %s))
                )
            GROUP BY feeder_id, interruption_type
        """
        params = (
            [now, end_of_period, start_of_period]
            + list(feeder_ids)
            + [from_date, to_date, start_of_period, start_of_period]
        )
        result = {}
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            for fid, itype, hours in cursor.fetchall():
                result.setdefault(fid, {})[itype or 'Unknown'] = float(hours)
        return result

    def _daily_energy(self, feeder_ids, from_date, to_date):
        """Returns {feeder_id: {date: energy_mwh}} from EnergyDelivered (meter-derived)."""
        from technical.models import EnergyDelivered

        result = {}
        qs = (
            EnergyDelivered.objects
            .filter(feeder_id__in=feeder_ids, date__gte=from_date, date__lte=to_date)
            .values_list('feeder_id', 'date', 'energy_mwh')
        )
        for fid, d, mwh in qs:
            result.setdefault(fid, {})[d] = float(mwh)
        return result
