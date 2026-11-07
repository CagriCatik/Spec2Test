# MCP: lesender Zugriff auf Suche und Belegprüfung

Ein FastMCP-Server über den vorhandenen Anwendungsfunktionen. Er entscheidet
nichts Eigenes: Validierung und Freigaberegeln sind die des Produkts, dieser
Adapter formatiert nur.

## Drei Eigenschaften, auf die er festgelegt ist

**Es wird nichts geschrieben.** Kein Werkzeug öffnet ein Projekt zum
Schreiben, speichert, bearbeitet oder gibt etwas frei. Wer etwas ändern
will, benutzt die Anwendung oder die Oberfläche, wo die Review-Regeln
gelten. Ein Test vergleicht die Projektdatei byteweise vor und nach allen
vier Aufrufen.

**Kein Modellaufruf geschieht nebenbei.** Nur `review_grounds` spricht mit
einem Modell, nur wenn es aufgerufen wird, und jede Antwort nennt
`model_calls`. Ein Test stellt sicher, dass die drei anderen Werkzeuge gar
keinen Modelladapter bauen.

**Eine unvollständige Prüfung ist sichtbar.** `review_grounds` gibt je
Behauptung `clear`, `finding` oder `not_checked` zurück, dazu `complete`
und die Liste `not_checked`. Ein Modellfehler setzt alles auf
`not_checked` — das ist nie ein Bestehen.

## Installation

```
pip install -e ".[mcp]"
```

## Starten

Der Server liest aus Umgebungsvariablen oder Argumenten:

| Variable | Argument | Bedeutung |
|---|---|---|
| `SPEC2TEST_CORPUS` | `--corpus` | Verzeichnis mit Anforderungsdokumenten |
| `SPEC2TEST_PROJECT` | `--project` | eine `.s2t.json`-Projektdatei |
| `SPEC2TEST_INDEX` | `--index` | wo der Suchindex liegt (Standard `.spec2test/mcp.db`) |
| `SPEC2TEST_REVIEW_MODEL` | `--review-model` | Modell für `review_grounds` |
| `SPEC2TEST_REVIEW_MODE` | `--review-mode` | `ollama_local`, `ollama_cloud` oder `fake` |

```
spec2test-mcp --corpus requirements --project runs/TS-UDS-001.s2t.json \
              --review-model gpt-oss:120b-cloud
```

oder gleichwertig `python -m spec2test.adapters.mcp`.

Fehlt, was ein Werkzeug braucht, sagt es das mit dem Namen der Variablen,
statt mit einer leeren Antwort zu arbeiten.

### Eintrag für einen MCP-Client

```json
{
  "mcpServers": {
    "spec2test": {
      "command": "spec2test-mcp",
      "env": {
        "SPEC2TEST_CORPUS": "/pfad/zu/requirements",
        "SPEC2TEST_PROJECT": "/pfad/zu/projekt.s2t.json",
        "SPEC2TEST_REVIEW_MODE": "ollama_local",
        "SPEC2TEST_REVIEW_MODEL": "gpt-oss:120b-cloud"
      }
    }
  }
}
```

## Die vier Werkzeuge

### `search_requirements(query, limit=5, kind=None)`

Quellen zu einer Anfrage, jede mit Kennung, Dokumentversion und Fundstelle
(Datei, Abschnitt, Zeichenbereich). Offline. `kind` schränkt auf eine
Passagenart ein (`requirement`, `signal`, `interface`, `explanation`,
`reference_test`).

### `get_requirement(requirement_id)`

Eine Anforderung über ihre externe oder interne Kennung, mit allen Spans.
Eine doppelt vergebene Kennung wird **als mehrdeutig gemeldet**, nicht auf
eine der beiden aufgelöst. Offline.

### `check_grounds(case_id=None)`

Die deterministische Belegprüfung über die Entwürfe des konfigurierten
Projekts: unbekannte Quelle, Zitat, das nicht in der Quelle steht, Rechnung
die nicht aufgeht, Wert ohne jeden Beleg. Je Entwurf kommen auch
`grounding_rules` und `legacy` zurück, damit ein Aufrufer Altbestand von
neuem Entwurf unterscheiden kann. Offline.

### `review_grounds(case_id)`

Die fachliche Prüfung, ausdrücklich ausgelöst. Antwortet mit
`model_calls`, `complete`, je Behauptung einem Urteil samt `claim_id` und
Feldadresse, und `not_checked`. Gibt nichts frei und ändert nichts.

## Was er nicht kann, und warum

* **Schreiben.** Generieren, Bearbeiten, Freigeben und Speichern bleiben in
  der Anwendung. Ein Werkzeug, das über MCP freigibt, würde die
  Belegregel-Version und die dokumentierte Testerbegründung umgehen.
* **Mehrere Projekte gleichzeitig.** Ein Server, ein konfiguriertes
  Projekt und ein Quellenverzeichnis.
* **Die fachliche Abnahme.** `review_grounds` liefert die Meinung eines
  Modells, als Rückfrage gekennzeichnet. Ob ein korrekt zitierter Wert der
  richtige für diesen Test ist, entscheidet ein Tester.

## Geprüft

`tests/test_mcp_server.py` fährt die Werkzeuge mit einem **echten
MCP-Client** über das echte Protokoll, nicht gegen eine Attrappe — die
Werkzeugliste, die Schemata und das JSON an der Grenze sind die, die ein
fremder Agent sieht. Der letzte Test startet den Server zusätzlich als
**eigenen Prozess über stdio**: ohne Einstiegspunkt oder ohne gelesene
Umgebung schlägt er fehl, während die In-Process-Tests noch grün wären.

Der Prüffall mit zwei gleichen Zahlen in verschiedenen Feldern ist dabei:
zwei `claim_id`s, ein Befund und eine unbeurteilte Behauptung — die
Zuordnung hält sie auseinander, und die zweite kommt als `not_checked`
zurück statt stillschweigend mitabgedeckt zu werden.
