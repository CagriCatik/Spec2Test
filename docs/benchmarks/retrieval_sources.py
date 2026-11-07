#!/usr/bin/env python3
"""Source finding: lexical BM25 against embeddings, over the same passages.

Run from the repository root:

    .venv/bin/python docs/benchmarks/retrieval_sources.py

Nothing is generated and no chat model is called. The two retrievers read the
same SQLite index, so every difference is ranking, not parsing. Embeddings
come from a local Ollama model and are cached, so a second run is fast.

    --embed-model   the Ollama embedding model (default nomic-embed-text)
    --limit         how deep a hit still counts (default 10)
    --no-vector     only the lexical retriever, when no model is available
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
from spec2test.application.indexing import sync_index  # noqa: E402
from spec2test.application.knowledge import normalise_for_search  # noqa: E402
from spec2test.application.retrieval_eval import (  # noqa: E402
    build_query_set,
    comparison_table,
    evaluate,
)
from spec2test.bootstrap import build_importers  # noqa: E402

HERE = Path(__file__).resolve().parent


def terms_of(text: str) -> list[str]:
    """Split a query the way the application splits a requirement."""
    return [t for t in normalise_for_search(text).split() if len(t) > 2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(ROOT / "requirements"))
    parser.add_argument("--embed-model", default="nomic-embed-text")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--no-vector", action="store_true")
    parser.add_argument("--out", default=str(HERE / "retrieval-sources-result.txt"))
    args = parser.parse_args()

    paths = sorted(Path(args.corpus).glob("*.md"))
    if not paths:
        print(f"no documents in {args.corpus}", file=sys.stderr)
        return 2

    work = Path(args.out).parent / ".retrieval-bench"
    work.mkdir(parents=True, exist_ok=True)
    lexical = SqliteKnowledgeIndex(work / "knowledge.db")
    synced = sync_index(lexical, paths, build_importers())
    print(
        f"{len(paths)} document(s), {len(synced.requirements)} requirement(s), "
        f"{lexical.passage_count()} passage(s)"
    )

    from spec2test.adapters.knowledge.vector_index import _all_passages

    passages = _all_passages(lexical)
    spec = json.loads((HERE / "retrieval-paraphrases.json").read_text(encoding="utf-8"))
    paraphrases = [(q["requirement"], q["question"]) for q in spec["questions"]]
    queries = build_query_set(passages, max_per_family=20, paraphrases=paraphrases)
    counts: dict[str, int] = {}
    for query in queries:
        counts[query.family] = counts.get(query.family, 0) + 1
    print("queries:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))

    scores = [
        evaluate(
            lexical, queries, name="fts5 (bm25)", limit=args.limit, terms_of=terms_of
        )
    ]

    if not args.no_vector:
        vector = VectorKnowledgeIndex(
            lexical,
            OllamaEmbedder(model=args.embed_model),
            cache_path=work / f"vectors-{args.embed_model.replace(':', '-')}.json",
        )
        try:
            embedded = vector.build(progress=lambda line: print(f"  {line}"))
        except EmbeddingUnavailable as exc:
            print(f"\nembeddings unavailable, lexical only: {exc}", file=sys.stderr)
        else:
            print(f"  {embedded} passage(s) embedded in this run")
            scores.append(
                evaluate(
                    vector,
                    queries,
                    name=f"embeddings ({vector.model_name})",
                    limit=args.limit,
                    terms_of=terms_of,
                )
            )
            scores.append(
                evaluate(
                    HybridKnowledgeIndex(lexical, vector),
                    queries,
                    name="hybrid (rank fusion)",
                    limit=args.limit,
                    terms_of=terms_of,
                )
            )

    table = comparison_table(scores)
    print()
    print(table)

    report = [
        f"corpus: {len(paths)} document(s), {lexical.passage_count()} passage(s)",
        f"queries: {len(queries)} ({', '.join(f'{k} {v}' for k, v in sorted(counts.items()))})",
        f"a hit counts when the expected passage is in the first {args.limit}",
        "",
        table,
        "",
        "misses, by retriever:",
    ]
    for score in scores:
        report.append(f"  {score.retriever}")
        for outcome in score.misses():
            report.append(
                f"    [{outcome.query.family}] {outcome.query.query_id}: "
                f"{outcome.query.text[:70]}"
            )
        if not score.misses():
            report.append("    none")
    Path(args.out).write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
