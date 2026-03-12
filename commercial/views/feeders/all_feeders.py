# commercial/views/feeders/all_feeders.py
from datetime import date

from dateutil.relativedelta import relativedelta  # type: ignore
from django.db.models import Q
from rest_framework.decorators import api_view
from rest_framework.response import Response

from commercial.models import *
from commercial.serializers import *
from common.models import Feeder, State

from .utils import calculate_atcc_metrics


@api_view(["GET"])
def feeders_by_location_view(request):
    state_name = request.query_params.get("state")
    district_name = request.query_params.get("business_district")
    year = int(request.query_params.get("year", date.today().year))
    month = int(request.query_params.get("month", date.today().month))
    start_date = date(year, month, 1)
    end_date = start_date + relativedelta(months=1)

    filters = Q()

    if district_name:
        filters = Q(business_district__name__iexact=district_name)
    elif state_name:
        state = State.objects.filter(name__iexact=state_name).first()
        if not state:
            return Response({"error": "Invalid state"}, status=400)
        filters = Q(business_district__state=state)

    feeders = Feeder.objects.filter(filters)
    result = []

    for feeder in feeders:
        metrics = calculate_atcc_metrics(feeder, start_date, end_date)

        result.append({
            "name": feeder.name,
            "slug": feeder.slug,
            "voltage_level": feeder.voltage_level,
            "business_district": {
                "name": feeder.business_district.name if feeder.business_district else None,
                "slug": feeder.business_district.slug if feeder.business_district else None,
            },
            **metrics  # Unpack and merge the calculated metrics directly into the top-level dict
        })

    return Response(result)