"""A full hybrid run in the window: generate, finish, save, reopen, export.

The close() defect sat exactly here. The run itself succeeded; the finally
block that releases the retriever then raised AttributeError, because the
hybrid and vector adapters had no close(). The CLI never called it, so a CLI
check passed over it. This test walks the whole window path instead.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from spec2test.adapters.fake.model import RuleBasedFakeModel  # noqa: E402
from spec2test.adapters.knowledge import SqliteKnowledgeIndex  # noqa: E402
from spec2test.application.indexing import sync_index  # noqa: E402
from spec2test.bootstrap import build_importers  # noqa: E402
from spec2test.ui import workers  # noqa: E402
from spec2test.ui.main_window import MainWindow  # noqa: E402

from test_retrieval import BagOfWordsEmbedder  # noqa: E402

TASK = "Check the contactor opening under overcurrent, including arcing."

DOCUMENTS = {
    "a_contactors": (
        "# Contactors\n\n"
        "### Requirement: SYS-REQ-0001\n"
        "* **Title:** Contactor opening\n"
        "* The BMS shall open the main contactors on `Overcurrent_Detected`.\n"
    ),
    "b_arcing": (
        "# Arc chamber\n\n"
        "## Explanation\n"
        "The arc chamber quenches the arc within 3 ms of separation.\n"
    ),
    "c_signals": (
        "# Signal definitions\n\n"
        "## Signals\n"
        "`Overcurrent_Detected` is a boolean status signal of the current path.\n"
    ),
}


class SynchronousPool:
    def start(self, task):
        task.run()

    def waitForDone(self, _ms=0):  # noqa: N802 - Qt naming
        return True


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def hybrid_window(app, tmp_path, monkeypatch):
    """A window configured for hybrid retrieval over a real index."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(workers, "list_models", lambda _c: ["fake"])
    monkeypatch.setattr(workers, "build_model", lambda *a, **k: RuleBasedFakeModel())
    monkeypatch.setattr(
        "spec2test.adapters.knowledge.OllamaEmbedder",
        lambda **_k: BagOfWordsEmbedder(),
    )
    for name, state in (("question", QMessageBox.Yes), ("warning", None),
                        ("critical", None), ("information", None)):
        monkeypatch.setattr(
            QMessageBox, name, staticmethod(lambda *a, _s=state, **k: _s)
        )

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name, text in DOCUMENTS.items():
        (corpus / f"{name}.md").write_text(text, encoding="utf-8")

    config = tmp_path / "config.yml"
    config.write_text(
        "cloud: {mode: fake}\n"
        "model: {name: fake}\n"
        "generation:\n"
        "  use_retrieval: true\n"
        "  retrieval: hybrid\n"
        "  embedding_model: fake-model\n"
        "  group_size: 1\n",
        encoding="utf-8",
    )
    window = MainWindow(config_path=config)
    window._threadpool = SynchronousPool()
    window.model_name.setCurrentText("fake")

    index_path = tmp_path / "knowledge.db"
    sync_index(
        SqliteKnowledgeIndex(index_path),
        sorted(corpus.glob("*.md")),
        build_importers(),
    )
    window._index_path = index_path
    window.set_sources(corpus, [])
    window.build_index(rebuild=False)
    app.processEvents()
    yield window, tmp_path
    window._project_dirty = False
    window.close()
    app.processEvents()


def test_a_hybrid_run_finishes_and_releases_the_retriever(hybrid_window, caplog):
    """The run used to succeed and then raise in the finally block."""
    window, _tmp = hybrid_window
    assert window._catalogue, "the index produced no requirements"
    window.scope.select_all_shown()

    errors: list[str] = []
    window._on_error = lambda message: errors.append(message)

    window.start_run()

    assert errors == [], errors
    assert window._run is not None, "no run was produced"
    assert window._run.status.value == "completed", window._run.status
    assert window.btn_run.isEnabled(), "the window stayed locked after the run"
    log = window.log.toPlainText()
    assert "AttributeError" not in log, log[-600:]
    assert "hybrid retrieval" in log, "the hybrid retriever was never built"


def test_the_task_text_reaches_the_model_and_the_retrieval(hybrid_window, monkeypatch):
    window, _tmp = hybrid_window
    model = RuleBasedFakeModel()
    monkeypatch.setattr(workers, "build_model", lambda *a, **k: model)
    window.scope.select_all_shown()
    window.task_description.setPlainText(TASK)

    window.start_run()

    assert any(TASK in message.content for message in model.calls[0]), (
        "the task text never reached the model"
    )
    assert window._run.task_description == TASK


def test_the_run_can_be_saved_reopened_and_exported(hybrid_window, app):
    window, tmp_path = hybrid_window
    window.scope.select_all_shown()
    window.task_description.setPlainText(TASK)
    window.start_run()
    assert window._run.status.value == "completed"

    from spec2test.adapters.persistence.project_store import ProjectStore

    target = tmp_path / "project.s2t.json"
    assert window._write_project(ProjectStore(target)) is True
    assert target.exists() and target.stat().st_size > 0
    window._project_dirty = False
    window.close()
    app.processEvents()

    reopened = MainWindow(config_path=tmp_path / "second.yml")
    try:
        assert reopened.open_project(target) is True
        assert reopened._specification is not None
        counts = reopened._specification.counts()
        assert counts["test_cases"] >= 1
        assert reopened.drafts.topLevelItemCount() >= 1

        # save_specification_xlsx, not the flat row exporter of the same name
        from spec2test.adapters.export import (
            save_specification_xlsx,
            to_json,
            to_markdown,
        )

        markdown = to_markdown(reopened._specification)
        assert "SYS-REQ-0001" in markdown
        assert TASK in markdown, "the task of the run is not in the specification"
        import json

        payload = json.loads(to_json(reopened._specification))
        assert payload["test_cases"]
        book = tmp_path / "spec.xlsx"
        save_specification_xlsx(reopened._specification, book)
        assert book.exists() and book.stat().st_size > 0
    finally:
        reopened._project_dirty = False
        reopened.close()
        app.processEvents()


def test_the_stored_context_survives_the_round_trip(hybrid_window, app):
    """The run records what was sent; reopening must not lose it."""
    window, tmp_path = hybrid_window
    window.scope.select_all_shown()
    window.task_description.setPlainText(TASK)
    window.start_run()
    sent = [item for unit in window._run.units for item in unit.context]
    if not sent:
        pytest.skip("this corpus produced no retrieved context")

    from spec2test.adapters.persistence.project_store import ProjectStore
    from spec2test.application.review import context_for_case

    target = tmp_path / "project.s2t.json"
    window._write_project(ProjectStore(target))
    window._project_dirty = False
    window.close()
    app.processEvents()

    reopened = MainWindow(config_path=tmp_path / "second.yml")
    try:
        assert reopened.open_project(target)
        case = reopened._project.test_cases[0]
        restored = context_for_case(reopened._project, case.case_id)
        assert restored, "the stored context was lost on reopening"
        assert all(item.spans for item in restored)
        assert {item.reason for item in restored} <= {
            item.reason for item in sent
        }
    finally:
        reopened._project_dirty = False
        reopened.close()
        app.processEvents()


def test_an_unreachable_embedding_model_still_produces_a_run(hybrid_window, monkeypatch):
    """Degradation must be visible and must not cost the run."""
    from spec2test.adapters.knowledge import EmbeddingUnavailable

    window, _tmp = hybrid_window

    class Dead:
        name = "dead"

        def embed(self, _texts):
            raise EmbeddingUnavailable("no daemon")

        def embed_query(self, _text):  # pragma: no cover
            raise EmbeddingUnavailable("no daemon")

    monkeypatch.setattr(
        "spec2test.adapters.knowledge.OllamaEmbedder", lambda **_k: Dead()
    )
    window.scope.select_all_shown()
    window.start_run()

    assert window._run is not None and window._run.status.value == "completed"
    log = window.log.toPlainText()
    assert "Falling back to lexical" in log, log[-600:]
