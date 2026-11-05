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


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """The tests must never read or write the repository's config.yml."""
    import gui

    monkeypatch.setattr(gui, "CFG_FILE", tmp_path / "config.yml")
    return tmp_path / "config.yml"


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


# ------------------------------------------------- configuration safety ------
def test_saving_from_the_gui_keeps_sections_it_does_not_know(window, isolated_config):
    import yaml

    isolated_config.write_text(
        yaml.safe_dump(
            {
                "cloud": {"mode": "ollama_cloud", "host": "https://ollama.com"},
                "generation": {"combine_pages": 5, "max_repair_attempts": 2},
                "model": {"name": "old-model"},
            }
        ),
        encoding="utf-8",
    )

    window._save_persisted()
    saved = yaml.safe_load(isolated_config.read_text(encoding="utf-8"))

    assert saved["cloud"] == {"mode": "ollama_cloud", "host": "https://ollama.com"}
    assert saved["generation"]["max_repair_attempts"] == 2, "unknown key was dropped"
    assert "combine_pages" in saved["generation"]


def test_the_merge_helper_overlays_without_dropping():
    from gui import _merge_config

    merged = _merge_config(
        {"a": {"keep": 1, "change": 1}, "b": 1},
        {"a": {"change": 2}, "c": 3},
    )
    assert merged == {"a": {"keep": 1, "change": 2}, "b": 1, "c": 3}
