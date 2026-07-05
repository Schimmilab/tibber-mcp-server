from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tibber_mcp import analysis

TZ = ZoneInfo("Europe/Berlin")


def _prices(values: list[float], day: str = "2026-07-05") -> list[dict]:
    """Stundenpreise ab Mitternacht, ein Wert pro Stunde."""
    return [
        {"startsAt": f"{day}T{hour:02d}:00:00.000+02:00", "total": total, "level": "NORMAL"}
        for hour, total in enumerate(values)
    ]


def test_price_context_rank_and_deviation():
    today = _prices([0.20, 0.10, 0.30, 0.40])  # Tagesschnitt 0.25
    now = datetime(2026, 7, 5, 2, 30, tzinfo=TZ)  # in der Stunde mit 0.30
    ctx = analysis.price_context(today, now)
    assert ctx["rank_today"] == 3
    assert ctx["hours_today"] == 4
    assert ctx["vs_day_average_pct"] == 20.0


def test_price_context_negative_day_average():
    # Tagesschnitt -0.05; aktuelle Stunde -0.10 ist die günstigste
    today = _prices([-0.10, -0.05, 0.0, -0.05])
    now = datetime(2026, 7, 5, 0, 30, tzinfo=TZ)
    ctx = analysis.price_context(today, now)
    assert ctx["rank_today"] == 1
    assert ctx["vs_day_average_pct"] == -100.0  # billiger als der Schnitt → negativ


def test_price_context_zero_day_average():
    today = _prices([-0.10, 0.10])
    now = datetime(2026, 7, 5, 0, 30, tzinfo=TZ)
    ctx = analysis.price_context(today, now)
    assert ctx["vs_day_average_pct"] is None


def test_price_context_no_entry_for_now_raises():
    today = _prices([0.20, 0.10])  # nur 0:00 und 1:00 Uhr
    now = datetime(2026, 7, 5, 5, 0, tzinfo=TZ)
    with pytest.raises(ValueError):
        analysis.price_context(today, now)


def test_cheapest_contiguous_window():
    prices = _prices([0.30, 0.10, 0.12, 0.35, 0.09, 0.40])
    result = analysis.find_cheapest_window(prices, duration_hours=2, contiguous=True)
    assert result["hours"] == [
        "2026-07-05T01:00:00.000+02:00",
        "2026-07-05T02:00:00.000+02:00",
    ]
    assert result["average_price_eur_kwh"] == pytest.approx(0.11)
    assert result["savings_vs_window_average_pct"] == 51.5


def test_cheapest_non_contiguous_hours():
    prices = _prices([0.30, 0.10, 0.12, 0.35, 0.09, 0.40])
    result = analysis.find_cheapest_window(prices, duration_hours=2, contiguous=False)
    assert result["hours"] == [
        "2026-07-05T01:00:00.000+02:00",
        "2026-07-05T04:00:00.000+02:00",
    ]
    assert result["average_price_eur_kwh"] == pytest.approx(0.095)


def test_duration_longer_than_window_raises():
    with pytest.raises(ValueError):
        analysis.find_cheapest_window(_prices([0.1, 0.2]), duration_hours=3)


def test_cheapest_window_negative_average():
    prices = _prices([-0.10, -0.20, -0.05, -0.25])
    result = analysis.find_cheapest_window(prices, duration_hours=1, contiguous=True)
    assert result["hours"] == ["2026-07-05T03:00:00.000+02:00"]
    # Fenster-Schnitt -0.15, Auswahl -0.25 → 66.7% günstiger
    assert result["savings_vs_window_average_pct"] == 66.7


def test_cheapest_window_zero_average():
    prices = _prices([-0.10, 0.10])
    result = analysis.find_cheapest_window(prices, duration_hours=1, contiguous=True)
    assert result["savings_vs_window_average_pct"] is None


def test_non_contiguous_sorts_chronologically_across_dst():
    # Herbst-Zeitumstellung: 02:00+02:00 (CEST) liegt VOR 02:00+01:00 (CET)
    prices = [
        {"startsAt": "2026-10-25T02:00:00.000+01:00", "total": 0.10, "level": "NORMAL"},
        {"startsAt": "2026-10-25T02:00:00.000+02:00", "total": 0.09, "level": "NORMAL"},
    ]
    result = analysis.find_cheapest_window(prices, duration_hours=2, contiguous=False)
    assert result["hours"] == [
        "2026-10-25T02:00:00.000+02:00",
        "2026-10-25T02:00:00.000+01:00",
    ]


def test_duration_below_one_raises():
    with pytest.raises(ValueError):
        analysis.find_cheapest_window(_prices([0.1, 0.2]), duration_hours=0)
