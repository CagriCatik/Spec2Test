"""GUI smoke test: the window builds and reports the run status honestly.

Skipped when PySide6 is not installed, which is exactly how the domain and
application tests stay independent of Qt.
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    import gui

    # no network in the test: the model list is stubbed
    monkeypatch.setattr(gui, "list_models", lambda _client: ["stub-model:1b"])
    return gui.MainWindow()


def test_the_window_builds_and_offers_manual_model_entry(window):
    assert window.model_name.isEditable(), "a manual model name must be possible"
    assert window.model_name.count() >= 1


def test_an_unreachable_endpoint_does_not_fall_back_to_the_cli(app, monkeypatch):
    import gui
    from spec2test.adapters.ollama import OllamaError

    def boom(_client):
        raise OllamaError("connection refused")

    monkeypatch.setattr(gui, "list_models", boom)
    called = {"subprocess": False}
    import subprocess

    def no_subprocess(*_a, **_k):  # pragma: no cover - must not run
        called["subprocess"] = True
        raise AssertionError("the GUI must not shell out to the ollama CLI")

    monkeypatch.setattr(subprocess, "check_output", no_subprocess)

    window = gui.MainWindow()
    assert called["subprocess"] is False
    assert window.model_name.count() >= 1, "a default name is still offered"


@pytest.mark.parametrize(
    "status,icon",
    [
        ("completed", "✅"),
        ("partial", "⚠️"),
        ("failed", "❌"),
        ("cancelled", "⏹️"),
    ],
)
def test_the_status_bar_shows_the_real_run_status(window, status, icon):
    window._on_status(
        {"status": status, "accepted_blocks": 1, "rejected_blocks": 1, "report": []}
    )
    window._on_done([])
    message = window.statusBar().currentMessage()
    assert icon in message
    assert status in message
    assert "block(s) rejected" in message


def test_a_finished_run_is_not_announced_as_success_without_a_status(window):
    window._run_status = None
    window._on_done([])
    assert "✅" not in window.statusBar().currentMessage()


def test_the_worker_can_be_cancelled(tmp_path):
    from workers import GeneratorTask

    task = GeneratorTask(pdf=tmp_path / "missing.pdf", cfg={})
    assert task._cancelled is False
    task.cancel()
    assert task._cancelled is True


def test_the_worker_reports_an_unreadable_pdf_as_an_error(tmp_path):
    from workers import GeneratorTask

    task = GeneratorTask(pdf=tmp_path / "missing.pdf", cfg={})
    errors: list[str] = []
    task.signals.error.connect(errors.append)
    task.run()
    assert errors and "PDF could not be read" in errors[0]
