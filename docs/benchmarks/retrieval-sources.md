# Quellenfindung: lexikalisch gegen semantisch

Diese Messung beantwortet eine Frage und nur diese: **findet der Retriever die
Passage, in der die Antwort steht?** Die Qualität der daraus geschriebenen
Testentwürfe ist eine getrennte Messung ([README.md](README.md)) und steht
noch aus. Beides zugleich zu messen hatte beim letzten Versuch genau das
Problem erzeugt, das der Review benennt: eine Kennzahl, die keinen Gewinn
zeigt, ohne dass man weiß, ob die Suche oder die Generierung schuld ist.

## Aufbau

Beide Retriever lesen **dieselben Passagen** aus demselben SQLite-Index. Das
Zerlegen der Dokumente, die Klassifikation der Passagen und die Auflösung
exakter Kennungen bleiben unverändert in `application/knowledge.py`. Nur die
Rangfolge unterscheidet sich — sonst würde der Vergleich zwei Dinge auf
einmal messen.

| | |
|---|---|
| Korpus | `requirements/*.md`, 22 Dokumente, 203 Passagen |
| lexikalisch | SQLite FTS5, BM25 (der bestehende Retriever) |
| semantisch | LlamaIndex + `nomic-embed-text` über Ollama, 768 Dimensionen, Kosinus |
| hybrid | beide, verschmolzen per Reciprocal Rank Fusion (k = 60, gleich gewichtet) |

Reproduzieren:

```
.venv/bin/python docs/benchmarks/retrieval_sources.py
```

Ohne Embedding-Modell: `--no-vector`. Das Modell wird einmal geholt
(`ollama pull nomic-embed-text`), die Vektoren liegen danach im Cache.

## Die Fragenmenge

60 Fragen in drei Familien, weil sie unterschiedlich scheitern.

| Familie | n | Beispiel | Erwartete Fundstelle wird abgeleitet aus |
|---|---|---|---|
| `exact_id` | 20 | `BMS-REQ-AFE-0104` | der Passage, die diese Kennung trägt |
| `signal_name` | 20 | `Pack_Current_Validated` | jeder Passage, deren Text den Bezeichner enthält |
| `paraphrase` | 20 | „Was macht die Batterie, wenn das Auto einen Unfall hat?" | der Anforderung, zu der die Frage geschrieben wurde |

Nur der **Wortlaut** der Paraphrasen ist von Hand geschrieben
([retrieval-paraphrases.json](retrieval-paraphrases.json)); ihre erwartete
Antwort ist die darin genannte Anforderung und wird zur Laufzeit gegen den
Korpus aufgelöst — eine falsche Kennung scheitert laut statt still eine Null
zu zählen. Die anderen 40 Fragen und alle erwarteten Fundstellen sind
maschinell aus dem Korpus abgeleitet.

Die Paraphrasen vermeiden bewusst die Bezeichner und die Titelwörter ihrer
Anforderung. Das ist der Zweck der Familie: die lexikalische Suche hat nichts
zum Treffen.

## Ergebnis

Ein Treffer zählt, wenn die erwartete Passage unter den ersten 10 steht.

| | exact_id | | signal_name | | paraphrase | | gesamt | |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| | MRR | r@5 | MRR | r@5 | MRR | r@5 | MRR | nicht gefunden |
| FTS5 (BM25) | **1,000** | **1,00** | **0,942** | **1,00** | 0,172 | 0,25 | 0,705 | 12 |
| Embeddings | 0,114 | 0,20 | 0,531 | 0,75 | **0,369** | **0,65** | 0,338 | 19 |
| Hybrid | **1,000** | **1,00** | 0,932 | 0,95 | 0,314 | 0,50 | **0,749** | **8** |

Vollständige Ausgabe mit allen Fehlschlägen:
[retrieval-sources-result.txt](retrieval-sources-result.txt).

## Was daraus folgt

**Keiner der beiden gewinnt. Sie scheitern an verschiedenen Fragen.**

* BM25 ist bei exakten Kennungen **fehlerfrei** und bei Signalnamen fast
  fehlerfrei. Bei Prosa findet es in **12 von 20 Fällen überhaupt nichts** —
  auch nicht auf Platz 10.
* Embeddings sind bei Prosa mehr als **doppelt so gut** (MRR 0,172 → 0,369;
  Fehlschläge 12 → 4), aber bei exakten Kennungen unbrauchbar: 13 von 20
  Anforderungskennungen findet das Modell nicht. Das ist kein Mangel dieses
  Modells, sondern die bekannte Schwäche dichter Vektoren bei
  Zeichenketten ohne Bedeutung.
* Der Hybrid ist in der Summe besser als beide einzeln: MRR 0,705 → **0,749**,
  nicht gefundene Fragen 12 → **8**, bei unveränderter Perfektion auf
  Kennungen. Den vollen semantischen Gewinn holt er nicht (Paraphrasen r@5
  0,50 gegen 0,65 der reinen Embeddings): das lexikalische Rauschen auf
  dieser Familie verdünnt das Signal.

**Zur Gewichtung.** Die naheliegende Korrektur wäre, die semantische Seite
höher zu gewichten. Gemessen (Sweep über 1:1 bis 1:5 und k ∈ {10, 60}):
jede Höhergewichtung zerstört die Kennungssuche, schon 1:2 fällt der MRR auf
exakten IDs von 1,000 auf 0,55. Der Paraphrasen-Gewinn ist dabei klein
(0,314 → 0,408). Deshalb bleibt es bei **gleicher Gewichtung**. k = 10 wäre
auf dieser Fragenmenge minimal besser (MRR 0,755, 6 Fehlschläge) als k = 60
(0,749, 8); der Unterschied liegt bei 60 Fragen im Rauschen, deshalb bleibt
der Standardwert 60 stehen, statt ihn auf diese Menge zu passen.

## Gilt das für den tatsächlichen Gebrauch?

Zum Teil. Die Kontextsuche während der Generierung ruft
`search_terms(requirement)` auf: eine Liste aus Bezeichnern und Stichwörtern,
keine Prosafrage. Dafür ist die lexikalische Seite klar die bessere, und der
Hybrid ändert dort wenig.

Die Paraphrasen-Familie zählt für den anderen Eingang: das freie Feld
**„Task for this run"** in der Oberfläche, in dem ein Tester in Prosa
beschreibt, was geprüft werden soll. Dafür ist die lexikalische Suche heute
fast blind.

## Stand im Produkt

`generation.retrieval` schaltet um, Standard ist `lexical`:

```yaml
generation:
  use_retrieval: true
  retrieval: hybrid            # lexical | hybrid
  embedding_model: nomic-embed-text
```

Hybrid bleibt **abschaltbar und nicht voreingestellt**, bis gemessen ist, was
es mit den geschriebenen Entwürfen macht. Bessere Quellenfindung ist eine
notwendige, keine hinreichende Bedingung für bessere Tests — die letzte
Messung hat gezeigt, dass mehr Kontext die Rückfragen des Modells
unterdrücken kann. Ist das Modell nicht erreichbar, fällt der Lauf auf
lexikalisch zurück und schreibt eine Warnung ins Protokoll.

## Was diese Messung nicht sagt

* Nichts über die fachliche Qualität der erzeugten Tests. Das ist die
  nächste Messung.
* Nichts über andere Embedding-Modelle. Gemessen wurde eines.
* Nichts über einen anderen Korpus. 22 Dokumente einer Domäne, 60 Fragen —
  genug für die Richtung, zu wenig für zwei Nachkommastellen.
* Die Paraphrasen sind von einem Modell geschrieben, nicht von Testern. Sie
  sind als Suchanfragen gedacht, nicht als fachliche Bewertung. Von Testern
  formulierte Fragen wären die bessere Menge.
