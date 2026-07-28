from django.core.management.base import BaseCommand
from django.db.models import Count, Sum, Avg
from technical.models import HourlyLoad, DailyHoursOfSupply
import datetime


class Command(BaseCommand):
    help = 'Check 33KV data counts'

    def handle(self, *args, **options):
        today = datetime.date.today()
        month_start = today.replace(day=1)
        week_ago = today - datetime.timedelta(days=6)

        # ── HourlyLoad full month by day ──────────────────────────────────────
        self.stdout.write("=== HourlyLoad July 2026 by day (33kv) ===")
        for row in HourlyLoad.objects.filter(
            feeder__voltage_level='33kv', date__gte=month_start, date__lte=today
        ).values('date', 'submission_type').annotate(cnt=Count('id')).order_by('date', 'submission_type'):
            self.stdout.write(
                "  " + str(row['date']) + " | " + str(row['submission_type']) + ": " + str(row['cnt'])
            )

        # ── Today feeder breakdown ─────────────────────────────────────────────
        self.stdout.write("\n=== Today's 33kv feeders — hours logged ===")
        for row in HourlyLoad.objects.filter(
            feeder__voltage_level='33kv', date=today
        ).values('feeder__name', 'submission_type').annotate(
            hours=Count('id'), total_mw=Sum('load_mw')
        ).order_by('feeder__name'):
            self.stdout.write(
                "  " + str(row['feeder__name']) + " [" + str(row['submission_type']) + "]: " +
                str(row['hours']) + " hours | " + str(round(row['total_mw'] or 0, 1)) + " MW total"
            )

        self.stdout.write("\n=== 33kv feeders with NO data today ===")
        from technical.models import Feeder
        feeders_with_today = HourlyLoad.objects.filter(
            feeder__voltage_level='33kv', date=today
        ).values_list('feeder_id', flat=True).distinct()
        missing = Feeder.objects.filter(voltage_level='33kv').exclude(id__in=feeders_with_today)
        self.stdout.write("Count: " + str(missing.count()))
        for f in missing:
            self.stdout.write("  - " + str(f.name))
