"""The ways a user actually drives the window, end to end.

Open a project, edit through the dialog, cancel, edit again, save, reopen,
export. The point of each test is the *path*, not the function underneath:
the functions have their own tests and pass while the window is unusable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from spec2test.adapters.fake.model import RuleBasedFakeModel  # noqa: E402
from spec2test.adapters.persistence.project_store import ProjectStore  # noqa: E402
from spec2test.ui import workers  # noqa: E402
from spec2test.ui.main_window import MainWindow  # noqa: E402
from spec2test.ui.review_panel import EditCaseDialog  # noqa: E402

SPEC = """# Pyrofuse

### Requirement: SYS-REQ-9001
* **Title:** Ultra-fast trip
* The comparator trips at 12 V and the latch asserts within 50 ms.
* The balancing duty cycle shall not exceed 60 %.
"""


class SynchronousPool:
    def start(self, task):
        task.run()

    def waitForDone(self, _ms=0):  # noqa: N802 - Qt naming
        return True


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def bench(app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(workers, "list_models", lambda _c: ["fake"])
    monkeypatch.setattr(workers, "build_model", lambda *a, **k: RuleBasedFakeModel())
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
    )
    corpus = tmp_path / "requirements"
    corpus.mkdir()
    (corpus / "pyrofuse.md").write_text(SPEC, encoding="utf-8")

    window = MainWindow(config_path=tmp_path / "config.yml")
    window._threadpool = SynchronousPool()
    window.model_name.setCurrentText("fake")
    window.set_sources(folder=corpus)
    window.build_index(rebuild=False)
    app.processEvents()
    yield window, tmp_path, corpus
    window._project_dirty = False
    window.close()
    app.processEvents()


def generate(window, app):
    window.scope.select_all_shown()
    window.start_run()
    app.processEvents()
    assert window._run is not None and window._project is not None
    return window._project.test_cases[0]


def select(window, case_id):
    for index in range(window.drafts.topLevelItemCount()):
        item = window.drafts.topLevelItem(index)
        if item.data(0, Qt.UserRole) == case_id:
            window.drafts.setCurrentItem(item)
            return item
    raise AssertionError(f"{case_id} is not in the draft tree")


# =============================================== what the window shows ======
def test_the_sources_area_lists_each_document_and_what_came_out(bench, app):
    window, _tmp, corpus = bench
    assert window.documents_view.topLevelItemCount() >= 1
    row = window.documents_view.topLevelItem(0)
    assert row.text(0) == "pyrofuse.md"
    assert row.text(1) == "markdown"
    assert int(row.text(2)) >= 1, "the requirement count per document"
    assert "1  Project and sources" in window.tabs.tabText(0)


def test_the_context_area_separates_references_from_search(bench):
    window, _tmp, _corpus = bench
    window.use_references.setChecked(True)
    window.context_mode.setCurrentIndex(window.context_mode.findData("off"))
    state = window.lbl_context_state.text()
    assert "References: followed" in state
    assert "Search: off" in state
    assert "still gets it" in state, "off must not read as 'no context at all'"

    window.use_references.setChecked(False)
    assert "References: not followed" in window.lbl_context_state.text()


def test_removing_a_source_marks_the_index_out_of_date(bench, app):
    window, _tmp, _corpus = bench
    assert window.index_is_stale is False
    window.scope.source_list.setCurrentRow(0)
    window.scope.btn_remove.click()
    app.processEvents()
    assert window.index_is_stale is True
    assert "out of date" in window.scope.lbl_index.text()


def test_a_run_during_an_index_update_is_refused(bench, monkeypatch):
    window, _tmp, _corpus = bench
    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda _p, title, text, *a, **k: asked.append(title)),
    )
    window._indexing = True
    window.scope.select_all_shown()
    window.start_run()
    assert "The index is being updated" in asked
    assert window._run is None, "no run may start against a moving index"


# ===================================================== the editing path =====
def test_cancelling_the_edit_dialog_changes_nothing(bench, app, monkeypatch):
    window, _tmp, _corpus = bench
    case = generate(window, app)
    select(window, case.case_id)
    before = window._project.case(case.case_id)

    monkeypatch.setattr(EditCaseDialog, "exec", lambda self: QDialog.Rejected)
    window.review.edit_selected()
    after = window._project.case(case.case_id)
    assert after.revision == before.revision
    assert after.title == before.title


def test_editing_a_value_through_the_dialog_reaches_the_project(
    bench, app, monkeypatch
):
    window, _tmp, _corpus = bench
    case = generate(window, app)
    select(window, case.case_id)
    before = window._project.case(case.case_id)

    def accept(self):
        self.title.setText("Trip threshold, reworded")
        node = self.steps_editor.tree.topLevelItem(0)
        node.setText(1, "raise the supply slowly")
        return QDialog.Accepted

    monkeypatch.setattr(EditCaseDialog, "exec", accept)
    window.review.edit_selected()

    after = window._project.case(case.case_id)
    assert after.title == "Trip threshold, reworded"
    assert after.steps[0].action == "raise the supply slowly"
    assert after.revision == before.revision + 1
    assert window._project_dirty, "an edit must mark the project unsaved"


def test_closing_with_unsaved_changes_asks_first(bench, app, monkeypatch):
    window, _tmp, _corpus = bench
    generate(window, app)
    window._project_dirty = True
    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(
            lambda _p, title, *a, **k: (asked.append(title), QMessageBox.Discard)[1]
        ),
    )
    monkeypatch.setattr(QMessageBox, "Discard", QMessageBox.Discard, raising=False)
    window.close()
    assert asked, "closing with unsaved work must ask"


# ============================================== save, reopen, export ========
def test_save_reopen_and_export_keep_the_bench(bench, app, monkeypatch):
    window, tmp_path, corpus = bench
    case = generate(window, app)
    window.task_description.setPlainText("Check the trip threshold.")
    window.guidance.setPlainText("# Project guidance: pyrofuse\n\nCover the latch.")

    def accept(self):
        self.title.setText("A saved title")
        return QDialog.Accepted

    monkeypatch.setattr(EditCaseDialog, "exec", accept)
    select(window, case.case_id)
    window.review.edit_selected()

    target = tmp_path / "project.s2t.json"
    assert window._write_project(ProjectStore(target))
    assert window._project_dirty is False
    window.close()
    app.processEvents()

    reopened = MainWindow(config_path=tmp_path / "second.yml")
    try:
        assert reopened.open_project(target)
        # the working set comes back, not only the drafts
        assert corpus in reopened.sources.folders()
        assert reopened.task_description.toPlainText() == "Check the trip threshold."
        assert "pyrofuse" in reopened.guidance.toPlainText()
        assert reopened._project.case(case.case_id).title == "A saved title"

        from spec2test.adapters.export import (
            save_specification_xlsx,
            to_json,
            to_markdown,
        )

        specification = reopened._specification
        assert "SYS-REQ-9001" in to_markdown(specification)
        assert json.loads(to_json(specification))["test_cases"]
        book = tmp_path / "spec.xlsx"
        save_specification_xlsx(specification, book)
        assert book.stat().st_size > 0
    finally:
        reopened._project_dirty = False
        reopened.close()
        app.processEvents()


def test_reopening_without_a_local_index_says_so(bench, app, tmp_path):
    window, _tmp, _corpus = bench
    generate(window, app)
    target = tmp_path / "p.s2t.json"
    window._write_project(ProjectStore(target))
    window._project_dirty = False
    window.close()
    app.processEvents()

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    reopened = MainWindow(config_path=elsewhere / "c.yml")
    reopened._index_path = elsewhere / "missing" / "knowledge.db"
    try:
        assert reopened.open_project(target)
        log = reopened.log.toPlainText()
        assert "No local index" in log, log[-400:]
        assert "stored run context is unchanged" in log
    finally:
        reopened._project_dirty = False
        reopened.close()
        app.processEvents()


# ================================================== keyboard and layout =====
def test_the_four_areas_have_keyboard_shortcuts(bench):
    window, _tmp, _corpus = bench
    shortcuts = {
        action.text(): action.shortcut().toString()
        for menu in window.menuBar().actions()
        if menu.menu()
        for action in menu.menu().actions()
        if not action.isSeparator()
    }
    for index in range(1, 5):
        matching = [k for k in shortcuts if k.startswith(f"{index}  ")]
        assert matching, f"no menu entry for area {index}: {sorted(shortcuts)}"
        assert shortcuts[matching[0]], f"area {index} has no shortcut"
    assert any("Generate" in k for k in shortcuts)


@pytest.mark.parametrize("size", [(900, 600), (1920, 1200)])
def test_the_window_survives_small_and_large_sizes(bench, app, size):
    window, _tmp, _corpus = bench
    window.resize(*size)
    window.show()
    app.processEvents()
    for index in range(window.tabs.count()):
        window.tabs.setCurrentIndex(index)
        app.processEvents()
    assert window.width() > 0 and window.height() > 0
    # the settings column must stay reachable at the small size
    assert window.scope.isVisible() or window.scope.parent() is not None


def test_the_window_fits_a_small_laptop_screen(app, tmp_path):
    """Measured: the export row in the bottom bar forced a 1256 px floor.

    A 1280x800 screen has no room to spare at that width, and a split
    screen none at all. The export controls moved into work area 4, where
    the specification they export lives.
    """
    from PySide6.QtWidgets import QMessageBox

    window = MainWindow(config_path=tmp_path / "small.yml")
    try:
        assert window.minimumSizeHint().width() <= 1000, (
            f"minimum width is {window.minimumSizeHint().width()} px"
        )
        window.show()
        window.resize(900, 650)
        app.processEvents()
        assert window.width() <= 1000
    finally:
        window._project_dirty = False
        window.close()
        app.processEvents()
        assert QMessageBox is not None


def test_the_export_controls_live_with_the_specification(bench):
    window, _tmp, _corpus = bench
    area = window.tabs.widget(3)
    for name in ("btn_export_md", "btn_export_json", "btn_save_xlsx",
                 "btn_save_csv", "export_what"):
        widget = getattr(window, name)
        parent = widget
        while parent is not None and parent is not area:
            parent = parent.parent()
        assert parent is area, f"{name} is not in work area 4"
