"""User interface tests.

Skipped when PySide6 is missing, which is how the domain and application tests
stay independent of Qt.
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

import pytest
import yaml

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from spec2test.adapters.fake.model import RuleBasedFakeModel, ScriptedModel  # noqa: E402
from spec2test.ui import workers  # noqa: E402
from spec2test.ui.main_window import MainWindow, merge_config  # noqa: E402

UI_DIR = Path(workers.__file__).parent


class SynchronousPool:
    """Runs a QRunnable in the calling thread so a test can assert on it."""

    def start(self, task):
        task.run()

    def waitForDone(self, _ms=0):  # noqa: N802 - Qt naming
        return True


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """A modal dialog would block the test run forever."""
    shown: list[str] = []
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(
            QMessageBox, name, staticmethod(lambda *a, _n=name, **k: shown.append(_n))
        )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
    )
    return shown


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    """No test may read or write inside the repository."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def clean_qt_state(app):
    """Leave no window, timer or queued event behind.

    Without this, a deferred model-list refresh of one test's window fires
    during the next test and reports that window's mode.
    """
    yield
    for widget in list(QApplication.topLevelWidgets()):
        widget.close()
        widget.deleteLater()
    for _ in range(3):
        QApplication.processEvents()
    QApplication.sendPostedEvents(None, 0)
    QApplication.processEvents()


@pytest.fixture
def window(app, tmp_path):
    """A window with no configuration file and no network access."""
    window = MainWindow(config_path=tmp_path / "config.yml")
    window._threadpool = SynchronousPool()
    window.model_name.setCurrentText("test-model:1b")
    yield window
    window.close()


@pytest.fixture
def fake_model(monkeypatch):
    model = RuleBasedFakeModel()
    monkeypatch.setattr(workers, "build_model", lambda *a, **k: model)
    return model


# =====================================================================
# design
# =====================================================================
def _emoji_free(text: str) -> bool:
    return not any(unicodedata.category(ch) == "So" for ch in text)


@pytest.mark.parametrize(
    "path", sorted(UI_DIR.glob("*.py")), ids=lambda p: p.name
)
def test_no_decorative_symbols_in_the_interface(path):
    """Labels and messages are text, not pictograms."""
    offenders = sorted(
        {ch for ch in path.read_text(encoding="utf-8") if unicodedata.category(ch) == "So"}
    )
    assert offenders == [], f"{path.name} contains {offenders}"


def test_visible_labels_are_plain_text(window):
    assert _emoji_free(window.windowTitle())
    assert _emoji_free(window.btn_run.text())
    assert _emoji_free(window.btn_check.text())
    for index in range(window.tabs.count()):
        assert _emoji_free(window.tabs.tabText(index))


def test_the_window_shows_only_tabs_the_pipeline_fills(window):
    labels = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert labels == [
        "Test drafts",
        "Requirements",
        "Findings",
        "Export table",
        "Log",
    ]


def test_dead_legacy_controls_are_gone(window):
    """Page blocks, parallel prompts, retrieval and the cache are unused."""
    for attribute in (
        "batch_size",
        "combine_pages",
        "parallel_prompts",
        "cases_per_prompt",
        "use_rag",
        "rag_top_k",
        "chunk_overlap",
        "use_cache",
        "cache_hours",
        "cache_entries",
    ):
        assert not hasattr(window, attribute), f"{attribute} still in the interface"


# =====================================================================
# configuration: read at startup, never written behind the user's back
# =====================================================================
def test_settings_are_loaded_from_the_configuration_file(app, tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "cloud": {"mode": "ollama_local", "host": "http://box.lan:11434",
                          "read_timeout_s": 123.0},
                "model": {"name": "configured-model:7b", "temperature": 0.7},
                "generation": {"max_cases_per_unit": 4, "max_repair_attempts": 1},
            }
        ),
        encoding="utf-8",
    )
    window = MainWindow(config_path=path)
    assert window.mode.currentText() == "ollama_local"
    assert window.host.text() == "http://box.lan:11434"
    assert window.read_timeout.value() == 123.0
    assert window.model_name.currentText() == "configured-model:7b"
    assert window.temperature.value() == pytest.approx(0.7)
    assert window.max_cases_per_unit.value() == 4
    assert window.max_repair_attempts.value() == 1


def test_a_missing_configuration_file_is_not_an_error(app, tmp_path):
    window = MainWindow(config_path=tmp_path / "absent.yml")
    assert window.mode.currentText()
    assert "No config.yml found" in window.log.toPlainText()


def test_runtime_changes_never_touch_the_configuration_file(window, tmp_path):
    path = tmp_path / "config.yml"
    original = "# a comment that must survive\ncloud:\n  mode: ollama_local\n"
    path.write_text(original, encoding="utf-8")
    window._config_path = path

    window.mode.setCurrentText("ollama_cloud")
    window.read_timeout.setValue(222.0)
    window.max_cases_per_unit.setValue(9)
    window.close()

    assert path.read_text(encoding="utf-8") == original, (
        "the file was written without being asked"
    )


def test_writing_the_configuration_is_an_explicit_action(window, tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(
        yaml.safe_dump({"experimental": {"flag": True}, "model": {"name": "old"}}),
        encoding="utf-8",
    )
    window._config_path = path
    window.model_name.setCurrentText("new-model:1b")
    window.max_cases_per_unit.setValue(7)

    window.write_config()

    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["model"]["name"] == "new-model:1b"
    assert saved["generation"]["max_cases_per_unit"] == 7
    assert saved["experimental"] == {"flag": True}, "unknown section dropped"


def test_no_api_key_is_ever_written_to_the_configuration(window, tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "super-secret")
    path = tmp_path / "config.yml"
    window._config_path = path
    window.write_config()
    assert "super-secret" not in path.read_text(encoding="utf-8")


def test_the_merge_helper_overlays_without_dropping():
    merged = merge_config(
        {"a": {"keep": 1, "change": 1}, "b": 1}, {"a": {"change": 2}, "c": 3}
    )
    assert merged == {"a": {"keep": 1, "change": 2}, "b": 1, "c": 3}


# =====================================================================
# the loaded configuration survives the trip to the worker
# =====================================================================
def test_settings_without_a_widget_are_not_replaced_by_defaults(app, tmp_path):
    """Load -> window -> worker must not lose values the window cannot edit."""
    path = tmp_path / "config.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "cloud": {"mode": "ollama_local", "max_retries": 0},
                "model": {"url": "http://custom-daemon:11434", "name": "m:1b"},
                "generation": {"input_char_budget": 1234, "job_timeout_s": 42},
            }
        ),
        encoding="utf-8",
    )
    from spec2test.bootstrap import (
        generation_settings_from_config,
        ollama_settings_from_config,
    )

    window = MainWindow(config_path=path)
    handed_over = window.collect_config()

    assert handed_over["model"]["url"] == "http://custom-daemon:11434"
    assert handed_over["cloud"]["max_retries"] == 0
    assert handed_over["generation"]["input_char_budget"] == 1234

    # and the values the worker actually derives from it
    settings = ollama_settings_from_config(handed_over, mode="ollama_local")
    assert settings.resolved_host == "http://custom-daemon:11434"
    assert settings.max_retries == 0
    limits = generation_settings_from_config(handed_over)
    assert limits.input_char_budget == 1234
    assert limits.job_timeout_s == 42


def test_unknown_sections_survive_the_trip_to_the_worker(app, tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(
        yaml.safe_dump(
            {"experimental": {"flag": True}, "rag": {"enabled": True, "top_k": 4}}
        ),
        encoding="utf-8",
    )
    handed_over = MainWindow(config_path=path).collect_config()
    assert handed_over["experimental"] == {"flag": True}
    assert handed_over["rag"] == {"enabled": True, "top_k": 4}


def test_edited_fields_do_win_over_the_file(app, tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "cloud": {"mode": "ollama_local", "read_timeout_s": 11},
                "generation": {"max_cases_per_unit": 2},
            }
        ),
        encoding="utf-8",
    )
    window = MainWindow(config_path=path)
    window.read_timeout.setValue(99.0)
    window.max_cases_per_unit.setValue(8)

    handed_over = window.collect_config()
    assert handed_over["cloud"]["read_timeout_s"] == 99.0
    assert handed_over["generation"]["max_cases_per_unit"] == 8


def test_the_job_budget_is_written_back_where_it_came_from(app, tmp_path):
    for section in ("cloud", "generation"):
        path = tmp_path / f"{section}.yml"
        path.write_text(
            yaml.safe_dump({section: {"job_timeout_s": 42}}), encoding="utf-8"
        )
        window = MainWindow(config_path=path)
        assert window.job_timeout.value() == 42, f"{section}: not shown in the window"
        window.job_timeout.setValue(55)
        handed_over = window.collect_config()
        assert handed_over[section]["job_timeout_s"] == 55.0

        from spec2test.bootstrap import generation_settings_from_config

        assert generation_settings_from_config(handed_over).job_timeout_s == 55.0


# =====================================================================
# endpoint settings reach the pipeline
# =====================================================================
@pytest.fixture
def recording_build_model(monkeypatch):
    recorded: dict = {}
    real = workers.build_model

    def recording(mode, settings=None, **kwargs):
        recorded["mode"] = mode
        recorded["settings"] = settings
        return real("fake")

    monkeypatch.setattr(workers, "build_model", recording)
    return recorded


@pytest.mark.parametrize(
    "mode,host,expected_host",
    [
        ("ollama_local", "", "http://localhost:11434"),
        ("ollama_local", "http://box.lan:11434", "http://box.lan:11434"),
        ("ollama_cloud", "", "https://ollama.com"),
        ("ollama_cloud", "https://ollama.example", "https://ollama.example"),
    ],
)
def test_mode_host_and_timeouts_reach_the_adapter(
    window, recording_build_model, spec_a, mode, host, expected_host
):
    window.mode.setCurrentText(mode)
    window.host.setText(host)
    window.connect_timeout.setValue(8.0)
    window.read_timeout.setValue(99.0)
    window.model_name.setCurrentText("some-model:1b")
    window._spec_paths = [spec_a]

    window.start_run()

    assert recording_build_model["mode"] == mode
    settings = recording_build_model["settings"]
    assert settings.mode == mode
    assert settings.resolved_host == expected_host
    assert settings.connect_timeout_s == 8.0
    assert settings.read_timeout_s == 99.0
    assert settings.model == "some-model:1b"


def test_gpu_options_are_local_only(window):
    window.mode.setCurrentText("ollama_local")
    assert window.gpu_box.isEnabled()
    window.mode.setCurrentText("ollama_cloud")
    assert not window.gpu_box.isEnabled()
    assert "OLLAMA_API_KEY" in window.lbl_auth.text()


def test_generation_limits_reach_the_pipeline(window, spec_a, monkeypatch):
    captured: dict = {}
    real = workers.generate_drafts

    def recording(requirements, model, **kwargs):
        captured["settings"] = kwargs.get("settings")
        return real(requirements, model, **kwargs)

    monkeypatch.setattr(workers, "generate_drafts", recording)
    monkeypatch.setattr(workers, "build_model", lambda *a, **k: RuleBasedFakeModel())
    window.max_cases_per_unit.setValue(2)
    window.max_repair_attempts.setValue(1)
    window.group_size.setValue(3)
    window.job_timeout.setValue(120)
    window._spec_paths = [spec_a]

    window.start_run()

    settings = captured["settings"]
    assert settings.max_cases_per_unit == 2
    assert settings.max_repair_attempts == 1
    assert settings.group_size == 3
    assert settings.job_timeout_s == 120


def test_the_guidance_file_is_appended_to_the_rules(window, spec_a, tmp_path, monkeypatch):
    guidance = tmp_path / "guidance.txt"
    guidance.write_text("Prefer HiL scenarios for this project.\n", encoding="utf-8")
    window._guidance_path = guidance

    model = ScriptedModel(["not json"] * 60)
    monkeypatch.setattr(workers, "build_model", lambda *a, **k: model)
    window._spec_paths = [spec_a]
    window.start_run()

    system = model.calls[0][0].content
    assert "schema_version" in system, "the contract rules were replaced"
    assert "Prefer HiL scenarios" in system, "the guidance was dropped"


# =====================================================================
# results
# =====================================================================
def test_drafts_requirements_and_sources_are_shown(window, fake_model, spec_a):
    window._spec_paths = [spec_a]
    window.start_run()

    assert window.requirements.topLevelItemCount() == 10
    assert window.drafts.topLevelItemCount() == 10
    assert "Test drafts (10)" == window.tabs.tabText(0)
    assert "Requirements (10)" == window.tabs.tabText(1)

    first = window.drafts.topLevelItem(0)
    labels = [first.child(i).text(0) for i in range(first.childCount())]
    assert any(label.startswith("Requirements:") for label in labels)
    assert any(label.startswith("Source:") for label in labels)
    assert any(label.startswith("Steps (") for label in labels)
    assert first.text(1).startswith("Draft"), "a draft never starts as approved"
    assert first.data(0, Qt.UserRole), "the tree item must carry its case id"

    assert window.btn_save_csv.isEnabled()
    assert window.table.model().rowCount() == 10
    assert "Drafts" in window.summary.toPlainText()


def test_the_findings_tab_lists_open_questions(window, fake_model, spec_a):
    window._spec_paths = [spec_a]
    window.start_run()
    html = window.findings.toHtml()
    assert "Open questions" in html
    assert "Completed" in html, "the run status must be named"
    assert "Endpoint" in html and "Units" in html, "the provenance must be shown"
    assert "10 planned" in html


def test_the_status_line_names_the_real_run_state(window, fake_model, spec_a):
    window._spec_paths = [spec_a]
    window.start_run()
    assert window.statusBar().currentMessage().startswith("Completed:")


def test_a_credential_error_stops_the_run(window, spec_a, monkeypatch):
    from spec2test.adapters.ollama import MissingApiKeyError
    from spec2test.application.ports import ModelDescriptor

    calls = {"n": 0}

    class KeylessCloud:
        @property
        def descriptor(self):
            return ModelDescriptor(
                model="m", endpoint="https://ollama.com/api/chat", mode="ollama_cloud"
            )

        def chat(self, messages, *, deadline=None):
            calls["n"] += 1
            raise MissingApiKeyError("no key")

    monkeypatch.setattr(workers, "build_model", lambda *a, **k: KeylessCloud())
    window._spec_paths = [spec_a]
    window.start_run()

    assert calls["n"] == 1, "a missing key must not be retried per requirement"
    assert window._run.status.value == "failed"
    assert len(window._run.not_started_units) == 9
    assert window.statusBar().currentMessage().startswith("Failed:")


def test_cancelling_reports_the_run_as_cancelled(window, fake_model, spec_a, monkeypatch):
    original = workers.DraftTask

    class CancellingTask(original):
        def run(self):
            self.cancel()
            super().run()

    monkeypatch.setattr("spec2test.ui.main_window.DraftTask", CancellingTask)
    window._spec_paths = [spec_a]
    window.start_run()

    assert window._run.status.value == "cancelled"
    assert window.statusBar().currentMessage().startswith("Cancelled:")


def test_a_run_without_a_document_is_refused(window, no_modal_dialogs):
    window._spec_paths = []
    window.start_run()
    assert "warning" in no_modal_dialogs


def test_a_run_without_a_model_name_is_refused(window, spec_a, no_modal_dialogs):
    window._spec_paths = [spec_a]
    window.model_name.setCurrentText("")
    window.start_run()
    assert "warning" in no_modal_dialogs


# =====================================================================
# model list
# =====================================================================
@pytest.mark.parametrize("configured_mode", [None, "ollama_local", "ollama_cloud"])
def test_building_the_window_performs_no_network_access(
    app, tmp_path, monkeypatch, configured_mode
):
    """Applying a configuration must not contact the endpoint either."""
    started: list = []
    monkeypatch.setattr(workers.ModelListTask, "run", lambda self: started.append(self))

    def refuse(*_a, **_k):  # pragma: no cover - must not run
        raise AssertionError("the GUI thread must not open a connection")

    monkeypatch.setattr(workers, "list_models", refuse)

    path = tmp_path / "config.yml"
    if configured_mode:
        path.write_text(
            yaml.safe_dump({"cloud": {"mode": configured_mode}}), encoding="utf-8"
        )
    window = MainWindow(config_path=path)
    window._threadpool = SynchronousPool()

    assert started == [], "construction must not contact the endpoint"
    if configured_mode:
        assert window.mode.currentText() == configured_mode

    # once the event loop runs, exactly one refresh happens - for the
    # configured mode, not for the combo box default
    for _ in range(3):
        QApplication.processEvents()
    assert len(started) == 1
    assert started[0].mode == (configured_mode or "ollama_local")


def test_the_model_list_runs_in_a_background_task(window, monkeypatch):
    seen: list[dict] = []

    def fake_run(self):
        seen.append(
            {
                "mode": self.mode,
                "timeouts": (self.CONNECT_TIMEOUT_S, self.READ_TIMEOUT_S),
            }
        )
        self.signals.models.emit(["listed-a", "listed-b"])

    monkeypatch.setattr(workers.ModelListTask, "run", fake_run)
    assert window.mode.currentText() == "ollama_local", "default without a file"

    window.mode.setCurrentText("ollama_cloud")  # a real change
    assert seen, "the mode change must trigger a refresh"
    assert seen[-1]["mode"] == "ollama_cloud", "the refresh used the wrong mode"
    assert seen[-1]["timeouts"] == (3.0, 5.0), "the list must use short timeouts"
    assert window.model_name.count() == 2

    window.mode.setCurrentText("ollama_local")
    assert seen[-1]["mode"] == "ollama_local"


def test_a_stale_window_cannot_pollute_a_later_refresh(app, tmp_path, monkeypatch):
    """The reported flake: a pending timer of an earlier window fired later.

    It then reported that window's mode and overwrote the observation of the
    test that was actually running.
    """
    seen: list[str] = []
    monkeypatch.setattr(
        workers.ModelListTask, "run", lambda self: seen.append(self.mode)
    )

    stale_config = tmp_path / "stale.yml"
    stale_config.write_text(
        yaml.safe_dump({"cloud": {"mode": "ollama_cloud"}}), encoding="utf-8"
    )
    stale = MainWindow(config_path=stale_config)
    stale._threadpool = SynchronousPool()
    stale.close()  # its refresh is still queued at this point

    current = MainWindow(config_path=tmp_path / "current.yml")
    current._threadpool = SynchronousPool()
    for _ in range(3):
        QApplication.processEvents()

    assert seen == ["ollama_local"], (
        f"a closed window still triggered a refresh: {seen}"
    )


def test_a_deferred_refresh_is_dropped_when_the_window_closes(app, tmp_path, monkeypatch):
    """A pending timer must not fire against a window that is already gone."""
    started: list = []
    monkeypatch.setattr(
        workers.ModelListTask, "run", lambda self: started.append(self.mode)
    )
    window = MainWindow(config_path=tmp_path / "config.yml")
    window._threadpool = SynchronousPool()
    window.close()
    for _ in range(3):
        QApplication.processEvents()
    assert started == [], "the deferred refresh outlived the window"


def test_a_loading_state_is_visible_while_the_list_is_fetched(window, monkeypatch):
    states: list = []

    class Pending:
        def start(self, task):
            states.append((window.btn_check.text(), window.btn_check.isEnabled()))

        def waitForDone(self, _ms=0):  # noqa: N802
            return True

    monkeypatch.setattr(workers.ModelListTask, "run", lambda self: None)
    window._threadpool = Pending()
    window.refresh_models()

    assert states == [("Loading models…", False)]
    assert "Loading model list" in window.statusBar().currentMessage()


def test_a_failed_list_restores_the_button_and_keeps_the_model_name(window, monkeypatch):
    monkeypatch.setattr(
        workers.ModelListTask,
        "run",
        lambda self: self.signals.failed.emit("connection refused"),
    )
    window.model_name.setCurrentText("my-model:7b")
    window.refresh_models()

    assert window.model_name.currentText() == "my-model:7b"
    assert window.btn_check.isEnabled()
    assert window.btn_check.text() == "Check connection and refresh models"
    assert "unavailable" in window.statusBar().currentMessage()


def test_a_closed_window_does_not_break_a_pending_request(window, monkeypatch):
    """The task must survive a receiver that is already gone."""
    task = workers.ModelListTask({"cloud": {"mode": "ollama_local"}}, "ollama_local")

    class Boom:
        def emit(self, _payload):
            raise RuntimeError("Signal source has been deleted")

    task._emit(Boom(), ["x"])  # must not raise
