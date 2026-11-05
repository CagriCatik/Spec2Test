"""Qt table model over the flat export rows."""
from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from ..adapters.export import COLUMNS as ALLOWED_KEYS


class TestCaseTableModel(QAbstractTableModel):
    """Qt model to show the list[dict] in a QTableView."""

    def __init__(self, cases: List[Dict] | None = None):
        super().__init__()
        self._cases: List[Dict] = cases or []

    # --------------------------- Qt overrides ---------------------------
    def rowCount(self, parent: QModelIndex | None = None):
        return len(self._cases)

    def columnCount(self, parent: QModelIndex | None = None):
        return len(ALLOWED_KEYS)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or role not in (Qt.DisplayRole, Qt.ToolTipRole):
            return None
        row, col = index.row(), index.column()
        key = ALLOWED_KEYS[col]
        value = self._cases[row].get(key, "")
        # pretty‑print lists compactly
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        return str(value)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return ALLOWED_KEYS[section]
        return str(section + 1)

    # --------------------------- public API -----------------------------
    def update_cases(self, cases: List[Dict]):
        self.beginResetModel()
        self._cases = cases
        self.endResetModel()
