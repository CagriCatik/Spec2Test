"""Domain model for Spec2Test.

Pure data: no Qt, no HTTP, no PDF, no Excel, no filesystem access.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Sequence


# ------------------------------------------------------------------ enums ---
class DocumentFormat(str, Enum):
    PDF = "pdf"
    TXT = "txt"
    MARKDOWN = "markdown"


class ClarificationStatus(str, Enum):
    """Whether a requirement is usable as a test basis as written."""

    CLEAR = "clear"
    NEEDS_CLARIFICATION = "needs_clarification"
    NOT_TESTABLE = "not_testable"


class ReviewStatus(str, Enum):
    """Only a deliberate tester decision reaches ``APPROVED``."""

    DRAFT = "draft"
    NEEDS_CLARIFICATION = "needs_clarification"
    APPROVED = "approved"
    REJECTED = "rejected"


class Severity(str, Enum):
    """How a validation finding affects the workflow.

    ERROR         - blocks acceptance as a valid draft (and approval)
    CLARIFICATION - may be stored as a draft, blocks approval
    INFO          - informational only
    """

    ERROR = "error"
    CLARIFICATION = "clarification"
    INFO = "info"


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UnitStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    #: planned but never sent to the model (budget exhausted, run cancelled)
    NOT_STARTED = "not_started"


class QuestionOrigin(str, Enum):
    MODEL = "model"
    VALIDATION = "validation"
    REVIEWER = "reviewer"


# ------------------------------------------------------------- documents ---
@dataclass(frozen=True)
class SourceDocument:
    """An imported document; ``text`` is the unmodified extracted text."""

    document_id: str
    filename: str
    format: DocumentFormat
    content_hash: str
    text: str
    page_count: int = 1
    empty_pages: tuple[int, ...] = ()

    def slice(self, char_start: int, char_end: int) -> str:
        return self.text[char_start:char_end]


@dataclass(frozen=True)
class SourceSpan:
    """A traceable location in the extracted text of one document."""

    document_id: str
    char_start: int
    char_end: int
    quote: str
    page: int | None = None
    section: str | None = None

    def __post_init__(self) -> None:
        if self.char_start < 0 or self.char_end < self.char_start:
            raise ValueError(
                f"invalid span [{self.char_start}, {self.char_end}) in {self.document_id}"
            )

    def label(self) -> str:
        where = f"page {self.page}" if self.page is not None else (self.section or "?")
        return f"{self.document_id} ({where}, chars {self.char_start}-{self.char_end})"


@dataclass(frozen=True)
class Requirement:
    """One requirement.

    ``requirement_id`` is the stable *internal* ID and is never presented as an
    ID of the source document. ``external_id`` is the ID found in the document,
    if any. Identical external IDs in different documents stay separate.
    """

    requirement_id: str
    text: str
    spans: tuple[SourceSpan, ...]
    external_id: str | None = None
    clarification_status: ClarificationStatus = ClarificationStatus.CLEAR
    notes: tuple[str, ...] = ()

    @property
    def has_external_id(self) -> bool:
        return bool(self.external_id)

    def display_id(self) -> str:
        return self.external_id or f"{self.requirement_id} (internal)"


# ------------------------------------------------------------- test cases ---
@dataclass(frozen=True)
class Measurement:
    """A measurable quantity. ``evidence`` must quote the source requirement."""

    quantity: str
    value: str
    unit: str | None = None
    tolerance: str | None = None
    evidence: str | None = None


@dataclass(frozen=True)
class TestStep:
    """An action together with the observable expectations for that action.

    Preconditions use the same type: a precondition may need several steps and
    its execution is never implicitly assumed to have succeeded.
    """

    order: int
    action: str
    expectations: tuple[str, ...] = ()
    measurements: tuple[Measurement, ...] = ()

    def __post_init__(self) -> None:
        if self.order < 1:
            raise ValueError("step order starts at 1")


@dataclass(frozen=True)
class OpenQuestion:
    question_id: str
    question: str
    requirement_ids: tuple[str, ...] = ()
    origin: QuestionOrigin = QuestionOrigin.MODEL
    suggestion: str | None = None  # explicitly marked proposal, never a spec


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: Severity
    path: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.value}] {self.code} at {self.path}: {self.message}"


@dataclass(frozen=True)
class TestCase:
    """A test draft. The application assigns ``case_id`` and ``review_status``."""

    case_id: str
    title: str
    objective: str
    requirement_ids: tuple[str, ...]
    preconditions: tuple[TestStep, ...] = ()
    steps: tuple[TestStep, ...] = ()
    postconditions: tuple[TestStep, ...] = ()
    open_questions: tuple[OpenQuestion, ...] = ()
    review_status: ReviewStatus = ReviewStatus.DRAFT
    revision: int = 1
    source_spans: tuple[SourceSpan, ...] = ()
    legacy_payload: dict | None = None

    def with_status(self, status: ReviewStatus) -> "TestCase":
        return replace(self, review_status=status)

    def next_revision(self, **changes) -> "TestCase":
        """Any edit produces a new revision and drops a previous approval."""
        return replace(
            self,
            revision=self.revision + 1,
            review_status=ReviewStatus.DRAFT,
            **changes,
        )

    def all_expectations(self) -> list[str]:
        out: list[str] = []
        for step in (*self.preconditions, *self.steps, *self.postconditions):
            out.extend(step.expectations)
        return out

    def all_measurements(self) -> list[Measurement]:
        out: list[Measurement] = []
        for step in (*self.preconditions, *self.steps, *self.postconditions):
            out.extend(step.measurements)
        return out


# ----------------------------------------------------------------- runs -----
@dataclass(frozen=True)
class GenerationAttempt:
    """One model call, kept verbatim for traceability."""

    attempt: int
    prompt_version: str
    schema_version: str
    raw_response: str
    issues: tuple[ValidationIssue, ...] = ()
    is_repair: bool = False
    error: str | None = None
    duration_s: float | None = None
    truncated: bool = False


@dataclass
class GenerationUnitResult:
    """Outcome for one requirement (or justified group of requirements)."""

    unit_id: str
    requirement_ids: tuple[str, ...]
    status: UnitStatus
    source_spans: tuple[SourceSpan, ...] = ()
    test_cases: list[TestCase] = field(default_factory=list)
    open_questions: list[OpenQuestion] = field(default_factory=list)
    attempts: list[GenerationAttempt] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    #: set when the failure makes every further unit pointless (credentials,
    #: configuration); the run stops instead of repeating the same error
    fatal_error: str | None = None

    @property
    def rejected(self) -> bool:
        return self.status is UnitStatus.REJECTED


@dataclass
class GenerationRun:
    """Status and provenance of one generation job."""

    run_id: str
    model: str
    endpoint: str
    mode: str
    prompt_version: str
    schema_version: str
    source_hash: str
    parameters: dict = field(default_factory=dict)
    status: RunStatus = RunStatus.RUNNING
    started_at: str | None = None
    finished_at: str | None = None
    #: how many units the run planned; compared against the processed ones
    planned_unit_count: int = 0
    units: list[GenerationUnitResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- views
    @property
    def test_cases(self) -> list[TestCase]:
        return [case for unit in self.units for case in unit.test_cases]

    @property
    def open_questions(self) -> list[OpenQuestion]:
        return [q for unit in self.units for q in unit.open_questions]

    @property
    def rejected_units(self) -> list[GenerationUnitResult]:
        return [unit for unit in self.units if unit.status is UnitStatus.REJECTED]

    @property
    def accepted_units(self) -> list[GenerationUnitResult]:
        return [unit for unit in self.units if unit.status is UnitStatus.ACCEPTED]

    @property
    def not_started_units(self) -> list[GenerationUnitResult]:
        return [unit for unit in self.units if unit.status is UnitStatus.NOT_STARTED]

    @property
    def processed_units(self) -> list[GenerationUnitResult]:
        """Units that actually reached the model (accepted or rejected)."""
        return [
            unit
            for unit in self.units
            if unit.status in (UnitStatus.ACCEPTED, UnitStatus.REJECTED)
        ]

    @property
    def is_incomplete(self) -> bool:
        """True when planned work was not processed."""
        if self.not_started_units:
            return True
        return self.planned_unit_count > len(self.processed_units)

    def resolve_status(self, *, cancelled: bool = False) -> RunStatus:
        """Derive the final status. Finishing is not the same as succeeding.

        A run is only ``completed`` when every planned unit was processed and
        accepted. Units that were never started - because the job budget ran
        out or the run was cancelled - make the run ``partial`` at best.
        """
        if cancelled:
            self.status = RunStatus.CANCELLED
        elif not self.units:
            self.status = RunStatus.FAILED
        elif not self.accepted_units:
            self.status = RunStatus.FAILED
        elif self.rejected_units or self.is_incomplete:
            self.status = RunStatus.PARTIAL
        else:
            self.status = RunStatus.COMPLETED
        return self.status

    def report_lines(self) -> list[str]:
        """Closing report; failed units stay visible with their source refs."""
        lines = [
            f"run {self.run_id}: {self.status.value}",
            f"  model={self.model} endpoint={self.endpoint} mode={self.mode}",
            f"  prompt={self.prompt_version} schema={self.schema_version}",
            f"  units planned={self.planned_unit_count or len(self.units)} "
            f"accepted={len(self.accepted_units)} rejected={len(self.rejected_units)} "
            f"not started={len(self.not_started_units)}",
            f"  test case drafts={len(self.test_cases)} open questions={len(self.open_questions)}",
        ]
        for unit in self.rejected_units:
            refs = ", ".join(span.label() for span in unit.source_spans) or "no span"
            lines.append(
                f"  REJECTED {unit.unit_id} [{', '.join(unit.requirement_ids)}] ({refs})"
            )
            for issue in unit.issues:
                lines.append(f"      {issue}")
        for unit in self.not_started_units:
            refs = ", ".join(span.label() for span in unit.source_spans) or "no span"
            lines.append(
                f"  NOT STARTED {unit.unit_id} "
                f"[{', '.join(unit.requirement_ids)}] ({refs})"
            )
            for issue in unit.issues:
                lines.append(f"      {issue}")
        for err in self.errors:
            lines.append(f"  ERROR {err}")
        return lines


def spans_of(requirements: Sequence[Requirement]) -> tuple[SourceSpan, ...]:
    return tuple(span for req in requirements for span in req.spans)
