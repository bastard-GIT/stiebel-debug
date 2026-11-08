"""Display-Tab: 28-Byte-Segmentpaket (f7 20) als Bitraster, geaenderte Bits hervorgehoben.

Darunter das Tasten-Log (e3 30 20). Zeilen = Bytes (Index 0 ist das erste Byte des Pakets,
also f7), Spalten = Bits MSB zuerst. Geaendert gegenueber dem vorherigen Paket: orange.
Die Bitbelegung ist nicht gesichert (siehe referenz/lwz180_protocol.md, Abschnitt 6).
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lwz180 import KEY_NAMES

DISPLAY_LEN = 28
KEY_LOG_SHOW = 500
COLOR_ON = QColor("#90caf9")        # Bit = 1
COLOR_CHANGED = QColor("#ffb74d")   # Bit hat sich gegenueber dem vorherigen Paket geaendert
COLOR_TEXT = QColor("#000000")


class DisplayTab(QWidget):
    """Bitraster des Displaypakets und Tasten-Log."""

    def __init__(self, model, bridge=None, parent=None) -> None:
        super().__init__(parent)
        self._model = model

        self._info = QLabel("Noch kein Display-Paket empfangen")
        self._raster = QTableWidget(DISPLAY_LEN, 8)
        self._raster.setHorizontalHeaderLabels([f"b{7 - i}" for i in range(8)])
        self._raster.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._raster.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._raster.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._raster.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self._raster.verticalHeader().setDefaultSectionSize(20)
        for r in range(DISPLAY_LEN):
            for c in range(8):
                self._raster.setItem(r, c, QTableWidgetItem(""))

        self._keys = QTableWidget(0, 3)
        self._keys.setHorizontalHeaderLabels(["Zeit", "Code", "Taste"])
        self._keys.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._keys.verticalHeader().setVisible(False)
        self._keys.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        top_box = QWidget()
        top_layout = QVBoxLayout(top_box)
        top_layout.addWidget(self._info)
        top_layout.addWidget(self._raster, 1)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(top_box)
        splitter.addWidget(self._keys)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        legend = QHBoxLayout()
        legend.addWidget(QLabel("Blau = Bit 1, orange = gegenueber vorherigem Paket geaendert"))
        legend.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(legend)
        layout.addWidget(splitter, 1)
        self.refresh()

    # ----- Oeffentliche Hooks -----

    def on_frames(self, rows: list[dict]) -> None:
        self.refresh()

    def refresh(self) -> None:
        self._fill_raster()
        self._fill_keys()

    # ----- Intern -----

    def _fill_raster(self) -> None:
        cur = self._model.display_current
        if cur is None:
            self._info.setText("Noch kein Display-Paket empfangen")
            self._raster.setVerticalHeaderLabels([f"{r:02d}" for r in range(DISPLAY_LEN)])
            for r in range(DISPLAY_LEN):
                for c in range(8):
                    self._set_cell(r, c, "", None)
            return
        changed = set(self._model.display_changed_bits())
        self._info.setText(f"Paket: {cur.hex(' ')}   geaenderte Bits: {len(changed)}")
        self._raster.setVerticalHeaderLabels([f"{r:02d} {cur[r]:02x}" for r in range(len(cur))])
        for r in range(min(DISPLAY_LEN, len(cur))):
            for c in range(8):
                bit_index = r * 8 + c          # MSB zuerst: c = 0 ist Bit 7
                bit = (cur[r] >> (7 - c)) & 1
                if bit_index in changed:
                    color = COLOR_CHANGED
                elif bit:
                    color = COLOR_ON
                else:
                    color = None
                self._set_cell(r, c, "1" if bit else "0", color)

    def _set_cell(self, row: int, col: int, text: str, color: QColor | None) -> None:
        item = self._raster.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            self._raster.setItem(row, col, item)
        item.setText(text)
        if color is None:
            item.setBackground(QBrush())
            item.setForeground(QBrush())
        else:
            item.setBackground(QBrush(color))
            item.setForeground(QBrush(COLOR_TEXT))

    def _fill_keys(self) -> None:
        log = self._model.key_log[-KEY_LOG_SHOW:]
        self._keys.setRowCount(len(log))
        for r, (ts, codes) in enumerate(reversed(log)):
            zeit = datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S.%f")[:-3]
            code_text = ",".join(str(c) for c in codes)
            names = ", ".join(KEY_NAMES.get(c, f"0x{c:02x}") for c in codes)
            for c, text in enumerate([zeit, code_text, names]):
                self._keys.setItem(r, c, QTableWidgetItem(text))
