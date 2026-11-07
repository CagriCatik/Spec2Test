"""The test specification: the deliverable, built from the stored data.

There is no second, separately generated text. Everything here is derived
from the project - requirements, drafts, revisions, findings, questions and
the context that was actually sent - so what a reader sees is what the review
decided.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from ..domain.models import (
    ContextItem,
    OpenQuestion,
    Project,
    Requirement,
    ReviewStatus,
    TestCase,
    ValidationIssue,
)
from ..domain.validation import validate_test_case


@dataclass
class RequirementState:
    """One requirement with the state of its coverage."""

    requirement: Requirement
    cases: list[TestCase] = field(default_factory=list)
    document_filename: str | None = None
    document_version: str | None = None
    content_hash: str | None = None
    #: a run was asked to generate for this requirement
    in_scope: bool = False

    @property
    def approved(self) -> list[TestCase]:
        return [c for c in self.cases if c.review_status is ReviewStatus.APPROVED]

    @property
    def has_draft(self) -> bool:
        return bool(self.cases)

    @property
    def label(self) -> str:
        return self.requirement.external_id or self.requirement.display_id()


@dataclass
class SpecificationEntry:
    """One test case as it appears in the specification."""

    case: TestCase
    requirements: list[Requirement] = field(default_factory=list)
    findings: list[ValidationIssue] = field(default_factory=list)
    context: list[ContextItem] = field(default_factory=list)

    @property
    def unresolved_questions(self) -> list[OpenQuestion]:
        return list(self.case.unresolved_questions)


@dataclass
class TestSpecification:
    """The complete deliverable."""

    title: str
    document_id: str
    revision: str
    created_at: str
    objective: str = ""
    scope: str = ""
    project_name: str = ""
    entries: list[SpecificationEntry] = field(default_factory=list)
    requirement_states: list[RequirementState] = field(default_factory=list)
    project_questions: list[OpenQuestion] = field(default_factory=list)
    runs: list[dict] = field(default_factory=list)
    approved_only: bool = False

    # ------------------------------------------------------------ views
    @property
    def cases(self) -> list[TestCase]:
        return [entry.case for entry in self.entries]

    @property
    def uncovered(self) -> list[RequirementState]:
        """In scope, but no draft came out of it - a real gap."""
        return [
            state
            for state in self.requirement_states
            if state.in_scope and not state.has_draft
        ]

    @property
    def out_of_scope(self) -> list[RequirementState]:
        """Known to the project, never asked for - context, not a gap."""
        return [
            state
            for state in self.requirement_states
            if not state.in_scope and not state.has_draft
        ]

    @property
    def without_approval(self) -> list[RequirementState]:
        return [
            state
            for state in self.requirement_states
            if state.has_draft and not state.approved
        ]

    def counts(self) -> dict[str, int]:
        by_status = {status.value: 0 for status in ReviewStatus}
        for entry in self.entries:
            by_status[entry.case.review_status.value] += 1
        return {
            "requirements": len(self.requirement_states),
            "test_cases": len(self.entries),
            **by_status,
            "requirements_in_scope": sum(
                1 for state in self.requirement_states if state.in_scope
            ),
            "uncovered_requirements": len(self.uncovered),
            "out_of_scope_requirements": len(self.out_of_scope),
            "open_questions": sum(
                len(entry.unresolved_questions) for entry in self.entries
            )
            + len([q for q in self.project_questions if not q.resolved]),
            "findings": sum(len(entry.findings) for entry in self.entries),
            "cases_with_findings": sum(
                1 for entry in self.entries if entry.findings
            ),
            "context_items": sum(len(entry.context) for entry in self.entries),
        }

    def coverage_note(self) -> str:
        """What the numbers mean - and what they do not."""
        counts = self.counts()
        covered = counts["requirements_in_scope"] - counts["uncovered_requirements"]
        return (
            f"{counts['test_cases']} draft(s) for {covered} of "
            f"{counts['requirements_in_scope']} requirement(s) in scope, "
            f"{counts['approved']} approved. "
            f"{counts['out_of_scope_requirements']} further requirement(s) of the "
            "knowledge base were never in scope and are not a gap. "
            f"{counts['cases_with_findings']} case(s) carry "
            f"{counts['findings']} validation finding(s) and "
            f"{counts['open_questions']} question(s) are unanswered. A linked "
            "draft is not proof of functional coverage, and an open finding or "
            "question means the case is not ready for use."
        )


def build_specification(
    project: Project, *, approved_only: bool = False
) -> TestSpecification:
    """Assemble the specification from the project as it stands."""
    meta = project.specification
    by_requirement = {r.requirement_id: r for r in project.requirements}
    documents = {d.document_id: d for d in project.documents}
    context_by_case: dict[str, list[ContextItem]] = {}
    for run in project.runs:
        for unit in run.units:
            for case in unit.test_cases:
                context_by_case.setdefault(case.case_id, []).extend(unit.context)

    entries: list[SpecificationEntry] = []
    for case in project.test_cases:
        if approved_only and case.review_status is not ReviewStatus.APPROVED:
            continue
        linked = [
            by_requirement[rid] for rid in case.requirement_ids if rid in by_requirement
        ]
        report = validate_test_case(
            case,
            project.requirements,
            context=context_by_case.get(case.case_id, []),
        )
        accepted = case.accepted_keys
        findings = [
            issue
            for issue in report.issues
            if (issue.code, issue.path) not in accepted
        ]
        entries.append(
            SpecificationEntry(
                case=case,
                requirements=linked,
                findings=findings,
                context=context_by_case.get(case.case_id, []),
            )
        )

    scope_ids = {
        requirement_id
        for run in project.runs
        for unit in run.units
        for requirement_id in unit.requirement_ids
    }
    states: list[RequirementState] = []
    for requirement in project.requirements:
        document = documents.get(
            requirement.spans[0].document_id if requirement.spans else ""
        )
        states.append(
            RequirementState(
                requirement=requirement,
                cases=[
                    entry.case
                    for entry in entries
                    if requirement.requirement_id in entry.case.requirement_ids
                ],
                document_filename=document.filename if document else None,
                document_version=document.version if document else None,
                content_hash=document.content_hash if document else None,
                in_scope=requirement.requirement_id in scope_ids,
            )
        )

    return TestSpecification(
        title=meta.title,
        document_id=meta.document_id,
        revision=meta.revision,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        objective=meta.objective,
        scope=meta.scope,
        project_name=project.name,
        entries=entries,
        requirement_states=states,
        project_questions=list(project.open_questions),
        runs=[
            {
                "run_id": run.run_id,
                "model": run.model,
                "endpoint": run.endpoint,
                "mode": run.mode,
                "status": run.status.value,
                "prompt_version": run.prompt_version,
                "schema_version": run.schema_version,
                "task_description": run.task_description,
                "domain_guidance": run.domain_guidance,
                "context_items": len(run.context_items),
            }
            for run in project.runs
        ],
        approved_only=approved_only,
    )


def steps_of(case: TestCase) -> list[tuple[str, int, str, Sequence[str]]]:
    """Flatten the three step lists, keeping which list a step belongs to."""
    rows: list[tuple[str, int, str, Sequence[str]]] = []
    for label, steps in (
        ("precondition", case.preconditions),
        ("step", case.steps),
        ("postcondition", case.postconditions),
    ):
        for step in steps:
            rows.append((label, step.order, step.action, step.expectations))
    return rows
