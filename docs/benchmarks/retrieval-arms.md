# Drei Varianten, eine Grundlinie: ohne Retrieval, lexikalisch, hybrid

**Befund in einem Satz: bessere Quellenfindung erreicht die Entwürfe nicht.
Hybrid holt nachweislich andere Passagen als BM25, und ein
Generierungsnutzen ist damit nicht gezeigt — weder in die eine noch
überzeugend in die andere Richtung.**

> **Korrekturen vom 2026-11-07.** Keine Zahl wurde geändert; drei
> Interpretationen waren zu stark. Sie stehen unten jeweils an Ort und
> Stelle als „Korrektur". Kurz: hybrid ist *nicht* in jeder Kennzahl
> schlechter als lexikalisch; die 32/42 % sind Anteile der **Posten**, nach
> Zeichen sind es 24/37 %; und die zusätzlichen Konfliktmeldungen belegen
> **keine** zusätzlichen sachlichen Widersprüche. Die Empfehlung in
> Abschnitt 4 bleibt, ihre Begründung ist schwächer geworden.

Rohdaten: [retrieval-arms-rows.json](retrieval-arms-rows.json),
Auswertung: [retrieval-arms-result.txt](retrieval-arms-result.txt).
Reproduzieren: `compare_retrieval_arms.py`, auswerten: `analyse_arms.py`.

## 1. Vollständigkeit

| | geplant | abgeschlossen | fehlgeschlagen | degradiert |
|---|---:|---:|---:|---:|
| ohne Retrieval | 40 | 40 | 0 | – |
| lexikalisch | 40 | 40 | 0 | 0 |
| hybrid | 40 | 40 | 0 | 0 |
| **gesamt** | **120** | **120** | **0** | **0** |

Keine nicht gestartete Einheit, kein Abbruch. **Kein Hybrid-Lauf ist auf
lexikalisch zurückgefallen**, die beiden Sichten unten ruhen deshalb auf
denselben Läufen; die Unterscheidung bleibt trotzdem im Werkzeug, weil sie
sich bei einem nicht erreichbaren Modell sofort auftut.

Modellaufrufe: **127 bei 120 Läufen**, davon **7 Reparaturen**
(ohne Retrieval 1, lexikalisch 5, hybrid 1). 16,3 Minuten Modellzeit.
Gleicher Code-Stand, Prompt `l1-test-draft-5`, Modell `gpt-oss:120b-cloud`,
identische Grenzen und Kontextbudgets in allen Varianten.

## 2. Vergleich

Nenner: 40 Läufe je Variante in beiden Sichten, 20 von 20 Anforderungen mit
Gegenstück in jeder Variante — der gepaarte Vergleich lässt nichts aus.

| Mittel je Lauf | ohne | lexikalisch | hybrid | besser ist |
|---|---:|---:|---:|---|
| Testentwürfe | 1,93 | 2,00 | 2,00 | – |
| offene Rückfragen | 9,65 | 8,80 | 9,80 | – |
| **unbelegte Werte** | **1,07** | 1,60 | 1,32 | weniger |
| Erwartungen ohne messbares Kriterium | 6,20 | **5,35** | 5,92 | weniger |
| **mögliche Wertkonflikte** | **0,00** | 0,75 | 1,05 | weniger |
| **Befunde für den Reviewer** | **7,47** | 7,83 | 8,80 | weniger |
| Kontextposten gesendet | 0,00 | 3,00 | 3,20 | – |
| davon durch die Aufgabenbeschreibung | 0,00 | 0,50 | 0,50 | – |
| **Kontextwerte tatsächlich übernommen** | 0,00 | **0,07** | 0,05 | mehr |
| Modellaufrufe | 1,02 | 1,12 | 1,02 | weniger |
| Reparaturen | 0,03 | 0,12 | 0,03 | weniger |
| Sekunden | 7,26 | 8,80 | 8,38 | weniger |

Drei Dinge daran sind belastbar genug, um etwas zu entscheiden:

1. **Retrieval verschlechtert die Belegtreue.** Unbelegte Werte steigen von
   1,07 auf 1,60 (lexikalisch) bzw. 1,32 (hybrid). Das ist derselbe Effekt
   wie in der 80-Lauf-Messung und hat den Prompt-Fix `l1-test-draft-4/5`
   überlebt. Die Konfliktmeldungen (0,00 → 0,75 → 1,05) gehören **nicht**
   hierher — siehe die Korrektur weiter unten.
2. **Für hybrid ist kein Generierungsnutzen gezeigt.**
   *Korrektur: die frühere Fassung schrieb „in jeder Kennzahl gleichauf
   oder schlechter". Das ist falsch.* Bei **unbelegten Werten liegt hybrid
   mit 1,32 unter lexikalisch mit 1,60** — aber über den 1,07 ohne
   Retrieval. Schlechter ist hybrid bei den Befunden für den Reviewer
   (8,80 gegen 7,83) und bei den Konfliktmeldungen (1,05 gegen 0,75, zu
   deren Aussagekraft siehe unten). Richtig ist deshalb nur: **ein
   belastbarer Qualitätsvorteil von hybrid ist nicht nachgewiesen**, kein
   durchgängiger Nachteil.
3. **Der Kontext wird fast nicht benutzt.** 3 übernommene Werte aus 120
   gesendeten Posten (lexikalisch), 2 aus 128 (hybrid). Das ist für sich
   genommen kein Fehler (siehe Abschnitt 3), aber es begrenzt, was
   Rangfolge überhaupt bewirken kann.

Die Rückfragen schwanken stark und über die Varianten hinweg uneinheitlich
(je Anforderung in [retrieval-arms-result.txt](retrieval-arms-result.txt),
Standardabweichung 4,3–6,3 bei Mittelwerten um 9). Aus dieser Kennzahl
lässt sich bei 40 Läufen je Variante nichts ableiten, in keine Richtung.

### Hat hybrid überhaupt etwas anderes gefunden?

Ja — sonst wäre der Vergleich wertlos. Über die 40 gepaarten Läufe hinweg
war die Kontextmenge **in 38 von 40 Fällen verschieden**; identisch nur
zweimal. Die Suche unterschied sich also wirklich, das Ergebnis nicht.

### Die Aufgabenbeschreibung verhungert am Budget

Genau der Mechanismus, für den der semantische Vorteil nachgewiesen ist,
kommt kaum zum Zug:

* In **22 von 40 Läufen** steuerte die Aufgaben-Suche **nichts** bei —
  in beiden Varianten gleich oft.
* Das Zeichenbudget war da bereits aufgebraucht: im Mittel **5569 von 6000
  Zeichen**, Maximum 5998. In allen 22 Fällen waren über 4000 Zeichen
  vergeben, bevor die Aufgaben-Suche an die Reihe kam.
* 36 bzw. 38 Treffer passten nicht mehr ins Budget (`C_CONTEXT_OMITTED`).

Die gemessene Stärke semantischer Suche bei Prosafragen konnte sich also nur
in 18 von 40 Läufen überhaupt auswirken. Das schwächt die Aussage „hybrid
hilft nicht" — es sagt genauer: **hybrid hilft nicht unter einem Budget, das
die Anforderungs-Suche vorher leerräumt.**

## 3. Paarprüfung

Sechs vollständige Paare, Stichprobe mit festem seed, gelesen statt gezählt.
Vollständiges Blatt: `pair_review.py`.

> **Die folgenden Antworten sind meine Lesart (Claude), nicht das Urteil
> eines Testers.** Sie sind als Diagnose brauchbar und ersetzen keine
> fachliche Abnahme. Wo ich fachlich nicht sicher entscheiden kann, steht
> das da.

**Relevante Quellen — es gibt sie, und nicht wenige:**

| Anforderung | herangezogen | mein Urteil |
|---|---|---|
| BMS-REQ-SEC-1004 (Brute-Force-Sperre) | BMS-REQ-COM-0804 (Seed-and-Key), beide Varianten | **relevant.** Das Zugriffsverfahren, gegen das die Sperre wirkt. |
| BMS-REQ-SEC-1004 | „NRC Decision Pipeline" (nur hybrid) | **relevant**, und ein guter Fang: die Ablehnungscodes sind genau der beobachtbare Ausgang einer Sperre. |
| BMS-REQ-BAL-0504 (Schalterdiagnose) | BMS-REQ-BAL-0502 (Zielspannung, Shunt-Planung), beide | **relevant.** Schwester-Anforderung desselben Mechanismus. |
| BMS-REQ-EST-0205 (Leistungsgrenzen) | BMS-REQ-CHG-0606 (V2L-Entladehüllkurve), beide | **relevant.** „Wie viel darf entnommen oder zurückgespeist werden" ist V2L-Gebiet. |
| BMS-REQ-HVC-0301 (Vorladereihenfolge) | BMS-REQ-SYS-0020 (Zustandsautomat), nur lexikalisch | **relevant, und passender als die Hybrid-Wahl** BMS-REQ-HVC-0302 (Widerstandserwärmung): gefragt war die Reihenfolge. |
| BMS-REQ-THM-0402 (zu kalt zum Laden) | „Thermal Operating Boundaries", nur hybrid | **relevant**, klar besser als die lexikalische Wahl BMS-REQ-BAL-0501. |

**Irrelevante Quellen — ebenfalls reichlich:**

| Anforderung | herangezogen | mein Urteil |
|---|---|---|
| BMS-REQ-AFE-0102 (Sensorleitungsbruch) | BMS-REQ-VNV-1201 (Verifikation & Validierung), lexikalisch | **irrelevant.** Eine Prozessanforderung. |
| BMS-REQ-AFE-0102 | BMS-REQ-BAL-0504 (Balancing-Schalter), hybrid | **irrelevant.** Falsches Teilsystem; die Ähnlichkeit ist „Diagnose eines Schalters". |
| BMS-REQ-AFE-0102 | „Bootloader Flash Reprogramming Sequence", über die Aufgaben-Suche | **irrelevant.** |
| BMS-REQ-BAL-0504 | „Network Topography & Communication Stack", lexikalisch | **irrelevant** für eine Schalterdiagnose. |
| BMS-REQ-THM-0402 | „Scope & System Interface Topography", hybrid | **irrelevant.** Eine Dokumenteinleitung. |
| mehrere | „Comprehensive DTC Master Table" (20×) | **uneinheitlich.** Für AFE-0102 passend (Fehlercode des Leitungsbruchs), für THM-0402 nicht. Ich kann nicht pauschal urteilen. |

**Q1 war die zusätzliche Quelle relevant?** Gemischt, etwa zur Hälfte. Beide
Varianten holen echte Schwester-Anforderungen, beide holen auch Fehlgriffe.

**Q2 hätte sie den Entwurf verbessern können?** In den sechs Paaren: ja, in
mindestens vier Fällen — COM-0804 für die Sperre, BAL-0502 für die
Schalterdiagnose, SYS-0020 für die Vorladereihenfolge, die thermischen
Grenzen für das Kaltladen. Die Entwürfe greifen sie nicht auf. Das ist der
eigentliche Befund der Paarprüfung: **die Quellen waren oft gut genug, der
Entwurf nutzt sie nicht.**

**Q3 wurde sie, wo benutzt, korrekt benutzt?** In fünf von sechs Paaren
wurde sie nachweislich gar nicht für einen Wert benutzt (`sup=0`). Für
BMS-REQ-EST-0205 produzierte hybrid **6 unbelegte Werte gegen 2 bei
lexikalisch** und 22 statt 12 Rückfragen — hier hat der Kontext nicht
geholfen, sondern gestreut. Eine korrekte Nutzung konnte ich in dieser
Stichprobe nirgends belegen.

### Korrektur: die Konfliktmeldungen beweisen keine Widersprüche

Der Bericht hat 0,00 → 0,75 → 1,05 als Schaden durch Retrieval gelesen. Das
ist nicht haltbar. Ohne Kontext gibt es nichts zu vergleichen, also kann die
Zahl gar nicht anders als bei null liegen. Entscheidend ist, wie viele
Meldungen fachlich zutreffen — also habe ich sie gelesen.

72 Meldungen, **28 verschiedene Behauptungen**. Meine Lesart (nicht die
eines Testers):

| | Anzahl | Beispiele |
|---|---:|---|
| vergleicht erkennbar verschiedene Größen mit gleicher Einheit | **~22** | `3,65 V` (Zellspannung) gegen `12 V` (Bordnetz); `800 V` (HV-Bus) gegen `12 V`; `20 A` (Balancing) gegen `2,2 A` (Versorgung); `500 ms` gegen `5 ms` aus einem anderen Dokument |
| offensichtlicher Parse-Artefakt | 1 | `8508 A` — als Strom sinnlos |
| unklar, einen Blick wert | ~4 | `30 A` gegen `25 A` (beide Schützströme, dasselbe Dokument); `20 A` gegen `5 A`; `20 V` gegen `10 V`; `12 V` gegen `10 V` |
| von mir bestätigter Widerspruch | **0** | – |

Die Meldung selbst sagt bereits „This is a suspicion, not a confirmed
conflict" — insofern lügt sie nicht. Aber als Beleg für Schaden durch
Retrieval taugt sie nicht. Sie ist stattdessen ein Befund **über die Regel**:
`C_POSSIBLE_VALUE_CONFLICT` vergleicht Größen, die nur die Einheit teilen,
und erzeugt damit überwiegend Rauschen, sobald Kontext da ist. Das gehört
auf die Liste, ist aber ein anderes Arbeitspaket.

### Was dabei auffiel und nicht in der Stichprobe stand

Über alle 80 Retrieval-Läufe gezählt:

| | Posten | Zeichen |
|---|---:|---:|
| lexikalisch | 38 von 120 — 32 % | 53 024 von 217 222 — **24 %** |
| hybrid | 54 von 128 — 42 % | 81 926 von 222 750 — **37 %** |

*Korrektur: die frühere Fassung nannte nur die 32/42 % und sprach dabei vom
„Budget". Das waren Anteile der **Posten**. Nach tatsächlich verbrauchtem
Text sind es **24 % bzw. 37 %** — Einleitungen sind kleiner als der
Durchschnitt (1395 bzw. 1517 Zeichen gegen rund 1950 der übrigen). Der
Effekt bleibt erheblich, ist aber kleiner, als die Postenzahl nahelegt.*

Die sechs meistgelieferten Abschnitte des gesamten Korpus:

```
 30x  1. Scope & System Interface Topography
 20x  2. Comprehensive DTC Master Table
 18x  1. Scope & Technical Overview
 16x  1. Network Topography & Communication Stack
 12x  1. Scope & Post-Crash Safety Topography
 12x  2. CAN-FD Broadcast Message Matrix
```

Ein Viertel bis gut ein Drittel des verbrauchten Kontexttextes geht an die
Einleitungsabschnitte. Hybrid ist darin **schlechter**, weil semantische
Ähnlichkeit allgemein gehaltene thematische Prosa belohnt — und genau das
ist eine Einleitung. Diese Abschnitte verdrängen über das Zeichenbudget
Schwester-Anforderungen und Aufgaben-Treffer, die etwas beigetragen hätten.

**Das ist eine Arbeitshypothese, keine bewiesene Ursache.** Schlechte
Segmentierung, Rangfolge und Budgetverteilung können gemeinsam beitragen,
und die drei sind hier nicht getrennt gemessen. Dass die Aufgaben-Suche in
22 von 40 Läufen am Budget scheiterte, zeigt mindestens einen zweiten Anteil.
Und **Einleitungen pauschal zu entfernen wäre falsch**: manche nennen
Betriebsbedingungen, die für einen Test gebraucht werden.

## 4. Empfehlung

**Lexikalisch bleibt Standard. Hybrid nicht einführen, auch nicht gezielt.
Der nächste Engpass ist die Kontextverwendung, und zwar ihr erster Schritt:
was überhaupt als abrufbare Passage im Index landet.**

Begründung aus den Zahlen:

* Hybrid liefert nachweislich andere Passagen (38 von 40 Paaren), ohne dass
  daraus ein gezeigter Generierungsnutzen folgt. Bei unbelegten Werten liegt
  es besser als lexikalisch (1,32 gegen 1,60), bei den Befunden für den
  Reviewer schlechter (8,80 gegen 7,83) — zusammen reicht das weder für eine
  Einführung noch für ein endgültiges Nein. Die Quellenmessung zeigt den
  Vorteil bei Prosafragen; die Generierung holt ihn in dieser Messung nicht
  ab, und der Hebel dafür war in 22 von 40 Läufen blockiert.
* Der Mehraufwand ist real: ein Modell, 203 Vektoren, ein Cache, ein
  Fallback-Pfad. Dafür gibt es derzeit keinen Gegenwert.
* Die Paarprüfung zeigt, dass gute Quellen gefunden werden und ungenutzt
  bleiben. Eine bessere Rangfolge vergrößert den ungenutzten Vorrat.

**Gegenevidenz, die ich nicht unterschlage:** der Vergleich ist diagnostisch,
nicht unabhängig — die 20 Anforderungen wurden gewählt, weil eine Prosafrage
zu ihnen existiert, und dieselben Fragen dienten vorher der
Retriever-Bewertung. Und die Aufgaben-Suche, der eigentliche Hebel von
hybrid, war in 22 von 40 Läufen durch das Budget blockiert. Ein Hybrid-Lauf
mit reserviertem Budget für die Aufgaben-Suche ist **nicht** gemessen. Wer
hybrid endgültig abschreiben will, sollte das vorher wissen; mein Vorschlag
ist trotzdem, zuerst die Passagen zu reparieren, weil davon beide Retriever
profitieren und weil 42 % Einleitungstext kein Rangfolgenproblem ist.

**Was die Zahlen nicht entscheiden:** ob die fachliche Testgenerierung der
übernächste Engpass ist. Dafür bräuchte es eine Abnahme durch Tester, nicht
durch mich. Die Rückfragenzahlen taugen dafür nicht — sie schwanken zu
stark und uneinheitlich.

## Grenzen dieser Messung

* Diagnostischer Vergleich auf bekannten Aufgaben, kein unabhängiger
  Abschlusstest, nicht repräsentativ für die 136 Anforderungen des Bestands.
* 20 Anforderungen aus 10 der 22 Dokumente, zwei Wiederholungen.
* Ein Generierungsmodell, ein Embedding-Modell.
* Die Urteile in Abschnitt 3 sind meine Lesart, nicht die eines Testers.
* Weniger Rückfragen ist hier nirgends als Erfolg gewertet worden.
