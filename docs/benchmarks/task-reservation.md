# 16 Läufe: nützt die Aufgabenreservierung den Entwürfen?

**Nein — nicht nachweisbar. Der Vergleich kann die Frage grossenteils gar
nicht beantworten, und wo er es kann, liegt der Effekt in der Streuung des
Modells. Die Reservierung ist deshalb standardmäßig aus und bleibt
einschaltbar.**

Rohdaten: [task-reservation-rows.json](task-reservation-rows.json).
Reproduzieren: `docs/benchmarks/compare_task_reservation.py`.

## Aufbau

Einziger Unterschied: `task_budget_share`. Segmentierung, Tabellenköpfe und
„erst sammeln, dann auswählen" sind in beiden Varianten gleich.

| | Reservierung | Anforderungen | Wiederholungen | Läufe |
|---|---:|---:|---:|---:|
| A | 0 % | 4 | 2 | 8 |
| B | 25 % | 4 | 2 | 8 |

Lexikalischer Retriever, `gpt-oss:120b-cloud`, Prompt `l1-test-draft-5`,
Budget 6000 Zeichen, gleiche Aufgabenbeschreibung. Die Varianten wechseln
sich in der Reihenfolge ab; gepaart wird nach Anforderung und Wiederholung.
Die vier Fälle sind die bereits von Hand gesichteten: SC-0901, DSC-1504,
NM-1901, HVC-0305. **Diagnostisch auf bekannten Fällen, kein unabhängiger
Abschlusstest.**

Vollständigkeit: 16 geplant, **16 abgeschlossen**, 0 Fehlschläge,
18 Modellaufrufe, **1 Reparatur** (Arm B). Kein Lauf über dem
Zeichenbudget; Tabellenköpfe sind mitgezählt (im Schnitt 235 Zeichen je
Lauf, in beiden Armen gleich).

## Das Hauptergebnis ist ein Nicht-Ergebnis

**In 6 von 8 Paaren ist der gesendete Kontext Zeichen für Zeichen
identisch.** Nur bei SC-0901 ändert die Reservierung überhaupt etwas.

| Anforderung | Wdh. | A Rückfragen | B Rückfragen | A unbelegt | B unbelegt | Kontext verschieden |
|---|---:|---:|---:|---:|---:|---|
| SC-0901 | 1 | 12 | 8 | 10 | 6 | **ja** |
| SC-0901 | 2 | 11 | 7 | 6 | 6 | **ja** |
| DSC-1504 | 1 | 6 | 8 | 1 | 0 | nein |
| DSC-1504 | 2 | 13 | 6 | 2 | 0 | nein |
| NM-1901 | 1 | 6 | 8 | 0 | 0 | nein |
| NM-1901 | 2 | 8 | 10 | 0 | 0 | nein |
| HVC-0305 | 1 | 6 | 3 | 0 | 0 | nein |
| HVC-0305 | 2 | 7 | 5 | 0 | 0 | nein |

Die sechs Paare mit identischem Kontext messen nichts als die Streuung des
Modells — und die ist groß: **DSC-1504, Wiederholung 2, liefert bei exakt
derselben Eingabe 13 gegen 6 Rückfragen.** Über alle sechs:
mittlerer Abstand 3,0 Rückfragen, Maximum 7.

Der Abstand in den beiden Paaren, die sich wirklich unterscheiden, beträgt
4 und 4. **Er liegt innerhalb des Rauschens.** Die Mittelwerte unten sind
daher nicht aussagekräftig und stehen nur der Vollständigkeit halber da.

| Mittel je Lauf | A (0 %) | B (25 %) |
|---|---:|---:|
| Entwürfe | 2,00 | 2,00 |
| offene Rückfragen | 8,62 | 6,88 |
| unbelegte Werte | 2,38 | 1,50 |
| Erwartungen ohne Kriterium | 4,00 | 3,75 |
| Befunde für den Reviewer | 8,25 | 7,50 |
| Kontextposten | 4,00 | 4,25 |
| davon durch die Aufgabe | 0,00 | **0,25** |
| Kontextzeichen | 5717 | 5757 |
| Kontextwerte übernommen | 0,12 | 0,12 |
| Reparaturen | 0,00 | 0,12 |

## Warum die Reservierung fast nichts bewegt

Bei 6000 Zeichen Budget sind 25 % reserviert = **1500 Zeichen**. Die
Kandidaten der Aufgaben-Suche sind fast alle größer:

```
SC-0901   3222  2562  2480  2351 Zeichen
DSC-1504  2128  2562  2736  1580
NM-1901   2161  2544   683  2562     <- einer passt
HVC-0305  2562  2385  2423  2247
```

Eine Reservierung, in die keine ganze Passage passt, kann nichts
zurückhalten. Von 16 Treffern passt einer — und der war bei NM-1901 bereits
von der Anforderungs-Suche gefunden und wird korrekt nicht doppelt
geschickt. **Die offline gemessenen „12 von 16 statt 5 von 16" gelten für
die Fragenmenge als Ganzes; diese vier Fälle gehören zur Minderheit, bei
der sie nicht greift.** Das ist Folge der Fallauswahl, nicht ein Fehler.

Ein eigener Fehler, den ich dabei gefunden habe: der Messaufbau umhüllt die
Frage mit „Focus this run on the following question a tester asked: ". Die
Füllwörter verdünnen die Suchanfrage und kosten über die 16 Holdout-Fragen
**2 Treffer (12 → 10)**. In der Oberfläche gibt es diese Hülle nicht; sie
ist ein Artefakt meiner Benchmarks, auch des 120-Lauf-Vergleichs.

## Fachliche Bewertung der Entwürfe

> **Meine Einschätzung (Claude), nicht das Urteil eines Testers.**

Nur SC-0901 trägt überhaupt Information. Kontext:

| Arm | Passagen |
|---|---|
| A | DTC-Tabelle (689), Netztopologie (2234), **„1. Scope & Ultra-Fast Safety Topography"** (2364, eine Dokumenteinleitung) |
| B | DTC-Tabelle (689), Netztopologie (2234), **„2. Ultra-Fast Short-Circuit Trip Timing Sequence"** (1217, der einschlägige Abschnitt), *Aufgabentreffer* „Network Topography & Communication Stack" (1307) |

**Welche hilfreiche Information wurde korrekt genutzt?**
In B: die Komparatorschwelle. Der Entwurf schreibt *„Shunt voltage exceeds
the comparator reference (≥250 mV)"* und *„≈255 mV step"*. Die Anforderung
nennt 2550 A und einen 100 µΩ-Shunt; 2550 A × 100 µΩ = 255 mV. Die
Rechnung stimmt und macht den Schritt ausführbar. In A fehlt sie.

**Welche Erwartung ist unbelegt?**
In A: *„Pulse rise time is sufficiently fast to represent an ultra-fast
short circuit (open question)"* — keine Zahl, keine Quelle, als Rückfrage
markiert. Dasselbe in Testfall 2. A hat 10 unbelegte Werte, B 6.

**Welche wichtigen Testbedingungen fehlen?**
In beiden: die Anstiegszeit des Prüfpulses und die Toleranz des
Komparators bleiben offen. Die Netztopologie-Passage, die beide Arme
tragen, beantwortet davon nichts.

**Ist der Entwurf brauchbarer?**
B ja — ein Tester kann *„≤2,0 µs nach Überschreiten von 250 mV"* an den
Prüfstand geben, *„sufficiently fast"* nicht.

**Aber der Grund ist nicht die Reservierung.** Der nützliche Abschnitt
(„Trip Timing Sequence") kam über die **Anforderungs-Suche**, nicht über die
Aufgaben-Suche: die niedrigere Obergrenze für Nicht-Aufgaben-Posten hat die
Rangfolge umsortiert und die 2364-Zeichen-Einleitung gegen den 1217-Zeichen-
Fachabschnitt getauscht. Der tatsächliche **Aufgabentreffer war die
Netztopologie — thematisch doppelt und für Lichtbogen und Auslösezeit
irrelevant.**

Das ist ein Nebeneffekt der Reservierung, kein Beleg für ihren Zweck, und er
tritt bei einem von vier Fällen auf.

## Entscheidung

Du hattest die Regel vorab genannt: zeigt der Vergleich keinen
nachvollziehbaren Nutzen, Standard aus und konfigurierbar erhalten. Der
Vergleich zeigt keinen. Umgesetzt:

```yaml
generation:
  task_budget_share: 0.0      # aus; 0.25 schaltet sie ein
```

`TASK_BUDGET_SHARE` steht auf `0.0`, `GenerationSettings.task_budget_share`
und `generation.task_budget_share` schalten sie je Lauf ein. Nichts anderes
wurde geändert: Segmentierung, Tabellenköpfe und „erst sammeln, dann
auswählen" bleiben aktiv.

## Grenzen

* 16 Läufe, vier bekannte Fälle, ein Modell. Diagnostisch.
* Die Streuung des Modells ist so groß wie der gesuchte Effekt. Ein
  belastbarer Nachweis bräuchte deutlich mehr Wiederholungen — die Frage
  ist es nicht wert.
* Die vier Fälle sind solche, bei denen die Reservierung kaum greift. Ein
  Vergleich über Fälle, bei denen sie greift, ist **nicht** gemacht; er
  würde ihren Nutzen fairer prüfen, hätte aber neue Läufe gebraucht.
* Die Hülle um die Aufgabenbeschreibung ist ein Artefakt der Benchmarks
  und sollte dort entfernt werden, bevor noch einmal gemessen wird.
