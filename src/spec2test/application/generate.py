"""Generation use case: call the model, validate strictly, repair in a bounded
way, and keep failures visible.

Nothing here knows about HTTP, Qt, PDF or Excel.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Sequence

from ..domain.models import (
    GenerationAttempt,
    GenerationRun,
    GenerationUnitResult,
    OpenQuestion,
    QuestionOrigin,
    Requirement,
    RunStatus,
    Severity,
    TestCase,
    UnitStatus,
    ValidationIssue,
)
from ..domain.validation import ValidationReport, validate_test_case
from .assembly import build_from_response
from .contracts import SCHEMA_VERSION, ParseOutcome, parse_model_response
from .deadline import Deadline, DeadlineExceeded
from .ports import ModelError, ModelPort
from .prompts import PROMPT_VERSION, build_generation_messages, build_repair_messages


@dataclass
class GenerationSettings:
    """Resource limits. ``max_cases_per_unit`` is a ceiling, never a quota."""

    max_cases_per_unit: int = 5
    max_repair_attempts: int = 2
    group_size: int = 1
    input_char_budget: int = 12_000
    job_timeout_s: float | None = 900.0
    system_prompt: str | None = None


@dataclass
class ProgressUpdate:
    """``fraction == 1.0`` means the processing finished - not that it worked."""

    done: int
    total: int
    unit_id: str
    note: str = ""

    @property
    def percent(self) -> int:
        return 100 if self.total == 0 else int(self.done / self.total * 100)


ProgressCallback = Callable[[ProgressUpdate], None]
LogCallback = Callable[[str], None]
CancelCheck = Callable[[], bool]


@dataclass
class _Unit:
    unit_id: str
    requirements: list[Requirement] = field(default_factory=list)

    @property
    def requirement_ids(self) -> tuple[str, ...]:
        return tuple(r.requirement_id for r in self.requirements)

    @property
    def char_size(self) -> int:
        return sum(len(r.text) for r in self.requirements)

    @property
    def spans(self):
        return tuple(span for r in self.requirements for span in r.spans)


def plan_units(
    requirements: Sequence[Requirement], settings: GenerationSettings
) -> list[_Unit]:
    """Group requirements into generation units inside the input budget.

    Oversized groups are split visibly; nothing is ever truncated.
    """
    units: list[_Unit] = []
    current: list[Requirement] = []

    def flush() -> None:
        if current:
            units.append(_Unit(f"U{len(units) + 1:03d}", list(current)))
            current.clear()

    for req in requirements:
        prospective = sum(len(r.text) for r in current) + len(req.text)
        if current and (
            len(current) >= max(1, settings.group_size)
            or prospective > settings.input_char_budget
        ):
            flush()
        current.append(req)
    flush()
    return units


def _log(log: LogCallback | None, message: str) -> None:
    if log:
        log(message)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id(prefix: str = "run") -> str:
    """Sortable and collision-free: two runs in the same second stay distinct."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:12]}"


def generate_drafts(
    requirements: Sequence[Requirement],
    model: ModelPort,
    *,
    settings: GenerationSettings | None = None,
    source_hash: str = "",
    run_id: str | None = None,
    progress: ProgressCallback | None = None,
    log: LogCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> GenerationRun:
    settings = settings or GenerationSettings()
    descriptor = model.descriptor
    run = GenerationRun(
        run_id=run_id or new_run_id(),
        model=descriptor.model,
        endpoint=descriptor.endpoint,
        mode=descriptor.mode,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        source_hash=source_hash,
        parameters=dict(descriptor.parameters),
        started_at=_now(),
    )
    deadline = Deadline(settings.job_timeout_s)
    units = plan_units(requirements, settings)
    run.planned_unit_count = len(units)
    cancelled = False

    _log(
        log,
        f"run {run.run_id}: {len(requirements)} requirement(s) in {len(units)} unit(s) "
        f"via {descriptor.mode} {descriptor.model} at {descriptor.endpoint}",
    )

    for index, unit in enumerate(units, 1):
        if should_cancel and should_cancel():
            cancelled = True
            reason = "the run was cancelled"
            run.errors.append(
                f"cancelled before unit {unit.unit_id}; "
                f"{len(units) - index + 1} unit(s) not started"
            )
            _log(log, f"run {run.run_id}: {run.errors[-1]}")
            run.units.extend(_not_started(units[index - 1 :], reason))
            break
        if deadline.expired:
            reason = (
                f"the job budget of {settings.job_timeout_s}s was exhausted "
                f"after {deadline.elapsed_s:.1f}s"
            )
            run.errors.append(
                f"job budget of {settings.job_timeout_s}s exhausted before "
                f"unit {unit.unit_id}; {len(units) - index + 1} unit(s) not started"
            )
            _log(log, f"run {run.run_id}: {run.errors[-1]}")
            run.units.extend(_not_started(units[index - 1 :], reason))
            break

        result = _process_unit(unit, model, settings, deadline, log)
        run.units.append(result)
        if result.fatal_error:
            run.errors.append(
                f"stopping the run after {unit.unit_id}: {result.fatal_error}"
            )
            _log(log, f"run {run.run_id}: {run.errors[-1]}")
            remaining = units[index:]
            if remaining:
                run.units.extend(_not_started(remaining, result.fatal_error))
            if progress:
                progress(
                    ProgressUpdate(
                        done=len(units),
                        total=len(units),
                        unit_id=unit.unit_id,
                        note="stopped",
                    )
                )
            break
        if progress:
            progress(
                ProgressUpdate(
                    done=index,
                    total=len(units),
                    unit_id=unit.unit_id,
                    note=result.status.value,
                )
            )

    run.finished_at = _now()
    run.resolve_status(cancelled=cancelled)
    for line in run.report_lines():
        _log(log, line)
    return run


def _not_started(units: Sequence["_Unit"], reason: str) -> list[GenerationUnitResult]:
    """Record planned units that never reached the model, with their sources."""
    return [
        GenerationUnitResult(
            unit_id=unit.unit_id,
            requirement_ids=unit.requirement_ids,
            status=UnitStatus.NOT_STARTED,
            source_spans=unit.spans,
            issues=[
                ValidationIssue(
                    "E_UNIT_NOT_STARTED",
                    Severity.ERROR,
                    unit.unit_id,
                    f"no test draft was generated for this requirement: {reason}",
                )
            ],
        )
        for unit in units
    ]


def _process_unit(
    unit: _Unit,
    model: ModelPort,
    settings: GenerationSettings,
    deadline: Deadline,
    log: LogCallback | None,
) -> GenerationUnitResult:
    result = GenerationUnitResult(
        unit_id=unit.unit_id,
        requirement_ids=unit.requirement_ids,
        status=UnitStatus.REJECTED,
        source_spans=unit.spans,
    )

    if unit.char_size > settings.input_char_budget:
        result.issues.append(
            ValidationIssue(
                "E_INPUT_TOO_LARGE",
                Severity.ERROR,
                unit.unit_id,
                f"requirement text of {unit.char_size} characters exceeds the input "
                f"budget of {settings.input_char_budget}; split the requirement "
                "manually - it is never truncated silently",
            )
        )
        return result

    messages = build_generation_messages(
        unit.requirements,
        max_cases=settings.max_cases_per_unit,
        system_prompt=settings.system_prompt,
    )
    attempt_no = 0
    last_raw = ""

    while True:
        attempt_no += 1
        is_repair = attempt_no > 1
        started = time.perf_counter()
        try:
            deadline.check(f"unit {unit.unit_id}")
            reply = model.chat(messages, deadline=deadline)
        except (ModelError, DeadlineExceeded) as exc:
            if getattr(exc, "fatal", False):
                result.fatal_error = f"{type(exc).__name__}: {exc}"
            result.attempts.append(
                GenerationAttempt(
                    attempt=attempt_no,
                    prompt_version=PROMPT_VERSION,
                    schema_version=SCHEMA_VERSION,
                    raw_response="",
                    is_repair=is_repair,
                    error=f"{type(exc).__name__}: {exc}",
                    duration_s=time.perf_counter() - started,
                )
            )
            result.issues.append(
                ValidationIssue(
                    "E_MODEL_CALL_FAILED",
                    Severity.ERROR,
                    unit.unit_id,
                    f"{type(exc).__name__}: {exc}",
                )
            )
            return result

        last_raw = reply.text
        parsed = parse_model_response(reply.text)
        cases: list[TestCase] = []
        questions: list[OpenQuestion] = []
        issues: list[ValidationIssue] = list(parsed.issues)
        accepted: list[TestCase] = []

        if parsed.ok and parsed.response is not None:
            cases, questions = build_from_response(
                parsed.response, unit.requirements, unit_id=unit.unit_id
            )
            per_case: list[tuple[TestCase, ValidationReport]] = []
            for case in cases:
                report = validate_test_case(case, unit.requirements)
                per_case.append((case, report))
                issues.extend(report.issues)
            accepted = [case for case, report in per_case if not report.blocks_draft]

        if reply.truncated:
            issues.append(
                ValidationIssue(
                    "E_TRUNCATED_ANSWER",
                    Severity.ERROR,
                    unit.unit_id,
                    f"the model stopped early (done_reason={reply.done_reason!r}); "
                    "the answer is incomplete",
                )
            )
        elif parsed.looks_truncated:
            issues.append(
                ValidationIssue(
                    "E_TRUNCATED_ANSWER",
                    Severity.ERROR,
                    unit.unit_id,
                    "the JSON is unbalanced; the answer appears to be truncated",
                )
            )

        result.attempts.append(
            GenerationAttempt(
                attempt=attempt_no,
                prompt_version=PROMPT_VERSION,
                schema_version=SCHEMA_VERSION,
                raw_response=reply.text,
                issues=tuple(issues),
                is_repair=is_repair,
                duration_s=reply.duration_s or (time.perf_counter() - started),
                truncated=reply.truncated or parsed.looks_truncated,
            )
        )

        blocking = [i for i in issues if i.severity is Severity.ERROR]
        if accepted and not blocking:
            result.status = UnitStatus.ACCEPTED
            result.test_cases = accepted
            result.open_questions = questions + _questions_from_findings(
                unit, [i for i in issues if i.severity is Severity.CLARIFICATION]
            )
            result.issues = [i for i in issues if i.severity is not Severity.ERROR]
            _log(
                log,
                f"  {unit.unit_id}: {len(accepted)} draft(s) accepted"
                + (f", {len(result.issues)} finding(s)" if result.issues else "")
                + (f" after {attempt_no - 1} repair(s)" if attempt_no > 1 else ""),
            )
            return result

        repairs_used = attempt_no - 1
        if repairs_used >= settings.max_repair_attempts:
            result.status = UnitStatus.REJECTED
            result.issues = issues
            _log(
                log,
                f"  {unit.unit_id}: rejected after {repairs_used} repair attempt(s) - "
                f"{len(blocking)} blocking finding(s)",
            )
            return result

        _log(
            log,
            f"  {unit.unit_id}: attempt {attempt_no} rejected "
            f"({', '.join(sorted({i.code for i in blocking}))}); repairing",
        )
        messages = build_repair_messages(
            unit.requirements,
            last_raw,
            blocking,
            max_cases=settings.max_cases_per_unit,
            system_prompt=settings.system_prompt,
        )


def _questions_from_findings(
    unit: _Unit, findings: Sequence[ValidationIssue]
) -> list[OpenQuestion]:
    """Clarification findings survive as open questions, never as a spec."""
    return [
        OpenQuestion(
            question_id=f"OQ-{unit.unit_id}-V{i + 1:02d}",
            question=f"{finding.code} at {finding.path}: {finding.message}",
            requirement_ids=unit.requirement_ids,
            origin=QuestionOrigin.VALIDATION,
        )
        for i, finding in enumerate(findings)
    ]


__all__ = [
    "GenerationSettings",
    "new_run_id",
    "ProgressUpdate",
    "RunStatus",
    "generate_drafts",
    "plan_units",
]
