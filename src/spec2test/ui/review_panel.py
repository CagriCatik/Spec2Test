"""Review panel: decide on the selected draft.

Everything a reviewer needs for one draft: its status, the findings that block
approval, the open questions, and the decisions. The panel only calls the
review use cases; it contains no rules of its own.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..application.review import (
    ApprovalBlocked,
    CaseEdit,
    ReviewError,
    accept_finding,
    approval_report,
    approve_case,
    edit_case,
    reject_case,
    reopen_case,
    request_clarification,
    resolve_question,
    revisions_of,
)
from ..application.step_text import StepTextError, steps_to_text, text_to_steps
from ..domain.models import Project, ReviewStatus, TestCase

STATUS_TEXT = {
    ReviewStatus.DRAFT: "Draft",
    ReviewStatus.NEEDS_CLARIFICATION: "Needs clarification",
    ReviewStatus.APPROVED: "Approved",
    ReviewStatus.REJECTED: "Rejected",
}


class AnswerDialog(QDialog):
    """Ask for a free-text answer with a title and a prompt."""

    def __init__(self, parent, title: str, prompt: str, placeholder: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        label = QLabel(prompt)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(placeholder)
        layout.addWidget(self.text)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def answer(self) -> str:
        return self.text.toPlainText().strip()

    @classmethod
    def ask(cls, parent, title: str, prompt: str, placeholder: str = "") -> str | None:
        dialog = cls(parent, title, prompt, placeholder)
        if dialog.exec() != QDialog.Accepted:
            return None
        return dialog.answer() or None


class EditCaseDialog(QDialog):
    """Edit a draft. Saving creates a new revision."""

    def __init__(self, parent, case: TestCase) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit {case.case_id} (revision {case.revision})")
        self.setMinimumSize(720, 560)
        self._case = case

        layout = QVBoxLayout(self)
        note = QLabel(
            "Saving creates a new revision. The current revision is kept, and "
            "an existing approval does not carry over."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        form = QFormLayout()
        self.title = QLineEdit(case.title)
        self.objective = QLineEdit(case.objective)
        self.requirements = QLineEdit(", ".join(case.requirement_ids))
        form.addRow("Title", self.title)
        form.addRow("Objective", self.objective)
        form.addRow("Requirement IDs", self.requirements)
        layout.addLayout(form)

        for label, attribute, source in (
            ("Preconditions", "preconditions_text", case.preconditions),
            ("Steps", "steps_text", case.steps),
            ("Postconditions", "postconditions_text", case.postconditions),
        ):
            group = QGroupBox(label)
            box = QVBoxLayout(group)
            editor = QPlainTextEdit(steps_to_text(source))
            editor.setPlaceholderText(
                "1. action\n   - expected result\n2. next action\n   - expected result"
            )
            box.addWidget(editor)
            setattr(self, attribute, editor)
            layout.addWidget(group)

        self.reason = QLineEdit()
        self.reason.setPlaceholderText("why this revision (optional)")
        layout.addWidget(self.reason)

        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        try:
            self.edit()
        except StepTextError as exc:
            self.error.setText(str(exc))
            return
        self.accept()

    def edit(self) -> CaseEdit:
        """The change, or a parse error for the step text."""
        requirement_ids = tuple(
            part.strip() for part in self.requirements.text().split(",") if part.strip()
        )
        return CaseEdit(
            title=self.title.text().strip(),
            objective=self.objective.text().strip(),
            requirement_ids=requirement_ids,
            preconditions=text_to_steps(self.preconditions_text.toPlainText()),
            steps=text_to_steps(self.steps_text.toPlainText()),
            postconditions=text_to_steps(self.postconditions_text.toPlainText()),
        )

    def note(self) -> str | None:
        return self.reason.text().strip() or None


class ReviewPanel(QWidget):
    """Actions for the selected draft."""

    changed = Signal(str)   # case id
    logged = Signal(str)

    def __init__(self, parent=None, *, reviewer: str = "tester") -> None:
        super().__init__(parent)
        self.reviewer = reviewer
        self._project: Project | None = None
        self._case_id: str | None = None
        self._ask: Callable[..., str | None] = AnswerDialog.ask

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)

        self.header = QLabel("Select a draft to review it.")
        self.header.setWordWrap(True)
        layout.addWidget(self.header)

        lists = QHBoxLayout()
        findings_box = QGroupBox("Findings blocking approval")
        findings_layout = QVBoxLayout(findings_box)
        self.findings = QListWidget()
        self.findings.setWordWrap(True)
        self.findings.setMaximumHeight(130)
        findings_layout.addWidget(self.findings)
        self.btn_accept = QPushButton("Accept selected finding with a reason…")
        self.btn_accept.clicked.connect(self.accept_selected_finding)
        findings_layout.addWidget(self.btn_accept)
        lists.addWidget(findings_box, 1)

        questions_box = QGroupBox("Open questions")
        questions_layout = QVBoxLayout(questions_box)
        self.questions = QListWidget()
        self.questions.setWordWrap(True)
        self.questions.setMaximumHeight(130)
        questions_layout.addWidget(self.questions)
        self.btn_answer = QPushButton("Answer selected question…")
        self.btn_answer.clicked.connect(self.answer_selected_question)
        questions_layout.addWidget(self.btn_answer)
        lists.addWidget(questions_box, 1)
        layout.addLayout(lists)

        actions = QHBoxLayout()
        self.btn_edit = QPushButton("Edit…")
        self.btn_edit.clicked.connect(self.edit_selected)
        self.btn_clarify = QPushButton("Needs clarification…")
        self.btn_clarify.clicked.connect(self.request_clarification)
        self.btn_reject = QPushButton("Reject…")
        self.btn_reject.clicked.connect(self.reject)
        self.btn_reopen = QPushButton("Reopen")
        self.btn_reopen.clicked.connect(self.reopen)
        self.btn_approve = QPushButton("Approve")
        self.btn_approve.setProperty("role", "primary")
        self.btn_approve.clicked.connect(self.approve)
        actions.addWidget(self.btn_edit)
        actions.addWidget(self.btn_clarify)
        actions.addWidget(self.btn_reject)
        actions.addWidget(self.btn_reopen)
        actions.addStretch(1)
        actions.addWidget(self.btn_approve)
        layout.addLayout(actions)

        self.show_case(None, None)

    # ----------------------------------------------------------- state --
    def show_case(self, project: Project | None, case_id: str | None) -> None:
        self._project = project
        self._case_id = case_id
        case = project.case(case_id) if project and case_id else None

        self.findings.clear()
        self.questions.clear()
        enabled = case is not None
        for button in (
            self.btn_edit,
            self.btn_clarify,
            self.btn_reject,
            self.btn_reopen,
            self.btn_approve,
            self.btn_accept,
            self.btn_answer,
        ):
            button.setEnabled(enabled)

        if case is None or project is None:
            self.header.setText("Select a draft to review it.")
            return

        allowed, report = approval_report(project, case.case_id)
        parts = [
            f"<b>{case.case_id}</b> - {STATUS_TEXT.get(case.review_status)}",
            f"revision {case.revision}",
        ]
        if case.needs_recheck:
            parts.append("<b>the requirements changed; review again</b>")
        if case.review_note:
            parts.append(f"note: {case.review_note}")
        parts.append("ready to approve" if allowed else "approval is blocked")
        self.header.setText(" &middot; ".join(parts))

        for issue in report.issues:
            item = QListWidgetItem(f"{issue.code}: {issue.message}")
            item.setToolTip(f"{issue.path}\n{issue.message}")
            item.setData(Qt.UserRole, (issue.code, issue.path, issue.severity.value))
            self.findings.addItem(item)
        for finding in case.accepted_findings:
            item = QListWidgetItem(f"accepted: {finding.code} - {finding.justification}")
            item.setFlags(Qt.ItemIsEnabled)
            self.findings.addItem(item)

        for question in case.open_questions:
            prefix = "answered" if question.resolved else "open"
            item = QListWidgetItem(f"[{prefix}] {question.question}")
            if question.resolved:
                item.setToolTip(f"{question.answer} ({question.resolved_by})")
                item.setFlags(Qt.ItemIsEnabled)
            item.setData(Qt.UserRole, question.question_id)
            self.questions.addItem(item)

        self.btn_approve.setEnabled(allowed)

    def _require(self) -> tuple[Project, str]:
        if self._project is None or self._case_id is None:
            raise ReviewError("no draft selected")
        return self._project, self._case_id

    def _done(self, message: str) -> None:
        self.logged.emit(message)
        self.show_case(self._project, self._case_id)
        if self._case_id:
            self.changed.emit(self._case_id)

    # --------------------------------------------------------- actions --
    def accept_selected_finding(self) -> None:
        project, case_id = self._require()
        item = self.findings.currentItem()
        data = item.data(Qt.UserRole) if item else None
        if not data:
            self.logged.emit("Select a finding first.")
            return
        code, path, severity = data
        if severity == "error":
            self.logged.emit(
                f"{code} is an error and cannot be accepted; edit the draft instead."
            )
            return
        reason = self._ask(
            self,
            "Accept finding",
            f"Why is {code} acceptable for {case_id}?",
            "for example: measured on the rig, see test concept 4.2",
        )
        if not reason:
            return
        try:
            accept_finding(project, case_id, code, path, reason, by=self.reviewer)
        except ReviewError as exc:
            self.logged.emit(str(exc))
            return
        self._done(f"{case_id}: accepted {code} - {reason}")

    def answer_selected_question(self) -> None:
        project, case_id = self._require()
        item = self.questions.currentItem()
        question_id = item.data(Qt.UserRole) if item else None
        if not question_id:
            self.logged.emit("Select an open question first.")
            return
        answer = self._ask(
            self,
            "Answer question",
            f"Answer for {question_id}. The question is kept with your answer.",
            "for example: limit confirmed as 10 deg by the system owner",
        )
        if not answer:
            return
        try:
            resolve_question(project, case_id, question_id, answer, by=self.reviewer)
        except (ReviewError, ValueError) as exc:
            self.logged.emit(str(exc))
            return
        self._done(f"{case_id}: {question_id} answered")

    def edit_selected(self) -> None:
        project, case_id = self._require()
        case = project.case(case_id)
        dialog = EditCaseDialog(self, case)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            updated = edit_case(
                project, case_id, dialog.edit(), by=self.reviewer, note=dialog.note()
            )
        except (ReviewError, StepTextError) as exc:
            self.logged.emit(str(exc))
            return
        self._done(f"{case_id}: saved as revision {updated.revision}")

    def approve(self) -> None:
        project, case_id = self._require()
        note = self._ask(
            self, "Approve", f"Note for the approval of {case_id} (optional).", ""
        )
        try:
            approve_case(project, case_id, by=self.reviewer, note=note)
        except ApprovalBlocked as exc:
            self.logged.emit(str(exc))
            self.show_case(project, case_id)
            return
        self._done(f"{case_id}: approved")

    def reject(self) -> None:
        project, case_id = self._require()
        reason = self._ask(
            self, "Reject", f"Why is {case_id} rejected?", "a reason is required"
        )
        if not reason:
            return
        reject_case(project, case_id, by=self.reviewer, note=reason)
        self._done(f"{case_id}: rejected - {reason}")

    def request_clarification(self) -> None:
        project, case_id = self._require()
        note = self._ask(
            self, "Needs clarification", f"What has to be clarified for {case_id}?", ""
        )
        request_clarification(project, case_id, by=self.reviewer, note=note)
        self._done(f"{case_id}: marked as needing clarification")

    def reopen(self) -> None:
        project, case_id = self._require()
        reopen_case(project, case_id, by=self.reviewer)
        self._done(f"{case_id}: reopened as draft")

    def revision_summary(self) -> list[str]:
        project, case_id = self._require()
        return [
            f"revision {case.revision}: {case.title} [{case.review_status.value}]"
            for case in revisions_of(project, case_id)
        ]
