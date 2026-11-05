# ─────────────────────────────────────────────────────────────────────────────
# file: gui.py
# ---------------------------------------------------------------------------
"""Graphical interface for the Test‑Specification-Generator.

-------------------------------
core.py           – business logic + save_csv/save_xlsx helpers
workers.py        – QRunnable that calls core.generate_test_cases
qmodels.py        – Qt table‑model wrapper
documentation.py  – PARAMETER_DOC (HTML help string)
config.yml        – optional defaults (can be empty)
"""

from __future__ import annotations

import sys
import time
import yaml
from pathlib import Path
from typing import Any, Final
from PySide6.QtCore import QSettings, QThreadPool, Qt
from PySide6.QtGui import QIcon, QCursor, QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableView,
    QTextBrowser,
    QTextEdit,
    QDoubleSpinBox,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

# local imports – these modules must exist in the same directory
from documentation import PARAMETER_DOC  # HTML help
from core import save_csv, save_xlsx, summarise_cases  # exporters & analytics
from spec2test.adapters.ollama import (  # model list via /api/tags
    MODE_LOCAL,
    OllamaChatClient,
    OllamaError,
    OllamaSettings,
    list_models,
)
from workers import GeneratorTask  # background worker
from qmodels import TestCaseTableModel  # table model wrapper
from config_models import (
    DEFAULT_MODEL_NAME,
    DEFAULT_OLLAMA_URL,
    AppConfig,
    CacheConfig,
    GenerationConfig,
    ModelConfig,
    PathConfig,
    RagConfig,
)

# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------
CFG_FILE: Final[Path] = Path(__file__).with_name("config.yml")


def _merge_config(existing: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Overlay *updates* on *existing* without dropping unknown keys."""
    merged = dict(existing)
    for key, value in updates.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _merge_config(current, value)
        else:
            merged[key] = value
    return merged

# ---------------------------------------------------------------------------
# Main Window class
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    """Qt MainWindow with controls, table preview, log & exporters."""

    _sanitised_once = False  # embed‑model name clean‑up guard

    # ------------------------------ init ----------------------------------
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AuroraSpecDominion")
        icon_path = Path(__file__).parent / "assets" / "icon.png"
        self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1280, 860)
        self._start_time: float | None = None  # stopwatch holder

        # runtime state
        self._threadpool = QThreadPool.globalInstance()
        self._settings = QSettings("acme", "tcg_gui")
        self._pdf_path: Path | None = None
        self._prompt_path: Path | None = None
        self._cases: list[dict] = []
        self._cache_dir: Path | None = CacheConfig().directory

        # build GUI
        self._build_ui()
        self._build_menubar()
        self._load_persisted()
        self._log("🏁 Ready.")

    # ──────────────────────────────────────────────────────────────────
    #  Helpers that were missing
    # ──────────────────────────────────────────────────────────────────
    def _log(self, msg: str) -> None:
        """Append *msg* to the log pane and also echo it to stdout."""
        if hasattr(self, "log"):
            self.log.append(msg)
            self.log.ensureCursorVisible()
        print(msg, flush=True)

    def _collect_cfg(self) -> dict[str, Any]:
        """Gather the current GUI values into a plain-dict config."""
        cfg = AppConfig(
            model=ModelConfig(
                name=self.model_name.currentText(),
                temperature=self.temperature.value(),
                top_p=self.top_p.value(),
                top_k=self.top_k.value(),
                max_tokens=self.max_tokens.value(),
                use_gpu=self.use_gpu.isChecked(),
                num_gpu=None if self.num_gpu.value() == -1 else self.num_gpu.value(),
                main_gpu=None if self.main_gpu.value() == -1 else self.main_gpu.value(),
                gpu_layers=None if self.gpu_layers.value() == -1 else self.gpu_layers.value(),
                num_thread=None if self.num_thread.value() == 0 else self.num_thread.value(),
            ),
            generation=GenerationConfig(
                batch_size=self.batch_size.value(),
                combine_pages=self.combine_pages.value(),
                cases_per_prompt=self.cases_per_prompt.value(),
                parallel_prompts=self.parallel_prompts.value(),
            ),
            rag=RagConfig(
                enabled=self.use_rag.isChecked(),
                top_k=self.rag_top_k.value(),
                chunk_overlap=self.chunk_overlap.value(),
            ),
            cache=CacheConfig(
                enabled=self.use_cache.isChecked(),
                directory=self._cache_dir,
                max_entries=self.cache_entries.value(),
                max_age_hours=self.cache_hours.value() or None,
            ),
            paths=PathConfig(
                pdf_path=self._pdf_path,
                prompt_path=self._prompt_path,
            ),
        )
        return cfg.to_dict()

    def _apply_config(self, cfg: AppConfig) -> None:
        self.model_name.setCurrentText(cfg.model.name)
        self.temperature.setValue(cfg.model.temperature)
        self.top_p.setValue(cfg.model.top_p)
        self.top_k.setValue(cfg.model.top_k)
        self.max_tokens.setValue(cfg.model.max_tokens)
        self.use_gpu.setChecked(cfg.model.use_gpu)
        self.num_gpu.setValue(cfg.model.num_gpu if cfg.model.num_gpu is not None else -1)
        self.main_gpu.setValue(
            cfg.model.main_gpu if cfg.model.main_gpu is not None else -1
        )
        self.gpu_layers.setValue(
            cfg.model.gpu_layers if cfg.model.gpu_layers is not None else -1
        )
        self.num_thread.setValue(cfg.model.num_thread or 0)
        self._update_gpu_controls(self.use_gpu.isChecked())

        self.batch_size.setValue(cfg.generation.batch_size)
        self.combine_pages.setValue(cfg.generation.combine_pages)
        self.cases_per_prompt.setValue(cfg.generation.cases_per_prompt)
        self.parallel_prompts.setValue(cfg.generation.parallel_prompts)

        self.use_rag.setChecked(cfg.rag.enabled)
        self.rag_top_k.setValue(cfg.rag.top_k)
        self.chunk_overlap.setValue(cfg.rag.chunk_overlap)
        self._sync_overlap_limit(self.combine_pages.value())
        self._update_rag_controls(self.use_rag.isChecked())

        self.use_cache.setChecked(cfg.cache.enabled)
        self.cache_hours.setValue(cfg.cache.max_age_hours or 0)
        self.cache_entries.setValue(cfg.cache.max_entries)
        self._cache_dir = cfg.cache.directory
        self.lbl_cache_dir.setText(self._cache_dir_display())
        self._update_cache_controls(self.use_cache.isChecked())

        self._pdf_path = None
        if cfg.paths.pdf_path and cfg.paths.pdf_path.exists():
            self._pdf_path = cfg.paths.pdf_path
            self.lbl_pdf.setText(self._pdf_path.name)
        else:
            self.lbl_pdf.setText("No PDF loaded")

        self._prompt_path = None
        if cfg.paths.prompt_path and cfg.paths.prompt_path.exists():
            self._prompt_path = cfg.paths.prompt_path
            self.lbl_prompt.setText(self._prompt_path.name)
        else:
            self.lbl_prompt.setText("No prompt loaded")

    def _update_rag_controls(self, checked: bool) -> None:
        self.rag_top_k.setEnabled(checked)
        self.chunk_overlap.setEnabled(checked)

    def _update_gpu_controls(self, checked: bool) -> None:
        for widget in (self.num_gpu, self.main_gpu, self.gpu_layers):
            widget.setEnabled(checked)

    def _sync_overlap_limit(self, value: int) -> None:
        maximum = max(0, value - 1)
        self.chunk_overlap.setMaximum(maximum)
        if self.chunk_overlap.value() > maximum:
            self.chunk_overlap.setValue(maximum)

    def _cache_dir_display(self) -> str:
        if self._cache_dir:
            return str(self._cache_dir)
        return "Not set"

    def _update_cache_controls(self, checked: bool) -> None:
        for widget in (self.cache_hours, self.cache_entries, self.btn_cache_dir):
            widget.setEnabled(checked)

    def _select_cache_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select cache directory",
            str(self._cache_dir) if self._cache_dir else "",
        )
        if directory:
            self._cache_dir = Path(directory)
            self.lbl_cache_dir.setText(self._cache_dir_display())
            self._log(f"💾 Cache directory set to: {self._cache_dir}")

    # ---------------- persistence (optional) -----------------
    def _load_persisted(self) -> None:
        """Restore last-used settings from *config.yml* (if it exists)."""
        if not CFG_FILE.exists():
            return
        try:
            with CFG_FILE.open() as fh:
                cfg_dict: dict[str, Any] = yaml.safe_load(fh) or {}
        except Exception as exc:  # malformed YAML, permissions, …
            self._log(f"⚠️  Couldn’t read {CFG_FILE.name}: {exc}")
            return

        cfg = AppConfig.from_dict(cfg_dict)
        self._apply_config(cfg)

    def _save_persisted(self) -> None:
        """Dump current GUI state to *config.yml*.

        The GUI only knows the keys it has widgets for. Everything else in the
        file - for example the ``cloud`` section used by the Spec2Test
        pipeline - is merged back unchanged, so saving from the GUI never drops
        settings it does not understand. Comments are not preserved by the YAML
        writer.
        """
        try:
            existing: dict[str, Any] = {}
            if CFG_FILE.exists():
                with CFG_FILE.open() as fh:
                    existing = yaml.safe_load(fh) or {}
            merged = _merge_config(existing, self._collect_cfg())
            with CFG_FILE.open("w") as fh:
                yaml.safe_dump(merged, fh, sort_keys=True, allow_unicode=True)
        except Exception as exc:
            self._log(f"⚠️  Couldn’t save {CFG_FILE.name}: {exc}")

    # make sure we persist on close
    def closeEvent(self, event):          # noqa: N802  (Qt naming)
        self._save_persisted()
        super().closeEvent(event)

    # ------------------------- menubar & help -----------------------------
    def _build_menubar(self) -> None:
        mb = QMenuBar(self)
        help_menu = mb.addMenu("&Help")
        act = QAction("About Parameters…", self)
        act.triggered.connect(self._show_param_doc)
        help_menu.addAction(act)
        self.setMenuBar(mb)

    def _show_param_doc(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Parameters")
        tb = QTextBrowser()
        tb.setHtml(PARAMETER_DOC)
        QVBoxLayout(dlg).addWidget(tb)
        dlg.resize(640, 520)
        dlg.exec()

    # ------------------------------- UI -----------------------------------
    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 8)

        # ---------- left‑hand controls ----------
        ctrl = QWidget(); v = QVBoxLayout(ctrl)
        splitter.addWidget(ctrl)

        # Model & sampling group
        gb_model = QGroupBox("Model"); form_m = QFormLayout(gb_model)
        self.model_name = QComboBox(editable=True); self._populate_models()
        self.temperature = QDoubleSpinBox(minimum=0.0, maximum=2.0, singleStep=0.1)
        self.top_p = QDoubleSpinBox(minimum=0.0, maximum=1.0, singleStep=0.05)
        self.top_k = QSpinBox(minimum=1, maximum=1000)
        self.max_tokens = QSpinBox(minimum=256, maximum=32768)
        for lbl, w in (
            ("🧠 Model:", self.model_name),
            ("🌡️ Temperature:", self.temperature),
            ("🎯 Top‑p:", self.top_p),
            ("🎯 Top‑k:", self.top_k),
            ("🔢 Max tokens:", self.max_tokens),
        ):
            form_m.addRow(lbl, w)

        self.use_gpu = QCheckBox("Enable GPU acceleration when available")
        self.use_gpu.setChecked(True)
        self.num_gpu = QSpinBox(); self.num_gpu.setRange(-1, 8)
        self.num_gpu.setSpecialValueText("Auto")
        self.main_gpu = QSpinBox(); self.main_gpu.setRange(-1, 8)
        self.main_gpu.setSpecialValueText("Auto")
        self.gpu_layers = QSpinBox(); self.gpu_layers.setRange(-1, 256)
        self.gpu_layers.setSpecialValueText("Auto")
        self.num_thread = QSpinBox(); self.num_thread.setRange(0, 128)
        self.num_thread.setSpecialValueText("Auto")

        form_m.addRow("⚡ GPU usage:", self.use_gpu)
        form_m.addRow("🧮 GPUs to use:", self.num_gpu)
        form_m.addRow("🎯 Primary GPU:", self.main_gpu)
        form_m.addRow("🧱 GPU layers:", self.gpu_layers)
        form_m.addRow("🧵 CPU threads:", self.num_thread)
        self.use_gpu.toggled.connect(self._update_gpu_controls)
        self._update_gpu_controls(self.use_gpu.isChecked())
        v.addWidget(gb_model)

        # Generation group
        gb_gen = QGroupBox("📄 Generation") 
        form_g = QFormLayout(gb_gen)

        self.batch_size = QSpinBox(minimum=1, maximum=100)
        self.combine_pages = QSpinBox(minimum=1, maximum=10)
        self.cases_per_prompt = QSpinBox(minimum=1, maximum=20)
        self.parallel_prompts = QSpinBox(minimum=1, maximum=20)

        for lbl, w in (
            ("📚 Batch size (pages):", self.batch_size),
            ("🧩 Combine pages:", self.combine_pages),
            ("📝 Cases / prompt:", self.cases_per_prompt),
            ("⚙️ Parallel prompts:", self.parallel_prompts),
        ):
            form_g.addRow(lbl, w)

        v.addWidget(gb_gen)

        # Context / RAG group
        gb_ctx = QGroupBox("🧠 Context Enhancements")
        form_c = QFormLayout(gb_ctx)
        self.use_rag = QCheckBox("Enable retrieval augmentation")
        self.rag_top_k = QSpinBox(minimum=1, maximum=5)
        self.chunk_overlap = QSpinBox(minimum=0, maximum=5)
        form_c.addRow("🔁 Retrieval:", self.use_rag)
        form_c.addRow("📑 Related chunks:", self.rag_top_k)
        form_c.addRow("🪟 Chunk overlap:", self.chunk_overlap)
        v.addWidget(gb_ctx)
        self.use_rag.toggled.connect(self._update_rag_controls)
        self.combine_pages.valueChanged.connect(self._sync_overlap_limit)
        self._sync_overlap_limit(self.combine_pages.value())
        self._update_rag_controls(self.use_rag.isChecked())

        # Cache / performance group
        gb_cache = QGroupBox("🚀 Performance & Cache")
        form_cache = QFormLayout(gb_cache)
        self.use_cache = QCheckBox("Enable disk cache for chunk responses")
        self.cache_hours = QSpinBox(minimum=0, maximum=240)
        self.cache_hours.setSpecialValueText("Unlimited")
        self.cache_hours.setValue(CacheConfig().max_age_hours or 0)
        self.cache_entries = QSpinBox(minimum=1, maximum=5000)
        self.cache_entries.setValue(CacheConfig().max_entries)
        self.lbl_cache_dir = QLabel(self._cache_dir_display())
        self.lbl_cache_dir.setWordWrap(True)
        self.btn_cache_dir = QPushButton("Choose…", clicked=self._select_cache_dir)

        wrap = QWidget(); wrap_layout = QHBoxLayout(wrap)
        wrap_layout.setContentsMargins(0, 0, 0, 0)
        wrap_layout.addWidget(self.lbl_cache_dir, 1)
        wrap_layout.addWidget(self.btn_cache_dir)

        form_cache.addRow("💾 Cache:", self.use_cache)
        form_cache.addRow("⏱️ Max age (hours):", self.cache_hours)
        form_cache.addRow("📦 Max entries:", self.cache_entries)
        form_cache.addRow("📁 Location:", wrap)
        v.addWidget(gb_cache)
        self.use_cache.toggled.connect(self._update_cache_controls)
        self._update_cache_controls(self.use_cache.isChecked())

        # Input files group
        gb_in = QGroupBox("📂 Input Files")  # Add emoji to group title
        vb_in = QVBoxLayout(gb_in)

        # Create buttons and keep references
        btn_pdf = QPushButton("📄 Load PDF…", clicked=self._select_pdf, parent=gb_in)
        btn_prompt = QPushButton("💬 Load Prompt…", clicked=self._select_prompt, parent=gb_in)

        # Ensure they expand similarly
        btn_pdf.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn_prompt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Synchronize button width
        max_width = max(btn_pdf.sizeHint().width(), btn_prompt.sizeHint().width())
        btn_pdf.setFixedWidth(max_width)
        btn_prompt.setFixedWidth(max_width)

        # PDF row
        hb_pdf = QHBoxLayout()
        self.lbl_pdf = QLabel("No PDF loaded")
        hb_pdf.addWidget(self.lbl_pdf, 1)
        hb_pdf.addWidget(btn_pdf)
        vb_in.addLayout(hb_pdf)

        # Prompt row
        hb_pr = QHBoxLayout()
        self.lbl_prompt = QLabel("No prompt loaded")
        hb_pr.addWidget(self.lbl_prompt, 1)
        hb_pr.addWidget(btn_prompt)
        vb_in.addLayout(hb_pr)

        # Add to main layout
        v.addWidget(gb_in)

        # Run controls
        gb_run = QGroupBox("Run"); hb_run = QHBoxLayout(gb_run)
        self.pb = QProgressBar(minimum=0, maximum=100)

        # Set the fixed height
        self.pb.setFixedHeight(30)

        # Allow horizontal expansion
        self.pb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Optional: Style for better appearance
        self.pb.setStyleSheet("""
            QProgressBar {
                border: 1px solid #999;
                border-radius: 5px;
                text-align: center;
                background-color: #333;
                height: 30px;  /* This is also needed here for stylesheet effects */
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                width: 1px;
            }
        """)
        self.btn_run = QPushButton("Generate", clicked=self._kick_off)
        hb_run.addWidget(self.pb, 1); hb_run.addWidget(self.btn_run)
        v.addWidget(gb_run)
        v.addStretch()

        # ---------- right‑hand preview ----------
        preview = QWidget(); pv = QVBoxLayout(preview)
        self.table = QTableView(); self.table.setModel(TestCaseTableModel())
        self.table.horizontalHeader().setStretchLastSection(True)
        pv.addWidget(self.table)

        gb_summary = QGroupBox("📈 Insights")
        sum_layout = QVBoxLayout(gb_summary)
        self.summary = QTextBrowser()
        self.summary.setReadOnly(True)
        self.summary.setHtml("<p><em>Generate cases to see coverage insights.</em></p>")
        sum_layout.addWidget(self.summary)
        pv.addWidget(gb_summary)
        splitter.addWidget(preview); splitter.setStretchFactor(1, 1)

        # ---------- log ----------
        self.log = QTextEdit(readOnly=True); layout.addWidget(self.log, 2)

        # ---------- export ----------
        export_row = QHBoxLayout()
        self.btn_save_csv = QPushButton("Save CSV…", clicked=self._save_csv)
        self.btn_save_xlsx = QPushButton("Save XLSX…", clicked=self._save_xlsx)
        for b in (self.btn_save_csv, self.btn_save_xlsx):
            b.setEnabled(False)
        export_row.addStretch(); export_row.addWidget(self.btn_save_csv); export_row.addWidget(self.btn_save_xlsx)
        layout.addLayout(export_row)

    # --------------------------- model list ----------------------------------
    def _populate_models(self) -> None:
        """Fill the model combo-box from /api/tags of the configured host.

        No CLI fallback: a model list obtained from the local CLI reports local
        aliases (``...-cloud``), which are not the names the cloud API expects.
        The combo stays editable, so a manual name is always possible.
        """
        host = DEFAULT_OLLAMA_URL
        mode = MODE_LOCAL
        try:
            settings = OllamaSettings(model="unused", mode=mode, host=host,
                                      connect_timeout_s=3.0, read_timeout_s=5.0,
                                      max_retries=0)
            models = list_models(OllamaChatClient(settings))
            self._log(f"🧠 {len(models)} model(s) reported by {host}/api/tags")
        except OllamaError as exc:
            models = []
            self._log(f"⚠️ Model list unavailable ({exc}). Enter a model name manually.")

        if not models:
            models = [DEFAULT_MODEL_NAME]
        self.model_name.addItems(models)

    # ----------------------- file selection -------------------------------
    def _select_pdf(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Select requirements PDF", filter="PDF (*.pdf)")
        if fn:
            self._pdf_path = Path(fn)
            self.lbl_pdf.setText(self._pdf_path.name)
            self._log(f"📄 PDF selected: {self._pdf_path.name}")

    def _select_prompt(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Select role‑prompt file", filter="Text (*.txt)")
        if fn:
            self._prompt_path = Path(fn)
            self.lbl_prompt.setText(self._prompt_path.name)
            self._log(f"📝 Prompt file: {self._prompt_path.name}")

    # ----------------------------- run ------------------------------------
    def _kick_off(self):
        if not self._pdf_path:
            QMessageBox.warning(self, "Missing PDF", "Load a PDF first.")
            return
        QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
        self.pb.setValue(0)
        self._start_time = time.perf_counter()      # start the clock
        self.summary.setHtml("<p><em>Generating…</em></p>")

        cfg = self._collect_cfg()
        task = GeneratorTask(pdf=self._pdf_path, cfg=cfg)
        task.signals.progress.connect(self.pb.setValue)
        task.signals.message.connect(self._log)
        task.signals.status.connect(self._on_status)
        task.signals.finished.connect(self._on_done)
        task.signals.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        self._threadpool.start(task)
        self.statusBar().showMessage("Generation running…")

    def _on_status(self, status: dict) -> None:
        """Remember the run status so the result is not reported as success."""
        self._run_status = status
        for line in status.get("report", []):
            self._log(line)

    def _on_done(self, cases):
        QApplication.restoreOverrideCursor()

        # stop the clock
        elapsed = None
        if self._start_time is not None:
            elapsed = time.perf_counter() - self._start_time
            self._start_time = None

        # update UI
        self._cases = cases
        self.table.model().update_cases(cases)
        self.pb.setValue(100)
        self.btn_save_csv.setEnabled(bool(cases))
        self.btn_save_xlsx.setEnabled(bool(cases))
        self._render_summary()

        # build the log/status message - a finished run is not a successful one
        status = getattr(self, "_run_status", None) or {}
        state = status.get("status", "unknown")
        icon = {
            "completed": "✅",
            "partial": "⚠️",
            "failed": "❌",
            "cancelled": "⏹️",
        }.get(state, "ℹ️")
        msg = f"{icon} Run {state}: {len(cases)} case(s)."
        rejected = status.get("rejected_blocks") or 0
        if rejected:
            msg += f" {rejected} block(s) rejected - see the log."
        if elapsed is not None:
            msg += f" ⏱️ Finished in {elapsed:.1f} s."

        # emit it
        self._log(msg)
        self.statusBar().showMessage(msg)

    # -------------------------- exporters ---------------------------------
    def _save_csv(self):
        if not self._cases:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save as CSV", filter="CSV files (*.csv)")
        if path:
            save_csv(self._cases, path)
            QMessageBox.information(self, "Saved", f"CSV written to:\n{path}")

    def _save_xlsx(self):
        if not self._cases:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save as Excel", filter="Excel Workbook (*.xlsx)")
        if path:
            save_xlsx(self._cases, path)
            QMessageBox.information(self, "Saved", f"Excel written to:\n{path}")

    def _render_summary(self) -> None:
        if not self._cases:
            self.summary.setHtml("<p><em>Generate cases to see coverage insights.</em></p>")
            return

        stats = summarise_cases(self._cases)
        missing = stats["missing_fields"]
        missing_required = {k: v for k, v in missing.items() if k in ("objective", "actions", "expected_results", "postconditions")}
        top_reqs = stats["top_requirements"]
        duplicates = stats["duplicate_names"]

        html = ["<h3>Coverage snapshot</h3>"]
        html.append(
            "<ul>"
            f"<li><strong>Total cases:</strong> {stats['total_cases']}</li>"
            f"<li><strong>Complete cases:</strong> {stats['complete_cases']}</li>"
            f"<li><strong>Unique requirements referenced:</strong> {stats['unique_requirements']}</li>"
            f"<li><strong>Average steps:</strong> {stats['avg_actions']} actions / {stats['avg_expected_results']} expected results</li>"
            "</ul>"
        )

        if any(missing_required.values()):
            html.append("<h4>Missing required fields</h4><ul>")
            for field, count in missing_required.items():
                if count:
                    html.append(f"<li>{field.replace('_', ' ').title()}: {count}</li>")
            html.append("</ul>")

        if top_reqs:
            html.append("<h4>Most referenced requirements</h4><ol>")
            for req, count in top_reqs:
                html.append(f"<li>{req} — {count} cases</li>")
            html.append("</ol>")

        if duplicates:
            dup_list = ", ".join(sorted(duplicates))
            html.append(
                f"<p style='color:#d35400'><strong>Duplicate names detected:</strong> {dup_list}</p>"
            )

        self.summary.setHtml("\n".join(html))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()