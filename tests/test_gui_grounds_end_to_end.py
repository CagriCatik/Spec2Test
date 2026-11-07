"""The whole path in the window: grounds visible, finding cleared, approved.

Generate -> look at the grounds -> clear the finding with a reason ->
approve -> save -> reopen -> export the specification. Offline, with the
stand-in model, so it runs anywhere and costs no model call.
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
from spec2test.application.review import approval_report  # noqa: E402
from spec2test.domain.models import GROUNDING_RULES_VERSION, ReviewStatus  # noqa: E402
from spec2test.ui import workers  # noqa: E402
from spec2test.ui.main_window import MainWindow  # noqa: E402

#: the stand-in extracts values in the units it knows (ms, V, %, A)
SPEC = """# Pyrofuse

### Requirement: SYS-REQ-7001
* **Title:** Ultra-fast trip
* The hardware comparator trips at 12 V and the latch shall assert within
  50 ms of the threshold being crossed.
* The balancing duty cycle shall not exceed 60 %.
"""


class SynchronousPool:
    def start(self, task):
        task.run()

    def waitForDone(self, _ms=0):  # noqa: N802 - Qt naming
        return True


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(workers, "list_models", lambda _c: ["fake"])
    monkeypatch.setattr(workers, "build_model", lambda *a, **k: RuleBasedFakeModel())
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
    )
    spec = tmp_path / "spec.md"
    spec.write_text(SPEC, encoding="utf-8")

    window = MainWindow(config_path=tmp_path / "config.yml")
    window._threadpool = SynchronousPool()
    window.model_name.setCurrentText("fake")
    window._spec_paths = [spec]
    yield window, tmp_path
    window._project_dirty = False
    window.close()
    app.processEvents()


def test_the_whole_path_from_generation_to_the_exported_specification(
    window, app, monkeypatch
):
    win, tmp_path = window

    # --- generate -------------------------------------------------------
    win.start_run()
    assert win._run is not None and win._run.status.value == "completed"
    project = win._project
    assert project is not None and project.test_cases
    case = project.test_cases[0]
    assert case.grounding_rules == GROUNDING_RULES_VERSION, (
        "a freshly generated draft is held to the current rule"
    )

    # --- look at the grounds in the window ------------------------------
    win.review.show_case(project, case.case_id)
    app.processEvents()
    grounds = [
        g
        for step in (*case.preconditions, *case.steps, *case.postconditions)
        for g in step.grounds
    ]
    assert grounds, "the draft carries no grounds at all"
    assert any(g.target for g in grounds), "a ground must address a field"

    # --- a tester edits the draft and adds a value of their own ---------
    # this is the realistic way a grounding finding appears: the edit is a
    # technical revision, so the new value needs a ground like any other
    from dataclasses import replace as _replace

    from spec2test.application.review import CaseEdit, accept_finding, edit_case

    first = case.steps[0]
    widened = _replace(
        first,
        expectations=(*first.expectations, "the recovery completes within 300 ms"),
    )
    edited = edit_case(
        project, case.case_id, CaseEdit(steps=(widened, *case.steps[1:]))
    )
    assert edited.revision == case.revision + 1
    assert edited.grounding_rules == GROUNDING_RULES_VERSION

    allowed, report = approval_report(project, case.case_id)
    assert allowed is False, "the added value must block approval"
    assert "C_CLAIM_WITHOUT_GROUND" in report.codes()
    assert report.blocks_draft is False, "but the draft is still storable"

    # --- the reviewer sees it in the panel ------------------------------
    win.review.show_case(project, case.case_id)
    app.processEvents()
    shown = " ".join(
        win.review.findings.item(i).text()
        for i in range(win.review.findings.count())
    )
    assert "C_CLAIM_WITHOUT_GROUND" in shown, shown
    assert "300 ms" in shown, shown

    # --- clear it with a documented reason ------------------------------
    from spec2test.application.review import resolve_question

    for question in project.case(case.case_id).unresolved_questions:
        resolve_question(
            project, case.case_id, question.question_id,
            "clarified with the system owner", by="dev",
        )
    for _ in range(12):
        allowed, report = approval_report(project, case.case_id)
        if allowed or not report.clarifications:
            break
        issue = report.clarifications[0]
        accept_finding(
            project, case.case_id, issue.code, issue.path,
            justification=f"checked by hand against SYS-REQ-7001 ({issue.code})",
            by="dev",
        )

    # --- approve --------------------------------------------------------
    from spec2test.application.review import approve_case

    allowed, report = approval_report(project, case.case_id)
    assert allowed, [i.code for i in report.issues]
    approved = approve_case(project, case.case_id, by="dev", note="function test")
    assert approved.review_status is ReviewStatus.APPROVED

    # --- save, reopen ---------------------------------------------------
    target = tmp_path / "project.s2t.json"
    assert win._write_project(ProjectStore(target))
    win._project_dirty = False
    win.close()
    app.processEvents()

    reopened = MainWindow(config_path=tmp_path / "second.yml")
    try:
        assert reopened.open_project(target)
        restored = reopened._project.case(case.case_id)
        assert restored.review_status is ReviewStatus.APPROVED
        assert restored.grounding_rules == GROUNDING_RULES_VERSION
        assert restored.accepted_findings, "the documented reasons are kept"
        kept = [
            g
            for step in (*restored.preconditions, *restored.steps,
                         *restored.postconditions)
            for g in step.grounds
        ]
        assert len(kept) == len(grounds), "the grounds did not survive the round trip"
        assert any(g.target for g in kept), "the field address did not survive"

        # --- export ------------------------------------------------------
        from spec2test.adapters.export import (
            save_specification_xlsx,
            to_json,
            to_markdown,
        )

        specification = reopened._specification
        markdown = to_markdown(specification)
        assert "Where the values come from" in markdown
        assert "SYS-REQ-7001" in markdown
        payload = json.loads(to_json(specification))
        assert payload["test_cases"]
        book = tmp_path / "spec.xlsx"
        save_specification_xlsx(specification, book)
        assert book.exists() and book.stat().st_size > 0
    finally:
        reopened._project_dirty = False
        reopened.close()
        app.processEvents()


def test_a_generated_draft_is_approvable_when_its_values_are_grounded(window):
    """The other half: grounding must not make a sound draft unapprovable."""
    win, _tmp = window
    win.start_run()
    project = win._project
    case = project.test_cases[0]
    allowed, report = approval_report(project, case.case_id)
    if not allowed:
        remaining = [i.code for i in report.issues]
        assert "C_CLAIM_WITHOUT_GROUND" not in remaining, (
            f"a fully grounded draft was blocked over grounding: {remaining}"
        )
    grounded = {
        g.target
        for step in case.steps
        for g in step.grounds
        if g.target
    }
    assert grounded, "the generated draft addressed no field"
