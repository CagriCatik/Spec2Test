#!/usr/bin/env python3
"""Four variants: old/new segmentation x old/new budget allocation.

No generation model. The point is to separate two changes that were made
together, so neither gets credit for the other's effect.

| variant            | segmentation | budget allocation |
|--------------------|--------------|-------------------|
| baseline           | old          | old               |
| segmentation only  | new          | old               |
| budget only        | old          | new               |
| combination        | new          | new               |

The old half runs from a real worktree of the commit it comes from, not from
today's code with a flag turned off - switching a feature off does not
reconstruct the behaviour of a revision that never had it.

**One limitation is unavoidable and is reported with the numbers.** The
table header that the new segmentation attaches to a group of rows is a
field the old selection code does not know. ``segmentation only`` therefore
shows the new row grouping *without* its column names. That is not a flaw
of the measurement; it is what happens when only one of the two changes is
adopted, and it is worth seeing.

All metrics are computed here, from the JSON the workers emit, so the
measurement is identical for every variant.

    .venv/bin/python docs/benchmarks/offline_variants.py --old-root PATH
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

IDENT = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b")
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
TABLE_DELIM = re.compile(r"^\s*\|[\s:|-]+\|\s*$")

VARIANTS = (
    # name, segmentation root key, selection root key
    ("baseline", "old", "old"),
    ("segmentation only", "new", "old"),
    ("budget only", "old", "new"),
    ("combination", "new", "new"),
)
QUESTION_SETS = ("tuned", "holdout")


def run_worker(root: Path, python: str, *args: str) -> object:
    done = subprocess.run(
        [python, str(HERE / "variant_worker.py"), *args, "--root", str(root)],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise SystemExit(f"worker failed in {root}:\n{done.stderr[-3000:]}")
    return json.loads(done.stdout)


def connected_ids(requirement_text: str, references, own_documents, passages) -> set:
    identifiers = set(IDENT.findall(requirement_text))
    linked = set()
    for passage in passages:
        if passage["external_id"] and passage["external_id"] in references:
            linked.add(passage["passage_id"])
        elif identifiers and identifiers & set(IDENT.findall(passage["text"])):
            linked.add(passage["passage_id"])
        elif passage["document_id"] in own_documents:
            linked.add(passage["passage_id"])
    return linked


def table_rows_without_columns(items) -> int:
    """Sent passages that are bare table rows and say nothing about columns."""
    orphans = 0
    for item in items:
        lines = [l for l in item["text"].splitlines() if l.strip()]
        rows = [l for l in lines if TABLE_ROW.match(l)]
        if not rows or len(rows) < len(lines) / 2:
            continue
        has_header = any(TABLE_DELIM.match(l) for l in lines) or bool(item.get("prefix"))
        if not has_header:
            orphans += 1
    return orphans


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-root", required=True)
    parser.add_argument("--work", default=str(HERE / ".variants"))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--char-budget", type=int, default=6_000)
    parser.add_argument("--retrievers", nargs="*", default=["lexical", "hybrid"])
    parser.add_argument("--out", default=str(HERE / "offline-variants-result.txt"))
    args = parser.parse_args()

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    roots = {"old": Path(args.old_root), "new": ROOT}
    corpus = str(ROOT / "requirements")
    questions = {
        "tuned": HERE / "retrieval-paraphrases.json",
        "holdout": HERE / "retrieval-paraphrases-holdout.json",
    }

    # --- one index per segmentation, each built by its own revision --------
    indexes: dict[str, dict] = {}
    for key, root in roots.items():
        db = work / f"index-{key}.db"
        if db.exists():
            db.unlink()
        print(f"indexing with the {key} segmentation ({root.name})...", flush=True)
        indexes[key] = run_worker(
            root, args.python, "index", "--db", str(db), "--corpus", corpus
        )
        indexes[key]["db"] = str(db)
        print(f"  {indexes[key]['summary']}", flush=True)

    # --- requirement facts, from today's code, identical for all variants --
    from spec2test.adapters.documents import DEFAULT_IMPORTERS
    from spec2test.application.indexing import collect_documents
    from spec2test.application.ingest import import_documents

    results = import_documents(collect_documents(ROOT / "requirements"), DEFAULT_IMPORTERS)
    requirements = {
        r.external_id: r
        for result in results
        for r in result.requirements
        if r.external_id
    }

    lines: list[str] = [
        "four variants, no generation model",
        f"corpus: {len(results)} documents, context budget {args.char_budget}",
        "",
        "passages per segmentation:",
    ]
    for key in roots:
        rows = indexes[key]["passages"]
        with_prefix = sum(1 for r in rows if r["prefix"])
        sizes = sorted(r["chars"] for r in rows)
        lines.append(
            f"  {key:<4} {len(rows):>4} passages, {with_prefix:>3} with a column "
            f"header, median {sizes[len(sizes) // 2]:>5} chars, "
            f"largest {sizes[-1]:>5}"
        )
    lines.append("")

    report: dict = {}
    for retriever in args.retrievers:
        for set_name, path in questions.items():
            spec = json.loads(path.read_text(encoding="utf-8"))
            for variant, seg_key, sel_key in VARIANTS:
                cache = work / f"vec-{seg_key}.json"
                selections = run_worker(
                    roots[sel_key],
                    args.python,
                    "select",
                    "--db",
                    indexes[seg_key]["db"],
                    "--corpus",
                    corpus,
                    "--questions",
                    str(path),
                    "--retriever",
                    retriever,
                    "--embed-cache",
                    str(cache),
                    "--char-budget",
                    str(args.char_budget),
                )
                passages = indexes[seg_key]["passages"]
                sent_connected = sent_total = 0
                chars_connected = chars_unconnected = 0
                starved = orphan_tables = 0
                for entry in selections:
                    requirement = requirements[entry["requirement"]]
                    own_documents = {s.document_id for s in requirement.spans}
                    helpful = connected_ids(
                        requirement.text,
                        set(requirement.references or ()),
                        own_documents,
                        passages,
                    )
                    items = entry["items"]
                    hit = False
                    for item in items:
                        if item["passage_id"] in helpful:
                            chars_connected += item["size"]
                            hit = True
                        else:
                            chars_unconnected += item["size"]
                    sent_total += 1
                    sent_connected += int(hit)
                    if not hit and entry["omitted"]:
                        starved += 1
                    orphan_tables += table_rows_without_columns(items)
                report[(retriever, set_name, variant)] = {
                    "questions": sent_total,
                    "sent": sent_connected / sent_total if sent_total else 0.0,
                    "connected_chars": chars_connected,
                    "unconnected_chars": chars_unconnected,
                    "starved": starved,
                    "orphan_tables": orphan_tables,
                    "items": sum(len(e["items"]) for e in selections) / max(sent_total, 1),
                }
                print(
                    f"  {retriever:<8} {set_name:<8} {variant:<18} "
                    f"sent={report[(retriever, set_name, variant)]['sent']:.2f}",
                    flush=True,
                )

    for retriever in args.retrievers:
        for set_name in QUESTION_SETS:
            lines.append("=" * 78)
            lines.append(f"{retriever.upper()}  /  {set_name.upper()}")
            lines.append("=" * 78)
            lines.append(
                f"  {'variant':<20}{'n':>4}{'sent':>8}{'starved':>9}{'items':>8}"
                f"{'connected':>12}{'share':>8}{'bare table rows':>18}"
            )
            for variant, _s, _b in VARIANTS:
                row = report.get((retriever, set_name, variant))
                if row is None:
                    continue
                total = row["connected_chars"] + row["unconnected_chars"]
                share = f"{100 * row['connected_chars'] / total:.0f}%" if total else "-"
                lines.append(
                    f"  {variant:<20}{row['questions']:>4}{row['sent']:>8.2f}"
                    f"{row['starved']:>9}{row['items']:>8.1f}"
                    f"{row['connected_chars']:>12}{share:>8}"
                    f"{row['orphan_tables']:>18}"
                )
            lines.append("")

    lines += [
        "sent    : at least one passage with a demonstrable link to the",
        "          requirement reached the prompt. A PROXY, not relevance:",
        "          a helpful passage can share no identifier at all.",
        "starved : nothing connected was sent although something was omitted.",
        "bare table rows: sent passages that are table rows with neither a",
        "          header row nor a column header attached - unreadable.",
        "",
        "'segmentation only' cannot carry a column header: that field does not",
        "exist in the old selection code. The bare-table-rows column shows what",
        "adopting one change without the other costs.",
    ]
    text = "\n".join(lines)
    print()
    print(text)
    Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
