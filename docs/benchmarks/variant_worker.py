#!/usr/bin/env python3
"""Run one code revision's segmentation or selection and dump the result.

This runs *inside* a checkout - the current one or a worktree of an older
commit - so that a variant uses that revision's real code instead of today's
code with a flag turned off. It computes no metric: it only reports what the
index holds and what build_context chose. The harness that compares the
variants is one program, so the measurement itself never changes.

    python variant_worker.py index  --root REPO --db PATH
    python variant_worker.py select --root REPO --db PATH --questions FILE \\
        --retriever lexical|hybrid --char-budget N
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["index", "select"])
    parser.add_argument("--root", required=True, help="the checkout to import from")
    parser.add_argument("--db", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--questions")
    parser.add_argument("--retriever", default="lexical")
    parser.add_argument("--embed-cache")
    parser.add_argument("--char-budget", type=int, default=6_000)
    parser.add_argument("--max-search-items", type=int, default=3)
    parser.add_argument("--max-task-items", type=int, default=2)
    parser.add_argument("--max-items", type=int, default=5)
    args = parser.parse_args()

    sys.path.insert(0, str(Path(args.root) / "src"))
    from spec2test.adapters.documents import DEFAULT_IMPORTERS
    from spec2test.adapters.knowledge import SqliteKnowledgeIndex
    from spec2test.application.indexing import collect_documents, sync_index

    if args.mode == "index":
        index = SqliteKnowledgeIndex(args.db)
        report = sync_index(index, collect_documents(Path(args.corpus)), DEFAULT_IMPORTERS)
        rows = []
        import sqlite3

        connection = sqlite3.connect(args.db)
        connection.row_factory = sqlite3.Row
        columns = {
            r["name"] for r in connection.execute("PRAGMA table_info(passages)")
        }
        for row in connection.execute("SELECT * FROM passages ORDER BY passage_id"):
            rows.append(
                {
                    "passage_id": row["passage_id"],
                    "document_id": row["document_id"],
                    "kind": row["kind"],
                    "heading": row["heading"],
                    "external_id": row["external_id"],
                    "chars": len(row["text"]),
                    "prefix": row["prefix"] if "prefix" in columns else None,
                    "text": row["text"],
                }
            )
        print(json.dumps({"summary": report.summary(), "passages": rows}))
        return 0

    # --- selection -------------------------------------------------------
    from spec2test.application.context import build_context
    from spec2test.application.ingest import reference_report

    # the index is read, never written: re-syncing would let this revision's
    # segmentation overwrite the one the variant is supposed to use
    index = SqliteKnowledgeIndex(args.db)
    from spec2test.application.ingest import import_documents

    results = import_documents(collect_documents(Path(args.corpus)), DEFAULT_IMPORTERS)
    requirements = [r for result in results for r in result.requirements]
    documents = {result.document.document_id: result.document for result in results}
    by_external = {r.external_id: r for r in requirements if r.external_id}

    knowledge = index
    if args.retriever == "hybrid":
        from spec2test.adapters.knowledge import OllamaEmbedder, VectorKnowledgeIndex
        from spec2test.adapters.knowledge import HybridKnowledgeIndex

        vector = VectorKnowledgeIndex(
            index, OllamaEmbedder(), cache_path=args.embed_cache
        )
        vector.build()
        knowledge = HybridKnowledgeIndex(index, vector)

    spec = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    out = []
    for entry in spec["questions"]:
        requirement = by_external.get(entry["requirement"])
        if requirement is None:
            continue
        selection = build_context(
            [requirement],
            reference_report(requirements),
            documents=documents,
            knowledge=knowledge,
            char_budget=args.char_budget,
            max_items=args.max_items,
            max_search_items=args.max_search_items,
            task_description=entry["question"],
            max_task_items=args.max_task_items,
        )
        out.append(
            {
                "requirement": entry["requirement"],
                "question": entry["question"],
                "items": [
                    {
                        "passage_id": item.requirement_id,
                        "external_id": item.external_id,
                        "kind": item.kind.value,
                        "reason": item.reason.value,
                        "heading": item.heading,
                        "document_id": item.document_id,
                        "size": item.size,
                        "prefix": getattr(item, "prefix", None),
                        "text": item.text,
                    }
                    for item in selection.items
                ],
                "omitted": sum(
                    1 for i in selection.issues if i.code == "C_CONTEXT_OMITTED"
                ),
            }
        )
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
