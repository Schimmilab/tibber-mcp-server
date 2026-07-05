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
    if avg == 0:
        vs_avg_pct = None
    else:
        vs_avg_pct = round((current["total"] - avg) / abs(avg) * 100, 1)
    return {
        # Preis-Gleichstände bekommen denselben (niedrigsten) Rang.
        "rank_today": totals.index(current["total"]) + 1,
        "hours_today": len(totals),
        "vs_day_average_pct": vs_avg_pct,
    }
