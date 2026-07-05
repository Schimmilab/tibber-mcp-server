import pytest

from tibber_mcp import server
from tibber_mcp.graphql import TibberApiError

HOME = {
    "id": "h1",
    "appNickname": "Zuhause",
    "address": {
        "address1": "Musterweg 1",
        "postalCode": "70173",
        "city": "Stuttgart",
        "country": "DE",
    },
    "features": {"realTimeConsumptionEnabled": True},
    "meteringPointData": {
        "consumptionEan": "DE0001",
        "gridCompany": "Netze BW",
        "estimatedAnnualConsumption": 3500,
    },
    "currentSubscription": {"status": "running"},
}


@pytest.fixture
def homes(monkeypatch):
    async def fake_get_homes():
        return [HOME]

    monkeypatch.setattr(server.graphql, "get_homes", fake_get_homes)


async def test_get_home_info(homes):
    result = await server.get_home_info()
    assert result == [
        {
            "home_id": "h1",
            "name": "Zuhause",
            "address": "Musterweg 1, 70173 Stuttgart",
            "has_pulse": True,
            "meter_ean": "DE0001",
            "grid_company": "Netze BW",
            "estimated_annual_kwh": 3500,
            "subscription_status": "running",
        }
    ]


async def test_resolve_home_id_defaults_to_first(homes):
    assert await server.resolve_home_id(None) == "h1"


async def test_resolve_home_id_rejects_unknown(homes):
    with pytest.raises(TibberApiError, match="h1"):
        await server.resolve_home_id("does-not-exist")
