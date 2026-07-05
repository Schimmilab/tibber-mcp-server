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


def find_cheapest_window(
    prices: list[dict], duration_hours: int, contiguous: bool = True
) -> dict:
    """Findet die günstigsten Stunden in einer Preisliste.

    contiguous=True: zusammenhängender Block (Waschmaschine).
    contiguous=False: die N billigsten Einzelstunden (E-Auto mit Ladepausen).
    """
    if duration_hours < 1:
        raise ValueError("duration_hours muss mindestens 1 sein.")
    if duration_hours > len(prices):
        raise ValueError(
            f"duration_hours={duration_hours} ist länger als das Fenster ({len(prices)} Stunden)."
        )
    window_avg = sum(p["total"] for p in prices) / len(prices)
    if contiguous:
        best: list[dict] | None = None
        best_avg = float("inf")
        for i in range(len(prices) - duration_hours + 1):
            chunk = prices[i : i + duration_hours]
            avg = sum(p["total"] for p in chunk) / duration_hours
            if avg < best_avg:
                best, best_avg = chunk, avg
        selected, avg = best, best_avg
    else:
        selected = sorted(prices, key=lambda p: p["total"])[:duration_hours]
        selected.sort(key=lambda p: p["startsAt"])
        avg = sum(p["total"] for p in selected) / duration_hours
    if window_avg == 0:
        savings_pct = None
    else:
        savings_pct = round((window_avg - avg) / abs(window_avg) * 100, 1)
    return {
        "hours": [p["startsAt"] for p in selected],
        "average_price_eur_kwh": avg,
        "savings_vs_window_average_pct": savings_pct,
    }
