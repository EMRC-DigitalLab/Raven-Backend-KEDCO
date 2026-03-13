from rest_framework.decorators import api_view
from rest_framework.response import Response

from commercial.models import SalesRepresentative
from commercial.serializers import SalesRepresentativeSerializer


@api_view(["GET"])
def list_sales_reps(request):
    reps = SalesRepresentative.objects.select_related(
        
    ).prefetch_related(
        'assigned_transformers'  # Adjust field name as needed
    ).all()

    reps = reps.order_by('name')

    data = SalesRepresentativeSerializer(reps, many=True).data
    return Response(data)