from rest_framework.response import Response
from rest_framework.decorators import api_view
from financial.utils import get_financial_feeder_data

@api_view(['GET'])
def financial_feeder_view(request):
    """
    Returns feeder-level financial metrics filtered by state or business district and date.
    Business district filter takes precedence.
    """
    data = get_financial_feeder_data(request)
    return Response(data)