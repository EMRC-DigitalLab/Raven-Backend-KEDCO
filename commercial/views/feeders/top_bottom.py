# commercial/views/feeders/top_bottom.py
from rest_framework.decorators import api_view
from rest_framework.response import Response

from commercial.analytics_utils import parse_date_range
from common.models import BusinessDistrict, Feeder, State

from .utils import calculate_atcc_metrics


@api_view(["GET"])
def feeder_performance_view(request):
    date_range   = parse_date_range(request)
    start_date   = date_range['start_date']
    end_date     = date_range['end_date']
    state_name   = request.query_params.get("state")
    district_name = request.query_params.get("business_district")

    feeders_qs = Feeder.objects.select_related(
        'business_district',
        'business_district__state',
        'substation',
    )

    location_info = {'type': 'all', 'name': 'All Feeders'}

    if district_name:
        try:
            district = BusinessDistrict.objects.select_related('state').get(
                name__iexact=district_name
            )
            feeders_qs    = feeders_qs.filter(business_district=district)
            location_info = {'type': 'business_district', 'name': district.name,
                             'state': district.state.name}
        except BusinessDistrict.DoesNotExist:
            return Response({'error': f"Business district '{district_name}' not found"}, status=400)

    elif state_name:
        try:
            state         = State.objects.get(name__iexact=state_name)
            feeders_qs    = feeders_qs.filter(business_district__state=state)
            location_info = {'type': 'state', 'name': state.name}
        except State.DoesNotExist:
            return Response({'error': f"State '{state_name}' not found"}, status=400)

    feeders = list(feeders_qs)
    if not feeders:
        return Response({'error': 'No feeders found for the specified criteria'}, status=404)

    feeder_data = [calculate_atcc_metrics(f, start_date, end_date) for f in feeders]

    sorted_by_atcc     = sorted(feeder_data, key=lambda x: x['atcc'])
    top_5_performers   = sorted_by_atcc[:5]
    bottom_5_performers = sorted_by_atcc[-5:][::-1]

    return Response({
        'period': {
            'mode':       date_range['mode'],
            'start_date': str(start_date),
            'end_date':   str(end_date),
            'label':      date_range['label'],
        },
        'location':  location_info,
        'top_5':     top_5_performers,
        'bottom_5':  bottom_5_performers,
    })
