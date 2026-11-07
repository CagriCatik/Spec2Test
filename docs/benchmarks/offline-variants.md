# Vier Varianten: Segmentierung und Budgetvergabe getrennt

Zwei Änderungen wurden zusammen gemacht. Diese Messung trennt sie, damit
keine den Erfolg der anderen bekommt. Kein Generierungsmodell beteiligt.

| Variante | Segmentierung | Budgetvergabe |
|---|---|---|
| Baseline | alt (`fd5c16a`) | alt (`fd5c16a`) |
| nur Segmentierung | neu | alt |
| nur Budgetvergabe | alt | neu |
| Kombination | neu | neu |

Der alte Stand läuft aus einem **echten Worktree** von `fd5c16a`, nicht aus
heutigem Code mit abgeschaltetem Schalter. Der Index wird von der Revision
gebaut, der die Segmentierung gehört, und beim Auswählen nur gelesen — sonst
überschriebe die eine Segmentierung die andere. Die Kennzahlen rechnet ein
einziges Programm aus den JSON-Ausgaben, damit die Messung selbst für alle
Varianten dieselbe ist.

Reproduzieren:

```
git worktree add --detach /tmp/old fd5c16a
.venv/bin/python docs/benchmarks/offline_variants.py --old-root /tmp/old
```

Ausgabe: [offline-variants-result.txt](offline-variants-result.txt).

## Eine unvermeidbare Lücke

Die Kopfzeile, die die neue Segmentierung an eine Zeilengruppe hängt, ist
ein Feld, das der alte Auswahlcode nicht kennt. **„nur Segmentierung" zeigt
die neue Zeilengruppierung ohne ihre Spaltennamen.** Das ist kein Mangel der
Messung, sondern was passiert, wenn man nur eine der beiden Änderungen
übernimmt — und es ist die Spalte „nackte Tabellenzeilen" wert.

## Ergebnis

Kontextbudget 6000 Zeichen, gleiche Dokumente, gleiche Fragen, gleiche
Retrieval-Einstellungen.

### lexikalisch

| Variante | | tuned (n=20) | | | | holdout (n=16) | | |
|---|---|---:|---:|---:|---|---:|---:|---:|
| | | gesendet | verhungert | verbunden | | gesendet | verhungert | verbunden |
| Baseline | | **0,85** | 3 | **45 %** | | 0,69 | 4 | **52 %** |
| nur Segmentierung | | **0,85** | **0** | 41 % | | 0,69 | **0** | 46 % |
| nur Budgetvergabe | | 0,55 | 9 | 21 % | | **0,75** | 4 | 39 % |
| Kombination | | 0,50 | 10 | 22 % | | 0,69 | 5 | 34 % |

### hybrid

| Variante | | tuned | | | | holdout | | |
|---|---|---:|---:|---:|---|---:|---:|---:|
| | | gesendet | verhungert | verbunden | | gesendet | verhungert | verbunden |
| Baseline | | **0,85** | 3 | **33 %** | | **0,75** | 3 | **42 %** |
| nur Segmentierung | | 0,65 | 3 | 31 % | | **0,75** | **0** | 34 % |
| nur Budgetvergabe | | 0,50 | 10 | 17 % | | 0,62 | 6 | 22 % |
| Kombination | | 0,60 | 8 | 17 % | | 0,62 | 6 | 18 % |

### Nackte Tabellenzeilen

Gesendete Passagen, die aus Tabellenzeilen bestehen und weder Kopfzeile noch
Spaltenkopf tragen — für einen Leser und für ein Modell nicht auswertbar:

| Variante | lex/tuned | lex/holdout | hyb/tuned | hyb/holdout |
|---|---:|---:|---:|---:|
| Baseline | 0 | 3 | 1 | 4 |
| nur Segmentierung | **21** | **17** | **20** | **12** |
| nur Budgetvergabe | 0 | 3 | 1 | 3 |
| **Kombination** | **0** | **0** | **0** | **0** |

Die Segmentierung allein macht es **schlechter**: sie erzeugt kleine
Zeilengruppen, deren Spaltenköpfe der alte Auswahlcode nicht mitschickt. Nur
die Kombination erreicht null.

## Attribution: Umbau oder Reservierung?

Die neue Budgetvergabe sind zwei Dinge: erst alle Kandidaten sammeln und
gemeinsam auswählen, und davon 25 % des Budgets für Treffer der
Aufgaben-Suche zurückhalten. Mit der Reservierung auf 0 gesetzt:

| | gesendet | verhungert | verbunden |
|---|---:|---:|---:|
| tuned, Reservierung 0 % | 0,70 | 6 | 29 % |
| tuned, Reservierung 25 % | 0,50 | 10 | 22 % |
| **holdout, Reservierung 0 %** | **0,69** | **5** | **35 %** |
| **holdout, Reservierung 25 %** | **0,69** | **5** | **34 %** |

**Auf dem Holdout kostet die Reservierung nichts.** Der gemessene Einbruch
liegt vollständig auf der getunten Fragenmenge — derselben, von der die
Pipeline-Messung schon gezeigt hat, dass ich sie unabsichtlich als
lexikalisches Worst-Case-Set gebaut habe. Ihre Paraphrasen vermeiden
Bezeichner, also sind Treffer der Aufgaben-Suche dort per Konstruktion
maximal „unverbunden" — das Hilfsmaß bestraft genau dort am härtesten.

Was die Reservierung einbringt:

| | Fragen, bei denen der Aufgabentext etwas beisteuerte |
|---|---:|
| tuned, 0 % | 4 von 20 |
| tuned, 25 % | **16 von 20** |
| holdout, 0 % | 5 von 16 |
| holdout, 25 % | **12 von 16** |

## Manuelle Sichtung vollständiger Pakete

> **Meine Einschätzung (Claude), nicht das Urteil eines Testers.** Vier
> Holdout-Fragen, Baseline gegen Kombination, jedes Paket ganz gelesen.

**BMS-REQ-SC-0901** — *Was unterbricht den Strom schneller, als Software
reagieren könnte?*
Baseline: ein **162-Zeichen-Fragment der DTC-Tabelle ohne Spalten**
(unlesbar), Netztopologie (irrelevant), SC-0902 Pyro-Treiberbrücke
(**hilfreich**), Aufgabentreffer „Scope & Auxiliary HV" (unklar).
Kombination: dieselbe DTC-Tabelle als **845-Zeichen-Gruppe mit Spaltenkopf**
(**hilfreich**), Netztopologie (irrelevant), **„2. Ultra-Fast Short-Circuit
Trip Timing Sequence"** (**hilfreich**, genau die Antwort), derselbe
Aufgabentreffer.
→ **Kombination besser.** Das Hilfsmaß wertet sie schlechter, weil die
Trip-Timing-Erklärung keine gemeinsame Kennung trägt.

**BMS-REQ-NM-1901** — *Wie einigen sich die Steuergeräte aufs Schlafen?*
Baseline: Bus-Off-Recovery (teilweise), **AUTOSAR CanNm State Machine**
(**hilfreich**), NM-1906 (**hilfreich**), Scope & Network (unklar).
Kombination: Bus-Off-Recovery, Netztopologie (**irrelevant**), CanNm State
Machine (**hilfreich**), „ISO 26262 Functional Safety Goals"
(**irrelevant**).
→ **Baseline besser.** NM-1906 wurde gegen zwei allgemeine Abschnitte
getauscht. Hier stimmen Hilfsmaß und Lektüre überein.

**BMS-REQ-DSC-1504** — *Wie macht eine Rettungsmannschaft das Fahrzeug von
Hand sicher?*
Baseline: nur zwei Posten — „Scope & System Interface Topography"
(**irrelevant**, eine Dokumenteinleitung) und SYS-0050 Crash/Pyro
(**hilfreich**).
Kombination: dieselbe irrelevante Einleitung, CAN-FD-Matrix (unklar),
DTC-Tabelle (unklar), und über die Aufgaben-Suche **„2. Crash Detection,
Active Discharge & Safe…"** (**hilfreich**, die eigentliche Antwort).
→ **Kombination besser.** Die Baseline hat den richtigen Abschnitt gar nicht
gefunden.

**BMS-REQ-HVC-0305** — *Wie wird die Haltekraft eines geschlossenen Schützes
gesenkt?*
Baseline: CAN-FD-Matrix (unklar), „Scope & Low-Voltage Power Tree"
(**teilweise hilfreich**, Spulenversorgung), Aufgabentreffer „Scope &
Mathematical Overview" (**irrelevant**).
Kombination: CAN-FD-Matrix, DTC-Tabelle (unklar), „Scope & System Interface
Topography" (**irrelevant**), derselbe irrelevante Aufgabentreffer.
→ **Baseline leicht besser.** Keine der beiden findet den
Economizer/PWM-Inhalt. Hier versagt die Suche, nicht die Auswahl.

**Bilanz der Sichtung: 2 zu 2.** Die Kombination ist kein Rückschritt, wie
das Hilfsmaß nahelegt, und auch kein Fortschritt. In beiden Fällen, in denen
sie gewinnt, ist der Gewinn ein Abschnitt, den die Baseline nicht hatte; in
beiden Fällen, in denen sie verliert, hat sie eine verbundene Anforderung
gegen eine allgemeine Dokumenteinleitung getauscht.

## Bewertung an den drei Kriterien

| Kriterium | Segmentierung | Budgetvergabe |
|---|---|---|
| übergibt häufiger hilfreichen Kontext | neutral (gesendet unverändert) | **nicht erfüllt** (holdout unverändert, tuned schlechter) |
| weniger hilfreiche Treffer scheitern am Budget | **erfüllt** (verhungert 3–4 → 0) | **nicht erfüllt** (verhungert 4 → 5, 3 → 6) |
| Tabellen verständlich erhalten | **nur in Kombination erfüllt** (21 → 0 nackte Zeilen) | Voraussetzung dafür |

## Empfehlung

**Die Segmentierung behalten. Die Budgetvergabe behalten, aber nicht als
Verbesserung verbuchen — sie hat ihren Nachweis nicht erbracht.**

Im Einzelnen:

1. **Segmentierung: behalten.** Sie erfüllt zwei der drei Kriterien und ist
   die einzige Änderung mit einem sauberen Positivbefund (nackte
   Tabellenzeilen 21 → 0, verhungerte Fragen 3–4 → 0). Sie funktioniert
   aber **nur zusammen mit der neuen Auswahl**: allein ist sie schlechter
   als die Baseline, weil der alte Code den Spaltenkopf nicht mitschickt.
2. **Umbau „erst sammeln, dann auswählen": behalten.** Auf dem Holdout
   neutral (0,69 gegen 0,69), und er ist die Voraussetzung dafür, dass der
   Spaltenkopf überhaupt ankommt.
3. **Reservierung von 25 %: offene Entscheidung.** Sie tut, wofür sie
   gebaut wurde (Aufgabentext wirkt in 12 von 16 statt 5 von 16 Fragen) und
   kostet auf dem Holdout nichts. Aber sie erfüllt keines der ersten beiden
   Kriterien, und ob mehr Aufgabenkontext den Entwürfen nützt, ist genau
   die Frage, die offline nicht zu beantworten ist.

**Ich schlage vor, sie stehen zu lassen und als erstes im
Generierungsvergleich zu prüfen**, statt sie jetzt zu entfernen oder ihren
Wert anzupassen. Einen anderen Prozentwert zu wählen hieße, auf dem Holdout
zu optimieren; das unterlasse ich.

## Was diese Messung nicht sagt

* „Verbunden" bleibt ein mechanisches Hilfsmaß. Die Sichtung oben zeigt
  zweimal, dass es eine bessere Auswahl schlechter bewertet.
* **Die 66–83 % „unverbunden" aus der Pipeline-Messung sind kein belegter
  Rauschanteil.** Von den vier gelesenen Paketen enthielt jedes mindestens
  eine hilfreiche unverbundene Passage.
* Nichts darüber, ob gesendeter Kontext auch benutzt wird. Das bleibt die
  Frage aus [retrieval-arms.md](retrieval-arms.md).
* Vier gelesene Pakete sind eine Stichprobe, keine Abnahme.
