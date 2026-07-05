"""Tibber MCP Server — Tool-Schicht. Kein Business-Code, nur Orchestrierung + Formatierung."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastmcp import FastMCP

from tibber_mcp import analysis, graphql
from tibber_mcp.graphql import TibberApiError

LOCAL_TZ = ZoneInfo("Europe/Berlin")

mcp = FastMCP(
    "tibber",
    instructions=(
        "Zugriff auf Tibber-Stromdaten: Preise mit Forecast, Verbrauch/Kosten, "
        "günstigste Stunden für Verbraucher, Pulse-Live-Messung. "
        "Preise in ct/kWh, Summen in EUR, Zeiten in Europe/Berlin."
    ),
)


async def resolve_home_id(home_id: str | None) -> str:
    """Validiert eine home_id bzw. liefert das erste Home als Default."""
    homes = await graphql.get_homes()
    if not homes:
        raise TibberApiError("Kein Home im Tibber-Account gefunden.")
    if home_id is None:
        return homes[0]["id"]
    if any(h["id"] == home_id for h in homes):
        return home_id
    available = ", ".join(
        f"{h['id']} "
        f"({h.get('appNickname') or (h.get('address') or {}).get('address1') or h['id']})"
        for h in homes
    )
    raise TibberApiError(f"Unbekannte home_id '{home_id}'. Verfügbar: {available}")


def _format_home(h: dict) -> dict:
    addr = h.get("address") or {}
    feat = h.get("features") or {}
    mpd = h.get("meteringPointData") or {}
    return {
        "home_id": h["id"],
        "name": h.get("appNickname"),
        "address": (
            f"{addr.get('address1', '?')}, "
            f"{addr.get('postalCode', '')} {addr.get('city', '')}"
        ).strip(", "),
        "has_pulse": feat.get("realTimeConsumptionEnabled", False),
        "meter_ean": mpd.get("consumptionEan"),
        "grid_company": mpd.get("gridCompany"),
        "estimated_annual_kwh": mpd.get("estimatedAnnualConsumption"),
        "subscription_status": (h.get("currentSubscription") or {}).get("status"),
    }


async def get_home_info() -> list[dict]:
    """Alle Homes im Tibber-Account: Adresse, Tarifstatus, Zählpunkt und ob ein
    Tibber Pulse (Live-Daten) vorhanden ist."""
    homes = await graphql.get_homes()
    if not homes:
        raise TibberApiError("Kein Home im Tibber-Account gefunden.")
    return [_format_home(h) for h in homes]


def _day_summary(entries: list[dict]) -> dict | None:
    entries = [e for e in entries if e.get("total") is not None]
    if not entries:
        return None
    totals = [e["total"] for e in entries]
    cheapest = min(entries, key=lambda e: e["total"])
    priciest = max(entries, key=lambda e: e["total"])
    return {
        "hours": [
            {
                "starts_at": e["startsAt"],
                "price_ct_kwh": round(e["total"] * 100, 2),
                "level": e["level"],
            }
            for e in entries
        ],
        "min_ct_kwh": round(min(totals) * 100, 2),
        "max_ct_kwh": round(max(totals) * 100, 2),
        "avg_ct_kwh": round(sum(totals) / len(totals) * 100, 2),
        "cheapest_hour": cheapest["startsAt"],
        "most_expensive_hour": priciest["startsAt"],
    }


async def get_current_price(home_id: str | None = None) -> dict:
    """Aktueller Strompreis mit Einordnung: Tibber-Level, Rang im Tagesverlauf
    und prozentuale Abweichung vom Tagesdurchschnitt."""
    hid = await resolve_home_id(home_id)
    info = await graphql.get_price_info(hid)
    current = info.get("current")
    if current is None:
        raise TibberApiError(
            "Kein aktueller Preis in der Tibber-Antwort — später erneut versuchen."
        )
    try:
        ctx = analysis.price_context(info["today"], datetime.now(LOCAL_TZ))
    except ValueError as exc:
        raise TibberApiError(str(exc)) from exc
    return {
        "price_ct_kwh": round(current["total"] * 100, 2),
        "level": current["level"],
        "starts_at": current["startsAt"],
        "rank_today": (
            f"{ctx['rank_today']}. günstigste von {ctx['hours_today']} Stunden"
        ),
        "vs_day_average_pct": ctx["vs_day_average_pct"],
    }


async def get_price_forecast(home_id: str | None = None) -> dict:
    """Stundenpreise für heute und (falls schon publiziert) morgen, jeweils mit
    Min/Max/Durchschnitt und günstigster/teuerster Stunde."""
    hid = await resolve_home_id(home_id)
    info = await graphql.get_price_info(hid)
    result: dict = {"today": _day_summary(info["today"])}
    tomorrow = _day_summary(info.get("tomorrow") or [])
    result["tomorrow_available"] = tomorrow is not None
    if tomorrow:
        result["tomorrow"] = tomorrow
    else:
        result["note"] = "Preise für morgen werden von Tibber gegen 13:00 publiziert."
    return result


async def find_cheapest_hours(
    duration_hours: int,
    window: str = "next_24h",
    contiguous: bool = True,
    home_id: str | None = None,
) -> dict:
    """Findet die günstigsten Stunden für einen Verbraucher (Waschmaschine,
    Spülmaschine, E-Auto-Ladung).

    duration_hours: Laufzeit des Verbrauchers in Stunden.
    window: 'today', 'tomorrow' oder 'next_24h'.
    contiguous: True = zusammenhängender Block, False = billigste Einzelstunden.
    next_24h schließt die laufende Stunde ein — start_hours[0] kann in der
    Vergangenheit liegen (sofort starten).
    """
    hid = await resolve_home_id(home_id)
    info = await graphql.get_price_info(hid)
    now = datetime.now(LOCAL_TZ)
    if window == "today":
        candidates = info["today"]
    elif window == "tomorrow":
        candidates = info.get("tomorrow") or []
        if not candidates:
            raise TibberApiError(
                "Preise für morgen sind noch nicht publiziert (kommen gegen 13:00)."
            )
    elif window == "next_24h":
        all_entries = info["today"] + (info.get("tomorrow") or [])
        candidates = [
            e
            for e in all_entries
            if datetime.fromisoformat(e["startsAt"]) + timedelta(hours=1) > now
        ][:24]
    else:
        raise TibberApiError("window muss 'today', 'tomorrow' oder 'next_24h' sein.")
    # Annahme: null-totals nur als trailing unpublizierte Stunden, nie mittendrin
    # (sonst Lücke im Sliding-Window).
    candidates = [e for e in candidates if e.get("total") is not None]
    try:
        result = analysis.find_cheapest_window(candidates, duration_hours, contiguous)
    except ValueError as exc:
        raise TibberApiError(str(exc)) from exc
    return {
        "window": window,
        "duration_hours": duration_hours,
        "contiguous": contiguous,
        "start_hours": result["hours"],
        "average_price_ct_kwh": round(result["average_price_eur_kwh"] * 100, 2),
        "savings_vs_window_average_pct": result["savings_vs_window_average_pct"],
    }


mcp.tool(get_home_info)
mcp.tool(get_current_price)
mcp.tool(get_price_forecast)
mcp.tool(find_cheapest_hours)


def main() -> None:
    mcp.run()
