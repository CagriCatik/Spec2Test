"""AP 5: saving, reopening and protecting a project file."""
from __future__ import annotations

import json

import pytest

from spec2test.adapters.persistence.project_store import (
    PROJECT_FORMAT,
    ProjectFileError,
    ProjectLocked,
    ProjectStore,
    project_from_dict,
    project_to_dict,
)
from spec2test.application.review import (
    CaseEdit,
    approve_case,
    edit_case,
    project_from_run,
    resolve_question,
)
from tests.test_review import case, requirement, run_with
from spec2test.domain.models import (
    DocumentFormat,
    OpenQuestion,
    ReviewStatus,
    SourceDocument,
    TestStep,
)

TEXT = "SYS-REQ-0001: The system shall react within 100 ms."


def document() -> SourceDocument:
    return SourceDocument(
        "doc-1", "spec.md", DocumentFormat.MARKDOWN, "hash", TEXT, page_count=1
    )


@pytest.fixture
def project():
    drafted = case(
        open_questions=(OpenQuestion("Q1", "which tolerance applies?"),),
        preconditions=(TestStep(1, "power up", ("the ECU answers",)),),
    )
    return project_from_run(
        run_with(drafted), [requirement()], [document()], name="demo"
    )


# =====================================================================
# round trip
# =====================================================================
def test_saving_and_reopening_keeps_everything(project, tmp_path):
    path = tmp_path / "demo.s2t.json"
    ProjectStore(path).save(project)
    reopened = ProjectStore(path).load()

    assert reopened.project_id == project.project_id
    assert reopened.name == "demo"
    assert reopened.documents[0].text == TEXT, "the original source is kept"
    assert reopened.documents[0].format is DocumentFormat.MARKDOWN
    assert reopened.requirements[0].external_id == "SYS-REQ-0001"
    assert reopened.requirements[0].spans[0].page == 1

    draft = reopened.test_cases[0]
    assert draft.review_status is ReviewStatus.DRAFT
    assert draft.steps[0].expectations == ("the reaction takes 100 ms",)
    assert draft.preconditions[0].action == "power up"
    assert draft.open_questions[0].question == "which tolerance applies?"
    assert reopened.runs[0].run_id == "run-1"
    assert reopened.history[0].action.value == "created"


def test_review_state_survives_a_reopen(project, tmp_path):
    resolve_question(project, "TC-1", "Q1", "±0.5 ms, confirmed", by="anna")
    edit_case(project, "TC-1", CaseEdit(title="Reaction time at 100 ms"))
    approve_case(project, "TC-1", by="anna", note="checked")

    path = tmp_path / "demo.s2t.json"
    ProjectStore(path).save(project)
    reopened = ProjectStore(path).load()

    draft = reopened.case("TC-1")
    assert draft.review_status is ReviewStatus.APPROVED
    assert draft.revision == 2
    assert draft.title == "Reaction time at 100 ms"
    question = draft.open_questions[0]
    assert question.resolved and question.answer == "±0.5 ms, confirmed"
    assert question.resolved_by == "anna"
    assert len(reopened.revisions["TC-1"]) == 1, "the earlier revision is kept"
    assert reopened.revisions["TC-1"][0].title == "Check the reaction time"
    assert [e.action.value for e in reopened.history_of("TC-1")] == [
        "created",
        "question_resolved",
        "edited",
        "approved",
    ]


def test_findings_of_a_failed_unit_survive(project, tmp_path):
    from spec2test.domain.models import Severity, UnitStatus, ValidationIssue

    unit = project.runs[0].units[0]
    unit.status = UnitStatus.REJECTED
    unit.issues = [ValidationIssue("E_SCHEMA", Severity.ERROR, "$", "broken")]

    path = tmp_path / "demo.s2t.json"
    ProjectStore(path).save(project)
    reopened = ProjectStore(path).load()

    stored = reopened.runs[0].units[0]
    assert stored.status is UnitStatus.REJECTED
    assert stored.issues[0].code == "E_SCHEMA"
    assert stored.issues[0].severity is Severity.ERROR


def test_the_payload_carries_a_format_version(project):
    payload = project_to_dict(project)
    assert payload["project_format"] == PROJECT_FORMAT
    assert "project" in payload


# =====================================================================
# damaged files
# =====================================================================
@pytest.mark.parametrize(
    "content,expected",
    [
        ("{{{", "not valid JSON"),
        ('{"project": {}}', "project_format"),
        ('{"project_format": "99", "project": {}}', "not supported"),
        ('{"project_format": "1"}', "'project' section"),
        ('[1, 2, 3]', "expected a project object"),
    ],
)
def test_a_damaged_file_is_reported_understandably(tmp_path, content, expected):
    path = tmp_path / "broken.s2t.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ProjectFileError) as excinfo:
        ProjectStore(path).load()
    assert expected in str(excinfo.value)


def test_a_wrong_field_type_names_the_field(tmp_path):
    payload = {
        "project_format": "1",
        "project": {
            "project_id": "p",
            "name": "n",
            "created_at": "t",
            "test_cases": "not a list",
        },
    }
    path = tmp_path / "broken.s2t.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProjectFileError) as excinfo:
        ProjectStore(path).load()
    assert "project.test_cases" in str(excinfo.value)


def test_a_missing_file_is_reported(tmp_path):
    with pytest.raises(ProjectFileError) as excinfo:
        ProjectStore(tmp_path / "absent.s2t.json").load()
    assert "does not exist" in str(excinfo.value)


def test_an_unknown_enum_value_is_reported(tmp_path):
    payload = {
        "project_format": "1",
        "project": {
            "project_id": "p",
            "name": "n",
            "created_at": "t",
            "test_cases": [
                {
                    "case_id": "TC-1",
                    "title": "t",
                    "objective": "o",
                    "requirement_ids": ["R1"],
                    "review_status": "blessed",
                }
            ],
        },
    }
    path = tmp_path / "broken.s2t.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProjectFileError) as excinfo:
        ProjectStore(path).load()
    assert "blessed" in str(excinfo.value)


def test_an_unknown_extra_field_does_not_break_reading(tmp_path, project):
    path = tmp_path / "demo.s2t.json"
    ProjectStore(path).save(project)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["project"]["something_from_a_newer_version"] = {"a": 1}
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert ProjectStore(path).load().name == "demo"


# =====================================================================
# writing
# =====================================================================
def test_writing_is_atomic_and_leaves_no_temporary_file(project, tmp_path):
    path = tmp_path / "demo.s2t.json"
    ProjectStore(path).save(project)
    ProjectStore(path).save(project)
    assert [p.name for p in tmp_path.iterdir()] == ["demo.s2t.json"]


def test_a_failed_write_keeps_the_previous_file(project, tmp_path, monkeypatch):
    path = tmp_path / "demo.s2t.json"
    ProjectStore(path).save(project)
    before = path.read_text(encoding="utf-8")

    import os

    monkeypatch.setattr(
        os, "replace", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk full"))
    )
    project.name = "renamed"
    with pytest.raises(OSError):
        ProjectStore(path).save(project)

    assert path.read_text(encoding="utf-8") == before
    assert [p.name for p in tmp_path.iterdir()] == ["demo.s2t.json"]


# =====================================================================
# one writer at a time
# =====================================================================
def test_a_second_writer_is_reported(project, tmp_path):
    path = tmp_path / "demo.s2t.json"
    first = ProjectStore(path)
    first.acquire_lock(owner="first")
    second = ProjectStore(path)
    assert second.locked_by_other is True
    with pytest.raises(ProjectLocked) as excinfo:
        second.acquire_lock()
    assert "already open" in str(excinfo.value)
    assert "first" in str(excinfo.value)
    first.release_lock()
    second.acquire_lock()
    second.release_lock()


def test_the_lock_is_released_by_the_context_manager(project, tmp_path):
    path = tmp_path / "demo.s2t.json"
    with ProjectStore(path) as store:
        store.save(project)
        assert ProjectStore(path).locked_by_other is True
    assert ProjectStore(path).locked_by_other is False


def test_reading_does_not_need_the_lock(project, tmp_path):
    path = tmp_path / "demo.s2t.json"
    with ProjectStore(path) as store:
        store.save(project)
        assert ProjectStore(path).load().name == "demo"


def test_project_from_dict_rejects_a_list():
    with pytest.raises(ProjectFileError):
        project_from_dict([1, 2])
