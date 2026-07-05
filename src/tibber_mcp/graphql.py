"""Eigener GraphQL-Client für die Tibber-API (Preise, Verbrauch, Stammdaten).

Live-Daten laufen bewusst NICHT hierüber, sondern über pyTibber (live.py).
"""
import os

import httpx

from tibber_mcp.cache import TTLCache

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
    payload = response.json()
    if payload.get("errors"):
        messages = "; ".join(e.get("message", "?") for e in payload["errors"])
        raise TibberApiError(f"Tibber-GraphQL-Fehler: {messages}")
    return payload["data"]
