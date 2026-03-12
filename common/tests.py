import pytest

from common.models import *


@pytest.mark.django_db
def test_location_hierarchy():
    state = State.objects.create(name="Lagos")
    district = BusinessDistrict.objects.create(name="Ikeja", state=state)
    substation = InjectionSubstation.objects.create(name="Ikeja SS", district=district)
    feeder = Feeder.objects.create(name="Feeder 1", substation=substation)
    transformer = DistributionTransformer.objects.create(name="Transformer A", feeder=feeder)

    assert transformer.feeder == feeder
    assert feeder.substation == substation
    assert substation.district == district
    assert district.state == state
