# Tibber MCP Server

MCP-Server für die Tibber-API: Strompreise mit Forecast, Verbrauch/Kosten,
Günstigste-Stunden-Suche und Pulse-Live-Daten — aufbereitet für LLMs.

## Setup

1. Repo klonen: `git clone https://github.com/Schimmilab/tibber-mcp-server.git`
2. Tibber-Token erstellen: https://developer.tibber.com/settings/access-token
3. Abhängigkeiten installieren: `uv sync`

## Einbindung in Claude Code / Claude Desktop

`.mcp.json` (Pfad an den Clone-Ort anpassen):

```json
{
  "mcpServers": {
    "tibber": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/pfad/zu/tibber-mcp-server",
        "tibber-mcp"
      ],
      "env": { "TIBBER_API_TOKEN": "<dein-token>" }
    }
  }
}
```

Alternativ liegt der Token in einer `.env`-Datei im Repo-Verzeichnis (nicht eingecheckt); dann statt des `env`-Blocks:

```json
"args": ["run", "--env-file", "/pfad/zu/tibber-mcp-server/.env", "--directory", "/pfad/zu/tibber-mcp-server", "tibber-mcp"]
```

Oder per Claude-Code-CLI global installieren:

```bash
claude mcp add tibber --scope user -- uv run --env-file /pfad/zu/tibber-mcp-server/.env --directory /pfad/zu/tibber-mcp-server tibber-mcp
```

## Tools

| Tool | Zweck |
|------|-------|
| `get_home_info` | Homes, Adresse, Zählpunkt, Pulse vorhanden? |
| `get_current_price` | Preis jetzt + Einordnung (Rang, % vs. Tagesschnitt); `resolution=QUARTER_HOURLY` für die laufende Viertelstunde |
| `get_price_forecast` | Preise heute/morgen mit Min/Max/Schnitt — 24 Stunden- oder 96 Viertelstundenwerte pro Tag |
| `find_cheapest_hours` | Günstigstes Fenster für Waschmaschine, E-Auto & Co.; Laufzeit in Stunden mit Bruchteilen, im Viertelstundenraster auf 15 min genau |

**Raster:** Alle drei Preis-Tools nehmen `resolution` = `HOURLY` (Standard) oder `QUARTER_HOURLY`. Seit der Umstellung auf 15-Minuten-Preise ist das Viertelstundenraster der tatsächlich abgerechnete Preis; das Stundenraster ist der Mittelwert daraus.
| `get_consumption` | Verbrauch pro Stunde/Tag/Woche/Monat (max. 744 Perioden) |
| `get_consumption_report` | Aggregierter Report mit Vorperioden-Vergleich |
| `get_live_measurement` | Pulse-Live-Snapshot (aktuelle Leistung, Tageswerte; wartet bis zu 15 s) |

Alle Preise in ct/kWh, Summen in EUR, Zeiten in Europe/Berlin.
Fehlermeldungen sind deutsch und LLM-tauglich.

## Entwicklung

```bash
uv run pytest          # Tests
```

Spec: `docs/superpowers/specs/2026-07-05-tibber-mcp-server-design.md`
Plan: `docs/superpowers/plans/2026-07-05-tibber-mcp-server.md`

## Lizenz

MIT — siehe [LICENSE](LICENSE). Ein [Schimmilab](https://schimmilab.de)-Projekt.

## Maintainer

Schimmi — https://schimmilab.de
Issues und Pull Requests willkommen.
