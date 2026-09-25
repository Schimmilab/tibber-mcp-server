"""15-Minuten-Raster (priceInfo(resolution: QUARTER_HOURLY)): 96 Preise pro Tag."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from tibber_mcp import analysis, graphql, server
from tibber_mcp.cache import seconds_until_next_quarter
from tibber_mcp.graphql import TibberApiError

TZ = ZoneInfo("Europe/Berlin")


def _viertel(start: datetime, totals: list[float]) -> list[dict]:
    return [
        {"startsAt": (start + timedelta(minutes=15 * i)).isoformat(), "total": t, "level": "NORMAL"}
        for i, t in enumerate(totals)
    ]


# --- cache / graphql -------------------------------------------------------------------


def test_seconds_until_next_quarter():
    assert seconds_until_next_quarter(datetime(2026, 9, 25, 13, 40, 0, tzinfo=TZ)) == 5 * 60
    assert seconds_until_next_quarter(datetime(2026, 9, 25, 13, 45, 0, tzinfo=TZ)) == 15 * 60


def _price_response(n: int) -> dict:
    today = _viertel(datetime(2026, 9, 25, tzinfo=TZ), [0.30] * n)
    return {"data": {"viewer": {"home": {"currentSubscription": {"priceInfo": {
        "current": today[0], "today": today, "tomorrow": []}}}}}}


@respx.mock
async def test_get_price_info_schickt_aufloesung_und_cacht_getrennt():
    route = respx.post(graphql.API_URL).mock(
        side_effect=lambda req: httpx.Response(
            200, json=_price_response(96 if b"QUARTER_HOURLY" in req.content else 24)
        )
    )
    viertel = await graphql.get_price_info("h1", "QUARTER_HOURLY")
    stunden = await graphql.get_price_info("h1")
    assert len(viertel["today"]) == 96 and len(stunden["today"]) == 24
    assert route.call_count == 2, "Stunden- und Viertelstundenraster dürfen sich den Cache nicht teilen"
    await graphql.get_price_info("h1", "QUARTER_HOURLY")
    assert route.call_count == 2


async def test_get_price_info_lehnt_unbekannte_aufloesung_ab():
    with pytest.raises(TibberApiError, match="QUARTER_HOURLY"):
        await graphql.get_price_info("h1", "MINUTELY")


# --- analysis --------------------------------------------------------------------------


def test_price_context_trifft_die_laufende_viertelstunde():
    today = _viertel(datetime(2026, 9, 25, tzinfo=TZ), [0.40, 0.10, 0.30, 0.20])
    now = datetime(2026, 9, 25, 0, 20, tzinfo=TZ)  # zweite Viertelstunde
    ctx = analysis.price_context(today, now, slot=timedelta(minutes=15))
    assert ctx["rank_today"] == 1
    # Ohne Slotlänge (Stundenannahme) würde 00:20 dem 00:00-Eintrag zugeordnet


# --- Tools -----------------------------------------------------------------------------


@pytest.fixture
def viertel_info(monkeypatch, homes):
    start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    # 96 Viertelstunden, steigend, außer einer billigen Senke 02:15–02:45 (3 Slots)
    totals = [0.20 + i * 0.001 for i in range(96)]
    for i in (9, 10, 11):
        totals[i] = 0.05
    today = _viertel(start, totals)
    now = datetime.now(TZ)
    idx = (now.hour * 60 + now.minute) // 15
    info = {"current": today[idx], "today": today, "tomorrow": []}
    calls = []

    async def fake(home_id, resolution="HOURLY"):
        calls.append(resolution)
        return info

    monkeypatch.setattr(server.graphql, "get_price_info", fake)
    return {"info": info, "calls": calls, "idx": idx}


@pytest.fixture
def homes(monkeypatch):
    async def fake_get_homes():
        return [{"id": "h1"}]

    monkeypatch.setattr(server.graphql, "get_homes", fake_get_homes)


async def test_current_price_viertelstunde(viertel_info):
    r = await server.get_current_price(resolution="QUARTER_HOURLY")
    assert viertel_info["calls"] == ["QUARTER_HOURLY"]
    assert r["starts_at"] == viertel_info["info"]["today"][viertel_info["idx"]]["startsAt"]
    assert "von 96 Viertelstunden" in r["rank_today"]
    assert r["resolution"] == "QUARTER_HOURLY"


async def test_forecast_viertelstunde_liefert_96_werte(viertel_info):
    r = await server.get_price_forecast(resolution="QUARTER_HOURLY")
    assert len(r["today"]["hours"]) == 96 and r["resolution"] == "QUARTER_HOURLY"
    assert r["today"]["cheapest_hour"] == viertel_info["info"]["today"][9]["startsAt"]


async def test_cheapest_dreiviertelstunde_findet_die_senke(viertel_info):
    r = await server.find_cheapest_hours(duration_hours=0.75, window="today", resolution="QUARTER_HOURLY")
    today = viertel_info["info"]["today"]
    assert r["start_hours"] == [today[9]["startsAt"], today[10]["startsAt"], today[11]["startsAt"]]
    assert r["average_price_ct_kwh"] == 5.0 and r["slot_minutes"] == 15


async def test_cheapest_next_24h_viertelstunde_hat_96_slots(viertel_info):
    # Fenster = ab laufender Viertelstunde, höchstens 96 Slots; die Senke (02:15) liegt
    # heute ggf. in der Vergangenheit — geprüft wird nur, dass das Fenster in Viertelstunden rechnet.
    r = await server.find_cheapest_hours(duration_hours=1, resolution="QUARTER_HOURLY")
    assert len(r["start_hours"]) == 4
    t = [datetime.fromisoformat(s) for s in r["start_hours"]]
    assert all(b - a == timedelta(minutes=15) for a, b in zip(t, t[1:]))


async def test_stundenraster_bleibt_default(viertel_info):
    await server.get_price_forecast()
    assert viertel_info["calls"] == ["HOURLY"]


async def test_bruchteil_im_stundenraster_rundet_auf(homes, monkeypatch):
    start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    today = [
        {"startsAt": (start + timedelta(hours=h)).isoformat(), "total": 0.2 + h * 0.01, "level": "NORMAL"}
        for h in range(24)
    ]

    async def fake(home_id, resolution="HOURLY"):
        return {"current": today[0], "today": today, "tomorrow": []}

    monkeypatch.setattr(server.graphql, "get_price_info", fake)
    r = await server.find_cheapest_hours(duration_hours=1.5, window="today")
    assert len(r["start_hours"]) == 2, "1,5 h belegen im Stundenraster zwei Stunden"


async def test_ungueltige_aufloesung_im_tool(viertel_info):
    with pytest.raises(TibberApiError, match="resolution"):
        await server.get_price_forecast(resolution="DAILY")


async def test_next_24h_viertelstunde_reicht_wirklich_24_stunden(homes, monkeypatch):
    # Die Senke liegt 10 h in der Zukunft — ein auf 24 Einträge gekapptes Fenster
    # (Stundenlogik im Viertelstundenraster = nur 6 h) würde sie nie sehen.
    start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    alle = _viertel(start, [0.30] * 192)
    ziel = (datetime.now(TZ) + timedelta(hours=10) - start) // timedelta(minutes=15)
    alle[ziel]["total"] = 0.01

    async def fake(home_id, resolution="HOURLY"):
        return {"current": alle[0], "today": alle[:96], "tomorrow": alle[96:]}

    monkeypatch.setattr(server.graphql, "get_price_info", fake)
    r = await server.find_cheapest_hours(duration_hours=0.25, resolution="QUARTER_HOURLY")
    assert r["start_hours"] == [alle[ziel]["startsAt"]]
