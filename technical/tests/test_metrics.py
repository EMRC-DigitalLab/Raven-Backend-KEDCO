from datetime import date, datetime, timedelta

import pytest

from common.models import BusinessDistrict, Feeder, InjectionSubstation, State
from technical.models import (
    DailyHoursOfSupply,
    EnergyDelivered,
    FeederInterruption,
    HourlyLoad,
)


@pytest.mark.django_db
def test_average_hours_of_supply(api_client):
    state = State.objects.create(name="Lagos", slug="lagos")
    district = BusinessDistrict.objects.create(name="Ikeja", state=state, slug="ikeja")
    substation = InjectionSubstation.objects.create(name="SS", district=district, slug="ss")
    feeder = Feeder.objects.create(name="F1", substation=substation, slug="f1")

    DailyHoursOfSupply.objects.create(feeder=feeder, date=date(2025, 3, 1), hours_supplied=12)
    DailyHoursOfSupply.objects.create(feeder=feeder, date=date(2025, 3, 2), hours_supplied=14)

    response = api_client.get('/api/metrics/technical-summary/?district=ikeja&date_from=2025-03-01&date_to=2025-03-02')
    assert response.status_code == 200
    assert response.data['average_hours_of_supply'] == 13.0


@pytest.mark.django_db
def test_peak_load(api_client):
    feeder = Feeder.objects.create(name="F2", slug="f2", substation_id=1)
    HourlyLoad.objects.create(feeder=feeder, date=date(2025, 3, 1), hour=10, load_mw=5.0)
    HourlyLoad.objects.create(feeder=feeder, date=date(2025, 3, 1), hour=11, load_mw=8.0)

    response = api_client.get('/api/metrics/technical-summary/?feeder=f2&date=2025-03-01')
    assert response.status_code == 200
    assert response.data['peak_load'] == 8.0
