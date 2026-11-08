"""Live-Tab: zeigt CAN-Frames vom ESP in Echtzeit (Elster-Dekodierung).

Abhaengigkeiten laut INTERFACES.md 5.1 / 5.2 / 5.3. Kein direkter Zugriff
von Hintergrund-Threads; alle Daten kommen ueber bridge.rx_frames (Qt-Signal).
"""

from __future__ import annotations

from collections import deque
from datetime import datetime

from PySide6.QtCore import (
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    QAbstractTableModel,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
    QHeaderView,
    QAbstractItemView,
)

from elster import ElsterDb, parse_frame, decode_value, switch_label
from mqtt_client import MqttBridge

MAX_ROWS = 50000
FLUSH_MS = 100
LATEST_REFRESH_TICKS = 5  # Filter-Neuberechnung im Modus "letzter Wert" alle 500 ms

COLUMNS = [
    "Zeit (ms)",
    "Uhrzeit",
    "Richtung",
    "CAN-ID",
    "Absender",
    "Empfaenger",
    "Nachrichtenart",
    "Index",
    "Switch",
    "Name",
    "Rohwert",
    "Wert",
]

MODE_ALL = "alle Frames"
MODE_LATEST = "letzter Wert pro Index"

SYSTEM_NAME = "SYSTEM_0xFE"
SYSTEM_TYPE_NAMES = {6: "system", 7: "system-resp"}

TIME_COLUMNS = (0, 1)        # "Zeit (ms)" und "Uhrzeit": sortieren nach ts (sonst t)
SORT_ROLE = int(Qt.UserRole) + 1
PC_HINT = "PC-Naehrung"      # ts_src == 'pc': Zeitstempel vom PC genaehert


class _Rec:
    """Eine Zeile: Rohdaten plus dekodierte Felder (einmal beim Einfuegen berechnet)."""

    __slots__ = (
        "seq", "t", "ts", "ts_src", "id", "data", "tx",
        "sender", "receiver", "msg_type", "msg_type_name",
        "index", "switch", "raw", "name", "value", "search",
    )


def _latest_key(rec: _Rec) -> tuple:
    """Schluessel fuer 'letzter Wert pro Index': (Sender-CAN-ID, Index, Switch)."""
    return (rec.id, rec.index, rec.switch)


def _hex_or_empty(v, fmt: str) -> str:
    return "" if v is None else fmt % v


def _time_text(rec: _Rec) -> str:
    """Spalte 'Zeit (ms)': Unix-Zeit mit 3 Nachkommastellen wenn ts, sonst t."""
    return str(rec.t) if rec.ts is None else "%.3f" % rec.ts


def _clock_text(rec: _Rec) -> str:
    """Spalte 'Uhrzeit': lokale Zeit HH:MM:SS.mmm, leer ohne ts; ' ~' bei ts_src 'pc'."""
    if rec.ts is None:
        return ""
    text = datetime.fromtimestamp(rec.ts).strftime("%H:%M:%S.%f")[:-3]
    return text + " ~" if rec.ts_src == "pc" else text


def _time_key(rec: _Rec) -> float:
    """Sortierschluessel der Zeitspalten: ts, sonst t."""
    return float(rec.t) if rec.ts is None else rec.ts


def _decode(db: ElsterDb, seq: int, frame: dict) -> _Rec:
    """Roh-Frame (dict mit t,id,data,tx) in _Rec mit Elster-Feldern umwandeln."""
    rec = _Rec()
    rec.seq = seq
    rec.t = int(frame["t"])
    ts = frame.get("ts")
    rec.ts = None if ts is None else float(ts)
    src = frame.get("ts_src")
    rec.ts_src = src if src in ("esp", "pc") else None
    rec.id = int(frame["id"])
    data = frame["data"]
    if isinstance(data, str):
        data = bytes.fromhex(data)
    rec.data = bytes(data)
    rec.tx = bool(frame.get("tx", False))

    parsed = parse_frame(rec.id, rec.data)
    if parsed is None:
        rec.sender = None
        rec.receiver = None
        rec.msg_type = None
        rec.msg_type_name = ""
        rec.index = None
        rec.switch = ""
        rec.raw = None
        rec.name = ""
        rec.value = ""
    else:
        rec.sender = parsed["sender"]
        rec.receiver = parsed["receiver"]
        rec.msg_type = parsed["msg_type"]
        rec.msg_type_name = parsed["msg_type_name"]
        rec.index = parsed["index"]
        rec.switch = switch_label(parsed.get("switch"))
        rec.raw = parsed["value"]
        if parsed.get("system"):
            # Systemnachricht (0xFE): kein Signal, Name fest, Typ Standard.
            rec.msg_type_name = SYSTEM_TYPE_NAMES.get(rec.msg_type, rec.msg_type_name)
            rec.name = SYSTEM_NAME
            sig = {}
        else:
            rec.name = db.signal_name(rec.index)
            sig = db.signal_by_index(rec.index) or {}
        try:
            # Bitfeld-Anzeige nur fuer aktuelle Werte (Min/Max-Antworten bleiben Zahlen)
            if parsed.get("system") or rec.switch:
                rec.value = decode_value(sig.get("type", "et_default"), rec.raw)
            else:
                rec.value = db.decode(rec.index, rec.raw, sig.get("type", "et_default"))
        except Exception:
            rec.value = ""

    sender_txt = db.member_name(rec.sender) if rec.sender is not None else ""
    rec.sender = sender_txt if sender_txt else ""
    recv_txt = db.member_name(rec.receiver) if rec.receiver is not None else ""
    rec.receiver = recv_txt
    # Suchtext fuer Textfilter (Index, Switch und Name)
    rec.search = (
        _hex_or_empty(rec.index, "0x%04x") + " " + rec.switch + " " + rec.name
    ).lower()
    return rec


class FrameTableModel(QAbstractTableModel):
    """Haelt bis MAX_ROWS Frames. Aelteste Zeilen werden verworfen."""

    def __init__(self, db: ElsterDb, parent=None):
        super().__init__(parent)
        self._db = db
        self._recs: deque[_Rec] = deque()
        self._seq = 0
        # (Sender, Index, Switch) -> seq des juengsten Frames (fuer "letzter Wert")
        self._latest: dict[tuple, int] = {}

    # --- Qt-Modell-API ---
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._recs)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        rec = self._recs[index.row()]
        c = index.column()
        if role == SORT_ROLE:
            return _time_key(rec) if c in TIME_COLUMNS else None
        if role == Qt.ToolTipRole:
            if c in TIME_COLUMNS and rec.ts_src == "pc":
                return PC_HINT
            return None
        if role != Qt.DisplayRole:
            return None
        if c == 0:
            return _time_text(rec)
        if c == 1:
            return _clock_text(rec)
        if c == 2:
            return "TX" if rec.tx else "RX"
        if c == 3:
            return "0x%03x" % rec.id
        if c == 4:
            return rec.sender
        if c == 5:
            return rec.receiver
        if c == 6:
            return rec.msg_type_name
        if c == 7:
            return _hex_or_empty(rec.index, "0x%04x")
        if c == 8:
            return rec.switch
        if c == 9:
            return rec.name
        if c == 10:
            return _hex_or_empty(rec.raw, "0x%04x")
        if c == 11:
            return rec.value
        return None

    # --- Eigene API ---
    def record(self, row: int) -> _Rec:
        return self._recs[row]

    def is_latest(self, rec: _Rec) -> bool:
        return (
            rec.index is not None
            and rec.msg_type == 2
            and self._latest.get(_latest_key(rec)) == rec.seq
        )

    def append_frames(self, frames: list[dict]) -> None:
        if not frames:
            return
        recs = []
        for f in frames:
            self._seq += 1
            recs.append(_decode(self._db, self._seq, f))
        start = len(self._recs)
        self.beginInsertRows(QModelIndex(), start, start + len(recs) - 1)
        for rec in recs:
            self._recs.append(rec)
            if rec.index is not None and rec.msg_type == 2:
                self._latest[_latest_key(rec)] = rec.seq
        self.endInsertRows()
        self._trim()

    def _trim(self) -> None:
        excess = len(self._recs) - MAX_ROWS
        if excess > 0:
            self.beginRemoveRows(QModelIndex(), 0, excess - 1)
            for _ in range(excess):
                self._recs.popleft()
            self.endRemoveRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._recs.clear()
        self._latest.clear()
        self.endResetModel()

    def load_rows(self, rows: list[dict]) -> None:
        """Ersetzt den Inhalt durch rows (Format {"t","ts","ts_src","id","data","tx"}; ts optional)."""
        self.clear()
        self._seq = 0
        self.append_frames(list(rows))

    def rows(self) -> list[dict]:
        """Alle gespeicherten Frames als {"t","ts","ts_src","id","data","tx"} fuer Export."""
        return [
            {"t": r.t, "ts": r.ts, "ts_src": r.ts_src,
             "id": r.id, "data": r.data, "tx": r.tx}
            for r in self._recs
        ]


class _FilterProxy(QSortFilterProxyModel):
    """Filter nach CAN-ID, Nachrichtenart, Text und 'letzter Wert pro Index'."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ids: set[int] = set()        # leer = alle
        self.msg_type = None              # None = alle
        self.text = ""                    # lowercase
        self.latest_only = False

    def set_filters(self, ids, msg_type, text, latest_only) -> None:
        self.ids = set(ids)
        self.msg_type = msg_type
        self.text = text.strip().lower()
        self.latest_only = latest_only
        self.invalidateFilter()

    def lessThan(self, left, right) -> bool:
        # Zeitspalten numerisch nach ts (sonst t) sortieren, nicht als Text.
        if left.column() in TIME_COLUMNS and right.column() in TIME_COLUMNS:
            model = self.sourceModel()
            return model.data(left, SORT_ROLE) < model.data(right, SORT_ROLE)
        return super().lessThan(left, right)

    def filterAcceptsRow(self, source_row, source_parent) -> bool:
        model = self.sourceModel()
        rec = model.record(source_row)
        if self.latest_only and not model.is_latest(rec):
            return False
        if self.ids and rec.id not in self.ids:
            return False
        if self.msg_type is not None and rec.msg_type != self.msg_type:
            return False
        if self.text and self.text not in rec.search:
            return False
        return True


def _parse_ids(text: str) -> tuple[list[int], str | None]:
    """'0x180, 0x500' -> ([0x180, 0x500], None). Bei Fehler ([], Fehlertext)."""
    ids: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part, 16))
        except ValueError:
            return [], f"Ungueltige CAN-ID: '{part}'"
    return ids, None


class LiveTab(QWidget):
    def __init__(self, db: ElsterDb, bridge: MqttBridge, parent=None):
        super().__init__(parent)
        self._db = db
        self._bridge = bridge
        self._paused = False
        self._pending: deque[dict] = deque()   # gesammelte Frames, noch nicht im Modell
        self._latest_dirty = False
        self._tick = 0

        self._model = FrameTableModel(db, self)
        self._proxy = _FilterProxy(self)
        self._proxy.setSourceModel(self._model)

        # --- Filterzeile ---
        self._id_edit = QLineEdit()
        self._id_edit.setPlaceholderText("CAN-IDs hex, z.B. 0x180, 0x500")
        self._type_combo = QComboBox()
        self._type_combo.addItem("alle Arten", None)
        for code, name in self._msg_type_choices():
            self._type_combo.addItem(name, code)
        self._text_edit = QLineEdit()
        self._text_edit.setPlaceholderText("Index, Switch oder Name")
        self._view_combo = QComboBox()
        self._view_combo.addItem(MODE_ALL)
        self._view_combo.addItem(MODE_LATEST)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("CAN-ID:"))
        filter_row.addWidget(self._id_edit, 2)
        filter_row.addWidget(QLabel("Art:"))
        filter_row.addWidget(self._type_combo)
        filter_row.addWidget(QLabel("Text:"))
        filter_row.addWidget(self._text_edit, 2)
        filter_row.addWidget(self._view_combo)

        # --- Steuerzeile ---
        self._pause_btn = QPushButton("Pause")
        self._clear_btn = QPushButton("Loeschen")
        self._autoscroll = QCheckBox("Autoscroll")
        self._autoscroll.setChecked(True)
        self._sniff_check = QCheckBox("Sniffer an")
        self._sniff_btn = QPushButton("Sniffer-Filter an ESP senden")
        self._status = QLabel("")

        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(self._pause_btn)
        ctrl_row.addWidget(self._clear_btn)
        ctrl_row.addWidget(self._autoscroll)
        ctrl_row.addStretch(1)
        ctrl_row.addWidget(self._sniff_check)
        ctrl_row.addWidget(self._sniff_btn)

        # --- Tabelle ---
        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)

        layout = QVBoxLayout(self)
        layout.addLayout(filter_row)
        layout.addLayout(ctrl_row)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._status)

        # --- Verbindungen ---
        self._bridge.rx_frames.connect(self._on_rx_frames)
        self._pause_btn.clicked.connect(self._toggle_pause)
        self._clear_btn.clicked.connect(self._on_clear)
        self._sniff_btn.clicked.connect(self._send_filter)
        self._sniff_check.toggled.connect(self._on_sniff_toggled)
        self._id_edit.textChanged.connect(self._apply_filters)
        self._text_edit.textChanged.connect(self._apply_filters)
        self._type_combo.currentIndexChanged.connect(self._apply_filters)
        self._view_combo.currentIndexChanged.connect(self._apply_filters)

        # --- Batch-Timer ---
        self._timer = QTimer(self)
        self._timer.setInterval(FLUSH_MS)
        self._timer.timeout.connect(self._flush)
        self._timer.start()

        self._update_status()

    # ------------------------------------------------------------------
    @staticmethod
    def _msg_type_choices() -> list[tuple[int, str]]:
        # Nachrichtenart = data[0] & 0x0F, Namen laut ElsterTable.json frame_format.message_type.
        return [
            (0, "write"),
            (1, "read"),
            (2, "response"),
            (3, "ack"),
            (4, "write ack"),
            (5, "write respond"),
            (6, "system"),
            (7, "system respond"),
        ]

    def _update_status(self, msg: str = "") -> None:
        state = "PAUSIERT" if self._paused else "laeuft"
        text = f"{state} | Zeilen: {self._model.rowCount()} / {MAX_ROWS} | Puffer: {len(self._pending)}"
        if msg:
            text += " | " + msg
        self._status.setText(text)

    # --- Frames -------------------------------------------------------
    def _on_rx_frames(self, frames: list) -> None:
        # Nur sammeln; eingefuegt wird im 100-ms-Takt in _flush().
        self._pending.extend(frames)
        while len(self._pending) > MAX_ROWS:
            self._pending.popleft()

    def _flush(self) -> None:
        self._tick += 1
        if self._paused:
            self._update_status()
            return
        if self._pending:
            batch = list(self._pending)
            self._pending.clear()
            self._model.append_frames(batch)
            self._latest_dirty = True
            if self._autoscroll.isChecked():
                self._table.scrollToBottom()
        # Im Modus "letzter Wert" ist die Filterung zeitabhaengig: gelegentlich neu rechnen.
        if (
            self._proxy.latest_only
            and self._latest_dirty
            and self._tick % LATEST_REFRESH_TICKS == 0
        ):
            self._proxy.invalidateFilter()
            self._latest_dirty = False
        self._update_status()

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self._pause_btn.setText("Weiter" if self._paused else "Pause")
        if not self._paused:
            self._flush()
        self._update_status()

    def _on_clear(self) -> None:
        self._pending.clear()
        self._model.clear()
        self._latest_dirty = False
        self._update_status()

    # --- Filter -------------------------------------------------------
    def _current_filters(self):
        ids, err = _parse_ids(self._id_edit.text())
        return ids, err

    def _apply_filters(self, *_args) -> None:
        ids, err = self._current_filters()
        if err:
            self._update_status(err)
            ids = []  # ungueltige Eingabe: ignorieren statt leere Tabelle
        self._proxy.set_filters(
            ids,
            self._type_combo.currentData(),
            self._text_edit.text(),
            self._view_combo.currentText() == MODE_LATEST,
        )
        self._update_status()

    # --- Sniffer ------------------------------------------------------
    def _filter_lists(self) -> tuple[list[int], list[int]]:
        ids, err = self._current_filters()
        if err:
            self._update_status(err)
            ids = []
        t = self._type_combo.currentData()
        types = [] if t is None else [int(t)]
        return ids, types

    def _send_filter(self) -> None:
        ids, types = self._filter_lists()
        self._bridge.send_sniff(self._sniff_check.isChecked(), ids, types)
        self._update_status("Sniffer-Filter gesendet")

    def _on_sniff_toggled(self, checked: bool) -> None:
        ids, types = self._filter_lists()
        self._bridge.send_sniff(checked, ids, types)
        self._update_status("Sniffer " + ("an" if checked else "aus"))

    # --- Export / Offline-Log ----------------------------------------
    def load_rows(self, rows: list[dict]) -> None:
        self._pending.clear()
        self._model.load_rows(rows)
        self._latest_dirty = True
        self._update_status("Offline-Log geladen")

    def rows(self) -> list[dict]:
        return self._model.rows()
