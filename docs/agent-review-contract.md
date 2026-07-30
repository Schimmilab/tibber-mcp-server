# Agent Review Contract

Prüfregeln für automatisierte Code-Reviews in diesem Repository — für Copilot Code Review, Claude Code, Codex oder einen menschlichen Reviewer, der es eilig hat.

**Wozu das hier gut ist:** Ein Review-Agent ohne Projektkontext findet Syntax und Stil. Beides ist hier schon durch Linter und Tests abgedeckt. Was er *nicht* von allein findet, sind die Fehler, die dieses Projekt tatsächlich einmal gemacht hat — und genau die stehen unten. **Jede Regel hat einen echten Vorfall als Beleg.** Erfundene Best-Practice-Regeln sind bewusst nicht dabei; sie erzeugen Rauschen und trainieren Reviewer darauf, die Liste zu ignorieren.

**Aufbau:** Die Regeln 1–4 sind der **Kern** und stehen wortgleich auch im Schwesterprojekt `wetter-mcp-server`. Die Regeln 5–6 sind **tibber-spezifisch** — sie folgen daraus, dass dieser Server einen API-Token braucht, mit Geldbeträgen rechnet und eine Live-Verbindung offen hält. Das Wetter-Repo hat keine davon.

**Wie zu lesen:** Jede Regel hat eine **Prüffrage** (der Auftrag) und ein **Kein Treffer, wenn** (die Begrenzung gegen Fehlalarme). Trifft beides nicht zu, ist es kein Fund — dann bitte schweigen.

---

# Kern (auch in `wetter-mcp-server`)

## 1 — Keine privaten Daten und keine Secrets, auch nicht in der History

**Prüffrage:** Enthält der Diff einen echten API-Token, eine Postadresse, einen Personennamen, eine private IP, eine Telefonnummer oder eine reale Mail-Adresse — auch in Beispielen, Docstrings, Testdaten, Fixtures oder Commit-Messages?

**Warum:** Beim Public-Release des Schwesterprojekts stand eine private Wohnadresse als Beispielort im Design-Dokument. Aufgefallen kurz vor dem Push; die Bereinigung ging nur noch über `git filter-repo` über die gesamte Historie. **Ein Secret-Scanner hätte das nicht gefunden** — es war kein Token, sondern eine Adresse.

**Hier kommt ein zweites Risiko dazu, das es im Wetter-Repo nicht gibt:** Dieser Server braucht `TIBBER_API_TOKEN`. Die README zeigt ihn im `env`-Block einer `.mcp.json` — **und `.mcp.json` wird in vielen Projekten eingecheckt.** Ein echter Token in einem Beispiel-Snippet ist damit einen Copy-Paste-Schritt vom Repo entfernt.

| Im Diff | Bewertung |
|---|---|
| Ein Token, der wie ein echter aussieht (nicht `<dein-token>`) | 🔴 **Fund**, auch in README-Beispielen |
| Hausanschrift, echter Personenname, private IP | 🔴 **Fund** |
| Test-Fixtures mit echten API-Antworten (enthalten `address1`, `appNickname`) | 🔴 **Fund** — Fixtures aus dem Live-Account sind eine typische Leckstelle |
| Platzhalter `<dein-token>`, `Bearer …` | ✅ kein Fund |

**Ebenfalls kein Fund: das öffentliche Pseudonym.** „Schimmi" / „Schimmilab" ist die Marke, unter der diese Repos ohnehin erscheinen — ein bereits öffentlicher Personenbezug wird durch Nennung nicht privater. **Ein Fund ist der Klarname**, nicht das Pseudonym. **Und ebenfalls kein Fund: die Git-Autor-Zeile** (`Author: … <…@…>`) — die steckt in jedem Commit jedes öffentlichen Repos und ist konfigurierte Identität, nicht Dateiinhalt. Regel 1 meint den **Text im Diff**, nicht die Commit-Metadaten.

> 🔎 **Diese Abgrenzung entstand aus einem Selbsttreffer im Schwesterprojekt:** dort stand in der Beispieltabelle zu dieser Regel eine **echte Hausanschrift** als Negativbeispiel — der Beleg für die Regel war selbst der Verstoß. Vor dem ersten Push korrigiert. **Lehre: ein Beispiel für „so sieht ein Leck aus" darf nie das echte Datum verwenden.**

**Kein Treffer, wenn:** der Wert erkennbar ein Platzhalter ist. **Und ausdrücklich kein Fund:** dass die Tool-*Antwort* zur Laufzeit eine Adresse enthalten kann (`home.address.address1` kommt aus dem Tibber-Konto des Betreibers) — das ist ein Datenfluss zum lokal laufenden Modell, kein Repo-Inhalt. Regel 1 prüft, was **im Repo landet**, nicht was der Server ausgibt.

## 2 — Kein Filter, der still verwirft

**Prüffrage:** Verwirft der Code Eingaben, ohne zu melden, wie viele? Konkret: List-Comprehensions mit Bedingung, `if not x: continue`, `try/except: pass`, `.get(key)` auf Pflichtfeldern, Typkonvertierungen mit stillem Fallback. Wenn ja — geht **irgendwo** hervor, wie viele Datensätze bewertet und wie viele übersprungen wurden?

**Warum:** Das ist die teuerste wiederkehrende Fehlerklasse dieses Bestands, dreimal in zwei Wochen:
- Ein Auswertungsskript verglich Zellen als exakte Strings und verwarf **12 von 15 Zeilen** — und gab aus den drei Restzeilen eine Korrelation aus, die das **Gegenteil** des dokumentierten Befunds behauptete.
- Ein Muster-Scanner meldete „keine wiederkehrenden Muster", obwohl **4 von 6 Einträgen** derselbe Vorgang waren.
- Ein Systemcheck meldete „✓ unauffällig", nachdem er **859 von 859** Prozessen übersprungen hatte.

**Der Kern in einem Satz: Ein Filter, der still verwirft, erzeugt keine Lücke, sondern eine falsche Zahl.**

**✅ Behoben am 2026-07-30 — der Fall bleibt als Beleg stehen. Ursprünglicher Verstoß in `analysis.py`, `price_context()`:**

```python
today = [p for p in today if p.get("total") is not None]
...
avg = sum(totals) / len(totals)
```

Preiseinträge ohne `total` flogen still raus, **und der Tagesdurchschnitt wurde anschließend über die Restmenge gebildet.** Liefert die Tibber-API für einige Stunden kein `total`, meldete die Funktion einen Durchschnitt über eine Teilmenge — als wäre es der Tagesdurchschnitt. Exakt die Fehlerklasse oben, nur mit Strompreisen statt Blutdruckwerten.

**Der Fix ist die Zahl daneben, nicht ein anderer Filter.** `price_context()` gibt jetzt zusätzlich `hours_received` und `hours_skipped` aus; `get_current_price` hängt bei einer Lücke eine `data_note` an („3 von 24 Preiseinträgen ohne Preis — Rang und Tagesdurchschnitt beziehen sich auf 21 Stunden, nicht auf den ganzen Tag"). **Bei vollständigen Daten bleibt die Antwort unverändert still** — ein Abdeckungsausweis, der immer redet, wird genauso ignoriert wie gar keiner. Vier Tests decken beide Richtungen ab.

**Kein Treffer, wenn:** das Verwerfen der Zweck der Funktion ist und der Umfang aus dem Rückgabewert hervorgeht.

**Kalibrierungsbeispiel aus dem Bestand — hier greift die Regel bewusst NICHT.** In `live.py` filtert der Callback:

```python
def on_data(pkg: dict) -> None:
    data = (pkg.get("data") or {}).get("liveMeasurement")
    if data:
        samples.append(data)
```

Auch das verwirft still. Aber: Pakete ohne `liveMeasurement` auszusortieren **ist** der Zweck, und die Zahl der gesammelten Messwerte steht am Ende in der Antwort (`samples`). **Kein Fund.** Der Unterschied zu `price_context()` ist, dass dort aus der Restmenge ein **Mittelwert** gebildet und als Tageswert ausgegeben wird — ein Ergebnis ohne Nenner. Wer beide Stellen gleich behandelt, hat die Regel nicht verstanden.

## 3 — Fehler müssen sagen, was zu tun ist

**Prüffrage:** Nennt jede neue oder geänderte Fehlermeldung (a) was schiefging, (b) mit welchem Wert, und (c) was der Aufrufer dagegen tun kann? Ein MCP-Server antwortet einem Modell, nicht einem Menschen mit Debugger.

**Warum:** Der Bestand setzt hier den Maßstab und **das ist hier wertvoller als im Wetter-Repo** — dieser Server kann aus einem Grund scheitern, den der Nutzer selbst beheben muss: fehlender Token. Die vorhandene Meldung nennt deshalb die konkrete URL zum Erstellen (`developer.tibber.com/settings/access-token`), und `Unbekannte home_id '{id}'. Verfügbar: {available}` listet die gültigen Werte gleich mit. Diese Qualität soll nicht durch eine beiläufige `raise ValueError("invalid input")` verwässert werden.

**Kein Treffer, wenn:** die Meldung an einer Stelle steht, die der Aufrufer nie sieht (interne Assertion, Testcode).

## 4 — Abhängigkeiten brauchen eine Ober- und eine Untergrenze

**Prüffrage:** Hat jede Zeile in `dependencies` beide Grenzen?

**Warum:** Eine Bestandsaufnahme über alle eigenen MCP-Server ergab Pins von `mcp>=0.9.0` bei installiertem `1.25.0` bis zu gar keiner Angabe — **nirgends eine Obergrenze.** Als das Protokoll auf eine zustandslose Fassung umgestellt wurde, hätte ein frisches `pip install` die neue SDK-Generation ungeprüft in produktiv laufende Server gezogen.

**⚠️ Offener Verstoß im Bestand — alle vier:** `fastmcp>=3`, `httpx>=0.27`, `pyTibber>=0.30`, `aiohttp>=3.9`. **Hier wiegt es schwerer als im Wetter-Repo**, weil `pyTibber` und `aiohttp` die Live-Verbindung tragen: ein Major-Sprung bei einem der beiden bricht `live.py`, und zwar erst zur Laufzeit beim nächsten Live-Abruf.

**Kein Treffer, wenn:** die Obergrenze bewusst offen ist und daneben als Kommentar begründet steht.

---

# tibber-spezifisch

## 5 — Geldbeträge und Zeitreihen: Einheit, Zeitzone und Vollständigkeit müssen stimmen

**Prüffrage:** Bei jeder Änderung an Preis-, Kosten- oder Verbrauchsrechnung — ist die **Einheit** eindeutig (ct/kWh vs. EUR, kWh vs. Wh), die **Zeitzone** explizit (Europe/Berlin, nicht Naive-Datetime), und ist die **Periode vollständig**?

**Warum:** Der Bestand hat genau in dieser Klasse schon zweimal falsche Zahlen produziert — außerhalb dieses Repos, aber mit denselben Daten:
- Das `previous`-Feld eines Verbrauchsreports lieferte **unvollständige Perioden**: derselbe Juni erschien einmal als 442 kWh, einmal als 133,88 kWh bei 5 statt 30 Tagen — der echte Wert war 682,87. Daraus entstand ein „+70 % Stromsprung", **den es nie gab**.
- Ein Ladevorgang wurde nach *Uhrzeit* der Wallbox zugerechnet statt am *Zählerstand* — 3,6 kWh Grundlast landeten im falschen Topf, obwohl ein exakter Zähler vorlag.

**Die Regel daraus: Wenn ein Zähler existiert, nicht über das Zeitfenster schätzen. Und eine aggregierte Periode ohne Angabe, wie viele Einträge sie enthält, ist kein Messwert.** Der Server sollte die Zahl der einbezogenen Perioden mit ausgeben, nicht nur die Summe.

**Kein Treffer, wenn:** die Funktion rohe API-Werte unverändert durchreicht und die Einheit im Feldnamen oder Docstring steht.

## 6 — Live-Verbindung: begrenzt, aufgeräumt, und ohne stille Endlosschleife

**Prüffrage:** Bei Änderungen an `live.py` — hat jeder Abruf ein **Zeitlimit**, wird die WebSocket-Verbindung in **jedem** Pfad geschlossen (auch im Fehlerfall), und ist ausgewiesen, **wie viele Messwerte** in das Ergebnis eingeflossen sind?

**Warum:** Das ist die einzige Stelle im Projekt, die eine dauerhafte Verbindung nach außen hält. Der Rest ist Request/Response und kann höchstens langsam sein. Eine nicht geschlossene Verbindung fällt im Alltag nicht auf, weil der Prozess weiterläuft — bis das Gerät am anderen Ende irgendwann keine Sitzungen mehr annimmt. Verwandter Fall aus dem eigenen Bestand: ein Hintergrunddienst verbrannte **58 Stunden CPU-Zeit in 13 Tagen**, ohne dass ein Bordmittel ihn meldete, weil er nur im Momentanwert unauffällig war.

Der aktuelle Stand macht es richtig, und zwar in drei Punkten, die zusammen das Prüfziel bilden:

| Eigenschaft | Wie es umgesetzt ist |
|---|---|
| Zeitlimit | `sample_seconds` begrenzt die Wartezeit, Abbruch außerdem früher bei genug Messwerten |
| Aufräumen in **jedem** Pfad | `finally: await conn.rt_disconnect()` innen, `async with aiohttp.ClientSession()` außen — auch bei einer Ausnahme mitten im Abruf |
| Nachvollziehbarkeit | die Antwort nennt die Zahl der Messwerte; bei null Messwerten kommt eine handlungsleitende Meldung („Ist der Pulse online?") statt eines leeren Ergebnisses |

**Eine Änderung, die eine dieser drei entfernt, ist ein Fund** — besonders das `finally`, weil sein Fehlen im Normalbetrieb nichts kaputtmacht und erst nach vielen Fehlversuchen auffällt.

**Kein Treffer, wenn:** die Änderung nur die Aufbereitung des bereits eingesammelten Ergebnisses betrifft.

---

## Was dieser Vertrag bewusst NICHT enthält

- **Keine Stilregeln.** Formatierung und Importreihenfolge macht der Linter.
- **Keine Testabdeckungs-Quoten.** Die Suite ist grün oder nicht.
- **Keine Architekturvorschläge.** 677 Zeilen in sechs Modulen, sauber getrennt (`graphql` I/O · `analysis` rein funktional · `cache` · `live` · `server` Tool-Schicht). Wer hier Schichten einzieht, löst ein Problem, das es nicht gibt.

## Herkunft und Übertragung

Alle Regeln stammen aus dokumentierten Vorfällen im eigenen Bestand. Der Vertrag ist **werkzeugunabhängig** formuliert — dieselbe Datei soll für Copilot Code Review, Claude Code und Codex taugen, statt für jedes Werkzeug eine eigene Prompt-Variante zu pflegen.

**Übertragen aus `wetter-mcp-server` am 2026-07-30.** Dabei zeigte sich die eigentlich interessante Zahl: **vier der fünf Wetter-Regeln waren wortgleich übertragbar, eine nicht** — „Was die README verspricht, muss ausführbar sein" war dort an `.env.example` aufgehängt, das es hier gar nicht gibt (der Token wird über den `env`-Block der MCP-Konfiguration gesetzt). Stattdessen kamen **zwei neue Regeln** dazu, die sich direkt aus dem Zweck dieses Servers ergeben: er rechnet mit Geld und hält eine Live-Verbindung. **Die Regeln 1–4 sind damit Kandidaten für eine wiederverwendbare Vorlage; 5–6 sind es ausdrücklich nicht.**
