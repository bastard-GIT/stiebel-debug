"""Analyse-Tab: Member-Vergleich und Typwahl fuer einen Index.

decode_raw ist kein Qt und testbar ohne GUI.
"""
from __future__ import annotations

import html
from collections import Counter

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from data_model import Cell, Measurements
from elster import decode_bits, decode_value
from reference_model import ReferenceError, ReferenceModel
from type_guess import guess_types

PSEUDO_TYPES = ("int16", "uint16", "hex", "bits")
DEFAULT_TYPE = "et_default"
COLOR_DIFF = "#c62828"      # Abweichung Referenz vs. mk-git (rot)
COLOR_ANDERS = "#ffe0b2"    # Zeile mit abweichendem Rohwert (orange)
COLOR_BIT = "#c8e6c9"       # gesetztes Bit im Bits-Reiter (gruen)
STATUS_TEXT = {"ok": "ok", "unsupported": "nicht unterstuetzt"}
MEMBER_HEADERS = ["Member", "Antwortzeit ms", "Status", "Rohwert", "Dekodiert",
                  "Min dekodiert", "Max dekodiert", "Verschiedene Rohwerte", "Abweichung"]
BITS_HEADERS = ["Bit", "Name", "bestaetigt"]


def _swap16(raw: int) -> int:
    return ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)


def decode_raw(raw: int, typ: str, swap: bool = False,
               bits: list[dict] | None = None) -> str:
    """Rohwert nach Typ als Text. swap: hi/lo vor der Dekodierung tauschen.
    bits: Bitnamen der Referenz fuer Pseudotyp 'bits'."""
    raw &= 0xFFFF
    if swap:
        raw = _swap16(raw)
    if typ == "int16":
        return str(raw - 0x10000 if raw & 0x8000 else raw)
    if typ == "uint16":
        return str(raw)
    if typ == "hex":
        return f"0x{raw:04x}"
    if typ == "bits":
        if bits:
            return decode_bits(raw, bits)
        return ",".join(f"b{i}" for i in range(16) if (raw >> i) & 1)
    return decode_value(typ, raw)


def pick_default_type(ref_type: str | None, mk_type: str | None,
                      guess_best: str | None, types: list[str]) -> str:
    """Standardtyp: Referenz -> mk-git (ohne et_default) -> beste Vermutung -> et_default."""
    candidates = (ref_type, None if mk_type == DEFAULT_TYPE else mk_type, guess_best)
    for cand in candidates:
        if cand and cand in types:
            return cand
    if DEFAULT_TYPE in types:
        return DEFAULT_TYPE
    return types[0] if types else DEFAULT_TYPE


def comment_text(cell: Cell | None, latency_med: float | None, typ: str, swap: bool,
                 bits: list[dict] | None = None) -> str:
    """Vorbelegung Kommentar: 'Min x / Max y, Antwortzeit z ms' (nur vorhandene Teile)."""
    parts = []
    if cell is not None and cell.min_raw is not None and cell.max_raw is not None:
        parts.append(f"Min {decode_raw(cell.min_raw, typ, swap, bits)} / "
                     f"Max {decode_raw(cell.max_raw, typ, swap, bits)}")
    if latency_med is not None:
        parts.append(f"Antwortzeit {latency_med:.1f} ms")
    return ", ".join(parts)


def _common_raw(cells: dict[int, Cell]) -> int | None:
    """Haeufigster letzter Rohwert aller ok-Member."""
    raws = [c.raw for c in cells.values() if c.status == "ok" and c.raw is not None]
    return Counter(raws).most_common(1)[0][0] if raws else None


def _member_label(ref: ReferenceModel, member: int) -> str:
    return f"0x{member:03x} {ref.member_name(member)}".rstrip()


def _readonly(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    return item


def _span(text: str, bad: bool) -> str:
    text = html.escape(text)
    return f'<span style="color:{COLOR_DIFF}; font-weight:bold">{text}</span>' if bad else text


class AnalysePanel(QWidget):
    referenceChanged = Signal()

    def __init__(self, meas: Measurements, ref: ReferenceModel, mkgit: dict,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._meas = meas
        self._ref = ref
        self._mkgit: dict = mkgit or {}
        self._index: int | None = None
        self._build_ui()
        self.set_models(meas, ref, mkgit)

    # ---- Aufbau ----
    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        self._lbl_head = QLabel()
        self._lbl_head.setTextFormat(Qt.RichText)
        self._lbl_head.setWordWrap(True)
        lay.addWidget(self._lbl_head)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Typ"))
        self._cmb_typ = QComboBox()
        bar.addWidget(self._cmb_typ, 1)
        self._chk_swap = QCheckBox("Bytes tauschen")
        bar.addWidget(self._chk_swap)
        bar.addStretch(1)
        self._btn_transfer = QPushButton("In Referenz uebertragen")
        bar.addWidget(self._btn_transfer)
        lay.addLayout(bar)

        self._tbl_member = self._make_table(MEMBER_HEADERS)
        self._tbl_types = self._make_table(["Typ"])
        self._tbl_bits = self._make_table(BITS_HEADERS)
        self._tbl_bits.setEditTriggers(QTableWidget.AllEditTriggers)
        self._btn_bits = QPushButton("Bitnamen in Referenz uebernehmen")
        bits_page = QWidget()
        bits_lay = QVBoxLayout(bits_page)
        bits_lay.addWidget(self._tbl_bits, 1)
        bits_lay.addWidget(self._btn_bits)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._tbl_member, "Member-Vergleich")
        self._tabs.addTab(self._tbl_types, "Alle Typen")
        self._tabs.addTab(bits_page, "Bits")
        lay.addWidget(self._tabs, 1)

        self._cmb_typ.currentIndexChanged.connect(lambda _i=0: self._refresh_tables())
        self._chk_swap.toggled.connect(lambda _b=False: self._refresh_tables())
        self._btn_transfer.clicked.connect(self._open_transfer)
        self._btn_bits.clicked.connect(self._on_apply_bits)

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        tbl = QTableWidget(0, len(headers))
        tbl.setHorizontalHeaderLabels(headers)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        return tbl

    def _fill_type_combo(self) -> None:
        types = list(self._ref.types) + [p for p in PSEUDO_TYPES if p not in self._ref.types]
        current = self._cmb_typ.currentText()
        self._cmb_typ.blockSignals(True)
        self._cmb_typ.clear()
        self._cmb_typ.addItems(types)
        if current in types:
            self._cmb_typ.setCurrentText(current)
        self._cmb_typ.blockSignals(False)

    # ---- Oeffentlich ----
    def set_models(self, meas: Measurements, ref: ReferenceModel, mkgit: dict) -> None:
        self._meas = meas
        self._ref = ref
        self._mkgit = mkgit or {}
        self._fill_type_combo()
        self._refresh()

    def set_index(self, index: int | None) -> None:
        self._index = index
        if index is not None:
            self._apply_default_type(index)
        self._refresh()

    # ---- Daten ----
    def _minmax_for(self, index: int) -> tuple[int, int] | None:
        for m in sorted(self._meas.cells(index)):
            c = self._meas.cells(index)[m]
            if c.min_raw is not None and c.max_raw is not None:
                return (c.min_raw, c.max_raw)
        return None

    def _apply_default_type(self, index: int) -> None:
        sig = self._ref.signal(index)
        ref_type = sig["type"] if sig else None
        mk_entry = self._mkgit.get(index)
        mk_type = mk_entry[1] if mk_entry else None
        guess_best = None
        raws = self._meas.all_raws(index)
        if raws:
            guesses = guess_types(raws, self._minmax_for(index))
            guess_best = guesses[0][0] if guesses else None
        types = [self._cmb_typ.itemText(i) for i in range(self._cmb_typ.count())]
        choice = "bits" if self._bits_list(index) else \
            pick_default_type(ref_type, mk_type, guess_best, types)
        self._cmb_typ.blockSignals(True)
        self._cmb_typ.setCurrentText(choice)
        self._cmb_typ.blockSignals(False)

    def _owner_candidates(self, index: int) -> list[int]:
        cells = self._meas.cells(index)
        return [m for m in sorted(cells) if cells[m].status == "ok"]

    def _default_owner(self, index: int) -> int | None:
        cells = self._meas.cells(index)
        cands = self._owner_candidates(index)
        if not cands:
            return None
        common = _common_raw(cells)
        for m in cands:
            if cells[m].raw == common:
                return m
        return cands[0]

    # ---- Anzeige ----
    def _refresh(self) -> None:
        self._lbl_head.setText(self._header_html())
        self._btn_transfer.setEnabled(self._index is not None)
        self._btn_bits.setEnabled(self._index is not None)
        self._refresh_tables()
        self._fill_bits_table()

    def _header_html(self) -> str:
        idx = self._index
        if idx is None:
            return "Kein Index gewaehlt"
        sig = self._ref.signal(idx)
        mk = self._mkgit.get(idx)
        ref_name = sig["name"] if sig else ""
        ref_type = sig["type"] if sig else ""
        mk_name, mk_type = mk if mk else ("", "")
        name_bad = bool(sig and mk) and ref_name != mk_name
        type_bad = bool(sig and mk) and ref_type != mk_type

        lines = [f"<b>Index 0x{idx:04x}</b>"]
        if sig:
            owner = sig.get("owner")
            owner_name = sig.get("owner_name") or (
                self._ref.member_name(int(owner, 16)) if owner else "")
            owner_txt = f"{owner} {owner_name}".strip() if owner else "-"
            lines.append(
                "Referenz: " + _span(ref_name, name_bad) + " | Typ "
                + _span(ref_type, type_bad) + " | Owner " + html.escape(owner_txt)
                + " | " + html.escape(sig.get("comment") or "")
            )
        else:
            lines.append("Referenz: nicht in Referenz")
        if mk:
            lines.append("mk-git: " + _span(mk_name, name_bad) + " | Typ "
                         + _span(mk_type, type_bad))
        else:
            lines.append("mk-git: nicht in mk-git")
        return "<br>".join(lines)

    def _refresh_tables(self) -> None:
        self._fill_member_table()
        self._fill_type_table()

    def _fill_member_table(self) -> None:
        tbl = self._tbl_member
        tbl.setRowCount(0)
        idx = self._index
        if idx is None:
            return
        cells = self._meas.cells(idx)
        typ = self._cmb_typ.currentText()
        swap = self._chk_swap.isChecked()
        bits = self._bits_list(idx)
        common = _common_raw(cells)
        rows = [(m, cells[m]) for m in self._meas.members if m in cells]
        tbl.setRowCount(len(rows))
        for r, (m, c) in enumerate(rows):
            med = self._meas.latency.get(m, {}).get("med")
            raw = c.raw if c.status == "ok" else None
            anders = raw is not None and common is not None and raw != common
            if c.min_raw is not None:
                min_txt = decode_raw(c.min_raw, typ, swap, bits)
            else:
                min_txt = c.mm_text
            max_txt = decode_raw(c.max_raw, typ, swap, bits) if c.max_raw is not None else ""
            values = [
                _member_label(self._ref, m),
                f"{med:.1f}" if med is not None else "",
                STATUS_TEXT.get(c.status, c.status),
                f"0x{raw:04x}" if raw is not None else "",
                decode_raw(raw, typ, swap, bits) if raw is not None else "",
                min_txt,
                max_txt,
                str(len(set(c.raws))),
                "anders" if anders else "",
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if anders:
                    item.setBackground(QColor(COLOR_ANDERS))
                tbl.setItem(r, col, item)

    def _fill_type_table(self) -> None:
        tbl = self._tbl_types
        tbl.setRowCount(0)
        tbl.setColumnCount(1)
        idx = self._index
        if idx is None:
            tbl.setHorizontalHeaderLabels(["Typ"])
            return
        cells = self._meas.cells(idx)
        members = self._owner_candidates(idx)
        swap = self._chk_swap.isChecked()
        bits = self._bits_list(idx)
        cur = self._cmb_typ.currentText()
        types = [self._cmb_typ.itemText(i) for i in range(self._cmb_typ.count())]

        tbl.setColumnCount(1 + len(members))
        tbl.setHorizontalHeaderLabels(["Typ"] + [_member_label(self._ref, m) for m in members])
        tbl.setRowCount(len(types))
        for r, typ in enumerate(types):
            label = QTableWidgetItem(typ)
            for col, m in enumerate(members, start=1):
                raw = cells[m].raw
                text = decode_raw(raw, typ, swap, bits) if raw is not None else ""
                tbl.setItem(r, col, QTableWidgetItem(text))
            tbl.setItem(r, 0, label)
            if typ == cur:
                for col in range(tbl.columnCount()):
                    item = tbl.item(r, col)
                    if item is not None:
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)

    def _bits_list(self, index: int | None) -> list[dict]:
        if index is None:
            return []
        return list(self._ref.bits(index) or [])

    # ---- Bits ----
    def _fill_bits_table(self) -> None:
        tbl = self._tbl_bits
        tbl.setRowCount(0)
        idx = self._index
        members = self._owner_candidates(idx) if idx is not None else []
        headers = BITS_HEADERS + [_member_label(self._ref, m) for m in members] + ["wechselt"]
        tbl.setColumnCount(len(headers))
        tbl.setHorizontalHeaderLabels(headers)
        if idx is None:
            return
        cells = self._meas.cells(idx)
        names = {int(b["bit"]): b for b in self._bits_list(idx)}
        seen = {r for c in cells.values() for r in (*c.raws, c.raw) if r is not None}
        tbl.setRowCount(16)
        for bit in range(16):
            info = names.get(bit, {})
            ok_item = QTableWidgetItem()
            ok_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            ok_item.setCheckState(Qt.CheckState.Checked if info.get("confirmed")
                                  else Qt.CheckState.Unchecked)
            tbl.setItem(bit, 0, _readonly(str(bit)))
            tbl.setItem(bit, 1, QTableWidgetItem(info.get("name") or ""))
            tbl.setItem(bit, 2, ok_item)
            for col, m in enumerate(members, start=3):
                raw = cells[m].raw
                on = raw is not None and (raw >> bit) & 1
                item = _readonly("" if raw is None else str((raw >> bit) & 1))
                if on:
                    item.setBackground(QColor(COLOR_BIT))
                tbl.setItem(bit, col, item)
            changed = {(r >> bit) & 1 for r in seen} == {0, 1}
            tbl.setItem(bit, len(headers) - 1, _readonly("ja" if changed else "nein"))

    def _collect_bits(self) -> list[dict]:
        tbl = self._tbl_bits
        out = []
        for bit in range(tbl.rowCount()):
            name_item = tbl.item(bit, 1)
            name = name_item.text().strip() if name_item else ""
            if not name:
                continue
            ok_item = tbl.item(bit, 2)
            out.append({"bit": bit, "name": name,
                        "confirmed": bool(ok_item and ok_item.checkState() == Qt.CheckState.Checked)})
        return out

    def _on_apply_bits(self) -> None:
        bits = self._collect_bits()
        if not bits:
            QMessageBox.information(self, "Bitnamen", "Keine Bitnamen eingetragen.")
            return
        try:
            self._apply_bits(bits)
        except ReferenceError as exc:
            QMessageBox.warning(self, "Bitnamen uebernehmen", str(exc))

    def _apply_bits(self, bits: list[dict]) -> None:
        """Schreibt Bitnamen in die Referenz. Owner: Member mit meisten ok-Antworten. Wirft ReferenceError."""
        if self._index is None:
            raise ReferenceError("Kein Index gewaehlt")
        cells = self._meas.cells(self._index)
        cands = self._owner_candidates(self._index)
        owner = max(cands, key=lambda m: len(cells[m].raws)) if cands else None
        self._ref.set_bits(self._index, bits, owner=owner)
        self.referenceChanged.emit()
        self._refresh()

    # ---- Uebertragen ----
    def _open_transfer(self) -> None:
        idx = self._index
        if idx is None:
            return
        sig = self._ref.signal(idx)
        mk_entry = self._mkgit.get(idx)
        if sig:
            name = sig["name"]
        elif mk_entry:
            name = mk_entry[0]
        else:
            name = f"UNK_0x{idx:04X}"
        typ_sel = self._cmb_typ.currentText()
        swap = self._chk_swap.isChecked()
        cells = self._meas.cells(idx)
        owner_default = self._default_owner(idx)

        dlg = QDialog(self)
        dlg.setWindowTitle(f"In Referenz uebertragen: 0x{idx:04x}")
        form = QFormLayout(dlg)

        ed_name = QLineEdit(name)
        form.addRow("Name", ed_name)

        cmb_typ = QComboBox()
        cmb_typ.addItems(list(self._ref.types))
        pick = typ_sel if typ_sel in self._ref.types else DEFAULT_TYPE
        if pick in self._ref.types:
            cmb_typ.setCurrentText(pick)
        form.addRow("Typ", cmb_typ)

        cmb_owner = QComboBox()
        for m in self._owner_candidates(idx):
            cmb_owner.addItem(_member_label(self._ref, m), m)
        if owner_default is not None:
            cmb_owner.setCurrentIndex(cmb_owner.findData(owner_default))
        form.addRow("Owner", cmb_owner)

        med = self._meas.latency.get(owner_default, {}).get("med") \
            if owner_default is not None else None
        comment = comment_text(cells.get(owner_default) if owner_default is not None else None,
                               med, typ_sel, swap, self._bits_list(idx))
        ed_comment = QLineEdit(comment)
        form.addRow("Kommentar", ed_comment)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        form.addRow(buttons)

        def on_ok() -> None:
            owner = cmb_owner.currentData()
            try:
                self._apply_transfer(ed_name.text(), cmb_typ.currentText(),
                                     int(owner) if owner is not None else None,
                                     ed_comment.text())
            except ReferenceError as exc:
                QMessageBox.warning(dlg, "In Referenz uebertragen", str(exc))
                return
            dlg.accept()

        buttons.accepted.connect(on_ok)
        buttons.rejected.connect(dlg.reject)
        dlg.exec()

    def _apply_transfer(self, name: str, typ: str, owner: int | None, kommentar: str) -> dict:
        """Schreibt in die Referenz-Arbeitskopie. Wirft ReferenceError bei Fehler."""
        if self._index is None:
            raise ReferenceError("Kein Index gewaehlt")
        sig = self._ref.upsert_signal(self._index, name, typ, owner, kommentar)
        self.referenceChanged.emit()
        self._refresh()
        return sig
