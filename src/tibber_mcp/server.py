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
        f"{h['id']} ({h.get('appNickname') or h['address']['address1']})" for h in homes
    )
    raise TibberApiError(f"Unbekannte home_id '{home_id}'. Verfügbar: {available}")


async def get_home_info() -> list[dict]:
    """Alle Homes im Tibber-Account: Adresse, Tarifstatus, Zählpunkt und ob ein
    Tibber Pulse (Live-Daten) vorhanden ist."""
    homes = await graphql.get_homes()
    return [
        {
            "home_id": h["id"],
            "name": h.get("appNickname"),
            "address": (
                f"{h['address']['address1']}, "
                f"{h['address']['postalCode']} {h['address']['city']}"
            ),
            "has_pulse": h["features"]["realTimeConsumptionEnabled"],
            "meter_ean": h["meteringPointData"]["consumptionEan"],
            "grid_company": h["meteringPointData"]["gridCompany"],
            "estimated_annual_kwh": h["meteringPointData"]["estimatedAnnualConsumption"],
            "subscription_status": (h.get("currentSubscription") or {}).get("status"),
        }
        for h in homes
    ]


mcp.tool(get_home_info)


def main() -> None:
    mcp.run()
