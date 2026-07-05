# Tibber MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lokaler MCP-Server (Python, FastMCP, stdio), der Tibber-Preise, Verbrauch, Home-Infos und Pulse-Live-Daten aufbereitet an LLMs liefert.

**Architecture:** Hybrid-Ansatz — eigener GraphQL-Client (httpx) für Preise/Verbrauch/Stammdaten, pyTibber nur für den Pulse-Live-WebSocket. Analyse-Logik (Günstigste-Stunden, Preis-Kontext, Reports) als pure functions in `analysis.py`. In-Memory-TTL-Cache gegen das Tibber-Rate-Limit.

**Tech Stack:** Python ≥3.11, uv, fastmcp (2.x), httpx, pyTibber, pytest + pytest-asyncio + respx.

**Spec:** `docs/superpowers/specs/2026-07-05-tibber-mcp-server-design.md`

**Wichtig für alle Tasks:** `asyncio_mode = "auto"` ist in pyproject.toml gesetzt — async Tests brauchen KEINEN `@pytest.mark.asyncio`-Marker. Alle Fehlermeldungen sind auf Deutsch und LLM-tauglich (kein Traceback-Jargon).

---

### Task 1: Projekt-Setup

**Files:**
- Create: `pyproject.toml`
- Create: `src/tibber_mcp/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: pyproject.toml schreiben**

```toml
[project]
name = "tibber-mcp-server"
version = "0.1.0"
description = "MCP-Server für die Tibber-API: Preise, Verbrauch, Pulse-Live-Daten"
requires-python = ">=3.11"
dependencies = [
    "fastmcp>=2.0",
    "httpx>=0.27",
    "pyTibber>=0.30",
    "aiohttp>=3.9",
]

[project.scripts]
tibber-mcp = "tibber_mcp.server:main"

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "respx>=0.21",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/tibber_mcp"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Paket-Skelett anlegen**

```bash
mkdir -p src/tibber_mcp tests
touch src/tibber_mcp/__init__.py tests/__init__.py
```

- [ ] **Step 3: Abhängigkeiten installieren und pytest verifizieren**

Run: `uv sync && uv run pytest`
Expected: `no tests ran` (Exit-Code 5 ist ok — es gibt noch keine Tests, aber uv/pytest laufen)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src tests uv.lock
git commit -m "chore: Projekt-Setup mit uv, fastmcp, pyTibber"
```

---

### Task 2: TTL-Cache (`cache.py`)

**Files:**
- Create: `src/tibber_mcp/cache.py`
- Test: `tests/test_cache.py`

- [ ] **Step 1: Failing Test schreiben**

```python
# tests/test_cache.py
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
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag verifizieren**

Run: `uv run pytest tests/test_cache.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'tibber_mcp.cache'`

- [ ] **Step 3: Implementierung**

```python
# src/tibber_mcp/cache.py
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
```

- [ ] **Step 4: Tests grün verifizieren**

Run: `uv run pytest tests/test_cache.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/tibber_mcp/cache.py tests/test_cache.py
git commit -m "feat: TTL-Cache mit Stunden-Ablauf-Helfer"
```

---

### Task 3: Preis-Kontext (`analysis.price_context`)

**Files:**
- Create: `src/tibber_mcp/analysis.py`
- Test: `tests/test_analysis.py`

Datenformat der Preiseinträge (kommt so aus der Tibber-API):
`{"startsAt": "2026-07-05T13:00:00.000+02:00", "total": 0.28, "level": "NORMAL"}` — `total` ist EUR/kWh.

- [ ] **Step 1: Failing Test schreiben**

```python
# tests/test_analysis.py
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
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag verifizieren**

Run: `uv run pytest tests/test_analysis.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'tibber_mcp.analysis'`

- [ ] **Step 3: Implementierung**

```python
# src/tibber_mcp/analysis.py
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
```

- [ ] **Step 4: Tests grün verifizieren**

Run: `uv run pytest tests/test_analysis.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/tibber_mcp/analysis.py tests/test_analysis.py
git commit -m "feat: Preis-Kontext (Tagesrang, Abweichung vom Schnitt)"
```

---

### Task 4: Günstigste-Stunden-Finder (`analysis.find_cheapest_window`)

**Files:**
- Modify: `src/tibber_mcp/analysis.py`
- Test: `tests/test_analysis.py`

- [ ] **Step 1: Failing Tests ergänzen**

An `tests/test_analysis.py` anhängen:

```python
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
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag verifizieren**

Run: `uv run pytest tests/test_analysis.py -v`
Expected: 3 neue FAIL mit `AttributeError: ... has no attribute 'find_cheapest_window'`

- [ ] **Step 3: Implementierung**

An `src/tibber_mcp/analysis.py` anhängen:

```python
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
    return {
        "hours": [p["startsAt"] for p in selected],
        "average_price_eur_kwh": avg,
        "savings_vs_window_average_pct": round((1 - avg / window_avg) * 100, 1),
    }
```

- [ ] **Step 4: Tests grün verifizieren**

Run: `uv run pytest tests/test_analysis.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/tibber_mcp/analysis.py tests/test_analysis.py
git commit -m "feat: Günstigste-Stunden-Finder (zusammenhängend und einzeln)"
```

---

### Task 5: Report-Aggregation (`analysis.period_bounds` / `aggregate` / `build_report`)

**Files:**
- Modify: `src/tibber_mcp/analysis.py`
- Test: `tests/test_analysis.py`

Verbrauchs-Nodes aus der Tibber-API haben die Form:
`{"from": "2026-07-02T00:00:00.000+02:00", "to": "...", "consumption": 8.0, "cost": 2.0, "unitPrice": 0.25}` — `consumption` in kWh, `cost` in EUR. `consumption` kann `null` sein (Zukunft/fehlende Daten).

- [ ] **Step 1: Failing Tests ergänzen**

An `tests/test_analysis.py` anhängen:

```python
from datetime import date


def test_period_bounds_month_offset():
    start, end = analysis.period_bounds("month", 1, date(2026, 7, 5))
    assert start == date(2026, 6, 1)
    assert end == date(2026, 7, 1)


def test_period_bounds_month_over_year_boundary():
    start, end = analysis.period_bounds("month", 7, date(2026, 7, 5))
    assert start == date(2025, 12, 1)
    assert end == date(2026, 1, 1)


def test_period_bounds_week():
    # 2026-07-05 ist ein Sonntag → laufende Woche beginnt Montag 2026-06-29
    start, end = analysis.period_bounds("week", 0, date(2026, 7, 5))
    assert start == date(2026, 6, 29)
    assert end == date(2026, 7, 6)


def test_period_bounds_year():
    start, end = analysis.period_bounds("year", 1, date(2026, 7, 5))
    assert start == date(2025, 1, 1)
    assert end == date(2026, 1, 1)


def _node(day: str, kwh: float | None, cost: float | None) -> dict:
    return {
        "from": f"{day}T00:00:00.000+02:00",
        "to": f"{day}T23:59:59.000+02:00",
        "consumption": kwh,
        "cost": cost,
        "unitPrice": (cost / kwh) if kwh and cost else None,
    }


def test_build_report_compares_periods():
    nodes = [
        _node("2026-06-10", 10.0, 3.0),
        _node("2026-07-02", 8.0, 2.0),
        _node("2026-07-04", None, None),  # fehlende Daten werden ignoriert
    ]
    report = analysis.build_report(nodes, "month", 0, date(2026, 7, 5))
    assert report["current"]["kwh"] == 8.0
    assert report["current"]["cost_eur"] == 2.0
    assert report["current"]["avg_price_ct_kwh"] == 25.0
    assert report["previous"]["kwh"] == 10.0
    assert report["change_vs_previous"]["kwh_pct"] == -20.0
    assert report["change_vs_previous"]["cost_pct"] == -33.3


def test_build_report_without_previous_data():
    nodes = [_node("2026-07-02", 8.0, 2.0)]
    report = analysis.build_report(nodes, "month", 0, date(2026, 7, 5))
    assert report["previous"]["kwh"] == 0
    assert report["change_vs_previous"] is None
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag verifizieren**

Run: `uv run pytest tests/test_analysis.py -v`
Expected: neue FAIL mit `AttributeError: ... has no attribute 'period_bounds'`

- [ ] **Step 3: Implementierung**

An `src/tibber_mcp/analysis.py` anhängen:

```python
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
                round((current["cost_eur"] / previous["cost_eur"] - 1) * 100, 1)
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
```

- [ ] **Step 4: Tests grün verifizieren**

Run: `uv run pytest tests/test_analysis.py -v`
Expected: alle passed

- [ ] **Step 5: Commit**

```bash
git add src/tibber_mcp/analysis.py tests/test_analysis.py
git commit -m "feat: Report-Aggregation mit Periodengrenzen und Vorperioden-Vergleich"
```

---

### Task 6: GraphQL-Client mit Fehlerbehandlung (`graphql.query`)

**Files:**
- Create: `src/tibber_mcp/graphql.py`
- Create: `tests/conftest.py`
- Test: `tests/test_graphql.py`

- [ ] **Step 1: conftest.py anlegen**

```python
# tests/conftest.py
import pytest


@pytest.fixture(autouse=True)
def api_token(monkeypatch):
    monkeypatch.setenv("TIBBER_API_TOKEN", "test-token")


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    from tibber_mcp import graphql
    from tibber_mcp.cache import TTLCache

    monkeypatch.setattr(graphql, "_cache", TTLCache())
```

Hinweis: `fresh_cache` importiert `tibber_mcp.graphql` — das Modul entsteht in diesem Task, daher schlagen ab jetzt ALLE Tests fehl, bis Step 3 fertig ist. Das ist beabsichtigt.

- [ ] **Step 2: Failing Tests schreiben**

```python
# tests/test_graphql.py
import httpx
import pytest
import respx

from tibber_mcp import graphql
from tibber_mcp.graphql import TibberApiError


@respx.mock
async def test_query_returns_data():
    respx.post(graphql.API_URL).mock(
        return_value=httpx.Response(200, json={"data": {"viewer": {"homes": []}}})
    )
    data = await graphql.query("{ viewer { homes { id } } }")
    assert data == {"viewer": {"homes": []}}


async def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv("TIBBER_API_TOKEN", raising=False)
    with pytest.raises(TibberApiError, match="TIBBER_API_TOKEN"):
        await graphql.query("{ viewer { name } }")


@respx.mock
async def test_invalid_token_401():
    respx.post(graphql.API_URL).mock(return_value=httpx.Response(401))
    with pytest.raises(TibberApiError, match="401"):
        await graphql.query("{ viewer { name } }")


@respx.mock
async def test_rate_limit_429():
    respx.post(graphql.API_URL).mock(return_value=httpx.Response(429))
    with pytest.raises(TibberApiError, match="Rate-Limit"):
        await graphql.query("{ viewer { name } }")


@respx.mock
async def test_graphql_errors_raised():
    respx.post(graphql.API_URL).mock(
        return_value=httpx.Response(
            200, json={"errors": [{"message": "boom"}], "data": None}
        )
    )
    with pytest.raises(TibberApiError, match="boom"):
        await graphql.query("{ viewer { name } }")
```

Run: `uv run pytest tests/test_graphql.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'tibber_mcp.graphql'`

- [ ] **Step 3: Implementierung**

```python
# src/tibber_mcp/graphql.py
"""Eigener GraphQL-Client für die Tibber-API (Preise, Verbrauch, Stammdaten).

Live-Daten laufen bewusst NICHT hierüber, sondern über pyTibber (live.py).
"""
import os

import httpx

from tibber_mcp.cache import TTLCache

API_URL = "https://api.tibber.com/v1-beta/gql"

_cache = TTLCache()


class TibberApiError(Exception):
    """Fehler mit LLM-tauglicher, deutscher Meldung."""


async def query(gql: str, variables: dict | None = None) -> dict:
    token = os.environ.get("TIBBER_API_TOKEN")
    if not token:
        raise TibberApiError(
            "TIBBER_API_TOKEN ist nicht gesetzt. Token unter "
            "https://developer.tibber.com/settings/access-token erstellen "
            "und als Umgebungsvariable setzen."
        )
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.post(
                API_URL,
                json={"query": gql, "variables": variables or {}},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise TibberApiError(f"Tibber-API nicht erreichbar: {exc}") from exc
    if response.status_code == 401:
        raise TibberApiError(
            "Tibber-Token ungültig (HTTP 401). Token unter "
            "https://developer.tibber.com prüfen."
        )
    if response.status_code == 429:
        raise TibberApiError(
            "Tibber-Rate-Limit erreicht (HTTP 429). Kurz warten und erneut versuchen."
        )
    if response.status_code != 200:
        raise TibberApiError(f"Tibber-API-Fehler: HTTP {response.status_code}")
    payload = response.json()
    if payload.get("errors"):
        messages = "; ".join(e.get("message", "?") for e in payload["errors"])
        raise TibberApiError(f"Tibber-GraphQL-Fehler: {messages}")
    return payload["data"]
```

- [ ] **Step 4: Tests grün verifizieren**

Run: `uv run pytest -v`
Expected: alle passed (auch die alten — conftest greift jetzt)

- [ ] **Step 5: Commit**

```bash
git add src/tibber_mcp/graphql.py tests/test_graphql.py tests/conftest.py
git commit -m "feat: GraphQL-Client mit deutscher Fehlerbehandlung"
```

---

### Task 7: Query-Wrapper mit Cache (`graphql.get_homes` / `get_price_info` / `get_consumption`)

**Files:**
- Modify: `src/tibber_mcp/graphql.py`
- Test: `tests/test_graphql.py`

- [ ] **Step 1: Failing Tests ergänzen**

An `tests/test_graphql.py` anhängen:

```python
HOMES_RESPONSE = {
    "data": {
        "viewer": {
            "homes": [
                {
                    "id": "h1",
                    "appNickname": "Zuhause",
                    "address": {
                        "address1": "Musterweg 1",
                        "postalCode": "70173",
                        "city": "Stuttgart",
                        "country": "DE",
                    },
                    "features": {"realTimeConsumptionEnabled": True},
                    "meteringPointData": {
                        "consumptionEan": "DE0001",
                        "gridCompany": "Netze BW",
                        "estimatedAnnualConsumption": 3500,
                    },
                    "currentSubscription": {"status": "running"},
                }
            ]
        }
    }
}


@respx.mock
async def test_get_homes_uses_cache():
    route = respx.post(graphql.API_URL).mock(
        return_value=httpx.Response(200, json=HOMES_RESPONSE)
    )
    homes = await graphql.get_homes()
    assert homes[0]["id"] == "h1"
    await graphql.get_homes()  # zweiter Aufruf aus dem Cache
    assert route.call_count == 1


@respx.mock
async def test_get_price_info():
    price_response = {
        "data": {
            "viewer": {
                "home": {
                    "currentSubscription": {
                        "priceInfo": {
                            "current": {
                                "total": 0.28,
                                "startsAt": "2026-07-05T13:00:00.000+02:00",
                                "level": "NORMAL",
                            },
                            "today": [
                                {
                                    "total": 0.28,
                                    "startsAt": "2026-07-05T13:00:00.000+02:00",
                                    "level": "NORMAL",
                                }
                            ],
                            "tomorrow": [],
                        }
                    }
                }
            }
        }
    }
    respx.post(graphql.API_URL).mock(
        return_value=httpx.Response(200, json=price_response)
    )
    info = await graphql.get_price_info("h1")
    assert info["current"]["total"] == 0.28
    assert info["tomorrow"] == []


@respx.mock
async def test_get_consumption():
    consumption_response = {
        "data": {
            "viewer": {
                "home": {
                    "consumption": {
                        "nodes": [
                            {
                                "from": "2026-07-04T00:00:00.000+02:00",
                                "to": "2026-07-05T00:00:00.000+02:00",
                                "consumption": 9.5,
                                "cost": 2.7,
                                "unitPrice": 0.284,
                            }
                        ]
                    }
                }
            }
        }
    }
    respx.post(graphql.API_URL).mock(
        return_value=httpx.Response(200, json=consumption_response)
    )
    nodes = await graphql.get_consumption("h1", "DAILY", 1)
    assert nodes[0]["consumption"] == 9.5
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag verifizieren**

Run: `uv run pytest tests/test_graphql.py -v`
Expected: 3 neue FAIL mit `AttributeError: ... has no attribute 'get_homes'`

- [ ] **Step 3: Implementierung**

An `src/tibber_mcp/graphql.py` anhängen (Import oben ergänzen: `from tibber_mcp.cache import TTLCache, seconds_until_next_hour`):

```python
HOMES_QUERY = """
{
  viewer {
    homes {
      id
      appNickname
      address { address1 postalCode city country }
      features { realTimeConsumptionEnabled }
      meteringPointData { consumptionEan gridCompany estimatedAnnualConsumption }
      currentSubscription { status }
    }
  }
}
"""

PRICE_QUERY = """
query ($homeId: ID!) {
  viewer {
    home(id: $homeId) {
      currentSubscription {
        priceInfo {
          current { total startsAt level }
          today { total startsAt level }
          tomorrow { total startsAt level }
        }
      }
    }
  }
}
"""

CONSUMPTION_QUERY = """
query ($homeId: ID!, $resolution: EnergyResolution!, $last: Int!) {
  viewer {
    home(id: $homeId) {
      consumption(resolution: $resolution, last: $last) {
        nodes { from to consumption cost unitPrice }
      }
    }
  }
}
"""


async def get_homes() -> list[dict]:
    """Alle Homes, 24h gecacht."""
    cached = _cache.get("homes")
    if cached is not None:
        return cached
    data = await query(HOMES_QUERY)
    homes = data["viewer"]["homes"]
    _cache.set("homes", homes, ttl_seconds=24 * 3600)
    return homes


async def get_price_info(home_id: str) -> dict:
    """priceInfo (current/today/tomorrow), gecacht bis zur nächsten vollen Stunde."""
    key = f"price:{home_id}"
    cached = _cache.get(key)
    if cached is not None:
        return cached
    data = await query(PRICE_QUERY, {"homeId": home_id})
    home = data["viewer"]["home"]
    if home is None or home.get("currentSubscription") is None:
        raise TibberApiError(
            "Kein aktiver Tibber-Vertrag für dieses Home gefunden — keine Preisdaten verfügbar."
        )
    info = home["currentSubscription"]["priceInfo"]
    _cache.set(key, info, ttl_seconds=seconds_until_next_hour())
    return info


async def get_consumption(home_id: str, resolution: str, last: int) -> list[dict]:
    """Verbrauchs-Nodes, 15 Minuten gecacht."""
    key = f"consumption:{home_id}:{resolution}:{last}"
    cached = _cache.get(key)
    if cached is not None:
        return cached
    data = await query(
        CONSUMPTION_QUERY, {"homeId": home_id, "resolution": resolution, "last": last}
    )
    home = data["viewer"]["home"]
    if home is None:
        raise TibberApiError("Home nicht gefunden — home_id prüfen.")
    nodes = home["consumption"]["nodes"]
    _cache.set(key, nodes, ttl_seconds=15 * 60)
    return nodes
```

- [ ] **Step 4: Tests grün verifizieren**

Run: `uv run pytest -v`
Expected: alle passed

- [ ] **Step 5: Commit**

```bash
git add src/tibber_mcp/graphql.py tests/test_graphql.py
git commit -m "feat: Tibber-Queries für Homes, Preise und Verbrauch mit TTL-Cache"
```

---

### Task 8: Server-Grundgerüst + `get_home_info` + Home-Auflösung

**Files:**
- Create: `src/tibber_mcp/server.py`
- Test: `tests/test_tools.py`

Designentscheidung: Die Tool-Funktionen sind normale async-Funktionen auf Modulebene und werden am Modulende per `mcp.tool(fn)` registriert. So bleiben sie in Tests direkt aufruf- und patchbar.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/test_tools.py
import pytest

from tibber_mcp import server
from tibber_mcp.graphql import TibberApiError

HOME = {
    "id": "h1",
    "appNickname": "Zuhause",
    "address": {
        "address1": "Musterweg 1",
        "postalCode": "70173",
        "city": "Stuttgart",
        "country": "DE",
    },
    "features": {"realTimeConsumptionEnabled": True},
    "meteringPointData": {
        "consumptionEan": "DE0001",
        "gridCompany": "Netze BW",
        "estimatedAnnualConsumption": 3500,
    },
    "currentSubscription": {"status": "running"},
}


@pytest.fixture
def homes(monkeypatch):
    async def fake_get_homes():
        return [HOME]

    monkeypatch.setattr(server.graphql, "get_homes", fake_get_homes)


async def test_get_home_info(homes):
    result = await server.get_home_info()
    assert result == [
        {
            "home_id": "h1",
            "name": "Zuhause",
            "address": "Musterweg 1, 70173 Stuttgart",
            "has_pulse": True,
            "meter_ean": "DE0001",
            "grid_company": "Netze BW",
            "estimated_annual_kwh": 3500,
            "subscription_status": "running",
        }
    ]


async def test_resolve_home_id_defaults_to_first(homes):
    assert await server.resolve_home_id(None) == "h1"


async def test_resolve_home_id_rejects_unknown(homes):
    with pytest.raises(TibberApiError, match="h1"):
        await server.resolve_home_id("does-not-exist")
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag verifizieren**

Run: `uv run pytest tests/test_tools.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'tibber_mcp.server'`

- [ ] **Step 3: Implementierung**

```python
# src/tibber_mcp/server.py
"""Tibber MCP Server — Tool-Schicht. Kein Business-Code, nur Orchestrierung + Formatierung."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastmcp import FastMCP

from tibber_mcp import analysis, graphql
from tibber_mcp.graphql import TibberApiError

LOCAL_TZ = ZoneInfo("Europe/Berlin")

mcp = FastMCP(
    "tibber",
    instructions=(
        "Zugriff auf Tibber-Stromdaten: Preise mit Forecast, Verbrauch/Kosten, "
        "günstigste Stunden für Verbraucher, Pulse-Live-Messung. "
        "Preise in ct/kWh, Summen in EUR, Zeiten in Europe/Berlin."
    ),
)


async def resolve_home_id(home_id: str | None) -> str:
    """Validiert eine home_id bzw. liefert das erste Home als Default."""
    homes = await graphql.get_homes()
    if not homes:
        raise TibberApiError("Kein Home im Tibber-Account gefunden.")
    if home_id is None:
        return homes[0]["id"]
    if any(h["id"] == home_id for h in homes):
        return home_id
    available = ", ".join(
        f"{h['id']} ({h.get('appNickname') or h['address']['address1']})" for h in homes
    )
    raise TibberApiError(f"Unbekannte home_id '{home_id}'. Verfügbar: {available}")


async def get_home_info() -> list[dict]:
    """Alle Homes im Tibber-Account: Adresse, Tarifstatus, Zählpunkt und ob ein
    Tibber Pulse (Live-Daten) vorhanden ist."""
    homes = await graphql.get_homes()
    return [
        {
            "home_id": h["id"],
            "name": h.get("appNickname"),
            "address": (
                f"{h['address']['address1']}, "
                f"{h['address']['postalCode']} {h['address']['city']}"
            ),
            "has_pulse": h["features"]["realTimeConsumptionEnabled"],
            "meter_ean": h["meteringPointData"]["consumptionEan"],
            "grid_company": h["meteringPointData"]["gridCompany"],
            "estimated_annual_kwh": h["meteringPointData"]["estimatedAnnualConsumption"],
            "subscription_status": (h.get("currentSubscription") or {}).get("status"),
        }
        for h in homes
    ]


mcp.tool(get_home_info)


def main() -> None:
    mcp.run()
```

- [ ] **Step 4: Tests grün verifizieren**

Run: `uv run pytest tests/test_tools.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/tibber_mcp/server.py tests/test_tools.py
git commit -m "feat: FastMCP-Server mit get_home_info und Home-Auflösung"
```

---

### Task 9: Preis-Tools (`get_current_price` + `get_price_forecast`)

**Files:**
- Modify: `src/tibber_mcp/server.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Failing Tests ergänzen**

An `tests/test_tools.py` anhängen:

```python
from datetime import datetime, timedelta


def _today_prices() -> list[dict]:
    """24 Stundenpreise für heute: 20 ct um 0 Uhr, +1 ct pro Stunde."""
    start = datetime.now(server.LOCAL_TZ).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return [
        {
            "startsAt": (start + timedelta(hours=h)).isoformat(),
            "total": round(0.20 + h * 0.01, 4),
            "level": "NORMAL",
        }
        for h in range(24)
    ]


@pytest.fixture
def price_info(monkeypatch):
    today = _today_prices()
    info = {"current": None, "today": today, "tomorrow": []}
    # current = Eintrag der aktuellen Stunde
    now_hour = datetime.now(server.LOCAL_TZ).hour
    info["current"] = today[now_hour]

    async def fake_get_price_info(home_id):
        return info

    monkeypatch.setattr(server.graphql, "get_price_info", fake_get_price_info)
    return info


async def test_get_current_price(homes, price_info):
    result = await server.get_current_price()
    expected_ct = round(price_info["current"]["total"] * 100, 2)
    assert result["price_ct_kwh"] == expected_ct
    assert result["level"] == "NORMAL"
    assert "günstigste" in result["rank_today"]


async def test_get_price_forecast_tomorrow_missing(homes, price_info):
    result = await server.get_price_forecast()
    assert result["tomorrow_available"] is False
    assert "13:00" in result["note"]
    assert len(result["today"]["hours"]) == 24
    assert result["today"]["min_ct_kwh"] == 20.0
    assert result["today"]["max_ct_kwh"] == 43.0
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag verifizieren**

Run: `uv run pytest tests/test_tools.py -v`
Expected: 2 neue FAIL mit `AttributeError: ... has no attribute 'get_current_price'`

- [ ] **Step 3: Implementierung**

In `src/tibber_mcp/server.py` vor `mcp.tool(get_home_info)` einfügen:

```python
def _day_summary(entries: list[dict]) -> dict | None:
    if not entries:
        return None
    totals = [e["total"] for e in entries]
    cheapest = min(entries, key=lambda e: e["total"])
    priciest = max(entries, key=lambda e: e["total"])
    return {
        "hours": [
            {
                "starts_at": e["startsAt"],
                "price_ct_kwh": round(e["total"] * 100, 2),
                "level": e["level"],
            }
            for e in entries
        ],
        "min_ct_kwh": round(min(totals) * 100, 2),
        "max_ct_kwh": round(max(totals) * 100, 2),
        "avg_ct_kwh": round(sum(totals) / len(totals) * 100, 2),
        "cheapest_hour": cheapest["startsAt"],
        "most_expensive_hour": priciest["startsAt"],
    }


async def get_current_price(home_id: str | None = None) -> dict:
    """Aktueller Strompreis mit Einordnung: Tibber-Level, Rang im Tagesverlauf
    und prozentuale Abweichung vom Tagesdurchschnitt."""
    hid = await resolve_home_id(home_id)
    info = await graphql.get_price_info(hid)
    current = info["current"]
    ctx = analysis.price_context(info["today"], datetime.now(LOCAL_TZ))
    return {
        "price_ct_kwh": round(current["total"] * 100, 2),
        "level": current["level"],
        "starts_at": current["startsAt"],
        "rank_today": (
            f"{ctx['rank_today']}. günstigste von {ctx['hours_today']} Stunden"
        ),
        "vs_day_average_pct": ctx["vs_day_average_pct"],
    }


async def get_price_forecast(home_id: str | None = None) -> dict:
    """Stundenpreise für heute und (falls schon publiziert) morgen, jeweils mit
    Min/Max/Durchschnitt und günstigster/teuerster Stunde."""
    hid = await resolve_home_id(home_id)
    info = await graphql.get_price_info(hid)
    result: dict = {"today": _day_summary(info["today"])}
    tomorrow = _day_summary(info.get("tomorrow") or [])
    result["tomorrow_available"] = tomorrow is not None
    if tomorrow:
        result["tomorrow"] = tomorrow
    else:
        result["note"] = "Preise für morgen werden von Tibber gegen 13:00 publiziert."
    return result
```

Und die Registrierung am Modulende erweitern:

```python
mcp.tool(get_home_info)
mcp.tool(get_current_price)
mcp.tool(get_price_forecast)
```

- [ ] **Step 4: Tests grün verifizieren**

Run: `uv run pytest tests/test_tools.py -v`
Expected: alle passed

- [ ] **Step 5: Commit**

```bash
git add src/tibber_mcp/server.py tests/test_tools.py
git commit -m "feat: Preis-Tools mit Tages-Einordnung und Forecast"
```

---

### Task 10: `find_cheapest_hours`-Tool

**Files:**
- Modify: `src/tibber_mcp/server.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Failing Tests ergänzen**

An `tests/test_tools.py` anhängen:

```python
async def test_find_cheapest_hours_today(homes, price_info):
    result = await server.find_cheapest_hours(duration_hours=2, window="today")
    # Preise steigen monoton → günstigster 2h-Block ist 0-2 Uhr
    assert result["start_hours"][0] == price_info["today"][0]["startsAt"]
    assert result["average_price_ct_kwh"] == 20.5
    assert result["savings_vs_window_average_pct"] > 0


async def test_find_cheapest_hours_tomorrow_not_published(homes, price_info):
    with pytest.raises(TibberApiError, match="13:00"):
        await server.find_cheapest_hours(duration_hours=2, window="tomorrow")


async def test_find_cheapest_hours_invalid_window(homes, price_info):
    with pytest.raises(TibberApiError, match="window"):
        await server.find_cheapest_hours(duration_hours=2, window="yesterday")
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag verifizieren**

Run: `uv run pytest tests/test_tools.py -v`
Expected: 3 neue FAIL mit `AttributeError: ... has no attribute 'find_cheapest_hours'`

- [ ] **Step 3: Implementierung**

In `src/tibber_mcp/server.py` vor der Tool-Registrierung einfügen:

```python
async def find_cheapest_hours(
    duration_hours: int,
    window: str = "next_24h",
    contiguous: bool = True,
    home_id: str | None = None,
) -> dict:
    """Findet die günstigsten Stunden für einen Verbraucher (Waschmaschine,
    Spülmaschine, E-Auto-Ladung).

    duration_hours: Laufzeit des Verbrauchers in Stunden.
    window: 'today', 'tomorrow' oder 'next_24h'.
    contiguous: True = zusammenhängender Block, False = billigste Einzelstunden.
    """
    hid = await resolve_home_id(home_id)
    info = await graphql.get_price_info(hid)
    now = datetime.now(LOCAL_TZ)
    if window == "today":
        candidates = info["today"]
    elif window == "tomorrow":
        candidates = info.get("tomorrow") or []
        if not candidates:
            raise TibberApiError(
                "Preise für morgen sind noch nicht publiziert (kommen gegen 13:00)."
            )
    elif window == "next_24h":
        all_entries = info["today"] + (info.get("tomorrow") or [])
        candidates = [
            e
            for e in all_entries
            if datetime.fromisoformat(e["startsAt"]) + timedelta(hours=1) > now
        ][:24]
    else:
        raise TibberApiError("window muss 'today', 'tomorrow' oder 'next_24h' sein.")
    try:
        result = analysis.find_cheapest_window(candidates, duration_hours, contiguous)
    except ValueError as exc:
        raise TibberApiError(str(exc)) from exc
    return {
        "window": window,
        "duration_hours": duration_hours,
        "contiguous": contiguous,
        "start_hours": result["hours"],
        "average_price_ct_kwh": round(result["average_price_eur_kwh"] * 100, 2),
        "savings_vs_window_average_pct": result["savings_vs_window_average_pct"],
    }
```

Registrierung ergänzen: `mcp.tool(find_cheapest_hours)`

- [ ] **Step 4: Tests grün verifizieren**

Run: `uv run pytest tests/test_tools.py -v`
Expected: alle passed

- [ ] **Step 5: Commit**

```bash
git add src/tibber_mcp/server.py tests/test_tools.py
git commit -m "feat: find_cheapest_hours mit today/tomorrow/next_24h-Fenster"
```

---

### Task 11: Verbrauchs-Tools (`get_consumption` + `get_consumption_report`)

**Files:**
- Modify: `src/tibber_mcp/server.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Failing Tests ergänzen**

An `tests/test_tools.py` anhängen:

```python
CONSUMPTION_NODES = [
    {
        "from": "2026-07-04T00:00:00.000+02:00",
        "to": "2026-07-05T00:00:00.000+02:00",
        "consumption": 9.512,
        "cost": 2.7183,
        "unitPrice": 0.2858,
    },
    {
        "from": "2026-07-05T00:00:00.000+02:00",
        "to": "2026-07-06T00:00:00.000+02:00",
        "consumption": None,
        "cost": None,
        "unitPrice": None,
    },
]


@pytest.fixture
def consumption(monkeypatch):
    captured = {}

    async def fake_get_consumption(home_id, resolution, last):
        captured["args"] = (home_id, resolution, last)
        return CONSUMPTION_NODES

    monkeypatch.setattr(server.graphql, "get_consumption", fake_get_consumption)
    return captured


async def test_get_consumption_formats_output(homes, consumption):
    result = await server.get_consumption(resolution="DAILY", last=2)
    assert result[0] == {
        "from": "2026-07-04T00:00:00.000+02:00",
        "to": "2026-07-05T00:00:00.000+02:00",
        "kwh": 9.51,
        "cost_eur": 2.72,
        "avg_price_ct_kwh": 28.58,
    }
    assert result[1]["kwh"] is None


async def test_get_consumption_rejects_bad_resolution(homes, consumption):
    with pytest.raises(TibberApiError, match="resolution"):
        await server.get_consumption(resolution="MINUTELY", last=2)


async def test_get_consumption_report_month(homes, monkeypatch):
    from datetime import date, timedelta as td

    today = datetime.now(server.LOCAL_TZ).date()
    cur_month_start = today.replace(day=1)
    prev_month_start = (cur_month_start - td(days=1)).replace(day=1)
    nodes = [
        {
            "from": f"{prev_month_start.isoformat()}T00:00:00.000+02:00",
            "to": "x",
            "consumption": 10.0,
            "cost": 3.0,
            "unitPrice": 0.3,
        },
        {
            "from": f"{cur_month_start.isoformat()}T00:00:00.000+02:00",
            "to": "x",
            "consumption": 8.0,
            "cost": 2.0,
            "unitPrice": 0.25,
        },
    ]

    async def fake_get_consumption(home_id, resolution, last):
        assert resolution == "DAILY"
        return nodes

    monkeypatch.setattr(server.graphql, "get_consumption", fake_get_consumption)
    report = await server.get_consumption_report(period="month", offset=0)
    assert report["current"]["kwh"] == 8.0
    assert report["previous"]["kwh"] == 10.0
    assert report["change_vs_previous"]["kwh_pct"] == -20.0


async def test_get_consumption_report_rejects_bad_period(homes):
    with pytest.raises(TibberApiError, match="period"):
        await server.get_consumption_report(period="quarter")
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag verifizieren**

Run: `uv run pytest tests/test_tools.py -v`
Expected: 4 neue FAIL mit `AttributeError: ... has no attribute 'get_consumption'`

- [ ] **Step 3: Implementierung**

In `src/tibber_mcp/server.py` vor der Tool-Registrierung einfügen:

```python
async def get_consumption(
    resolution: str = "DAILY", last: int = 7, home_id: str | None = None
) -> list[dict]:
    """Historischer Verbrauch pro Periode: kWh, Kosten (EUR), Durchschnittspreis.

    resolution: HOURLY, DAILY, WEEKLY oder MONTHLY.
    last: Anzahl der letzten Perioden.
    """
    if resolution not in {"HOURLY", "DAILY", "WEEKLY", "MONTHLY"}:
        raise TibberApiError("resolution muss HOURLY, DAILY, WEEKLY oder MONTHLY sein.")
    hid = await resolve_home_id(home_id)
    nodes = await graphql.get_consumption(hid, resolution, last)
    return [
        {
            "from": n["from"],
            "to": n["to"],
            "kwh": round(n["consumption"], 2) if n["consumption"] is not None else None,
            "cost_eur": round(n["cost"], 2) if n["cost"] is not None else None,
            "avg_price_ct_kwh": (
                round(n["unitPrice"] * 100, 2) if n["unitPrice"] is not None else None
            ),
        }
        for n in nodes
    ]


async def get_consumption_report(
    period: str = "month", offset: int = 0, home_id: str | None = None
) -> dict:
    """Aggregierter Verbrauchs-/Kostenreport mit Vergleich zur Vorperiode.

    period: 'week', 'month' oder 'year'.
    offset: 0 = laufende Periode, 1 = vorherige, usw.
    """
    hid = await resolve_home_id(home_id)
    today = datetime.now(LOCAL_TZ).date()
    if period == "week":
        nodes = await graphql.get_consumption(hid, "DAILY", 7 * (offset + 2))
    elif period == "month":
        nodes = await graphql.get_consumption(hid, "DAILY", 31 * (offset + 2) + 3)
    elif period == "year":
        nodes = await graphql.get_consumption(hid, "MONTHLY", 12 * (offset + 2))
    else:
        raise TibberApiError("period muss 'week', 'month' oder 'year' sein.")
    return analysis.build_report(nodes, period, offset, today)
```

Registrierung ergänzen:

```python
mcp.tool(get_consumption)
mcp.tool(get_consumption_report)
```

- [ ] **Step 4: Tests grün verifizieren**

Run: `uv run pytest -v`
Expected: alle passed

- [ ] **Step 5: Commit**

```bash
git add src/tibber_mcp/server.py tests/test_tools.py
git commit -m "feat: Verbrauchs-Tools mit Rohdaten und Perioden-Report"
```

---

### Task 12: Pulse-Live-Daten (`live.py` + `get_live_measurement`)

**Files:**
- Create: `src/tibber_mcp/live.py`
- Modify: `src/tibber_mcp/server.py`
- Test: `tests/test_live.py`

Der WebSocket-Teil läuft über pyTibber und wird NICHT automatisiert getestet (manuelle Verifikation in Task 13). Die Sample-Auswertung ist eine pure function und wird per TDD gebaut.

Hinweis: pyTibber-API-Oberfläche (Stand ~0.30): `tibber.Tibber(token, websession=..., user_agent=...)`, `await conn.update_info()`, `conn.get_homes()`, `await home.update_info()`, `home.home_id`, `home.has_real_time_consumption`, `await home.rt_subscribe(callback)`, `await conn.rt_disconnect()`. Beim Implementieren gegen die installierte Version prüfen (`uv run python -c "import tibber; help(tibber.Tibber)"`) und bei Abweichungen die Aufrufe anpassen — die Struktur des Moduls bleibt gleich.

- [ ] **Step 1: Failing Test schreiben**

```python
# tests/test_live.py
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
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag verifizieren**

Run: `uv run pytest tests/test_live.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'tibber_mcp.live'`

- [ ] **Step 3: Implementierung**

```python
# src/tibber_mcp/live.py
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
    token: str, home_id: str | None = None, sample_seconds: float = 5.0
) -> dict:
    """Abonniert den Pulse-Live-Stream für sample_seconds und liefert einen Snapshot."""
    async with aiohttp.ClientSession() as session:
        conn = tibber.Tibber(token, websession=session, user_agent="tibber-mcp-server")
        await conn.update_info()
        homes = conn.get_homes()
        if not homes:
            raise TibberApiError("Kein Home im Tibber-Account gefunden.")
        home = homes[0]
        if home_id is not None:
            matches = [h for h in homes if h.home_id == home_id]
            if not matches:
                raise TibberApiError(f"Unbekannte home_id '{home_id}'.")
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

        await home.rt_subscribe(on_data)
        await asyncio.sleep(sample_seconds)
        await conn.rt_disconnect()
    if not samples:
        raise TibberApiError(
            f"In {sample_seconds:.0f} Sekunden keine Live-Daten empfangen. "
            "Ist der Pulse online?"
        )
    return summarize(samples)
```

Und in `src/tibber_mcp/server.py`: oben `import os` und `from tibber_mcp import live` ergänzen, dann vor der Tool-Registrierung:

```python
async def get_live_measurement(home_id: str | None = None) -> dict:
    """Live-Messung vom Tibber Pulse: aktuelle Leistung (W), Min/Max im
    5-Sekunden-Messfenster, Tagesverbrauch (kWh) und Tageskosten (EUR).
    Benötigt einen Tibber Pulse am Zähler."""
    token = os.environ.get("TIBBER_API_TOKEN")
    if not token:
        raise TibberApiError(
            "TIBBER_API_TOKEN ist nicht gesetzt. Token unter "
            "https://developer.tibber.com/settings/access-token erstellen."
        )
    return await live.live_snapshot(token, home_id)
```

Registrierung ergänzen: `mcp.tool(get_live_measurement)`

- [ ] **Step 4: Tests grün verifizieren**

Run: `uv run pytest -v`
Expected: alle passed

- [ ] **Step 5: Commit**

```bash
git add src/tibber_mcp/live.py src/tibber_mcp/server.py tests/test_live.py
git commit -m "feat: Pulse-Live-Snapshot über pyTibber"
```

---

### Task 13: README, Smoke-Test und Einbindung

**Files:**
- Create: `README.md`

- [ ] **Step 1: Gesamte Testsuite laufen lassen**

Run: `uv run pytest -v`
Expected: alle passed

- [ ] **Step 2: Server-Start verifizieren (ohne Token, muss trotzdem starten)**

Run: `timeout 3 uv run tibber-mcp; echo "exit: $?"`
Expected: Server startet, wartet auf stdio, wird nach 3s vom timeout beendet (exit: 124). KEIN Python-Traceback beim Start — der Token wird erst beim ersten Tool-Aufruf gebraucht.

- [ ] **Step 3: README schreiben**

````markdown
# Tibber MCP Server

MCP-Server für die Tibber-API: Strompreise mit Forecast, Verbrauch/Kosten,
Günstigste-Stunden-Suche und Pulse-Live-Daten — aufbereitet für LLMs.

## Setup

1. Tibber-Token erstellen: https://developer.tibber.com/settings/access-token
2. Abhängigkeiten installieren: `uv sync`

## Einbindung in Claude Code / Claude Desktop

`.mcp.json`:

```json
{
  "mcpServers": {
    "tibber": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/Users/jurgenschilling/workspace/tibber-mcp-server",
        "tibber-mcp"
      ],
      "env": { "TIBBER_API_TOKEN": "<dein-token>" }
    }
  }
}
```

## Tools

| Tool | Zweck |
|------|-------|
| `get_home_info` | Homes, Adresse, Zählpunkt, Pulse vorhanden? |
| `get_current_price` | Preis jetzt + Einordnung (Rang, % vs. Tagesschnitt) |
| `get_price_forecast` | Stundenpreise heute/morgen mit Min/Max/Schnitt |
| `find_cheapest_hours` | Günstigste Stunden für Waschmaschine, E-Auto & Co. |
| `get_consumption` | Verbrauch pro Stunde/Tag/Woche/Monat |
| `get_consumption_report` | Aggregierter Report mit Vorperioden-Vergleich |
| `get_live_measurement` | Pulse-Live-Snapshot (aktuelle Leistung, Tageswerte) |

## Entwicklung

```bash
uv run pytest          # Tests
```

Spec: `docs/superpowers/specs/2026-07-05-tibber-mcp-server-design.md`
````

- [ ] **Step 4: Manueller End-to-End-Test mit echtem Token**

Mit gesetztem `TIBBER_API_TOKEN` (Nutzer fragen, falls nicht in der Umgebung):

Run:
```bash
TIBBER_API_TOKEN=<token> uv run python -c "
import asyncio
from tibber_mcp import server

async def main():
    print(await server.get_home_info())
    print(await server.get_current_price())
    print(await server.find_cheapest_hours(duration_hours=3, window='today'))
    print(await server.get_consumption_report(period='month'))
    print(await server.get_live_measurement())

asyncio.run(main())
"
```
Expected: echte Daten für alle fünf Aufrufe; `get_live_measurement` liefert Leistung in W (bzw. die Kein-Pulse-Fehlermeldung, falls kein Pulse installiert ist — auch das ist ein korrektes Ergebnis).

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: README mit Setup, Tools und Einbindung"
```
