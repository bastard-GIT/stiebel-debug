"""Scanner-Tab: Index-Bereich eines CAN-Members per MQTT abfragen.

Debug-Firmware: Bereichs-Scan mit Format, Switch und Pause (cmd/scan).
Produktiv-Firmware: Bereichs-Scan mit Switch (heatingpump/scan/start, Takt fest 150 ms)
und Teilnehmer-Suche (heatingpump/scan/members).
"""

import csv

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import prod_api
from elster import ElsterDb, decode_value, switch_label
from export_io import save_scan_csv
from mqtt_client import FW_DEBUG, FW_PROD, FW_UNKNOWN, MqttBridge

FW_LOCK_TEXT = {
    FW_UNKNOWN: "Warte auf Status der Firmware...",
}
MAX_RANGE = 4096
MIN_DELAY_MS = 100
DEFAULT_DELAY_MS = 200
COLUMNS = ["Index", "Switch", "Name", "Rohwert", "Status", "Dekodiert"]
RIGHT_ALIGNED_COLS = (0, 3)  # Index, Rohwert
DEFAULT_SWITCH = 0xFA

# Produktiv: Takt fest, Pause entfaellt (send_scan ignoriert delay_ms)
PROD_MS_PER_ID = 150
PROD_FORMAT_TEXT = ("Im Produktiv-Modus nicht verfuegbar: Format kurz/lang gibt es nur bei "
                    "Debug-Firmware, Auswahl ist auto.")
PROD_DELAY_TEXT = ("Im Produktiv-Modus nicht verfuegbar: der Takt ist fest 150 ms, "
                   "eine Pause entfaellt.")
PROD_MEMBER_TEXT = "Nur mit Produktiv-Firmware (scan/members)"

# Teilnehmer-Suche (Produktiv)
MEMBER_ID_MAX = 0x7FF
MEMBER_DEFAULT_FROM = "0x000"
MEMBER_DEFAULT_TO = "0x7ff"
MEMBER_DEFAULT_INDEX = "0x000b"
MEMBER_COLUMNS = ["ID", "Name", "Index", "Rohwert hex", "Rohwert dez"]
MEMBER_RIGHT_ALIGNED_COLS = (3, 4)
MEMBER_CSV_HEADER = ["id", "name", "index", "raw"]
RAW_NOT_SUPPORTED = 0x8000  # Antwort "nicht unterstuetzt"

# Anzeige -> Parameter fuer send_scan (INTERFACES 8.3)
FORMAT_CHOICES = (("auto", "auto"), ("kurz", "short"), ("lang", "long"))
SWITCH_ALL = "alle"  # drei Scans nacheinander, keine Firmware-Option
SWITCH_CHOICES = (("aktuell", 0xFA), ("min", 0xF8), ("max", 0xF9), ("alle", SWITCH_ALL))
SEQ_SWITCHES = (0xFA, 0xF8, 0xF9)  # Reihenfolge bei 'alle'


def parse_hex(text: str) -> int | None:
    """Hex ("0x..."), dezimal oder None bei ungueltiger Eingabe; nur 0..0xFFFF."""
    s = text.strip()
    if not s:
        return None
    try:
        value = int(s, 16) if s.lower().startswith("0x") else int(s, 10)
    except ValueError:
        return None
    if not 0 <= value <= 0xFFFF:
        return None
    return value


def parse_member_id(text: str) -> int | None:
    """CAN-ID (Hex/dezimal) fuer Scan-Start: 0..0x7FF ohne PC-ID 0x680, sonst None."""
    value = parse_hex(text)
    if value is None or value > MEMBER_ID_MAX or value == prod_api.PC_CAN_ID:
        return None
    return value


def save_member_csv(path: str, rows: list[list]) -> None:
    """Teilnehmer-Suche als CSV: Semikolon, UTF-8 mit BOM. Spalten id, name, index, raw (dezimal)."""
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";", lineterminator="\r\n")
        writer.writerow(MEMBER_CSV_HEADER)
        writer.writerows(rows)


class ScannerTab(QWidget):
    def __init__(self, db: ElsterDb, bridge: MqttBridge):
        super().__init__()
        self._db = db
        self._bridge = bridge
        # Trefferliste je (index, switch); index/raw/switch als int, status als str.
        self._items: dict[tuple[int, int], dict] = {}
        self._scan_fmt = "auto"  # Format des laufenden Scans, Fallback fuer Treffer ohne format
        self._scanning = False
        # Sequenz 'alle': member, start, end, delay, fmt, count, queue (noch nicht gestartete Switches),
        # current, completed (abgeschlossene Scans). None bei Einzelscan.
        self._seq: dict | None = None
        self._fw = FW_UNKNOWN
        # Teilnehmer-Suche (Produktiv): laufend, gefundene Antworten {"id","index","raw"} als int
        self._member_scanning = False
        self._member_items: list[dict] = []

        self._build_ui()
        self._populate_members()
        self._update_estimate()
        self._update_member_estimate()
        self._apply_enabled()

        bridge.scan_progress.connect(self._on_progress)
        bridge.scan_result.connect(self._on_result)
        bridge.firmware_kind.connect(self._on_firmware)
        # Bridge-Fakes aelterer Tests haben kein member_result-Signal; mit MqttBridge immer vorhanden.
        member_signal = getattr(bridge, "member_result", None)
        if member_signal is not None:
            member_signal.connect(self._on_member_result)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        self._member = QComboBox()
        self._member_hex = QLineEdit()
        self._member_hex.setPlaceholderText("z. B. 0x7ff")
        self._member_hex.setToolTip("Optional: Hex-ID statt Auswahl oben (0x000-0x7FF, ohne 0x680).")
        self._from = QLineEdit("0x4e00")
        self._to = QLineEdit("0x4fff")
        self._format = QComboBox()
        for label, value in FORMAT_CHOICES:
            self._format.addItem(label, value)
        self._switch = QComboBox()
        for label, value in SWITCH_CHOICES:
            self._switch.addItem(label, value)
        self._delay = QSpinBox()
        self._delay.setRange(MIN_DELAY_MS, 10000)
        self._delay.setValue(DEFAULT_DELAY_MS)
        self._delay.setSuffix(" ms")

        form = QFormLayout()
        form.addRow("Member:", self._member)
        form.addRow("oder Hex-ID:", self._member_hex)
        form.addRow("Von (Hex):", self._from)
        form.addRow("Bis (Hex):", self._to)
        form.addRow("Format:", self._format)
        form.addRow("Switch:", self._switch)
        form.addRow("Pause:", self._delay)

        self._estimate = QLabel()
        for edit in (self._from, self._to):
            edit.textChanged.connect(self._update_estimate)
        self._delay.valueChanged.connect(self._update_estimate)
        self._switch.currentIndexChanged.connect(self._update_estimate)

        self._start_btn = QPushButton("Start")
        self._stop_btn = QPushButton("Stopp")
        self._stop_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start)
        self._stop_btn.clicked.connect(self._on_stop)

        self._progress = QProgressBar()
        self._progress.setValue(0)
        self._progress.setFormat("%v / %m")

        self._only_ok = QCheckBox("nur Antworten (ok)")
        self._only_ok.toggled.connect(self._rebuild_table)

        self._export_btn = QPushButton("CSV exportieren")
        self._export_btn.clicked.connect(self._on_export)

        buttons = QHBoxLayout()
        buttons.addWidget(self._start_btn)
        buttons.addWidget(self._stop_btn)
        buttons.addStretch(1)
        buttons.addWidget(self._only_ok)
        buttons.addWidget(self._export_btn)

        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        settings = QGroupBox("Scan-Parameter")
        settings.setLayout(form)

        self._fw_label = QLabel("")
        self._fw_label.setWordWrap(True)
        self._fw_label.setStyleSheet("font-weight: bold;")
        self._lockable = (self._member, self._member_hex, self._from, self._to, self._format,
                          self._switch, self._delay, self._start_btn)

        # Teilnehmer-Suche (Produktiv)
        self._mem_from = QLineEdit(MEMBER_DEFAULT_FROM)
        self._mem_to = QLineEdit(MEMBER_DEFAULT_TO)
        self._mem_index = QLineEdit(MEMBER_DEFAULT_INDEX)
        for edit in (self._mem_from, self._mem_to, self._mem_index):
            edit.textChanged.connect(self._update_member_estimate)
        mem_form = QFormLayout()
        mem_form.addRow("ID von (Hex):", self._mem_from)
        mem_form.addRow("ID bis (Hex):", self._mem_to)
        mem_form.addRow("Index (Hex):", self._mem_index)

        self._mem_estimate = QLabel()
        self._mem_estimate.setWordWrap(True)
        self._mem_start_btn = QPushButton("Suchen")
        self._mem_stop_btn = QPushButton("Stopp")
        self._mem_stop_btn.setEnabled(False)
        self._mem_start_btn.clicked.connect(self._on_member_start)
        self._mem_stop_btn.clicked.connect(self._on_member_stop)

        self._mem_progress = QProgressBar()
        self._mem_progress.setValue(0)
        self._mem_progress.setFormat("%v / %m")

        self._mem_export_btn = QPushButton("Als CSV exportieren")
        self._mem_export_btn.clicked.connect(self._on_member_export)

        mem_buttons = QHBoxLayout()
        mem_buttons.addWidget(self._mem_start_btn)
        mem_buttons.addWidget(self._mem_stop_btn)
        mem_buttons.addStretch(1)
        mem_buttons.addWidget(self._mem_export_btn)

        self._mem_table = QTableWidget(0, len(MEMBER_COLUMNS))
        self._mem_table.setHorizontalHeaderLabels(MEMBER_COLUMNS)
        self._mem_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._mem_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._mem_table.verticalHeader().setVisible(False)
        self._mem_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._mem_table.itemDoubleClicked.connect(self._on_member_double_click)

        member_box = QGroupBox("Teilnehmer-Suche (Produktiv)")
        member_layout = QVBoxLayout(member_box)
        member_layout.addLayout(mem_form)
        member_layout.addWidget(self._mem_estimate)
        member_layout.addLayout(mem_buttons)
        member_layout.addWidget(self._mem_progress)
        member_layout.addWidget(self._mem_table, 1)
        self._mem_inputs = (self._mem_from, self._mem_to, self._mem_index)

        layout = QVBoxLayout(self)
        layout.addWidget(self._fw_label)
        layout.addWidget(settings)
        layout.addWidget(self._estimate)
        layout.addLayout(buttons)
        layout.addWidget(self._progress)
        layout.addWidget(self._table, 1)
        layout.addWidget(member_box, 1)

    def _populate_members(self) -> None:
        for member in self._db.members:
            raw_id = member.get("can_id")
            if raw_id is None:
                continue
            # ElsterTable.json liefert can_id als Hex-String ("0x180")
            can_id = int(raw_id, 0) if isinstance(raw_id, str) else int(raw_id)
            label = f"{member.get('name', '?')} (0x{can_id:03x})"
            self._member.addItem(label, can_id)

    # ---------- Hilfsfunktionen ----------

    def _range(self) -> tuple[int, int] | None:
        start = parse_hex(self._from.text())
        end = parse_hex(self._to.text())
        if start is None or end is None or start > end:
            return None
        return start, end

    def _member_range(self) -> tuple[int, int, int] | None:
        """(lo, hi, index) der Teilnehmer-Suche oder None bei ungueltiger Eingabe."""
        lo = parse_hex(self._mem_from.text())
        hi = parse_hex(self._mem_to.text())
        index = parse_hex(self._mem_index.text())
        if lo is None or hi is None or index is None:
            return None
        if lo > hi or hi > MEMBER_ID_MAX:
            return None
        return lo, hi, index

    def _ms_per_id(self) -> int:
        """Takt je Index: produktiv fest 150 ms, debug die eingestellte Pause."""
        return PROD_MS_PER_ID if self._fw == FW_PROD else self._delay.value()

    def _scan_count(self) -> int:
        """Anzahl der Scans: 3 bei 'alle', sonst 1."""
        return len(SEQ_SWITCHES) if self._switch.currentData() == SWITCH_ALL else 1

    def _update_estimate(self, *_args) -> None:
        rng = self._range()
        if rng is None:
            self._estimate.setText("Bereich ungueltig (Hex 0000-FFFF, Von <= Bis).")
            return
        per_scan = rng[1] - rng[0] + 1
        scans = self._scan_count()
        total = per_scan * scans
        ms = self._ms_per_id()
        seconds = total * ms / 1000.0
        prefix = f"{scans} Scans x {per_scan} Indizes = " if scans > 1 else ""
        self._estimate.setText(f"{prefix}{total} Indizes x {ms} ms = ca. {seconds:.1f} s")

    def _update_member_estimate(self, *_args) -> None:
        rng = self._member_range()
        if rng is None:
            self._mem_estimate.setText(
                "Bereich ungueltig (ID Hex 000-7FF, Von <= Bis; Index Hex 0000-FFFF).")
            return
        count = rng[1] - rng[0] + 1
        seconds = count * PROD_MS_PER_ID / 1000.0
        self._mem_estimate.setText(
            f"Dauer: ca. {PROD_MS_PER_ID} ms pro ID; {count} IDs = ca. {seconds:.0f} s.")

    def _set_scanning(self, active: bool) -> None:
        self._scanning = active
        self._apply_enabled()

    def _set_member_scanning(self, active: bool) -> None:
        self._member_scanning = active
        self._apply_enabled()

    def _on_firmware(self, kind: str) -> None:
        self._fw = kind
        if kind == FW_PROD:
            self._format.setCurrentIndex(self._format.findData("auto"))
        if kind not in (FW_DEBUG, FW_PROD):
            self._seq = None  # Sequenz bricht ab; Stopp bleibt wie bei einem Einzelscan bedienbar
        self._apply_enabled()
        self._update_estimate()

    def _apply_enabled(self) -> None:
        """Start und Eingaben bei Debug- oder Produktiv-Firmware, ohne laufenden Scan.

        Stopp bleibt bei laufendem Scan bedienbar. Bereichs-Scan und Teilnehmer-Suche
        sperren sich gegenseitig.
        """
        prod = self._fw == FW_PROD
        busy = self._scanning or self._member_scanning
        open_inputs = self._fw in (FW_DEBUG, FW_PROD) and not busy
        for widget in self._lockable:
            widget.setEnabled(open_inputs)
        # Produktiv: Format und Pause nicht verfuegbar, Tooltip erklaert warum
        self._format.setEnabled(open_inputs and not prod)
        self._delay.setEnabled(open_inputs and not prod)
        self._format.setToolTip(PROD_FORMAT_TEXT if prod else "")
        self._delay.setToolTip(PROD_DELAY_TEXT if prod else "")
        self._stop_btn.setEnabled(self._scanning)
        self._fw_label.setText(FW_LOCK_TEXT.get(self._fw, ""))
        self._fw_label.setVisible(self._fw == FW_UNKNOWN)
        # Teilnehmer-Suche: nur Produktiv
        member_open = prod and not busy
        self._mem_start_btn.setEnabled(member_open)
        self._mem_start_btn.setToolTip("" if prod else PROD_MEMBER_TEXT)
        for edit in self._mem_inputs:
            edit.setEnabled(member_open)
        self._mem_stop_btn.setEnabled(self._member_scanning)

    def _row_visible(self, item: dict) -> bool:
        return not self._only_ok.isChecked() or item["status"] == "ok"

    def _table_row(self, item: dict) -> list[str]:
        index = item["index"]
        raw = item["raw"]
        status = item["status"]
        switch = item["switch"]
        name = self._db.signal_name(index)
        decoded = "-"
        signal = self._db.signal_by_index(index)
        if status == "ok" and (signal is not None or self._db.bits_for(index)):
            decoded = self._db.decode(index, raw, signal.get("type", "") if signal else None)
        return [
            f"0x{index:04x}",
            switch_label(switch),
            name,
            f"0x{raw:04x}",
            status,
            decoded,
        ]

    def _append_row(self, item: dict) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        for col, text in enumerate(self._table_row(item)):
            cell = QTableWidgetItem(text)
            if col in RIGHT_ALIGNED_COLS:
                cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._table.setItem(row, col, cell)

    def _rebuild_table(self, *_args) -> None:
        self._table.setRowCount(0)
        for item in self._items.values():
            if self._row_visible(item):
                self._append_row(item)

    def _member_name(self, can_id: int) -> str:
        """Name aus der Produktiv-Tabelle, sonst aus der Datenbank, sonst "-"."""
        name = prod_api.member_name(can_id)
        if name is None:
            known = self._db.member_by_id(can_id)
            name = known["name"] if known else "-"
        return name

    def _member_display_name(self, item: dict) -> str:
        name = self._member_name(item["id"])
        if item["raw"] == RAW_NOT_SUPPORTED:
            name += " (nicht unterstuetzt)"
        return name

    def _member_row(self, item: dict) -> list[str]:
        return [
            f"0x{item['id']:03x}",
            self._member_display_name(item),
            f"0x{item['index']:04x}",
            f"0x{item['raw']:04x}",
            str(item["raw"]),
        ]

    def _append_member_row(self, item: dict) -> None:
        row = self._mem_table.rowCount()
        self._mem_table.insertRow(row)
        for col, text in enumerate(self._member_row(item)):
            cell = QTableWidgetItem(text)
            if col in MEMBER_RIGHT_ALIGNED_COLS:
                cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._mem_table.setItem(row, col, cell)

    def _selected_member(self) -> int | None:
        """Member aus gueltiger Hex-ID, sonst aus der Auswahl. None bei Fehler (mit Hinweis)."""
        text = self._member_hex.text()
        if text.strip():
            member = parse_member_id(text)
            if member is None:
                QMessageBox.warning(self, "Eingabe",
                                    "Hex-ID ungueltig: 0x000-0x7FF, ohne 0x680 (PC).")
            return member
        member = self._member.currentData()
        if member is None:
            QMessageBox.warning(self, "Eingabe", "Kein Member ausgewaehlt.")
        return member

    # ---------- Aktionen: Bereichs-Scan ----------

    def _on_start(self) -> None:
        rng = self._range()
        if rng is None:
            QMessageBox.warning(self, "Eingabe", "Bereich ungueltig (Hex 0000-FFFF, Von <= Bis).")
            return
        member = self._selected_member()
        if member is None:
            return
        start, end = rng
        fmt = self._format.currentData()
        if self._fw == FW_PROD:
            fmt = "auto"  # Produktiv kennt nur auto
        switch = self._switch.currentData()
        if fmt == "short" and end > 0xFF:
            QMessageBox.warning(
                self,
                "Eingabe",
                "Kurzformat nur fuer Indizes bis 0xFF. Bitte Format auto oder lang waehlen "
                "oder den Bereich begrenzen.",
            )
            return
        count = end - start + 1
        scans = self._scan_count()
        total = count * scans
        if total > MAX_RANGE:
            answer = QMessageBox.warning(
                self,
                "Grosser Bereich",
                f"Der Bereich umfasst {total} Indizes (Limit {MAX_RANGE})"
                + (f" ueber {scans} Scans" if scans > 1 else "") + ". "
                "Der Scan kann sehr lange dauern.\n\nTrotzdem starten?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        self._items.clear()
        self._table.setRowCount(0)
        self._progress.setRange(0, total)
        self._progress.setValue(0)
        self._scan_fmt = fmt
        if switch == SWITCH_ALL:
            self._seq = {
                "member": member, "start": start, "end": end, "delay": self._delay.value(),
                "fmt": fmt, "count": count, "queue": list(SEQ_SWITCHES[1:]),
                "current": SEQ_SWITCHES[0], "completed": 0,
            }
            self._set_scanning(True)
            self._bridge.send_scan(member, start, end, self._delay.value(), fmt, SEQ_SWITCHES[0])
            return
        self._set_scanning(True)
        self._bridge.send_scan(member, start, end, self._delay.value(), fmt, switch)

    def _on_stop(self) -> None:
        self._seq = None  # keine weiteren Scans der Sequenz
        self._bridge.send_stop()
        self._set_scanning(False)

    def _on_progress(self, data: dict) -> None:
        if data.get("mode") == "members":  # Teilnehmer-Suche, nicht dieser Balken
            self._on_member_progress(data)
            return
        if self._seq is not None:
            self._on_seq_progress(data)
            return
        total = int(data.get("total", 0))
        done = int(data.get("done", 0))
        if total > 0 and self._progress.maximum() != total:
            self._progress.setRange(0, total)
        self._progress.setValue(done)
        if total > 0 and done >= total:
            self._set_scanning(False)

    def _on_seq_progress(self, data: dict) -> None:
        """Sequenz 'alle': Gesamtfortschritt; bei state 'done' des aktuellen Scans den naechsten starten."""
        seq = self._seq
        state = data.get("state")
        if state == "stopped":
            self._seq = None
            self._set_scanning(False)
            return
        switch = int(data.get("switch", DEFAULT_SWITCH))
        if switch != seq["current"]:
            return  # Fortschritt eines anderen Scans der Sequenz
        done = int(data.get("done", 0))
        count = seq["count"]
        if state != "done":
            self._progress.setValue(seq["completed"] * count + done)
            return
        seq["completed"] += 1
        self._progress.setValue(seq["completed"] * count)
        if seq["queue"]:
            seq["current"] = seq["queue"].pop(0)
            self._bridge.send_scan(seq["member"], seq["start"], seq["end"], seq["delay"],
                                   seq["fmt"], seq["current"])
            return
        self._seq = None
        self._set_scanning(False)

    def _on_result(self, data: dict) -> None:
        payload_fmt = str(data.get("format", self._scan_fmt))
        for raw_item in data.get("items", []):
            item = {
                "index": int(raw_item["index"]),
                "raw": int(raw_item["raw"]),
                "status": str(raw_item["status"]),
                "switch": int(raw_item.get("switch", DEFAULT_SWITCH)),
                "format": str(raw_item.get("format", payload_fmt)),
            }
            key = (item["index"], item["switch"])
            replaced = key in self._items
            self._items[key] = item  # gleiche (index, switch) ersetzt den Treffer
            if replaced:
                self._rebuild_table()
            elif self._row_visible(item):
                self._append_row(item)

    def _on_export(self) -> None:
        if not self._items:
            QMessageBox.information(self, "Export", "Keine Ergebnisse zum Exportieren.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "CSV exportieren", "scan.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            save_scan_csv(path, list(self._items.values()), self._db)
        except OSError as exc:
            QMessageBox.critical(self, "Export", f"Speichern fehlgeschlagen:\n{exc}")

    # ---------- Aktionen: Teilnehmer-Suche (Produktiv) ----------

    def _on_member_start(self) -> None:
        rng = self._member_range()
        if rng is None:
            QMessageBox.warning(self, "Teilnehmer-Suche",
                                "Bereich ungueltig (ID Hex 000-7FF, Von <= Bis; "
                                "Index Hex 0000-FFFF).")
            return
        lo, hi, index = rng
        self._member_items.clear()
        self._mem_table.setRowCount(0)
        self._mem_progress.setRange(0, hi - lo + 1)
        self._mem_progress.setValue(0)
        self._set_member_scanning(True)
        try:
            self._bridge.send_member_scan(lo, hi, index)
        except ValueError as exc:
            self._set_member_scanning(False)
            QMessageBox.warning(self, "Teilnehmer-Suche", str(exc))

    def _on_member_stop(self) -> None:
        self._bridge.send_stop()
        self._set_member_scanning(False)

    def _on_member_progress(self, data: dict) -> None:
        state = data.get("state")
        if state == "running" and not self._member_scanning:
            self._set_member_scanning(True)
        total = int(data.get("total", 0))
        done = int(data.get("done", 0))
        if total > 0 and self._mem_progress.maximum() != total:
            self._mem_progress.setRange(0, total)
        self._mem_progress.setValue(done)
        if state in ("done", "stopped"):
            self._set_member_scanning(False)

    def _on_member_result(self, data: dict) -> None:
        item = {"id": int(data["id"]), "index": int(data["index"]), "raw": int(data["raw"])}
        self._member_items.append(item)
        self._append_member_row(item)

    def _on_member_double_click(self, cell: QTableWidgetItem) -> None:
        """Doppelklick uebernimmt die ID der Zeile in das Hex-ID-Feld des Scanners."""
        id_text = self._mem_table.item(cell.row(), 0).text()
        self._member_hex.setText(id_text)

    def _on_member_export(self) -> None:
        if not self._member_items:
            QMessageBox.information(self, "Export", "Keine Ergebnisse zum Exportieren.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "CSV exportieren", "teilnehmer.csv", "CSV (*.csv)"
        )
        if not path:
            return
        rows = [
            [f"0x{item['id']:03x}", self._member_display_name(item),
             f"0x{item['index']:04x}", item["raw"]]
            for item in self._member_items
        ]
        try:
            save_member_csv(path, rows)
        except OSError as exc:
            QMessageBox.critical(self, "Export", f"Speichern fehlgeschlagen:\n{exc}")
