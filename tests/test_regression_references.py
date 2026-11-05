"""Regression tests for the review of 96e78c4.

P1  references were imported as new requirements
P1  several runs produced duplicate test case IDs in one project
P1  open questions could survive an approval
"""
from __future__ import annotations

import pytest

from spec2test.adapters.documents import TextDocumentImporter
from spec2test.adapters.fake.model import RuleBasedFakeModel
from spec2test.application.generate import generate_drafts, plan_units, GenerationSettings
from spec2test.application.ingest import extract_requirements, reference_report
from spec2test.application.review import (
    ApprovalBlocked,
    ReviewError,
    add_project_question,
    add_question,
    add_run,
    approval_report,
    approve_case,
    project_from_run,
    recheck_all,
    resolve_project_question,
    revoke_finding,
    accept_finding,
)
from spec2test.domain.models import (
    DocumentFormat,
    QuestionOrigin,
    Requirement,
    ReviewStatus,
    SourceDocument,
    SourceSpan,
    TestCase,
    TestStep,
)


def ingest_text(tmp_path, text: str, name: str = "spec.md"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return extract_requirements(TextDocumentImporter().load(path))


# =====================================================================
# 1  definitions and references
# =====================================================================
def test_a_reference_in_running_text_is_not_a_new_requirement(tmp_path):
    result = ingest_text(
        tmp_path,
        "### Requirement: BMS-REQ-AFE-0103\n"
        "The software shall report a sensor fault as specified in "
        "BMS-REQ-THM-0403 within 100 ms.\n"
        "The reaction shall be logged.\n",
    )
    assert [r.external_id for r in result.requirements] == ["BMS-REQ-AFE-0103"]
    requirement = result.requirements[0]
    assert requirement.references == ("BMS-REQ-THM-0403",)
    assert requirement.text.rstrip().endswith("logged."), (
        "the requirement was cut off at the reference"
    )


@pytest.mark.parametrize(
    "line",
    [
        "BMS-REQ-AFE-0101: the ADC shall sample every 10 ms",
        "### Requirement: BMS-REQ-AFE-0101",
        "## BMS-REQ-AFE-0101",
        "**BMS-REQ-AFE-0101** the ADC shall sample",
        "- BMS-REQ-AFE-0101: the ADC shall sample",
        "Requirement BMS-REQ-AFE-0101 - the ADC shall sample",
    ],
)
def test_the_usual_definition_forms_are_recognised(tmp_path, line):
    result = ingest_text(tmp_path, line + "\nThe software shall do it.\n")
    assert [r.external_id for r in result.requirements] == ["BMS-REQ-AFE-0101"]


@pytest.mark.parametrize(
    "sentence",
    [
        "The software shall behave as described in BMS-REQ-THM-0403.",
        "See BMS-REQ-THM-0403 for the fault reaction.",
        "Both BMS-REQ-THM-0403 and BMS-REQ-PWR-0905 apply here.",
    ],
)
def test_an_id_inside_a_sentence_stays_a_reference(tmp_path, sentence):
    result = ingest_text(
        tmp_path, f"### Requirement: SYS-REQ-0001\n{sentence}\n"
    )
    assert [r.external_id for r in result.requirements] == ["SYS-REQ-0001"]
    assert "BMS-REQ-THM-0403" in result.requirements[0].references


def test_a_document_with_references_only_is_reported(tmp_path):
    result = ingest_text(
        tmp_path,
        "# Notes\n\nThe interface shall follow BMS-REQ-AFE-0101 exactly.\n",
    )
    codes = {issue.code for issue in result.issues}
    assert "C_NO_REQUIREMENT_DEFINITION" in codes


def test_references_are_resolved_to_their_definition(tmp_path):
    first = ingest_text(
        tmp_path,
        "### Requirement: BMS-REQ-AFE-0103\nThe software shall follow "
        "BMS-REQ-THM-0403 and shall log it.\n",
        name="a.md",
    )
    second = ingest_text(
        tmp_path,
        "### Requirement: BMS-REQ-THM-0403\nThe software shall detect runaway "
        "within 2 s.\n",
        name="b.md",
    )
    report = reference_report([*first.requirements, *second.requirements])

    assert report.unresolved == []
    target = report.resolve("BMS-REQ-THM-0403")
    assert target is not None and target.text.startswith("BMS-REQ-THM-0403")
    assert [r.external_id for r in report.citations["BMS-REQ-THM-0403"]] == [
        "BMS-REQ-AFE-0103"
    ]


def test_a_reference_to_nothing_is_reported(tmp_path):
    result = ingest_text(
        tmp_path,
        "### Requirement: SYS-REQ-0001\nThe system shall follow SYS-REQ-9999.\n",
    )
    report = reference_report(result.requirements)
    assert [target for _r, target in report.unresolved] == ["SYS-REQ-9999"]
    assert "C_UNRESOLVED_REFERENCE" in {i.code for i in report.issues()}


def test_a_duplicate_definition_is_reported(tmp_path):
    first = ingest_text(
        tmp_path, "SYS-REQ-0001: the system shall start within 2 s.\n", name="a.md"
    )
    second = ingest_text(
        tmp_path, "SYS-REQ-0001: the system shall stop within 3 s.\n", name="b.md"
    )
    report = reference_report([*first.requirements, *second.requirements])
    assert list(report.duplicate_definitions) == ["SYS-REQ-0001"]
    assert report.resolve("SYS-REQ-0001") is None, "an ambiguous ID must not resolve"
    assert "C_DUPLICATE_DEFINITION" in {i.code for i in report.issues()}


def test_the_shipped_corpus_has_unique_definitions():
    from pathlib import Path

    folder = Path(__file__).resolve().parents[1] / "requirements"
    documents = sorted(folder.glob("*.md"))
    if not documents:
        pytest.skip("the repository ships no requirement documents")

    importer = TextDocumentImporter()
    requirements = [
        r
        for path in documents
        for r in extract_requirements(importer.load(path)).requirements
    ]
    report = reference_report(requirements)
    assert report.duplicate_definitions == {}, "a requirement ID is defined twice"
    external = [r.external_id for r in requirements if r.external_id]
    assert len(external) == len(set(external))


# =====================================================================
# 2  unique IDs across runs
# =====================================================================
def requirement(rid="REQ-1", ext="SYS-REQ-0001"):
    text = f"{ext}: The system shall react within 100 ms."
    return Requirement(rid, text, (SourceSpan("doc-1", 0, len(text), text, page=1),), ext)


def document(doc_id="doc-1"):
    return SourceDocument(doc_id, "spec.md", DocumentFormat.MARKDOWN, "h", "text")


def test_two_runs_produce_distinct_test_case_ids():
    requirements = [requirement(), requirement("REQ-2", "SYS-REQ-0002")]
    first = generate_drafts(requirements, RuleBasedFakeModel())
    second = generate_drafts(requirements, RuleBasedFakeModel())

    project = project_from_run(first, requirements, [document()], name="demo")
    add_run(project, second, requirements, [document()])

    ids = [case.case_id for case in project.test_cases]
    assert len(ids) == 4
    assert len(set(ids)) == 4, f"duplicate test case IDs: {ids}"


def test_question_ids_are_unique_across_runs():
    requirements = [requirement()]
    first = generate_drafts(requirements, RuleBasedFakeModel())
    second = generate_drafts(requirements, RuleBasedFakeModel())
    project = project_from_run(first, requirements, [document()], name="demo")
    add_run(project, second, requirements, [document()])

    ids = [
        question.question_id
        for case in project.test_cases
        for question in case.open_questions
    ]
    assert len(ids) == len(set(ids))


def test_merging_the_same_run_twice_is_refused():
    requirements = [requirement()]
    run = generate_drafts(requirements, RuleBasedFakeModel())
    project = project_from_run(run, requirements, [document()], name="demo")
    with pytest.raises(ReviewError) as excinfo:
        add_run(project, run, requirements, [document()])
    assert "already exist" in str(excinfo.value)


def test_a_new_run_brings_its_documents_into_the_project():
    requirements = [requirement()]
    project = project_from_run(
        generate_drafts(requirements, RuleBasedFakeModel()),
        requirements,
        [document("doc-1")],
        name="demo",
    )
    other = [requirement("REQ-9", "SYS-REQ-0009")]
    add_run(
        project,
        generate_drafts(other, RuleBasedFakeModel()),
        other,
        [document("doc-2")],
    )
    assert sorted(d.document_id for d in project.documents) == ["doc-1", "doc-2"]
    assert project.document("doc-2") is not None


def test_a_unit_prefix_keeps_planning_testable():
    units = plan_units([requirement()], GenerationSettings())
    assert units[0].unit_id == "U001", "the default stays short"
    prefixed = plan_units([requirement()], GenerationSettings(), prefix="abc123-")
    assert prefixed[0].unit_id == "abc123-U001"


# =====================================================================
# 3  the approval gate
# =====================================================================
def case(case_id="TC-1", **kwargs):
    base = dict(
        case_id=case_id,
        title="t",
        objective="o",
        requirement_ids=("REQ-1",),
        steps=(TestStep(1, "trigger", ("the reaction takes 100 ms",)),),
    )
    base.update(kwargs)
    return TestCase(**base)


@pytest.fixture
def project():
    from spec2test.domain.models import Project

    return Project(
        "p",
        "demo",
        "now",
        requirements=[requirement()],
        test_cases=[case()],
    )


def test_a_project_question_about_the_requirement_blocks_approval(project):
    assert approval_report(project, "TC-1")[0] is True
    question = add_project_question(project, "which tolerance?", ["REQ-1"])
    assert approval_report(project, "TC-1")[0] is False
    with pytest.raises(ApprovalBlocked):
        approve_case(project, "TC-1")

    resolve_project_question(project, question.question_id, "±1 ms confirmed")
    assert approval_report(project, "TC-1")[0] is True
    assert approve_case(project, "TC-1").review_status is ReviewStatus.APPROVED


def test_a_project_question_about_another_requirement_does_not_block(project):
    add_project_question(project, "unrelated", ["REQ-OTHER"])
    assert approval_report(project, "TC-1")[0] is True


def test_a_question_without_a_requirement_blocks_every_draft(project):
    add_project_question(project, "applies to the whole project")
    assert approval_report(project, "TC-1")[0] is False


def test_a_new_question_withdraws_an_existing_approval(project):
    approve_case(project, "TC-1")
    add_question(project, "TC-1", "Is the cold start covered?")

    updated = project.case("TC-1")
    assert updated.review_status is ReviewStatus.DRAFT
    assert "approval withdrawn" in (updated.review_note or "")
    assert project.history_of("TC-1")[-1].status_before is ReviewStatus.APPROVED


def test_a_new_project_question_withdraws_an_existing_approval(project):
    approve_case(project, "TC-1")
    add_project_question(project, "which tolerance?", ["REQ-1"])
    assert project.case("TC-1").review_status is ReviewStatus.DRAFT


def test_withdrawing_an_acceptance_withdraws_the_approval(project):
    project.test_cases = [case(steps=(TestStep(1, "do", ("it works",)),))]
    _allowed, report = approval_report(project, "TC-1")
    finding = next(i for i in report.issues if i.code == "C_NO_MEASURABLE_CRITERION")
    accept_finding(project, "TC-1", finding.code, finding.path, "rig procedure")
    approve_case(project, "TC-1")

    revoke_finding(project, "TC-1", finding.code, finding.path)
    assert project.case("TC-1").review_status is ReviewStatus.DRAFT


def test_recheck_all_withdraws_approvals_that_no_longer_hold(project):
    approve_case(project, "TC-1")
    project.open_questions.append(
        __import__("spec2test.domain.models", fromlist=["OpenQuestion"]).OpenQuestion(
            "PQ-X", "late question", ("REQ-1",), QuestionOrigin.REVIEWER
        )
    )
    withdrawn = recheck_all(project)
    assert [c.case_id for c in withdrawn] == ["TC-1"]
    assert project.case("TC-1").review_status is ReviewStatus.DRAFT


def test_validation_questions_do_not_become_unclearable_project_questions():
    """They are findings of their own draft and are cleared there."""
    requirements = [requirement()]
    run = generate_drafts(requirements, RuleBasedFakeModel())
    project = project_from_run(run, requirements, [document()], name="demo")
    assert all(
        question.origin is QuestionOrigin.MODEL
        for question in project.open_questions
    )
