from datetime import datetime, timedelta

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


async def test_get_home_info_tolerates_null_fields(monkeypatch):
    async def fake_get_homes():
        return [{"id": "h2", "appNickname": None, "address": None,
                 "features": None, "meteringPointData": None,
                 "currentSubscription": None}]
    monkeypatch.setattr(server.graphql, "get_homes", fake_get_homes)
    result = await server.get_home_info()
    assert result[0]["home_id"] == "h2"
    assert result[0]["has_pulse"] is False
    assert result[0]["meter_ean"] is None


async def test_tool_error_reaches_mcp_client(monkeypatch):
    async def fake_get_homes():
        return []
    monkeypatch.setattr(server.graphql, "get_homes", fake_get_homes)
    from fastmcp.exceptions import ToolError
    with pytest.raises(ToolError, match="Kein Home"):
        await server.mcp.call_tool("get_home_info", {})


def _today_prices() -> list[dict]:
    """24 Stundenpreise für heute: 20 ct um 0 Uhr, +1 ct pro Stunde."""
    start = datetime.now(server.LOCAL_TZ).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return [
        {
            "startsAt": (start + timedelta(hours=h)).isoformat(),
            "total": round(0.20 + h * 0.01, 4),
            "level": "NORMAL",
        }
        for h in range(24)
    ]


@pytest.fixture
def price_info(monkeypatch):
    today = _today_prices()
    info = {"current": None, "today": today, "tomorrow": []}
    # current = Eintrag der aktuellen Stunde
    now_hour = datetime.now(server.LOCAL_TZ).hour
    info["current"] = today[now_hour]

    async def fake_get_price_info(home_id):
        return info

    monkeypatch.setattr(server.graphql, "get_price_info", fake_get_price_info)
    return info


async def test_get_current_price(homes, price_info):
    result = await server.get_current_price()
    expected_ct = round(price_info["current"]["total"] * 100, 2)
    assert result["price_ct_kwh"] == expected_ct
    assert result["level"] == "NORMAL"
    assert "günstigste" in result["rank_today"]


async def test_get_price_forecast_tomorrow_missing(homes, price_info):
    result = await server.get_price_forecast()
    assert result["tomorrow_available"] is False
    assert "13:00" in result["note"]
    assert len(result["today"]["hours"]) == 24
    assert result["today"]["min_ct_kwh"] == 20.0
    assert result["today"]["max_ct_kwh"] == 43.0


async def test_get_current_price_without_current_entry(homes, price_info, monkeypatch):
    info = dict(price_info)
    info["current"] = None

    async def fake_get_price_info(home_id):
        return info

    monkeypatch.setattr(server.graphql, "get_price_info", fake_get_price_info)
    with pytest.raises(TibberApiError, match="aktueller Preis"):
        await server.get_current_price()
