"""Reine Analyse-Funktionen ohne I/O. Eingabe: geparste API-Daten, Ausgabe: Ergebnis-Dicts."""
from datetime import date, datetime, timedelta


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def price_context(today: list[dict], now: datetime) -> dict:
    """Ordnet den aktuellen Preis in den Tagesverlauf ein.

    today: Preiseinträge {"startsAt", "total", "level"} für den heutigen Tag.
    """
    current = None
    for entry in today:
        starts = _parse(entry["startsAt"])
        if starts <= now < starts + timedelta(hours=1):
            current = entry
            break
    if current is None:
        raise ValueError("Kein Preiseintrag für die aktuelle Stunde gefunden.")
    totals = sorted(p["total"] for p in today)
    avg = sum(totals) / len(totals)
    return {
        "rank_today": totals.index(current["total"]) + 1,
        "hours_today": len(totals),
        "vs_day_average_pct": round((current["total"] / avg - 1) * 100, 1),
    }
