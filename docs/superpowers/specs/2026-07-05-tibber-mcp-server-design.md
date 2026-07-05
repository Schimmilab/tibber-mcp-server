# Tibber MCP Server — Design

> Letzte Aktualisierung: 2026-07-05

## Ziel

Lokaler MCP-Server (Python, FastMCP, stdio-Transport), der über die Tibber-API
(Personal Access Token) Strompreise, Verbrauchsdaten, Vertrags-/Home-Infos und
Pulse-Live-Daten aufbereitet an LLMs (Claude Code / Claude Desktop) bereitstellt.

Der Server liefert nicht nur Rohdaten, sondern aufbereitete Analysen:
Günstigste-Stunden-Suche, Preis-Einordnung und Kosten-/Verbrauchsreports.

## Entscheidungen

| Frage | Entscheidung |
|-------|--------------|
| Sprache/Framework | Python ≥ 3.11 + FastMCP |
| API-Zugriff | **Hybrid:** eigener GraphQL-Client (httpx) für Preise/Verbrauch/Stammdaten; pyTibber (`tibber`-Paket) ausschließlich für den Pulse-Live-WebSocket |
| Betrieb | Lokal auf dem Mac, stdio; Start via `uv run tibber-mcp` |
| Auth | `TIBBER_API_TOKEN` aus der Umgebung (kein Token im Code oder in Dateien) |
| Multi-Home | Optionaler `home_id`-Parameter an allen Tools; Default = erstes Home |
| Paketverwaltung | uv, `pyproject.toml` |

## Architektur

```
Claude (Code/Desktop)
   │ stdio
   ▼
FastMCP Server (server.py)
   │
   ├── graphql.py   eigener Client (httpx.AsyncClient) → https://api.tibber.com/v1-beta/gql
   │                Queries: Preise/Forecast, Verbrauch, Home-/Vertragsinfos
   ├── live.py      pyTibber → graphql-ws-Subscription, nur für Pulse-Snapshot
   ├── analysis.py  reine Funktionen: Günstigste-Stunden, Preis-Kontext, Report-Aggregation
   └── cache.py     In-Memory-TTL-Cache
```

### Komponenten

- **server.py** — FastMCP-Instanz, Tool-Definitionen mit Docstrings und typisierten
  Parametern. Kein Business-Code; ruft graphql/live/analysis auf und formt die Antwort.
- **graphql.py** — dünner Client: eine `query(str, variables)`-Funktion plus benannte
  Query-Konstanten. Fehlerbehandlung (HTTP-Status, GraphQL-Errors) zentral hier.
- **live.py** — kapselt pyTibber: verbindet, sammelt ~5 Sekunden Messwerte,
  trennt sauber, gibt Snapshot zurück. Keine dauerhafte Verbindung.
- **analysis.py** — pure functions ohne I/O. Eingabe: geparste API-Daten,
  Ausgabe: Analyse-Ergebnis. Vollständig unit-testbar ohne Netz.
- **cache.py** — simpler TTL-Cache (dict-basiert). Preisdaten: gültig bis zur
  nächsten vollen Stunde. Home-Infos: 24 h. Verbrauchsdaten: 15 min.
  Zweck: Tibber-Rate-Limit (100 Requests / 5 min) nie erreichen.

## Tools

Aufgabenorientiert, kein 1:1-API-Spiegel. Alle Tools akzeptieren optional `home_id`.

### 1. `get_home_info`
Homes mit Adresse, Tarifinfo, Zählpunkt-ID und Feature-Flags
(insb. `realTimeConsumptionEnabled` → Pulse vorhanden?).

### 2. `get_current_price`
Aktueller Strompreis mit Einordnung:
- Preis (ct/kWh, total inkl. Steuern), Tibber-Level (`VERY_CHEAP` … `VERY_EXPENSIVE`)
- Rang der aktuellen Stunde im Tagesverlauf (z. B. „5.-günstigste von 24")
- Abweichung vom Tagesdurchschnitt in Prozent

### 3. `get_price_forecast`
Stundenpreise für heute und (falls publiziert) morgen.
- Pro Tag: Liste der Stundenpreise + Min/Max/Durchschnitt + günstigste/teuerste Stunde
- Morgen-Preise erscheinen bei Tibber ~13:00; vorher liefert das Tool
  `tomorrow_available: false` mit Hinweis statt leerer Liste

### 4. `find_cheapest_hours`
Parameter: `duration_hours` (Laufzeit des Verbrauchers), `window`
(`today` | `tomorrow` | `next_24h`), `contiguous` (zusammenhängender Block ja/nein).
Liefert beste Startzeit(en), Durchschnittspreis des Fensters und Ersparnis
in Prozent gegenüber dem Fenster-Durchschnitt. Anwendungsfall: Waschmaschine,
Spülmaschine, E-Auto-Ladung.

### 5. `get_consumption`
Rohverbrauch: `resolution` (`HOURLY` | `DAILY` | `WEEKLY` | `MONTHLY`),
`last` (Anzahl Perioden). Pro Periode: kWh, Kosten (EUR), Durchschnittspreis.

### 6. `get_consumption_report`
Aggregierter Report. Parameter: `period` (`week` | `month` | `year`),
`offset` (0 = laufende Periode, 1 = vorherige, …).
- Gesamt-kWh, Gesamtkosten, gezahlter Durchschnittspreis
- Vergleich zur Vorperiode (absolut und Prozent)

### 7. `get_live_measurement`
Pulse-Snapshot: abonniert den Live-Stream, sammelt ~5 Sekunden, trennt wieder.
Liefert: aktuelle Leistung (W), Min/Max im Messfenster, akkumulierter
Tagesverbrauch (kWh) und Tageskosten (EUR).
Ohne Pulse am Home: klare Fehlermeldung statt Timeout.

## Output-Format

- Kompaktes, flaches JSON — LLM-freundlich, keine tiefe Verschachtelung
- Zeiten: ISO-8601 in Europe/Berlin
- Preise: ct/kWh; Summen: EUR
- Zahlen sinnvoll gerundet (Preise 2 Nachkommastellen, kWh 2, Prozent 1)

## Fehlerbehandlung

| Fall | Verhalten |
|------|-----------|
| Token fehlt/ungültig | Klare Meldung mit Hinweis auf https://developer.tibber.com |
| Morgen-Preise noch nicht da | `tomorrow_available: false` + Erklärung |
| Kein Pulse | Fehlertext „Home hat keinen Pulse (realTimeConsumptionEnabled=false)" |
| HTTP-/GraphQL-Fehler, Rate-Limit | Verständlicher Fehlertext; nie ein Traceback zum LLM |
| Ungültige `home_id` | Fehlermeldung mit Liste der verfügbaren Home-IDs |

## Projektstruktur

```
tibber-mcp-server/
├── pyproject.toml          # uv; deps: fastmcp, httpx, tibber
├── README.md               # Setup, Token-Beschaffung, .mcp.json-Beispiel
├── docs/superpowers/specs/ # dieses Dokument
├── src/tibber_mcp/
│   ├── __init__.py
│   ├── server.py
│   ├── graphql.py
│   ├── live.py
│   ├── analysis.py
│   └── cache.py
└── tests/
    ├── test_analysis.py    # pure functions, ohne API
    ├── test_cache.py
    └── test_tools.py       # Tools gegen gemockte GraphQL-Responses (respx)
```

## Testing

- pytest + pytest-asyncio
- `analysis.py` und `cache.py`: direkte Unit-Tests, kein Mocking nötig
- Tool-Schicht: GraphQL-HTTP-Antworten mit respx mocken
- `live.py`: bewusst dünn gehalten; manuelle Verifikation gegen den echten Pulse
  (automatisierter WebSocket-Mock lohnt den Aufwand nicht)

## Einbindung in Claude Code

`.mcp.json` im jeweiligen Projekt bzw. global:

```json
{
  "mcpServers": {
    "tibber": {
      "command": "uv",
      "args": ["run", "--directory", "/Users/jurgenschilling/workspace/tibber-mcp-server", "tibber-mcp"],
      "env": { "TIBBER_API_TOKEN": "..." }
    }
  }
}
```

## Nicht im Scope

- Gehosteter HTTP-Transport (bewusst verworfen; bei Bedarf später nachrüstbar)
- Dauerhafte Live-Subscription / Push-Benachrichtigungen
- Persistente Datenhaltung (kein Verlauf über die Tibber-API-Historie hinaus)
- Steuerung von Geräten (Tibber-API ist read-only für unsere Zwecke)
