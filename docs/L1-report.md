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
pytest -q  →  408 bestanden, 5 übersprungen
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
3. **GUI bleibt auf dem Legacy-Pfad.** Quellenansicht, Prüfbefunde, offene
   Fragen und Revisionen sind AP 5. Die GUI zeigt den Laufstatus, nutzt aber
   weiter das flache Format ohne Anforderungsbezug; `application/legacy.py` ist
   der dafür vorgesehene, als veraltet markierte Pfad.
4. **Modellliste in der GUI blockiert weiterhin kurz den Qt-Thread** (3 s
   Zeitlimit) – wie im Ausgangsstand. Die nichtblockierende Variante ist AP 5.
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
9. **Der Umgebungs-Zufall** `pandas 3.1.1` / `PyMuPDF 2.1` wurde für die Tests
   benutzt; `requirements.txt` nennt weiter die festgeschriebenen Versionen des
   Ausgangsstands. Eine Abgleichprüfung der Pins steht aus.

## 9 Commits

```
1d81342  AP0: Referenzfixtures und Regressionstests für den Ausgangszustand
ad220e9  AP1/AP2: Datenverlust beheben, Domäne und Anwendung herauslösen
615c160  AP3: Ollama Cloud direkt anbinden, Tests und Konfiguration
```
