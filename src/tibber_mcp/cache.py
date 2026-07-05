"""Simpler In-Memory-TTL-Cache. Zweck: Tibber-Rate-Limit (100 Req/5min) nie erreichen."""
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Berlin")


class TTLCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[object, float]] = {}

    def get(self, key: str) -> object | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() >= expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: object, ttl_seconds: float) -> None:
        self._store[key] = (value, time.monotonic() + ttl_seconds)


def seconds_until_next_hour(now: datetime | None = None) -> float:
    """TTL für Preisdaten: gültig bis zur nächsten vollen Stunde."""
    now = now or datetime.now(LOCAL_TZ)
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return (next_hour - now).total_seconds()
