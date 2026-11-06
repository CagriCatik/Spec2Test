"""Main window.

Design rules for this file:

* No decorative icons in labels or messages. Text says what it means.
* The window shows only controls that the current pipeline actually uses.
* ``config.yml`` is read at startup and never written behind the user's back.
  Runtime changes live in the window; writing them back is an explicit action
  in the File menu.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Final

import yaml
from PySide6.QtCore import QSignalBlocker, QThreadPool, Qt, QTimer
from PySide6.QtGui import QAction, QCursor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QPlainTextEdit,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableView,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..adapters.export import (
    save_csv,
    save_json,
    save_markdown,
    save_specification_xlsx,
    save_xlsx,
    summarise_rows,
)
from ..application.generate import estimate_runtime
from ..application.specification import build_specification
from ..adapters.persistence.project_store import (
    PROJECT_SUFFIX,
    ProjectFileError,
    ProjectLocked,
    ProjectStore,
)
from ..application.review import ReviewError, add_run, project_from_run
from ..adapters.ollama import API_KEY_ENV, capabilities_for, check_model_choice
from ..application.legacy import legacy_rows
from ..application.indexing import collect_documents
from ..bootstrap import default_config_path, generation_settings_from_config
from ..domain.models import ReviewStatus
from ..config import (
    DEFAULT_JOB_TIMEOUT_S,
    MODE_CLOUD,
    MODE_LOCAL,
    AppConfig,
    CloudConfig,
)
from .help_text import PARAMETER_DOC
from .models import TestCaseTableModel
from .review_panel import STATUS_TEXT, ReviewPanel
from .scope_panel import ScopePanel, SpecificationView
from .workers import DraftTask, IndexTask, ModelListTask

ASSETS: Final[Path] = Path(__file__).resolve().parents[3] / "assets"

STYLESHEET: Final[str] = """
QGroupBox {
    font-weight: 600;
    border: 1px solid palette(mid);
    border-radius: 4px;
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    padding: 0 4px;
}
QLabel[role="hint"] {
    color: palette(mid);
}
QTextEdit#log {
    font-family: "SF Mono", "Menlo", "Consolas", monospace;
    font-size: 11px;
}
QPushButton[role="primary"] {
    font-weight: 600;
    padding: 6px 18px;
}
QTreeWidget::item { padding: 2px 0; }
QProgressBar {
    border: none;
    background: palette(mid);
    border-radius: 3px;
}
QProgressBar::chunk {
    background: palette(highlight);
    border-radius: 3px;
}
"""

STATUS_LABELS: Final[dict[str, str]] = {
    "completed": "Completed",
    "partial": "Partial",
    "failed": "Failed",
    "cancelled": "Cancelled",
}


def merge_config(existing: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Overlay *updates* on *existing* without dropping unknown keys."""
    merged = dict(existing)
    for key, value in updates.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_config(current, value)
        else:
            merged[key] = value
    return merged


class MainWindow(QMainWindow):
    """Import requirements, generate reviewable test drafts, inspect them."""

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Spec2Test")
        self.resize(1280, 860)
        icon = ASSETS / "icon.png"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.setStyleSheet(STYLESHEET)

        self._closing = False
        self._threadpool = QThreadPool.globalInstance()
        self._config_path = config_path or default_config_path()
        self._spec_paths: list[Path] = []
        self._guidance_path: Path | None = None
        self._cases: list[dict] = []
        self._run = None
        self._project = None
        self._project_store: ProjectStore | None = None
        self._project_dirty = False
        self._last_requirements: list = []
        self._last_documents: list = []
        self._catalogue: list = []
        self._index_path = Path.cwd() / ".spec2test" / "knowledge.db"
        self._index_task = None
        self._specification = None
        self._task: DraftTask | None = None
        self._start_time: float | None = None
        self._model_list_pending = False
        self._model_task: ModelListTask | None = None
        #: the file as loaded; the window only overlays the fields it owns
        self._loaded_config: dict[str, Any] = {}
        #: where the job budget came from, so it is written back in place
        self._job_timeout_key = "cloud"

        self._build_menubar()
        self._build_ui()
        self._load_config()

        # The model list is fetched once the event loop runs, so building the
        # window never waits for a network round trip. The window is passed as
        # the timer's context: if it is closed first, the call is dropped
        # instead of firing against a dead window.
        self._on_mode_changed(self.mode.currentText(), refresh=False)
        QTimer.singleShot(0, self, self.refresh_models)

    # =================================================================
    # construction
    # =================================================================
    def _build_menubar(self) -> None:
        bar = QMenuBar(self)

        file_menu = bar.addMenu("&File")
        for title, slot, shortcut in (
            ("Open specification…", self.select_specifications, "Ctrl+O"),
            ("Open guidance file…", self.select_guidance, None),
        ):
            action = QAction(title, self)
            action.triggered.connect(slot)
            if shortcut:
                action.setShortcut(shortcut)
            file_menu.addAction(action)
        file_menu.addSeparator()
        for title, slot, shortcut in (
            ("Open project…", self.open_project, "Ctrl+Shift+O"),
            ("Save project", self.save_project, "Ctrl+S"),
            ("Save project as…", self.save_project_as, "Ctrl+Shift+S"),
        ):
            action = QAction(title, self)
            action.triggered.connect(slot)
            if shortcut:
                action.setShortcut(shortcut)
            file_menu.addAction(action)
        file_menu.addSeparator()
        self.act_save_config = QAction("Write settings to config.yml…", self)
        self.act_save_config.triggered.connect(self.write_config)
        file_menu.addAction(self.act_save_config)
        file_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = bar.addMenu("&Help")
        params = QAction("Parameter reference", self)
        params.triggered.connect(self._show_parameter_help)
        help_menu.addAction(params)
        self.setMenuBar(bar)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, 1)
        splitter.addWidget(self._build_settings_panel())
        splitter.addWidget(self._build_results_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 900])

        outer.addLayout(self._build_action_bar())
        self.statusBar().showMessage("Ready")

    # ------------------------------------------------------ settings ---
    def _build_settings_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(340)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(4)

        self.scope = ScopePanel()
        self.scope.btn_folder.clicked.connect(self.select_knowledge_folder)
        self.scope.btn_files.clicked.connect(self.select_knowledge_files)
        self.scope.index_requested.connect(self.build_index)
        layout.addWidget(self.scope)
        layout.addWidget(self._group_task())
        layout.addWidget(self._group_endpoint())
        layout.addWidget(self._group_model())
        layout.addWidget(self._group_generation())
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return scroll

    def _group_task(self) -> QGroupBox:
        """Free task description and the project guidance, both editable."""
        group = QGroupBox("Task and guidance")
        layout = QVBoxLayout(group)

        layout.addWidget(QLabel("Task for this run"))
        self.task_description = QPlainTextEdit()
        self.task_description.setPlaceholderText(
            "for example: Write a test specification for cell balancing with "
            "nominal cases, boundaries and the described fault reactions."
        )
        self.task_description.setMaximumHeight(80)
        layout.addWidget(self.task_description)

        layout.addWidget(QLabel("Project guidance (added to the rules)"))
        self.guidance = QPlainTextEdit()
        self.guidance.setPlaceholderText(
            "role, coverage wishes, terminology, test level, language. The "
            "output format and the validation rules cannot be changed here."
        )
        self.guidance.setMaximumHeight(120)
        layout.addWidget(self.guidance)

        row = QHBoxLayout()
        self.btn_guidance_load = QPushButton("Load…")
        self.btn_guidance_save = QPushButton("Save…")
        self.btn_guidance_load.clicked.connect(self.load_guidance)
        self.btn_guidance_save.clicked.connect(self.save_guidance)
        row.addStretch(1)
        row.addWidget(self.btn_guidance_load)
        row.addWidget(self.btn_guidance_save)
        layout.addLayout(row)
        return group

    def _group_documents(self) -> QGroupBox:
        group = QGroupBox("Input")
        form = QFormLayout(group)

        self.lbl_specs = QLabel("No document selected")
        self.lbl_specs.setWordWrap(True)
        btn_specs = QPushButton("Choose…")
        btn_specs.clicked.connect(self.select_specifications)
        form.addRow(self._with_button(self.lbl_specs, btn_specs))
        hint = QLabel("PDF, TXT or Markdown. Several files are allowed.")
        hint.setProperty("role", "hint")
        form.addRow(hint)

        self.lbl_guidance = QLabel("No guidance file")
        self.lbl_guidance.setWordWrap(True)
        btn_guidance = QPushButton("Choose…")
        btn_guidance.clicked.connect(self.select_guidance)
        form.addRow(self._with_button(self.lbl_guidance, btn_guidance))
        guidance_hint = QLabel(
            "Optional project guidance. It is added to the generation rules "
            "and cannot override them."
        )
        guidance_hint.setWordWrap(True)
        guidance_hint.setProperty("role", "hint")
        form.addRow(guidance_hint)
        return group

    def _group_endpoint(self) -> QGroupBox:
        group = QGroupBox("Endpoint")
        form = QFormLayout(group)

        self.mode = QComboBox()
        self.mode.addItems([MODE_LOCAL, MODE_CLOUD])
        self.mode.currentTextChanged.connect(self._on_mode_changed)
        self.host = QLineEdit()
        self.host.setPlaceholderText("default host of the selected mode")
        self.connect_timeout = QDoubleSpinBox(minimum=1.0, maximum=120.0, singleStep=1.0)
        self.read_timeout = QDoubleSpinBox(minimum=5.0, maximum=3600.0, singleStep=10.0)

        form.addRow("Mode", self.mode)
        form.addRow("Host", self.host)
        form.addRow("Connect timeout (s)", self.connect_timeout)
        form.addRow("Read timeout (s)", self.read_timeout)

        self.lbl_auth = QLabel()
        self.lbl_auth.setWordWrap(True)
        self.lbl_auth.setProperty("role", "hint")
        form.addRow(self.lbl_auth)

        self.btn_check = QPushButton("Check connection and refresh models")
        self.btn_check.clicked.connect(self.refresh_models)
        form.addRow(self.btn_check)
        return group

    def _group_model(self) -> QGroupBox:
        group = QGroupBox("Model")
        form = QFormLayout(group)

        self.model_name = QComboBox(editable=True)
        self.model_name.setToolTip(
            "The name belongs to the mode: a local alias such as "
            "gpt-oss:120b-cloud for local operation, the /api/tags name such "
            "as gpt-oss:120b for the cloud API."
        )
        self.temperature = QDoubleSpinBox(minimum=0.0, maximum=2.0, singleStep=0.1)
        self.top_p = QDoubleSpinBox(minimum=0.0, maximum=1.0, singleStep=0.05)
        self.top_k = QSpinBox(minimum=1, maximum=1000)
        self.max_tokens = QSpinBox(minimum=256, maximum=65536, singleStep=256)

        form.addRow("Name", self.model_name)
        form.addRow("Temperature", self.temperature)
        form.addRow("Top-p", self.top_p)
        form.addRow("Top-k", self.top_k)
        form.addRow("Max tokens", self.max_tokens)

        self.gpu_box = QGroupBox("Hardware (local mode only)")
        gpu_form = QFormLayout(self.gpu_box)
        self.use_gpu = QCheckBox("Use GPU when available")
        self.num_gpu = QSpinBox(minimum=-1, maximum=8)
        self.num_gpu.setSpecialValueText("Automatic")
        self.main_gpu = QSpinBox(minimum=-1, maximum=8)
        self.main_gpu.setSpecialValueText("Automatic")
        self.gpu_layers = QSpinBox(minimum=-1, maximum=256)
        self.gpu_layers.setSpecialValueText("Automatic")
        self.num_thread = QSpinBox(minimum=0, maximum=256)
        self.num_thread.setSpecialValueText("Automatic")
        gpu_form.addRow(self.use_gpu)
        gpu_form.addRow("GPUs", self.num_gpu)
        gpu_form.addRow("Primary GPU", self.main_gpu)
        gpu_form.addRow("GPU layers", self.gpu_layers)
        gpu_form.addRow("Threads", self.num_thread)
        self.use_gpu.toggled.connect(self._update_gpu_controls)
        form.addRow(self.gpu_box)
        return group

    def _group_generation(self) -> QGroupBox:
        group = QGroupBox("Generation")
        form = QFormLayout(group)

        self.max_cases_per_unit = QSpinBox(minimum=1, maximum=50)
        self.max_repair_attempts = QSpinBox(minimum=0, maximum=5)
        self.group_size = QSpinBox(minimum=1, maximum=20)
        self.job_timeout = QSpinBox(minimum=30, maximum=7200, singleStep=30)

        for label, widget, tip in (
            ("Max drafts per requirement", self.max_cases_per_unit,
             "Resource ceiling, enforced by the application. Not a quota."),
            ("Repair attempts", self.max_repair_attempts,
             "After this many repairs an invalid answer is rejected visibly."),
            ("Requirements per request", self.group_size,
             "1 sends one request per requirement."),
            ("Job budget (s)", self.job_timeout,
             "Total runtime. Unprocessed requirements are reported."),
        ):
            widget.setToolTip(tip)
            form.addRow(label, widget)
        return group

    @staticmethod
    def _with_button(label: QLabel, button: QPushButton) -> QWidget:
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row.addWidget(label, 1)
        row.addWidget(button)
        return wrapper

    # ------------------------------------------------------- results ---
    def _build_results_panel(self) -> QWidget:
        self.tabs = QTabWidget()

        drafts_tab = QWidget()
        drafts_layout = QVBoxLayout(drafts_tab)
        drafts_layout.setContentsMargins(0, 0, 0, 0)
        drafts_split = QSplitter(Qt.Vertical)
        self.drafts = QTreeWidget()
        self.drafts.setHeaderLabels(["Test draft", "Status"])
        self.drafts.setColumnWidth(0, 560)
        self.drafts.setAlternatingRowColors(True)
        self.drafts.currentItemChanged.connect(self._on_draft_selected)
        drafts_split.addWidget(self.drafts)
        self.review = ReviewPanel()
        self.review.logged.connect(self.log_line)
        self.review.changed.connect(self._on_review_changed)
        drafts_split.addWidget(self.review)
        drafts_split.setStretchFactor(0, 3)
        drafts_split.setStretchFactor(1, 2)
        drafts_layout.addWidget(drafts_split)
        self.tabs.addTab(drafts_tab, "Test drafts")

        self.requirements = QTreeWidget()
        self.requirements.setHeaderLabels(["Requirement and source", "Clarification"])
        self.requirements.setColumnWidth(0, 560)
        self.requirements.setAlternatingRowColors(True)
        self.tabs.addTab(self.requirements, "Requirements")

        self.findings = QTextBrowser()
        self.findings.setObjectName("findings")
        self.findings.setHtml("<p>Generate drafts to see the findings.</p>")
        self.tabs.addTab(self.findings, "Findings")

        table_tab = QWidget()
        table_layout = QVBoxLayout(table_tab)
        table_layout.setContentsMargins(0, 0, 0, 0)
        self.summary = QTextBrowser()
        self.summary.setMaximumHeight(150)
        self.summary.setHtml("<p>Generate drafts to see the summary.</p>")
        table_layout.addWidget(self.summary)
        self.table = QTableView()
        self.table.setModel(TestCaseTableModel())
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        table_layout.addWidget(self.table, 1)
        self.tabs.addTab(table_tab, "Export table")

        self.specification = SpecificationView()
        self.tabs.addTab(self.specification, "Test specification")

        # what was actually sent as supporting context, with its origin
        self.context = QTreeWidget()
        self.context.setHeaderLabels(["Supporting context", "Kind and reason"])
        self.context.setColumnWidth(0, 560)
        self.context.setAlternatingRowColors(True)
        self.tabs.addTab(self.context, "Context")

        self.log = QTextEdit(readOnly=True)
        self.log.setObjectName("log")
        self.tabs.addTab(self.log, "Log")
        return self.tabs

    def _build_action_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(220)
        self.progress.setFixedHeight(6)
        self.progress.setValue(0)

        self.btn_run = QPushButton("Generate drafts")
        self.btn_run.setProperty("role", "primary")
        self.btn_run.clicked.connect(self.start_run)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_run)
        self.export_what = QComboBox()
        self.export_what.addItems(["All drafts", "Approved only"])
        self.export_what.setToolTip(
            "A draft export is marked as such. Approved only contains the test "
            "cases a reviewer has released."
        )
        self.btn_export_md = QPushButton("Specification (Markdown)…")
        self.btn_export_md.clicked.connect(self.export_specification_markdown)
        self.btn_export_json = QPushButton("Specification (JSON)…")
        self.btn_export_json.clicked.connect(self.export_specification_json)
        self.btn_save_xlsx = QPushButton("Specification (Excel)…")
        self.btn_save_xlsx.clicked.connect(self.export_specification_xlsx)
        self.btn_save_csv = QPushButton("Flat CSV…")
        self.btn_save_csv.clicked.connect(self.export_csv)
        for button in (
            self.btn_save_csv,
            self.btn_save_xlsx,
            self.btn_export_md,
            self.btn_export_json,
        ):
            button.setEnabled(False)

        bar.addWidget(self.progress)
        bar.addStretch(1)
        bar.addWidget(QLabel("Export:"))
        bar.addWidget(self.export_what)
        bar.addWidget(self.btn_export_md)
        bar.addWidget(self.btn_export_json)
        bar.addWidget(self.btn_save_xlsx)
        bar.addWidget(self.btn_save_csv)
        bar.addSpacing(16)
        bar.addWidget(self.btn_cancel)
        bar.addWidget(self.btn_run)
        return bar

    # =================================================================
    # configuration
    # =================================================================
    def _load_config(self) -> None:
        """Read config.yml once. The file is never written automatically."""
        data: dict[str, Any] = {}
        if self._config_path and Path(self._config_path).exists():
            try:
                data = yaml.safe_load(Path(self._config_path).read_text("utf-8")) or {}
            except Exception as exc:
                self.log_line(f"Could not read {self._config_path}: {exc}")
                data = {}
            else:
                self.log_line(f"Settings loaded from {self._config_path}")
        else:
            self.log_line("No config.yml found; using defaults")

        # Everything the window has no control over is kept verbatim, so a
        # setting without a widget survives the trip to the worker.
        self._loaded_config = data if isinstance(data, dict) else {}
        raw_generation = self._loaded_config.get("generation") or {}
        raw_cloud = self._loaded_config.get("cloud") or {}
        self._job_timeout_key = (
            "generation" if "job_timeout_s" in raw_generation else "cloud"
        )
        budget = raw_generation.get(
            "job_timeout_s", raw_cloud.get("job_timeout_s", DEFAULT_JOB_TIMEOUT_S)
        )
        self.apply_config(AppConfig.from_dict(self._loaded_config))
        try:
            self.job_timeout.setValue(int(float(budget)))
        except (TypeError, ValueError):
            pass

    def apply_config(self, cfg: AppConfig) -> None:
        # Applying a configuration must not trigger a model-list request:
        # the caller decides when the endpoint is contacted.
        blocker = QSignalBlocker(self.mode)
        self.mode.setCurrentText(cfg.cloud.mode)
        del blocker
        self.host.setText(cfg.cloud.host or "")
        self.connect_timeout.setValue(cfg.cloud.connect_timeout_s)
        self.read_timeout.setValue(cfg.cloud.read_timeout_s)
        if cfg.cloud.job_timeout_s:
            self.job_timeout.setValue(int(cfg.cloud.job_timeout_s))

        self.model_name.setCurrentText(cfg.model.name)
        self.temperature.setValue(cfg.model.temperature)
        self.top_p.setValue(cfg.model.top_p)
        self.top_k.setValue(cfg.model.top_k)
        self.max_tokens.setValue(cfg.model.max_tokens)
        self.use_gpu.setChecked(cfg.model.use_gpu)
        self.num_gpu.setValue(-1 if cfg.model.num_gpu is None else cfg.model.num_gpu)
        self.main_gpu.setValue(-1 if cfg.model.main_gpu is None else cfg.model.main_gpu)
        self.gpu_layers.setValue(
            -1 if cfg.model.gpu_layers is None else cfg.model.gpu_layers
        )
        self.num_thread.setValue(cfg.model.num_thread or 0)

        self.max_cases_per_unit.setValue(cfg.generation.max_cases_per_unit)
        self.max_repair_attempts.setValue(cfg.generation.max_repair_attempts)
        self.group_size.setValue(cfg.generation.group_size)

        if cfg.paths.pdf_path:
            self._spec_paths = [Path(cfg.paths.pdf_path)]
            self.lbl_specs.setText(Path(cfg.paths.pdf_path).name)
        if cfg.paths.prompt_path:
            self._guidance_path = Path(cfg.paths.prompt_path)
            self.lbl_guidance.setText(Path(cfg.paths.prompt_path).name)
        self._update_gpu_controls(self.use_gpu.isChecked())

    def cloud_config(self, *, job_timeout_s: float | None = None) -> CloudConfig:
        cloud = CloudConfig(
            mode=self.mode.currentText(),
            host=self.host.text().strip() or None,
            connect_timeout_s=self.connect_timeout.value(),
            read_timeout_s=self.read_timeout.value(),
        )
        if job_timeout_s is not None:
            cloud.job_timeout_s = job_timeout_s
        return cloud

    def widget_overlay(self) -> dict[str, Any]:
        """Only the fields the window actually edits.

        Everything else - model.url, cloud.max_retries, the input budget, the
        legacy sections - stays as it was loaded.
        """
        overlay: dict[str, Any] = {
            "cloud": {
                "mode": self.mode.currentText(),
                "host": self.host.text().strip() or None,
                "connect_timeout_s": self.connect_timeout.value(),
                "read_timeout_s": self.read_timeout.value(),
            },
            "model": {
                "name": self.model_name.currentText().strip(),
                "temperature": self.temperature.value(),
                "top_p": self.top_p.value(),
                "top_k": self.top_k.value(),
                "max_tokens": self.max_tokens.value(),
                "use_gpu": self.use_gpu.isChecked(),
                "num_gpu": None if self.num_gpu.value() == -1 else self.num_gpu.value(),
                "main_gpu": (
                    None if self.main_gpu.value() == -1 else self.main_gpu.value()
                ),
                "gpu_layers": (
                    None if self.gpu_layers.value() == -1 else self.gpu_layers.value()
                ),
                "num_thread": (
                    None if self.num_thread.value() == 0 else self.num_thread.value()
                ),
            },
            "generation": {
                "max_cases_per_unit": self.max_cases_per_unit.value(),
                "max_repair_attempts": self.max_repair_attempts.value(),
                "group_size": self.group_size.value(),
            },
        }
        # write the job budget back where it came from
        overlay.setdefault(self._job_timeout_key, {})["job_timeout_s"] = float(
            self.job_timeout.value()
        )
        paths: dict[str, Any] = {}
        if self._spec_paths:
            paths["pdf_path"] = str(self._spec_paths[0])
        if self._guidance_path:
            paths["prompt_path"] = str(self._guidance_path)
        if paths:
            overlay["paths"] = paths
        return overlay

    def collect_config(self) -> dict[str, Any]:
        """The loaded configuration with the edited fields applied on top."""
        return merge_config(self._loaded_config, self.widget_overlay())

    def write_config(self) -> None:
        """Write the current settings to config.yml - an explicit action.

        Unknown sections and keys are preserved. Comments are not: the YAML
        writer cannot reproduce them, which is why this never happens
        automatically.
        """
        target = Path(self._config_path) if self._config_path else Path("config.yml")
        answer = QMessageBox.question(
            self,
            "Write settings",
            f"Write the current settings to\n{target}?\n\n"
            "Values and unknown sections are preserved; comments in the file "
            "are lost.",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            existing: dict[str, Any] = {}
            if target.exists():
                existing = yaml.safe_load(target.read_text("utf-8")) or {}
            merged = merge_config(existing, self.collect_config())
            target.write_text(
                yaml.safe_dump(merged, sort_keys=True, allow_unicode=True), "utf-8"
            )
        except Exception as exc:
            self.log_line(f"Could not write {target}: {exc}")
            QMessageBox.critical(self, "Write failed", str(exc))
            return
        self.log_line(f"Settings written to {target}")
        self.statusBar().showMessage(f"Settings written to {target}")

    # =================================================================
    # endpoint and model list
    # =================================================================
    def _on_mode_changed(self, mode: str, *, refresh: bool = True) -> None:
        is_local = mode == MODE_LOCAL
        self.gpu_box.setEnabled(is_local)
        self._update_gpu_controls(self.use_gpu.isChecked())
        if is_local:
            self.lbl_auth.setText(
                "No authentication. If the local Ollama daemon is signed in to "
                "ollama.com, cloud models run through it."
            )
        else:
            self.lbl_auth.setText(
                f"The API key is read from ${API_KEY_ENV}. It is never stored "
                "in config.yml and never logged."
            )
        if refresh:
            self.refresh_models()

    def _update_gpu_controls(self, checked: bool) -> None:
        is_local = self.mode.currentText() == MODE_LOCAL
        for widget in (self.num_gpu, self.main_gpu, self.gpu_layers, self.num_thread):
            widget.setEnabled(bool(checked) and is_local)

    def refresh_models(self) -> None:
        """Fetch /api/tags in the background; never block the GUI thread."""
        if getattr(self, "_closing", False):
            return
        cloud = self.cloud_config()
        self._model_list_pending = True
        self.btn_check.setEnabled(False)
        self.btn_check.setText("Loading models…")
        self.statusBar().showMessage(f"Loading model list from {cloud.resolved_host()}")

        task = ModelListTask({"cloud": cloud.to_dict()}, cloud.mode)
        task.signals.models.connect(self._on_models_loaded)
        task.signals.failed.connect(self._on_model_list_failed)
        self._model_task = task  # keep a reference while it runs
        self._threadpool.start(task)

    def _finish_model_list(self) -> None:
        self._model_list_pending = False
        self.btn_check.setEnabled(True)
        self.btn_check.setText("Check connection and refresh models")

    def _on_models_loaded(self, models) -> None:
        models = list(models)
        selected = self.model_name.currentText().strip()
        self.model_name.clear()
        self.model_name.addItems(models)
        if selected:
            self.model_name.setCurrentText(selected)
        mode = self.mode.currentText()
        self.log_line(f"{len(models)} model(s) reported by /api/tags")
        if selected:
            for warning in check_model_choice(selected, models, mode):
                self.log_line(f"Warning: {warning}")
            for note in capabilities_for(selected, mode).notes:
                self.log_line(f"Note: {note}")
        self.statusBar().showMessage(f"{len(models)} model(s) available")
        self._finish_model_list()

    def _on_model_list_failed(self, message: str) -> None:
        self.log_line(f"Model list unavailable: {message}")
        if not self.model_name.currentText().strip():
            self.model_name.setCurrentText("")
        self.statusBar().showMessage("Model list unavailable - enter a name manually")
        self._finish_model_list()

    # =================================================================
    # input selection
    # =================================================================
    def select_specifications(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(
            self,
            "Select requirement documents",
            filter="Specifications (*.pdf *.txt *.md *.markdown);;All files (*)",
        )
        if not names:
            return
        self._spec_paths = [Path(name) for name in names]
        self.lbl_specs.setText(
            self._spec_paths[0].name
            if len(self._spec_paths) == 1
            else f"{len(self._spec_paths)} documents"
        )
        for path in self._spec_paths:
            self.log_line(f"Selected {path}")

    def select_guidance(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "Select project guidance file", filter="Text (*.txt *.md);;All files (*)"
        )
        if not name:
            return
        self._guidance_path = Path(name)
        self.lbl_guidance.setText(self._guidance_path.name)
        self.log_line(f"Guidance file: {self._guidance_path}")

    # =================================================================
    # knowledge base
    # =================================================================
    def select_knowledge_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select knowledge base folder")
        if not folder:
            return
        self.scope.set_sources(Path(folder), [])
        self.log_line(f"Knowledge base: {folder}")
        self.build_index(rebuild=False)

    def select_knowledge_files(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(
            self,
            "Select knowledge base documents",
            filter="Specifications (*.pdf *.txt *.md *.markdown);;All files (*)",
        )
        if not names:
            return
        self.scope.set_sources(None, [Path(n) for n in names])
        self.log_line(f"Knowledge base: {len(names)} file(s)")
        self.build_index(rebuild=False)

    def knowledge_paths(self) -> list[Path]:
        if self.scope.folder is not None:
            return collect_documents(self.scope.folder)
        return self.scope.files

    def build_index(self, rebuild: bool = False) -> None:
        paths = self.knowledge_paths()
        if not paths:
            self.log_line("Select a knowledge base first.")
            return
        self.scope.set_busy(True)
        self.statusBar().showMessage(
            f"{'Rebuilding' if rebuild else 'Updating'} the index over "
            f"{len(paths)} document(s)…"
        )
        task = IndexTask(paths, self._index_path, rebuild=rebuild)
        task.signals.finished.connect(self._on_index_ready)
        task.signals.failed.connect(self._on_index_failed)
        self._index_task = task
        self._threadpool.start(task)

    def _on_index_ready(self, result) -> None:
        self.scope.set_busy(False)
        self._catalogue = list(result.requirements)
        self._last_documents = list(result.documents.values())
        self.scope.set_requirements(self._catalogue)
        self.scope.show_index_state(
            result.summary(),
            len(result.documents),
            len(result.requirements),
            result.warnings,
        )
        for warning in result.warnings[:5]:
            self.log_line(f"Import warning: {warning}")
        message = (
            f"Index ready: {len(result.documents)} document(s), "
            f"{len(result.requirements)} requirement(s) ({result.summary()})"
        )
        self.log_line(message)
        self.statusBar().showMessage(message)

    def _on_index_failed(self, message: str) -> None:
        self.scope.set_busy(False)
        self.log_line(f"Index failed: {message}")
        self.statusBar().showMessage("Index failed")
        QMessageBox.critical(self, "Index failed", message)

    # =================================================================
    # guidance
    # =================================================================
    def load_guidance(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "Load project guidance", filter="Text (*.txt *.md);;All files (*)"
        )
        if not name:
            return
        try:
            self.guidance.setPlainText(Path(name).read_text(encoding="utf-8"))
        except OSError as exc:
            QMessageBox.critical(self, "Could not read the file", str(exc))
            return
        self._guidance_path = Path(name)
        self.lbl_guidance.setText(Path(name).name)
        self.log_line(f"Guidance loaded from {name}")

    def save_guidance(self) -> None:
        name, _ = QFileDialog.getSaveFileName(
            self, "Save project guidance", "guidance.md",
            filter="Text (*.txt *.md);;All files (*)",
        )
        if not name:
            return
        try:
            Path(name).write_text(self.guidance.toPlainText(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Could not write the file", str(exc))
            return
        self._guidance_path = Path(name)
        self.lbl_guidance.setText(Path(name).name)
        self.log_line(f"Guidance saved to {name}")

    # =================================================================
    # run
    # =================================================================
    def specifications(self) -> list[Path]:
        return list(self._spec_paths)

    def start_run(self) -> None:
        specs = self.specifications() or self.knowledge_paths()
        if not specs:
            QMessageBox.warning(
                self,
                "No document",
                "Select a knowledge base or a specification file first.",
            )
            return
        scope_ids = self.scope.selected_ids() or None
        if self._catalogue and scope_ids is None:
            QMessageBox.warning(
                self,
                "No scope",
                "Select the requirements that should get test cases. "
                "The knowledge base stays available as context.",
            )
            return
        if not self.model_name.currentText().strip():
            QMessageBox.warning(self, "No model", "Enter or select a model name first.")
            return

        settings = generation_settings_from_config(self.collect_config())
        unit_count = max(
            1, -(-len(scope_ids or self._catalogue or [1]) // settings.group_size)
        )
        expected, fits = estimate_runtime(unit_count, settings)
        if not fits:
            answer = QMessageBox.question(
                self,
                "Scope larger than the budget",
                f"{unit_count} request(s) need about {expected / 60:.0f} minutes, "
                f"the job budget is {(settings.job_timeout_s or 0) / 60:.0f} "
                "minutes.\n\nThe run would stop early and report the rest as "
                "not started.\n\nStart anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
        self.progress.setValue(0)
        self._start_time = time.perf_counter()
        self._run = None
        self.drafts.clear()
        self.requirements.clear()
        self.findings.setHtml("<p>Running…</p>")
        self.summary.setHtml("<p>Running…</p>")

        task = DraftTask(
            specs,
            self.collect_config(),
            scope_ids=scope_ids,
            task_description=self.task_description.toPlainText().strip() or None,
            guidance=self.guidance.toPlainText().strip() or None,
            index_path=self._index_path,
        )
        self._task = task
        task.signals.progress.connect(self.progress.setValue)
        task.signals.message.connect(self.log_line)
        task.signals.documents.connect(self._remember_documents)
        task.signals.requirements.connect(self.show_requirements)
        task.signals.finished.connect(self.show_run)
        task.signals.error.connect(self._on_error)
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.statusBar().showMessage("Generation running")
        self._threadpool.start(task)

    def cancel_run(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self.log_line(
                "Cancelling: no new requests. A running request ends within its "
                "read timeout."
            )
            self.btn_cancel.setEnabled(False)

    def _reset_buttons(self) -> None:
        QApplication.restoreOverrideCursor()
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)

    def _on_error(self, message: str) -> None:
        self._reset_buttons()
        self.statusBar().showMessage(message)
        self.findings.setHtml(f"<h3>Error</h3><p>{message}</p>")
        QMessageBox.critical(self, "Error", message)

    # =================================================================
    # result views
    # =================================================================
    def log_line(self, message: str) -> None:
        if hasattr(self, "log"):
            self.log.append(message)
            self.log.ensureCursorVisible()
        print(message, flush=True)

    def _remember_documents(self, documents) -> None:
        self._last_documents = list(documents)

    def show_requirements(self, requirements) -> None:
        self._last_requirements = list(requirements)
        self.requirements.clear()
        for requirement in requirements:
            top = QTreeWidgetItem(
                [
                    requirement.external_id or requirement.display_id(),
                    requirement.clarification_status.value,
                ]
            )
            top.setToolTip(0, f"internal ID: {requirement.requirement_id}")
            top.addChild(QTreeWidgetItem([requirement.text.strip()[:400], ""]))
            for span in requirement.spans:
                child = QTreeWidgetItem([f"Source: {span.label()}", ""])
                child.setToolTip(0, span.quote[:800])
                top.addChild(child)
            for note in requirement.notes:
                top.addChild(QTreeWidgetItem([f"Note: {note}", ""]))
            self.requirements.addTopLevelItem(top)
        self.tabs.setTabText(1, f"Requirements ({len(requirements)})")

    def show_run(self, run) -> None:
        self._reset_buttons()
        self._run = run
        self._absorb_run(run)

        elapsed = None
        if self._start_time is not None:
            elapsed = time.perf_counter() - self._start_time
            self._start_time = None

        self._fill_drafts_from_project()
        self._fill_findings(run)
        self._fill_context(run)
        self._fill_specification()
        self._cases = legacy_rows(
            self._project.test_cases if self._project else run.test_cases
        )
        self.table.model().update_cases(self._cases)
        self._enable_exports(bool(self._cases))
        self._fill_summary()

        label = STATUS_LABELS.get(run.status.value, run.status.value)
        parts = [
            f"{label}: {len(run.test_cases)} draft(s)",
            f"{len(run.open_questions)} open question(s)",
        ]
        if run.rejected_units:
            parts.append(f"{len(run.rejected_units)} rejected")
        if run.not_started_units:
            parts.append(f"{len(run.not_started_units)} not started")
        if elapsed is not None:
            parts.append(f"{elapsed:.1f} s")
        message = ", ".join(parts)
        self.log_line(message)
        self.statusBar().showMessage(message)

    def _absorb_run(self, run) -> None:
        """Keep the result as a project so it can be reviewed and saved.

        The project holds the whole catalogue, not only the generated scope:
        a requirement without a draft has to stay visible, and references need
        their targets.
        """
        if self._project is None:
            self._project = project_from_run(
                run,
                self._last_requirements,
                self._last_documents,
                name=(self._spec_paths[0].stem if self._spec_paths else "project"),
            )
        else:
            try:
                add_run(
                    self._project,
                    run,
                    self._last_requirements,
                    self._last_documents,
                )
            except ReviewError as exc:
                self.log_line(f"Run not merged into the project: {exc}")
                QMessageBox.warning(self, "Run not merged", str(exc))
                return
        self._mark_dirty()
        self.review.show_case(self._project, None)

    def _on_draft_selected(self, current, _previous) -> None:
        while current is not None and current.parent() is not None:
            current = current.parent()
        case_id = current.data(0, Qt.UserRole) if current is not None else None
        self.review.show_case(self._project, case_id)

    def _on_review_changed(self, case_id: str) -> None:
        if self._project is None:
            return
        self._mark_dirty()
        self._fill_drafts_from_project()
        self._fill_summary()
        self._fill_specification()
        self._select_case(case_id)
        counts = self._project.count_by_status()
        self.statusBar().showMessage(
            ", ".join(
                f"{STATUS_TEXT.get(status, status.value)}: {counts[status.value]}"
                for status in ReviewStatus
                if counts[status.value]
            )
        )

    def _select_case(self, case_id: str) -> None:
        for index in range(self.drafts.topLevelItemCount()):
            item = self.drafts.topLevelItem(index)
            if item.data(0, Qt.UserRole) == case_id:
                self.drafts.setCurrentItem(item)
                return

    def _fill_drafts_from_project(self) -> None:
        if self._project is None:
            return
        self._render_drafts(self._project.test_cases)
        self._cases = legacy_rows(self._project.test_cases)
        self.table.model().update_cases(self._cases)

    def _render_drafts(self, cases) -> None:
        self.drafts.clear()
        for case in cases:
            status = STATUS_TEXT.get(case.review_status, case.review_status.value)
            if case.needs_recheck:
                status += ", review again"
            top = QTreeWidgetItem([case.title, f"{status}, revision {case.revision}"])
            top.setData(0, Qt.UserRole, case.case_id)
            top.addChild(QTreeWidgetItem([f"Objective: {case.objective}", ""]))
            top.addChild(
                QTreeWidgetItem(
                    [f"Requirements: {', '.join(case.requirement_ids)}", ""]
                )
            )
            for span in case.source_spans:
                item = QTreeWidgetItem([f"Source: {span.label()}", ""])
                item.setToolTip(0, span.quote[:800])
                top.addChild(item)
            for label, steps in (
                ("Preconditions", case.preconditions),
                ("Steps", case.steps),
                ("Postconditions", case.postconditions),
            ):
                if not steps:
                    continue
                group = QTreeWidgetItem([f"{label} ({len(steps)})", ""])
                for step in steps:
                    node = QTreeWidgetItem([f"{step.order}. {step.action}", ""])
                    for expectation in step.expectations:
                        node.addChild(QTreeWidgetItem([f"Expected: {expectation}", ""]))
                    for measurement in step.measurements:
                        value = (
                            f"{measurement.value} {measurement.unit or ''}".strip()
                            if measurement.value is not None
                            else "value open"
                        )
                        node.addChild(
                            QTreeWidgetItem(
                                [
                                    f"Measured: {measurement.quantity} = {value}",
                                    measurement.evidence or "no evidence",
                                ]
                            )
                        )
                    group.addChild(node)
                top.addChild(group)
            for finding in case.accepted_findings:
                top.addChild(
                    QTreeWidgetItem(
                        [f"Accepted: {finding.code} - {finding.justification}", ""]
                    )
                )
            if case.open_questions:
                group = QTreeWidgetItem(
                    [f"Open questions ({len(case.open_questions)})", ""]
                )
                for question in case.open_questions:
                    label = question.question
                    if question.resolved:
                        label = f"[answered] {label}"
                    node = QTreeWidgetItem([label, question.origin.value])
                    if question.resolved:
                        node.addChild(
                            QTreeWidgetItem(
                                [f"Answer: {question.answer}", question.resolved_by or ""]
                            )
                        )
                    if question.suggestion:
                        node.addChild(
                            QTreeWidgetItem(
                                [
                                    "Proposal, not a specification: "
                                    f"{question.suggestion}",
                                    "",
                                ]
                            )
                        )
                    group.addChild(node)
                top.addChild(group)
            self.drafts.addTopLevelItem(top)
        self.drafts.expandToDepth(0)
        self.tabs.setTabText(0, f"Test drafts ({len(cases)})")

    def _fill_specification(self) -> None:
        """The deliverable, rebuilt from the project as it stands."""
        if self._project is None:
            return
        self._specification = build_specification(self._project)
        self.specification.show_specification(self._specification)
        counts = self._specification.counts()
        self.tabs.setTabText(4, f"Test specification ({counts['test_cases']})")

    def _fill_context(self, run) -> None:
        """Everything that was sent alongside the requirement, and why."""
        self.context.clear()
        total = 0
        for unit in run.units:
            if not unit.context:
                continue
            top = QTreeWidgetItem(
                [
                    f"{unit.unit_id}: {', '.join(unit.requirement_ids)}",
                    f"{len(unit.context)} item(s)",
                ]
            )
            for item in unit.context:
                total += 1
                node = QTreeWidgetItem(
                    [
                        item.external_id or item.heading or item.requirement_id,
                        f"{item.kind.value}, {item.reason.value}",
                    ]
                )
                node.addChild(QTreeWidgetItem([f"Source: {item.label}", ""]))
                if item.matched_terms:
                    node.addChild(
                        QTreeWidgetItem(
                            [f"Matched: {', '.join(item.matched_terms)}", ""]
                        )
                    )
                for span in item.spans:
                    position = QTreeWidgetItem([f"Position: {span.label()}", ""])
                    position.setToolTip(0, span.quote[:800])
                    node.addChild(position)
                text = QTreeWidgetItem([item.text.strip()[:600], ""])
                text.setToolTip(0, item.text[:2000])
                node.addChild(text)
                top.addChild(node)
            self.context.addTopLevelItem(top)
        self.context.expandToDepth(0)
        self.tabs.setTabText(5, f"Context ({total})" if total else "Context")

    def _fill_findings(self, run) -> None:
        def escape(text: str) -> str:
            return str(text).replace("&", "&amp;").replace("<", "&lt;")

        blocked = [
            unit
            for unit in run.units
            if unit.status.value in ("rejected", "not_started")
        ]
        parts = [
            "<style>"
            "body { font-size: 12px; }"
            "h3 { margin: 14px 0 6px 0; }"
            "table.meta td { padding: 1px 14px 1px 0; vertical-align: top; }"
            "table.meta td:first-child { color: #666; }"
            "ul { margin: 4px 0 4px 18px; padding: 0; }"
            "li { margin-bottom: 3px; }"
            "code { font-family: 'SF Mono','Menlo','Consolas',monospace;"
            " font-size: 11px; color: #555; }"
            "</style>",
            "<h3>Run</h3><table class='meta'>",
        ]
        for name, value in (
            ("Status", STATUS_LABELS.get(run.status.value, run.status.value)),
            ("Model", f"{run.model} ({run.mode})"),
            ("Endpoint", run.endpoint),
            ("Prompt / schema", f"{run.prompt_version} / {run.schema_version}"),
            (
                "Units",
                f"{run.planned_unit_count or len(run.units)} planned, "
                f"{len(run.accepted_units)} accepted, "
                f"{len(run.rejected_units)} rejected, "
                f"{len(run.not_started_units)} not started",
            ),
            (
                "Result",
                f"{len(run.test_cases)} draft(s), "
                f"{len(run.open_questions)} open question(s)",
            ),
        ):
            parts.append(
                f"<tr><td>{escape(name)}</td><td>{escape(value)}</td></tr>"
            )
        parts.append("</table>")
        if run.errors:
            parts.append("<h3>Run errors</h3><ul>")
            parts.extend(f"<li>{escape(error)}</li>" for error in run.errors)
            parts.append("</ul>")

        if run.open_questions:
            parts.append("<h3>Open questions</h3><ul>")
            for question in run.open_questions:
                parts.append(
                    f"<li><code>{escape(question.origin.value)}</code> "
                    f"{escape(question.question)}</li>"
                )
            parts.append("</ul>")

        if blocked:
            parts.append("<h3>Not usable</h3><ul>")
            for unit in blocked:
                refs = (
                    ", ".join(span.label() for span in unit.source_spans) or "no span"
                )
                parts.append(
                    f"<li>{escape(unit.status.value)} {escape(unit.unit_id)} "
                    f"[{escape(', '.join(unit.requirement_ids))}] "
                    f"({escape(refs)})<ul>"
                )
                for issue in unit.issues:
                    parts.append(f"<li>{escape(issue)}</li>")
                parts.append("</ul></li>")
            parts.append("</ul>")

        if not run.open_questions and not blocked:
            parts.append("<p>No findings.</p>")
        self.findings.setHtml("".join(parts))
        self.tabs.setTabText(2, f"Findings ({len(run.open_questions) + len(blocked)})")

    def _fill_summary(self) -> None:
        if not self._cases:
            self.summary.setHtml("<p>No drafts to summarise.</p>")
            return
        stats = summarise_rows(self._cases)
        rows = [
            ("Drafts", stats["total_cases"]),
            ("Complete drafts", stats["complete_cases"]),
            ("Distinct requirements referenced", stats["unique_requirements"]),
            ("Average steps", stats["avg_actions"]),
            ("Average expectations", stats["avg_expected_results"]),
        ]
        html = ["<table cellpadding='3'>"]
        html.extend(f"<tr><td>{name}</td><td><b>{value}</b></td></tr>" for name, value in rows)
        html.append("</table>")
        if stats["duplicate_names"]:
            html.append(
                "<p>Duplicate titles: "
                + ", ".join(stats["duplicate_names"][:5])
                + "</p>"
            )
        html.append(
            "<p>These counts describe the drafts, not functional coverage.</p>"
        )
        self.summary.setHtml("".join(html))

    # =================================================================
    # export
    # =================================================================
    def _enable_exports(self, enabled: bool) -> None:
        for button in (
            self.btn_save_csv,
            self.btn_save_xlsx,
            self.btn_export_md,
            self.btn_export_json,
        ):
            button.setEnabled(enabled)

    def current_specification(self):
        """The specification for what the export selector asks for."""
        if self._project is None:
            return None
        approved_only = self.export_what.currentText() == "Approved only"
        return build_specification(self._project, approved_only=approved_only)

    def export_csv(self) -> None:
        self._export(save_csv, "Export CSV", "CSV files (*.csv)")

    def export_xlsx(self) -> None:
        self._export(save_xlsx, "Export Excel", "Excel workbook (*.xlsx)")

    def export_specification_markdown(self) -> None:
        self._export_specification(
            save_markdown, "Export specification", "Markdown (*.md)", ".md"
        )

    def export_specification_json(self) -> None:
        self._export_specification(
            save_json, "Export specification", "JSON (*.json)", ".json"
        )

    def export_specification_xlsx(self) -> None:
        self._export_specification(
            save_specification_xlsx,
            "Export specification",
            "Excel workbook (*.xlsx)",
            ".xlsx",
        )

    def _export_specification(self, writer, title, file_filter, suffix) -> None:
        spec = self.current_specification()
        if spec is None:
            self.log_line("There is nothing to export yet.")
            return
        if not spec.entries:
            QMessageBox.warning(
                self,
                "Nothing to export",
                "The selection contains no test case. With 'Approved only' "
                "a case has to be approved first.",
            )
            return
        suggested = f"{spec.document_id}{suffix}"
        path, _ = QFileDialog.getSaveFileName(self, title, suggested, filter=file_filter)
        if not path:
            return
        try:
            written = writer(spec, path)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        counts = spec.counts()
        note = "approved only" if spec.approved_only else "drafts included"
        self.log_line(
            f"Specification written to {written} "
            f"({counts['test_cases']} case(s), {note}, "
            f"{counts['open_questions']} open question(s), "
            f"{counts['uncovered_requirements']} requirement(s) without a draft)"
        )
        QMessageBox.information(self, "Export finished", f"Written to\n{written}")

    def _export(self, writer, title: str, file_filter: str) -> None:
        if not self._cases:
            return
        path, _ = QFileDialog.getSaveFileName(self, title, filter=file_filter)
        if not path:
            return
        try:
            written = writer(self._cases, path)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.log_line(f"Exported {len(self._cases)} draft(s) to {written}")
        QMessageBox.information(self, "Export finished", f"Written to\n{written}")

    # =================================================================
    # projects
    # =================================================================
    def open_project(self, path: Path | str | None = None) -> bool:
        """Load a saved project: sources, drafts, findings and decisions."""
        if path is None:
            name, _ = QFileDialog.getOpenFileName(
                self,
                "Open project",
                filter=f"Spec2Test project (*{PROJECT_SUFFIX});;All files (*)",
            )
            if not name:
                return False
            path = name

        if not self.confirm_discard_changes():
            return False

        store = ProjectStore(path)
        try:
            project = store.load()
        except ProjectFileError as exc:
            self.log_line(f"Project could not be opened: {exc}")
            QMessageBox.critical(self, "Project could not be opened", str(exc))
            return False
        try:
            store.acquire_lock()
        except ProjectLocked as exc:
            self.log_line(str(exc))
            QMessageBox.warning(self, "Project is open elsewhere", str(exc))
            return False

        self._release_project()
        self._project = project
        self._project_store = store
        self._project_dirty = False
        self._last_requirements = list(project.requirements)
        self._last_documents = list(project.documents)
        self._run = project.runs[-1] if project.runs else None

        self.show_requirements(project.requirements)
        self._fill_drafts_from_project()
        if self._run is not None:
            self._fill_findings(self._run)
            self._fill_context(self._run)
        self._fill_specification()
        if project.requirements:
            self._catalogue = list(project.requirements)
            self.scope.set_requirements(self._catalogue)
            self.scope.show_project_source(
                project.name, len(project.documents), len(project.requirements)
            )
        self._fill_summary()
        self._enable_exports(bool(self._cases))
        self.review.show_case(project, None)

        counts = project.count_by_status()
        summary = ", ".join(
            f"{STATUS_TEXT.get(status, status.value)}: {counts[status.value]}"
            for status in ReviewStatus
            if counts[status.value]
        )
        message = (
            f"Project '{project.name}' opened: {len(project.test_cases)} draft(s)"
            + (f" ({summary})" if summary else "")
        )
        self.log_line(message)
        self.statusBar().showMessage(message)
        self._write_project_title()
        return True

    def save_project(self) -> bool:
        if self._project is None:
            self.log_line("There is nothing to save yet; generate drafts first.")
            return False
        if self._project_store is None:
            return self.save_project_as()
        return self._write_project(self._project_store)

    def save_project_as(self) -> bool:
        if self._project is None:
            self.log_line("There is nothing to save yet; generate drafts first.")
            return False
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Save project",
            f"{self._project.name}{PROJECT_SUFFIX}",
            filter=f"Spec2Test project (*{PROJECT_SUFFIX});;All files (*)",
        )
        if not name:
            return False
        store = ProjectStore(name)
        try:
            store.acquire_lock()
        except ProjectLocked as exc:
            QMessageBox.warning(self, "Project is open elsewhere", str(exc))
            return False
        self._release_project()
        self._project_store = store
        return self._write_project(store)

    def _write_project(self, store: ProjectStore) -> bool:
        try:
            path = store.save(self._project)
        except OSError as exc:
            self.log_line(f"Project could not be saved: {exc}")
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self._project_dirty = False
        self.log_line(f"Project saved to {path}")
        self.statusBar().showMessage(f"Project saved to {path}")
        self._write_project_title()
        return True

    def _mark_dirty(self) -> None:
        self._project_dirty = True
        title = f"Spec2Test - {self._project.name}" if self._project else "Spec2Test"
        self.setWindowTitle(f"{title} *")

    def _write_project_title(self) -> None:
        name = self._project.name if self._project else None
        self.setWindowTitle(f"Spec2Test - {name}" if name else "Spec2Test")

    def confirm_discard_changes(self) -> bool:
        """Ask before losing unsaved review work. False means "stay"."""
        if self._project is None or not self._project_dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            "The project has unsaved changes.\n\nSave them before closing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Save:
            return self.save_project()
        self.log_line("Unsaved project changes discarded.")
        return True

    def _release_project(self) -> None:
        if self._project_store is not None:
            self._project_store.release_lock()
            self._project_store = None

    # =================================================================
    # shutdown
    # =================================================================
    def closeEvent(self, event):  # noqa: N802 - Qt naming
        """Ask about unsaved review work, then stop the running generation.

        config.yml is deliberately not written here: settings are only stored
        when the user chooses it in the File menu.
        """
        if not self.confirm_discard_changes():
            event.ignore()
            return
        self._closing = True
        self._release_project()
        if self._task is not None:
            self._task.cancel()
        self._threadpool.waitForDone(2000)
        super().closeEvent(event)

    # =================================================================
    # help
    # =================================================================
    def _show_parameter_help(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Parameter reference")
        browser = QTextBrowser()
        browser.setHtml(PARAMETER_DOC)
        QVBoxLayout(dialog).addWidget(browser)
        dialog.resize(680, 560)
        dialog.exec()


__all__ = ["MainWindow", "merge_config"]
