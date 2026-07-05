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


def test_price_context_no_entry_for_now_raises():
    today = _prices([0.20, 0.10])  # nur 0:00 und 1:00 Uhr
    now = datetime(2026, 7, 5, 5, 0, tzinfo=TZ)
    with pytest.raises(ValueError):
        analysis.price_context(today, now)
