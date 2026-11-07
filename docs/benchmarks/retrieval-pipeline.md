# Gefunden ist nicht gesendet

Die Quellenmessung beantwortete die erste Hälfte: *rankt der Retriever die
richtige Passage hoch?* Diese Messung beantwortet die zweite: *kommt sie
auch im Prompt an?* Beides offline, ohne Generierungsmodell.

Reproduzieren: `.venv/bin/python docs/benchmarks/retrieval_pipeline.py`.
Ausgabe: [retrieval-pipeline-result.txt](retrieval-pipeline-result.txt).

## Was gemessen wird, und was nicht

Für die Rangfolge ist das Ziel die Passage mit der Antwort. Für die
Pipeline kann es das nicht sein: eine Paraphrase fragt nach Anforderung X,
und X ist die Anforderung unter Test — der Generator hat sie bereits und
schickt sie zu Recht nie als ihren eigenen Kontext. *(Das war mein erster
Messaufbau, und er lieferte „gesendet: 0,00" für alles. Kein Produktfehler,
ein Messfehler.)*

Die Pipeline-Hälfte benutzt deshalb eine **mechanisch abgeleitete** Menge
*verbundener* Passagen: solche, die X ausdrücklich referenziert, oder die
einen Bezeichner tragen, den X auch trägt, oder die aus demselben Dokument
stammen.

**Das ist ein Hilfsmaß, kein Urteil.** Eine verbundene Passage kann nutzlos
sein, eine unverbundene genau richtig. Über Relevanz entscheidet allein die
Lektüre in [retrieval-arms.md](retrieval-arms.md). Was das Maß zeigt und
keine Rangfolgenkennzahl zeigt: wie viel des Budgets an Passagen geht, zu
denen sich überhaupt keine Verbindung nachweisen lässt.

## Zwei Fragenmengen, getrennt

| | n | Herkunft |
|---|---:|---|
| `tuned` | 20 | die Paraphrasen, mit denen die Gewichtung gewählt wurde |
| `holdout` | 16 | nach der Gewichtung und der Segmentierung geschrieben, über Anforderungen, die die erste Menge nie berührt hat |

Der Holdout ist in
[retrieval-paraphrases-holdout.json](retrieval-paraphrases-holdout.json)
festgehalten, mit dem Vermerk, dass er seinen Zweck verliert, sobald
irgendein Schwellwert auf seine Zahlen hin geändert wird.

## Ergebnis

Kontextbudget 6000 Zeichen, Treffer zählt in den ersten 10.

| Menge | Retriever | gefunden | **gesendet** | verhungert | verbundene Zeichen | unverbundene |
|---|---|---:|---:|---:|---:|---:|
| tuned | FTS5 | 0,85 | **0,50** | 9 von 20 | 23 447 (**22 %**) | 82 729 |
| tuned | hybrid | 0,90 | **0,60** | 7 von 20 | 18 751 (**17 %**) | 89 263 |
| holdout | FTS5 | 0,81 | **0,69** | 4 von 16 | 28 903 (**34 %**) | 55 664 |
| holdout | hybrid | 0,81 | **0,62** | 5 von 16 | 15 756 (**18 %**) | 72 792 |

**Die Lücke ist der Befund.** Eine verbundene Passage wird in 81–90 % der
Fälle gefunden und erreicht den Prompt in 50–69 %. Bei 4 bis 9 von 20 bzw.
16 Fragen wurde etwas Verbundenes gefunden und **nichts davon gesendet**.

**66 bis 83 % des gesendeten Kontextbudgets gehen an Passagen ohne
nachweisbare Verbindung zur Anforderung.** Hybrid ist darin in beiden Mengen
schlechter: 17 % gegen 22 % und 18 % gegen 34 %.

Die Budgetreservierung für die Aufgaben-Suche wirkt: von den
zurückgehaltenen Zeichen werden 76–91 % tatsächlich genutzt. Vorher
steuerte die Aufgaben-Suche in 22 von 40 Läufen gar nichts bei.

## Was der Holdout über meine erste Messung sagt

Unangenehm und wichtig. Auf den **selbst geschriebenen 20** Fragen war BM25
bei Prosa fast blind (MRR 0,172, 12 von 20 gar nicht gefunden). Auf den
**16 Holdout-Fragen** ist es deutlich besser:

| Paraphrasen | FTS5 MRR | hybrid MRR | FTS5 r@5 | hybrid r@5 |
|---|---:|---:|---:|---:|
| tuned (20) | 0,172 | 0,313 | 0,25 | 0,45 |
| **holdout (16)** | **0,414** | **0,494** | **0,69** | **0,69** |

Bei gleicher Trefferquote auf Platz 5 und einem MRR-Abstand von 0,08 statt
0,14 schrumpft der gemessene Vorteil semantischer Suche erheblich. Meine
erste Fragenmenge war für BM25 schwerer, als eine frisch geschriebene es
ist — ich habe die Bezeichner beim Formulieren bewusst vermieden und damit
unabsichtlich ein lexikalisches Worst-Case-Set gebaut. **Die Aussage
„BM25 ist bei Prosafragen fast blind" war überzogen** und gilt in dieser
Schärfe nur für jene 20 Fragen.

Damit steht die Empfehlung aus [retrieval-arms.md](retrieval-arms.md) eher
fester: für hybrid spricht noch weniger als gedacht.

## Woran es liegt, soweit messbar

| Beitrag | Beleg | Status |
|---|---|---|
| Budget: Verbundenes wird gefunden und verdrängt | 4–9 verhungerte Fragen je Menge; 173–196 ausgelassene Treffer | **gemessen** |
| Rangfolge: Unverbundenes steht vor Verbundenem | 66–83 % unverbundene Zeichen | **gemessen** |
| Segmentierung: Passagen zu grob | Tabellen jetzt zeilenweise (203 → 213 Passagen, 15 mit Spaltenkopf) | geändert, Wirkung **nicht isoliert gemessen** |

Die drei wirken zusammen; diese Messung trennt sie nicht. Wer eine
Ursachenaussage will, muss sie einzeln abschalten.

## Grenzen

* 36 Fragen über 22 Dokumente einer Domäne.
* „Verbunden" ist ein mechanisches Hilfsmaß, keine fachliche Relevanz.
* Ein Embedding-Modell.
* Kein Generierungsmodell beteiligt: diese Zahlen sagen nichts darüber, ob
  ein gesendeter Kontext auch benutzt wird. Das bleibt die offene Frage aus
  [retrieval-arms.md](retrieval-arms.md).
