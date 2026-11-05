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
                "experimental": {"future_flag": True},
                "generation": {"combine_pages": 5, "max_repair_attempts": 2},
                "model": {"name": "old-model"},
            }
        ),
        encoding="utf-8",
    )

    window._save_persisted()
    saved = yaml.safe_load(isolated_config.read_text(encoding="utf-8"))

    assert saved["experimental"] == {"future_flag": True}, "unknown section dropped"
    assert saved["generation"]["max_repair_attempts"] == 2, "unknown key was dropped"
    assert "combine_pages" in saved["generation"]
    # the cloud section is written by the GUI itself now
    assert saved["cloud"]["mode"] in ("ollama_cloud", "ollama_local")


def test_the_merge_helper_overlays_without_dropping():
    from gui import _merge_config

    merged = _merge_config(
        {"a": {"keep": 1, "change": 1}, "b": 1},
        {"a": {"change": 2}, "c": 3},
    )
    assert merged == {"a": {"keep": 1, "change": 2}, "b": 1, "c": 3}


# ------------------------------------------- GUI → worker → adapter ----------
def _fake_run():
    from spec2test.domain.models import GenerationRun

    run = GenerationRun(
        run_id="r",
        model="m",
        endpoint="e",
        mode="fake",
        prompt_version="p",
        schema_version="1.0",
        source_hash="h",
    )
    run.resolve_status()
    return run


@pytest.fixture
def recording_worker(monkeypatch):
    """Replace the agent and the generation call, keep the real worker code."""
    import workers

    recorded: dict = {}

    class RecordingAgent:
        def __init__(self, settings=None, **kwargs):
            recorded["settings"] = settings
            recorded["kwargs"] = kwargs
            self.settings = settings
            self.model = settings.model
            self.url = settings.resolved_host

        def ensure_server(self):  # no network in the test
            return True

    def fake_generate(**kwargs):
        recorded["generate_kwargs"] = kwargs
        return [], _fake_run()

    monkeypatch.setattr(workers, "AgentLocal", RecordingAgent)
    monkeypatch.setattr(workers, "generate_test_cases", fake_generate)
    return recorded


@pytest.mark.parametrize(
    "mode,host,expected_host",
    [
        ("ollama_cloud", "", "https://ollama.com"),
        ("ollama_cloud", "https://ollama.example", "https://ollama.example"),
        ("ollama_local", "", "http://localhost:11434"),
        ("ollama_local", "http://box.lan:11434", "http://box.lan:11434"),
    ],
)
def test_the_gui_hands_mode_host_and_timeouts_to_the_worker(
    window, recording_worker, spec_pdf, mode, host, expected_host
):
    """The review finding: the worker fell back to ollama_local."""
    import workers

    window.mode.setCurrentText(mode)
    window.host.setText(host)
    window.connect_timeout.setValue(7.0)
    window.read_timeout.setValue(123.0)
    window.model_name.setCurrentText("some-model:1b")

    task = workers.GeneratorTask(pdf=spec_pdf, cfg=window._collect_cfg())
    task.run()

    settings = recording_worker["settings"]
    assert settings is not None, "the worker did not build shared settings"
    assert settings.mode == mode
    assert settings.resolved_host == expected_host
    assert settings.connect_timeout_s == 7.0
    assert settings.read_timeout_s == 123.0
    assert settings.model == "some-model:1b"


def test_the_full_kick_off_path_carries_the_cloud_mode(
    window, recording_worker, spec_pdf
):
    """Covers _kick_off itself, not just _collect_cfg."""

    class SynchronousPool:
        def start(self, task):
            task.run()

    window._threadpool = SynchronousPool()
    window._pdf_path = spec_pdf
    window.mode.setCurrentText("ollama_cloud")
    window.host.setText("")

    window._kick_off()

    assert recording_worker["settings"].mode == "ollama_cloud"
    assert recording_worker["settings"].resolved_host == "https://ollama.com"


def test_gpu_options_are_not_sent_in_cloud_mode(window, recording_worker, spec_pdf):
    import workers

    window.mode.setCurrentText("ollama_cloud")
    window.use_gpu.setChecked(True)
    window.num_gpu.setValue(4)

    task = workers.GeneratorTask(pdf=spec_pdf, cfg=window._collect_cfg())
    task.run()

    options = recording_worker["settings"].options()
    assert "num_gpu" not in options
    assert "num_thread" not in options


def test_gpu_options_are_sent_in_local_mode(window, recording_worker, spec_pdf):
    import workers

    window.mode.setCurrentText("ollama_local")
    window.use_gpu.setChecked(True)
    window.num_gpu.setValue(4)

    task = workers.GeneratorTask(pdf=spec_pdf, cfg=window._collect_cfg())
    task.run()

    assert recording_worker["settings"].options()["num_gpu"] == 4


def test_the_mode_survives_saving_and_reloading_the_configuration(window, isolated_config):
    window.mode.setCurrentText("ollama_cloud")
    window.host.setText("https://ollama.example")
    window.read_timeout.setValue(222.0)
    window._save_persisted()

    window.mode.setCurrentText("ollama_local")
    window.host.setText("")
    window._load_persisted()

    assert window.mode.currentText() == "ollama_cloud"
    assert window.host.text() == "https://ollama.example"
    assert window.read_timeout.value() == 222.0


def test_the_model_list_follows_the_selected_mode(window, monkeypatch):
    import gui

    seen: list[str] = []

    def record(client):
        seen.append(client.settings.resolved_host)
        return ["listed-model"]

    monkeypatch.setattr(gui, "list_models", record)
    window.mode.setCurrentText("ollama_local")
    window.host.setText("")
    window._populate_models()
    window.mode.setCurrentText("ollama_cloud")
    window._populate_models()

    assert "http://localhost:11434" in seen
    assert "https://ollama.com" in seen, "the model list ignored the cloud mode"
