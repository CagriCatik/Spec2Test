#!/usr/bin/env python3
"""16 runs: does holding budget back for the task text help the drafts?

The only difference between the arms is ``task_budget_share``. Segmentation,
table headers and collect-then-select are identical in both. Four
requirements whose context packages were already read by hand - two where
the new selection read better, two where it read worse - two repetitions
each, variants alternating so a drift in the service cannot land on one arm.

    A  task_budget_share = 0.00   4 requirements x 2 repetitions = 8 runs
    B  task_budget_share = 0.25   4 requirements x 2 repetitions = 8 runs

Diagnostic on known cases. Not an independent final test.

    .venv/bin/python docs/benchmarks/compare_task_reservation.py OUTDIR
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from spec2test.adapters.documents import DEFAULT_IMPORTERS  # noqa: E402
from spec2test.adapters.knowledge import SqliteKnowledgeIndex  # noqa: E402
from spec2test.adapters.ollama.client import (  # noqa: E402
    OllamaChatClient,
    OllamaSettings,
)
from spec2test.adapters.persistence import JsonRunStore  # noqa: E402
from spec2test.application.generate import (  # noqa: E402
    GenerationSettings,
    generate_drafts,
)
from spec2test.application.indexing import collect_documents, sync_index  # noqa: E402
from spec2test.application.prompts import PROMPT_VERSION  # noqa: E402
from spec2test.domain.models import SelectionReason  # noqa: E402

HERE = Path(__file__).resolve().parent
CASES = ("BMS-REQ-SC-0901", "BMS-REQ-DSC-1504", "BMS-REQ-NM-1901", "BMS-REQ-HVC-0305")
MODEL = "gpt-oss:120b-cloud"
ARMS = (("A", 0.00), ("B", 0.25))

#: identical in both arms
LIMITS = dict(
    use_reference_context=True,
    use_retrieval=True,
    retrieval="lexical",
    max_cases_per_unit=2,
    max_repair_attempts=1,
    job_timeout_s=420,
    context_char_budget=6_000,
    max_context_items=5,
    max_search_items=3,
    max_task_items=2,
    group_size=1,
)


def build_model():
    return OllamaChatClient(
        OllamaSettings(
            model=MODEL,
            mode="ollama_local",
            host="http://localhost:11434",
            read_timeout_s=300,
            max_retries=1,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("outdir")
    args = parser.parse_args()
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)

    index = SqliteKnowledgeIndex(out / "knowledge.db")
    synced = sync_index(
        index, collect_documents(ROOT / "requirements"), DEFAULT_IMPORTERS
    )
    requirements = list(synced.requirements)
    documents = dict(synced.documents)
    by_external = {r.external_id: r for r in requirements if r.external_id}
    print(f"index: {synced.summary()}", flush=True)

    spec = json.loads(
        (HERE / "retrieval-paraphrases-holdout.json").read_text(encoding="utf-8")
    )
    questions = {q["requirement"]: q["question"] for q in spec["questions"]}
    missing = [c for c in CASES if c not in by_external or c not in questions]
    if missing:
        print(f"not available: {missing}", file=sys.stderr)
        return 2

    store = JsonRunStore(out / "runs")
    rows: list[dict] = []
    rows_path = out / "rows.json"
    plan = [
        # alternate the arms so a drift in the service cannot land on one of them
        (case, repetition, arm, share)
        for repetition in (1, 2)
        for case in CASES
        for arm, share in (ARMS if repetition == 1 else tuple(reversed(ARMS)))
    ]

    for case, repetition, arm, share in plan:
        requirement = by_external[case]
        task = (
            "Focus this run on the following question a tester asked: "
            f"{questions[case]}"
        )
        settings = GenerationSettings(
            **LIMITS, task_description=task, task_budget_share=share
        )
        started = time.perf_counter()
        error = None
        try:
            run = generate_drafts(
                [requirement],
                build_model(),
                settings=settings,
                documents=documents,
                catalogue=requirements,
                knowledge=index,
                log=lambda _m: None,
            )
        except Exception as exc:  # noqa: BLE001 - a failed run is data
            error = f"{type(exc).__name__}: {exc}"
            rows.append(
                dict(
                    arm=arm, share=share, requirement=case, repetition=repetition,
                    status="raised", error=error,
                    seconds=round(time.perf_counter() - started, 1),
                )
            )
            rows_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
            print(f"  {arm} rep{repetition} {case}: RAISED {error}", flush=True)
            continue
        store.save_run(run)

        codes = [i.code for u in run.units for i in u.issues]
        context = [c for u in run.units for c in u.context]
        task_items = [c for c in context if c.reason is SelectionReason.TASK_SEARCH]
        # the prefix is part of what is sent and must be inside the budget
        sent_chars = sum(c.size for c in context)
        prefix_chars = sum(len(c.prefix) + 1 for c in context if c.prefix)
        rows.append(
            dict(
                arm=arm, share=share, requirement=case, repetition=repetition,
                prompt_version=run.prompt_version, model=run.model,
                status=run.status.value, error=None,
                drafts=len(run.test_cases),
                questions=len(run.open_questions),
                context_sent=len(context),
                context_task=len(task_items),
                context_chars=sent_chars,
                prefix_chars=prefix_chars,
                over_budget=max(0, sent_chars - LIMITS["context_char_budget"]),
                context_supported=codes.count("C_VALUE_FROM_CONTEXT"),
                unsupported=codes.count("C_UNSUPPORTED_VALUE"),
                no_criterion=codes.count("C_NO_MEASURABLE_CRITERION"),
                conflicts=codes.count("C_POSSIBLE_VALUE_CONFLICT"),
                findings_for_review=len(
                    [c for c in codes if c != "C_CONTEXT_OMITTED"]
                ),
                model_calls=sum(len(u.attempts) for u in run.units),
                repairs=sum(1 for u in run.units for a in u.attempts if a.is_repair),
                headings=[c.heading for c in context],
                task_headings=[c.heading for c in task_items],
                seconds=round(time.perf_counter() - started, 1),
                run_id=run.run_id,
            )
        )
        rows_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        last = rows[-1]
        print(
            f"  {arm} rep{repetition} {case}: {last['status']:<9} "
            f"drafts={last['drafts']} q={last['questions']} "
            f"unsup={last['unsupported']} ctx={last['context_sent']}"
            f"(task {last['context_task']}, {last['context_chars']}ch, "
            f"prefix {last['prefix_chars']}) sup={last['context_supported']} "
            f"rep={last['repairs']} {last['seconds']}s",
            flush=True,
        )

    over = [r for r in rows if r.get("over_budget")]
    print(f"DONE {len(rows)} runs, prompt {PROMPT_VERSION}", flush=True)
    print(f"runs over the character budget: {len(over)}", flush=True)
    print(f"rows: {rows_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
