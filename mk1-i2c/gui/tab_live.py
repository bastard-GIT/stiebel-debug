"""Live-Tab: zeigt Rohframes mit Zeit, Bytes, ACK, Flags und LWZ-180-Dekodierung.

Daten kommen ueber on_frames() (vom Hauptfenster, im GUI-Thread). Gezeichnet werden die
letzten MAX_SHOW passenden Frames aus dem DataModel. Pause friert nur die Anzeige ein,
das Modell sammelt weiter.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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

MAX_SHOW = 1000
COLUMNS = ["Zeit", "Quelle", "Bytes (hex)", "ACK", "Flags", "Header", "CRC", "Typ / Name", "Wert"]
FLAG_NAMES = {0x01: "NOSTOP", 0x02: "BITERR", 0x04: "OVERLEN", 0x08: "RING_OVF", 0x10: "REPSTART"}


def flags_text(flags: int) -> str:
    """Flags als Namensliste, '-' wenn keine gesetzt sind."""
    names = [name for bit, name in FLAG_NAMES.items() if flags & bit]
    return ",".join(names) if names else "-"


def _time_text(row: dict) -> str:
    """Lokale Uhrzeit mit Millisekunden, sonst t in ms (wenn keine Unix-Zeit vorliegt)."""
    ts = row.get("ts")
    if ts is None:
        return f"t={int(row['t'])} ms"
    return datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S.%f")[:-3]


def _crc_text(res: dict | None) -> str:
    if res is None or res.get("crc_ok") is None:
        return ""
    return "ok" if res["crc_ok"] else "FALSCH"


def _name_text(res: dict | None) -> str:
    if res is None:
        return ""
    if res.get("kind") == "tasten" and res.get("keys"):
        return "Taste " + ",".join(str(k) for k in res["keys"])
    if res.get("name"):
        return res["name"]
    if res.get("ok") is False and res.get("error"):
        return res["error"]
    return res.get("kind") or ""


def _value_text(res: dict | None) -> str:
    if res is None or res.get("value") is None:
        return ""
    unit = res.get("unit") or ""
    return f"{res['value']} {unit}".strip()


def matches(row: dict, res: dict | None, header: str, text: str) -> bool:
    """Filter: header (ohne Gross/Klein, leer = alle) und Teilstring in Hex, Header oder Name."""
    if header:
        if res is None or str(res.get("header") or "").lower() != header:
            return False
    if text:
        parts = [bytes(row["data"]).hex(), str(res.get("header") or "") if res else ""]
        if res is not None and res.get("name"):
            parts.append(res["name"])
        if text not in " ".join(parts).lower():
            return False
    return True


def select_rows(model, header: str, text: str) -> list[tuple[dict, dict | None]]:
    """Die letzten MAX_SHOW passenden (Zeile, Dekodierergebnis) in Zeitreihenfolge."""
    out: list[tuple[dict, dict | None]] = []
    for row, res in zip(reversed(model.frames), reversed(model.decoded)):
        if matches(row, res, header, text):
            out.append((row, res))
            if len(out) >= MAX_SHOW:
                break
    out.reverse()
    return out


class LiveTab(QWidget):
    """Rohframe-Tabelle mit Filter, Pause und Autoscroll."""

    def __init__(self, model, bridge, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self._bridge = bridge
        self._paused = False

        self._header_edit = QLineEdit()
        self._header_edit.setPlaceholderText("Header, z.B. e3 20 10")
        self._text_edit = QLineEdit()
        self._text_edit.setPlaceholderText("Hex, Typ oder Name")
        self._pause_btn = QPushButton("Pause")
        self._autoscroll = QCheckBox("Autoscroll")
        self._autoscroll.setChecked(True)
        self._status = QLabel("")

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Header:"))
        filter_row.addWidget(self._header_edit, 1)
        filter_row.addWidget(QLabel("Text:"))
        filter_row.addWidget(self._text_edit, 2)
        filter_row.addWidget(self._pause_btn)
        filter_row.addWidget(self._autoscroll)

        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)

        layout = QVBoxLayout(self)
        layout.addLayout(filter_row)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._status)

        self._header_edit.textChanged.connect(self.refresh)
        self._text_edit.textChanged.connect(self.refresh)
        self._pause_btn.clicked.connect(self._toggle_pause)

        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)
        self._dirty = False
        self._timer.start()
        self.refresh()

    # ----- Oeffentliche Hooks -----

    def on_frames(self, rows: list[dict]) -> None:
        """Neue Frames sind schon im Modell. Anzeige im Takt aktualisieren (siehe _tick)."""
        self._dirty = True

    def refresh(self) -> None:
        """Tabelle aus dem Modell neu aufbauen (letzte MAX_SHOW passende Frames)."""
        self._dirty = False
        selected = select_rows(self._model, self._header_edit.text().strip().lower(),
                               self._text_edit.text().strip().lower())
        self._table.setRowCount(len(selected))
        for r, (row, res) in enumerate(selected):
            cells = [
                _time_text(row),
                str(row.get("ts_src") or ""),
                bytes(row["data"]).hex(" "),
                str(row.get("ack", "")),
                flags_text(int(row.get("flags", 0))),
                str((res or {}).get("header") or ""),
                _crc_text(res),
                _name_text(res),
                _value_text(res),
            ]
            for c, text in enumerate(cells):
                self._table.setItem(r, c, QTableWidgetItem(text))
        if self._autoscroll.isChecked() and not self._paused and selected:
            self._table.scrollToBottom()
        self._update_status(len(selected))

    # ----- Intern -----

    def _tick(self) -> None:
        if self._dirty and not self._paused:
            self.refresh()

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self._pause_btn.setText("Weiter" if self._paused else "Pause")
        if not self._paused:
            self.refresh()
        self._update_status()

    def _update_status(self, shown: int | None = None) -> None:
        state = "PAUSIERT" if self._paused else "laeuft"
        count = len(self._model.frames) if shown is None else shown
        self._status.setText(f"{state} | angezeigt: {count} | gesamt im Modell: {len(self._model.frames)}")
