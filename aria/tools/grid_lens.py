from datetime import datetime

from django.db.models import Avg, Count, Q, Sum


def _parse(d: str):
    return datetime.strptime(d, '%Y-%m-%d').date()


def query_grid_lens(start_date: str, end_date: str) -> dict:
    """Return GridLens loss decomposition metrics."""
    from analytics.models import MonthlyOverviewSummary

    start, end = _parse(start_date), _parse(end_date)

    rows = MonthlyOverviewSummary.objects.filter(month__gte=start, month__lte=end)
    count = rows.count()
    if not count:
        return {'period': {'start': start_date, 'end': end_date}, 'records': 0, 'message': 'No overview data for this period.'}

    # Pull all numeric fields dynamically
    numeric_fields = [
        f.name for f in MonthlyOverviewSummary._meta.get_fields()
        if hasattr(f, 'get_internal_type') and f.get_internal_type() in ('DecimalField', 'FloatField')
    ]
    agg = rows.aggregate(**{f: Sum(f) for f in numeric_fields})

    summary = {}
    for k, v in agg.items():
        if v is not None:
            summary[k] = round(float(v), 4)

    return {
        'period': {'start': start_date, 'end': end_date},
        'records': count,
        'aggregated_metrics': summary,
    }
