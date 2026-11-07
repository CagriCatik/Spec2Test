#!/usr/bin/env python3
"""A limited real function test of the grounding path. Ten model calls, hard.

Not a benchmark. The question is whether the parts work together on real
output: does the model produce grounds, does the deterministic check judge
them correctly, and does the review agent catch a technical mismatch that
the check cannot see.

Budget: the generation of three drafts (one call each, at most one repair
each) plus one review call per case. The script counts every call and stops
rather than exceeding ten. Nothing is retried automatically beyond the one
repair the pipeline already allows.

Three requirements, chosen for three different situations:

  SC-0901   a limit that the requirement states outright
  THM-0404  a case where the answer needs a supporting passage
  BAL-0503  a case where a needed value is genuinely absent

    .venv/bin/python docs/benchmarks/function_test_grounds.py OUTDIR
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from spec2test.adapters.documents import DEFAULT_IMPORTERS  # noqa: E402
from spec2test.adapters.knowledge import SqliteKnowledgeIndex  # noqa: E402
from spec2test.adapters.ollama.client import (  # noqa: E402
    OllamaChatClient,
    OllamaSettings,
)
from spec2test.application.generate import (  # noqa: E402
    GenerationSettings,
    generate_drafts,
)
from spec2test.application.ground_review import review_grounds  # noqa: E402
from spec2test.application.indexing import collect_documents, sync_index  # noqa: E402
from spec2test.application.prompts import PROMPT_VERSION  # noqa: E402
from spec2test.domain.grounding import check_grounds  # noqa: E402
from spec2test.domain.models import GroundKind  # noqa: E402

MODEL = "gpt-oss:120b-cloud"
CALL_BUDGET = 10

CASES = [
    ("BMS-REQ-SC-0901", "a limit the requirement states outright",
     "Check the hardware trip threshold and the response time."),
    ("BMS-REQ-THM-0404", "needs a supporting passage",
     "Check the evacuation warning: how the occupants are told and how long "
     "they have."),
    ("BMS-REQ-BAL-0503", "a needed value is genuinely absent",
     "Check the balancing PWM pattern including the die temperature limit "
     "that stops it."),
]


class CountingModel:
    """Wraps the client and refuses to go past the budget."""

    def __init__(self, inner, budget: int) -> None:
        self._inner = inner
        self.budget = budget
        self.calls = 0

    @property
    def descriptor(self):
        return self._inner.descriptor

    def chat(self, messages, *, deadline=None):
        if self.calls >= self.budget:
            raise RuntimeError(
                f"call budget of {self.budget} exhausted; stopping rather "
                "than spending more"
            )
        self.calls += 1
        return self._inner.chat(messages, deadline=deadline)


def describe(ground) -> dict:
    out = {
        "claim": ground.claim,
        "target": ground.target,
        "kind": ground.kind.value,
    }
    if ground.kind is GroundKind.CALCULATION:
        out["inputs"] = [
            {"value": i.value, "source": i.source, "quote": (i.quote or "")[:120]}
            for i in ground.inputs
        ]
        out["formula"] = ground.formula
    elif ground.kind is GroundKind.UNRESOLVED:
        out["question"] = ground.question
    else:
        out["source"] = ground.source
        out["quote"] = (ground.quote or "")[:200]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("outdir")
    parser.add_argument("--budget", type=int, default=CALL_BUDGET)
    parser.add_argument(
        "--phase", choices=["all", "generate", "synthetic"], default="all",
        help="run one phase, so a crash does not spend the whole budget twice",
    )
    parser.add_argument("--only", nargs="*", default=None, help="requirement ids")
    parser.add_argument(
        "--no-repair", action="store_true",
        help="one call per draft: no repair attempt, so a single call suffices",
    )
    parser.add_argument(
        "--fake", action="store_true",
        help="dry run against the offline stand-in: costs no model call and "
        "proves the script itself works before real calls are spent",
    )
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
    print(f"index: {synced.summary()}  prompt {PROMPT_VERSION}", flush=True)

    if args.fake:
        from spec2test.adapters.fake.model import RuleBasedFakeModel

        inner = RuleBasedFakeModel()
    else:
        inner = OllamaChatClient(
            OllamaSettings(
                model=MODEL, mode="ollama_local", host="http://localhost:11434",
                read_timeout_s=300, max_retries=1,
            )
        )
    model = CountingModel(inner, args.budget)
    record: list[dict] = []

    # --- 1. generate three drafts --------------------------------------
    for external_id, situation, task in CASES:
        if args.phase == "synthetic":
            break
        if args.only and external_id not in args.only:
            continue
        requirement = by_external.get(external_id)
        if requirement is None:
            print(f"  {external_id} not in the corpus", file=sys.stderr)
            continue
        needed = 1 if args.no_repair else 2
        if model.calls + needed > args.budget:
            print(f"  stopping before {external_id}: budget", flush=True)
            break
        started = time.perf_counter()
        try:
            run = generate_drafts(
                [requirement],
                model,
                settings=GenerationSettings(
                    group_size=1, use_retrieval=True, max_search_items=3,
                    max_cases_per_unit=1,
                    max_repair_attempts=0 if args.no_repair else 1,
                    job_timeout_s=300, task_description=task,
                ),
                documents=documents, catalogue=requirements,
                knowledge=index, log=lambda _m: None,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  {external_id}: RAISED {type(exc).__name__}: {exc}", flush=True)
            record.append({"requirement": external_id, "error": str(exc)})
            continue

        unit = run.units[0]
        entry = {
            "requirement": external_id,
            "situation": situation,
            "task": task,
            "status": run.status.value,
            "unit_status": unit.status.value,
            "model_calls_so_far": model.calls,
            "repairs": sum(1 for a in unit.attempts if a.is_repair),
            "seconds": round(time.perf_counter() - started, 1),
            "unit_issues": [
                {"severity": i.severity.value, "code": i.code,
                 "path": i.path, "message": i.message[:240]}
                for i in unit.issues
            ],
            "attempts": [
                {"is_repair": a.is_repair,
                 "error": a.error,
                 "issues": [
                     {"code": i.code, "path": i.path, "message": i.message[:160]}
                     for i in a.issues
                 ],
                 "raw_response": a.raw_response}
                for a in unit.attempts
            ],
            "context": [
                {"kind": c.kind.value, "reason": c.reason.value, "heading": c.heading}
                for c in unit.context
            ],
            "cases": [],
        }
        for case in unit.test_cases:
            grounds = [
                describe(g)
                for step in (*case.preconditions, *case.steps, *case.postconditions)
                for g in step.grounds
            ]
            deterministic = [
                {"code": i.code, "path": i.path, "message": i.message[:220]}
                for i in check_grounds(case, [requirement], context=list(unit.context))
            ]
            entry.setdefault("_live", []).append(
                (case, requirement, list(unit.context), {})
            )
            entry["cases"].append(
                entry["_live"][-1][3]
            )
            entry["cases"][-1].update(
                {
                    "case_id": case.case_id,
                    "title": case.title,
                    "grounding_rules": case.grounding_rules,
                    "steps": [
                        {
                            "order": s.order,
                            "action": s.action,
                            "expectations": list(s.expectations),
                            "measurements": [
                                f"{m.quantity} = {m.value} {m.unit or ''}".strip()
                                for m in s.measurements
                            ],
                        }
                        for s in case.steps
                    ],
                    "grounds": grounds,
                    "deterministic": deterministic,
                    "open_questions": [q.question for q in case.open_questions],
                }
            )
        record.append(entry)
        total_grounds = sum(len(c["grounds"]) for c in entry["cases"])
        total_findings = sum(len(c["deterministic"]) for c in entry["cases"])
        print(
            f"  {external_id}: {unit.status.value}, {len(unit.test_cases)} case(s), "
            f"{total_grounds} ground(s), {total_findings} deterministic "
            f"finding(s), calls={model.calls}",
            flush=True,
        )
        if not unit.test_cases:
            for issue in unit.issues[:4]:
                print(f"      {issue.severity.value} {issue.code}: "
                      f"{issue.message[:150]}", flush=True)

    # --- 2. the review agent over what was generated --------------------
    print(f"\nreview agent (calls so far {model.calls}/{args.budget})", flush=True)
    for entry in record:
        for case, requirement, context, case_record in entry.get("_live", []):
            if model.calls >= args.budget:
                case_record["agent"] = {"skipped": "call budget exhausted"}
                print(f"  {case_record['case_id']}: skipped, budget", flush=True)
                continue
            review = review_grounds(case, [requirement], model, context=context)
            case_record["agent"] = {
                "ok": review.ok,
                "complete": review.complete,
                "error": review.error,
                "summary": review.summary(),
                "verdicts": [asdict(v) for v in review.verdicts],
                "issues": [
                    {"code": i.code, "path": i.path, "message": i.message[:240]}
                    for i in review.issues
                ],
            }
            print(
                f"  {case_record['case_id']}: {review.summary()} "
                f"(calls {model.calls})",
                flush=True,
            )
        entry.pop("_live", None)

    # --- 3. the three synthetic cases through the real agent ------------
    synthetic = [] if args.phase == "generate" else _synthetic_cases()
    if synthetic:
        print(f"\nsynthetic cases (calls so far {model.calls}/{args.budget})", flush=True)
    results = []
    for name, case, requirement in synthetic:
        deterministic = [i.code for i in check_grounds(case, [requirement])]
        item = {
            "name": name,
            "deterministic": deterministic,
            "claim": case.steps[0].grounds[0].claim,
            "quote": case.steps[0].grounds[0].quote,
        }
        if model.calls >= args.budget:
            item["agent"] = {"skipped": "call budget exhausted"}
            print(f"  {name}: skipped, budget", flush=True)
        else:
            review = review_grounds(case, [requirement], model)
            item["agent"] = {
                "ok": review.ok,
                "complete": review.complete,
                "summary": review.summary(),
                "verdicts": [asdict(v) for v in review.verdicts],
                "issues": [
                    {"code": i.code, "message": i.message[:240]}
                    for i in review.issues
                ],
            }
            print(f"  {name}: {review.summary()} (calls {model.calls})", flush=True)
        results.append(item)

    payload = {
        "prompt_version": PROMPT_VERSION,
        "model": MODEL,
        "call_budget": args.budget,
        "calls_used": model.calls,
        "generated": record,
        "synthetic": results,
    }
    name = f"record-{args.phase}.json"
    (out / name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\ncalls used: {model.calls}/{args.budget}", flush=True)
    print(f"record: {out / name}", flush=True)
    return 0


def _synthetic_cases():
    """The three injected cases, as in tests/test_ground_review.py."""
    from spec2test.domain.models import (
        GROUNDING_RULES_VERSION, Ground, GroundKind, Requirement, SourceSpan,
        TestCase, TestStep,
    )

    text = (
        "SYS-REQ-0001: While the pack is charging, the cell voltage shall not "
        "exceed 4.20 V. The 12 V auxiliary supply powers the monitoring board. "
        "During driving the cell voltage shall not exceed 4.15 V."
    )
    requirement = Requirement(
        requirement_id="REQ-1", text=text, external_id="SYS-REQ-0001",
        spans=(SourceSpan("spec.md", 0, len(text), text, section="1"),),
    )

    def case(case_id, claim, quote, expectation):
        return TestCase(
            case_id=case_id,
            title="Cell voltage while driving",
            objective="Check the cell voltage limit during driving.",
            requirement_ids=("SYS-REQ-0001",),
            grounding_rules=GROUNDING_RULES_VERSION,
            steps=(
                TestStep(
                    1, "drive the vehicle at constant speed", (expectation,),
                    grounds=(
                        Ground(
                            claim=claim, kind=GroundKind.TARGET_REQUIREMENT,
                            target="expectations[0]", source="SYS-REQ-0001",
                            quote=quote,
                        ),
                    ),
                ),
            ),
        )

    return [
        (
            "wrong quantity (12 V is the supply rail)",
            case("SYN-1", "12 V",
                 "The 12 V auxiliary supply powers the monitoring board.",
                 "the measured cell voltage stays below 12 V"),
            requirement,
        ),
        (
            "wrong state (4.20 V is the charging limit)",
            case("SYN-2", "4.20 V",
                 "While the pack is charging, the cell voltage shall not exceed 4.20 V.",
                 "the measured cell voltage stays below 4.20 V"),
            requirement,
        ),
        (
            "correct (4.15 V is the driving limit)",
            case("SYN-3", "4.15 V",
                 "During driving the cell voltage shall not exceed 4.15 V.",
                 "the measured cell voltage stays below 4.15 V"),
            requirement,
        ),
    ]


if __name__ == "__main__":
    raise SystemExit(main())
