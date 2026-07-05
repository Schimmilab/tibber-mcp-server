"""Eigener GraphQL-Client für die Tibber-API (Preise, Verbrauch, Stammdaten).

Live-Daten laufen bewusst NICHT hierüber, sondern über pyTibber (live.py).
"""
import os

import httpx

from tibber_mcp.cache import TTLCache, seconds_until_next_hour

API_URL = "https://api.tibber.com/v1-beta/gql"

_cache = TTLCache()


class TibberApiError(Exception):
    """Fehler mit LLM-tauglicher, deutscher Meldung."""


async def query(gql: str, variables: dict | None = None) -> dict:
    token = os.environ.get("TIBBER_API_TOKEN")
    if not token:
        raise TibberApiError(
            "TIBBER_API_TOKEN ist nicht gesetzt. Token unter "
            "https://developer.tibber.com/settings/access-token erstellen "
            "und als Umgebungsvariable setzen."
        )
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.post(
                API_URL,
                json={"query": gql, "variables": variables or {}},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise TibberApiError(f"Tibber-API nicht erreichbar: {exc}") from exc
    if response.status_code == 401:
        raise TibberApiError(
            "Tibber-Token ungültig (HTTP 401). Token unter "
            "https://developer.tibber.com prüfen."
        )
    if response.status_code == 429:
        raise TibberApiError(
            "Tibber-Rate-Limit erreicht (HTTP 429). Kurz warten und erneut versuchen."
        )
    if response.status_code != 200:
        raise TibberApiError(f"Tibber-API-Fehler: HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise TibberApiError(
            f"Tibber-API lieferte keine gültige JSON-Antwort (HTTP {response.status_code})."
        ) from exc
    if payload.get("errors"):
        messages = "; ".join(e.get("message", "?") for e in payload["errors"])
        raise TibberApiError(f"Tibber-GraphQL-Fehler: {messages}")
    if "data" not in payload or payload["data"] is None:
        raise TibberApiError("Tibber-API-Antwort enthält kein 'data'-Feld.")
    return payload["data"]


HOMES_QUERY = """
{
  viewer {
    homes {
      id
      appNickname
      address { address1 postalCode city country }
      features { realTimeConsumptionEnabled }
      meteringPointData { consumptionEan gridCompany estimatedAnnualConsumption }
      currentSubscription { status }
    }
  }
}
"""

PRICE_QUERY = """
query ($homeId: ID!) {
  viewer {
    home(id: $homeId) {
      currentSubscription {
        priceInfo {
          current { total startsAt level }
          today { total startsAt level }
          tomorrow { total startsAt level }
        }
      }
    }
  }
}
"""

CONSUMPTION_QUERY = """
query ($homeId: ID!, $resolution: EnergyResolution!, $last: Int!) {
  viewer {
    home(id: $homeId) {
      consumption(resolution: $resolution, last: $last) {
        nodes { from to consumption cost unitPrice }
      }
    }
  }
}
"""


async def get_homes() -> list[dict]:
    """Alle Homes, 24h gecacht."""
    cached = _cache.get("homes")
    if cached is not None:
        return cached
    data = await query(HOMES_QUERY)
    homes = data["viewer"]["homes"]
    _cache.set("homes", homes, ttl_seconds=24 * 3600)
    return homes


async def get_price_info(home_id: str) -> dict:
    """priceInfo (current/today/tomorrow), gecacht bis zur nächsten vollen Stunde."""
    key = f"price:{home_id}"
    cached = _cache.get(key)
    if cached is not None:
        return cached
    data = await query(PRICE_QUERY, {"homeId": home_id})
    home = data["viewer"]["home"]
    if home is None or home.get("currentSubscription") is None:
        raise TibberApiError(
            "Kein aktiver Tibber-Vertrag für dieses Home gefunden — keine Preisdaten verfügbar."
        )
    info = home["currentSubscription"]["priceInfo"]
    _cache.set(key, info, ttl_seconds=seconds_until_next_hour())
    return info


async def get_consumption(home_id: str, resolution: str, last: int) -> list[dict]:
    """Verbrauchs-Nodes, 15 Minuten gecacht."""
    key = f"consumption:{home_id}:{resolution}:{last}"
    cached = _cache.get(key)
    if cached is not None:
        return cached
    data = await query(
        CONSUMPTION_QUERY, {"homeId": home_id, "resolution": resolution, "last": last}
    )
    home = data["viewer"]["home"]
    if home is None:
        raise TibberApiError("Home nicht gefunden — home_id prüfen.")
    consumption = home.get("consumption")
    nodes = (consumption or {}).get("nodes") or []
    _cache.set(key, nodes, ttl_seconds=15 * 60)
    return nodes
