import pytest

from tibber_mcp import live


def test_summarize_samples():
    samples = [
        {
            "power": 500,
            "accumulatedConsumption": 4.2,
            "accumulatedCost": 1.10,
            "timestamp": "2026-07-05T14:00:01.000+02:00",
        },
        {
            "power": 900,
            "accumulatedConsumption": 4.3,
            "accumulatedCost": 1.15,
            "timestamp": "2026-07-05T14:00:03.000+02:00",
        },
    ]
    result = live.summarize(samples)
    assert result == {
        "power_w": 900,
        "power_min_w": 500,
        "power_max_w": 900,
        "samples": 2,
        "accumulated_kwh_today": 4.3,
        "accumulated_cost_today_eur": 1.15,
        "timestamp": "2026-07-05T14:00:03.000+02:00",
    }


def test_summarize_empty_raises():
    with pytest.raises(ValueError):
        live.summarize([])


def test_summarize_rounds_accumulated_values():
    samples = [{"power": 100, "accumulatedConsumption": 4.23456, "accumulatedCost": 1.14999, "timestamp": "t"}]
    result = live.summarize(samples)
    assert result["accumulated_kwh_today"] == 4.23
    assert result["accumulated_cost_today_eur"] == 1.15


def test_summarize_tolerates_null_power():
    samples = [
        {"power": None, "accumulatedConsumption": 1.0, "accumulatedCost": 0.3, "timestamp": "t1"},
        {"power": 400, "accumulatedConsumption": 1.1, "accumulatedCost": 0.33, "timestamp": "t2"},
    ]
    result = live.summarize(samples)
    assert result["power_min_w"] == 400
    assert result["power_max_w"] == 400
    assert result["samples"] == 2
