
# from django.db.models import Avg, Count
# from datetime import datetime, timedelta
# from dateutil.relativedelta import relativedelta # type: ignore

# from technical.models import HourlyLoad, FeederInterruption

# def get_month_range(year, month):
#     start = datetime(year, month, 1)
#     end = start + relativedelta(months=1) - timedelta(days=1)
#     return start.date(), end.date()


# def delta(current, previous):
#     if previous == 0:
#         return 0
#     return round(((current - previous) / previous) * 100, 2)


# def calculate_hours_of_supply(from_date, to_date):
#     hours = HourlyLoad.objects.filter(
#         date__range=(from_date, to_date),
#         load_mw__gt=0
#     ).values('feeder', 'date').annotate(
#         count=Count('hour')
#     ).aggregate(avg=Avg('count'))['avg'] or 0
#     return round(hours, 2)


# def get_avg_interruption_duration(from_date, to_date):
#     qs = FeederInterruption.objects.filter(
#         occurred_at__date__range=(from_date, to_date),
#         restored_at__isnull=False
#     )
#     total_hours = sum(i.duration_hours for i in qs)
#     count = qs.count()
#     return round(total_hours / count, 2) if count else 0
