import pytest

from common.models import *


@pytest.mark.django_db
def test_location_hierarchy():
    state = State.objects.create(name="Lagos")
    district = BusinessDistrict.objects.create(name="Ikeja", state=state)
    substation = InjectionSubstation.objects.create(name="Ikeja SS", state=state)
    band = Band.objects.create(name="A")
    feeder = Feeder.objects.create(
        name="Feeder 1", substation=substation, band=band, voltage_level="11kv"
    )
    transformer = DistributionTransformer.objects.create(name="Transformer A", feeder=feeder)

    assert transformer.feeder == feeder
    assert feeder.substation == substation
    assert substation.state == state
    assert district.state == state
