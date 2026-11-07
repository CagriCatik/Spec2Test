# Der recherchierende Prüfer

Ein smolagents-Agent, der fehlende Bedingungen selbst nachschlägt, statt
nur zu beurteilen, was ihm vorgelegt wird. Er benutzt dafür den MCP-Server
dieses Projekts ([mcp.md](mcp.md)).

## Die Trennung, um die es geht

**Eine Quelle, die der Prüfer findet, war kein Kontext des
Generierungslaufs.** Der Entwurf ist ohne sie entstanden. Sie in den
Laufkontext zu falten würde die Geschichte umschreiben und den Lauf besser
informiert aussehen lassen, als er war.

Alles, was der Prüfer liest, landet deshalb in einem **eigenen
Prüf-Snapshot** neben dem Projekt — nie darin. Jeder gespeicherte Bericht
trägt den Satz:

> The sources under 'consulted' were read during this review. They were NOT
> part of the context the draft was generated with and must not be merged
> into the run's context.

## Was der Prüfer darf und nicht darf

| | |
|---|---|
| Werkzeuge | nur `search_requirements` und `get_requirement` |
| **nicht** angeboten | `review_grounds` und `check_grounds` |
| schreibt | nichts am Entwurf, nichts an der Freigabe |
| Budget | Modellaufrufe, Werkzeugaufrufe und Laufzeit, jeweils hart |

`review_grounds` wird bewusst zurückgehalten: ein Prüfer, der einen Prüfer
aufrufen kann, erzeugt rekursive Modellprüfungen und eine Rechnung, die
niemand genehmigt hat. Der Bericht nennt, was zurückgehalten wurde.

Werkzeuge außerhalb der erlaubten Liste werden **gar nicht erst gebaut**:
wächst dem Server später ein schreibendes Werkzeug, kann es nicht
versehentlich durchgereicht werden.

## Drei Regeln, die durchgesetzt und nicht erhofft werden

1. **Jedes Urteil adressiert eine `claim_id`.** Eine Behauptung, die der
   Prüfer nicht beantwortet, bleibt `not_checked`.
2. **Eine nicht gefundene oder mehrdeutige Quelle ergibt kein `clear`.**
   Antwortet der Prüfer `fits` und beruft sich dabei auf eine Quelle, die
   er nachweislich nie gelesen hat, wird das Urteil auf `not_checked`
   **herabgestuft**, mit `downgraded_from: "fits"` im Bericht. Nichts
   gefunden zu haben ist nicht dasselbe wie nichts gefunden zu haben, was
   dagegen spricht.
3. **Das Budget stoppt den Lauf.** Modell und Werkzeuge sind in Zähler
   gewickelt, die Uhr wird vor jedem Aufruf geprüft. Der Grund überlebt:
   smolagents verwandelt einen Werkzeugfehler in eine Beobachtung und läuft
   weiter, deshalb gewinnt die Budgetmeldung über das, was danach
   herauskommt.

## Installation

```
pip install -e ".[mcp,agents]"
```

## Aufruf

```
spec2test research-review runs/TS-UDS-001.s2t.json TC-ac9093-U001-01 \
  --corpus requirements \
  --model gpt-oss:120b-cloud \
  --api-base http://localhost:11434/v1 \
  --max-model-calls 6 --max-tool-calls 8 --max-seconds 120
```

Der Befehl startet den MCP-Server als Kindprozess, nennt die angebotenen
und die zurückgehaltenen Werkzeuge, gibt je Behauptung `checked, no
finding` / `FINDING` / `NOT CHECKED` aus, listet die gelesenen Quellen mit
Datei, Version und Zeichenbereich und schreibt den Snapshot neben das
Projekt.

## Der Snapshot

```json
{
  "case_id": "TC-R-1", "case_revision": 1,
  "model": "gpt-oss:120b-cloud",
  "prompt_version": "research-review-1",
  "framework": "smolagents ToolCallingAgent",
  "budget": {"max_model_calls": 6, "max_tool_calls": 8, "max_seconds": 120.0},
  "model_calls": 3,
  "tool_calls": [{"tool": "get_requirement",
                  "arguments": {"requirement_id": "SYS-REQ-8002"},
                  "result_count": 1}],
  "consulted": [{"identifier": "SYS-REQ-8002",
                 "document_filename": "b_states.md",
                 "document_version": null,
                 "char_start": 31, "char_end": 190,
                 "quote": "While the pack is charging ..."}],
  "verdicts": [{"claim_id": "step0-g0", "outcome": "finding",
                "verdict": "missing_condition", "confidence": "high",
                "consulted": ["SYS-REQ-8002"]}],
  "budget_exhausted": false,
  "provenance_note": "... NOT part of the context ..."
}
```

## Eine Abweichung vom Auftrag, und warum

Der Auftrag nannte die Anbindung über smolagents. **smolagents' eigener
`MCPClient` wird nicht benutzt.** Er erreicht MCP über `mcpadapt`, das ein
älteres `mcp`-Paket braucht, als FastMCP 4 installiert; beide Bedingungen
sind nicht gleichzeitig erfüllbar:

```
mcpadapt 0.1.20 -> from mcp.client.streamable_http import streamablehttp_client
ImportError: cannot import name 'streamablehttp_client' ...   (mcp 2.3.0)
```

Eine Bibliothek herunterzupinnen, damit der Adapter der anderen zufrieden
ist, bricht beim nächsten Release wieder — ausprobiert: `fastmcp<3` zieht
`mcp 1.30`, womit dann fastmcp selbst nicht mehr lädt. Stattdessen spricht
`adapters/agents/mcp_bridge.py` das Protokoll direkt (über FastMCPs Client,
rund 150 Zeilen) und reicht smolagents gewöhnliche `Tool`-Objekte. Der
Server bleibt derselbe, als Kindprozess gestartet; nur die Adapterschicht
zwischen den beiden Bibliotheken ist unsere.

## Geprüft

`tests/test_research_reviewer.py`, 17 Tests, deterministische
Modellantworten und ein **echter MCP-Serverprozess**:

| Situation | geprüft |
|---|---|
| Recherche findet die fehlende Bedingung | Befund `missing_condition`, Quelle mit Datei und Zeichenbereich im Snapshot |
| Recherche findet eine fachlich unpassende Quelle | Befund `mismatched_quantity` |
| keine passende Quelle | `fits` auf eine ungelesene Quelle wird herabgestuft; eine unbekannte Kennung wird als `found: false` festgehalten |
| Budgetende | Modellaufrufe, Werkzeugaufrufe und Uhr, jeweils mit erhaltenem Grund |
| MCP-Ausfall | toter Server beim Verbinden; Werkzeug, das mitten im Lauf ausfällt — nichts kommt als Bestehen zurück |

Dazu: dass `review_grounds` nicht angeboten wird, dass der Entwurf
unverändert bleibt (Vergleich vor und nach dem Lauf) und dass die CLI die
Projektdatei byteweise nicht anfasst.

Zwei Fehler hat der Testaufbau gefunden, die ohne echten Prozess
unsichtbar geblieben wären: `server_parameters` gab den Paketpfad nicht an
den Kindprozess weiter (er starb mit ModuleNotFoundError, was oben nur als
„connection closed" ankam), und smolagents prüft die Werkzeugsignatur gegen
das deklarierte Schema, was ein generisches `**kwargs` ausschließt.

## Weiterhin offen

Es ist **kein echter Modelllauf** gemacht worden: alle Antworten in den
Tests sind geskriptet. Ob ein echtes Modell die Werkzeuge sinnvoll
einsetzt — also wirklich nachschlägt, statt zu raten — ist ungemessen und
gehört in den nächsten Funktionstest, zusammen mit den beiden offenen
Generierungsfällen aus
[benchmarks/function-test.md](benchmarks/function-test.md).
