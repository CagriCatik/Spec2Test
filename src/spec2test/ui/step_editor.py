"""A step editor that does not lose what it cannot show.

The previous dialog round-tripped every step through free text. Measured:
editing nothing but the title destroyed the measurements and the grounds of
every step, and turned each measurement into a prose expectation:

    before: measurements 1 | grounds 1
    after : measurements 0 | grounds 0
    expectations after: ('the latch asserts within 2.0 us',
                         'response time = 2.0 us')

Tolerance and evidence were gone without a word.

Two ideas fix that:

**Nothing is parsed back out of prose.** Each step is a row with its own
fields; a measurement is a row of its own with quantity, value, unit,
tolerance and evidence. What the editor does not offer, it carries
through untouched rather than re-deriving.

**A field the editor cannot show is still kept — but not blindly.**
Grounds belong to a field (``expectations[0]``) and are written by the
model or the review workflow, not typed here. They ride along with their
step, and their address is followed when rows move:

* a field that only moved keeps its ground, with the address corrected;
* a field whose text was **changed** keeps its ground marked stale: an old
  quote must not go on covering a sentence that now says something else;
* a field that was **removed** leaves its ground stale too, rather than
  pointing at whatever took its place.

A stale ground still shows its quote — throwing it away because a
neighbouring word changed helps nobody — but it stops counting as
coverage, so the claim reappears as unsupported until someone looks.

The result is that a title-only change is exactly a title-only change: the
steps come back identical, the revision does not rise, and the grounding
rule version does not move either.
"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..domain.models import Measurement, TestStep

#: what a row is
ACTION, EXPECTATION, MEASUREMENT = "action", "expectation", "measurement"
_ROLE = Qt.UserRole + 1
_PAYLOAD = Qt.UserRole + 2
#: the field address this row had when it was loaded, e.g. "expectations[0]"
_ORIGIN = Qt.UserRole + 3
#: the text it had then, so a change can be told from a move
_ORIGINAL_TEXT = Qt.UserRole + 4

COLUMNS = ("Step / field", "Value", "Unit", "Tolerance", "Evidence")


class StepEditor(QWidget):
    """Edit actions, expectations and measurements without losing anything."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._steps: tuple[TestStep, ...] = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(list(COLUMNS))
        self.tree.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked
        )
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.tree)

        buttons = QHBoxLayout()
        self.btn_add_step = QPushButton("Add step")
        self.btn_add_expectation = QPushButton("Add expected result")
        self.btn_add_measurement = QPushButton("Add measurement")
        self.btn_remove = QPushButton("Remove row")
        for button, slot in (
            (self.btn_add_step, self.add_step),
            (self.btn_add_expectation, self.add_expectation),
            (self.btn_add_measurement, self.add_measurement),
            (self.btn_remove, self.remove_selected),
        ):
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.lbl_kept = QLabel()
        self.lbl_kept.setWordWrap(True)
        self.lbl_kept.setProperty("role", "hint")
        layout.addWidget(self.lbl_kept)

    # ------------------------------------------------------------ load --
    def set_steps(self, steps) -> None:
        self._steps = tuple(steps)
        self.tree.clear()
        kept = 0
        for index, step in enumerate(self._steps):
            node = QTreeWidgetItem([f"{step.order}. ", step.action, "", "", ""])
            node.setData(0, _ROLE, ACTION)
            node.setData(0, _PAYLOAD, index)
            node.setFlags(node.flags() | Qt.ItemIsEditable)
            for position, expectation in enumerate(step.expectations):
                child = QTreeWidgetItem(["expected", expectation, "", "", ""])
                child.setData(0, _ROLE, EXPECTATION)
                child.setData(0, _ORIGIN, f"expectations[{position}]")
                child.setData(0, _ORIGINAL_TEXT, expectation)
                child.setFlags(child.flags() | Qt.ItemIsEditable)
                node.addChild(child)
            for position, measurement in enumerate(step.measurements):
                child = QTreeWidgetItem(
                    [
                        "measured",
                        measurement.quantity,
                        measurement.unit or "",
                        measurement.tolerance or "",
                        measurement.evidence or "",
                    ]
                )
                child.setData(0, _ROLE, MEASUREMENT)
                child.setData(0, _ORIGIN, f"measurements[{position}]")
                child.setData(
                    0,
                    _ORIGINAL_TEXT,
                    "|".join(
                        [
                            measurement.quantity,
                            measurement.value or "",
                            measurement.unit or "",
                            measurement.tolerance or "",
                            measurement.evidence or "",
                        ]
                    ),
                )
                # the measured value lives in its own column so it can be
                # edited without being re-parsed out of a sentence
                child.setText(1, measurement.quantity)
                child.setToolTip(1, "quantity")
                child.setData(1, _PAYLOAD, measurement.value or "")
                child.setFlags(child.flags() | Qt.ItemIsEditable)
                node.addChild(child)
            kept += len(step.grounds)
            node.setExpanded(True)
            self.tree.addTopLevelItem(node)
        self.lbl_kept.setText(
            f"{kept} ground(s) belong to these steps. They are not edited "
            "here and are carried through unchanged."
            if kept
            else "These steps carry no grounds yet."
        )

    # ------------------------------------------------------------ edit --
    def _current_step_node(self) -> QTreeWidgetItem | None:
        item = self.tree.currentItem()
        if item is None:
            return self.tree.topLevelItem(self.tree.topLevelItemCount() - 1)
        return item if item.parent() is None else item.parent()

    def add_step(self) -> None:
        node = QTreeWidgetItem([f"{self.tree.topLevelItemCount() + 1}. ", "", "", "", ""])
        node.setData(0, _ROLE, ACTION)
        node.setData(0, _PAYLOAD, None)
        node.setFlags(node.flags() | Qt.ItemIsEditable)
        self.tree.addTopLevelItem(node)
        self.tree.setCurrentItem(node)

    def _add_child(self, role: str, labels) -> None:
        node = self._current_step_node()
        if node is None:
            self.add_step()
            node = self.tree.topLevelItem(self.tree.topLevelItemCount() - 1)
        child = QTreeWidgetItem(list(labels))
        child.setData(0, _ROLE, role)
        child.setFlags(child.flags() | Qt.ItemIsEditable)
        node.addChild(child)
        node.setExpanded(True)
        self.tree.setCurrentItem(child)

    def add_expectation(self) -> None:
        self._add_child(EXPECTATION, ["expected", "", "", "", ""])

    def add_measurement(self) -> None:
        self._add_child(MEASUREMENT, ["measured", "", "", "", ""])

    def remove_selected(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        parent = item.parent()
        if parent is None:
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
        else:
            parent.removeChild(item)

    # ------------------------------------------------------------ read --
    def steps(self) -> tuple[TestStep, ...]:
        """The edited steps, with everything the editor does not show kept."""
        out: list[TestStep] = []
        for position in range(self.tree.topLevelItemCount()):
            node = self.tree.topLevelItem(position)
            action = node.text(1).strip()
            expectations: list[str] = []
            measurements: list[Measurement] = []
            #: old address -> (new address, changed?) so grounds can follow
            moves: dict[str, tuple[str | None, bool]] = {}
            for index in range(node.childCount()):
                child = node.child(index)
                role = child.data(0, _ROLE)
                if role == EXPECTATION:
                    text = child.text(1).strip()
                    if not text:
                        continue
                    origin = child.data(0, _ORIGIN)
                    if origin:
                        moves[origin] = (
                            f"expectations[{len(expectations)}]",
                            text != (child.data(0, _ORIGINAL_TEXT) or ""),
                        )
                    expectations.append(text)
                elif role == MEASUREMENT:
                    quantity = child.text(1).strip()
                    if not quantity:
                        continue
                    stored = child.data(1, _PAYLOAD)
                    now = "|".join(
                        [
                            quantity,
                            stored or "",
                            child.text(2).strip(),
                            child.text(3).strip(),
                            child.text(4).strip(),
                        ]
                    )
                    origin = child.data(0, _ORIGIN)
                    if origin:
                        moves[origin] = (
                            f"measurements[{len(measurements)}]",
                            now != (child.data(0, _ORIGINAL_TEXT) or ""),
                        )
                    measurements.append(
                        Measurement(
                            quantity=quantity,
                            value=(stored or None),
                            unit=child.text(2).strip() or None,
                            tolerance=child.text(3).strip() or None,
                            evidence=child.text(4).strip() or None,
                        )
                    )
            original_index = node.data(0, _PAYLOAD)
            original = (
                self._steps[original_index]
                if isinstance(original_index, int) and original_index < len(self._steps)
                else None
            )
            if original is not None:
                out.append(
                    replace(
                        original,
                        order=position + 1,
                        action=action,
                        expectations=tuple(expectations),
                        measurements=tuple(measurements),
                        grounds=_follow_grounds(original.grounds, moves),
                    )
                )
            else:
                out.append(
                    TestStep(
                        order=position + 1,
                        action=action,
                        expectations=tuple(expectations),
                        measurements=tuple(measurements),
                    )
                )
        return tuple(out)

    def unchanged(self) -> bool:
        """True when nothing a reviewer typed differs from what was loaded."""
        return self.steps() == self._steps


def _follow_grounds(grounds, moves: dict) -> tuple:
    """Move each ground to where its field went, or mark it stale.

    ``moves`` maps the address a field had when the editor loaded to the
    address it has now and whether its text changed. A field that is not in
    the map was removed.
    """
    out = []
    for ground in grounds:
        if not ground.target:
            out.append(ground)
            continue
        moved = moves.get(ground.target)
        if moved is None:
            out.append(
                replace(
                    ground,
                    stale_reason=(
                        f"the field {ground.target} it belonged to was removed"
                    ),
                )
            )
            continue
        new_target, changed = moved
        if changed:
            out.append(
                replace(
                    ground,
                    target=new_target,
                    stale_reason=f"the text of {ground.target} was edited",
                )
            )
        elif new_target != ground.target:
            # only moved: the sentence is the same, so the quote still holds
            out.append(replace(ground, target=new_target))
        else:
            out.append(ground)
    return tuple(out)


__all__ = [
    "ACTION",
    "COLUMNS",
    "EXPECTATION",
    "MEASUREMENT",
    "StepEditor",
]
