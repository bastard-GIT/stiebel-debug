"""Index-Browser: alle Indizes einer Messung mit Abgleich gegen Referenz und mk-git.

Reine Qt-Ansicht. Daten: data_model.Measurements, reference_model.ReferenceModel,
type_guess.load_mkgit_table (dict Index -> (Name, Typ)).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

COLUMNS = ["Index", "Name Ref", "Name mk-git", "Status", "Member", "Min/Max", "Typ-Abweichung"]
COL_INDEX, COL_NAME_REF, COL_NAME_MK, COL_STATUS, COL_MEMBER, COL_MINMAX, COL_TYPE = range(7)
NUMERIC_COLS = (COL_INDEX, COL_MEMBER)
RIGHT_ALIGNED_COLS = NUMERIC_COLS

ST_REF = "Referenz"
ST_MK = "nur mk-git"
ST_UNK = "unbekannt"
# Dezente Hintergrundfarben mit Alpha: lesbar im Hell- und Dunkelmodus.
STATUS_COLORS = {
    ST_REF: QColor(46, 160, 67, 70),
    ST_MK: QColor(230, 180, 0, 70),
    ST_UNK: QColor(220, 53, 69, 60),
}
STATUS_CHOICES = (("alle", None), ("Referenz", ST_REF), ("nur mk-git", ST_MK), ("unbekannt", ST_UNK))


@dataclass
class _Row:
    index: int
    name_ref: str
    name_mk: str
    status: str
    members: int          # Anzahl Member mit Status 'ok'
    has_minmax: bool      # mindestens ein Member mit min_raw und max_raw
    type_diff: bool       # Ref-Typ und mk-git-Typ vorhanden und verschieden
    search: str           # Suchtext (kleingeschrieben): Index-Hex und Namen


class _NumItem(QTableWidgetItem):
    """Zelle mit numerischer Sortierung ueber Qt.UserRole (Anzeige bleibt Text)."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        a = self.data(Qt.UserRole)
        b = other.data(Qt.UserRole)
        if a is None or b is None:
            return super().__lt__(other)
        return a < b


class IndexBrowser(QWidget):
    indexSelected = Signal(int)

    def __init__(self, meas, ref, mkgit, parent=None):
        super().__init__(parent)
        self._meas = meas
        self._ref = ref
        self._mkgit = mkgit or {}
        self._recs: dict[int, _Row] = {}
        self._loading = False     # unterdrueckt Signale waehrend reload/refresh
        self._last: int | None = None  # zuletzt gemeldeter Index

        self._build_ui()
        self.reload()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        self._search = QLineEdit()
        self._search.setPlaceholderText("Index (0x...) oder Name")
        self._status = QComboBox()
        for label, value in STATUS_CHOICES:
            self._status.addItem(label, value)
        self._only_minmax = QCheckBox("nur mit Min/Max")
        self._only_diff = QCheckBox("nur Typ-Abweichung")
        self._count = QLabel()

        filters = QHBoxLayout()
        filters.addWidget(self._search, 1)
        filters.addWidget(self._status)
        filters.addWidget(self._only_minmax)
        filters.addWidget(self._only_diff)
        filters.addWidget(self._count)

        self._back_btn = QPushButton("Zurueck")
        self._next_btn = QPushButton("Weiter")
        self._unknown_btn = QPushButton("Naechster unbekannter")
        nav = QHBoxLayout()
        nav.addWidget(self._back_btn)
        nav.addWidget(self._next_btn)
        nav.addWidget(self._unknown_btn)
        nav.addStretch(1)

        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        for col in range(len(COLUMNS)):
            mode = QHeaderView.Stretch if col in (COL_NAME_REF, COL_NAME_MK) else QHeaderView.ResizeToContents
            header.setSectionResizeMode(col, mode)

        layout = QVBoxLayout(self)
        layout.addLayout(filters)
        layout.addLayout(nav)
        layout.addWidget(self._table, 1)

        self._search.textChanged.connect(self._apply_filter)
        self._status.currentIndexChanged.connect(self._apply_filter)
        self._only_minmax.toggled.connect(self._apply_filter)
        self._only_diff.toggled.connect(self._apply_filter)
        self._back_btn.clicked.connect(lambda: self._go(-1, unknown_only=False, wrap=False))
        self._next_btn.clicked.connect(lambda: self._go(+1, unknown_only=False, wrap=False))
        self._unknown_btn.clicked.connect(lambda: self._go(+1, unknown_only=True, wrap=True))
        self._table.itemSelectionChanged.connect(self._on_selection)

    # ---------- Daten ----------

    def _build_rows(self) -> dict[int, _Row]:
        # Referenz-Signale einmal als Map: ref.signal() waere pro Index eine lineare Suche.
        ref_map = {int(s["index_int"]): s for s in self._ref.data.get("signals", [])
                   if not s.get("sentinel")}
        recs: dict[int, _Row] = {}
        for index in self._meas.indices:
            cells = self._meas.cells(index)
            sig = ref_map.get(index)
            mk_name, mk_type = self._mkgit.get(index, ("", ""))
            if sig is not None:
                status = ST_REF
            elif index in self._mkgit:
                status = ST_MK
            else:
                status = ST_UNK
            name_ref = sig.get("name", "") if sig is not None else ""
            type_ref = sig.get("type", "") if sig is not None else ""
            recs[index] = _Row(
                index=index,
                name_ref=name_ref,
                name_mk=mk_name,
                status=status,
                members=sum(1 for c in cells.values() if c.status == "ok"),
                has_minmax=any(c.min_raw is not None and c.max_raw is not None
                               for c in cells.values()),
                type_diff=bool(type_ref and mk_type and type_ref != mk_type),
                search=f"0x{index:04x} {name_ref} {mk_name}".lower(),
            )
        return recs

    @staticmethod
    def _texts(rec: _Row) -> list[str]:
        return [
            f"0x{rec.index:04x}",
            rec.name_ref,
            rec.name_mk,
            rec.status,
            str(rec.members),
            "ja" if rec.has_minmax else "nein",
            "ja" if rec.type_diff else "nein",
        ]

    def _set_row(self, row: int, rec: _Row) -> None:
        """Zeile neu anlegen (Tabelle ohne Sortierung, ohne Auswahl)."""
        for col, text in enumerate(self._texts(rec)):
            item = _NumItem(text) if col in NUMERIC_COLS else QTableWidgetItem(text)
            if col in RIGHT_ALIGNED_COLS:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._table.setItem(row, col, item)
        self._style_row(row, rec)

    def _update_row(self, row: int, rec: _Row) -> None:
        """Bestehende Zellen aendern. setItem auf ausgewaehlter Zeile ist sehr langsam."""
        for col, text in enumerate(self._texts(rec)):
            self._table.item(row, col).setText(text)
        self._style_row(row, rec)

    def _style_row(self, row: int, rec: _Row) -> None:
        brush = QBrush(STATUS_COLORS[rec.status])
        self._table.item(row, COL_INDEX).setData(Qt.UserRole, rec.index)
        self._table.item(row, COL_MEMBER).setData(Qt.UserRole, rec.members)
        for col in range(len(COLUMNS)):
            self._table.item(row, col).setBackground(brush)

    def _row_index(self, row: int) -> int | None:
        item = self._table.item(row, COL_INDEX)
        return item.data(Qt.UserRole) if item is not None else None

    def _row_status(self, row: int) -> str:
        item = self._table.item(row, COL_STATUS)
        return item.text() if item is not None else ""

    # ---------- Filter ----------

    def _apply_filter(self, *_args) -> None:
        text = self._search.text().strip().lower()
        status = self._status.currentData()
        only_minmax = self._only_minmax.isChecked()
        only_diff = self._only_diff.isChecked()
        visible = 0
        for row in range(self._table.rowCount()):
            rec = self._recs.get(self._row_index(row))
            show = (rec is not None
                    and (not text or text in rec.search)
                    and (status is None or rec.status == status)
                    and (not only_minmax or rec.has_minmax)
                    and (not only_diff or rec.type_diff))
            self._table.setRowHidden(row, not show)
            visible += int(show)
        self._count.setText(f"{visible} von {len(self._recs)} Indizes")

    # ---------- Navigation ----------

    def _candidates(self, cur: int, direction: int, wrap: bool):
        """Zeilen-Reihenfolge ab cur in Richtung direction (ohne cur selbst)."""
        n = self._table.rowCount()
        if cur < 0:
            # ohne Auswahl: vorwaerts bei 0 beginnen, rueckwaerts am Ende
            cur = -1 if direction > 0 else n
        for step in range(1, n + 1):
            row = cur + direction * step
            if wrap:
                row %= n
            elif not 0 <= row < n:
                break
            yield row

    def _go(self, direction: int, unknown_only: bool, wrap: bool) -> None:
        for row in self._candidates(self._table.currentRow(), direction, wrap):
            if self._table.isRowHidden(row):
                continue
            if unknown_only and self._row_status(row) != ST_UNK:
                continue
            self._table.setCurrentCell(row, 0)  # waehlt die ganze Zeile (SelectRows)
            self._table.scrollToItem(self._table.item(row, 0))
            return

    def _on_selection(self) -> None:
        if self._loading:
            return
        index = self.current_index()
        if index == self._last:
            return
        self._last = index
        if index is not None:
            self.indexSelected.emit(index)

    # ---------- Oeffentliche Methoden ----------

    def set_models(self, meas, ref, mkgit) -> None:
        self._meas = meas
        self._ref = ref
        self._mkgit = mkgit or {}
        self.reload()

    def reload(self) -> None:
        """Tabelle neu fuellen; aktuelle Auswahl nach Index wiederherstellen."""
        prev = self.current_index()
        self._loading = True
        try:
            self._recs = self._build_rows()
            table = self._table
            table.setSortingEnabled(False)
            table.setRowCount(0)
            table.setRowCount(len(self._recs))
            for row, rec in enumerate(self._recs.values()):
                self._set_row(row, rec)
            table.setSortingEnabled(True)
            table.sortByColumn(COL_INDEX, Qt.AscendingOrder)
            self._apply_filter()
            if prev is not None:
                self._select_row_for(prev)
        finally:
            self._loading = False
        new = self.current_index()
        if new != prev:
            self._last = new
            if new is not None:
                self.indexSelected.emit(new)
        self._last = new

    def _select_row_for(self, index: int) -> bool:
        for row in range(self._table.rowCount()):
            if self._row_index(row) == index:
                if self._table.isRowHidden(row):
                    return False
                self._table.setCurrentCell(row, 0)
                self._table.scrollToItem(self._table.item(row, 0))
                return True
        return False  # nicht gefunden (Zeilen-Schleife ohne Treffer)

    def select_index(self, index: int) -> bool:
        """Zeile zu index waehlen. False, wenn Index fehlt oder per Filter ausgeblendet."""
        return self._select_row_for(int(index))

    def current_index(self) -> int | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        return self._row_index(row)

    def visible_indices(self) -> list[int]:
        out = []
        for row in range(self._table.rowCount()):
            if not self._table.isRowHidden(row):
                index = self._row_index(row)
                if index is not None:
                    out.append(index)
        return out

    def refresh_status(self) -> None:
        """Status und Farben nach Aenderung der Referenz neu berechnen, Auswahl bleibt."""
        self._loading = True
        table = self._table
        table.setUpdatesEnabled(False)
        try:
            self._recs = self._build_rows()
            for row in range(table.rowCount()):
                rec = self._recs.get(self._row_index(row))
                if rec is not None:
                    self._update_row(row, rec)
            self._apply_filter()
        finally:
            table.setUpdatesEnabled(True)
            self._loading = False
