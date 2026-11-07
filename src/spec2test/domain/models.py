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


class ContextKind(str, Enum):
    """What a piece of supporting context is.

    Knowing the kind keeps the roles apart: an interface description may name
    a signal, but it never supplies a missing target value.
    """

    REQUIREMENT = "requirement"
    SIGNAL = "signal"
    INTERFACE = "interface"
    EXPLANATION = "explanation"
    REFERENCE_TEST = "reference_test"


class SelectionReason(str, Enum):
    """Why a piece of context was chosen."""

    EXPLICIT_REFERENCE = "explicit_reference"
    FULL_TEXT_SEARCH = "full_text_search"
    #: found by searching the tester's free task description, not the
    #: requirement. Kept apart so its contribution can be measured.
    TASK_SEARCH = "task_search"


class QuestionOrigin(str, Enum):
    MODEL = "model"
    VALIDATION = "validation"
    REVIEWER = "reviewer"


class ReviewAction(str, Enum):
    """What a reviewer did. Every entry is kept for traceability."""

    CREATED = "created"
    EDITED = "edited"
    APPROVED = "approved"
    REJECTED = "rejected"
    CLARIFICATION_REQUESTED = "clarification_requested"
    REOPENED = "reopened"
    SOURCE_CHANGED = "source_changed"
    QUESTION_RESOLVED = "question_resolved"


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
    #: the identifier the document declares for itself, if it does
    declared_id: str | None = None
    #: the version the document declares for itself, if it does
    version: str | None = None

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
    #: external IDs mentioned inside this requirement's text - references to
    #: other requirements, never definitions of new ones
    references: tuple[str, ...] = ()

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
    #: ``None``: the quantity is named, no target value is given
    value: str | None = None
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
    """A question that blocks approval until a reviewer clears it.

    A cleared question is marked, never deleted: the answer and the reason are
    part of the record.
    """

    question_id: str
    question: str
    requirement_ids: tuple[str, ...] = ()
    origin: QuestionOrigin = QuestionOrigin.MODEL
    suggestion: str | None = None  # explicitly marked proposal, never a spec
    resolved: bool = False
    answer: str | None = None
    resolved_by: str | None = None
    resolved_at: str | None = None

    def resolve(self, answer: str, *, by: str, at: str) -> "OpenQuestion":
        if not answer.strip():
            raise ValueError("a question is cleared with an answer, not with silence")
        return replace(
            self, resolved=True, answer=answer.strip(), resolved_by=by, resolved_at=at
        )

    def reopen(self) -> "OpenQuestion":
        return replace(self, resolved=False, resolved_by=None, resolved_at=None)


@dataclass(frozen=True)
class AcceptedFinding:
    """A clarification finding a reviewer has deliberately accepted.

    Errors can never be accepted. A clarification can: the reason why it is
    acceptable - a clarification mail, a decision, a known deviation - is part
    of the record, so the approval stays explainable.
    """

    code: str
    path: str
    justification: str
    by: str
    at: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.code, self.path)


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
    #: clarification findings the reviewer accepted, with their reason
    accepted_findings: tuple[AcceptedFinding, ...] = ()
    #: set when the requirements behind this draft changed after it was written
    needs_recheck: bool = False
    #: free-text note of the last review decision
    review_note: str | None = None

    def with_status(self, status: ReviewStatus) -> "TestCase":
        return replace(self, review_status=status)

    def next_revision(self, **changes) -> "TestCase":
        """Any edit produces a new revision and drops a previous approval.

        Accepted findings do not carry over: the new text has to be judged
        again, so an acceptance can never silently cover a new defect.
        """
        return replace(
            self,
            revision=self.revision + 1,
            review_status=ReviewStatus.DRAFT,
            accepted_findings=(),
            **changes,
        )

    @property
    def accepted_keys(self) -> set[tuple[str, str]]:
        return {finding.key for finding in self.accepted_findings}

    @property
    def unresolved_questions(self) -> tuple["OpenQuestion", ...]:
        return tuple(q for q in self.open_questions if not q.resolved)

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
class ContextItem:
    """One supporting requirement, with everything needed to trace it back."""

    requirement_id: str
    external_id: str | None
    text: str
    document_id: str
    spans: tuple[SourceSpan, ...] = ()
    cited_by: tuple[str, ...] = ()
    document_filename: str | None = None
    declared_document_id: str | None = None
    document_version: str | None = None
    content_hash: str | None = None
    kind: ContextKind = ContextKind.REQUIREMENT
    reason: SelectionReason = SelectionReason.EXPLICIT_REFERENCE
    #: what the search matched on, when it came from the full-text index
    matched_terms: tuple[str, ...] = ()
    heading: str | None = None

    @property
    def label(self) -> str:
        parts = [self.external_id or self.requirement_id, self.kind.value]
        if self.heading:
            parts.append(self.heading)
        if self.document_filename:
            parts.append(self.document_filename)
        if self.declared_document_id:
            parts.append(self.declared_document_id)
        if self.document_version:
            parts.append(f"version {self.document_version}")
        if self.content_hash:
            parts.append(f"hash {self.content_hash[:12]}")
        return ", ".join(parts)

    @property
    def size(self) -> int:
        return len(self.text)


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
    #: the supporting context exactly as it was sent, stored verbatim so the
    #: run stays explainable after the documents change
    context: tuple[ContextItem, ...] = ()
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
    #: the free task description a user gave for this run, if any
    task_description: str | None = None
    #: the project guidance that was appended to the contract rules
    domain_guidance: str | None = None
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
    def context_items(self) -> list[ContextItem]:
        """Every context item that was sent during this run."""
        return [item for unit in self.units for item in unit.context]

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
            f"  supporting context items sent={len(self.context_items)}",
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


@dataclass(frozen=True)
class ReviewEvent:
    """One entry of the review history. Never removed, only appended."""

    action: ReviewAction
    case_id: str
    revision: int
    at: str
    by: str | None = None
    note: str | None = None
    status_before: ReviewStatus | None = None
    status_after: ReviewStatus | None = None
    #: why a draft was sent back, for later comparison of generation settings
    categories: tuple[str, ...] = ()


@dataclass
class SpecificationMeta:
    """Heading data of the test specification a project produces."""

    title: str = "Test specification"
    document_id: str = "TS-001"
    revision: str = "1"
    objective: str = ""
    scope: str = ""


@dataclass
class Project:
    """Everything a reviewer needs to continue work later.

    Holds the original documents, the captured requirements, the current test
    drafts, every earlier revision of them, the runs that produced them and the
    review history.
    """

    project_id: str
    name: str
    created_at: str
    documents: list[SourceDocument] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    test_cases: list[TestCase] = field(default_factory=list)
    #: previous revisions, newest last, keyed by case id
    revisions: dict[str, list[TestCase]] = field(default_factory=dict)
    runs: list[GenerationRun] = field(default_factory=list)
    history: list[ReviewEvent] = field(default_factory=list)
    open_questions: list[OpenQuestion] = field(default_factory=list)
    specification: SpecificationMeta = field(default_factory=SpecificationMeta)

    # ------------------------------------------------------------- lookup
    def case(self, case_id: str) -> TestCase | None:
        return next((c for c in self.test_cases if c.case_id == case_id), None)

    def requirement(self, requirement_id: str) -> Requirement | None:
        return next(
            (r for r in self.requirements if r.requirement_id == requirement_id), None
        )

    def document(self, document_id: str) -> SourceDocument | None:
        return next((d for d in self.documents if d.document_id == document_id), None)

    def cases_for(self, requirement_id: str) -> list[TestCase]:
        return [c for c in self.test_cases if requirement_id in c.requirement_ids]

    def history_of(self, case_id: str) -> list[ReviewEvent]:
        return [event for event in self.history if event.case_id == case_id]

    # ------------------------------------------------------------ counting
    def count_by_status(self) -> dict[str, int]:
        counts = {status.value: 0 for status in ReviewStatus}
        for case in self.test_cases:
            counts[case.review_status.value] += 1
        return counts

    @property
    def approved_cases(self) -> list[TestCase]:
        return [c for c in self.test_cases if c.review_status is ReviewStatus.APPROVED]

    @property
    def cases_needing_recheck(self) -> list[TestCase]:
        return [c for c in self.test_cases if c.needs_recheck]

    def rework_statistics(self) -> dict[str, object]:
        """How much manual work the drafts needed.

        Recorded so two model or retrieval configurations can be compared on
        more than the approval rate: how often a draft was edited before it
        was accepted, and what reviewers objected to.
        """
        edits = [e for e in self.history if e.action is ReviewAction.EDITED]
        rejections = [e for e in self.history if e.action is ReviewAction.REJECTED]
        categories: dict[str, int] = {}
        for event in self.history:
            for category in event.categories:
                categories[category] = categories.get(category, 0) + 1
        approved = self.approved_cases
        revisions_at_approval = [case.revision for case in approved]
        return {
            "cases": len(self.test_cases),
            "approved": len(approved),
            "rejected": sum(
                1 for c in self.test_cases if c.review_status is ReviewStatus.REJECTED
            ),
            "edits": len(edits),
            "rejections": len(rejections),
            "edits_per_case": (
                round(len(edits) / len(self.test_cases), 2) if self.test_cases else 0
            ),
            "mean_revision_at_approval": (
                round(sum(revisions_at_approval) / len(revisions_at_approval), 2)
                if revisions_at_approval
                else 0
            ),
            "finding_categories": categories,
            "open_questions_resolved": sum(
                1
                for case in self.test_cases
                for question in case.open_questions
                if question.resolved
            ),
        }

    def replace_case(self, case: TestCase) -> None:
        """Replace a case in place, keeping the previous one as a revision."""
        for index, existing in enumerate(self.test_cases):
            if existing.case_id == case.case_id:
                if existing.revision != case.revision:
                    self.revisions.setdefault(case.case_id, []).append(existing)
                self.test_cases[index] = case
                return
        self.test_cases.append(case)


def spans_of(requirements: Sequence[Requirement]) -> tuple[SourceSpan, ...]:
    return tuple(span for req in requirements for span in req.spans)
