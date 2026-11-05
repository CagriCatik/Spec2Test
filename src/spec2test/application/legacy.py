"""Deprecated compatibility path for the existing PySide6 GUI.

The GUI still works on the historical flat shape (``actions`` /
``expected_results``) and on a per-page-block prompt without captured
requirements, so it cannot provide requirement traceability. This bridge keeps
it running with the AP 1 corrections (no lost pages, visible parse failures,
run status) until the GUI is moved onto :func:`application.generate.
generate_drafts` in L2. Do not extend it.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, Sequence

from ..domain.models import (
    GenerationAttempt,
    GenerationRun,
    GenerationUnitResult,
    Severity,
    SourceSpan,
    TestCase,
    UnitStatus,
    ValidationIssue,
)
from .chunking import Block, build_blocks
from .contracts import ParseOutcome, strip_code_fences
from .generate import new_run_id

LEGACY_KEYS = (
    "testcase_name",
    "objective",
    "linked_requirements",
    "preconditions",
    "actions",
    "expected_results",
    "postconditions",
)
LEGACY_REQUIRED = ("objective", "actions", "expected_results", "postconditions")
LEGACY_PROMPT_VERSION = "legacy-flat-1"
LEGACY_SCHEMA_VERSION = "legacy-flat"


class LegacyAgent(Protocol):
    def generate(self, content: str, *, system_prompt: str | None = ...) -> str: ...


@dataclass
class LegacyParseResult:
    outcome: ParseOutcome
    cases: list[dict] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    raw: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is ParseOutcome.OK


def _issue(code: str, path: str, message: str, severity: Severity = Severity.ERROR):
    return ValidationIssue(code, severity, path, message)


def parse_legacy_cases(raw: str) -> LegacyParseResult:
    """Parse the historical flat JSON array.

    Non-object elements and wrong field types are reported, never turned into
    empty placeholder cases.
    """
    cleaned = strip_code_fences(raw or "")
    if not cleaned:
        return LegacyParseResult(
            ParseOutcome.EMPTY,
            issues=[_issue("E_EMPTY_RESPONSE", "$", "the model returned no content")],
            raw=raw or "",
        )
    try:
        data: Any = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start == -1 or end <= start:
            return LegacyParseResult(
                ParseOutcome.INVALID_JSON,
                issues=[_issue("E_INVALID_JSON", "$", "no JSON array found in the answer")],
                raw=raw,
            )
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            return LegacyParseResult(
                ParseOutcome.INVALID_JSON,
                issues=[_issue("E_INVALID_JSON", "$", f"not valid JSON ({exc.msg})")],
                raw=raw,
            )

    if not isinstance(data, list):
        return LegacyParseResult(
            ParseOutcome.SCHEMA_INVALID,
            issues=[
                _issue("E_SCHEMA", "$", f"expected a JSON array, got {type(data).__name__}")
            ],
            raw=raw,
        )

    cases: list[dict] = []
    issues: list[ValidationIssue] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            issues.append(
                _issue(
                    "E_INVALID_TYPE",
                    f"$[{index}]",
                    f"element is {type(item).__name__}, expected an object; "
                    "it is rejected instead of becoming an empty test case",
                )
            )
            continue
        case = {key: item.get(key, "") for key in LEGACY_KEYS}
        for key, value in case.items():
            if value not in ("", None) and not isinstance(value, (str, list)):
                issues.append(
                    _issue(
                        "E_INVALID_TYPE",
                        f"$[{index}].{key}",
                        f"field is {type(value).__name__}, expected string or list",
                    )
                )
        unknown = sorted(set(item) - set(LEGACY_KEYS))
        if unknown:
            issues.append(
                _issue(
                    "C_UNKNOWN_FIELD",
                    f"$[{index}]",
                    f"unexpected field(s) {unknown} were dropped",
                    Severity.CLARIFICATION,
                )
            )
        missing = [key for key in LEGACY_REQUIRED if not _filled(case.get(key))]
        if missing:
            issues.append(
                _issue("E_EMPTY_FIELD", f"$[{index}]", f"empty required field(s): {missing}")
            )
        cases.append(case)

    if not cases:
        return LegacyParseResult(
            ParseOutcome.NO_TEST_CASES,
            issues=issues
            or [_issue("E_NO_TEST_CASES", "$", "valid JSON but no test case was returned")],
            raw=raw,
        )
    outcome = ParseOutcome.OK if not any(
        i.severity is Severity.ERROR for i in issues
    ) else ParseOutcome.SCHEMA_INVALID
    return LegacyParseResult(outcome, cases=cases, issues=issues, raw=raw)


def _filled(value) -> bool:
    if isinstance(value, list):
        return any(str(v).strip() for v in value)
    return bool(str(value or "").strip())


def _spans(pages: Sequence[str], block: Block, document_id: str) -> tuple[SourceSpan, ...]:
    offsets: list[int] = []
    cursor = 0
    for page in pages:
        offsets.append(cursor)
        cursor += len(page) + 2
    spans: list[SourceSpan] = []
    for page_no in block.pages:
        start = offsets[page_no - 1]
        text = pages[page_no - 1]
        spans.append(
            SourceSpan(
                document_id=document_id,
                char_start=start,
                char_end=start + len(text),
                quote=text[:200],
                page=page_no,
            )
        )
    return tuple(spans)


def generate_legacy_cases(
    pages: Sequence[str],
    agent: LegacyAgent,
    *,
    document_id: str,
    combine: int,
    overlap: int = 0,
    n_cases: int = 6,
    prompt_role: str | None = None,
    max_repair_attempts: int = 1,
    build_prompt: Callable[[Block, int, str], str] | None = None,
    model_name: str = "unknown",
    endpoint: str = "unknown",
    log: Callable[[str], None] | None = None,
    progress: Callable[[int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    cache_get: Callable[[str], list[dict] | None] | None = None,
    cache_set: Callable[[str, list[dict]], None] | None = None,
) -> tuple[list[dict], GenerationRun]:
    """DEPRECATED bridge. Returns legacy case dicts plus a run with its status."""
    blocks = build_blocks(pages, combine, overlap=overlap)
    run = GenerationRun(
        run_id=new_run_id("legacy"),
        model=model_name,
        endpoint=endpoint,
        mode="legacy",
        prompt_version=LEGACY_PROMPT_VERSION,
        schema_version=LEGACY_SCHEMA_VERSION,
        source_hash="",
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    all_cases: list[dict] = []
    cancelled = False

    def say(message: str) -> None:
        if log:
            log(message)

    for block in blocks:
        if should_cancel and should_cancel():
            cancelled = True
            say(f"cancelled before block {block.index}/{len(blocks)}")
            break

        unit = GenerationUnitResult(
            unit_id=f"block-{block.index}",
            requirement_ids=(),
            status=UnitStatus.REJECTED,
            source_spans=_spans(pages, block, document_id),
        )
        prompt = (
            build_prompt(block, len(blocks), block.text)
            if build_prompt
            else _default_prompt(block, len(blocks), n_cases)
        )
        if cache_get is not None:
            hit = cache_get(prompt)
            if hit is not None:
                # Only validated results are ever stored, and a cache hit is
                # no approval record.
                unit.status = UnitStatus.ACCEPTED
                unit.issues.append(
                    _issue(
                        "C_CACHE_HIT",
                        unit.unit_id,
                        f"{len(hit)} case(s) reused from the response cache; "
                        "this is not a technical approval",
                        Severity.INFO,
                    )
                )
                all_cases.extend(hit)
                run.units.append(unit)
                say(f"block {block.index}/{len(blocks)}: {len(hit)} cached case(s) reused")
                if progress:
                    progress(int(len(run.units) / len(blocks) * 100))
                continue

        attempt_no = 0
        while True:
            attempt_no += 1
            started = time.perf_counter()
            try:
                raw = agent.generate(prompt, system_prompt=prompt_role)
            except Exception as exc:  # the adapter decides; no CLI fallback here
                unit.attempts.append(
                    GenerationAttempt(
                        attempt=attempt_no,
                        prompt_version=LEGACY_PROMPT_VERSION,
                        schema_version=LEGACY_SCHEMA_VERSION,
                        raw_response="",
                        is_repair=attempt_no > 1,
                        error=f"{type(exc).__name__}: {exc}",
                        duration_s=time.perf_counter() - started,
                    )
                )
                unit.issues.append(
                    _issue("E_MODEL_CALL_FAILED", unit.unit_id, f"{type(exc).__name__}: {exc}")
                )
                break

            parsed = parse_legacy_cases(raw)
            unit.attempts.append(
                GenerationAttempt(
                    attempt=attempt_no,
                    prompt_version=LEGACY_PROMPT_VERSION,
                    schema_version=LEGACY_SCHEMA_VERSION,
                    raw_response=raw,
                    issues=tuple(parsed.issues),
                    is_repair=attempt_no > 1,
                    duration_s=time.perf_counter() - started,
                )
            )
            say(
                f"block {block.index}/{len(blocks)} ({block.label()}): "
                f"{parsed.outcome.name.lower()}, {len(parsed.cases)} case(s)"
            )
            if parsed.ok:
                unit.status = UnitStatus.ACCEPTED
                unit.issues = [i for i in parsed.issues if i.severity is not Severity.ERROR]
                all_cases.extend(parsed.cases)
                if cache_set is not None:
                    cache_set(prompt, parsed.cases)
                break
            if attempt_no - 1 >= max_repair_attempts:
                unit.issues = list(parsed.issues)
                # Partial but structurally sound cases are kept; the block is
                # still reported as rejected so the failure stays visible.
                usable = [c for c in parsed.cases if all(_filled(c[k]) for k in LEGACY_REQUIRED)]
                all_cases.extend(usable)
                say(
                    f"block {block.index}/{len(blocks)} rejected after "
                    f"{attempt_no - 1} repair attempt(s): {parsed.outcome.name.lower()}"
                )
                break
            prompt = (
                f"{prompt}\n\nYour previous answer was rejected by local validation:\n"
                + "\n".join(f"- {i.path}: {i.message}" for i in parsed.issues)
                + "\n\nReturn the complete corrected JSON array."
            )

        run.units.append(unit)
        if progress:
            progress(int(len(run.units) / len(blocks) * 100))

    run.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run.resolve_status(cancelled=cancelled)
    for line in run.report_lines():
        say(line)
    return all_cases, run


def _default_prompt(block: Block, total: int, n_cases: int) -> str:
    return (
        f"Generate at most {n_cases} independent test cases following the JSON "
        "schema, including the 'linked_requirements' field.\n-----\n"
        f"Requirements excerpt ({block.index}/{total}, {block.label()}):\n"
        f"{block.text}\n-----"
    )


def legacy_rows(cases: Sequence[TestCase]) -> list[dict]:
    """Flatten new test drafts into the historical flat shape.

    Interim export for the existing CSV/Excel buttons. Actions and expectations
    keep their step number as a prefix, so the pairing survives the flattening
    instead of degrading into two unrelated lists. A real export with separate
    tables for steps and requirement links is AP 6.
    """
    rows: list[dict] = []
    for case in cases:
        actions: list[str] = []
        expectations: list[str] = []
        for step in case.steps:
            actions.append(f"{step.order}. {step.action}")
            for expectation in step.expectations:
                expectations.append(f"{step.order}. {expectation}")
        rows.append(
            {
                "testcase_name": case.title,
                "objective": case.objective,
                "linked_requirements": list(case.requirement_ids),
                "preconditions": [
                    f"{step.order}. {step.action}" for step in case.preconditions
                ],
                "actions": actions,
                "expected_results": expectations,
                "postconditions": [
                    f"{step.order}. {step.action}" for step in case.postconditions
                ],
            }
        )
    return rows
