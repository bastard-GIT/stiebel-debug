"""Werte-Tab: letzter Istwert je Typ als Tabelle, Verlauf des gewaehlten Typs als QtChart.

Unbekannte Typen erscheinen als UNK_0x.. (ohne Skalierung). Quelle: DataModel.latest und history().
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCharts import QChart, QChartView, QDateTimeAxis, QLineSeries, QValueAxis
from PySide6.QtCore import QDateTime, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

CHART_POINTS = 2000  # Verlauf: die letzten Punkte je Typ
COLUMNS = ["Typ", "Name", "Wert", "Einheit", "Zeit"]


def _type_label(type_id: int) -> str:
    return f"0x{type_id:02x}"


class WerteTab(QWidget):
    """Tabelle der letzten Werte und Verlaufsdiagramm fuer einen Typ."""

    def __init__(self, model, bridge=None, parent=None) -> None:
        super().__init__(parent)
        self._model = model

        self._type_combo = QComboBox()
        self._type_combo.setMinimumWidth(260)
        self._info = QLabel("")

        top = QHBoxLayout()
        top.addWidget(QLabel("Verlauf fuer Typ:"))
        top.addWidget(self._type_combo, 1)
        top.addWidget(self._info, 2)

        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        self._series = QLineSeries()
        self._series.setName("Wert")
        self._chart = QChart()
        self._chart.addSeries(self._series)
        self._chart.legend().hide()
        self._axis_x = QDateTimeAxis()
        self._axis_x.setFormat("HH:mm:ss")
        self._axis_x.setTitleText("Zeit")
        self._axis_y = QValueAxis()
        self._axis_y.setTitleText("Wert")
        self._chart.addAxis(self._axis_x, Qt.AlignmentFlag.AlignBottom)
        self._chart.addAxis(self._axis_y, Qt.AlignmentFlag.AlignLeft)
        self._series.attachAxis(self._axis_x)
        self._series.attachAxis(self._axis_y)
        self._chart_view = QChartView(self._chart)
        self._chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._table)
        splitter.addWidget(self._chart_view)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(splitter, 1)

        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        self.refresh()

    # ----- Oeffentliche Hooks -----

    def on_frames(self, rows: list[dict]) -> None:
        self.refresh()

    def refresh(self) -> None:
        self._fill_table()
        self._fill_combo()
        self._fill_chart()

    # ----- Intern -----

    def _fill_table(self) -> None:
        latest = self._model.latest
        self._table.setRowCount(len(latest))
        for r, type_id in enumerate(sorted(latest)):
            item = latest[type_id]
            ts = item.get("ts")
            zeit = datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S") if ts is not None else ""
            value = item.get("value")
            cells = [
                _type_label(type_id),
                str(item.get("name") or ""),
                "" if value is None else f"{value}",
                str(item.get("unit") or ""),
                zeit,
            ]
            for c, text in enumerate(cells):
                self._table.setItem(r, c, QTableWidgetItem(text))

    def _fill_combo(self) -> None:
        """Typliste neu aufbauen, Auswahl nach Moeglichkeit behalten (ohne Signal)."""
        wanted = sorted(self._model.latest)
        current = self._type_combo.currentData()
        self._type_combo.blockSignals(True)
        self._type_combo.clear()
        for type_id in wanted:
            name = self._model.latest[type_id].get("name") or ""
            self._type_combo.addItem(f"{_type_label(type_id)}  {name}", type_id)
        if current is not None:
            idx = self._type_combo.findData(current)
            if idx >= 0:
                self._type_combo.setCurrentIndex(idx)
        self._type_combo.blockSignals(False)

    def _on_type_changed(self, _index: int) -> None:
        self._fill_chart()

    def _fill_chart(self) -> None:
        type_id = self._type_combo.currentData()
        self._series.clear()
        if type_id is None:
            self._info.setText("Keine Istwerte empfangen")
            return
        points = self._model.history(int(type_id))[-CHART_POINTS:]
        for ts, value in points:
            self._series.append(ts * 1000.0, value)
        name = self._model.latest.get(int(type_id), {}).get("name") or ""
        self._info.setText(f"{name}: {len(points)} Punkte")
        self._chart.setTitle(name)
        if not points:
            return
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        x_min, x_max = min(xs), max(xs)
        if x_max - x_min < 1.0:
            x_max = x_min + 1.0
        y_min, y_max = min(ys), max(ys)
        margin = max((y_max - y_min) * 0.1, 0.5)
        self._axis_x.setRange(QDateTime.fromMSecsSinceEpoch(int(x_min * 1000)),
                              QDateTime.fromMSecsSinceEpoch(int(x_max * 1000)))
        self._axis_y.setRange(y_min - margin, y_max + margin)
