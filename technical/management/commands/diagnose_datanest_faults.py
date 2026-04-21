# technical/management/commands/diagnose_datanest_faults.py
"""
Rigorous diagnostic command to investigate why feeder interruptions are
being fetched as N/A from DataNest.

Checks:
  1.  All tables in DataNest — look for a fault-type lookup table
  2.  Schema of `technicalenergyfault` — column names, types, nullability
  3.  Schema of `Technicalhourlydata`  — same
  4.  Row counts in both tables
  5.  `technicalenergyfault.fault_type` value distribution (what's actually stored)
  6.  NULL / empty / 'N/A' breakdown in fault_type
  7.  Sample raw rows from `technicalenergyfault`
  8.  `Technicalhourlydata.LoadS` value distribution (non-numeric strings = old fault types)
  9.  Sample rows where LoadS is non-numeric (the old fault-detection approach)
  10. Any fault-type lookup / reference table found — show its full contents
  11. Cross-check: feeders in Raven vs DataNest (slug match rate)
  12. Date range of data in both tables

Usage:
    python manage.py diagnose_datanest_faults
    python manage.py diagnose_datanest_faults --table technicalenergyfault
"""

from django.core.management.base import BaseCommand
from django.db import connections

from common.models import Feeder


class Command(BaseCommand):
    help = 'Rigorous diagnostic of DataNest fault/interruption data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--table',
            type=str,
            default=None,
            help='Focus deep-dive on a specific DataNest table name',
        )
        parser.add_argument(
            '--feeder',
            type=str,
            default=None,
            help='Limit sample rows to a specific feeder_id slug',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=20,
            help='Number of sample rows to display (default: 20)',
        )

    def handle(self, *args, **options):
        focus_table = options.get('table')
        feeder_filter = options.get('feeder')
        sample_limit = options.get('limit', 20)

        self.sep = '-' * 72

        conn = connections['external']
        with conn.cursor() as cur:
            self._check1_all_tables(cur)
            self._check2_schema(cur, 'technicalenergyfault')
            self._check2_schema(cur, 'Technicalhourlydata')
            self._check3_row_counts(cur)
            self._check4_fault_type_distribution(cur, feeder_filter)
            self._check5_null_breakdown(cur)
            self._check6_sample_fault_rows(cur, sample_limit, feeder_filter)
            self._check7_loads_nonnumeric(cur, sample_limit, feeder_filter)
            self._check8_fault_lookup_tables(cur)
            self._check9_feeder_slug_match(cur)
            self._check10_date_ranges(cur)

            if focus_table:
                self._deep_dive_table(cur, focus_table, sample_limit)

        self.stdout.write('\n' + self.style.SUCCESS('=== Diagnostic complete ==='))

    # ─────────────────────────────────────────────────────────────────────────
    # Check 1 — all tables in DataNest
    # ─────────────────────────────────────────────────────────────────────────
    def _check1_all_tables(self, cur):
        self._header('CHECK 1 — All tables in DataNest database')
        cur.execute("SHOW TABLES")
        tables = [row[0] for row in cur.fetchall()]
        self.stdout.write(f'  Total tables: {len(tables)}\n')
        for t in sorted(tables):
            self.stdout.write(f'    {t}')

        # Flag anything that looks like a fault-type reference table
        keywords = ('fault', 'type', 'category', 'interruption', 'outage', 'cause')
        suspects = [t for t in tables if any(k in t.lower() for k in keywords)]
        if suspects:
            self.stdout.write(
                '\n' + self.style.WARNING(
                    f'  Possible fault-type lookup tables: {suspects}'
                )
            )
        self._all_tables = tables   # keep for later checks

    # ─────────────────────────────────────────────────────────────────────────
    # Check 2 — schema of key tables
    # ─────────────────────────────────────────────────────────────────────────
    def _check2_schema(self, cur, table_name):
        self._header(f'CHECK 2 — Schema: {table_name}')
        try:
            cur.execute(f'DESCRIBE `{table_name}`')
            rows = cur.fetchall()
            if not rows:
                self.stdout.write(self.style.WARNING(f'  Table not found or empty schema.'))
                return
            self.stdout.write(f'  {"Field":<35} {"Type":<25} {"Null":<6} {"Key":<6} {"Default"}')
            self.stdout.write(f'  {"-"*35} {"-"*25} {"-"*6} {"-"*6} {"-"*15}')
            for col in rows:
                field, ftype, null, key, default, extra = col
                self.stdout.write(
                    f'  {str(field):<35} {str(ftype):<25} {str(null):<6} '
                    f'{str(key):<6} {str(default)}'
                )
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'  ERROR: {exc}'))

    # ─────────────────────────────────────────────────────────────────────────
    # Check 3 — row counts
    # ─────────────────────────────────────────────────────────────────────────
    def _check3_row_counts(self, cur):
        self._header('CHECK 3 — Row counts in key tables')
        for table in ('technicalenergyfault', 'Technicalhourlydata'):
            try:
                cur.execute(f'SELECT COUNT(*) FROM `{table}`')
                count = cur.fetchone()[0]
                self.stdout.write(f'  {table:<35}  {count:>12,} rows')
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'  {table}: ERROR — {exc}'))

    # ─────────────────────────────────────────────────────────────────────────
    # Check 4 — fault_type distribution in technicalenergyfault
    # ─────────────────────────────────────────────────────────────────────────
    def _check4_fault_type_distribution(self, cur, feeder_filter=None):
        self._header('CHECK 4 — fault_type value distribution in technicalenergyfault')
        try:
            where = f"WHERE feeder_id = '{feeder_filter}'" if feeder_filter else ''
            cur.execute(f"""
                SELECT
                    COALESCE(fault_type, '<<NULL>>') AS ft,
                    COUNT(*)                          AS cnt
                FROM technicalenergyfault
                {where}
                GROUP BY ft
                ORDER BY cnt DESC
                LIMIT 60
            """)
            rows = cur.fetchall()
            if not rows:
                self.stdout.write('  No rows found.')
                return
            self.stdout.write(f'  {"fault_type value":<35} {"count":>12}')
            self.stdout.write(f'  {"-"*35} {"-"*12}')
            for ft, cnt in rows:
                flag = ''
                if ft in ('<<NULL>>', '', 'N/A', 'n/a'):
                    flag = '  <── PROBLEM'
                self.stdout.write(f'  {str(ft):<35} {cnt:>12,}{flag}')
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'  ERROR: {exc}'))

    # ─────────────────────────────────────────────────────────────────────────
    # Check 5 — NULL / empty / N/A breakdown
    # ─────────────────────────────────────────────────────────────────────────
    def _check5_null_breakdown(self, cur):
        self._header('CHECK 5 — NULL / empty / literal-N/A breakdown in technicalenergyfault')
        queries = {
            'Total rows':                  "SELECT COUNT(*) FROM technicalenergyfault",
            'NULL fault_type':             "SELECT COUNT(*) FROM technicalenergyfault WHERE fault_type IS NULL",
            'Empty string fault_type':     "SELECT COUNT(*) FROM technicalenergyfault WHERE fault_type = ''",
            "Literal 'N/A' fault_type":    "SELECT COUNT(*) FROM technicalenergyfault WHERE fault_type = 'N/A'",
            "Literal 'n/a' fault_type":    "SELECT COUNT(*) FROM technicalenergyfault WHERE fault_type = 'n/a'",
            'Non-empty, non-N/A':          """
                SELECT COUNT(*) FROM technicalenergyfault
                WHERE fault_type IS NOT NULL
                  AND fault_type != ''
                  AND UPPER(fault_type) != 'N/A'
            """,
            'Has time_of_restoration':     "SELECT COUNT(*) FROM technicalenergyfault WHERE time_of_restoration IS NOT NULL",
            'Missing time_of_restoration': "SELECT COUNT(*) FROM technicalenergyfault WHERE time_of_restoration IS NULL",
        }
        for label, sql in queries.items():
            try:
                cur.execute(sql)
                val = cur.fetchone()[0]
                self.stdout.write(f'  {label:<40}  {val:>12,}')
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'  {label}: ERROR — {exc}'))

    # ─────────────────────────────────────────────────────────────────────────
    # Check 6 — sample rows from technicalenergyfault
    # ─────────────────────────────────────────────────────────────────────────
    def _check6_sample_fault_rows(self, cur, limit, feeder_filter=None):
        self._header('CHECK 6 — Sample rows from technicalenergyfault')
        where = f"AND feeder_id = '{feeder_filter}'" if feeder_filter else ''
        for label, extra_where in [
            ('Most recent rows (any fault_type)',       ''),
            ("Rows where fault_type is NULL",           'AND fault_type IS NULL'),
            ("Rows where fault_type = 'N/A'",          "AND UPPER(COALESCE(fault_type,'')) = 'N/A'"),
            ("Rows with a non-null, non-N/A fault_type",
             "AND fault_type IS NOT NULL AND UPPER(fault_type) != 'N/A' AND fault_type != ''"),
        ]:
            self.stdout.write(f'\n  >> {label}')
            try:
                cur.execute(f"""
                    SELECT feeder_id, time_of_occurrence, time_of_restoration,
                           fault_type, description, load_interrupted_mw
                    FROM technicalenergyfault
                    WHERE 1=1 {where} {extra_where}
                    ORDER BY time_of_occurrence DESC
                    LIMIT {limit}
                """)
                rows = cur.fetchall()
                if not rows:
                    self.stdout.write('     (no rows)')
                    continue
                self.stdout.write(
                    f'  {"feeder_id":<25} {"occurred_at":<22} {"restored_at":<22} '
                    f'{"fault_type":<15} {"desc[:30]":<32} {"load_mw"}'
                )
                self.stdout.write('  ' + '-' * 130)
                for feeder_id, occ, rest, ft, desc, load in rows:
                    self.stdout.write(
                        f'  {str(feeder_id):<25} {str(occ):<22} {str(rest):<22} '
                        f'{str(ft):<15} {str(desc or "")[:30]:<32} {load}'
                    )
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'  ERROR: {exc}'))

    # ─────────────────────────────────────────────────────────────────────────
    # Check 7 — non-numeric LoadS values in Technicalhourlydata
    # ─────────────────────────────────────────────────────────────────────────
    def _check7_loads_nonnumeric(self, cur, limit, feeder_filter=None):
        self._header('CHECK 7 — Non-numeric LoadS values in Technicalhourlydata (old fault-type approach)')
        where = f"AND feeder_id = '{feeder_filter}'" if feeder_filter else ''
        try:
            # Distribution of non-numeric LoadS values
            cur.execute(f"""
                SELECT LoadS, COUNT(*) AS cnt
                FROM Technicalhourlydata
                WHERE LoadS IS NOT NULL
                  AND LoadS != ''
                  AND LoadS REGEXP '[^0-9.\\-]'
                  {where}
                GROUP BY LoadS
                ORDER BY cnt DESC
                LIMIT 60
            """)
            rows = cur.fetchall()
            self.stdout.write('  Non-numeric LoadS value distribution:')
            if not rows:
                self.stdout.write('  (none found — all LoadS values are numeric or empty)')
            else:
                self.stdout.write(f'  {"LoadS value":<35} {"count":>12}')
                self.stdout.write(f'  {"-"*35} {"-"*12}')
                for val, cnt in rows:
                    self.stdout.write(f'  {str(val):<35} {cnt:>12,}')

            # Sample non-numeric rows
            self.stdout.write(f'\n  Sample non-numeric LoadS rows (up to {limit}):')
            cur.execute(f"""
                SELECT feeder_id, Date, Hour_d, LoadS
                FROM Technicalhourlydata
                WHERE LoadS IS NOT NULL
                  AND LoadS != ''
                  AND LoadS REGEXP '[^0-9.\\-]'
                  {where}
                ORDER BY Date DESC, Hour_d DESC
                LIMIT {limit}
            """)
            rows = cur.fetchall()
            if not rows:
                self.stdout.write('  (none)')
            else:
                self.stdout.write(f'  {"feeder_id":<25} {"Date":<12} {"Hour":<6} {"LoadS"}')
                self.stdout.write('  ' + '-'*60)
                for feeder_id, date, hour, loads in rows:
                    self.stdout.write(f'  {str(feeder_id):<25} {str(date):<12} {str(hour):<6} {loads}')

        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'  ERROR: {exc}'))

    # ─────────────────────────────────────────────────────────────────────────
    # Check 8 — look for fault-type lookup tables and dump their contents
    # ─────────────────────────────────────────────────────────────────────────
    def _check8_fault_lookup_tables(self, cur):
        self._header('CHECK 8 — Fault-type lookup / reference tables')
        keywords = ('fault_type', 'fault_category', 'interruption_type',
                    'outage_type', 'fault_cause', 'faulttype', 'faulttypes',
                    'fault_types', 'interruption_cause')
        candidates = []
        for t in getattr(self, '_all_tables', []):
            if any(k in t.lower() for k in keywords):
                candidates.append(t)
            elif t.lower() in keywords:
                candidates.append(t)

        if not candidates:
            self.stdout.write('  No obvious fault-type lookup table found by name.')
            self.stdout.write('  Checking all tables with ≤ 200 rows that might be reference tables...')
            # Show small tables that could be lookup tables
            for t in getattr(self, '_all_tables', []):
                try:
                    cur.execute(f'SELECT COUNT(*) FROM `{t}`')
                    cnt = cur.fetchone()[0]
                    if 1 <= cnt <= 200:
                        self.stdout.write(f'    Small table (possible lookup): {t} — {cnt} rows')
                        candidates.append(t)
                except Exception:
                    pass
        else:
            self.stdout.write(f'  Found candidate tables: {candidates}')

        for t in candidates:
            self.stdout.write(f'\n  >> Full contents of `{t}`:')
            try:
                cur.execute(f'SELECT * FROM `{t}` LIMIT 200')
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
                self.stdout.write(f'  Columns: {cols}')
                for row in rows:
                    self.stdout.write(f'  {dict(zip(cols, row))}')
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'  ERROR reading {t}: {exc}'))

    # ─────────────────────────────────────────────────────────────────────────
    # Check 9 — feeder slug match rate
    # ─────────────────────────────────────────────────────────────────────────
    def _check9_feeder_slug_match(self, cur):
        self._header('CHECK 9 — Feeder slug match: Raven vs DataNest')
        try:
            cur.execute("SELECT DISTINCT feeder_id FROM technicalenergyfault LIMIT 2000")
            dn_fault_slugs = {row[0] for row in cur.fetchall()}

            cur.execute("SELECT DISTINCT feeder_id FROM Technicalhourlydata LIMIT 5000")
            dn_hourly_slugs = {row[0] for row in cur.fetchall()}

            raven_slugs = set(Feeder.objects.values_list('slug', flat=True))

            matched_fault   = dn_fault_slugs & raven_slugs
            unmatched_fault = dn_fault_slugs - raven_slugs

            matched_hourly   = dn_hourly_slugs & raven_slugs
            unmatched_hourly = dn_hourly_slugs - raven_slugs

            self.stdout.write(f'  Raven feeders total:                     {len(raven_slugs):>6}')
            self.stdout.write(f'  DataNest technicalenergyfault feeders:   {len(dn_fault_slugs):>6}')
            self.stdout.write(f'    matched in Raven:                      {len(matched_fault):>6}')
            self.stdout.write(f'    NOT in Raven (skipped by sync):        {len(unmatched_fault):>6}')
            if unmatched_fault:
                self.stdout.write(f'    Unmatched slugs: {sorted(unmatched_fault)[:30]}')

            self.stdout.write(f'\n  DataNest Technicalhourlydata feeders:   {len(dn_hourly_slugs):>6}')
            self.stdout.write(f'    matched in Raven:                      {len(matched_hourly):>6}')
            self.stdout.write(f'    NOT in Raven:                          {len(unmatched_hourly):>6}')

        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'  ERROR: {exc}'))

    # ─────────────────────────────────────────────────────────────────────────
    # Check 10 — date ranges
    # ─────────────────────────────────────────────────────────────────────────
    def _check10_date_ranges(self, cur):
        self._header('CHECK 10 — Date ranges in both tables')
        for table, col in [
            ('technicalenergyfault', 'time_of_occurrence'),
            ('Technicalhourlydata',  'Date'),
        ]:
            try:
                cur.execute(f'SELECT MIN(`{col}`), MAX(`{col}`) FROM `{table}`')
                mn, mx = cur.fetchone()
                self.stdout.write(f'  {table:<35}  {col}: {mn}  →  {mx}')
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'  {table}: ERROR — {exc}'))

    # ─────────────────────────────────────────────────────────────────────────
    # Deep-dive on a user-specified table
    # ─────────────────────────────────────────────────────────────────────────
    def _deep_dive_table(self, cur, table_name, limit):
        self._header(f'DEEP DIVE — {table_name}')
        try:
            cur.execute(f'DESCRIBE `{table_name}`')
            cols_info = cur.fetchall()
            self.stdout.write('  Schema:')
            for col in cols_info:
                self.stdout.write(f'    {col}')

            cur.execute(f'SELECT COUNT(*) FROM `{table_name}`')
            self.stdout.write(f'\n  Row count: {cur.fetchone()[0]:,}')

            cur.execute(f'SELECT * FROM `{table_name}` LIMIT {limit}')
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            self.stdout.write(f'\n  First {limit} rows:')
            self.stdout.write(f'  {cols}')
            for row in rows:
                self.stdout.write(f'  {list(row)}')
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'  ERROR: {exc}'))

    # ─────────────────────────────────────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────────────────────────────────────
    def _header(self, title):
        self.stdout.write('\n' + self.sep)
        self.stdout.write(self.style.SUCCESS(f'  {title}'))
        self.stdout.write(self.sep)
