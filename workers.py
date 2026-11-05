# ─────────────────────────────────────────────────────────────────────────────
# file: workers.py
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import traceback

from pathlib import Path
from typing import Any, Dict, List

from PySide6.QtCore import QObject, QRunnable, Signal

from config_models import AppConfig
from core import AgentLocal, PromptCache, generate_test_cases, load_prompt_file
from core import pdf_to_text_pages
from spec2test.adapters.persistence import JsonRunStore
from spec2test.application.generate import ProgressUpdate, generate_drafts
from spec2test.application.ingest import content_hash, import_documents
from spec2test.application.legacy import legacy_rows
from spec2test.application.ports import ModelError
from spec2test.bootstrap import (
    build_importers,
    build_model,
    generation_settings_from_config,
    load_env_file,
    ollama_settings_from_config,
)


class _Signals(QObject):
    """Qt signals container so we can emit from the worker thread."""

    progress = Signal(int)          # 0‑100 % - "finished", not "successful"
    message = Signal(str)           # log lines
    status = Signal(dict)           # run status before the result is delivered
    finished = Signal(list)         # list[dict] with test‑case objects
    error = Signal(str)


class GeneratorTask(QRunnable):
    """Runs test‑case generation in a background thread.

    Parameters
    ----------
    pdf : pathlib.Path | str  – requirements PDF to parse
    cfg : dict                – full config dict assembled by GUI (model + generation)
    """

    def __init__(self, pdf: Path | str, cfg: dict[str, Any]):
        super().__init__()
        self.pdf = Path(pdf)
        self.cfg = cfg
        self.signals = _Signals()
        self._cancelled = False

    def cancel(self) -> None:
        """Stop starting new requests. A running request ends within its timeout."""
        self._cancelled = True

    # ------------------------------------------------------------------
    # Qt entry‑point
    # ------------------------------------------------------------------
    def run(self):
        try:
            self.signals.message.emit("👊 Parsing PDF…")
            # One PDF parser for the whole application (PyMuPDF via the adapter)
            try:
                num_pages: object = len(pdf_to_text_pages(str(self.pdf)))
            except Exception as exc:
                self.signals.error.emit(f"PDF could not be read: {exc}")
                return
            self.signals.message.emit(f"📑 Parsed {num_pages} pages, starting generation…")

            # Build agent configuration
            cfg = AppConfig.from_dict(self.cfg)
            model_cfg = cfg.model
            prompt_role = load_prompt_file(cfg.paths.prompt_path)

            # One shared conversion for GUI, worker and CLI: mode, host and
            # timeouts come from the configuration, never from a local default.
            settings = ollama_settings_from_config(self.cfg, mode=cfg.cloud.mode)
            mode = settings.mode
            agent = AgentLocal(
                settings=settings,
                system_prompt=prompt_role,
                use_gpu=model_cfg.use_gpu,
            )

            self.signals.message.emit(
                f"🌐 mode={mode} endpoint={agent.settings.endpoint}"
            )
            if agent.ensure_server():
                self.signals.message.emit("🌐 Endpoint reachable")
            else:
                self.signals.message.emit(
                    "⚠️ Endpoint not reachable or credentials rejected. "
                    "Generation will report the error; the local Ollama CLI is "
                    "never started as a fallback."
                )

            gen_cfg = cfg.generation
            rag_cfg = cfg.rag

            cache = None
            cache_cfg = cfg.cache
            if cache_cfg.enabled and cache_cfg.directory:
                try:
                    cache = PromptCache(
                        cache_cfg.directory,
                        max_entries=cache_cfg.max_entries,
                        max_age_hours=cache_cfg.max_age_hours,
                    )
                    self.signals.message.emit(
                        f"💾 Using cache at {cache.path}"
                    )
                except Exception as exc:
                    cache = None
                    self.signals.message.emit(
                        f"⚠️ Cache disabled due to error: {exc}"
                    )

            cases, run = generate_test_cases(
                pdf_path=str(self.pdf),
                agent=agent,
                n_cases=gen_cfg.cases_per_prompt,
                combine=gen_cfg.combine_pages,
                prompt_role=prompt_role,
                use_rag=rag_cfg.enabled,
                rag_top_k=rag_cfg.top_k,
                chunk_overlap=rag_cfg.chunk_overlap,
                progress_cb=self.signals.progress.emit,
                logger=self.signals.message.emit,
                cache=cache,
                should_cancel=lambda: self._cancelled,
                return_run=True,
            )

            # The status says whether the run worked; 100 % only means it ended.
            self.signals.status.emit(
                {
                    "status": run.status.value,
                    "accepted_blocks": len(run.accepted_units),
                    "rejected_blocks": len(run.rejected_units),
                    "report": run.report_lines(),
                }
            )
            self.signals.finished.emit(cases)
        except Exception as exc:
            self.signals.error.emit(str(exc))
            self.signals.message.emit(traceback.format_exc())

class _DraftSignals(QObject):
    """Signals of the traceable pipeline. Only plain data crosses the thread."""

    progress = Signal(int)          # 0-100 %; "finished", not "successful"
    message = Signal(str)
    requirements = Signal(object)   # list[Requirement], before generation starts
    finished = Signal(object)       # GenerationRun
    error = Signal(str)


class DraftTask(QRunnable):
    """Runs the Spec2Test pipeline: import, generate, validate, store.

    Replaces :class:`GeneratorTask` for the GUI. Requirements keep their source
    spans, drafts keep their steps, findings and open questions, and the run
    status distinguishes completed, partial, failed and cancelled.
    """

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

    def run(self):  # Qt entry point
        try:
            for name in load_env_file():
                self.signals.message.emit(f"🔐 {name} loaded from .env")

            app_cfg = AppConfig.from_dict(self.cfg)
            settings = ollama_settings_from_config(self.cfg, mode=app_cfg.cloud.mode)
            self.signals.message.emit(
                f"🌐 mode={settings.mode} endpoint={settings.endpoint} "
                f"model={settings.model}"
            )
            try:
                # the configured mode decides, never the derived settings
                model = build_model(
                    app_cfg.cloud.mode, settings, log=self.signals.message.emit
                )
            except ValueError as exc:
                self.signals.error.emit(f"Configuration error: {exc}")
                return

            # ---------------- import ----------------
            if not self.specs:
                self.signals.error.emit("No specification selected.")
                return
            self.signals.message.emit(
                f"📄 Importing {len(self.specs)} document(s)…"
            )
            try:
                results = import_documents(self.specs, build_importers())
            except (OSError, ValueError) as exc:
                self.signals.error.emit(f"Import failed: {exc}")
                return

            requirements = []
            for result in results:
                doc = result.document
                self.signals.message.emit(
                    f"📑 {doc.filename}: {doc.format.value}, {doc.page_count} page(s), "
                    f"{len(result.requirements)} requirement(s)"
                )
                for warning in result.warnings:
                    self.signals.message.emit(f"⚠️ {warning}")
                for issue in result.issues:
                    self.signals.message.emit(f"ℹ️ {issue}")
                requirements.extend(result.requirements)

            if not requirements:
                self.signals.error.emit(
                    "No requirement could be captured from the selected document(s)."
                )
                return
            self.signals.requirements.emit(requirements)

            # ---------------- generate ----------------
            gen_settings = generation_settings_from_config(self.cfg)
            if app_cfg.generation.cases_per_prompt:
                gen_settings.max_cases_per_unit = app_cfg.generation.cases_per_prompt
            if app_cfg.cloud.job_timeout_s is not None:
                gen_settings.job_timeout_s = app_cfg.cloud.job_timeout_s
            prompt_role = load_prompt_file(app_cfg.paths.prompt_path)
            if prompt_role:
                gen_settings.system_prompt = prompt_role
                self.signals.message.emit("💬 Using the selected prompt file")

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
                    source_hash=content_hash(
                        "\n".join(r.text for r in requirements)
                    ),
                    progress=on_progress,
                    log=self.signals.message.emit,
                    should_cancel=lambda: self._cancelled,
                )
            except ModelError as exc:
                self.signals.error.emit(f"{type(exc).__name__}: {exc}")
                return

            # ---------------- store ----------------
            try:
                path = JsonRunStore(self.output_dir).save_run(run)
                self.signals.message.emit(f"💾 Run saved: {path}")
            except OSError as exc:
                self.signals.message.emit(f"⚠️ Run could not be saved: {exc}")

            self.signals.progress.emit(100)
            self.signals.finished.emit(run)
        except Exception as exc:  # pragma: no cover - last resort for the GUI
            self.signals.error.emit(str(exc))
            self.signals.message.emit(traceback.format_exc())


__all__ = ["DraftTask", "GeneratorTask", "legacy_rows"]
