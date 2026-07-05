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


async def test_get_price_forecast_ignores_null_totals(homes, price_info, monkeypatch):
    info = dict(price_info)
    info["today"] = list(info["today"]) + [
        {"startsAt": "2099-01-01T00:00:00.000+01:00", "total": None, "level": None}
    ]

    async def fake_get_price_info(home_id):
        return info

    monkeypatch.setattr(server.graphql, "get_price_info", fake_get_price_info)
    result = await server.get_price_forecast()
    assert len(result["today"]["hours"]) == 24  # None-Eintrag gefiltert


async def test_get_price_forecast_with_tomorrow(homes, price_info, monkeypatch):
    info = dict(price_info)
    info["tomorrow"] = [
        {"startsAt": "2026-07-06T00:00:00.000+02:00", "total": 0.15, "level": "CHEAP"},
        {"startsAt": "2026-07-06T01:00:00.000+02:00", "total": 0.25, "level": "NORMAL"},
    ]

    async def fake_get_price_info(home_id):
        return info

    monkeypatch.setattr(server.graphql, "get_price_info", fake_get_price_info)
    result = await server.get_price_forecast()
    assert result["tomorrow_available"] is True
    assert result["tomorrow"]["min_ct_kwh"] == 15.0
    assert result["tomorrow"]["cheapest_hour"] == "2026-07-06T00:00:00.000+02:00"
    assert "note" not in result


async def test_find_cheapest_hours_today(homes, price_info):
    result = await server.find_cheapest_hours(duration_hours=2, window="today")
    # Preise steigen monoton → günstigster 2h-Block ist 0-2 Uhr
    assert result["start_hours"][0] == price_info["today"][0]["startsAt"]
    assert result["average_price_ct_kwh"] == 20.5
    assert result["savings_vs_window_average_pct"] > 0


async def test_find_cheapest_hours_tomorrow_not_published(homes, price_info):
    with pytest.raises(TibberApiError, match="13:00"):
        await server.find_cheapest_hours(duration_hours=2, window="tomorrow")


async def test_find_cheapest_hours_invalid_window(homes, price_info):
    with pytest.raises(TibberApiError, match="window"):
        await server.find_cheapest_hours(duration_hours=2, window="yesterday")


async def test_find_cheapest_hours_next_24h_default(homes, price_info):
    now_hour = datetime.now(server.LOCAL_TZ).hour
    result = await server.find_cheapest_hours(duration_hours=1)
    # Monoton steigende Preise → günstigste Zukunfts-Stunde ist die laufende Stunde
    assert result["window"] == "next_24h"
    assert result["start_hours"] == [price_info["today"][now_hour]["startsAt"]]


async def test_find_cheapest_hours_non_contiguous(homes, price_info):
    result = await server.find_cheapest_hours(
        duration_hours=2, window="today", contiguous=False
    )
    assert result["start_hours"] == [
        price_info["today"][0]["startsAt"],
        price_info["today"][1]["startsAt"],
    ]
    assert result["average_price_ct_kwh"] == 20.5


async def test_find_cheapest_hours_duration_too_long(homes, price_info):
    with pytest.raises(TibberApiError, match="länger"):
        await server.find_cheapest_hours(duration_hours=25, window="today")


CONSUMPTION_NODES = [
    {
        "from": "2026-07-04T00:00:00.000+02:00",
        "to": "2026-07-05T00:00:00.000+02:00",
        "consumption": 9.512,
        "cost": 2.7183,
        "unitPrice": 0.2858,
    },
    {
        "from": "2026-07-05T00:00:00.000+02:00",
        "to": "2026-07-06T00:00:00.000+02:00",
        "consumption": None,
        "cost": None,
        "unitPrice": None,
    },
]


@pytest.fixture
def consumption(monkeypatch):
    captured = {}

    async def fake_get_consumption(home_id, resolution, last):
        captured["args"] = (home_id, resolution, last)
        return CONSUMPTION_NODES

    monkeypatch.setattr(server.graphql, "get_consumption", fake_get_consumption)
    return captured


async def test_get_consumption_formats_output(homes, consumption):
    result = await server.get_consumption(resolution="DAILY", last=2)
    assert result[0] == {
        "from": "2026-07-04T00:00:00.000+02:00",
        "to": "2026-07-05T00:00:00.000+02:00",
        "kwh": 9.51,
        "cost_eur": 2.72,
        "avg_price_ct_kwh": 28.58,
    }
    assert result[1]["kwh"] is None


async def test_get_consumption_rejects_bad_resolution(homes, consumption):
    with pytest.raises(TibberApiError, match="resolution"):
        await server.get_consumption(resolution="MINUTELY", last=2)


async def test_get_consumption_report_month(homes, monkeypatch):
    from datetime import date, timedelta as td

    today = datetime.now(server.LOCAL_TZ).date()
    cur_month_start = today.replace(day=1)
    prev_month_start = (cur_month_start - td(days=1)).replace(day=1)
    nodes = [
        {
            "from": f"{prev_month_start.isoformat()}T00:00:00.000+02:00",
            "to": "x",
            "consumption": 10.0,
            "cost": 3.0,
            "unitPrice": 0.3,
        },
        {
            "from": f"{cur_month_start.isoformat()}T00:00:00.000+02:00",
            "to": "x",
            "consumption": 8.0,
            "cost": 2.0,
            "unitPrice": 0.25,
        },
    ]

    async def fake_get_consumption(home_id, resolution, last):
        assert resolution == "DAILY"
        return nodes

    monkeypatch.setattr(server.graphql, "get_consumption", fake_get_consumption)
    report = await server.get_consumption_report(period="month", offset=0)
    assert report["current"]["kwh"] == 8.0
    assert report["previous"]["kwh"] == 10.0
    assert report["change_vs_previous"]["kwh_pct"] == -20.0


async def test_get_consumption_report_rejects_bad_period(homes):
    with pytest.raises(TibberApiError, match="period"):
        await server.get_consumption_report(period="quarter")


async def test_get_consumption_report_rejects_negative_offset(homes):
    with pytest.raises(TibberApiError, match="offset"):
        await server.get_consumption_report(period="month", offset=-1)
