#!/usr/bin/env python3
"""Build a review sheet for complete pairs, for a human to judge.

``context values actually used`` counts one narrow thing: a number lifted
from a retrieved passage. A passage that explained an operating state, a
signal mapping or a fault reaction, without contributing a figure, scores
zero there and may still have carried the run. Declining to take a value
whose applicability is unclear is correct behaviour, not a miss.

So this script does not score anything. It lays one requirement's three runs
side by side - the requirement, the tester's question, every retrieved
passage with its origin, and the drafts each arm produced - and leaves three
questions open under each pair:

    Q1 was the extra source relevant to this requirement?
    Q2 could it have improved the draft?
    Q3 was it used correctly where it was used?

Fill those in by reading. Whoever answers them signs the sheet; the script
writes "unanswered" and will not guess.

    .venv/bin/python docs/benchmarks/pair_review.py ROWS RUNS_DIR --out sheet.md
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

ARMS = ("none", "lexical", "hybrid")


def load_run(runs_dir: Path, run_id: str) -> dict | None:
    """The store wraps the run: {"store_version", "run", "summary"}."""
    for candidate in runs_dir.glob(f"*{run_id}*.json"):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload.get("run", payload)
    return None


def steps_of(case: dict) -> list[str]:
    lines = []
    for label in ("preconditions", "steps", "postconditions"):
        for step in case.get(label) or []:
            action = step.get("action", "")
            lines.append(f"    {label[:4]} {step.get('order', '?')}. {action}")
            for expectation in step.get("expectations") or []:
                lines.append(f"         expect: {expectation}")
            for measurement in step.get("measurements") or []:
                value = measurement.get("value")
                unit = measurement.get("unit") or ""
                shown = f"{value} {unit}".strip() if value is not None else "value open"
                lines.append(
                    f"         measure: {measurement.get('quantity')} = {shown}"
                )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rows")
    parser.add_argument("runs_dir")
    parser.add_argument("--out", required=True)
    parser.add_argument("--pairs", type=int, default=6)
    parser.add_argument("--seed", type=int, default=20261107)
    args = parser.parse_args()

    rows = json.loads(Path(args.rows).read_text(encoding="utf-8"))
    runs_dir = Path(args.runs_dir)
    finished = [r for r in rows if r.get("status") != "raised" and r.get("run_id")]

    by_requirement: dict[str, dict[str, list[dict]]] = {}
    for row in finished:
        by_requirement.setdefault(row["requirement"], {}).setdefault(
            row["arm"], []
        ).append(row)
    complete = sorted(
        rid for rid, arms in by_requirement.items() if set(arms) >= set(ARMS)
    )
    incomplete = sorted(set(by_requirement) - set(complete))

    random.seed(args.seed)
    sample = sorted(random.sample(complete, min(args.pairs, len(complete))))

    spec = json.loads(
        (Path(__file__).resolve().parent / "retrieval-paraphrases.json").read_text(
            encoding="utf-8"
        )
    )
    questions = {q["requirement"]: q["question"] for q in spec["questions"]}

    out: list[str] = [
        "# Fachliche Paarprüfung",
        "",
        "Nicht gescored, sondern gelesen. Unter jedem Paar stehen drei Fragen,",
        "die die Kennzahl `context values actually used` nicht beantworten kann.",
        "",
        f"- vollständige Paare: {len(complete)} von {len(by_requirement)}",
        f"- hier gelesen: {len(sample)} (Stichprobe, seed {args.seed})",
    ]
    if incomplete:
        out.append(f"- ohne Gegenstück in allen Varianten: {', '.join(incomplete)}")
    out += [
        "",
        "> **Wer das ausfüllt, unterschreibt es.** Leere Antworten bleiben leer;",
        "> dieses Skript rät nicht.",
        "",
    ]

    for requirement_id in sample:
        out += [f"## {requirement_id}", ""]
        if requirement_id in questions:
            out += [f"**Frage des Testers:** {questions[requirement_id]}", ""]
        for arm in ARMS:
            entries = by_requirement[requirement_id].get(arm) or []
            if not entries:
                out += [f"### {arm}", "", "_kein Lauf_", ""]
                continue
            row = entries[0]
            out += [
                f"### {arm}"
                + (" (DEGRADIERT -> " + row["effective_retriever"] + ")"
                   if row.get("degraded") else ""),
                "",
                f"`{row['run_id']}` · Entwürfe {row.get('drafts')} · "
                f"Rückfragen {row.get('questions')} · "
                f"unbelegte Werte {row.get('unsupported')} · "
                f"Kontext gesendet {row.get('context_sent')} "
                f"(davon durch die Aufgabe {row.get('context_task_search')}) · "
                f"belegt {row.get('context_supported')}",
                "",
            ]
            run = load_run(runs_dir, row["run_id"])
            if run is None:
                out += ["_Lauf-Datei nicht gefunden_", ""]
                continue
            units = run.get("units") or []
            context = [c for u in units for c in (u.get("context") or [])]
            if context:
                out.append("**Herangezogene Quellen**")
                out.append("")
                for item in context:
                    out.append(
                        f"- `{item.get('external_id') or item.get('requirement_id')}`"
                        f" · {item.get('kind')} · {item.get('reason')}"
                        f" · {item.get('document_filename') or item.get('document_id')}"
                        f" · *{item.get('heading') or '-'}*"
                    )
                    text = " ".join((item.get("text") or "").split())
                    out.append(f"  > {text[:400]}")
                out.append("")
            else:
                out += ["**Herangezogene Quellen:** keine", ""]
            cases = [c for u in units for c in (u.get("test_cases") or [])]
            out.append("**Entwürfe**")
            out.append("")
            for case in cases:
                out.append(f"- **{case.get('title')}**")
                out.append(f"  - Ziel: {case.get('objective')}")
                out += [f"  {line}" for line in steps_of(case)]
                for question in case.get("open_questions") or []:
                    out.append(f"    ? {question.get('text')}")
            out.append("")
        out += [
            "| Frage | Antwort |",
            "| - | - |",
            "| Q1 war die zusätzliche Quelle für diese Anforderung relevant? | unbeantwortet |",
            "| Q2 hätte sie den Entwurf verbessern können? | unbeantwortet |",
            "| Q3 wurde sie, wo benutzt, korrekt benutzt? | unbeantwortet |",
            "",
            "Gelesen von: _offen_",
            "",
            "---",
            "",
        ]

    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"{len(sample)} pair(s) written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
