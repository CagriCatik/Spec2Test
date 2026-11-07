#!/usr/bin/env python3
"""Three arms over one baseline: no retrieval, lexical, hybrid.

20 requirements x 3 arms x 2 repetitions = 120 runs. Everything except the
retriever is identical: same code, same prompt version, same model, same
limits, same context budget, same task description per requirement.

What is recorded, and why:

* **requested and effective retriever.** A hybrid arm that fell back to
  lexical because the embedding model was unreachable must not be counted as
  a hybrid run. The effective retriever is read from the object that was
  actually handed to the generation, and the arm is marked ``degraded`` when
  the two differ. Degraded runs are reported separately, never merged in.
* **context found, sent and supported, kept apart.** Found: hits the
  retriever returned. Sent: items that survived the budget and reached the
  prompt. Supported: items a draft actually drew a value from, counted as
  C_VALUE_FROM_CONTEXT. A retriever that finds a lot and supports nothing
  has not helped.
* **failures, repairs, model calls.** In full, per run.
* **quality, not just fewer questions.** Fewer open questions is not a win on
  its own: the last comparison showed retrieval suppressing the questions the
  model should have asked. Unsupported values, expectations without a
  measurable criterion and the number of findings a reviewer would have to
  work through are recorded alongside.

Usage:

    .venv/bin/python docs/benchmarks/compare_retrieval_arms.py OUTDIR
    .venv/bin/python docs/benchmarks/compare_retrieval_arms.py OUTDIR --arms lexical
    .venv/bin/python docs/benchmarks/compare_retrieval_arms.py OUTDIR --smoke

``rows.json`` is rewritten after every run, so an interrupted measurement
keeps what it already has.
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
from spec2test.adapters.knowledge import (  # noqa: E402
    HybridKnowledgeIndex,
    OllamaEmbedder,
    SqliteKnowledgeIndex,
    VectorKnowledgeIndex,
)
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
ARMS = ("none", "lexical", "hybrid")

MODEL = "gpt-oss:120b-cloud"
EMBED_MODEL = "nomic-embed-text"
# identical in every arm
LIMITS = dict(
    use_reference_context=True,
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


def retriever_for(arm: str, lexical, vector):
    """Returns (object, effective name). None means: no retrieval at all."""
    if arm == "none":
        return None, "none"
    if arm == "lexical":
        return lexical, "lexical"
    if vector is None:
        return lexical, "lexical"  # degraded; the caller notices and says so
    return HybridKnowledgeIndex(lexical, vector), "hybrid"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("outdir")
    parser.add_argument("--arms", nargs="*", default=list(ARMS), choices=ARMS)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--smoke", action="store_true", help="2 requirements, 1 rep")
    args = parser.parse_args()

    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)

    lexical = SqliteKnowledgeIndex(out / "knowledge.db")
    sync = sync_index(
        lexical, collect_documents(ROOT / "requirements"), DEFAULT_IMPORTERS
    )
    requirements = list(sync.requirements)
    documents = dict(sync.documents)
    print(f"index: {sync.summary()}", flush=True)

    # the 20 requirements that have an authored prose question. The question
    # becomes this run's task description, which is what makes the hybrid arm
    # testable: a requirement's own identifiers are what BM25 is already good at.
    spec = json.loads((HERE / "retrieval-paraphrases.json").read_text(encoding="utf-8"))
    by_external = {r.external_id: r for r in requirements if r.external_id}
    chosen: list[tuple[object, str]] = []
    for entry in spec["questions"]:
        requirement = by_external.get(entry["requirement"])
        if requirement is None:
            print(f"  skipped: {entry['requirement']} is not in the corpus")
            continue
        chosen.append((requirement, entry["question"]))
    if args.smoke:
        chosen = chosen[:2]
        args.repetitions = 1
    print(
        f"{len(chosen)} requirement(s), {len(args.arms)} arm(s), "
        f"{args.repetitions} repetition(s) = "
        f"{len(chosen) * len(args.arms) * args.repetitions} run(s)",
        flush=True,
    )

    vector = None
    if "hybrid" in args.arms:
        from spec2test.adapters.knowledge import EmbeddingUnavailable

        candidate = VectorKnowledgeIndex(
            lexical,
            OllamaEmbedder(model=EMBED_MODEL),
            cache_path=out / "vectors.json",
        )
        try:
            embedded = candidate.build(progress=lambda line: print(f"  {line}"))
        except EmbeddingUnavailable as exc:
            print(f"  embeddings unavailable: {exc}", flush=True)
        else:
            vector = candidate
            print(f"  embeddings ready, {embedded} embedded this run", flush=True)

    store = JsonRunStore(out / "runs")
    rows: list[dict] = []
    rows_path = out / "rows.json"

    for arm in args.arms:
        for repetition in range(1, args.repetitions + 1):
            for requirement, question in chosen:
                knowledge, effective = retriever_for(arm, lexical, vector)
                degraded = effective != arm and arm != "none"
                task = (
                    "Focus this run on the following question a tester asked: "
                    f"{question}"
                )
                settings = GenerationSettings(
                    **LIMITS,
                    use_retrieval=arm != "none",
                    retrieval="hybrid" if arm == "hybrid" else "lexical",
                    task_description=task,
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
                        knowledge=knowledge,
                        log=lambda _m: None,
                    )
                except Exception as exc:  # noqa: BLE001 - a failed run is data
                    print(
                        f"  {arm} rep{repetition} {requirement.external_id}: "
                        f"RAISED {type(exc).__name__}: {exc}",
                        flush=True,
                    )
                    rows.append(
                        dict(
                            arm=arm,
                            effective_retriever=effective,
                            degraded=degraded,
                            repetition=repetition,
                            requirement=requirement.external_id,
                            status="raised",
                            error=f"{type(exc).__name__}: {exc}",
                            seconds=round(time.perf_counter() - started, 1),
                        )
                    )
                    rows_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
                    continue
                store.save_run(run)

                codes = [i.code for u in run.units for i in u.issues]
                context = [item for u in run.units for item in u.context]
                by_reason: dict[str, int] = {}
                for item in context:
                    by_reason[item.reason.value] = by_reason.get(item.reason.value, 0) + 1
                omitted = codes.count("C_CONTEXT_OMITTED")
                rows.append(
                    dict(
                        arm=arm,
                        effective_retriever=effective,
                        degraded=degraded,
                        repetition=repetition,
                        requirement=requirement.external_id,
                        prompt_version=run.prompt_version,
                        model=run.model,
                        status=run.status.value,
                        error=None,
                        # what came out
                        drafts=len(run.test_cases),
                        questions=len(run.open_questions),
                        # context: found, sent, supported
                        context_sent=len(context),
                        context_by_reason=by_reason,
                        context_task_search=by_reason.get(
                            SelectionReason.TASK_SEARCH.value, 0
                        ),
                        context_omitted_findings=omitted,
                        context_supported=codes.count("C_VALUE_FROM_CONTEXT"),
                        # quality
                        unsupported=codes.count("C_UNSUPPORTED_VALUE"),
                        no_criterion=codes.count("C_NO_MEASURABLE_CRITERION"),
                        conflicts=codes.count("C_POSSIBLE_VALUE_CONFLICT"),
                        findings=len(codes),
                        findings_for_review=len(
                            [c for c in codes if c != "C_CONTEXT_OMITTED"]
                        ),
                        # effort
                        model_calls=sum(len(u.attempts) for u in run.units),
                        repairs=sum(
                            1 for u in run.units for a in u.attempts if a.is_repair
                        ),
                        units_not_started=sum(
                            1 for u in run.units if u.status.value == "not_started"
                        ),
                        seconds=round(time.perf_counter() - started, 1),
                        run_id=run.run_id,
                    )
                )
                rows_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
                last = rows[-1]
                print(
                    f"  {arm:<7} rep{repetition} {requirement.external_id}: "
                    f"{last['status']:<9} drafts={last['drafts']} "
                    f"q={last['questions']} unsup={last['unsupported']} "
                    f"ctx={last['context_sent']}(task {last['context_task_search']}) "
                    f"sup={last['context_supported']} rep={last['repairs']} "
                    f"{last['seconds']}s",
                    flush=True,
                )

    print(f"DONE {len(rows)} run(s), prompt {PROMPT_VERSION}", flush=True)
    print(f"rows: {rows_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
