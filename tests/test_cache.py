import time
from datetime import datetime
from zoneinfo import ZoneInfo

from tibber_mcp.cache import TTLCache, seconds_until_next_hour


def test_set_and_get():
    cache = TTLCache()
    cache.set("key", {"a": 1}, ttl_seconds=60)
    assert cache.get("key") == {"a": 1}


def test_missing_key_returns_none():
    assert TTLCache().get("nope") is None


def test_expired_entry_returns_none():
    cache = TTLCache()
    cache.set("key", "value", ttl_seconds=0.01)
    time.sleep(0.02)
    assert cache.get("key") is None


def test_seconds_until_next_hour():
    now = datetime(2026, 7, 5, 13, 45, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    assert seconds_until_next_hour(now) == 15 * 60
