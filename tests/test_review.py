"""AP 5: revisions, clarification, approval and source changes."""
from __future__ import annotations

import pytest

from spec2test.application.review import (
    ApprovalBlocked,
    CaseEdit,
    ReviewError,
    accept_finding,
    add_question,
    add_run,
    apply_requirement_changes,
    approval_report,
    approve_case,
    edit_case,
    project_from_run,
    reject_case,
    reopen_case,
    request_clarification,
    resolve_question,
    revisions_of,
    revoke_finding,
    unresolved_questions,
    validation_overview,
)
from spec2test.domain.models import (
    GenerationRun,
    OpenQuestion,
    Requirement,
    ReviewAction,
    ReviewStatus,
    RunStatus,
    SourceDocument,
    DocumentFormat,
    SourceSpan,
    TestCase,
    TestStep,
    UnitStatus,
    GenerationUnitResult,
)

TEXT = "SYS-REQ-0001: The system shall react within 100 ms."


def requirement(rid="REQ-1", ext="SYS-REQ-0001", text=TEXT) -> Requirement:
    return Requirement(
        rid, text, (SourceSpan("doc-1", 0, len(text), text, page=1),), ext
    )


def case(case_id="TC-1", **kwargs) -> TestCase:
    base = dict(
        case_id=case_id,
        title="Check the reaction time",
        objective="verify the timing",
        requirement_ids=("REQ-1",),
        steps=(TestStep(1, "trigger", ("the reaction takes 100 ms",)),),
    )
    base.update(kwargs)
    return TestCase(**base)


def run_with(*cases, status=RunStatus.COMPLETED) -> GenerationRun:
    run = GenerationRun(
        run_id="run-1",
        model="m",
        endpoint="e",
        mode="fake",
        prompt_version="p",
        schema_version="1.0",
        source_hash="h",
        planned_unit_count=1,
    )
    unit = GenerationUnitResult("U1", ("REQ-1",), UnitStatus.ACCEPTED)
    unit.test_cases = list(cases)
    run.units = [unit]
    run.status = status
    return run


@pytest.fixture
def project():
    document = SourceDocument(
        "doc-1", "spec.md", DocumentFormat.MARKDOWN, "hash", TEXT, page_count=1
    )
    return project_from_run(
        run_with(case()), [requirement()], [document], name="demo"
    )


# =====================================================================
# project assembly
# =====================================================================
def test_a_project_keeps_sources_requirements_and_drafts(project):
    assert project.documents[0].text == TEXT
    assert project.requirements[0].requirement_id == "REQ-1"
    assert len(project.test_cases) == 1
    assert project.test_cases[0].review_status is ReviewStatus.DRAFT
    assert project.history[0].action is ReviewAction.CREATED


def test_a_further_run_does_not_touch_existing_drafts(project):
    add_run(project, run_with(case("TC-2")), [requirement("REQ-2", "SYS-REQ-0002")])
    assert [c.case_id for c in project.test_cases] == ["TC-1", "TC-2"]
    assert len(project.requirements) == 2
    assert len(project.runs) == 2


def test_counting_by_status(project):
    assert project.count_by_status()["draft"] == 1
    approve_case(project, "TC-1")
    assert project.count_by_status()["approved"] == 1


# =====================================================================
# editing creates revisions
# =====================================================================
def test_an_edit_creates_a_new_revision(project):
    edited = edit_case(project, "TC-1", CaseEdit(title="Sharper title"))
    assert edited.revision == 2
    assert edited.title == "Sharper title"
    assert project.case("TC-1").title == "Sharper title"


def test_earlier_revisions_are_kept(project):
    edit_case(project, "TC-1", CaseEdit(title="Second"))
    edit_case(project, "TC-1", CaseEdit(title="Third"))
    history = revisions_of(project, "TC-1")
    assert [c.title for c in history] == [
        "Check the reaction time",
        "Second",
        "Third",
    ]
    assert [c.revision for c in history] == [1, 2, 3]


def test_an_approval_does_not_carry_over_to_a_new_revision(project):
    approve_case(project, "TC-1")
    assert project.case("TC-1").review_status is ReviewStatus.APPROVED

    edited = edit_case(project, "TC-1", CaseEdit(objective="changed objective"))
    assert edited.review_status is ReviewStatus.DRAFT
    assert edited.revision == 2
    # the approved revision is still on record
    assert revisions_of(project, "TC-1")[0].review_status is ReviewStatus.APPROVED


def test_editing_steps_is_possible(project):
    new_steps = (
        TestStep(1, "set the speed to 50 km/h", ("the speed is 50 km/h",)),
        TestStep(2, "trigger the event", ("the reaction takes 100 ms",)),
    )
    edited = edit_case(project, "TC-1", CaseEdit(steps=new_steps))
    assert len(edited.steps) == 2
    assert edited.steps[1].expectations == ("the reaction takes 100 ms",)


def test_an_empty_edit_is_refused(project):
    with pytest.raises(ReviewError):
        edit_case(project, "TC-1", CaseEdit())


def test_editing_an_unknown_case_is_refused(project):
    with pytest.raises(ReviewError):
        edit_case(project, "TC-999", CaseEdit(title="x"))


def test_every_edit_is_recorded_with_its_revision(project):
    edit_case(project, "TC-1", CaseEdit(title="Second"), by="anna", note="clearer")
    event = project.history_of("TC-1")[-1]
    assert event.action is ReviewAction.EDITED
    assert event.revision == 2
    assert event.by == "anna"
    assert event.note == "clearer"


# =====================================================================
# open questions
# =====================================================================
def test_a_question_is_marked_resolved_not_deleted(project):
    with_question = case(
        open_questions=(OpenQuestion("Q1", "which tolerance applies?"),)
    )
    project.test_cases = [with_question]

    updated = resolve_question(
        project, "TC-1", "Q1", "±0.5 ms per clarification mail", by="anna"
    )
    question = updated.open_questions[0]
    assert len(updated.open_questions) == 1, "the question must not disappear"
    assert question.resolved is True
    assert question.answer == "±0.5 ms per clarification mail"
    assert question.resolved_by == "anna"
    assert question.resolved_at
    assert updated.unresolved_questions == ()


def test_resolving_does_not_create_a_revision(project):
    project.test_cases = [case(open_questions=(OpenQuestion("Q1", "which limit?"),))]
    updated = resolve_question(project, "TC-1", "Q1", "10 deg")
    assert updated.revision == 1, "clearing a question is not a content change"
    assert project.history_of("TC-1")[-1].action is ReviewAction.QUESTION_RESOLVED


def test_an_empty_answer_is_refused(project):
    project.test_cases = [case(open_questions=(OpenQuestion("Q1", "which limit?"),))]
    with pytest.raises(ValueError):
        resolve_question(project, "TC-1", "Q1", "   ")


def test_an_unknown_question_is_refused(project):
    with pytest.raises(ReviewError):
        resolve_question(project, "TC-1", "Q-nope", "answer")


def test_a_reviewer_can_add_a_question(project):
    updated = add_question(project, "TC-1", "Is the cold start covered?", by="anna")
    assert updated.open_questions[-1].origin.value == "reviewer"
    assert updated.unresolved_questions
    with pytest.raises(ApprovalBlocked):
        approve_case(project, "TC-1")


def test_unresolved_questions_are_listed_across_the_project(project):
    project.test_cases = [
        case(open_questions=(OpenQuestion("Q1", "a?"), OpenQuestion("Q2", "b?")))
    ]
    resolve_question(project, "TC-1", "Q1", "answered")
    remaining = list(unresolved_questions(project))
    assert [q.question_id for _case_id, q in remaining] == ["Q2"]


# =====================================================================
# approval
# =====================================================================
def test_a_clean_draft_can_be_approved(project):
    approved = approve_case(project, "TC-1", by="anna", note="checked against spec")
    assert approved.review_status is ReviewStatus.APPROVED
    event = project.history_of("TC-1")[-1]
    assert event.action is ReviewAction.APPROVED
    assert event.by == "anna"
    assert event.revision == approved.revision
    assert event.at


def test_approval_revalidates_and_refuses_an_unknown_requirement(project):
    project.test_cases = [case(requirement_ids=("REQ-DOES-NOT-EXIST",))]
    allowed, report = approval_report(project, "TC-1")
    assert allowed is False
    assert "E_UNKNOWN_REQUIREMENT_REF" in report.codes()
    with pytest.raises(ApprovalBlocked) as excinfo:
        approve_case(project, "TC-1")
    assert "E_UNKNOWN_REQUIREMENT_REF" in excinfo.value.report.codes()


def test_an_unresolved_question_blocks_approval(project):
    project.test_cases = [case(open_questions=(OpenQuestion("Q1", "which limit?"),))]
    with pytest.raises(ApprovalBlocked):
        approve_case(project, "TC-1")

    resolve_question(project, "TC-1", "Q1", "10 deg, confirmed")
    assert approve_case(project, "TC-1").review_status is ReviewStatus.APPROVED


def test_a_rejection_needs_a_reason(project):
    with pytest.raises(ReviewError):
        reject_case(project, "TC-1")
    rejected = reject_case(
        project, "TC-1", note="wrong signal", categories=("wrong-signal",)
    )
    assert rejected.review_status is ReviewStatus.REJECTED
    assert project.history_of("TC-1")[-1].categories == ("wrong-signal",)


def test_clarification_and_reopening(project):
    waiting = request_clarification(project, "TC-1", note="ask the supplier")
    assert waiting.review_status is ReviewStatus.NEEDS_CLARIFICATION
    back = reopen_case(project, "TC-1", note="answer received")
    assert back.review_status is ReviewStatus.DRAFT
    assert [e.action for e in project.history_of("TC-1")][-2:] == [
        ReviewAction.CLARIFICATION_REQUESTED,
        ReviewAction.REOPENED,
    ]


# =====================================================================
# source changes
# =====================================================================
def test_a_changed_requirement_marks_the_draft_for_a_fresh_review(project):
    approve_case(project, "TC-1")
    changed = requirement(text="SYS-REQ-0001: The system shall react within 50 ms.")

    touched = apply_requirement_changes(project, [changed])

    assert [c.case_id for c in touched] == ["TC-1"]
    current = project.case("TC-1")
    assert current.needs_recheck is True
    assert current.review_status is ReviewStatus.DRAFT, "the approval is withdrawn"
    assert project.history_of("TC-1")[-1].action is ReviewAction.SOURCE_CHANGED
    assert project.requirements[0].text.endswith("50 ms.")


def test_a_draft_marked_for_recheck_cannot_be_approved(project):
    apply_requirement_changes(
        project, [requirement(text="SYS-REQ-0001: changed within 50 ms.")]
    )
    with pytest.raises(ApprovalBlocked):
        approve_case(project, "TC-1")


def test_an_unchanged_requirement_leaves_the_draft_alone(project):
    approve_case(project, "TC-1")
    assert apply_requirement_changes(project, [requirement()]) == []
    assert project.case("TC-1").review_status is ReviewStatus.APPROVED


def test_a_removed_requirement_is_reported(project):
    touched = apply_requirement_changes(project, [])
    assert touched and touched[0].needs_recheck
    assert "removed" in project.history_of("TC-1")[-1].note


def test_an_edit_clears_the_recheck_flag(project):
    apply_requirement_changes(
        project, [requirement(text="SYS-REQ-0001: changed within 50 ms.")]
    )
    edited = edit_case(project, "TC-1", CaseEdit(title="adjusted to 50 ms"))
    assert edited.needs_recheck is False


# =====================================================================
# overview and metrics
# =====================================================================
def test_the_validation_overview_is_recomputed_from_stored_data(project):
    overview = validation_overview(project)
    assert set(overview) == {"TC-1"}
    assert overview["TC-1"].blocks_draft is False


def test_rework_statistics_describe_the_manual_effort(project):
    edit_case(project, "TC-1", CaseEdit(title="Second"))
    reject_case(project, "TC-1", note="wrong channel", categories=("wrong-signal",))
    reopen_case(project, "TC-1", note="fixed")
    edit_case(project, "TC-1", CaseEdit(title="Third"))
    approve_case(project, "TC-1")

    stats = project.rework_statistics()
    assert stats["cases"] == 1
    assert stats["approved"] == 1
    assert stats["edits"] == 2
    assert stats["rejections"] == 1
    assert stats["finding_categories"] == {"wrong-signal": 1}
    assert stats["mean_revision_at_approval"] == 3


# =====================================================================
# accepting a finding with a reason
# =====================================================================
def _vague_case():
    return case(
        steps=(TestStep(1, "trigger", ("the system behaves correctly",)),),
    )


def test_a_clarification_can_be_accepted_with_a_reason(project):
    project.test_cases = [_vague_case()]
    allowed, report = approval_report(project, "TC-1")
    assert allowed is False
    finding = next(i for i in report.issues if i.code == "C_NO_MEASURABLE_CRITERION")

    updated = accept_finding(
        project,
        "TC-1",
        finding.code,
        finding.path,
        "measured manually on the rig, documented in the test concept",
        by="anna",
    )
    assert updated.accepted_findings[0].justification.startswith("measured manually")
    assert updated.accepted_findings[0].by == "anna"
    assert updated.accepted_findings[0].at

    allowed, _ = approval_report(project, "TC-1")
    assert allowed is True
    assert approve_case(project, "TC-1").review_status is ReviewStatus.APPROVED


def test_accepting_needs_a_reason(project):
    project.test_cases = [_vague_case()]
    with pytest.raises(ReviewError):
        accept_finding(project, "TC-1", "C_NO_MEASURABLE_CRITERION", "x", "   ")


def test_an_error_can_never_be_accepted(project):
    project.test_cases = [case(requirement_ids=("REQ-UNKNOWN",))]
    _allowed, report = approval_report(project, "TC-1")
    error = next(i for i in report.issues if i.code == "E_UNKNOWN_REQUIREMENT_REF")
    with pytest.raises(ReviewError) as excinfo:
        accept_finding(project, "TC-1", error.code, error.path, "we know")
    assert "cannot be accepted" in str(excinfo.value)


def test_accepting_a_finding_that_does_not_exist_is_refused(project):
    with pytest.raises(ReviewError):
        accept_finding(project, "TC-1", "C_MADE_UP", "$", "because")


def test_an_acceptance_can_be_withdrawn(project):
    project.test_cases = [_vague_case()]
    _allowed, report = approval_report(project, "TC-1")
    finding = next(i for i in report.issues if i.code == "C_NO_MEASURABLE_CRITERION")
    accept_finding(project, "TC-1", finding.code, finding.path, "documented")
    assert approval_report(project, "TC-1")[0] is True

    revoke_finding(project, "TC-1", finding.code, finding.path)
    assert approval_report(project, "TC-1")[0] is False
    with pytest.raises(ReviewError):
        revoke_finding(project, "TC-1", finding.code, finding.path)


def test_an_edit_clears_previous_acceptances(project):
    project.test_cases = [_vague_case()]
    _allowed, report = approval_report(project, "TC-1")
    finding = next(i for i in report.issues if i.code == "C_NO_MEASURABLE_CRITERION")
    accept_finding(project, "TC-1", finding.code, finding.path, "documented")

    edited = edit_case(project, "TC-1", CaseEdit(title="new title"))
    assert edited.accepted_findings == (), (
        "a new revision must be judged again, not inherit an acceptance"
    )
    assert approval_report(project, "TC-1")[0] is False


def test_the_reason_for_an_acceptance_is_in_the_history(project):
    project.test_cases = [_vague_case()]
    _allowed, report = approval_report(project, "TC-1")
    finding = next(i for i in report.issues if i.code == "C_NO_MEASURABLE_CRITERION")
    accept_finding(project, "TC-1", finding.code, finding.path, "rig procedure 4.2")
    event = project.history_of("TC-1")[-1]
    assert "rig procedure 4.2" in event.note
    assert event.categories == ("C_NO_MEASURABLE_CRITERION",)
