"""
energy_account/views/periods/available_periods.py

GET /api/energy-account/periods/

Returns every billing period that has at least one EAMonthlyReturn record,
ordered most-recent first.  The frontend should call this first to know which
year/month params to pass to all other EA endpoints.
"""
import calendar

from django.db.models import Count
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from energy_account.permissions import HasEAAccess
from energy_account.models import EAMonthlyReturn


@api_view(['GET'])
@permission_classes([HasEAAccess])
def available_periods(request):
    """
    GET /api/energy-account/periods/

    Returns all billing periods (year + month) that have EA data, plus the
    station count for each.  Ordered most-recent first.

    Response:
        {
            "latest": { "year": 2024, "month": 3, "label": "March 2024" },
            "periods": [
                { "year": 2024, "month": 3, "label": "March 2024", "station_count": 42 },
                ...
            ]
        }
    """
    rows = (
        EAMonthlyReturn.objects
        .values('year', 'month')
        .annotate(station_count=Count('id'))
        .order_by('-year', '-month')
    )

    periods = [
        {
            'year':          row['year'],
            'month':         row['month'],
            'label':         f"{calendar.month_name[row['month']]} {row['year']}",
            'station_count': row['station_count'],
        }
        for row in rows
    ]

    latest = periods[0] if periods else None

    return Response({
        'latest':  latest,
        'periods': periods,
    })
