"""Reine Analyse-Funktionen ohne I/O. Eingabe: geparste API-Daten, Ausgabe: Ergebnis-Dicts."""
from datetime import date, datetime, timedelta


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def price_context(today: list[dict], now: datetime) -> dict:
    """Ordnet den aktuellen Preis in den Tagesverlauf ein.

    today: Preiseinträge {"startsAt", "total", "level"} für den heutigen Tag.

    Einträge ohne "total" sind nicht auswertbar und werden aussortiert. Wie viele
    das waren, steht im Ergebnis (`hours_received` / `hours_skipped`) — ohne diese
    Zahlen wäre `vs_day_average_pct` ein Mittelwert über eine Teilmenge, ausgegeben
    als Tageswert. Siehe docs/agent-review-contract.md, Regel 2.
    """
    received = len(today)
    today = [p for p in today if p.get("total") is not None]
    skipped = received - len(today)
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
        "hours_received": received,
        "hours_skipped": skipped,
        "vs_day_average_pct": vs_avg_pct,
    }


def find_cheapest_window(
    prices: list[dict], duration_hours: int, contiguous: bool = True
) -> dict:
    """Findet die günstigsten Stunden in einer Preisliste.

    contiguous=True: zusammenhängender Block (Waschmaschine).
    contiguous=False: die N billigsten Einzelstunden (E-Auto mit Ladepausen).
    prices muss chronologisch sortiert und lückenlos sein (Voraussetzung des Sliding-Window).
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
        selected.sort(key=lambda p: _parse(p["startsAt"]))
        avg = sum(p["total"] for p in selected) / duration_hours
    if window_avg == 0:
        savings_pct = None
    else:
        savings_pct = round((window_avg - avg) / abs(window_avg) * 100, 1)
    return {
        "hours": [p["startsAt"] for p in selected],
        "average_price_eur_kwh": round(avg, 4),
        "savings_vs_window_average_pct": savings_pct,
    }


def period_bounds(period: str, offset: int, today: date) -> tuple[date, date]:
    """Start (inklusiv) und Ende (exklusiv) einer Periode. offset 0 = laufend, 1 = vorherige."""
    if period == "week":
        monday = today - timedelta(days=today.weekday())
        start = monday - timedelta(weeks=offset)
        return start, start + timedelta(days=7)
    if period == "month":
        year, month = today.year, today.month - offset
        while month < 1:
            month += 12
            year -= 1
        start = date(year, month, 1)
        end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        return start, end
    if period == "year":
        start = date(today.year - offset, 1, 1)
        return start, date(start.year + 1, 1, 1)
    raise ValueError("period muss 'week', 'month' oder 'year' sein.")


def aggregate(nodes: list[dict], start: date, end: date) -> dict:
    """Summiert Verbrauchs-Nodes, deren 'from'-Datum in [start, end) liegt."""
    selected = [
        n
        for n in nodes
        if n["consumption"] is not None
        and start <= _parse(n["from"]).date() < end
    ]
    kwh = sum(n["consumption"] for n in selected)
    cost = sum(n["cost"] or 0 for n in selected)
    return {
        "kwh": round(kwh, 2),
        "cost_eur": round(cost, 2),
        "avg_price_ct_kwh": round(cost / kwh * 100, 2) if kwh else None,
        "entries": len(selected),
    }


def build_report(nodes: list[dict], period: str, offset: int, today: date) -> dict:
    """Report für eine Periode inkl. Vergleich zur Vorperiode."""
    cur_start, cur_end = period_bounds(period, offset, today)
    prev_start, prev_end = period_bounds(period, offset + 1, today)
    current = aggregate(nodes, cur_start, cur_end)
    previous = aggregate(nodes, prev_start, prev_end)
    change = None
    if previous["kwh"]:
        change = {
            "kwh_pct": round((current["kwh"] / previous["kwh"] - 1) * 100, 1),
            "cost_pct": (
                round(
                    (current["cost_eur"] - previous["cost_eur"])
                    / abs(previous["cost_eur"])
                    * 100,
                    1,
                )
                if previous["cost_eur"]
                else None
            ),
        }
    return {
        "period": period,
        "current": {"from": cur_start.isoformat(), "to": cur_end.isoformat(), **current},
        "previous": {"from": prev_start.isoformat(), "to": prev_end.isoformat(), **previous},
        "change_vs_previous": change,
    }
