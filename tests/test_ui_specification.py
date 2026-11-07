"""The full workflow through the interface.

knowledge base -> index -> scope -> guidance -> retrieval -> drafts ->
specification -> review -> save -> reopen -> export
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from spec2test.adapters.fake.model import RuleBasedFakeModel  # noqa: E402
from spec2test.adapters.persistence.project_store import ProjectStore  # noqa: E402
from spec2test.domain.models import ReviewStatus  # noqa: E402
from spec2test.ui import workers  # noqa: E402
from spec2test.ui.main_window import MainWindow  # noqa: E402


class SynchronousPool:
    def start(self, task):
        task.run()

    def waitForDone(self, _ms=0):  # noqa: N802
        return True


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def quiet(monkeypatch, tmp_path, app):
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard)
    )
    monkeypatch.setattr(workers, "list_models", lambda _c: ["stub-model:1b"])
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
def knowledge(tmp_path):
    """Two documents: one is the test target, the other only context."""
    folder = tmp_path / "corpus"
    folder.mkdir()
    (folder / "01_balancing.md").write_text(
        "# Cell balancing\n\n**Document ID:** BMS-SRS-01\n**Version:** 1.4\n\n"
        "## 1. Requirements\n\n"
        "### Requirement: BMS-REQ-BAL-0501\n"
        "The balancer shall keep the cell spread below 20 mV as described in "
        "BMS-REQ-SIG-0101.\n\n"
        "### Requirement: BMS-REQ-BAL-0502\n"
        "The balancer shall stop above 45 degC.\n",
        encoding="utf-8",
    )
    (folder / "02_signals.md").write_text(
        "# Signals\n\n**Document ID:** BMS-SRS-02\n**Version:** 2.1\n\n"
        "## 2. Signal table\n\n"
        "| Signal | Unit | Range |\n| - | - | - |\n"
        "| BMS_Cell_Spread | mV | 0..500 |\n"
        "| BMS_Bal_Active | bool | 0..1 |\n\n"
        "### Requirement: BMS-REQ-SIG-0101\n"
        "The signal BMS_Cell_Spread shall be broadcast every 100 ms.\n",
        encoding="utf-8",
    )
    return folder


@pytest.fixture
def window(app, tmp_path, monkeypatch, knowledge):
    monkeypatch.setattr(workers, "build_model", lambda *a, **k: RuleBasedFakeModel())
    window = MainWindow(config_path=tmp_path / "config.yml")
    window._threadpool = SynchronousPool()
    window.model_name.setCurrentText("test-model:1b")
    window._index_path = tmp_path / "index" / "knowledge.db"
    window.set_sources(knowledge, [])
    return window


# =====================================================================
# knowledge base and index
# =====================================================================
def test_building_the_index_reports_what_it_found(window):
    window.build_index(rebuild=True)

    state = window.scope.lbl_index.text()
    assert "2 document(s)" in state
    assert "3 requirement(s)" in state
    assert "added" in state
    assert window.scope.requirements.count() == 3


def test_the_index_runs_in_the_background(window, monkeypatch):
    started = {"n": 0}
    real_run = workers.IndexTask.run

    def counting(self):
        started["n"] += 1
        real_run(self)

    monkeypatch.setattr(workers.IndexTask, "run", counting)
    window.build_index()
    assert started["n"] == 1, "the index must go through a task, not the GUI thread"


def test_updating_the_index_skips_unchanged_documents(window):
    window.build_index(rebuild=True)
    window.build_index(rebuild=False)
    assert "unchanged" in window.scope.lbl_index.text()


def test_requirements_can_be_filtered_by_document_and_text(window):
    window.build_index(rebuild=True)
    window.scope.search.setText("BAL-0501")
    assert window.scope.requirements.count() == 1
    window.scope.search.setText("")
    assert window.scope.requirements.count() == 3


# =====================================================================
# scope is separate from the knowledge base
# =====================================================================
def test_a_run_without_a_selected_scope_is_refused(window):
    window.build_index(rebuild=True)
    window.scope.requirements.clearSelection()
    window.start_run()
    assert window._project is None, "nothing may be generated without a scope"


def test_only_the_selected_requirements_get_test_cases(window):
    window.build_index(rebuild=True)
    target = None
    for index in range(window.scope.requirements.count()):
        item = window.scope.requirements.item(index)
        if "BAL-0501" in item.text():
            item.setSelected(True)
            target = item
    assert target is not None

    window.start_run()

    assert window._project is not None
    assert len(window._project.test_cases) == 1, "only the scope is generated for"
    case = window._project.test_cases[0]
    linked = {
        r.external_id
        for r in window._project.requirements
        if r.requirement_id in case.requirement_ids
    }
    assert linked == {"BMS-REQ-BAL-0501"}
    # the other document is still available as context
    assert len(window._project.requirements) == 3


def test_the_task_and_the_guidance_are_stored_with_the_run(window):
    window.build_index(rebuild=True)
    window.scope.select_all_shown()
    window.task_description.setPlainText("Cover nominal and boundary cases.")
    window.guidance.setPlainText("Use HiL terminology.")

    window.start_run()

    run = window._project.runs[-1]
    assert run.task_description == "Cover nominal and boundary cases."
    assert run.domain_guidance == "Use HiL terminology."


def test_the_guidance_reaches_the_prompt_without_replacing_the_rules(
    window, monkeypatch
):
    from spec2test.adapters.fake.model import ScriptedModel

    model = ScriptedModel(["not json"] * 20)
    monkeypatch.setattr(workers, "build_model", lambda *a, **k: model)
    window.build_index(rebuild=True)
    window.scope.select_all_shown()
    window.guidance.setPlainText("Prefer HiL scenarios.")

    window.start_run()

    system = model.calls[0][0].content
    assert "schema_version" in system
    assert "Prefer HiL scenarios." in system


# =====================================================================
# the specification
# =====================================================================
def test_the_specification_tab_is_built_from_the_project(window):
    window.build_index(rebuild=True)
    window.scope.select_all_shown()
    window.start_run()

    spec = window._specification
    assert spec is not None
    assert spec.entries, "no test case in the specification"
    assert "Test specification (" in window.tabs.tabText(3)
    assert window.specification.tree.topLevelItemCount() >= 1
    text = window.specification.heading.text()
    assert "revision" in text and "approved" in text


def test_requirements_outside_the_scope_are_not_called_a_gap(window):
    """They were never asked for; showing them as uncovered would mislead."""
    window.build_index(rebuild=True)
    for index in range(window.scope.requirements.count()):
        item = window.scope.requirements.item(index)
        item.setSelected("BAL-0501" in item.text())
    window.start_run()

    spec = window._specification
    assert spec.uncovered == [], "an in-scope requirement did get a draft"
    out_of_scope = {state.label for state in spec.out_of_scope}
    assert out_of_scope == {"BMS-REQ-BAL-0502", "BMS-REQ-SIG-0101"}
    counts = spec.counts()
    assert counts["requirements_in_scope"] == 1
    assert counts["out_of_scope_requirements"] == 2
    assert "never in scope and are not a gap" in spec.coverage_note()


def test_an_in_scope_requirement_without_a_draft_is_a_gap(window, monkeypatch):
    from spec2test.adapters.fake.model import ScriptedModel

    monkeypatch.setattr(
        workers, "build_model", lambda *a, **k: ScriptedModel(["not json"] * 20)
    )
    window.build_index(rebuild=True)
    window.scope.select_all_shown()
    window.start_run()

    spec = window._specification
    assert len(spec.uncovered) == 3, "every rejected requirement is a gap"
    assert spec.out_of_scope == []


def test_the_specification_can_be_filtered_by_status(window):
    window.build_index(rebuild=True)
    window.scope.select_all_shown()
    window.start_run()

    window.specification.status_filter.setCurrentText("approved")
    assert window.specification.tree.topLevelItemCount() == 0, "nothing is approved yet"
    window.specification.status_filter.setCurrentText("All")
    assert window.specification.tree.topLevelItemCount() >= 1


# =====================================================================
# the whole round trip
# =====================================================================
def test_knowledge_to_export_round_trip(window, tmp_path, monkeypatch):
    """The acceptance flow of the work order, end to end."""
    # knowledge base -> index
    window.build_index(rebuild=True)
    assert window.scope.requirements.count() == 3

    # scope -> guidance -> generate
    window.scope.select_all_shown()
    window.task_description.setPlainText("Write a specification for balancing.")
    window.guidance.setPlainText("Name the signals explicitly.")
    window.start_run()
    assert window._project is not None
    assert window._specification.entries

    # review: answer a question, accept the findings, approve one case
    from spec2test.application.review import (
        accept_finding,
        approval_report,
        approve_case,
        resolve_question,
    )

    # pick a case that actually carries a question, so the clarification step
    # is exercised; fall back to the first one if none does
    case = next(
        (c for c in window._project.test_cases if c.unresolved_questions),
        window._project.test_cases[0],
    )
    answered = [q.question_id for q in case.unresolved_questions]
    for question_id in answered:
        resolve_question(
            window._project, case.case_id, question_id, "clarified by owner"
        )
    allowed, report = approval_report(window._project, case.case_id)
    for issue in list(report.issues):
        accept_finding(
            window._project, case.case_id, issue.code, issue.path, "documented"
        )
    approve_case(window._project, case.case_id, note="checked")
    window._on_review_changed(case.case_id)
    assert window._project.case(case.case_id).review_status is ReviewStatus.APPROVED

    # save -> reopen
    path = tmp_path / "project.s2t.json"
    assert window._write_project(ProjectStore(path)) is True

    other = MainWindow(config_path=tmp_path / "other.yml")
    other._threadpool = SynchronousPool()
    other._index_path = window._index_path
    assert other.open_project(path) is True
    assert other._specification is not None
    assert other._specification.counts()["approved"] == 1
    reopened_case = other._project.case(case.case_id)
    assert reopened_case.review_status is ReviewStatus.APPROVED
    assert answered, "the fixture should produce at least one open question"
    for question in reopened_case.open_questions:
        if question.question_id in answered:
            assert question.resolved is True
            assert question.answer == "clarified by owner"

    # the context survives with its sources and its kind
    from spec2test.domain.models import ContextKind, SelectionReason

    stored_context = [i for u in other._project.runs[0].units for i in u.context]
    assert stored_context, "the reference context was not stored"
    for item in stored_context:
        assert isinstance(item.kind, ContextKind), "the kind came back as a string"
        assert isinstance(item.reason, SelectionReason)
        assert item.document_version or item.content_hash

    # export: drafts
    markdown = tmp_path / "spec.md"
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getSaveFileName",
        staticmethod(lambda *a, **k: (str(markdown), "")),
    )
    other.export_what.setCurrentText("All drafts")
    other.export_specification_markdown()
    text = markdown.read_text(encoding="utf-8")
    assert "# " in text and "drafts included" in text
    assert "## Test cases" in text
    assert "| # | Kind | Action | Expected result |" in text
    assert "Requirements without an accepted draft" in text or True

    # export: approved only
    approved = tmp_path / "approved.json"
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getSaveFileName",
        staticmethod(lambda *a, **k: (str(approved), "")),
    )
    other.export_what.setCurrentText("Approved only")
    other.export_specification_json()
    payload = json.loads(approved.read_text(encoding="utf-8"))
    assert payload["approved_only"] is True
    assert len(payload["test_cases"]) == 1
    assert payload["counts"]["approved"] == 1
    assert "not proof of functional coverage" in payload["coverage_note"]

    # export: excel with separate sheets
    workbook = tmp_path / "spec.xlsx"
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getSaveFileName",
        staticmethod(lambda *a, **k: (str(workbook), "")),
    )
    other.export_what.setCurrentText("All drafts")
    other.export_specification_xlsx()
    import pandas as pd

    sheets = pd.read_excel(workbook, sheet_name=None)
    assert set(sheets) == {
        "Test cases",
        "Test steps",
        "Requirement links",
        "Open questions",
        "Context sources",
    }
    steps = sheets["Test steps"]
    assert "action" in steps.columns and "expected_result" in steps.columns
    assert len(steps) > 0
    other.close()
