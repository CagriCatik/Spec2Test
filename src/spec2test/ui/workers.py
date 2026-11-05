"""Qt workers. They call use cases and emit plain data.

No Qt type crosses into the application, and no HTTP object reaches the views.
The legacy flat pipeline is no longer wrapped here; it stays reachable for
scripts through ``core.generate_test_cases``.
"""
from __future__ import annotations

import dataclasses
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal

from ..adapters.documents import load_guidance_file
from ..adapters.ollama import OllamaChatClient, OllamaError, list_models
from ..adapters.persistence import JsonRunStore
from ..application.generate import ProgressUpdate, generate_drafts
from ..application.ingest import content_hash, import_documents
from ..application.ports import ModelError
from ..bootstrap import (
    build_importers,
    build_model,
    generation_settings_from_config,
    load_env_file,
    ollama_settings_from_config,
)
from ..config import AppConfig


class _ModelListSignals(QObject):
    models = Signal(object)   # list[str]
    failed = Signal(str)


class ModelListTask(QRunnable):
    """Fetches /api/tags off the GUI thread, with short timeouts."""

    CONNECT_TIMEOUT_S = 3.0
    READ_TIMEOUT_S = 5.0

    def __init__(self, cfg: dict[str, Any], mode: str) -> None:
        super().__init__()
        self.cfg = cfg
        self.mode = mode
        self.signals = _ModelListSignals()

    def _emit(self, signal, payload) -> None:
        """A window closed while the request was in flight is not an error."""
        try:
            signal.emit(payload)
        except RuntimeError:  # the receiver is already gone
            pass

    def run(self):  # Qt entry point
        try:
            settings = ollama_settings_from_config(
                self.cfg, mode=self.mode, model="unused"
            )
            settings = dataclasses.replace(
                settings,
                connect_timeout_s=self.CONNECT_TIMEOUT_S,
                read_timeout_s=self.READ_TIMEOUT_S,
                max_retries=0,
            )
            self._emit(self.signals.models, list_models(OllamaChatClient(settings)))
        except OllamaError as exc:
            self._emit(self.signals.failed, str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self._emit(self.signals.failed, f"{type(exc).__name__}: {exc}")


class _DraftSignals(QObject):
    """Only plain data crosses the thread boundary."""

    progress = Signal(int)          # 0-100 %; "finished", not "successful"
    message = Signal(str)
    requirements = Signal(object)   # list[Requirement], before generation starts
    finished = Signal(object)       # GenerationRun
    error = Signal(str)


class DraftTask(QRunnable):
    """Import documents, generate drafts, validate them, store the run."""

    def __init__(
        self,
        specs: list[Path] | list[str],
        cfg: dict[str, Any],
        *,
        output_dir: Path | str | None = None,
    ) -> None:
        super().__init__()
        self.specs = [Path(spec) for spec in specs]
        self.cfg = cfg
        self.output_dir = Path(output_dir) if output_dir else Path.cwd() / "runs"
        self.signals = _DraftSignals()
        self._cancelled = False

    def cancel(self) -> None:
        """Stop starting new requests; a running one ends within its timeout."""
        self._cancelled = True

    # ------------------------------------------------------------------
    def run(self):  # Qt entry point
        try:
            self._run()
        except Exception as exc:  # pragma: no cover - last resort for the GUI
            self.signals.error.emit(str(exc))
            self.signals.message.emit(traceback.format_exc())

    def _run(self) -> None:
        for name in load_env_file():
            self.signals.message.emit(f"{name} loaded from .env")

        app_cfg = AppConfig.from_dict(self.cfg)
        settings = ollama_settings_from_config(self.cfg, mode=app_cfg.cloud.mode)
        self.signals.message.emit(
            f"Mode {settings.mode}, endpoint {settings.endpoint}, "
            f"model {settings.model}"
        )
        try:
            # the configured mode decides, never the derived settings
            model = build_model(
                app_cfg.cloud.mode, settings, log=self.signals.message.emit
            )
        except ValueError as exc:
            self.signals.error.emit(f"Configuration error: {exc}")
            return

        requirements = self._import()
        if requirements is None:
            return
        self.signals.requirements.emit(requirements)

        gen_settings = generation_settings_from_config(self.cfg)
        guidance = load_guidance_file(app_cfg.paths.prompt_path)
        if guidance:
            gen_settings.domain_guidance = guidance
            self.signals.message.emit(
                "Prompt file added as project guidance; the contract rules stay "
                "in force"
            )
        self.signals.message.emit(
            f"Limits: at most {gen_settings.max_cases_per_unit} draft(s) per unit, "
            f"{gen_settings.max_repair_attempts} repair(s), "
            f"{gen_settings.group_size} requirement(s) per request, "
            f"budget {gen_settings.job_timeout_s}s"
        )

        def on_progress(update: ProgressUpdate) -> None:
            self.signals.progress.emit(update.percent)
            self.signals.message.emit(
                f"[{update.percent:3d}%] {update.done}/{update.total} "
                f"{update.unit_id}: {update.note}"
            )

        try:
            run = generate_drafts(
                requirements,
                model,
                settings=gen_settings,
                source_hash=content_hash("\n".join(r.text for r in requirements)),
                progress=on_progress,
                log=self.signals.message.emit,
                should_cancel=lambda: self._cancelled,
            )
        except ModelError as exc:
            self.signals.error.emit(f"{type(exc).__name__}: {exc}")
            return

        try:
            path = JsonRunStore(self.output_dir).save_run(run)
            self.signals.message.emit(f"Run saved to {path}")
        except OSError as exc:
            self.signals.message.emit(f"Run could not be saved: {exc}")

        self.signals.progress.emit(100)
        self.signals.finished.emit(run)

    def _import(self):
        if not self.specs:
            self.signals.error.emit("No specification selected.")
            return None
        self.signals.message.emit(f"Importing {len(self.specs)} document(s)")
        try:
            results = import_documents(self.specs, build_importers())
        except (OSError, ValueError) as exc:
            self.signals.error.emit(f"Import failed: {exc}")
            return None

        requirements = []
        for result in results:
            document = result.document
            self.signals.message.emit(
                f"{document.filename}: {document.format.value}, "
                f"{document.page_count} page(s), "
                f"{len(result.requirements)} requirement(s)"
            )
            for warning in result.warnings:
                self.signals.message.emit(f"Warning: {warning}")
            for issue in result.issues:
                self.signals.message.emit(str(issue))
            requirements.extend(result.requirements)

        if not requirements:
            self.signals.error.emit(
                "No requirement could be captured from the selected document(s)."
            )
            return None
        return requirements


__all__ = ["DraftTask", "ModelListTask"]
