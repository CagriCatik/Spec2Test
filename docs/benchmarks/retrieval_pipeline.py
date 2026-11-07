#!/usr/bin/env python3
"""Found is not sent: the whole context path, offline, without a chat model.

The source benchmark answered "does the retriever rank the right passage
highly". That is only the first half. A passage that is found and then
dropped by the character budget has helped nobody. This script runs the real
``build_context`` for every query and records, per query:

For the ranking half the target is the passage holding the answer. For the
pipeline half it cannot be: a paraphrase asks about requirement X, and X is
the requirement under test - the generator already has it and correctly
never sends a requirement as its own context. So the pipeline half uses a
mechanically derived set of **connected** passages instead:

* a requirement X explicitly references, or
* a passage whose text carries an identifier that X also carries
  (``Pack_Current_Validated`` and the like), or
* a passage from the same document section X lives in.

That is a proxy, not a judgement. A connected passage can be useless and an
unconnected one can be exactly right; only the reading in retrieval-arms.md
decides relevance. What the proxy does show, and what no ranking number
shows, is how much of the budget leaves for passages with no demonstrable
link to the requirement at all.

* **found**     - a connected passage is in the retriever's top ``limit``
* **sent**      - at least one connected passage survived deduplication, the
                  item limit and the budget
* **connected / unconnected characters** - how the sent budget divides
* **starved**   - connected passages were found and none was sent

Two question sets are evaluated separately:

``tuned``   the 20 paraphrases plus derived ID and signal queries. The
            retriever weighting was chosen with these in view, so a good
            result here is expected and proves little.
``holdout`` written after the weighting and the segmentation were fixed,
            over requirements the first set never touched. Nothing has been
            tuned on them.

    .venv/bin/python docs/benchmarks/retrieval_pipeline.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from spec2test.adapters.knowledge import (  # noqa: E402
    EmbeddingUnavailable,
    HybridKnowledgeIndex,
    OllamaEmbedder,
    SqliteKnowledgeIndex,
    VectorKnowledgeIndex,
)
from spec2test.application.context import build_context  # noqa: E402
from spec2test.application.indexing import collect_documents, sync_index  # noqa: E402
from spec2test.application.ingest import reference_report  # noqa: E402
from spec2test.application.knowledge import normalise_for_search  # noqa: E402
from spec2test.application.retrieval_eval import (  # noqa: E402
    build_query_set,
    comparison_table,
    evaluate,
)
from spec2test.adapters.documents import DEFAULT_IMPORTERS  # noqa: E402

HERE = Path(__file__).resolve().parent


def terms_of(text: str) -> list[str]:
    return [t for t in normalise_for_search(text).split() if len(t) > 2]


IDENT = __import__("re").compile(r"\b[A-Z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b")


def connected_passages(requirement, passages) -> set[str]:
    """Passages with a demonstrable link to this requirement. A proxy."""
    own = {
        f"{span.document_id}:req:{span.char_start}" for span in requirement.spans
    }
    identifiers = set(IDENT.findall(requirement.text))
    referenced = set(requirement.references or ())
    documents = {span.document_id for span in requirement.spans}
    linked = set()
    for passage in passages:
        if passage.passage_id in own:
            continue
        if passage.external_id and passage.external_id in referenced:
            linked.add(passage.passage_id)
        elif identifiers and identifiers & set(IDENT.findall(passage.text)):
            linked.add(passage.passage_id)
        elif passage.document_id in documents:
            linked.add(passage.passage_id)
    return linked


def pipeline_row(
    query, requirement, requirements, documents, knowledge, passages,
    *, limit, char_budget
) -> dict:
    """One query through the retriever and then through build_context."""
    if requirement is None:
        return {"query_id": query.query_id, "family": query.family, "pipeline": False}
    helpful = connected_passages(requirement, passages)
    hits = knowledge.search(terms_of(query.text), limit=limit)
    found = any(h.passage.passage_id in helpful for h in hits)

    selection = build_context(
        [requirement],
        reference_report(requirements),
        documents=documents,
        knowledge=knowledge,
        char_budget=char_budget,
        task_description=query.text,
    )
    ledger = selection.ledger
    connected = unconnected = 0
    sent_ids = []
    for item in selection.items:
        sent_ids.append(item.requirement_id)
        if item.requirement_id in helpful:
            connected += item.size
        else:
            unconnected += item.size
    sent = any(pid in helpful for pid in sent_ids)
    return {
        "query_id": query.query_id,
        "family": query.family,
        "pipeline": True,
        "helpful_known": len(helpful),
        "found": found,
        "sent": sent,
        "starved": bool(found and not sent),
        "connected_chars": connected,
        "unconnected_chars": unconnected,
        "items_sent": len(sent_ids),
        "omitted": getattr(ledger, "omitted", 0),
        "reserved_for_task": getattr(ledger, "reserved_for_task", 0),
        "used_by_task": getattr(ledger, "used_by_task", 0),
    }


def summarise(name: str, rows: list[dict]) -> list[str]:
    """Only paraphrases go through the pipeline; the rest is ranking only."""
    rows = [r for r in rows if r.get("pipeline")]
    if not rows:
        return [f"{name}: no pipeline run", ""]
    out = [f"{name}  ({len(rows)} prose questions through build_context)"]
    out.append(
        f"  {'n':>4}{'found':>8}{'sent':>8}{'starved':>9}{'items':>8}"
        f"{'connected chars':>18}{'unconnected':>14}{'omitted':>9}"
    )
    connected = sum(r["connected_chars"] for r in rows)
    unconnected = sum(r["unconnected_chars"] for r in rows)
    share = (
        f"{100 * connected / (connected + unconnected):.0f}%"
        if connected + unconnected
        else "-"
    )
    out.append(
        f"  {len(rows):>4}"
        f"{sum(r['found'] for r in rows) / len(rows):>8.2f}"
        f"{sum(r['sent'] for r in rows) / len(rows):>8.2f}"
        f"{sum(r['starved'] for r in rows):>9}"
        f"{sum(r['items_sent'] for r in rows) / len(rows):>8.1f}"
        f"{connected:>12} ({share:>3}){unconnected:>14}"
        f"{sum(r['omitted'] for r in rows):>9}"
    )
    reserved = sum(r["reserved_for_task"] for r in rows)
    used = sum(r["used_by_task"] for r in rows)
    out.append(
        f"  budget held for the task text: {reserved} characters, "
        f"{used} used ({100 * used / reserved:.0f}%)"
        if reserved
        else "  budget held for the task text: none"
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--char-budget", type=int, default=6_000)
    parser.add_argument("--embed-model", default="nomic-embed-text")
    parser.add_argument("--no-vector", action="store_true")
    parser.add_argument("--out", default=str(HERE / "retrieval-pipeline-result.txt"))
    args = parser.parse_args()

    work = Path(args.out).parent / ".retrieval-bench"
    work.mkdir(parents=True, exist_ok=True)
    lexical = SqliteKnowledgeIndex(work / "pipeline.db")
    synced = sync_index(
        lexical, collect_documents(ROOT / "requirements"), DEFAULT_IMPORTERS
    )
    requirements = list(synced.requirements)
    documents = dict(synced.documents)
    by_external = {r.external_id: r for r in requirements if r.external_id}
    print(f"{len(documents)} documents, {lexical.passage_count()} passages")

    from spec2test.adapters.knowledge.vector_index import _all_passages

    passages = _all_passages(lexical)

    retrievers = [("fts5 (bm25)", lexical)]
    if not args.no_vector:
        vector = VectorKnowledgeIndex(
            lexical,
            OllamaEmbedder(model=args.embed_model),
            cache_path=work / f"pipeline-vectors-{args.embed_model}.json",
        )
        try:
            vector.build(progress=lambda line: print(f"  {line}"))
        except EmbeddingUnavailable as exc:
            print(f"  embeddings unavailable: {exc}", file=sys.stderr)
        else:
            retrievers.append(("hybrid (rank fusion)", HybridKnowledgeIndex(lexical, vector)))

    sets = {
        "tuned (the 20 questions the weighting was chosen with)":
            HERE / "retrieval-paraphrases.json",
        "holdout (written afterwards, never used for tuning)":
            HERE / "retrieval-paraphrases-holdout.json",
    }

    lines: list[str] = [
        f"corpus: {len(documents)} documents, {lexical.passage_count()} passages",
        f"a hit counts within the first {args.limit}; "
        f"context budget {args.char_budget} characters",
        "",
    ]
    for label, path in sets.items():
        spec = json.loads(path.read_text(encoding="utf-8"))
        pairs = [(q["requirement"], q["question"]) for q in spec["questions"]]
        known = [(rid, q) for rid, q in pairs if rid in by_external]
        if len(known) != len(pairs):
            missing = [rid for rid, _ in pairs if rid not in by_external]
            lines.append(f"  NOT IN THE CORPUS, skipped: {', '.join(missing)}")
        queries = build_query_set(passages, max_per_family=20, paraphrases=known)
        by_query_requirement = {
            f"para-{rid}": by_external[rid] for rid, _ in known
        }

        lines.append("=" * 78)
        lines.append(label.upper())
        lines.append("=" * 78)
        scores = []
        for name, retriever in retrievers:
            rows = [
                pipeline_row(
                    query,
                    by_query_requirement.get(query.query_id),
                    requirements,
                    documents,
                    retriever,
                    passages,
                    limit=args.limit,
                    char_budget=args.char_budget,
                )
                for query in queries
            ]
            lines += summarise(name, rows)
            lines.append("")
            scores.append(
                evaluate(
                    retriever, queries, name=name, limit=args.limit, terms_of=terms_of
                )
            )
        lines.append("ranking only, for comparison with retrieval-sources.md:")
        lines.append(comparison_table(scores))

    text = "\n".join(lines)
    print()
    print(text)
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
