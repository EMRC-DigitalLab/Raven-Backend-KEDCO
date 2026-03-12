from datetime import datetime

from dateutil.relativedelta import relativedelta


def get_month_range_from_request(request):
    month = int(request.GET.get("month", 0))
    year = int(request.GET.get("year", 0))

    if not month or not year:
        today = datetime.today()
        month = today.month
        year = today.year

    start_date = datetime(year, month, 1)
    end_date = start_date + relativedelta(months=1) - relativedelta(days=1)

    return start_date.date(), end_date.date()
