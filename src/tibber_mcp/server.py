"""Tibber MCP Server — Tool-Schicht. Kein Business-Code, nur Orchestrierung + Formatierung."""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastmcp import FastMCP

from tibber_mcp import analysis, graphql, live
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
    if result["today"] is None:
        result["today_note"] = "Keine Preisdaten für heute in der Tibber-Antwort."
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


async def get_consumption(
    resolution: str = "DAILY", last: int = 7, home_id: str | None = None
) -> list[dict]:
    """Historischer Verbrauch pro Periode: kWh, Kosten (EUR), Durchschnittspreis.

    resolution: HOURLY, DAILY, WEEKLY oder MONTHLY.
    last: Anzahl der letzten Perioden.
    """
    if resolution not in {"HOURLY", "DAILY", "WEEKLY", "MONTHLY"}:
        raise TibberApiError("resolution muss HOURLY, DAILY, WEEKLY oder MONTHLY sein.")
    if last < 1:
        raise TibberApiError("last muss mindestens 1 sein.")
    if last > 744:
        raise TibberApiError(
            "last darf höchstens 744 sein (ein Monat in Stunden) — für längere "
            "Zeiträume gröbere resolution wählen."
        )
    hid = await resolve_home_id(home_id)
    nodes = await graphql.get_consumption(hid, resolution, last)
    return [
        {
            "from": n["from"],
            "to": n["to"],
            "kwh": round(n["consumption"], 2) if n["consumption"] is not None else None,
            "cost_eur": round(n["cost"], 2) if n["cost"] is not None else None,
            "avg_price_ct_kwh": (
                round(n["unitPrice"] * 100, 2) if n["unitPrice"] is not None else None
            ),
        }
        for n in nodes
    ]


async def get_consumption_report(
    period: str = "month", offset: int = 0, home_id: str | None = None
) -> dict:
    """Aggregierter Verbrauchs-/Kostenreport mit Vergleich zur Vorperiode.

    period: 'week', 'month' oder 'year'.
    offset: 0 = laufende Periode, 1 = vorherige, usw.
    """
    if offset < 0:
        raise TibberApiError("offset muss 0 oder größer sein (0 = laufende Periode).")
    hid = await resolve_home_id(home_id)
    today = datetime.now(LOCAL_TZ).date()
    if period == "week":
        nodes = await graphql.get_consumption(hid, "DAILY", 7 * (offset + 2))
    elif period == "month":
        nodes = await graphql.get_consumption(hid, "DAILY", 31 * (offset + 2) + 3)
    elif period == "year":
        nodes = await graphql.get_consumption(hid, "MONTHLY", 12 * (offset + 2))
    else:
        raise TibberApiError("period muss 'week', 'month' oder 'year' sein.")
    return analysis.build_report(nodes, period, offset, today)


async def get_live_measurement(home_id: str | None = None) -> dict:
    """Live-Messung vom Tibber Pulse: aktuelle Leistung (W), Min/Max im
    Messfenster, Tagesverbrauch (kWh) und Tageskosten (EUR). Wartet bis zu
    15 Sekunden, endet sobald Messwerte vorliegen.
    Benötigt einen Tibber Pulse am Zähler."""
    token = os.environ.get("TIBBER_API_TOKEN")
    if not token:
        raise TibberApiError(
            "TIBBER_API_TOKEN ist nicht gesetzt. Token unter "
            "https://developer.tibber.com/settings/access-token erstellen."
        )
    return await live.live_snapshot(token, home_id)


mcp.tool(get_home_info)
mcp.tool(get_current_price)
mcp.tool(get_price_forecast)
mcp.tool(find_cheapest_hours)
mcp.tool(get_consumption)
mcp.tool(get_consumption_report)
mcp.tool(get_live_measurement)


def main() -> None:
    mcp.run()
