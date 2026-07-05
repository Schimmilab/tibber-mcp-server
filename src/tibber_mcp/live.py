"""Pulse-Live-Snapshot über pyTibber. Verbindet kurz, sammelt Messwerte, trennt wieder.

Einziger Ort im Projekt, der pyTibber nutzt — alles andere läuft über graphql.py.
"""
import asyncio

import aiohttp
import tibber

from tibber_mcp.graphql import TibberApiError


def summarize(samples: list[dict]) -> dict:
    """Verdichtet gesammelte liveMeasurement-Samples zu einem Snapshot."""
    if not samples:
        raise ValueError("Keine Samples zum Auswerten.")
    powers = [s["power"] for s in samples if s.get("power") is not None]
    last = samples[-1]
    return {
        "power_w": last.get("power"),
        "power_min_w": min(powers) if powers else None,
        "power_max_w": max(powers) if powers else None,
        "samples": len(samples),
        "accumulated_kwh_today": last.get("accumulatedConsumption"),
        "accumulated_cost_today_eur": last.get("accumulatedCost"),
        "timestamp": last.get("timestamp"),
    }


async def live_snapshot(
    token: str, home_id: str | None = None, sample_seconds: float = 15.0
) -> dict:
    """Abonniert den Pulse-Live-Stream und liefert einen Snapshot.

    Wartet bis zu sample_seconds, endet aber früher, sobald genug
    Messwerte vorliegen (mind. 2 Samples).
    """
    async with aiohttp.ClientSession() as session:
        conn = tibber.Tibber(token, websession=session, user_agent="tibber-mcp-server")
        try:
            await conn.update_info()
            homes = conn.get_homes()
            if not homes:
                raise TibberApiError("Kein Home im Tibber-Account gefunden.")
            home = homes[0]
            if home_id is not None:
                matches = [h for h in homes if h.home_id == home_id]
                if not matches:
                    available = ", ".join(h.home_id for h in homes)
                    raise TibberApiError(
                        f"Unbekannte home_id '{home_id}'. Verfügbar: {available}"
                    )
                home = matches[0]
            await home.update_info()
            if not home.has_real_time_consumption:
                raise TibberApiError(
                    "Dieses Home hat keinen Tibber Pulse "
                    "(realTimeConsumptionEnabled=false) — keine Live-Daten verfügbar."
                )
            samples: list[dict] = []

            def on_data(pkg: dict) -> None:
                data = (pkg.get("data") or {}).get("liveMeasurement")
                if data:
                    samples.append(data)

            try:
                await home.rt_subscribe(on_data)
                waited = 0.0
                while waited < sample_seconds and len(samples) < 2:
                    await asyncio.sleep(0.5)
                    waited += 0.5
            finally:
                await conn.rt_disconnect()
        except TibberApiError:
            raise
        except Exception as exc:
            raise TibberApiError(
                f"Tibber-Live-Verbindung fehlgeschlagen: {exc}"
            ) from exc
    if not samples:
        raise TibberApiError(
            f"In {sample_seconds:.0f} Sekunden keine Live-Daten empfangen. "
            "Ist der Pulse online?"
        )
    return summarize(samples)
