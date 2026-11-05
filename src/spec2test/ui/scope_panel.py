"""Knowledge base, index state and the test scope.

Two things are kept apart on purpose: the **knowledge base** is what may be
searched for context, the **scope** is what test cases are written for. A
document can supply context without becoming a test target.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

ALL_DOCUMENTS = "All documents"


class ScopePanel(QWidget):
    """Pick the knowledge base, build the index, choose what to test."""

    index_requested = Signal(bool)   # True = rebuild from scratch
    scope_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._requirements: list = []
        self._folder: Path | None = None
        self._files: list[Path] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        base = QGroupBox("Knowledge base")
        base_layout = QVBoxLayout(base)
        self.lbl_base = QLabel("No knowledge base selected")
        self.lbl_base.setWordWrap(True)
        base_layout.addWidget(self.lbl_base)
        buttons = QHBoxLayout()
        self.btn_folder = QPushButton("Choose folder…")
        self.btn_files = QPushButton("Choose files…")
        buttons.addWidget(self.btn_folder)
        buttons.addWidget(self.btn_files)
        base_layout.addLayout(buttons)

        self.lbl_index = QLabel("Index: not built yet")
        self.lbl_index.setWordWrap(True)
        self.lbl_index.setProperty("role", "hint")
        base_layout.addWidget(self.lbl_index)
        index_buttons = QHBoxLayout()
        self.btn_build = QPushButton("Build index")
        self.btn_update = QPushButton("Update index")
        self.btn_build.clicked.connect(lambda: self.index_requested.emit(True))
        self.btn_update.clicked.connect(lambda: self.index_requested.emit(False))
        index_buttons.addWidget(self.btn_build)
        index_buttons.addWidget(self.btn_update)
        base_layout.addLayout(index_buttons)
        layout.addWidget(base)

        scope = QGroupBox("Test scope")
        scope_layout = QVBoxLayout(scope)
        hint = QLabel(
            "Only the requirements selected here get test cases. Everything "
            "else in the knowledge base can still be used as context."
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        scope_layout.addWidget(hint)

        filter_row = QHBoxLayout()
        self.document_filter = QComboBox()
        self.document_filter.addItem(ALL_DOCUMENTS)
        self.document_filter.currentTextChanged.connect(self._apply_filter)
        self.search = QLineEdit()
        self.search.setPlaceholderText("filter by ID or text")
        self.search.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.document_filter, 1)
        filter_row.addWidget(self.search, 1)
        scope_layout.addLayout(filter_row)

        self.requirements = QListWidget()
        self.requirements.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.requirements.itemSelectionChanged.connect(self.scope_changed)
        scope_layout.addWidget(self.requirements)

        select_row = QHBoxLayout()
        self.btn_all = QPushButton("Select all shown")
        self.btn_none = QPushButton("Select none")
        self.btn_all.clicked.connect(self.select_all_shown)
        self.btn_none.clicked.connect(self.requirements.clearSelection)
        select_row.addWidget(self.btn_all)
        select_row.addWidget(self.btn_none)
        scope_layout.addLayout(select_row)

        self.lbl_scope = QLabel("No requirement selected")
        self.lbl_scope.setWordWrap(True)
        scope_layout.addWidget(self.lbl_scope)
        layout.addWidget(scope, 1)

        self.scope_changed.connect(self._update_scope_label)

    # ------------------------------------------------------- knowledge --
    def set_sources(self, folder: Path | None, files: list[Path]) -> None:
        self._folder = folder
        self._files = list(files)
        if folder:
            self.lbl_base.setText(f"Folder: {folder}")
        elif files:
            self.lbl_base.setText(
                files[0].name if len(files) == 1 else f"{len(files)} files"
            )
        else:
            self.lbl_base.setText("No knowledge base selected")

    @property
    def folder(self) -> Path | None:
        return self._folder

    @property
    def files(self) -> list[Path]:
        return list(self._files)

    def show_index_state(
        self, summary: str, documents: int, requirements: int, warnings: list[str]
    ) -> None:
        text = (
            f"Index: {documents} document(s), {requirements} requirement(s). "
            f"Last run: {summary}"
        )
        if warnings:
            text += f"  |  {len(warnings)} import warning(s): " + "; ".join(
                warnings[:2]
            )
        self.lbl_index.setText(text)

    def set_busy(self, busy: bool) -> None:
        for button in (self.btn_build, self.btn_update, self.btn_folder, self.btn_files):
            button.setEnabled(not busy)
        if busy:
            self.lbl_index.setText("Index: working…")

    # ----------------------------------------------------------- scope --
    def set_requirements(self, requirements) -> None:
        self._requirements = list(requirements)
        documents = sorted(
            {
                self._document_of(requirement)
                for requirement in self._requirements
            }
        )
        current = self.document_filter.currentText()
        self.document_filter.blockSignals(True)
        self.document_filter.clear()
        self.document_filter.addItem(ALL_DOCUMENTS)
        self.document_filter.addItems(documents)
        if current in documents:
            self.document_filter.setCurrentText(current)
        self.document_filter.blockSignals(False)
        self._apply_filter()

    @staticmethod
    def _document_of(requirement) -> str:
        return requirement.spans[0].document_id if requirement.spans else "unknown"

    def _apply_filter(self) -> None:
        wanted_document = self.document_filter.currentText()
        needle = self.search.text().strip().lower()
        selected = self.selected_ids()
        self.requirements.clear()
        for requirement in self._requirements:
            document = self._document_of(requirement)
            if wanted_document != ALL_DOCUMENTS and document != wanted_document:
                continue
            label = requirement.external_id or requirement.display_id()
            haystack = f"{label} {requirement.text}".lower()
            if needle and needle not in haystack:
                continue
            summary = " ".join(requirement.text.split())[:110]
            item = QListWidgetItem(f"{label} - {summary}")
            item.setData(Qt.UserRole, requirement.requirement_id)
            item.setToolTip(requirement.text[:600])
            self.requirements.addItem(item)
            if requirement.requirement_id in selected:
                item.setSelected(True)
        self._update_scope_label()

    def select_all_shown(self) -> None:
        self.requirements.selectAll()

    def selected_ids(self) -> set[str]:
        return {
            item.data(Qt.UserRole) for item in self.requirements.selectedItems()
        }

    def selected_requirements(self) -> list:
        chosen = self.selected_ids()
        return [r for r in self._requirements if r.requirement_id in chosen]

    def _update_scope_label(self) -> None:
        chosen = self.selected_requirements()
        if not chosen:
            self.lbl_scope.setText(
                "No requirement selected - nothing would be generated."
            )
            return
        names = ", ".join(
            (r.external_id or r.display_id()) for r in chosen[:6]
        )
        more = "" if len(chosen) <= 6 else f" and {len(chosen) - 6} more"
        self.lbl_scope.setText(f"Scope: {len(chosen)} requirement(s) - {names}{more}")


class SpecificationView(QWidget):
    """The test specification: browse, filter, jump to sources."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._spec = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        self.heading = QLabel("No specification yet")
        self.heading.setWordWrap(True)
        header.addWidget(self.heading, 1)
        self.status_filter = QComboBox()
        self.status_filter.addItems(
            ["All", "draft", "needs_clarification", "approved", "rejected"]
        )
        self.status_filter.currentTextChanged.connect(self.refresh)
        self.search = QLineEdit()
        self.search.setPlaceholderText("filter by requirement, title or text")
        self.search.textChanged.connect(self.refresh)
        header.addWidget(self.status_filter)
        header.addWidget(self.search, 1)
        layout.addLayout(header)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Test specification", "Status"])
        self.tree.setColumnWidth(0, 620)
        self.tree.setAlternatingRowColors(True)
        layout.addWidget(self.tree, 1)

    def show_specification(self, spec) -> None:
        self._spec = spec
        self.refresh()

    def refresh(self) -> None:
        spec = self._spec
        self.tree.clear()
        if spec is None:
            self.heading.setText("No specification yet")
            return
        counts = spec.counts()
        self.heading.setText(
            f"<b>{spec.title}</b> - {spec.document_id}, revision {spec.revision} "
            f"&middot; {counts['test_cases']} case(s), {counts['approved']} approved, "
            f"{counts['requirements_in_scope']} requirement(s) in scope, "
            f"{counts['uncovered_requirements']} of them without a draft"
        )

        wanted = self.status_filter.currentText()
        needle = self.search.text().strip().lower()
        by_document: dict[str, list] = {}
        for entry in spec.entries:
            if wanted != "All" and entry.case.review_status.value != wanted:
                continue
            haystack = " ".join(
                [entry.case.title, entry.case.objective, *entry.case.requirement_ids]
            ).lower()
            if needle and needle not in haystack:
                continue
            document = next(
                (
                    state.document_filename or "unknown document"
                    for state in spec.requirement_states
                    for requirement in entry.requirements
                    if state.requirement.requirement_id == requirement.requirement_id
                ),
                "unknown document",
            )
            by_document.setdefault(document, []).append(entry)

        for document, entries in sorted(by_document.items()):
            top = QTreeWidgetItem([document, f"{len(entries)} case(s)"])
            for entry in entries:
                case = entry.case
                node = QTreeWidgetItem(
                    [
                        f"{case.case_id} - {case.title}",
                        f"{case.review_status.value}, revision {case.revision}",
                    ]
                )
                node.setData(0, Qt.UserRole, case.case_id)
                node.addChild(
                    QTreeWidgetItem(
                        [
                            "Requirements: "
                            + ", ".join(
                                r.external_id or r.display_id()
                                for r in entry.requirements
                            ),
                            "",
                        ]
                    )
                )
                for label, steps in (
                    ("Preconditions", case.preconditions),
                    ("Steps", case.steps),
                    ("Postconditions", case.postconditions),
                ):
                    if not steps:
                        continue
                    group = QTreeWidgetItem([f"{label} ({len(steps)})", ""])
                    for step in steps:
                        leaf = QTreeWidgetItem([f"{step.order}. {step.action}", ""])
                        for expectation in step.expectations:
                            leaf.addChild(
                                QTreeWidgetItem([f"Expected: {expectation}", ""])
                            )
                        group.addChild(leaf)
                    node.addChild(group)
                if entry.unresolved_questions:
                    group = QTreeWidgetItem(
                        [f"Open questions ({len(entry.unresolved_questions)})", ""]
                    )
                    for question in entry.unresolved_questions:
                        group.addChild(
                            QTreeWidgetItem([question.question, question.origin.value])
                        )
                    node.addChild(group)
                if entry.findings:
                    group = QTreeWidgetItem([f"Findings ({len(entry.findings)})", ""])
                    for issue in entry.findings:
                        group.addChild(
                            QTreeWidgetItem([f"{issue.code}: {issue.message}", ""])
                        )
                    node.addChild(group)
                if entry.context:
                    group = QTreeWidgetItem([f"Sources ({len(entry.context)})", ""])
                    for item in entry.context:
                        leaf = QTreeWidgetItem(
                            [
                                item.external_id
                                or item.heading
                                or item.requirement_id,
                                f"{item.kind.value}, {item.reason.value}",
                            ]
                        )
                        leaf.setToolTip(0, item.text[:1200])
                        leaf.addChild(QTreeWidgetItem([f"Source: {item.label}", ""]))
                        group.addChild(leaf)
                    node.addChild(group)
                top.addChild(node)
            self.tree.addTopLevelItem(top)

        for label, states in (
            ("Requirements in scope without a draft", spec.uncovered),
            ("Knowledge base, not in scope", spec.out_of_scope),
        ):
            if not states:
                continue
            top = QTreeWidgetItem([label, str(len(states))])
            for state in states:
                top.addChild(
                    QTreeWidgetItem(
                        [state.label, state.document_filename or "unknown document"]
                    )
                )
            self.tree.addTopLevelItem(top)
            top.setExpanded(False)
        self.tree.expandToDepth(0)
        # the two summary nodes stay collapsed: they would bury the cases
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item.text(0).startswith(("Requirements in scope", "Knowledge base")):
                item.setExpanded(False)

    def selected_case_id(self) -> str | None:
        item = self.tree.currentItem()
        while item is not None:
            case_id = item.data(0, Qt.UserRole)
            if case_id:
                return case_id
            item = item.parent()
        return None
