# L1 "Stabiler Cloud-Durchstich" – Abschlussbericht

Branch `l1-cloud-durchstich`, Ausgangsstand `59eabc7`.
Umfang: AP 0–3 plus CLI-Beispielablauf. Nicht nach `main` zusammengeführt.

Adressaten: Entwickler und Reviewer des Repositories.

## 1 Ausgangszustand

`HEAD` war exakt der geprüfte Stand `59eabc7`; ein Vergleich mit dem Branch war
also nicht nötig. Es gibt keine `AGENTS.md`. Die Abhängigkeiten waren im
Arbeitsumfeld nicht installiert; nach Einrichten einer venv liefen die
bestehenden Tests: **7 bestanden, 1 übersprungen** (scikit-learn fehlte).
Der Fehler lag also nicht in den Tests, sondern darin, dass die Tests die
Fehler nicht abdeckten.

Reproduzierte Fehler des Ausgangsstands (`tests/test_regression_ap0.py`):

| Fehler | Nachweis |
| - | - |
| Seitenverlust bei Blockbildung | 5 Seiten, `combine=3` → nur Seiten 1–3 im Prompt; Seiten 4 und 5 erreichen das Modell nie |
| Seitenverlust bei Überlappung | 7 Seiten `combine=4 overlap=2` verliert Seite 7; 10 Seiten `combine=3` verliert Seite 10 |
| Leere Platzhalter-Testfälle | gemischtes JSON-Array erzeugte für jedes Nicht-Objekt ein `{}` mit leeren Feldern |
| Ergebnisse nicht unterscheidbar | leere Ausgabe, ungültiges JSON und „gültig ohne Testfälle" ergaben alle `[]` |
| Stille Typumwandlung | `actions: {"step": "do"}` wurde unbeanstandet zu einem String |
| Stiller CLI-Fallback | jeder HTTP-Fehler startete `ollama run` über `subprocess` |

## 2 Geändertes Verhalten

### Blockbildung (AP 1)
`application/chunking.build_blocks` erzeugt nur Blöcke, die die Abdeckung
erweitern, behält aber jeden Restblock. Die Abdeckung wird nach dem Aufbau
geprüft (`verify_coverage`) und schlägt sonst hart fehl. Getestet für alle
Kombinationen von 1–12 Seiten mit `combine` 1–5 und jeder zulässigen
Überlappung.

### Antwortprüfung (AP 1)
Fünf unterscheidbare Ergebnisse: `OK`, `EMPTY`, `INVALID_JSON`,
`SCHEMA_INVALID`, `NO_TEST_CASES`. Jedes unbrauchbare Ergebnis trägt einen
Befund mit Pfad und Meldung. Unbalanciertes JSON und `done_reason == "length"`
werden als abgebrochene Ausgabe gemeldet. Reparierte Antworten werden erneut
vollständig geprüft; nach höchstens zwei Reparaturen (konfigurierbar) wird der
betroffene Teil sichtbar zurückgewiesen und bleibt mit Quellreferenzen im
Abschlussbericht.

### Laufstatus (AP 1)
`completed`, `partial`, `failed`, `cancelled`. Ein Lauf ohne angenommene
Einheit ist nie `completed`. 100 % Fortschritt heißt nur, dass die Verarbeitung
beendet ist; die GUI zeigt jetzt `✅/⚠️/❌/⏹️` mit dem echten Status statt
„✅ generiert".

### Schichten (AP 2)
`src/spec2test/{domain,application,adapters}`. Die Domäne importiert nichts
außer sich selbst, die Anwendung keine konkreten Adapter. `tests/test_architecture.py`
prüft das über den Importbaum und lädt Domäne und Anwendung zusätzlich mit
blockierten Bibliotheken (Qt, requests, PyMuPDF, pandas, openpyxl, sklearn).
`core.py` ist nur noch Kompatibilitätsschicht; `application/legacy.py` ist die
ausdrücklich veraltete Brücke für die GUI und wird mit L2 entfernt.

### Vertrag (AP 2)
Ein versioniertes Objekt mit `schema_version`, `test_cases`, `open_questions`.
Keine gemischten Typen, zusätzliche Felder werden zurückgewiesen, und der
Vertrag enthält weder IDs noch Reviewstatus – beides vergibt die Anwendung.
Ein Modell kann seine eigene Antwort also nicht freigeben.

### Ollama Cloud (AP 3)
Explizite Modi `ollama_cloud` und `ollama_local`. Cloud über
`https://ollama.com/api/chat`, Bearer-Authentifizierung mit `$OLLAMA_API_KEY`,
echte Chat-Rollen, `stream: false`, Antwort aus `message.content`. Modellliste
über `/api/tags`, manuelle Namen erlaubt. Getrennte Verbindungs- und
Lesezeitlimits, das Lesezeitlimit wird zusätzlich durch das Auftragsbudget
begrenzt. 401/403 verständlich und ohne Wiederholung, 404 ohne Wiederholung,
429 mit `Retry-After`, 408/5xx und Transportfehler mit begrenzten
Wiederholungen und Backoff; jede Wiederholung wird mit dem Hinweis protokolliert,
dass sie erneut Nutzung verursachen kann. GPU-Parameter nur im lokalen Modus.

Der API-Key wird ausschließlich aus der Umgebung gelesen, nicht in `config.yml`
geschrieben, nicht geloggt, aus Fehlermeldungen entfernt (`***`), nicht im
`repr` ausgegeben und nur an den konfigurierten Host gesendet; Weiterleitungen
werden nicht verfolgt, sondern als Fehler gemeldet.

### Weitere Änderungen
* PDF-Lesen auf PyMuPDF vereinheitlicht, PyPDF2 entfernt.
* Modellliste in der GUI über `/api/tags` statt über `ollama list`; der stille
  CLI-Aufruf hätte lokale Aliase mit API-Namen vermischt.
* Antwortcache keyed auf Endpoint, Modell, Optionen, Prompt- und Schemaversion;
  Einträge ohne aktuelle Formatmarkierung werden ignoriert, es werden nur
  validierte Ergebnisse gespeichert, und ein Treffer wird als „kein
  fachlicher Freigabenachweis" gekennzeichnet.
* `config.yml`: benutzerspezifische Windows-Pfade durch portable relative
  Beispiele ersetzt, `cloud`- und `generation`-Abschnitt ergänzt. Der alte
  Leser (`config_models.AppConfig`) funktioniert weiter.
* **Die GUI überschrieb `config.yml` bisher vollständig** und hätte die neuen
  Abschnitte beim Schließen des Fensters verworfen – aufgefallen, weil der
  GUI-Test die Repository-Konfiguration zurückgesetzt hat. `_save_persisted`
  führt die GUI-Werte jetzt über den bestehenden Dateiinhalt zusammen, sodass
  unbekannte Schlüssel erhalten bleiben; die Tests arbeiten auf einer
  temporären Kopie.
* Neue Abhängigkeit **pydantic**, begründet in `requirements.txt`: die Cloud
  erzwingt kein Schema, deshalb ist lokale Validierung Pflicht; pydantic
  liefert strikte Modelle mit „extra verboten" und präzise Fehlerpfade, die
  direkt in den Reparaturprompt gehen.

## 3 Referenzdaten (AP 0)

Zehn synthetische Anforderungen in `tests/fixtures/`, deutsch und englisch:
Normalfall, untere und obere Grenze, Zeitbedingung, Mehrfachbedingungen,
fehlender Grenzwert, widersprüchliche Vorgaben (0008/0009), fehlende ID,
doppelte externe ID in einem zweiten Dokument und eine Anforderung über die
Seitengrenze 3/4. `expectations.yml` hält je Beispiel Testabsichten, notwendige
Belege und unzulässige Ergänzungen fest. Kein Test erwartet eine
Modellformulierung. Die PDF wird aus versionierten Seitentexten deterministisch
erzeugt, damit keine Binärdatei die Referenzwahrheit bestimmt.

## 4 Ausgeführte Tests

```
pytest -q  →  710 bestanden, 4 übersprungen (mit scikit-learn)
```

Übersprungen: vier Live-Tests (benötigen `SPEC2TEST_LIVE=1`) und ein
TF-IDF-Test ohne scikit-learn.

| Datei | Inhalt |
| - | - |
| `test_regression_ap0.py` | die sechs Ausgangsfehler als Regressionsnetz |
| `test_domain.py` | leere Felder, unbekannte und doppelte IDs, fehlende Erwartungen, Grenzwerte ohne Beleg, Freigabe trotz offener Punkte |
| `test_contracts.py` | Parse-Ergebnisse, strikte Typen, Legacy-Import ohne Index-Paarung |
| `test_chunking.py` | Abdeckung für 1–12 Seiten × `combine` 1–5 × jede Überlappung |
| `test_ingest.py` | Quellstellen, Seitengrenze, doppelte IDs, leere PDF-Seiten, Unicode, unbekanntes Format |
| `test_generate.py` | Teilfehler, begrenzte Reparatur, Abbruch, Budget, abgebrochene Antworten, Fortschritt |
| `test_ollama_client.py` | Erfolg, fehlender Key, 401/403, 429 mit `Retry-After`, Zeitüberschreitungen, ungültige und abgebrochene Antworten, Weiterleitungen, Key-Redaktion |
| `test_mode_isolation.py` | ein Cloudfehler erreicht nie einen lokalen Endpoint |
| `test_architecture.py` | Schichtregeln über den Importbaum |
| `test_end_to_end.py` | Fixtures → Fake-Modell → Prüfung → Speichern → erneutes Laden, plus CLI |
| `test_gui_status.py` | Fenster baut, Status wird wahrheitsgemäß angezeigt, kein CLI-Aufruf |
| `test_config.py` | portable Pfade, kein Key in der Datei, beide Konfigurationsleser |

Dazu kommt der Nachweis, dass Speichern aus der GUI den `cloud`-Abschnitt und
unbekannte Schlüssel nicht mehr verwirft.

CI benötigt keine kostenpflichtigen Cloud-Aufrufe: alle HTTP-Tests laufen gegen
einen Mock-Transport, alle Pipeline-Tests gegen Fake-Modelladapter.

## 5 Live-Test

Der Cloud-Live-Test über `https://ollama.com` wurde **nicht** ausgeführt: in
dieser Arbeitsumgebung lag kein `OLLAMA_API_KEY` vor, und das Abfragen von
Zugangsdaten wurde bewusst unterlassen. Der Cloud-Pfad ist damit ausschließlich
durch Mock-Tests abgedeckt.

Stattdessen wurde ein echter Modellaufruf im Modus `ollama_local` gegen den
lokal laufenden Ollama-Server (0.40.5) nachgewiesen, der nur Cloud-Alias-Modelle
bereitstellt:

```
SPEC2TEST_LIVE=1 SPEC2TEST_LIVE_MODE=ollama_local \
  SPEC2TEST_LIVE_MODEL=gpt-oss:20b-cloud \
  SPEC2TEST_LIVE_HOST=http://localhost:11434 pytest tests/test_live_cloud.py
→ 3 bestanden, 1 übersprungen (18 s)
```

Ergebnis: `/api/tags` meldete sieben Modelle, alle mit Alias-Endung
(`gpt-oss:120b-cloud`, `nemotron-3-super:cloud`, …) – genau die Namensform, die
laut Plan nicht mit API-Namen gleichgesetzt werden darf. Aus einer
synthetischen Anforderung entstand im ersten Versuch ein vertragsgültiger
Entwurf (`completed`, 1 Entwurf, 0 Reparaturen).

Der Lauf bestätigte die zentrale Regel in der Praxis: das Modell ergänzte eine
Kalibriertoleranz „±0.5 °C", die in der Anforderung nicht vorkommt. Die lokale
Validierung meldete `C_UNSUPPORTED_VALUE`, der Entwurf wurde als `draft`
gespeichert, der Befund blieb als offene Frage erhalten und sperrt die
Freigabe. Strukturell gültig war die Antwort trotzdem – formale Gültigkeit und
fachliche Richtigkeit bleiben getrennt.

## 6 Beispielablauf

```bash
python -m spec2test.cli run --spec tests/fixtures/specs/spec_a.md --mode fake
```

Ohne Netzwerk: zehn Anforderungen erfasst (neun mit externer ID, eine mit
interner ID und Hinweis auf Grenzprüfung), zehn Entwürfe als `draft` mit
Quellstellen, drei offene Fragen genau bei den drei Anforderungen ohne
messbaren Wert (fehlender Grenzwert, Widerspruch ohne Zahlen, Anforderung ohne
ID). Lauf gespeichert, Exitcode 0.

## 7 Selbstprüfung

**Sind alle Quellen enthalten?** Ja. Jede Seite landet in mindestens einem
Block, geprüft für alle getesteten Konfigurationen und zusätzlich zur Laufzeit
durch `verify_coverage`. Leere oder bildbasierte PDF-Seiten werden gemeldet,
nicht verschwiegen; OCR bleibt außerhalb des Umfangs. Eine Anforderung über
eine Seitengrenze behält beide Seiten als Quellstellen.

**Werden ungültige Antworten sichtbar zurückgewiesen?** Ja. Unbekannte
Requirement-IDs, fehlende Pflichtschritte, Schritte ohne Erwartung und
ungültige Typen blockieren die Übernahme als Entwurf. Nach höchstens zwei
Reparaturen wird die Einheit mit Code, Pfad, Meldung und Quellreferenzen
zurückgewiesen; die Rohantwort jedes Versuchs bleibt erhalten.

**Können Cloudfehler den lokalen Modus unbemerkt aktivieren?** Nein. Der Modus
ist explizit und wird nirgends umgeschrieben; `bootstrap.build_model` lehnt
einen Modus-Konflikt ab. Bei fehlendem Key wird kein Request gesendet. Der
CLI-Fallback und `subprocess` sind aus `core.py` entfernt.
`test_mode_isolation.py` prüft für 401, 500, Verbindungsabbruch und
Lesezeitüberschreitung, dass kein lokaler Endpoint kontaktiert wird.

**Bleiben offene Fragen erhalten?** Ja. Fragen des Modells und Befunde der
Validierung werden als `OpenQuestion` mit Herkunft (`model`, `validation`)
gespeichert, in der Ausgabe gezeigt, in die Laufdatei geschrieben und sperren
die Freigabe.

**Sind Fachfreigabe und formale Validierung getrennt?** Ja.
`validate_test_case` entscheidet nur über `blocks_draft` und
`blocks_approval`. Eine Modellantwort beginnt immer als `draft`; der Vertrag
kennt kein Statusfeld. `approved` entsteht nur durch eine ausdrückliche
Testerentscheidung, und eine Bearbeitung danach erzeugt eine neue Revision und
hebt die Freigabe auf.

## 8 Verbleibende Einschränkungen

1. **Cloud-Live-Aufruf nicht nachgewiesen** – fehlende Zugangsdaten. Mit
   `OLLAMA_API_KEY` genügt
   `SPEC2TEST_LIVE=1 SPEC2TEST_LIVE_MODEL=gpt-oss:120b pytest tests/test_live_cloud.py`.
2. **Modellvergleich nicht durchgeführt** – gehört zu L3 und setzt mehrere
   Durchläufe gegen zwei Cloud-Modelle voraus.
3. **Die GUI generiert weiter über den Legacy-Pfad.** Modus, Host und
   Zeitlimits teilt sie inzwischen mit Worker und CLI, der Generierungsablauf
   selbst nutzt aber weiter das flache Format ohne Anforderungsbezug.
   Quellenansicht, Prüfbefunde, offene Fragen und Revisionen sind AP 5;
   `application/legacy.py` ist der dafür vorgesehene, als veraltet markierte
   Pfad.
4. **Die Modellliste blockiert weiterhin kurz den Qt-Thread** (Verbindungs-
   zeitlimit, voreingestellt 10 s, in der GUI einstellbar) – wie im
   Ausgangsstand, jetzt aber mit einer Schaltfläche zum erneuten Abruf statt
   nur beim Start. Die nichtblockierende Variante ist AP 5.
5. **Anforderungserfassung ist rein deterministisch.** Freitext ohne ID wird
   über Absatzgrenzen und Modalverben erkannt; dabei kann erläuternde Prosa als
   Kandidat auftauchen. Solche Treffer sind als `needs_clarification` mit
   Hinweis auf Grenzprüfung gekennzeichnet. Modellgestützte Segmentierung ist
   AP 4.
6. **Tokenbudget ist zeichenbasiert.** `input_char_budget` teilt sichtbar auf
   und schneidet nie still ab, ist aber kein echtes Tokenbudget; Reserven für
   Systemprompt, Antwort und Reparatur sind noch nicht modelliert (AP 4).
7. **Keine Projektdateien mit Revisionen, kein Excel-Export der Schritte,
   keine Abdeckungskennzahlen** – AP 5 und AP 6.
8. **Streaming ist nicht umgesetzt** (`stream: false`), wie im Plan als
   eigenes Arbeitspaket vorgesehen.
9. **Kommentare in `config.yml` gehen verloren, sobald die GUI eine Einstellung
   speichert.** Beim Nacharbeiten fiel auf, dass schon das Öffnen und Schließen
   des Fensters die Datei umschrieb und damit die Kommentare entfernte. Die GUI
   schreibt jetzt nur noch, wenn sich tatsächlich ein Wert geändert hat –
   eine unveränderte Sitzung lässt die Datei unberührt. Ändert man dagegen eine
   Einstellung in der GUI, bleiben alle Werte und unbekannten Abschnitte
   erhalten, die Kommentare aber nicht: der YAML-Writer kann sie nicht
   zurückschreiben. Eine kommentarerhaltende Bibliothek wäre eine neue
   Abhängigkeit und ist dafür nicht gerechtfertigt; die bessere Lösung ist, den
   GUI-Zustand von der Projektkonfiguration zu trennen (das vorhandene, bisher
   ungenutzte `QSettings`-Objekt wäre der Ort dafür).
10. **Der Umgebungs-Zufall** `pandas 3.1.1` / `PyMuPDF 2.1` wurde für die Tests
   benutzt; `requirements.txt` nennt weiter die festgeschriebenen Versionen des
   Ausgangsstands. Eine Abgleichprüfung der Pins steht aus.

## 10 Nacharbeiten aus dem Review von `b5be55b`

Vier Befunde, alle reproduziert, behoben und durch Regressionstests abgedeckt
(`tests/test_regression_review.py`, `tests/test_gui_status.py`).

**P1 – Die GUI übernahm den Cloud-Modus nicht.** Bestätigt: `config.yml` sagte
`ollama_cloud`, die GUI lief auf `ollama_local`. `_collect_cfg()` lieferte gar
keinen `cloud`-Abschnitt, und ein Host-Feld existierte überhaupt nicht.
Behoben durch eine gemeinsame `CloudConfig` (Modus, Host, Verbindungs- und
Lesezeitlimit, Wiederholungen, Auftragsbudget) in `config_models.py`, die GUI,
Worker und CLI über `bootstrap.ollama_settings_from_config` identisch
auswerten. Die GUI hat jetzt eine Gruppe **Endpoint** mit Modusauswahl,
Host-Feld, beiden Zeitlimits und einer Verbindungsprüfung; GPU-Bedienelemente
sind im Cloud-Modus abgeschaltet, die Modellliste folgt dem gewählten Modus,
und `AgentLocal` nimmt die gemeinsamen `OllamaSettings` direkt entgegen.
Geprüft wird der tatsächliche Aufrufweg inklusive `_kick_off()`.

**P1 – Ein unvollständiger Lauf konnte `completed` heißen.** Bestätigt mit der
Reproduktion aus dem Review. Ursache: beim erschöpften Zeitbudget brach die
Schleife ab, ohne die nicht gestarteten Einheiten zu erfassen; `resolve_status`
sah nur angenommene Einheiten. Behoben durch `UnitStatus.NOT_STARTED`:
nicht gestartete Einheiten werden mit Requirement-IDs, Quellstellen und dem
Befund `E_UNIT_NOT_STARTED` erfasst und erscheinen im Bericht als
`NOT STARTED`. `GenerationRun.planned_unit_count` vergleicht geplante mit
tatsächlich bearbeiteten Einheiten; `is_incomplete` erzwingt mindestens
`partial`. Gilt auch für den Abbruch, der weiterhin `cancelled` bleibt.

**P1 – Ergebnisdateien konnten überschrieben werden.** Bestätigt: zwei Läufe
derselben Sekunde ergaben denselben Dateinamen, der erste Lauf war verloren.
Lauf-IDs sind jetzt `run-<UTC-Zeitstempel>-<12 Hex>` (sortierbar und
kollisionsfrei, 200 IDs in einer Sekunde bleiben verschieden), die
Legacy-Brücke nutzt dieselbe Funktion. Die Ablage schreibt in eine eindeutige
temporäre Datei (`tempfile.mkstemp` im Zielverzeichnis) mit `fsync` vor dem
atomaren Ersetzen, räumt sie bei Fehlern auf und verweigert das Überschreiben
eines bestehenden Laufs mit `RunAlreadyStoredError`; `overwrite=True` bleibt
als ausdrückliche Entscheidung möglich.

**P2 – Der dokumentierte CLI-Aufruf lief nach normaler Installation nicht.**
Bestätigt: es gab keine Paketkonfiguration. Ergänzt: `pyproject.toml` mit
src-Layout, Laufzeitabhängigkeiten (requests, pydantic, PyYAML, PyMuPDF),
Extras `gui`, `rag`, `dev` und dem Einstiegspunkt `spec2test = spec2test.cli:main`.
Zusätzlich sucht `bootstrap` die Konfiguration zuerst im Arbeitsverzeichnis,
damit ein installiertes Kommando die Konfiguration des jeweiligen Projekts
verwendet. Geprüft in einer frischen venv außerhalb des Repositories: sowohl
`spec2test run …` als auch `python -m spec2test.cli run …` laufen ohne
`PYTHONPATH` durch.

Beim Nacharbeiten zusätzlich aufgefallen und behoben: schon das Öffnen und
Schließen der GUI schrieb `config.yml` neu und entfernte dabei die Kommentare.
Die GUI schreibt jetzt nur noch bei einer echten Änderung.

Teststand danach: **437 bestanden, 5 übersprungen**.

### Lokaler Modus als Standard, mit der ausgelieferten Konfiguration nachgewiesen

`config.yml` steht jetzt auf `mode: ollama_local` mit `gpt-oss:120b-cloud`.
Grund: ist der lokale Ollama-Daemon bei ollama.com angemeldet, laufen
Cloud-Modelle über ihn, und Spec2Test braucht keinen eigenen Schlüssel – der
Daemon authentifiziert. Das ist der Weg ohne Zusatzaufwand.

Nachgewiesen ohne jedes Flag, nur mit der ausgelieferten Konfiguration:

```
$ python -m spec2test.cli models
7 model(s) at http://localhost:11434: beam:cloud, gemma4:31b-cloud,
  gpt-oss:120b-cloud, gpt-oss:20b-cloud, nemotron-3-{nano,super,ultra}

$ python -m spec2test.cli run --spec tests/fixtures/specs/spec_b.md
run …: completed
  model=gpt-oss:120b-cloud endpoint=http://localhost:11434/api/chat mode=ollama_local
  units planned=2 accepted=2 rejected=0 not started=0
  test case drafts=3 open questions=13
```

Bemerkenswert am Ergebnis: `gpt-oss:120b` lieferte vertragsgültiges JSON ohne
Reparatur, Schritte mit paarweisen Erwartungen – und stellte von sich aus zwei
brauchbare Rückfragen statt Signalnamen zu erfinden („What specific signal or
status flag defines a 'valid self-diagnosis'?"). Genau das soll die Regel
bewirken.

Dabei fiel auf, dass das CLI den Modus aus `config.yml` ignorierte und immer
`fake` nahm; `--mode` greift jetzt nur noch als ausdrückliche Übersteuerung.

### Direkter Cloud-Nachweis weiterhin offen

In dieser Umgebung ist `OLLAMA_API_KEY` nicht gesetzt. Der Adapter meldet das
selbst, ohne dass Zugangsdaten angefasst werden mussten:

```
$ PYTHONPATH=src python -m spec2test.cli models --mode ollama_cloud
model list failed: mode ollama_cloud requires an API key in $OLLAMA_API_KEY;
no request was sent and the local CLI is not used as a fallback
$ echo $?
1
```

Der Nachweis steht damit aus und braucht einen Schlüssel in der Umgebung
(`cp .env.example .env`, Schlüssel von https://ollama.com → Settings → Keys
eintragen). Der Aufruf über den lokalen Ollama-Server mit Cloud-Modell ersetzt
ihn nicht – das ist im Review zu Recht festgehalten. Die beiden Wege
unterscheiden sich im Transport: `ollama_local` schickt `POST
http://localhost:11434/api/chat` ohne Authorization-Header, `ollama_cloud`
schickt `POST https://ollama.com/api/chat` mit `Authorization: Bearer …`.
Abgedeckt ist der Cloud-Weg bisher nur durch Mock-Tests.

## 11 GUI-Migration und Schlüsselanbindung

### API-Key über `.env`

`bootstrap.load_env_file` liest `KEY=VALUE`-Zeilen aus einer `.env` im
Arbeitsverzeichnis in die Umgebung – Kommentare, Leerzeilen, `export`-Präfixe
und Anführungszeichen inbegriffen. Die Funktion gibt **nur die Variablennamen**
zurück, nie die Werte, damit ein Aufrufer keinen Schlüssel versehentlich
protokollieren kann. Eine bereits exportierte Variable gewinnt. Ein ~20-zeiliger
Parser statt einer neuen Abhängigkeit.

`.env.example` ist die Vorlage, `.env` ist in `.gitignore` aufgenommen
(`!.env.example` als Ausnahme). CLI und GUI-Worker laden die Datei beim Start
und melden nur den Namen der gesetzten Variable.

### GUI auf die neue Pipeline umgestellt

`workers.DraftTask` ersetzt `GeneratorTask` als Aktion der GUI: Dokumentimport,
`generate_drafts()`, strikte Validierung und Ablage des Laufs. `GeneratorTask`
bleibt als dokumentierter Legacy-Einstieg für Skripte erhalten.

* **Dateiauswahl** nimmt jetzt PDF, TXT und Markdown und mehrere Dateien
  gleichzeitig.
* **Vier Ansichten**: *Drafts* (Testfall mit Vor-/Nachbedingungen, Schritten,
  Erwartungen, Messgrößen samt Belegzitat und offenen Fragen), *Requirements*
  (erfasste Anforderungen mit Quellstelle je Seite/Abschnitt und
  Klärungsstatus), *Findings* (Laufbericht, offene Fragen, zurückgewiesene und
  nicht gestartete Anforderungen mit Quellreferenz), *Flat table* (speist die
  bestehenden CSV-/Excel-Schaltflächen und das Insight-Panel).
* **Abbruch** über eine eigene Schaltfläche: keine neuen Requests, ein
  laufender endet in seinem Zeitlimit; der Lauf wird als `cancelled` angezeigt.
* `application.legacy.legacy_rows` flacht die neuen Entwürfe für den Export ab
  und behält die Zuordnung, indem Aktionen und Erwartungen ihre Schrittnummer
  als Präfix tragen. Ein echter Export mit getrennten Tabellen bleibt AP 6.

### Zusätzlich gefunden: Auth-Fehler wurden pro Anforderung wiederholt

Beim ersten GUI-Durchlauf ohne Schlüssel erzeugte ein fehlender API-Key zehn
identische Fehlschläge – einen pro Anforderung. `ModelError` hat deshalb neben
`retryable` jetzt ein Flag `fatal`, gesetzt für `MissingApiKeyError`,
`AuthenticationError` und `RedirectNotFollowedError`. Die Generierung bricht
nach dem ersten solchen Fehler ab und erfasst die restlichen Anforderungen als
`NOT_STARTED`. Das Flag liegt im Port, nicht im Adapter-Wissen der Anwendung.

Ebenfalls korrigiert: `_kick_off` setzte die Statuszeile erst nach dem Start des
Tasks, was die Abschlussmeldung bei synchroner Ausführung überschrieb.

Teststand: **457 bestanden, 5 übersprungen** (neu: `tests/test_env.py`,
erweiterte GUI- und Generierungstests).

### Noch offen in der GUI

Entwürfe sind nur lesbar: Bearbeiten, Freigeben und das Wiederöffnen eines
gespeicherten Projekts samt Revisionen sind die nächsten Schritte (AP 5).

## 12 Review von `5332383` und Umbau der Oberfläche

### Prompt-Konflikt (höchste Priorität)

Bestätigt, und mit einem echten Modellaufruf belegt: mit der ausgelieferten
`config.yml` ersetzte die Promptdatei die Vertragsregeln vollständig. Im
Systemprompt fehlten `schema_version`, `test_cases`, das Verbot erfundener
Werte und der Hinweis, dass Dokumentinhalt Eingabedaten sind; stattdessen
stand dort die Forderung nach dem alten Array. `gpt-oss:120b` antwortete
daraufhin mit `[]` – null Entwürfe.

`prompts.build_system_message` setzt jetzt immer auf `SYSTEM_PROMPT` auf und
hängt eine Projektdatei unter einer Überschrift an, die ihren niedrigeren Rang
benennt. Die drei mitgelieferten Dateien enthalten nur noch fachliche
Hinweise (Rolle, Abdeckungswünsche, Terminologie, Umgang mit Lücken); die
formatvorschreibenden Originale liegen mit Erklärung in `prompts/legacy/` und
gehören zu `core.generate_test_cases`, dessen Parser genau dieses Format
erwartet. Regressionstests prüfen jede ausgelieferte Promptdatei und den
tatsächlichen Pfad mit der ausgelieferten Konfiguration.

### Einstellungen und Fallzahlgrenze

Bestätigt: `config.yml` nannte `max_cases_per_unit: 5`, die GUI überschrieb es
mit `cases_per_prompt: 20`, und ein auf einen Fall begrenzter Lauf nahm drei
Fälle an. `generation_settings_from_config` ist jetzt die einzige Stelle, die
`GenerationSettings` baut; der alte Wert zählt nur noch als Rückfallwert, und
Unsinnswerte werden begrenzt. Die Obergrenze wird in `_process_unit` erzwungen:
überzählige Entwürfe werden nicht übernommen, der Lauf erhält den Befund
`C_CASE_LIMIT_ENFORCED`, und die Rohantwort behält alles.

### Modellabfrage blockiert nicht mehr

`ModelListTask` holt `/api/tags` im Hintergrund mit 3 s Verbindungs- und 5 s
Lesezeitlimit. Der Fensteraufbau greift gar nicht mehr aufs Netz zu; die
Abfrage startet erst, wenn die Ereignisschleife läuft. Während des Ladens ist
die Schaltfläche deaktiviert und beschriftet, ein Fehlschlag stellt sie wieder
her und lässt den konfigurierten Modellnamen unangetastet.

### Oberfläche nach `src/`, ohne Dekoration

Auf Wunsch des Auftraggebers liegt die Oberfläche jetzt im Paket:
`src/spec2test/ui/` mit `main_window.py`, `workers.py`, `models.py`,
`help_text.py` und `app.py`. Mitgezogen sind `config_models.py` →
`spec2test/config.py` und die Exporter → `spec2test/adapters/export/`, damit
das Paket keine Wurzelmodule mehr importiert; Testregeln erzwingen das. Im
Wurzelverzeichnis bleiben nur `gui.py` als Starter und `core.py` als
Legacy-Schicht, dazu ein `config_models.py`-Shim. Neuer Einstiegspunkt
`spec2test-gui`.

Das Fenster wurde neu geschrieben: keine Emoji-Dekoration mehr (ein Test
verbietet Symbolzeichen in `ui/`), ein gegliederter Einstellungsbereich
(Eingabe, Endpunkt, Modell, Generierung) in einem Scrollbereich, fünf
Ergebnisreiter und eine Aktionsleiste mit schlankem Fortschrittsbalken. Die
toten Legacy-Regler – Seitenblöcke, Parallelität, RAG, Cache – sind
verschwunden, weil die Pipeline sie nicht verwendet; in `config.yml` bleiben
sie für den Skriptpfad erhalten und sind dort als solche gekennzeichnet.
Der Laufbericht wird als Tabelle statt als abgeschnittener Vorformatblock
dargestellt.

### `config.yml` wird nicht mehr überschrieben

Der Auftraggeber bemängelte, dass die Datei sich durch Arbeiten in der GUI
ändert. Das war reproduzierbar – auch auf der Arbeitskopie dieses Berichts
hatte die alte Oberfläche beim Schließen Kommentare entfernt und den Modus
zurückgesetzt. Die Datei wird jetzt **nur beim Start gelesen**. Änderungen im
Fenster gelten für die Sitzung; `Datei > Write settings to config.yml`
schreibt sie auf ausdrückliche Bestätigung zurück und erhält dabei unbekannte
Abschnitte. Ein Test schließt das Fenster nach Änderungen und prüft, dass die
Datei byteweise unverändert bleibt.

Teststand: **523 bestanden, 5 übersprungen**.

## 13 Review von `bbe307c`

### Konfigurationswerte gingen zwischen Laden und Ausführen verloren

Alle vier gemeldeten Werte reproduziert: `collect_config()` baute eine frische
`AppConfig`, sodass jedes Feld ohne Bedienelement auf den Dataclass-Standard
zurückfiel – `model.url`, `cloud.max_retries`, `generation.input_char_budget`
und das Zeitbudget aus `generation.job_timeout_s`. GUI und CLI konnten damit
trotz derselben Datei verschiedene Server und Limits verwenden.

Das Fenster hält jetzt die geladene Datei als `_loaded_config` und legt nur
die Felder darüber, die es wirklich bearbeitet (`widget_overlay()`). Das
Zeitbudget wird dorthin zurückgeschrieben, wo es herkam – `generation` oder
`cloud` –, damit der Rundlauf verlustfrei ist. Vier Tests verfolgen die Werte
über Laden → Fenster → abgeleitete Worker-Einstellungen; weitere prüfen, dass
unbekannte Abschnitte überleben und bearbeitete Felder trotzdem gewinnen.

### Instabiler GUI-Test

Lokal zunächst nicht reproduzierbar, weil beim Reviewer scikit-learn
installiert ist und ein Test mehr läuft. Die Ursache war trotzdem eindeutig
und beim Nachstellen fiel ein echter Fehler auf: `apply_config()` setzte den
Modus im Kombinationsfeld, löste damit `currentTextChanged` aus und startete
**während des Fensteraufbaus** synchron eine Modellabfrage. Deren Task lief
später auf dem globalen Threadpool und meldete den Modus des alten Fensters in
den gerade laufenden Test hinein.

Drei Korrekturen:

* `apply_config()` blockiert das Signal des Kombinationsfelds; wann der
  Endpunkt kontaktiert wird, entscheidet der Aufrufer.
* Der verzögerte Abruf hängt am Fenster (`QTimer.singleShot(0, self, …)`), und
  `refresh_models()` bricht ab, wenn das Fenster geschlossen wird.
* Die Tests räumen Fenster, Timer und anstehende Ereignisse nach jedem Test
  ab.

Zwei Regressionstests stellen genau die gemeldete Kreuzkontamination nach;
ohne die Korrektur schlagen beide fehl. Mit installiertem scikit-learn, also
in der Umgebung des Reviewers, liefen danach acht vollständige Durchläufe
hintereinander grün.

### Standardmodus vereinheitlicht

Dabei fiel auf, dass ohne Konfigurationsdatei weiterhin `ollama_cloud` die
Vorgabe war, obwohl `ollama_local` als Standard dokumentiert ist. `DEFAULT_MODE`,
der Rückfall in `bootstrap` und der von `spec2test models` nutzen jetzt
einheitlich `ollama_local`; nur `spec2test run` fällt ohne Konfiguration
weiterhin bewusst auf `fake` zurück, damit der Offline-Beispiellauf ohne
Vorbereitung funktioniert.

Teststand: **532 bestanden, 4 übersprungen** (mit scikit-learn).

## 14 AP 5: Review-Ablauf und Projekte

In der vom Reviewer vorgegebenen Reihenfolge umgesetzt.

**1 Projekt speichern und wieder öffnen.** `Project` bündelt Originaldokumente,
Anforderungen, aktuelle Entwürfe, alle früheren Revisionen, die Läufe und die
Review-Historie. `ProjectStore` schreibt eine versionierte Datei
(`project_format: "1"`, Endung `*.s2t.json`) über eine eindeutige temporäre
Datei mit `fsync` und atomarem Ersetzen. Beschädigte Dateien werden mit einer
verständlichen Meldung abgelehnt, die das betroffene Feld nennt – geprüft für
ungültiges JSON, fehlende Formatmarkierung, unbekannte Version, falschen
Feldtyp und unbekannten Enum-Wert. Eine Sperrdatei markiert das Projekt als
geöffnet; ein zweiter Schreiber wird gemeldet statt still zu gewinnen.

**2 Entwürfe bearbeiten.** Jede gespeicherte Änderung erzeugt eine neue
Revision; die vorherige bleibt vollständig erhalten und bleibt abrufbar. Eine
bestehende Freigabe gilt nicht für die neue Revision. Schritte werden in einem
schlichten Textformat bearbeitet (`1. Aktion` / `   - Erwartung`), das
eigenständig getestet ist und beim Parsen neu nummeriert, damit ein Einschub
keine Handarbeit erfordert.

**3 Offene Fragen bearbeiten.** Eine Frage wird mit Antwort, Bearbeiter und
Zeitpunkt als geklärt markiert, nie gelöscht. Dabei fiel auf, dass
`may_approve` auch bereits geklärte Fragen mitzählte – behoben.

**4 Freigeben oder ablehnen.** Vor der Freigabe läuft die deterministische
Validierung erneut für genau diese Revision. Entscheidungen werden mit
Zeitpunkt, Bearbeiter, Revision und Begründung in der Historie geführt; eine
Ablehnung verlangt einen Grund.

Dabei zeigte der Abnahmetest eine Lücke: ein Befund wie
`C_NO_MEASURABLE_CRITERION` blockierte die Freigabe **dauerhaft**, weil der
Tester ihn nirgends als geklärt markieren konnte. Neu ist deshalb
`accept_finding`: eine Klärungsmeldung kann mit ausdrücklicher Begründung
angenommen werden, ein Fehler dagegen nie – gegen einen strukturell kaputten
Entwurf hilft keine Begründung. Eine Bearbeitung löscht frühere Annahmen,
damit eine neue Revision erneut beurteilt wird.

**5 Durchgängiger Abnahmetest.** generieren → speichern → schließen → öffnen →
bearbeiten → Frage klären → Befunde begründet annehmen → freigeben → erneut
öffnen. Geprüft wird dabei, dass Status, Revision, Antwort samt Bearbeiter,
frühere Revision und die vollständige Entscheidungskette den Neustart
überleben. Zwei weitere Abnahmetests decken die Quelländerung nach Freigabe
und das Speichern eines teilweise fehlgeschlagenen Laufs ab.

**Oberfläche.** Der Reiter „Test drafts" ist geteilt: oben der Baum, unten das
Review-Panel mit blockierenden Befunden, offenen Fragen und den Aktionen
Bearbeiten, Klärung anfordern, Ablehnen, Wiedereröffnen und Freigeben. Die
Freigabeschaltfläche ist nur aktiv, wenn die Validierung sie zulässt. Das
Dateimenü hat Projekt öffnen, speichern und speichern unter; beim Schließen
wird die Sperre freigegeben.

**Messgröße für den späteren RAG-Vergleich.** Auf Anregung des Reviewers führt
`Project.rework_statistics()` neben Freigaben auch Bearbeitungen je Fall,
Ablehnungen, die Revision zum Zeitpunkt der Freigabe und die Kategorien der
Beanstandungen – also Fehlerart und Nachbearbeitungsaufwand.

Teststand: **628 bestanden, 4 übersprungen**.

## 15 Neuer Anforderungsbestand

Die alten Beispieldateien `requirements/sw_req_spec_ras.{md,pdf}` wurden auf
Wunsch entfernt; im Repository liegen jetzt 22 Dokumente einer
Batteriemanagement-Spezifikation.

Dabei fiel ein Fehler auf: der Importer erfasste aus diesen 22 Dokumenten nur
**9 Anforderungen**. Die neuen Dokumente nummerieren als `BMS-REQ-AFE-0101`,
also mit einem Teilsystemsegment **nach** `REQ`, während das Standardmuster
Ziffern unmittelbar danach verlangte. Das Muster erkennt jetzt beide Formen
sowie die Kurzformen `SYS-REQ-004` und `REQ-12`; Bezeichner ohne `REQ`-Segment
wie `BMS-SRS-02-AFE-SENSING`, `CAN-FD` oder `SHUNT_RES` werden weiterhin nicht
verwechselt. Ergebnis: **138 Anforderungen, davon 132 mit externer ID**.

Ebenfalls korrigiert: eine leere Markdown-Überschrift löste die PDF-Warnung
„page None contains no extractable text … OCR" aus. Die Warnung gilt jetzt nur
für seitenbasierte Dokumente.

`config.yml` zeigt auf ein Dokument des neuen Bestands. Der Standard-Prompt ist
auf `Prompt_Advanced_Test_Architect.txt` umgestellt: die Expertenvariante ist
auf Hinterachslenkung geschrieben und würde bei Batteriethemen in die Irre
führen. Beide Dateien bleiben erhalten.

Teststand: **639 bestanden, 4 übersprungen**.

## 16 Review von `96e78c4`

### Korrektur meiner eigenen Angabe

Meine Zahl „132 auflösbare IDs" war falsch. Richtig sind **130 unterschiedliche
IDs**; zwei der 132 Vorkommen waren Querverweise, die der Importer
fälschlich als Definitionen behandelte.

### Verweise wurden als Anforderungen importiert

Reproduziert: `BMS-REQ-THM-0403` wird in Dokument 02 erwähnt und in 05
definiert, `BMS-REQ-PWR-0905` innerhalb von Dokument 10. Jeder ID-Treffer
begann eine neue Anforderung, wodurch die zitierende Anforderung mitten im
Satz endete und ein Phantom entstand.

Neu wird zwischen Definition und Verweis unterschieden: eine Definition steht
am Zeilenanfang, höchstens hinter Überschriftenzeichen, einem Listenpunkt,
Fettmarkierung oder einem Label wie `Requirement:`. Eine ID mitten im Satz ist
ein Verweis und wird in `Requirement.references` gespeichert. `reference_report()`
löst die Verweise deterministisch auf und meldet mehrfache Definitionen sowie
Verweise ohne Ziel. Findet ein Dokument nur Verweise und keine Definition,
wird das als Befund gemeldet statt still zu raten.

Ergebnis für den Bestand: **136 Anforderungen, 130 mit ID, alle eindeutig,
2 Querverweise aufgelöst, 0 unauflösbar** – und die zuvor abgeschnittene
Anforderung ist wieder vollständig.

### Doppelte Testfall-IDs bei mehreren Läufen

Reproduziert: zwei Läufe ergaben 10 Testfälle mit 5 IDs. Die Einheiten-IDs
tragen jetzt ein kurzes Laufkürzel (`TC-2ca954-U001-01`), womit auch die daraus
abgeleiteten Fragen-IDs eindeutig sind. `add_run()` lehnt einen Lauf ab, dessen
Fall-IDs bereits existieren, und übernimmt neue Quelldokumente ins Projekt.

### Freigabe trotz offener Fragen

Beide Fälle reproduziert und behoben. `approval_report()` bezieht jetzt
übergeordnete Projektfragen ein, die dieselbe Anforderung betreffen oder keiner
Anforderung zugeordnet sind. Taucht nach einer Freigabe ein neuer Blocker auf –
eine neue Frage am Fall, eine neue Projektfrage, eine zurückgenommene
Befundannahme –, wird die Freigabe sichtbar zurückgezogen und der Vorgang
protokolliert. `recheck_all()` prüft alle Freigaben erneut.

Dabei fiel auf, dass die aus der Validierung abgeleiteten Rückfragen als
Projektfragen gespeichert wurden. Mit der verschärften Prüfung hätten sie die
Freigabe dauerhaft blockiert, ohne dass man sie dort klären kann – sie sind
bereits Befunde ihres eigenen Entwurfs. Projektfragen enthalten jetzt nur noch
Rückfragen des Modells.

### Ungespeicherte Änderungen beim Schließen

Das Fenster führt einen Änderungsstatus, zeigt ihn im Titel und fragt beim
Schließen und beim Öffnen eines anderen Projekts mit Speichern / Verwerfen /
Abbrechen nach. Abbrechen hält das Fenster offen.

Teststand: **677 bestanden, 4 übersprungen**.

## 17 Aufgelöste Verweise als belegter Zusatzkontext

Umgesetzt nach den Vorgaben des Auftrags; die Regressionstests wurden wie
verlangt vor der Implementierung festgelegt.

**Auswahl.** Nur direkt zitierte Anforderungen, keine rekursive Erweiterung.
Selbstverweise und Anforderungen derselben Einheit werden übersprungen, Zyklen
enden nach einem Schritt.

**Herkunft.** Jeder Kontexteintrag trägt Requirement-ID, externe ID, Dokument,
dessen deklarierte ID und Version, Inhalts-Hash und Quellstelle. Der
Textimporter liest `**Document ID:**` und `**Version:**` aus; alle 22
Dokumente des Bestands deklarieren beides.

**Rollen.** Der Prompt trennt „REQUIREMENTS UNDER TEST" von „SUPPORTING
CONTEXT (background, not a test target)". Eine neunte Systemregel verbietet
Testfälle für Kontextanforderungen und das Übernehmen ihrer Werte, sofern die
Zielanforderung sie nicht verlangt. Promptversion `l1-test-draft-3`.

**Nachvollziehbarkeit.** Der gesendete Kontext liegt als unveränderlicher
Snapshot in `GenerationUnitResult.context` und überlebt das Speichern und
Wiederöffnen eines Projekts. `ContextItem` liegt dafür in der Domäne, damit die
Ablage sie typisiert wiederherstellen kann.

**Budget.** `context_char_budget` und `max_context_items`. Was nicht passt,
wird als `C_CONTEXT_OMITTED` gemeldet und ganz weggelassen statt gekürzt.

**Widersprüche.** Abweichende Werte erzeugen `C_CONTRADICTORY_SOURCES`; das
Modell darf keine Quelle bevorzugen.

**Validierung mit Herkunft.** Ein Wert, der nur im Zusatzkontext steht, gilt
nicht als belegt. Er wird als `C_VALUE_FROM_CONTEXT` mit der nennenden Quelle
gemeldet, blockiert weiter die Freigabe und bleibt damit Gegenstand des
Testerreviews.

### Zwei Befunde aus dem Abnahmevergleich

Der erste Vergleich ergab **mehr** unbelegte Werte mit Kontext (20 → 33). Die
Ursache war ein Messfehler auf diesem Bestand: die Dokumente schreiben Werte
als LaTeX (`$50\text{ ms} \pm 2\text{ ms}$`), und die Belegprüfung las daraus
nur einen Phantomwert. Jede korrekt zitierte Zahl galt damit als unbelegt.
`normalise_math()` übersetzt die Notation jetzt vor dem Auslesen; die
Einheitenliste wurde um °C, K, Ohm-Varianten, Ah/Wh und weitere ergänzt. Ohne
diese Korrektur wäre die Prüfung auf echten Dokumenten unbrauchbar gewesen.

Die Widerspruchsprüfung meldete **zehn Fehlalarme** aus einem einzigen
Kontextpaar, etwa „2, 50, 200 ms gegen 0,5 ms" – das sind verschiedene Größen
(Abtastperiode gegen Erkennungszeit), kein Widerspruch. Sie meldet jetzt nur
noch, wenn beide Seiten für dieselbe Einheit **genau einen** Wert nennen. Das
Zuordnen von Größen über ihren Namen braucht die Retrieval-Stufe.

### Abnahmevergleich

Zwei Anforderungen mit aufgelösten Verweisen, je zwei Läufe gegen
`gpt-oss:120b` über den lokalen Daemon:

| Kennzahl | ohne Kontext | mit Kontext |
| - | - | - |
| Entwürfe | 4,0 | 4,0 |
| offene Fragen | 26,5 | 21,5 |
| unbelegte Werte | 15,5 | 11,0 |
| Erwartung ohne Messkriterium | 10,5 | 9,5 |
| Reparaturen | 1,0 | 1,0 |

Die Richtung stimmt, **die Aussagekraft ist aber gering**: zwei Anforderungen,
zwei Läufe je Variante, und die Streuung ist groß (unbelegte Werte mit Kontext:
16 und 6). Kein Wert wurde dem Kontext zugeordnet (`C_VALUE_FROM_CONTEXT` = 0),
das Modell übernahm also keine Kontextwerte. Für eine belastbare Aussage
braucht es mehr Anforderungen und mehr Wiederholungen; beide Läufe sind samt
Kontext gespeichert.

Teststand: **710 bestanden, 4 übersprungen**.

## 9 Commits

```
1d81342  AP0: Referenzfixtures und Regressionstests für den Ausgangszustand
ad220e9  AP1/AP2: Datenverlust beheben, Domäne und Anwendung herauslösen
615c160  AP3: Ollama Cloud direkt anbinden, Tests und Konfiguration
```
