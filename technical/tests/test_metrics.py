from datetime import date

import pytest

from common.models import Band, Feeder, InjectionSubstation, State
from technical.models import DailyHoursOfSupply, HourlyLoad


@pytest.mark.django_db
def test_average_hours_of_supply():
    state = State.objects.create(name="Lagos", slug="lagos")
    substation = InjectionSubstation.objects.create(name="SS", state=state, slug="ss")
    band = Band.objects.create(name="A")
    feeder = Feeder.objects.create(
        name="F1", substation=substation, band=band, voltage_level="11kv", slug="f1"
    )

    DailyHoursOfSupply.objects.create(feeder=feeder, date=date(2025, 3, 1), hours_supplied=12)
    DailyHoursOfSupply.objects.create(feeder=feeder, date=date(2025, 3, 2), hours_supplied=14)

    records = DailyHoursOfSupply.objects.filter(feeder=feeder)
    avg = sum(r.hours_supplied for r in records) / records.count()
    assert avg == 13.0


@pytest.mark.django_db
def test_peak_load():
    state = State.objects.create(name="Kano", slug="kano")
    substation = InjectionSubstation.objects.create(name="SS2", state=state, slug="ss2")
    band = Band.objects.create(name="B")
    feeder = Feeder.objects.create(
        name="F2", substation=substation, band=band, voltage_level="11kv", slug="f2"
    )

    HourlyLoad.objects.create(feeder=feeder, date=date(2025, 3, 1), hour=10, load_mw=5.0)
    HourlyLoad.objects.create(feeder=feeder, date=date(2025, 3, 1), hour=11, load_mw=8.0)

    peak = HourlyLoad.objects.filter(feeder=feeder, date=date(2025, 3, 1)).order_by("-load_mw").first()
    assert peak.load_mw == 8.0
