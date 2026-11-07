# Funktionstest der Belegkette, zehn Modellaufrufe

Kein Benchmark. Die Frage ist, ob die Teile an echter Modellausgabe
zusammenspielen: liefert das Modell Belege, beurteilt die deterministische
Prüfung sie richtig, und erkennt ein echter Agent einen fachlichen
Fehlgriff, den die Prüfung nicht sehen kann.

Rohdaten: [function-test/](function-test/). Skript:
`docs/benchmarks/function_test_grounds.py`.

## Aufrufbilanz: 10 von 10, und drei davon habe ich verschwendet

| Was | Aufrufe |
|---|---:|
| SC-0901, erster Versuch — **Abbruch durch einen Fehler in meinem Skript** | 2 |
| Drei synthetische Fälle durch den echten Agenten | 3 |
| SC-0901, zweiter Versuch — **Abbruch durch einen zweiten Skriptfehler** | 2 |
| SC-0901, dritter Versuch — verworfen, Grund protokolliert | 2 |
| SC-0901, vierter Versuch ohne Reparatur — Rohantwort gesichert | 1 |
| **Summe** | **10** |

**Vier der zehn Aufrufe gingen an zwei Fehler in meinem Messskript**
(`attempt.raw` statt `raw_response`, und eine Zusammenfassung, die auf eine
leere Fallliste zugriff). Dadurch blieben für THM-0404 und BAL-0503 keine
Aufrufe übrig: **die geforderten drei Entwürfe sind nicht entstanden, nur
einer.** Das Skript hat jetzt einen `--fake`-Probelauf, der genau das
verhindert; ich habe ihn erst nach dem Schaden eingebaut.

Keine automatische Wiederholung über die eine Reparatur hinaus, die die
Pipeline ohnehin erlaubt. An Retrieval-Gewichtung und Prompt wurde während
der Prüfung nichts geändert — der Prompt stand auf `l1-test-draft-7`; die
Korrektur auf `-8` kam erst danach und ohne weiteren Aufruf.

## 1. Zuordnung und Vollständigkeit (vorab, ohne Modellaufruf)

**Gleicher Wert reicht nicht als Zuordnung.** `Ground.target` nennt jetzt
das Feld (`expectations[0]`, `measurements[1]`). Ein Test stellt den Fall
nach, den du genannt hast:

```
Schritt: "the heater switches on at 5.0 V"
         "the heater switches off at 5.0 V"
Beleg:   claim 5.0 V, target expectations[0]
Befund:  C_CLAIM_WITHOUT_GROUND an expectations[1]
```

Ohne `target` und bei mehrfach vorkommendem Wert: `C_GROUND_AMBIGUOUS_TARGET`
statt stiller Zuordnung. Ein eindeutiger Wert braucht weiterhin kein Ziel.

**`fits` ist nicht „nicht geprüft".** Je gesendeter Behauptung gibt es
jetzt einen Eintrag mit `clear` | `finding` | `not_checked`. Eine gültige
Antwort, die eine Behauptung auslässt, ergibt `not_checked` plus
`C_GROUND_REVIEW_NOT_CHECKED`. Modellfehler und unlesbare Antworten setzen
**alle** Behauptungen auf `not_checked`; `review.complete` ist nur wahr,
wenn jede gesendete Behauptung beurteilt zurückkam. Im Probelauf gegen die
Attrappe, die das Review-Format gar nicht spricht, kommt korrekt
`0 of 6 claim(s) judged, 6 not checked` heraus — keine stille Bestätigung.

## 2–3. Die drei synthetischen Fälle durch den echten Agenten

Alle drei passieren die deterministische Prüfung ohne Beanstandung: die
Quelle existiert, das Zitat steht wirklich da.

| Fall | deterministisch | Agent | Begründung des Agenten |
|---|---|---|---|
| `12 V` für eine Zellspannung | keine Beanstandung | **finding** `mismatched_quantity` (high) | „refers to a 12 V auxiliary supply powering the monitoring board, not the cell voltage" |
| `4.20 V` beim Fahren | keine Beanstandung | **finding** `mismatched_condition` (high) | „limits cell voltage to 4.20 V only while the pack is charging, whereas the test step concerns driving" |
| `4.15 V` beim Fahren | keine Beanstandung | **clear** `fits` (high) | „states the cell voltage shall not exceed 4.15 V during driving" |

> **Entwicklerprüfung (Claude, keine Testerabnahme).** Alle drei Urteile
> sind sachlich richtig, und die Begründungen benennen genau das
> entscheidende Wort im Zitat. Kein Fehlalarm beim korrekten Fall. Damit ist
> dein Einwand beantwortet: ein echtes Modell erkennt diese fachlichen
> Fehlgriffe, nicht nur die Verarbeitung funktioniert.
>
> Eine Unschärfe: den Zustandsfall hat der Agent `mismatched_condition`
> genannt, meine Vorlage erwartete `mismatched_state`. Beides ist
> vertretbar — Laden gegen Fahren ist Zustand *und* Bedingung. Die beiden
> Verdikte überlappen; das ist ein Entwurfsmangel meiner Kategorien, kein
> Fehler des Agenten. Für einen Reviewer macht es keinen Unterschied.

## 2–3. Der echte Entwurf: SC-0901

Der einzige, für den Aufrufe reichten. Gefordert war der Fall „Grenzwert
direkt belegt"; er hat zusätzlich eine Rechnung geliefert.

**Was das Modell schrieb** (gekürzt):

```
Schritt 2  Inject an analog voltage pulse of amplitude corresponding to
           2550 A (≈255 mV) across the shunt
  erwartet The injected pulse amplitude equals the calculated voltage for 2550 A.
  Beleg    kind=calculation  target=measurements[0]
           formula "V = I * R = 2550 A * 100 µΩ = 255 mV"
Schritt 3  Measure the time between the leading edge and the rising edge
  erwartet HW_Trip_Latch_Output transitions to HIGH within ≤2.0 µs.
  Beleg    kind=target_requirement  target=expectations[0]
           quote "When an analog pulse simulating 2550 A current step is
                  applied to the shunt terminals ..."
offene Fragen
  "What waveform (rise time, fall time, duration) is required for the pulse"
  "What measurement tolerance is acceptable for the ≤2.0 µs response"
  "How should the SR latch be reset after a trip"
```

**Und dann wurde der ganze Entwurf verworfen:**

```
$.test_cases.0.steps.0.grounds.0.inputs.0.value   Field required
$.test_cases.0.steps.0.grounds.0.inputs.1.value   Field required
```

Das Modell hatte `inputs` ohne den Schlüssel `value` geschrieben und die
Zahlen in die Zitate gepackt:

```json
"inputs": [{"source": "BMS-REQ-SC-0901", "quote": "primary 100 µΩ current shunt"},
           {"source": "BMS-REQ-SC-0901", "quote": "...simulating 2550 A current step"}]
```

> **Entwicklerprüfung.** Das ist **mein Fehler, nicht der des Modells.**
> Die Rechnung ist vollständig und richtig — `2550 A × 100 µΩ = 255 mV`,
> genau die Arithmetik, die ich im vorigen Bericht selbst falsch
> hingeschrieben hatte. Das Modell hat die Information geliefert, nur im
> falschen Feld. Mein Vertrag hat daraus einen Schemafehler gemacht, der
> den **gesamten Entwurf** verwirft und eine Reparatur verbrennt — genau
> das Verhalten, das wir für *falsche* Belege schon abgestellt hatten, aber
> für *unvollständig geschriebene* nicht.

**Behoben:**

* `GroundInput.value` und `.source` sind nicht mehr Pflicht im Vertrag und
  im Domänenobjekt. Eine unvollständige Eingabe ist darstellbar.
* `check_calculation` meldet sie: *„input 1 has no value of its own …
  put it in `value` so the arithmetic can be recomputed"* —
  `C_GROUND_CALCULATION_NOT_REPRODUCIBLE`, eine Rückfrage. Der Entwurf
  bleibt speicherbar, die Freigabe ist gesperrt.
* Prompt-Regel 11 zeigt jetzt ein vollständiges `inputs`-Beispiel
  (`l1-test-draft-8`).
* Ein Regressionstest fährt **genau diese Antwort** durch den Vertrag und
  verlangt `blocks_draft is False` und `blocks_approval is True`.

**Nicht gemessen:** ob das Modell mit `l1-test-draft-8` das Feld nun füllt.
Dafür wäre ein elfter Aufruf nötig gewesen.

## 4–5. Übersicht

| Fall | Behauptung | Beleg | deterministisch | Agent |
|---|---|---|---|---|
| SC-0901 real | `255 mV` | Rechnung `2550 A × 100 µΩ`, `value` fehlt | **vorher:** Entwurf verworfen · **nachher:** `C_GROUND_CALCULATION_NOT_REPRODUCIBLE` | nicht erreicht (Budget) |
| SC-0901 real | `≤2.0 µs` | Zitat der Anforderung, `target=expectations[0]` | keine Beanstandung | nicht erreicht (Budget) |
| synthetisch 1 | `12 V` | Zitat Bordnetzversorgung | keine Beanstandung | **finding** quantity |
| synthetisch 2 | `4.20 V` | Zitat Ladegrenze | keine Beanstandung | **finding** condition |
| synthetisch 3 | `4.15 V` | Zitat Fahrgrenze | keine Beanstandung | **clear** |

## 6. Der vollständige Ablauf in der Oberfläche

Offline mit der Attrappe, als Test festgehalten
(`tests/test_gui_grounds_end_to_end.py`):

Generieren → der Entwurf trägt `grounding_rules = 1` und Belege mit
Feldadresse → ein Tester ergänzt beim Bearbeiten eine Erwartung mit
`300 ms` → das ist eine fachliche Revision, also blockiert
`C_CLAIM_WITHOUT_GROUND` die Freigabe, **ohne** das Speichern zu verhindern
→ der Befund steht im Review-Panel, mit dem Wert → Rückfragen beantwortet,
Befund mit dokumentierter Begründung angenommen → Freigabe → Speichern →
Wiederöffnen: Status, Regelversion, Belege **und** deren Feldadressen
überstehen die Runde → Export nach Markdown (mit „Where the values come
from"), JSON und Excel.

## 7. Fehlende Belege, übersehene Fehler, Fehlalarme

**Fehlalarme:** keine. Der Agent hat den korrekten Fall als `fits`
beurteilt, und die deterministische Prüfung hat an den beiden
Zitat-Belegen des echten Entwurfs nichts zu beanstanden gehabt.

**Übersehen:** *Korrektur vom 2026-11-07 — „übersehen: nichts" war zu
stark.* Belastbar ist nur: **die zwei eingebauten Fehler wurden erkannt,
der korrekte synthetische Fall wurde akzeptiert.** Über echte Entwürfe
lässt sich nichts sagen — es gibt keine vollständige fachliche
Referenzbewertung, gegen die man „übersehen" überhaupt messen könnte. Der
Agent lief über keinen einzigen echten Entwurf.

**Fehlende Belege:** der echte Entwurf hat jede seiner konkreten Angaben
belegt und für das, was die Anforderung nicht hergibt (Pulsform,
Messtoleranz, Latch-Reset), offene Fragen gestellt statt Werte zu erfinden.
Das ist das gewünschte Verhalten.

**Behobene Implementierungsfehler, alle mit Test:**

1. Ein unvollständig geschriebener Beleg verwarf den ganzen Entwurf
   (oben beschrieben).
2. `GroundInput` verweigerte im Konstruktor, was ein Modell real liefert.
3. Mein Messskript las `attempt.raw` statt `raw_response` und griff auf
   eine leere Fallliste zu — vier Modellaufrufe verloren. Jetzt mit
   `--fake`-Probelauf und phasenweise aufrufbar.

963 Tests, 99 Architekturtests.

## Weiterhin offen

**Zwei der drei echten Generierungsfälle sind nicht entstanden** und bleiben
offen, bis sie nachgeholt werden:

| Anforderung | Situation | Stand |
|---|---|---|
| BMS-REQ-THM-0404 | braucht einen Zusatzkontext | **offen** |
| BMS-REQ-BAL-0503 | eine benötigte Angabe fehlt tatsächlich | **offen** |

Dazu: der Prüfagent lief über keinen echten Entwurf, und ob das Modell mit
`l1-test-draft-8` das Feld `value` nun füllt, ist ungemessen. Alle drei
brauchen Modellaufrufe.

## Grenzen

* **Ein** echter Entwurf statt der geforderten drei, weil ich vier Aufrufe
  verschwendet habe. THM-0404 (Zusatzkontext nötig) und BAL-0503 (Angabe
  fehlt tatsächlich) sind ungeprüft.
* Der Agent lief nicht über den echten Entwurf — das Budget war aufgebraucht.
* Drei synthetische Fälle, ein Modell, ein Korpus.
* Alle fachlichen Urteile oben sind meine, nicht die eines Testers.
