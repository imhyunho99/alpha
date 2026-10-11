"""여러 화면이 같이 쓰는 작은 부품: 작업 스레드, 표 채우기, 값 정리."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem


class _Worker(QThread):
    done = Signal(object)

    def __init__(self, fn, *args):
        super().__init__()
        self._fn, self._args = fn, args

    def run(self):
        try:
            self.done.emit(self._fn(*self._args))
        except Exception as exc:
            self.done.emit({"error": str(exc)})


def _num(value, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) else default


def _rows(value) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _make_table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _fill(table: QTableWidget, rows: list[list[str]]) -> None:
    table.setRowCount(len(rows))
    for r, cells in enumerate(rows):
        for c, text in enumerate(cells):
            table.setItem(r, c, QTableWidgetItem(text))
