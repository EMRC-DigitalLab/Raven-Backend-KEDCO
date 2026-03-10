from common.models import Feeder
from commercial.models import Customer
from common.models import BusinessDistrict, State

def get_filtered_feeders(request):
    filters = {}

    if 'business_district' in request.GET:
        district_name = request.GET.get('business_district')
        try:
            district = BusinessDistrict.objects.get(name__iexact=district_name)
            filters['business_district'] = district
        except BusinessDistrict.DoesNotExist:
            return Feeder.objects.none()

    elif 'state' in request.GET:
        state_name = request.GET.get('state')
        try:
            state = State.objects.get(name__iexact=state_name)
            filters['business_district__state'] = state
        except State.DoesNotExist:
            return Feeder.objects.none()

    # ✅ Voltage level filter — applies to ALL ViewSets that call this helper
    feeder_type = request.GET.get('feeder_type', '')
    if feeder_type in ('11kv', '33kv'):
        filters['voltage_level'] = feeder_type

    return Feeder.objects.filter(**filters).distinct()



def get_filtered_customers(request):
    customers = Customer.objects.all()

    # Location filters
    state = request.GET.get('state')
    district = request.GET.get('district')
    substation = request.GET.get('substation')
    feeder = request.GET.get('feeder')
    transformer = request.GET.get('transformer')
    band = request.GET.get('band')

    if transformer:
        customers = customers.filter(transformer__slug=transformer)
    elif feeder:
        customers = customers.filter(transformer__feeder__slug=feeder)
    elif substation:
        customers = customers.filter(transformer__feeder__substation__slug=substation)
    elif district:
        customers = customers.filter(transformer__feeder__substation__district__slug=district)
    elif state:
        customers = customers.filter(transformer__feeder__substation__district__state__slug=state)

    if band:
        customers = customers.filter(band__slug=band)

    # Other filters
    category = request.GET.get('category')
    if category:
        customers = customers.filter(category=category)

    metering_type = request.GET.get('metering_type')
    if metering_type:
        customers = customers.filter(metering_type=metering_type)

    # Date filtering
    joined_date = request.GET.get('joined_date')
    joined_from = request.GET.get('joined_from')
    joined_to = request.GET.get('joined_to')

    if joined_date:
        customers = customers.filter(joined_date=joined_date)
    elif joined_from and joined_to:
        customers = customers.filter(joined_date__range=(joined_from, joined_to))
    elif joined_from:
        customers = customers.filter(joined_date__gte=joined_from)
    elif joined_to:
        customers = customers.filter(joined_date__lte=joined_to)

    return customers
