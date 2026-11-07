"""Generation use case: call the model, validate strictly, repair in a bounded
way, and keep failures visible.

Nothing here knows about HTTP, Qt, PDF or Excel.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Mapping, Sequence

from ..domain.models import (
    SourceDocument,
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
from .context import (
    DEFAULT_CONTEXT_CHAR_BUDGET,
    DEFAULT_MAX_CONTEXT_ITEMS,
    ContextSelection,
    build_context,
    detect_contradictions,
)
from .ingest import ReferenceReport, reference_report
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
    #: optional project guidance, appended to - never replacing - the contract
    #: rules of the system prompt
    domain_guidance: str | None = None
    #: send the requirements a unit cites as supporting context
    use_reference_context: bool = True
    context_char_budget: int = DEFAULT_CONTEXT_CHAR_BUDGET
    max_context_items: int = DEFAULT_MAX_CONTEXT_ITEMS
    #: add full-text hits from the knowledge index on top of the references
    use_retrieval: bool = False
    max_search_items: int = 3
    #: "lexical" (BM25 only) or "hybrid" (BM25 fused with embeddings).
    #: Measured on this corpus, hybrid finds the source of a prose question
    #: twice as often and keeps BM25's perfect exact-ID retrieval, at the cost
    #: of an embedding model. It stays opt-in until the effect on the written
    #: drafts has been measured as well.
    retrieval: str = "lexical"
    #: the Ollama embedding model used when retrieval == "hybrid"
    embedding_model: str = "nomic-embed-text"


#: measured mean duration of one unit against a cloud model, for the estimate
TYPICAL_SECONDS_PER_UNIT = 11.0


def estimate_runtime(unit_count: int, settings: GenerationSettings) -> tuple[float, bool]:
    """Rough runtime for a planned scope, and whether the budget covers it."""
    expected = unit_count * TYPICAL_SECONDS_PER_UNIT
    budget = settings.job_timeout_s
    return expected, budget is None or expected <= budget


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
    requirements: Sequence[Requirement],
    settings: GenerationSettings,
    *,
    prefix: str = "",
) -> list[_Unit]:
    """Group requirements into generation units inside the input budget.

    Oversized groups are split visibly; nothing is ever truncated. ``prefix``
    makes the unit IDs - and with them every test case and question ID derived
    from them - unique across the runs of a project.
    """
    units: list[_Unit] = []
    current: list[Requirement] = []

    def flush() -> None:
        if current:
            units.append(_Unit(f"{prefix}U{len(units) + 1:03d}", list(current)))
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
    documents: Mapping[str, SourceDocument] | None = None,
    catalogue: Sequence[Requirement] | None = None,
    knowledge=None,
) -> GenerationRun:
    """``catalogue`` is the full requirement set references are resolved in;
    it defaults to the requirements being generated for."""
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
    references = (
        reference_report(list(catalogue if catalogue is not None else requirements))
        if settings.use_reference_context
        else None
    )
    deadline = Deadline(settings.job_timeout_s)
    # a short token of the run id keeps IDs unique when a project collects
    # several runs
    units = plan_units(requirements, settings, prefix=f"{run.run_id.split('-')[-1][:6]}-")
    run.planned_unit_count = len(units)
    cancelled = False

    _log(
        log,
        f"run {run.run_id}: {len(requirements)} requirement(s) in {len(units)} unit(s) "
        f"via {descriptor.mode} {descriptor.model} at {descriptor.endpoint}",
    )
    expected, fits = estimate_runtime(len(units), settings)
    if not fits:
        _log(
            log,
            f"warning: {len(units)} unit(s) need about {expected / 60:.0f} min, "
            f"the job budget is {(settings.job_timeout_s or 0) / 60:.0f} min. "
            "The run will stop early and report the rest as not started. "
            "Raise generation.job_timeout_s or narrow the scope.",
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

        result = _process_unit(
            unit,
            model,
            settings,
            deadline,
            log,
            references,
            documents or {},
            knowledge if settings.use_retrieval else None,
        )
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


def context_issues_of(result: GenerationUnitResult) -> list[ValidationIssue]:
    """The context findings recorded before the first model call."""
    return [
        issue
        for issue in result.issues
        if issue.code
        in {
            "C_UNRESOLVED_REFERENCE",
            "C_AMBIGUOUS_REFERENCE",
            "C_CONTEXT_OMITTED",
            "C_POSSIBLE_VALUE_CONFLICT",
        }
    ]


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
    references: ReferenceReport | None = None,
    documents: Mapping[str, SourceDocument] | None = None,
    knowledge=None,
) -> GenerationUnitResult:
    result = GenerationUnitResult(
        unit_id=unit.unit_id,
        requirement_ids=unit.requirement_ids,
        status=UnitStatus.REJECTED,
        source_spans=unit.spans,
    )

    selection = ContextSelection()
    if references is not None:
        selection = build_context(
            unit.requirements,
            references,
            documents=documents or {},
            knowledge=knowledge,
            char_budget=settings.context_char_budget,
            max_items=settings.max_context_items,
            max_search_items=settings.max_search_items,
        )
        # stored verbatim: the run stays explainable after the documents change
        result.context = selection.items
        context_issues = [
            *selection.issues,
            *detect_contradictions(unit.requirements, selection.items),
        ]
        result.issues.extend(context_issues)
        if selection.items:
            _log(
                log,
                f"  {unit.unit_id}: {len(selection.items)} supporting context "
                f"requirement(s): "
                + ", ".join(
                    f"{item.external_id or item.heading or item.requirement_id}"
                    f" ({item.kind.value}, {item.reason.value})"
                    for item in selection.items
                ),
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
        domain_guidance=settings.domain_guidance,
        context=selection.items,
    )
    attempt_no = 0
    last_raw = ""
    previous_raw: str | None = None

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

        previous_raw = last_raw if attempt_no > 1 else None
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
                report = validate_test_case(
                    case, unit.requirements, context=selection.items
                )
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

        if len(accepted) > settings.max_cases_per_unit:
            # The prompt asks for a ceiling; the application enforces it, so a
            # talkative model cannot blow up the resource budget. The discarded
            # cases stay in the raw answer of this attempt.
            discarded = len(accepted) - settings.max_cases_per_unit
            accepted = accepted[: settings.max_cases_per_unit]
            issues.append(
                ValidationIssue(
                    "C_CASE_LIMIT_ENFORCED",
                    Severity.CLARIFICATION,
                    unit.unit_id,
                    f"the model returned {discarded} test case(s) more than the "
                    f"limit of {settings.max_cases_per_unit}; the surplus was not "
                    "accepted and remains only in the raw answer",
                )
            )

        blocking = [i for i in issues if i.severity is Severity.ERROR]
        if accepted and not blocking:
            result.status = UnitStatus.ACCEPTED
            result.test_cases = accepted
            result.open_questions = questions + _questions_from_findings(
                unit, [i for i in issues if i.severity is Severity.CLARIFICATION]
            )
            result.issues = [
                *context_issues_of(result),
                *[i for i in issues if i.severity is not Severity.ERROR],
            ]
            _log(
                log,
                f"  {unit.unit_id}: {len(accepted)} draft(s) accepted"
                + (f", {len(result.issues)} finding(s)" if result.issues else "")
                + (f" after {attempt_no - 1} repair(s)" if attempt_no > 1 else ""),
            )
            return result

        if is_repair and reply.text == previous_raw:
            issues.append(
                ValidationIssue(
                    "C_REPAIR_IDENTICAL",
                    Severity.CLARIFICATION,
                    unit.unit_id,
                    "the repair returned exactly the same answer; further "
                    "attempts would only cost usage without changing anything",
                )
            )
            result.status = UnitStatus.REJECTED
            result.issues = [*context_issues_of(result), *issues]
            _log(
                log,
                f"  {unit.unit_id}: repair returned an identical answer; "
                "stopping instead of spending another call",
            )
            return result

        repairs_used = attempt_no - 1
        if repairs_used >= settings.max_repair_attempts:
            result.status = UnitStatus.REJECTED
            result.issues = [*context_issues_of(result), *issues]
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
            domain_guidance=settings.domain_guidance,
            context=selection.items,
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
    "estimate_runtime",
    "new_run_id",
    "ProgressUpdate",
    "RunStatus",
    "generate_drafts",
    "plan_units",
]
