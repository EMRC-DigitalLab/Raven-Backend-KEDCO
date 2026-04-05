"""
commercial/management/commands/diagnose_estimated_billing.py

Diagnoses why estimated billing numbers are inflated for a given month.
Traces bad values back to specific customers and readings.

Usage:
    python manage.py diagnose_estimated_billing
    python manage.py diagnose_estimated_billing --year 2026 --month 2
    python manage.py diagnose_estimated_billing --year 2026 --month 3
"""

from datetime import date, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.core.management.base import BaseCommand

from commercial.models import CommercialCustomer, MeterReading

VAT_RATE = Decimal("0.075")


class Command(BaseCommand):
    help = "Diagnose why estimated billing numbers are inflated for a given month."

    def add_arguments(self, parser):
        parser.add_argument("--year",  type=int, default=2026)
        parser.add_argument("--month", type=int, default=2)

    def handle(self, *args, **options):
        year  = options["year"]
        month = options["month"]

        start = date(year, month, 1)
        end   = min(start + relativedelta(months=1) - timedelta(days=1), date.today())
        days  = (end - start).days + 1

        self.stdout.write(
            f"\n{'='*65}\n  ESTIMATED BILLING DIAGNOSTIC — {start.strftime('%B %Y')} ({days} days)\n{'='*65}"
        )

        # ── 1. Coverage ──────────────────────────────────────────────────────
        total_customers = CommercialCustomer.objects.count()
        readings_in_period = MeterReading.objects.filter(
            reading_date__gte=start,
            reading_date__lte=end,
        )
        read_ids   = set(readings_in_period.values_list("customer_id", flat=True).distinct())
        unread_ids = list(
            CommercialCustomer.objects.exclude(id__in=read_ids).values_list("id", flat=True)
        )

        self.stdout.write(f"\n[1] COVERAGE")
        self.stdout.write(f"    Total customers  : {total_customers:,}")
        self.stdout.write(f"    Read this period : {len(read_ids):,}")
        self.stdout.write(f"    UNREAD           : {len(unread_ids):,}  ({round(len(unread_ids)/total_customers*100,1) if total_customers else 0}%)")

        # ── 2. Last readings for unread customers ────────────────────────────
        last_readings = list(
            MeterReading.objects
            .filter(
                customer_id__in=unread_ids,
                billed_consumption__gt=0,
                tariff_rate__isnull=False,
            )
            .order_by("customer_id", "-reading_date")
            .distinct("customer_id")
            .values(
                "customer_id",
                "billed_consumption",
                "tariff_rate",
                "reading_date",
                "external_id",
                "customer__feeder__name",
                "customer__feeder__band__name",
            )
        )

        no_history = len(unread_ids) - len(last_readings)
        self.stdout.write(f"\n[2] LAST READING AVAILABILITY (for unread customers)")
        self.stdout.write(f"    Have a last reading : {len(last_readings):,}")
        self.stdout.write(f"    No history at all   : {no_history:,}  (excluded from estimate)")

        if not last_readings:
            self.stdout.write("  No last readings found. Nothing to estimate.")
            return

        # ── 3. Age of last readings ──────────────────────────────────────────
        ages_days = sorted([(start - r["reading_date"]).days for r in last_readings])
        self.stdout.write(f"\n[3] AGE OF LAST READING (days before {start})")
        self.stdout.write(f"    Min    : {min(ages_days):,} days")
        self.stdout.write(f"    Max    : {max(ages_days):,} days")
        self.stdout.write(f"    Avg    : {round(sum(ages_days)/len(ages_days), 1):,} days")
        buckets = [
            ("FUTURE (negative)",  lambda d: d < 0),
            ("0–30 days",          lambda d: 0 <= d < 30),
            ("30–90 days",         lambda d: 30 <= d < 90),
            ("90–180 days",        lambda d: 90 <= d < 180),
            ("180–365 days",       lambda d: 180 <= d < 365),
            ("> 1 year",           lambda d: d >= 365),
        ]
        for label, fn in buckets:
            self.stdout.write(f"    {label:<22}: {sum(1 for d in ages_days if fn(d)):,}")

        # Show FUTURE readings (negative age) — these are post-period readings
        future = [r for r in last_readings if (start - r["reading_date"]).days < 0]
        if future:
            self.stdout.write(f"\n    !! {len(future)} readings are DATED AFTER {start} (future dates)")
            self.stdout.write(f"    These customers have readings in the period but were still")
            self.stdout.write(f"    picked up as 'unread' — suggests a coverage logic gap.")
            for r in future[:5]:
                self.stdout.write(
                    f"      customer_id={r['customer_id']} | reading_date={r['reading_date']} "
                    f"| billed_consumption={r['billed_consumption']} | feeder={r['customer__feeder__name']}"
                )

        # ── 4. billed_consumption breakdown ─────────────────────────────────
        consumptions = sorted([Decimal(str(r["billed_consumption"])) for r in last_readings])
        rates        = sorted([Decimal(str(r["tariff_rate"])) for r in last_readings])

        def _stats(vals):
            n = len(vals)
            return {
                "min":    round(vals[0], 2),
                "max":    round(vals[-1], 2),
                "avg":    round(sum(vals) / n, 2),
                "median": round(vals[n // 2], 2),
            }

        c = _stats(consumptions)
        r = _stats(rates)

        self.stdout.write(f"\n[4] LAST billed_consumption (kWh) — divided by 7 in formula")
        self.stdout.write(f"    Min    : {c['min']:>18,} kWh")
        self.stdout.write(f"    Max    : {c['max']:>18,} kWh")
        self.stdout.write(f"    Avg    : {c['avg']:>18,} kWh")
        self.stdout.write(f"    Median : {c['median']:>18,} kWh")

        self.stdout.write(f"\n[5] LAST tariff_rate (NGN/kWh)")
        self.stdout.write(f"    Min    : {r['min']:>15,}")
        self.stdout.write(f"    Max    : {r['max']:>15,}")
        self.stdout.write(f"    Avg    : {r['avg']:>15,}")
        self.stdout.write(f"    Median : {r['median']:>15,}")

        # ── 5. Suspicious values with customer details ───────────────────────
        negatives   = [r for r in last_readings if Decimal(str(r["billed_consumption"])) < 0]
        zeros       = [r for r in last_readings if Decimal(str(r["billed_consumption"])) == 0]
        very_large  = [r for r in last_readings if Decimal(str(r["billed_consumption"])) > 10_000]
        zero_rate   = [r for r in last_readings if Decimal(str(r["tariff_rate"])) == 0]

        self.stdout.write(f"\n[6] SUSPICIOUS VALUES BREAKDOWN")
        self.stdout.write(f"    Negative billed_consumption   : {len(negatives):,}")
        self.stdout.write(f"    Zero billed_consumption       : {len(zeros):,}")
        self.stdout.write(f"    billed_consumption > 10,000   : {len(very_large):,}")
        self.stdout.write(f"    Zero tariff_rate              : {len(zero_rate):,}")

        if negatives:
            self.stdout.write(f"\n    -- NEGATIVE billed_consumption (top 5 worst) --")
            negatives_sorted = sorted(negatives, key=lambda x: Decimal(str(x["billed_consumption"])))
            for r in negatives_sorted[:5]:
                self.stdout.write(
                    f"      customer_id={r['customer_id']}"
                    f" | reading_date={r['reading_date']}"
                    f" | consumption={r['billed_consumption']}"
                    f" | rate={r['tariff_rate']}"
                    f" | feeder={r['customer__feeder__name']}"
                    f" | band={r['customer__feeder__band__name']}"
                )

        if very_large:
            self.stdout.write(f"\n    -- VERY LARGE billed_consumption (top 5 biggest) --")
            very_large_sorted = sorted(very_large, key=lambda x: Decimal(str(x["billed_consumption"])), reverse=True)
            for r in very_large_sorted[:5]:
                self.stdout.write(
                    f"      customer_id={r['customer_id']}"
                    f" | reading_date={r['reading_date']}"
                    f" | consumption={r['billed_consumption']}"
                    f" | rate={r['tariff_rate']}"
                    f" | feeder={r['customer__feeder__name']}"
                    f" | band={r['customer__feeder__band__name']}"
                )

        # ── 6. Compute estimates and show top inflators ──────────────────────
        enriched = []
        est_total = Decimal("0")

        for row in last_readings:
            bc           = Decimal(str(row["billed_consumption"]))
            tr           = Decimal(str(row["tariff_rate"]))
            daily_kwh    = bc / 7
            daily_charge = daily_kwh * tr
            daily_total  = daily_charge * (1 + VAT_RATE)
            est          = daily_total * days
            est_total   += est
            enriched.append({**row, "estimate": est})

        enriched_sorted = sorted(enriched, key=lambda x: abs(x["estimate"]), reverse=True)
        avg_est = est_total / len(enriched) if enriched else Decimal("0")

        self.stdout.write(f"\n[7] ESTIMATED REVENUE — current formula ({days}-day period)")
        self.stdout.write(f"    Total estimated revenue : NGN {float(round(est_total, 2)):>22,.2f}")
        self.stdout.write(f"    Avg per unread customer : NGN {float(round(avg_est, 2)):>22,.2f}")

        self.stdout.write(f"\n    -- TOP 10 CUSTOMERS DRIVING THE TOTAL --")
        self.stdout.write(f"    {'customer_id':<38} {'reading_date':<14} {'consumption':>14} {'rate':>8} {'estimate NGN':>20} feeder / band")
        self.stdout.write(f"    {'-'*115}")
        for row in enriched_sorted[:10]:
            self.stdout.write(
                f"    {str(row['customer_id']):<38}"
                f" {str(row['reading_date']):<14}"
                f" {float(row['billed_consumption']):>14,.2f}"
                f" {float(row['tariff_rate']):>8,.2f}"
                f" {float(round(row['estimate'], 2)):>20,.2f}"
                f" {row['customer__feeder__name'] or 'N/A'} / {row['customer__feeder__band__name'] or 'N/A'}"
            )

        # ── 7. Sanity check ──────────────────────────────────────────────────
        est_daily = est_total / days if days else Decimal("0")
        self.stdout.write(f"\n[8] SANITY CHECK")
        self.stdout.write(f"    Daily equivalent (total / {days}) : NGN {float(round(est_daily, 2)):>22,.2f}")
        self.stdout.write(f"    The {days}-day multiplier amplifies every customer's number by {days}x.")
        self.stdout.write(f"    Any bad consumption value gets multiplied 4x (div7 * days * rate * vat).\n")

        self.stdout.write("  Diagnostic complete.\n")
