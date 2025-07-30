from datetime import date
from dateutil.relativedelta import relativedelta  # type: ignore
from rest_framework.decorators import api_view
from rest_framework.response import Response
from commercial.models import *
from commercial.serializers import *
from common.models import Feeder
from .utils import calculate_atcc_metrics


@api_view(["GET"])
def feeder_performance_view(request):
    year = int(request.query_params.get("year", date.today().year))
    month = int(request.query_params.get("month", date.today().month))
    start_date = date(year, month, 1)
    end_date = start_date + relativedelta(months=1)

    feeders = Feeder.objects.all()
    feeder_data = []

    for feeder in feeders:
        metrics = calculate_atcc_metrics(feeder, start_date, end_date)
        feeder_data.append(metrics)

    sorted_by_atcc = sorted(feeder_data, key=lambda x: x["atcc"])
    return Response({
        "top_5": sorted_by_atcc[:5],
        "bottom_5": sorted_by_atcc[-5:][::-1]
    })
