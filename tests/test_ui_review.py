"""AP 5 in the interface: review a draft, save and reopen a project."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from spec2test.adapters.fake.model import RuleBasedFakeModel  # noqa: E402
from spec2test.adapters.persistence.project_store import ProjectStore  # noqa: E402
from spec2test.domain.models import ReviewStatus  # noqa: E402
from spec2test.ui import workers  # noqa: E402
from spec2test.ui.main_window import MainWindow  # noqa: E402
from spec2test.ui.review_panel import EditCaseDialog  # noqa: E402


class SynchronousPool:
    def start(self, task):
        task.run()

    def waitForDone(self, _ms=0):  # noqa: N802
        return True


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No interface test may open a connection.

    Without this a host such as http://box.lan:11434 sends the test run into a
    DNS lookup. Tests that care about the list patch ModelListTask.run.
    """
    monkeypatch.setattr(workers, "list_models", lambda _client: ["stub-model:1b"])


@pytest.fixture(autouse=True)
def quiet(monkeypatch, tmp_path, app):
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    # a real question dialog would block the run forever; default to discard
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard)
    )
    monkeypatch.chdir(tmp_path)
    yield
    for widget in list(QApplication.topLevelWidgets()):
        if hasattr(widget, "_project_dirty"):
            widget._project_dirty = False
        widget.close()
        widget.deleteLater()
    for _ in range(3):
        QApplication.processEvents()


@pytest.fixture
def window(app, tmp_path, monkeypatch, spec_a):
    monkeypatch.setattr(workers, "build_model", lambda *a, **k: RuleBasedFakeModel())
    window = MainWindow(config_path=tmp_path / "config.yml")
    window._threadpool = SynchronousPool()
    window.model_name.setCurrentText("test-model:1b")
    window._spec_paths = [spec_a]
    window.start_run()
    yield window
    window.close()


def answers(*values):
    """Replace the dialog with prepared answers."""
    queue = list(values)
    return lambda *_a, **_k: queue.pop(0) if queue else None


def select(window, case_id: str) -> None:
    for index in range(window.drafts.topLevelItemCount()):
        item = window.drafts.topLevelItem(index)
        if item.data(0, Qt.UserRole) == case_id:
            window.drafts.setCurrentItem(item)
            return
    raise AssertionError(f"{case_id} is not in the tree")


def a_blocked_case(window):
    return next(c for c in window._project.test_cases if c.unresolved_questions)


def a_clean_case(window):
    project = window._project
    from spec2test.application.review import approval_report

    return next(
        c for c in project.test_cases if approval_report(project, c.case_id)[0]
    )


# =====================================================================
# the run becomes a project
# =====================================================================
def test_a_finished_run_becomes_a_reviewable_project(window):
    assert window._project is not None
    assert len(window._project.test_cases) == 10
    assert window._project.documents, "the original document is kept"
    assert window.drafts.topLevelItem(0).data(0, Qt.UserRole)


def test_selecting_a_draft_shows_its_review_state(window):
    case = a_blocked_case(window)
    select(window, case.case_id)
    assert case.case_id in window.review.header.text()
    assert "blocked" in window.review.header.text()
    assert window.review.questions.count() >= 1
    assert window.review.btn_approve.isEnabled() is False


def test_a_clean_draft_can_be_approved_from_the_panel(window, monkeypatch):
    case = a_clean_case(window)
    select(window, case.case_id)
    assert window.review.btn_approve.isEnabled() is True

    monkeypatch.setattr(window.review, "_ask", answers("checked against the spec"))
    window.review.approve()

    assert window._project.case(case.case_id).review_status is ReviewStatus.APPROVED
    assert "approved" in window.log.toPlainText()


# =====================================================================
# questions and findings
# =====================================================================
def test_answering_a_question_keeps_it_with_the_answer(window, monkeypatch):
    case = a_blocked_case(window)
    select(window, case.case_id)
    window.review.questions.setCurrentRow(0)
    monkeypatch.setattr(window.review, "_ask", answers("10 deg, confirmed by owner"))

    window.review.answer_selected_question()

    question = window._project.case(case.case_id).open_questions[0]
    assert question.resolved is True
    assert question.answer == "10 deg, confirmed by owner"
    assert question.resolved_by == "tester"


def test_a_finding_can_be_accepted_with_a_reason(window, monkeypatch):
    case = a_blocked_case(window)
    select(window, case.case_id)
    window.review.findings.setCurrentRow(0)
    monkeypatch.setattr(window.review, "_ask", answers("measured on the rig, 4.2"))

    window.review.accept_selected_finding()

    accepted = window._project.case(case.case_id).accepted_findings
    assert accepted and accepted[0].justification == "measured on the rig, 4.2"


def test_accepting_without_a_selection_is_reported(window):
    select(window, a_blocked_case(window).case_id)
    window.review.findings.setCurrentRow(-1)
    window.review.accept_selected_finding()
    assert "Select a finding" in window.log.toPlainText()


def test_the_whole_blocking_set_can_be_cleared_then_approved(window, monkeypatch):
    case = a_blocked_case(window)
    select(window, case.case_id)

    monkeypatch.setattr(window.review, "_ask", answers("clarified by the owner"))
    window.review.questions.setCurrentRow(0)
    window.review.answer_selected_question()

    while not window.review.btn_approve.isEnabled():
        monkeypatch.setattr(window.review, "_ask", answers("documented deviation"))
        window.review.findings.setCurrentRow(0)
        before = len(window._project.case(case.case_id).accepted_findings)
        window.review.accept_selected_finding()
        assert len(window._project.case(case.case_id).accepted_findings) > before

    monkeypatch.setattr(window.review, "_ask", answers("ok"))
    window.review.approve()
    assert window._project.case(case.case_id).review_status is ReviewStatus.APPROVED


# =====================================================================
# editing
# =====================================================================
def test_editing_from_the_panel_creates_a_revision(window, monkeypatch):
    case = a_clean_case(window)
    select(window, case.case_id)

    def fake_dialog(parent, selected):
        dialog = EditCaseDialog(parent, selected)
        dialog.title.setText("Reviewed title")
        dialog.exec = lambda: QDialog.Accepted
        return dialog

    monkeypatch.setattr(
        "spec2test.ui.review_panel.EditCaseDialog", fake_dialog
    )
    window.review.edit_selected()

    updated = window._project.case(case.case_id)
    assert updated.revision == 2
    assert updated.title == "Reviewed title"
    assert "revision 2" in window.log.toPlainText()


def test_an_untouched_dialog_changes_nothing(window):
    """The editor used to resubmit every step, revision and all."""
    case = a_clean_case(window)
    dialog = EditCaseDialog(window, case)
    assert dialog.edit().changes() == {}
    dialog._validate_and_accept()
    assert "nothing was changed" in dialog.error.text()


def test_the_steps_round_trip_through_the_structured_editor(window):
    case = a_clean_case(window)
    dialog = EditCaseDialog(window, case)
    assert dialog.steps_editor.steps() == tuple(case.steps)
    assert dialog.preconditions_editor.steps() == tuple(case.preconditions)
    assert dialog.postconditions_editor.steps() == tuple(case.postconditions)


def test_a_step_without_an_action_is_reported(window):
    case = a_clean_case(window)
    dialog = EditCaseDialog(window, case)
    dialog.steps_editor.add_step()
    dialog._validate_and_accept()
    assert "without an action" in dialog.error.text()


def test_a_title_only_edit_keeps_steps_measurements_and_grounds(window):
    """Reproduced before the rewrite: a title change destroyed both.

        before: measurements 1 | grounds 1
        after : measurements 0 | grounds 0
    """
    from spec2test.application.review import CaseEdit, edit_case
    from spec2test.domain.models import (
        Ground, GroundKind, Measurement, TestStep,
    )

    project = window._project
    case = project.test_cases[0]
    rich = TestStep(
        1,
        "inject the pulse",
        ("the latch asserts within 2.0 us",),
        (Measurement("response time", "2.0", "us", tolerance="0.1",
                     evidence="<= 2.0 us"),),
        grounds=(
            Ground(claim="2.0 us", kind=GroundKind.TARGET_REQUIREMENT,
                   target="expectations[0]", source="SYS-REQ-1",
                   quote="<= 2.0 us"),
        ),
    )
    prepared = edit_case(project, case.case_id, CaseEdit(steps=(rich,)))
    before_revision = prepared.revision

    dialog = EditCaseDialog(window, prepared)
    dialog.title.setText("A better title")
    edit = dialog.edit()
    assert set(edit.changes()) == {"title"}, edit.changes()

    updated = edit_case(project, case.case_id, edit)
    assert updated.title == "A better title"
    assert updated.revision == before_revision + 1
    step = updated.steps[0]
    assert len(step.measurements) == 1
    assert step.measurements[0].tolerance == "0.1"
    assert step.measurements[0].evidence == "<= 2.0 us"
    assert len(step.grounds) == 1
    assert step.grounds[0].target == "expectations[0]"
    assert step.expectations == ("the latch asserts within 2.0 us",), (
        "the measurement must not have become a prose expectation"
    )


def test_a_technical_edit_keeps_the_grounds_of_the_steps_it_touches(window):
    from spec2test.application.review import CaseEdit, edit_case
    from spec2test.domain.models import (
        Ground, GroundKind, Measurement, TestStep,
    )

    project = window._project
    case = project.test_cases[0]
    rich = TestStep(
        1, "inject the pulse", ("the latch asserts within 2.0 us",),
        (Measurement("response time", "2.0", "us", evidence="<= 2.0 us"),),
        grounds=(Ground(claim="2.0 us", kind=GroundKind.TARGET_REQUIREMENT,
                        target="expectations[0]", source="SYS-REQ-1",
                        quote="<= 2.0 us"),),
    )
    prepared = edit_case(project, case.case_id, CaseEdit(steps=(rich,)))

    dialog = EditCaseDialog(window, prepared)
    node = dialog.steps_editor.tree.topLevelItem(0)
    node.setText(1, "inject the pulse twice")
    edit = dialog.edit()
    assert set(edit.changes()) == {"steps"}

    updated = edit_case(project, case.case_id, edit)
    step = updated.steps[0]
    assert step.action == "inject the pulse twice"
    assert len(step.measurements) == 1 and len(step.grounds) == 1
    assert updated.grounding_rules == "1", "a technical edit sets the rule version"


# =====================================================================
# decisions
# =====================================================================
def test_a_rejection_without_a_reason_does_nothing(window, monkeypatch):
    case = a_clean_case(window)
    select(window, case.case_id)
    monkeypatch.setattr(window.review, "_ask", answers(None))
    window.review.reject()
    assert window._project.case(case.case_id).review_status is ReviewStatus.DRAFT


def test_rejecting_and_reopening(window, monkeypatch):
    case = a_clean_case(window)
    select(window, case.case_id)
    monkeypatch.setattr(window.review, "_ask", answers("wrong channel"))
    window.review.reject()
    assert window._project.case(case.case_id).review_status is ReviewStatus.REJECTED
    window.review.reopen()
    assert window._project.case(case.case_id).review_status is ReviewStatus.DRAFT


def test_the_status_bar_summarises_the_review_state(window, monkeypatch):
    case = a_clean_case(window)
    select(window, case.case_id)
    monkeypatch.setattr(window.review, "_ask", answers("ok"))
    window.review.approve()
    assert "Approved: 1" in window.statusBar().currentMessage()


# =====================================================================
# project files
# =====================================================================
def test_saving_and_reopening_a_project_in_the_window(window, tmp_path, monkeypatch):
    case = a_clean_case(window)
    select(window, case.case_id)
    monkeypatch.setattr(window.review, "_ask", answers("checked"))
    window.review.approve()

    path = tmp_path / "demo.s2t.json"
    assert window._write_project(ProjectStore(path)) is True
    assert path.exists()

    other = MainWindow(config_path=tmp_path / "other.yml")
    other._threadpool = SynchronousPool()
    assert other.open_project(path) is True

    assert other.drafts.topLevelItemCount() == 10
    assert other._project.case(case.case_id).review_status is ReviewStatus.APPROVED
    assert other.requirements.topLevelItemCount() == 10
    assert "Approved: 1" in other.statusBar().currentMessage()
    assert other.btn_save_csv.isEnabled()
    other.close()


def test_opening_a_damaged_project_is_reported(app, tmp_path):
    path = tmp_path / "broken.s2t.json"
    path.write_text("{{{", encoding="utf-8")
    window = MainWindow(config_path=tmp_path / "c.yml")
    window._threadpool = SynchronousPool()
    assert window.open_project(path) is False
    assert "could not be opened" in window.log.toPlainText()
    window.close()


def test_a_project_open_elsewhere_is_refused(window, tmp_path):
    path = tmp_path / "demo.s2t.json"
    window._write_project(ProjectStore(path))
    holder = ProjectStore(path)
    holder.acquire_lock(owner="another window")

    other = MainWindow(config_path=tmp_path / "other.yml")
    other._threadpool = SynchronousPool()
    assert other.open_project(path) is False
    assert "already open" in other.log.toPlainText()
    other.close()
    holder.release_lock()


def test_closing_the_window_releases_the_project_lock(window, tmp_path):
    path = tmp_path / "demo.s2t.json"
    store = ProjectStore(path)
    store.acquire_lock()
    window._project_store = store
    window.close()
    assert ProjectStore(path).locked_by_other is False


def test_saving_without_a_project_is_reported(app, tmp_path):
    window = MainWindow(config_path=tmp_path / "c.yml")
    window._threadpool = SynchronousPool()
    assert window.save_project() is False
    assert "nothing to save" in window.log.toPlainText()
    window.close()


# =====================================================================
# unsaved changes
# =====================================================================
def test_a_fresh_run_marks_the_project_as_unsaved(window):
    assert window._project_dirty is True
    assert window.windowTitle().endswith("*")


def test_saving_clears_the_unsaved_marker(window, tmp_path):
    window._write_project(ProjectStore(tmp_path / "demo.s2t.json"))
    assert window._project_dirty is False
    assert not window.windowTitle().endswith("*")


def test_a_review_decision_marks_the_project_as_unsaved(window, tmp_path, monkeypatch):
    window._write_project(ProjectStore(tmp_path / "demo.s2t.json"))
    assert window._project_dirty is False

    case = next(iter(window._project.test_cases))
    select(window, case.case_id)
    monkeypatch.setattr(window.review, "_ask", answers("looks wrong"))
    window.review.reject()

    assert window._project_dirty is True


def test_closing_with_unsaved_changes_offers_save_discard_cancel(window, monkeypatch):
    seen: dict = {}

    def fake_question(_parent, title, text, buttons=None, default=None):
        seen["title"] = title
        seen["buttons"] = buttons
        return QMessageBox.Discard

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    assert window.confirm_discard_changes() is True

    assert seen["title"] == "Unsaved changes"
    offered = seen["buttons"]
    for button in (QMessageBox.Save, QMessageBox.Discard, QMessageBox.Cancel):
        assert offered & button, f"{button} is not offered"


def test_cancel_keeps_the_window_open(window, monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Cancel)
    )
    assert window.confirm_discard_changes() is False

    from PySide6.QtGui import QCloseEvent

    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted() is False, "the window closed despite Cancel"
    assert window._project_dirty is True


def test_save_from_the_dialog_writes_the_project(window, tmp_path, monkeypatch):
    window._project_store = ProjectStore(tmp_path / "demo.s2t.json")
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Save)
    )
    assert window.confirm_discard_changes() is True
    assert (tmp_path / "demo.s2t.json").exists()
    assert window._project_dirty is False


def test_discard_loses_the_changes_knowingly(window, monkeypatch):
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard)
    )
    assert window.confirm_discard_changes() is True
    assert "discarded" in window.log.toPlainText()


def test_an_unchanged_project_closes_without_asking(window, tmp_path, monkeypatch):
    window._write_project(ProjectStore(tmp_path / "demo.s2t.json"))
    asked = {"n": 0}

    def counting(*_a, **_k):
        asked["n"] += 1
        return QMessageBox.Discard

    monkeypatch.setattr(QMessageBox, "question", staticmethod(counting))
    assert window.confirm_discard_changes() is True
    assert asked["n"] == 0


def test_opening_another_project_asks_first(window, tmp_path, monkeypatch):
    path = tmp_path / "demo.s2t.json"
    window._write_project(ProjectStore(path))
    case = next(iter(window._project.test_cases))
    select(window, case.case_id)
    monkeypatch.setattr(window.review, "_ask", answers("wrong"))
    window.review.reject()

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Cancel)
    )
    assert window.open_project(path) is False, "the open went ahead despite Cancel"


def test_a_second_run_merges_into_the_same_project(window, spec_a):
    before = len(window._project.test_cases)
    window.start_run()
    assert len(window._project.test_cases) == before * 2
    ids = [c.case_id for c in window._project.test_cases]
    assert len(set(ids)) == len(ids), "merging produced duplicate IDs"
    assert len(window._project.runs) == 2


# =====================================================================
# the supporting context is visible
# =====================================================================
def test_the_context_tab_shows_what_was_sent(app, tmp_path, monkeypatch):
    from spec2test.adapters.documents import DEFAULT_IMPORTERS
    from spec2test.application.ingest import import_documents
    from spec2test.ui.main_window import MainWindow

    first = tmp_path / "a.md"
    first.write_text(
        "**Document ID:** DOC-A\n**Version:** 1.0\n\n"
        "### Requirement: SYS-REQ-0001\n"
        "The controller shall limit the current as in SYS-REQ-0002.\n",
        encoding="utf-8",
    )
    second = tmp_path / "b.md"
    second.write_text(
        "**Document ID:** DOC-B\n**Version:** 4.2\n\n"
        "### Requirement: SYS-REQ-0002\nThe current limit shall be 2.5 A.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(workers, "build_model", lambda *a, **k: RuleBasedFakeModel())

    window = MainWindow(config_path=tmp_path / "config.yml")
    window._threadpool = SynchronousPool()
    window.model_name.setCurrentText("m:1b")
    window._spec_paths = [first, second]
    window.start_run()

    labels = [
        window.context.topLevelItem(i).text(0)
        for i in range(window.context.topLevelItemCount())
    ]
    assert labels, "the context tab stayed empty"
    assert "Context (" in window.tabs.tabText(5)

    entry = None
    for index in range(window.context.topLevelItemCount()):
        unit = window.context.topLevelItem(index)
        for child in range(unit.childCount()):
            if unit.child(child).text(0) == "SYS-REQ-0002":
                entry = unit.child(child)
    assert entry is not None, "the referenced requirement is not shown"
    assert "requirement" in entry.text(1) and "explicit_reference" in entry.text(1)
    details = [entry.child(i).text(0) for i in range(entry.childCount())]
    assert any("DOC-B" in d and "4.2" in d for d in details), "the origin is missing"
    assert any(d.startswith("Position:") for d in details)
    window.close()
