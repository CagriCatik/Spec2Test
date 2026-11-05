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
    QLineEdit,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
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
    API_KEY_ENV,
    OllamaChatClient,
    OllamaError,
    capabilities_for,
    check_model_choice,
    list_models,
)
from spec2test.bootstrap import ollama_settings_from_config
from workers import DraftTask, GeneratorTask  # background workers
from spec2test.application.legacy import legacy_rows
from qmodels import TestCaseTableModel  # table model wrapper
from config_models import (
    DEFAULT_CONNECT_TIMEOUT_S,
    DEFAULT_MODEL_NAME,
    DEFAULT_OLLAMA_URL,
    DEFAULT_READ_TIMEOUT_S,
    MODE_CLOUD,
    MODE_LOCAL,
    AppConfig,
    CloudConfig,
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
        self._spec_paths: list[Path] = []
        self._run = None
        self._task = None
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
            cloud=self._cloud_config(),
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
        self.mode.setCurrentText(cfg.cloud.mode)
        self.host.setText(cfg.cloud.host or "")
        self.connect_timeout.setValue(cfg.cloud.connect_timeout_s)
        self.read_timeout.setValue(cfg.cloud.read_timeout_s)
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
        is_local = self.mode.currentText() == MODE_LOCAL
        for widget in (self.num_gpu, self.main_gpu, self.gpu_layers):
            widget.setEnabled(bool(checked) and is_local)

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
        # remember the state as loaded, so an unchanged session does not
        # rewrite the file and strip its comments
        self._loaded_cfg = self._collect_cfg()

    def _save_persisted(self) -> None:
        """Write the current GUI state to *config.yml* if it changed.

        The GUI only knows the keys it has widgets for. Everything else in the
        file is merged back unchanged, so saving never drops settings the GUI
        does not understand.

        Nothing is written when no value changed: the YAML writer cannot keep
        comments, so merely opening and closing the window must not strip the
        documentation from a hand-edited file.
        """
        current = self._collect_cfg()
        if current == getattr(self, "_loaded_cfg", None):
            return
        try:
            existing: dict[str, Any] = {}
            if CFG_FILE.exists():
                with CFG_FILE.open() as fh:
                    existing = yaml.safe_load(fh) or {}
            merged = _merge_config(existing, current)
            with CFG_FILE.open("w") as fh:
                yaml.safe_dump(merged, fh, sort_keys=True, allow_unicode=True)
            self._loaded_cfg = current
            self._log(f"💾 Settings written to {CFG_FILE.name}")
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

        # Endpoint group - the mode is always explicit
        gb_ep = QGroupBox("Endpoint"); form_ep = QFormLayout(gb_ep)
        self.mode = QComboBox()
        self.mode.addItems([MODE_CLOUD, MODE_LOCAL])
        self.host = QLineEdit()
        self.host.setPlaceholderText("leave empty for the default host of the mode")
        self.connect_timeout = QDoubleSpinBox(minimum=1.0, maximum=120.0, singleStep=1.0)
        self.connect_timeout.setValue(DEFAULT_CONNECT_TIMEOUT_S)
        self.read_timeout = QDoubleSpinBox(minimum=5.0, maximum=3600.0, singleStep=10.0)
        self.read_timeout.setValue(DEFAULT_READ_TIMEOUT_S)
        self.btn_check = QPushButton("Check connection / refresh models")
        for lbl, w in (
            ("🔀 Mode:", self.mode),
            ("🌐 Host:", self.host),
            ("⏱️ Connect timeout (s):", self.connect_timeout),
            ("⏱️ Read timeout (s):", self.read_timeout),
        ):
            form_ep.addRow(lbl, w)
        form_ep.addRow(self.btn_check)
        v.addWidget(gb_ep)

        # Model & sampling group
        gb_model = QGroupBox("Model"); form_m = QFormLayout(gb_model)
        self.model_name = QComboBox(editable=True)
        self.mode.currentTextChanged.connect(self._on_mode_changed)
        self.btn_check.clicked.connect(self._populate_models)
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
        # fill the model list once every endpoint widget exists
        self._populate_models()
        self._on_mode_changed(self.mode.currentText())
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
        btn_pdf = QPushButton("📄 Load spec…", clicked=self._select_pdf, parent=gb_in)
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
        self.btn_cancel = QPushButton("Cancel", clicked=self._cancel_run)
        self.btn_cancel.setEnabled(False)
        hb_run.addWidget(self.pb, 1)
        hb_run.addWidget(self.btn_run)
        hb_run.addWidget(self.btn_cancel)
        v.addWidget(gb_run)
        v.addStretch()

        # ---------- right‑hand preview ----------
        preview = QWidget(); pv = QVBoxLayout(preview)

        self.tabs = QTabWidget()

        # test drafts with their steps, expectations and open questions
        self.drafts = QTreeWidget()
        self.drafts.setHeaderLabels(["Test draft", "Status"])
        self.drafts.setColumnWidth(0, 520)
        self.tabs.addTab(self.drafts, "🧪 Drafts")

        # captured requirements with their source positions
        self.requirements = QTreeWidget()
        self.requirements.setHeaderLabels(["Requirement / source", "Clarification"])
        self.requirements.setColumnWidth(0, 520)
        self.tabs.addTab(self.requirements, "🔗 Requirements")

        # validation findings, rejected and unprocessed requirements
        self.findings = QTextBrowser()
        self.findings.setHtml("<p><em>Generate drafts to see the findings.</em></p>")
        self.tabs.addTab(self.findings, "🔍 Findings")

        # flat table: feeds the exporters and the insight panel
        self.table = QTableView(); self.table.setModel(TestCaseTableModel())
        self.table.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.table, "📋 Flat table")

        pv.addWidget(self.tabs)

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
    def _cloud_config(self) -> CloudConfig:
        """The endpoint settings currently selected in the GUI."""
        return CloudConfig(
            mode=self.mode.currentText(),
            host=self.host.text().strip() or None,
            connect_timeout_s=self.connect_timeout.value(),
            read_timeout_s=self.read_timeout.value(),
        )

    def _on_mode_changed(self, mode: str) -> None:
        """GPU options exist only for local operation."""
        is_local = mode == MODE_LOCAL
        self.use_gpu.setEnabled(is_local)
        self._update_gpu_controls(is_local and self.use_gpu.isChecked())
        if mode == MODE_CLOUD:
            self._log(
                f"🔀 Mode {mode}: the API key is read from ${API_KEY_ENV}, "
                "never stored and never logged. GPU options do not apply."
            )
        else:
            self._log(f"🔀 Mode {mode}: no authentication, GPU options apply.")
        self._populate_models()

    def _populate_models(self) -> None:
        """Fill the model combo-box from /api/tags of the configured host.

        No CLI fallback: a model list from the local CLI reports local aliases
        (``...-cloud``), which are not the names the cloud API expects. The
        combo stays editable, so a manual name is always possible.
        """
        cloud = self._cloud_config()
        settings = ollama_settings_from_config(
            {"cloud": cloud.to_dict(), "model": {"name": "unused"}},
            mode=cloud.mode,
            model="unused",
        )
        selected = self.model_name.currentText()
        try:
            models = list_models(OllamaChatClient(settings))
            self._log(
                f"🧠 {len(models)} model(s) reported by {settings.resolved_host}/api/tags"
            )
        except OllamaError as exc:
            models = []
            self._log(f"⚠️ Model list unavailable ({exc}). Enter a model name manually.")

        self.model_name.clear()
        self.model_name.addItems(models or [DEFAULT_MODEL_NAME])
        if selected:
            self.model_name.setCurrentText(selected)
            for warning in check_model_choice(selected, models, cloud.mode):
                self._log(f"⚠️ {warning}")
            for note in capabilities_for(selected, cloud.mode).notes:
                self._log(f"ℹ️ {note}")

    # ----------------------- file selection -------------------------------
    def _select_pdf(self):
        """Select one or more specifications: PDF, TXT or Markdown."""
        names, _ = QFileDialog.getOpenFileNames(
            self,
            "Select requirement documents",
            filter="Specifications (*.pdf *.txt *.md *.markdown);;All files (*)",
        )
        if not names:
            return
        self._spec_paths = [Path(n) for n in names]
        self._pdf_path = self._spec_paths[0]  # kept for the legacy path
        label = (
            self._spec_paths[0].name
            if len(self._spec_paths) == 1
            else f"{len(self._spec_paths)} documents"
        )
        self.lbl_pdf.setText(label)
        for path in self._spec_paths:
            self._log(f"📄 Selected: {path.name}")

    def _select_prompt(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Select role‑prompt file", filter="Text (*.txt)")
        if fn:
            self._prompt_path = Path(fn)
            self.lbl_prompt.setText(self._prompt_path.name)
            self._log(f"📝 Prompt file: {self._prompt_path.name}")

    # ----------------------------- run ------------------------------------
    def _specs(self) -> list[Path]:
        paths = list(getattr(self, "_spec_paths", []) or [])
        if not paths and self._pdf_path:
            paths = [self._pdf_path]
        return paths

    def _kick_off(self):
        """Run the traceable pipeline: import, generate, validate, store."""
        specs = self._specs()
        if not specs:
            QMessageBox.warning(
                self, "No document", "Load a PDF, TXT or Markdown file first."
            )
            return
        QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
        self.pb.setValue(0)
        self._start_time = time.perf_counter()
        self._run = None
        self.summary.setHtml("<p><em>Generating…</em></p>")
        self.findings.setHtml("<p><em>Running…</em></p>")
        self.drafts.clear()
        self.requirements.clear()

        task = DraftTask(specs, self._collect_cfg())
        self._task = task
        task.signals.progress.connect(self.pb.setValue)
        task.signals.message.connect(self._log)
        task.signals.requirements.connect(self._show_requirements)
        task.signals.finished.connect(self._on_run_finished)
        task.signals.error.connect(self._on_error)
        self.btn_cancel.setEnabled(True)
        # announce before starting, so the final message is never overwritten
        self.statusBar().showMessage("Generation running…")
        self._threadpool.start(task)

    def _cancel_run(self) -> None:
        task = getattr(self, "_task", None)
        if task is not None:
            task.cancel()
            self._log("⏹️ Cancelling: no new requests; a running one ends on timeout.")
            self.btn_cancel.setEnabled(False)

    def _on_error(self, message: str) -> None:
        QApplication.restoreOverrideCursor()
        self.btn_cancel.setEnabled(False)
        self.statusBar().showMessage(f"❌ {message}")
        self.findings.setHtml(f"<p><b>Error:</b> {message}</p>")
        QMessageBox.critical(self, "Error", message)

    # ------------------------- result views -------------------------------
    def _show_requirements(self, requirements) -> None:
        """Captured requirements with their source positions."""
        self.requirements.clear()
        for req in requirements:
            top = QTreeWidgetItem(
                [req.external_id or req.display_id(), req.clarification_status.value]
            )
            top.addChild(QTreeWidgetItem([req.text.strip()[:400], ""]))
            for span in req.spans:
                child = QTreeWidgetItem([f"source: {span.label()}", ""])
                child.setToolTip(0, span.quote[:800])
                top.addChild(child)
            for note in req.notes:
                top.addChild(QTreeWidgetItem([f"note: {note}", ""]))
            top.setToolTip(0, f"internal ID: {req.requirement_id}")
            self.requirements.addTopLevelItem(top)
        self.tabs.setTabText(1, f"🔗 Requirements ({len(requirements)})")

    def _show_drafts(self, run) -> None:
        self.drafts.clear()
        for case in run.test_cases:
            top = QTreeWidgetItem(
                [case.title, f"{case.review_status.value} rev{case.revision}"]
            )
            top.addChild(
                QTreeWidgetItem([f"objective: {case.objective}", ""])
            )
            top.addChild(
                QTreeWidgetItem(
                    [f"requirements: {', '.join(case.requirement_ids)}", ""]
                )
            )
            for span in case.source_spans:
                item = QTreeWidgetItem([f"source: {span.label()}", ""])
                item.setToolTip(0, span.quote[:800])
                top.addChild(item)
            for label, steps in (
                ("preconditions", case.preconditions),
                ("steps", case.steps),
                ("postconditions", case.postconditions),
            ):
                if not steps:
                    continue
                group = QTreeWidgetItem([label, f"{len(steps)}"])
                for step in steps:
                    node = QTreeWidgetItem([f"{step.order}. {step.action}", ""])
                    for expectation in step.expectations:
                        node.addChild(QTreeWidgetItem([f"expect: {expectation}", ""]))
                    for measurement in step.measurements:
                        node.addChild(
                            QTreeWidgetItem(
                                [
                                    f"measure: {measurement.quantity} = "
                                    f"{measurement.value} {measurement.unit or ''}".strip(),
                                    measurement.evidence or "no evidence",
                                ]
                            )
                        )
                    group.addChild(node)
                top.addChild(group)
            if case.open_questions:
                group = QTreeWidgetItem(
                    ["open questions", str(len(case.open_questions))]
                )
                for question in case.open_questions:
                    node = QTreeWidgetItem([question.question, question.origin.value])
                    if question.suggestion:
                        node.addChild(
                            QTreeWidgetItem(
                                [f"proposal (not a specification): {question.suggestion}", ""]
                            )
                        )
                    group.addChild(node)
                top.addChild(group)
            self.drafts.addTopLevelItem(top)
        self.drafts.expandToDepth(0)
        self.tabs.setTabText(0, f"🧪 Drafts ({len(run.test_cases)})")

    def _show_findings(self, run) -> None:
        parts = ["<h3>Run report</h3><pre>"]
        parts.extend(line.replace("<", "&lt;") for line in run.report_lines())
        parts.append("</pre>")

        if run.open_questions:
            parts.append("<h3>Open questions</h3><ul>")
            for question in run.open_questions:
                parts.append(
                    f"<li><b>{question.origin.value}</b>: "
                    f"{question.question}</li>".replace("<li><b", "<li><b")
                )
            parts.append("</ul>")

        blocked = [u for u in run.units if u.status.value in ("rejected", "not_started")]
        if blocked:
            parts.append("<h3>Not usable</h3><ul>")
            for unit in blocked:
                refs = ", ".join(span.label() for span in unit.source_spans) or "no span"
                parts.append(
                    f"<li><b>{unit.status.value}</b> {unit.unit_id} "
                    f"[{', '.join(unit.requirement_ids)}] ({refs})<ul>"
                )
                for issue in unit.issues:
                    parts.append(f"<li>{str(issue)}</li>")
                parts.append("</ul></li>")
            parts.append("</ul>")

        if len(parts) == 3:
            parts.append("<p><em>No findings.</em></p>")
        self.findings.setHtml("".join(parts))
        self.tabs.setTabText(2, f"🔍 Findings ({len(run.open_questions) + len(blocked)})")

    def _on_run_finished(self, run) -> None:
        QApplication.restoreOverrideCursor()
        self.btn_cancel.setEnabled(False)
        self._run = run

        elapsed = None
        if self._start_time is not None:
            elapsed = time.perf_counter() - self._start_time
            self._start_time = None

        self._show_drafts(run)
        self._show_findings(run)
        # the flat rows keep the existing exporters and the insight panel working
        self._cases = legacy_rows(run.test_cases)
        self.table.model().update_cases(self._cases)
        self.btn_save_csv.setEnabled(bool(self._cases))
        self.btn_save_xlsx.setEnabled(bool(self._cases))
        self._render_summary()

        icon = {
            "completed": "✅",
            "partial": "⚠️",
            "failed": "❌",
            "cancelled": "⏹️",
        }.get(run.status.value, "ℹ️")
        msg = (
            f"{icon} Run {run.status.value}: {len(run.test_cases)} draft(s), "
            f"{len(run.open_questions)} open question(s)"
        )
        if run.rejected_units:
            msg += f", {len(run.rejected_units)} rejected"
        if run.not_started_units:
            msg += f", {len(run.not_started_units)} not started"
        if elapsed is not None:
            msg += f". ⏱️ {elapsed:.1f} s"
        self._log(msg)
        self.statusBar().showMessage(msg)

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