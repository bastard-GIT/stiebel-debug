"""Tab 'Abfrage': gezieltes Lesen und Schreiben eines Elster-Signals per MQTT und Verlauf der Ergebnisse."""

import re
from datetime import datetime

from PySide6.QtCore import Qt, QStringListModel, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QCompleter,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from elster import ElsterDb, decode_value, switch_label
from mqtt_client import FW_DEBUG, FW_UNKNOWN, MqttBridge

try:
    from elster import build_write_request, encode_value, is_writable
except ImportError:  # Schreibfunktionen in elster.py fehlen noch: Schreiben bleibt gesperrt
    _WRITE_MISSING = "Schreiben noch nicht verfuegbar (elster-Schreibfunktionen fehlen)."

    def encode_value(type_name, text):
        raise ValueError(_WRITE_MISSING)

    def build_write_request(member, index, value, fmt="auto"):
        raise ValueError(_WRITE_MISSING)

    def is_writable(type_name):
        return False

MAX_HISTORY = 1000
FW_LOCK_TEXT = {
    "prod": "Nur mit Debug-Firmware moeglich: die Produktiv-Firmware beantwortet keine Abfragen.",
    FW_UNKNOWN: "Warte auf Status der Firmware...",
}
DEFAULT_TYPE = "et_default"
STATUS_TEXT = {
    "ok": "ok",
    "unsupported": "nicht unterstützt",
    "timeout": "Timeout",
}
WRITE_ACK_TEXT = {2: "response", 3: "ack", 4: "write ack", 5: "write respond"}
HEADERS = ["Zeit", "Modus", "Member", "Index", "Switch", "Name", "Typ", "Roh hex", "Roh dez",
           "Wert", "Ack", "Rücklesewert", "Status"]
_HEX_RE = re.compile(r"^(0x)?[0-9a-fA-F]{1,4}$", re.IGNORECASE)
_MEMBER_LABEL_RE = re.compile(r"\((0x[0-9a-fA-F]{1,4})\)\s*$")

# Anzeige -> Parameter fuer send_read (INTERFACES 8.3)
FORMAT_CHOICES = (("auto", "auto"), ("kurz", "short"), ("lang", "long"))
SWITCH_ALL = "alle"  # keine Firmware-Option: drei normale Abfragen nacheinander
SWITCH_CHOICES = (("aktuell", 0xFA), ("min", 0xF8), ("max", 0xF9), ("alle", SWITCH_ALL))
DEFAULT_SWITCH = 0xFA
SEQ_SWITCHES = (0xFA, 0xF8, 0xF9)  # Reihenfolge bei 'alle'
SEQ_TIMEOUT_MS = 3000  # Wartezeit je Switch, danach weiter zum naechsten; im Test verkuerzbar
NO_ANSWER = "keine Antwort"
SUMMARY_LABELS = (("aktuell", 0xFA), ("min", 0xF8), ("max", 0xF9))
OWN_CAN_ID = 0x680  # eigene ID der Steuerung: nie als Ziel erlaubt
MODE_READ = "read"
MODE_WRITE = "write"
MODE_CHOICES = (("Lesen", MODE_READ), ("Schreiben", MODE_WRITE))
STANDARD_TYPE_LABEL = "Standard (Typ des Signals)"


def _to_int(value):
    """Wandelt Zahl oder '0x..'-String in int; None wenn nicht möglich."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value), 0)
    except ValueError:
        return None


def _parse_raw_hex(text: str) -> int:
    """Rohwert in Hex ('0x0208' oder '0208'); ValueError wenn ungueltig."""
    t = text.strip()
    if t[:2].lower() == "0x":
        t = t[2:]
    try:
        raw = int(t, 16)
    except ValueError:
        raise ValueError(f"Rohwert ist kein Hex-Wert: '{text}'") from None
    if not 0 <= raw <= 0xFFFF:
        raise ValueError("Rohwert muss zwischen 0x0000 und 0xFFFF liegen.")
    return raw


class QueryTab(QWidget):
    def __init__(self, db: ElsterDb, bridge: MqttBridge):
        super().__init__()
        self._db = db
        self._bridge = bridge
        # (member, index, switch) der letzten Abfrage; nur deren Antwort füllt "Letztes Ergebnis".
        self._pending: tuple[int, int, int] | None = None
        self._fw = FW_UNKNOWN
        # Sequenz 'alle': member, index, fmt, queue (noch nicht gesendete Switches), current.
        self._seq: dict | None = None
        # Ergebnis je Switch fuer die Zeile 'aktuell | min | max'; None = noch nicht eingetroffen.
        self._alle_key: tuple[int, int] | None = None
        self._alle_results: dict[int, str | None] = {}
        self._seq_timer = QTimer(self)
        self._seq_timer.setSingleShot(True)
        self._seq_timer.timeout.connect(self._on_seq_timeout)
        # Schreiben: Sendeauftrag offen (bis write_result) und letzter gelesener Wert je (member, index).
        self._write_pending = False
        self._last_read: dict[tuple[int, int], str] = {}
        # Absender und Timeout der laufenden Leseabfrage (auch fuer die Sequenz 'alle').
        self._read_src = OWN_CAN_ID
        self._read_timeout = 1000

        self._mode_combo = QComboBox()
        for label, value in MODE_CHOICES:
            self._mode_combo.addItem(label, value)

        self._member_combo = QComboBox()
        self._member_combo.setEditable(True)
        self._member_combo.setInsertPolicy(QComboBox.NoInsert)
        for m in db.members:
            can_id = _to_int(m.get("can_id"))  # Referenz liefert "0x180" als String
            if can_id is None:
                continue
            self._member_combo.addItem(f"{m.get('name', '')} (0x{can_id:03x})", can_id)
        self._member_combo.setEditText(self._member_combo.itemText(0))

        # Absender (nur Lesen): Standard eigene ID 0x680, sonst beliebige CAN-ID (cmd/read "src").
        self._src_combo = QComboBox()
        self._src_combo.setEditable(True)
        self._src_combo.setInsertPolicy(QComboBox.NoInsert)
        self._src_combo.addItem(f"PC (0x{OWN_CAN_ID:03x})", OWN_CAN_ID)
        for m in db.members:
            can_id = _to_int(m.get("can_id"))
            if can_id is None or can_id == OWN_CAN_ID:
                continue
            self._src_combo.addItem(f"{m.get('name', '')} (0x{can_id:03x})", can_id)
        self._src_combo.setToolTip("Absender-CAN-ID des Requests. Eine ID, die selbst auf dem Bus sendet, "
                                   "kann Kollisionen ausloesen; deren Antworten gehen auch an das echte Geraet.")
        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(100, 10000)
        self._timeout_spin.setSingleStep(500)
        self._timeout_spin.setValue(1000)
        self._timeout_spin.setSuffix(" ms")
        self._timeout_spin.setToolTip("Antwort-Timeout der Firmware. FES (0x100) braucht 0,5..1,5 s, "
                                      "mit Rueckstau bis ~4 s.")

        self._index_edit = QLineEdit()
        self._index_edit.setPlaceholderText("Hex (0x4ef0) oder Signalname")
        self._completer_model = QStringListModel(self._signal_labels(), self)
        completer = QCompleter(self._completer_model, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        self._index_edit.setCompleter(completer)
        self._index_edit.returnPressed.connect(self._on_query)

        self._format_combo = QComboBox()
        for label, value in FORMAT_CHOICES:
            self._format_combo.addItem(label, value)
        self._switch_combo = QComboBox()
        for label, value in SWITCH_CHOICES:
            self._switch_combo.addItem(label, value)

        # Schreib-Eingaben
        self._value_edit = QLineEdit()
        self._value_edit.setPlaceholderText("z.B. 52.0 (oder Hex bei Rohwert)")
        self._type_combo = QComboBox()
        self._type_combo.addItem(STANDARD_TYPE_LABEL, None)
        for type_name in sorted({s.get("type") for s in db.signals if s.get("type")}
                                | {DEFAULT_TYPE}):
            self._type_combo.addItem(type_name, type_name)
        self._raw_check = QCheckBox("Rohwert (hex)")
        self._arm_check = QCheckBox("Schreiben freischalten")
        self._preview_label = QLabel("")
        self._preview_label.setWordWrap(True)
        self._preview_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self._query_btn = QPushButton("Abfragen")
        self._query_btn.clicked.connect(self._on_query)
        self._send_btn = QPushButton("Senden")
        self._send_btn.clicked.connect(self._on_send)
        self._clear_btn = QPushButton("Verlauf loeschen")
        self._clear_btn.clicked.connect(self._on_clear)

        self._info_label = QLabel("–")
        self._info_label.setWordWrap(True)
        self._info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self._hint_label = QLabel("")
        self._hint_label.setWordWrap(True)

        self._fw_label = QLabel("")
        self._fw_label.setWordWrap(True)
        self._fw_label.setStyleSheet("font-weight: bold;")

        # Bedienelemente nach Modus: Lesen-Only und Schreiben-Only (Sichtbarkeit).
        self._switch_lbl = QLabel("Switch:")
        self._src_lbl = QLabel("Absender:")
        self._timeout_lbl = QLabel("Timeout:")
        self._value_lbl = QLabel("Wert:")
        self._type_lbl = QLabel("Typ:")
        self._opts_lbl = QLabel("Optionen:")
        self._preview_lbl = QLabel("Vorschau:")
        self._read_only = (self._switch_lbl, self._switch_combo, self._query_btn,
                           self._src_lbl, self._src_combo, self._timeout_lbl, self._timeout_spin)
        self._write_only = (self._value_lbl, self._value_edit, self._type_lbl, self._type_combo,
                            self._opts_lbl, self._raw_check, self._arm_check,
                            self._preview_lbl, self._preview_label, self._send_btn)
        self._write_inputs = (self._value_edit, self._type_combo, self._raw_check, self._arm_check)
        # Firmware-Sperre: Lesen-Controls plus Moduswahl; Schreib-Eingaben einzeln in _update_write_ui.
        self._lockable = (self._mode_combo, self._member_combo, self._index_edit, self._format_combo,
                          self._switch_combo, self._query_btn, self._src_combo, self._timeout_spin)

        form = QFormLayout()
        form.addRow("Modus:", self._mode_combo)
        form.addRow("Empfaenger:", self._member_combo)
        form.addRow(self._src_lbl, self._src_combo)
        form.addRow(self._timeout_lbl, self._timeout_spin)
        form.addRow("Index:", self._index_edit)
        form.addRow("Format:", self._format_combo)
        form.addRow(self._switch_lbl, self._switch_combo)
        form.addRow(self._value_lbl, self._value_edit)
        form.addRow(self._type_lbl, self._type_combo)
        opts_row = QHBoxLayout()
        opts_row.addWidget(self._raw_check)
        opts_row.addWidget(self._arm_check)
        opts_row.addStretch(1)
        form.addRow(self._opts_lbl, opts_row)
        form.addRow(self._preview_lbl, self._preview_label)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self._query_btn)
        btn_row.addWidget(self._send_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self._clear_btn)
        form.addRow("", btn_row)

        query_box = QGroupBox("Abfrage")
        query_box.setLayout(form)

        result_box = QGroupBox("Letztes Ergebnis")
        result_layout = QVBoxLayout(result_box)
        result_layout.addWidget(self._info_label)
        result_layout.addWidget(self._hint_label)

        self._table = QTableWidget(0, len(HEADERS))
        self._table.setHorizontalHeaderLabels(HEADERS)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(HEADERS.index("Name"), QHeaderView.Stretch)

        history_box = QGroupBox(f"Verlauf (neueste oben, max. {MAX_HISTORY})")
        history_layout = QVBoxLayout(history_box)
        history_layout.addWidget(self._table)

        root = QVBoxLayout(self)
        root.addWidget(self._fw_label)
        root.addWidget(query_box)
        root.addWidget(result_box)
        root.addWidget(history_box, 1)

        # Signale erst nach Aufbau aller Widgets verbinden (Slots greifen auf alle zu).
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._member_combo.currentTextChanged.connect(lambda _t: self._update_write_ui())
        self._index_edit.textChanged.connect(lambda _t: self._update_write_ui())
        self._format_combo.currentIndexChanged.connect(lambda _i: self._update_write_ui())
        self._value_edit.textChanged.connect(lambda _t: self._update_write_ui())
        self._type_combo.currentIndexChanged.connect(lambda _i: self._update_write_ui())
        self._raw_check.toggled.connect(lambda _c: self._update_write_ui())
        self._arm_check.toggled.connect(lambda _c: self._update_write_ui())

        self._apply_firmware(FW_UNKNOWN)
        bridge.read_result.connect(self._on_read_result)
        bridge.firmware_kind.connect(self._apply_firmware)
        if hasattr(bridge, "write_result"):  # aeltere Bridges ohne Schreibfunktion
            bridge.write_result.connect(self._on_write_result)
        if hasattr(bridge, "connected"):
            bridge.connected.connect(self._on_connected)

    # ----- Firmware-Sperre -------------------------------------------------

    def _apply_firmware(self, kind: str) -> None:
        """Abfrage-Bedienelemente nur bei Debug-Firmware aktiv; Hinweis bei prod/unknown."""
        self._fw = kind
        if kind != FW_DEBUG and self._seq is not None:
            self._seq_abort("Abfrage 'alle' abgebrochen: Firmware ist nicht Debug.")
        self._write_pending = False  # Firmwarewechsel: Antwort auf offenen Schreibauftrag unsicher
        self._disarm()
        self._apply_lock()

    def _on_connected(self, online: bool) -> None:
        if not online:
            self._disarm()

    def _apply_lock(self) -> None:
        allowed = self._fw == FW_DEBUG
        for widget in self._lockable:
            widget.setEnabled(allowed)
        if self._seq is not None:  # Firmware beantwortet nur eine Anfrage gleichzeitig
            self._query_btn.setEnabled(False)
            self._mode_combo.setEnabled(False)
        self._fw_label.setText(FW_LOCK_TEXT.get(self._fw, ""))
        self._fw_label.setVisible(not allowed)
        self._update_write_ui()

    def _disarm(self) -> None:
        self._arm_check.setChecked(False)

    # ----- Modus Schreiben ------------------------------------------------

    def _mode(self) -> str:
        return self._mode_combo.currentData()

    def _on_mode_changed(self, _index: int) -> None:
        self._disarm()
        self._hint_label.setText("")
        self._update_write_ui()

    def _update_write_ui(self) -> None:
        """Sichtbarkeit, Vorschau und Sendefreigabe aus Modus, Firmware und Eingaben."""
        write = self._mode() == MODE_WRITE
        allowed = self._fw == FW_DEBUG
        for widget in self._read_only:
            widget.setVisible(not write)
        for widget in self._write_only:
            widget.setVisible(write)
        for widget in self._write_inputs:
            widget.setEnabled(allowed)
        plan, error = self._write_plan() if write else (None, "")
        if not write:
            self._preview_label.setText("")
            self._preview_label.setStyleSheet("")
        elif plan is None:
            self._preview_label.setText(error)
            self._preview_label.setStyleSheet("color: #c00000; font-weight: bold;")
        else:
            self._preview_label.setText(
                f"Rohwert: 0x{plan['raw']:04x} ({plan['raw']})   "
                f"Frame: 0x{plan['can_id']:03x}  {bytes(plan['frame']).hex(' ')}"
            )
            self._preview_label.setStyleSheet("")
        self._send_btn.setEnabled(
            write and allowed and plan is not None and self._arm_check.isChecked()
            and not self._write_pending
        )

    def _write_plan(self):
        """Prueft alle Schreib-Eingaben. Gibt (plan, '') oder (None, fehlertext)."""
        member, member_error = self._parse_member(self._member_combo.currentText())
        if member is None:
            return None, member_error
        index, hint = self._resolve_index(self._index_edit.text())
        if index is None:
            return None, hint
        if not 0 <= index <= 0xFFFF:
            return None, "Index muss zwischen 0x0000 und 0xFFFF liegen."
        fmt = self._format_combo.currentData()
        type_name = self._effective_type(index)
        text = self._value_edit.text().strip()
        if not text:
            return None, "Bitte Wert eingeben."
        try:
            if self._raw_check.isChecked():
                raw = _parse_raw_hex(text)
            else:
                if not is_writable(type_name):
                    return None, f"Typ '{type_name}' ist nicht schreibbar."
                raw = encode_value(type_name, text)
                if not 0 <= raw <= 0xFFFF:
                    raise ValueError("Rohwert ausserhalb 0x0000..0xFFFF.")
            can_id, frame = build_write_request(member, index, raw, fmt)
        except ValueError as exc:
            return None, f"Wert ungueltig: {exc}"
        return {"member": member, "index": index, "type": type_name, "fmt": fmt,
                "raw": raw, "can_id": can_id, "frame": frame, "text": text}, ""

    def _effective_type(self, index: int) -> str:
        chosen = self._type_combo.currentData()
        if chosen:
            return chosen
        sig = self._db.signal_by_index(index)
        return (sig.get("type") if sig else None) or DEFAULT_TYPE

    def _confirm_text(self, plan: dict) -> str:
        member = plan["member"]
        index = plan["index"]
        sig = self._db.signal_by_index(index)
        name = sig.get("name", self._db.signal_name(index)) if sig else self._db.signal_name(index)
        lines = [
            "Schreibbefehl senden?",
            f"Member: {self._db.member_name(member)} (0x{member:03x})",
            f"Index: 0x{index:04x} {name}",
            f"Typ: {plan['type']}",
            f"Neuer Wert: {self._decode(plan['type'], plan['raw'], index)}",
            f"Rohwert: 0x{plan['raw']:04x} ({plan['raw']})",
            f"Frame: 0x{plan['can_id']:03x}  {bytes(plan['frame']).hex(' ')}",
        ]
        last = self._last_read.get((member, index))
        if last is not None:
            lines.append(f"Letzter gelesener Wert: {last}")
        if self._db.member_by_id(member) is None:
            lines.append("Warnung: Unbekannter Member")
        if sig is None:
            lines.append("Warnung: Unbekanntes Signal")
        return "\n".join(lines)

    def _confirm_write(self, text: str) -> bool:
        answer = QMessageBox.question(self, "Schreiben bestaetigen", text,
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def _on_send(self):
        if not self._send_btn.isEnabled():
            return
        plan, error = self._write_plan()
        if plan is None:
            self._hint_label.setText(error)
            return
        if not self._confirm_write(self._confirm_text(plan)):
            return
        try:
            self._bridge.send_write(plan["member"], plan["index"], plan["raw"], plan["fmt"])
        except ValueError as exc:
            self._hint_label.setText(f"Senden abgelehnt: {exc}")
            return
        self._write_pending = True
        self._info_label.setText(
            f"Schreibauftrag gesendet: Member 0x{plan['member']:03x}, Index 0x{plan['index']:04x}, "
            f"Rohwert 0x{plan['raw']:04x} ..."
        )
        self._update_write_ui()

    def _on_write_result(self, payload: dict):
        member = _to_int(payload.get("member"))
        index = _to_int(payload.get("index"))
        value = _to_int(payload.get("value"))
        readback = _to_int(payload.get("readback"))
        ack = _to_int(payload.get("ack"))
        status = str(payload.get("status") or "-")
        if index is None:
            return
        sig = self._db.signal_by_index(index)
        name = sig.get("name", self._db.signal_name(index)) if sig else self._db.signal_name(index)
        type_name = (sig.get("type") if sig else None) or DEFAULT_TYPE
        sent_text = self._decode(type_name, value, index)
        readback_text = self._decode(type_name, readback, index) if readback is not None else "–"
        ack_text = WRITE_ACK_TEXT.get(ack, "-")
        value_hex = f"0x{value:04x}" if value is not None else "–"
        value_dec = str(value) if value is not None else "–"
        member_text = self._db.member_name(member) if member is not None else "–"

        if member is not None and readback is not None and status != "timeout":
            self._last_read[(member, index)] = f"{readback_text} (0x{readback:04x})"
        self._info_label.setText(
            f"Schreiben: {name}\n"
            f"Member: {member_text}   Index: 0x{index:04x}   Typ: {type_name}\n"
            f"Gesendet: {value_hex} ({value_dec})   Wert: {sent_text}\n"
            f"Ack: {ack_text}   Rücklesewert: {readback_text}\n"
            f"Status: {status}"
        )
        self._add_history_row([
            datetime.now().strftime("%H:%M:%S"),
            "Schreiben",
            member_text,
            f"0x{index:04x}",
            "",
            name,
            type_name,
            value_hex,
            value_dec,
            sent_text,
            ack_text,
            readback_text,
            status,
        ])
        self._write_pending = False
        self._disarm()  # nach jedem abgeschlossenen Schreiben wieder gesperrt
        self._update_write_ui()

    # ----- Hilfsfunktionen -------------------------------------------------

    def _parse_member(self, text: str) -> tuple[int | None, str]:
        """Member aus Listeneintrag 'NAME (0x180)', Zahl, '0x..' oder Referenzname.
        Gibt (can_id, '') oder (None, hinweis)."""
        text = text.strip()
        if not text:
            return None, "Bitte Member eingeben (Name, 0x-Hex oder Zahl)."
        value = None
        m = _MEMBER_LABEL_RE.search(text)
        if m:
            value = int(m.group(1), 16)
        else:
            value = _to_int(text)
            if value is None:
                for member in self._db.members:
                    if str(member.get("name", "")).lower() == text.lower():
                        value = _to_int(member.get("can_id"))
                        break
        if value is None:
            return None, (f"Member '{text}' unbekannt: Referenzname, 0x-Hex (0x4ab) oder Zahl "
                          "eingeben.")
        if value == OWN_CAN_ID:
            return None, "Member 0x680 ist die eigene ID und nicht erlaubt."
        if not 0 <= value <= 0x7FF:
            return None, f"Member 0x{value:x} ausserhalb 0x000..0x7FF."
        return value, ""

    def _parse_src(self) -> tuple[int | None, str]:
        """Absender aus 'NAME (0x700)', Zahl oder '0x..'. 0x680 erlaubt (Standard)."""
        text = self._src_combo.currentText().strip()
        m = _MEMBER_LABEL_RE.search(text)
        value = int(m.group(1), 16) if m else _to_int(text)
        if value is None:
            for member in self._db.members:
                if str(member.get("name", "")).lower() == text.lower():
                    value = _to_int(member.get("can_id"))
                    break
        if value is None or not 0 <= value <= 0x7FF:
            return None, f"Absender '{text}' ungueltig: 0x000..0x7FF als Hex, Zahl oder Referenzname."
        return value, ""

    def _read_extra(self) -> dict:
        """Zusatzfelder fuer send_read nur bei Abweichung vom Standard (aeltere Bridges ohne src/timeout_ms)."""
        extra = {}
        if self._read_src != OWN_CAN_ID:
            extra["src"] = self._read_src
        if self._read_timeout != 1000:
            extra["timeout_ms"] = self._read_timeout
        return extra

    def _current_member(self) -> int | None:
        return self._parse_member(self._member_combo.currentText())[0]

    def _signal_labels(self):
        labels = []
        for s in self._db.search_signals(""):  # leerer Teilstring trifft alle Signale
            idx = _to_int(s.get("index_int"))
            if idx is not None:
                labels.append(f"{s.get('name', '')} (0x{idx:04x})")
        return labels

    def _resolve_index(self, text: str):
        """Hex-Index oder Namenssuche. Gibt (index, hinweis) oder (None, fehlertext)."""
        text = text.strip()
        if not text:
            return None, "Bitte Index oder Signalname eingeben."
        if _HEX_RE.match(text):
            return int(text, 16), ""
        # Auswahl aus Completer-Format "NAME (0x....)" zulassen
        m = re.search(r"\((0x[0-9a-fA-F]{1,4})\)\s*$", text)
        if m:
            return int(m.group(1), 16), ""
        matches = self._db.search_signals(text)
        if not matches:
            return None, f"Kein Signal zu '{text}' gefunden."
        exact = [s for s in matches if s.get("name", "").lower() == text.lower()]
        chosen = (exact or matches)[0]
        idx = _to_int(chosen.get("index_int"))
        if idx is None:
            return None, f"Signal '{chosen.get('name')}' hat keinen gueltigen Index."
        hint = f"Namenssuche: {chosen.get('name')} (0x{idx:04x})"
        if len(matches) > 1:
            hint += f" – {len(matches) - 1} weitere Treffer, bitte genauer eingeben oder Vorschlag waehlen"
        return idx, hint

    def _decode(self, type_name: str, raw, index: int | None = None):
        if raw is None:
            return "–"
        try:
            if index is not None:
                return self._db.decode(index, raw, type_name)
            return decode_value(type_name, raw)
        except Exception as exc:  # Decoder darf die GUI nicht abstuerzen lassen
            return f"Fehler: {exc}"

    # ----- Slots -----------------------------------------------------------

    def _on_query(self):
        if self._seq is not None:
            return  # Sequenz 'alle' laeuft bereits
        if self._mode() == MODE_WRITE:
            return  # Enter im Indexfeld sendet nie einen Schreibbefehl
        member, member_error = self._parse_member(self._member_combo.currentText())
        if member is None:
            self._hint_label.setText(member_error)
            return
        index, hint = self._resolve_index(self._index_edit.text())
        if index is None:
            self._hint_label.setText(hint)
            return
        if not 0 <= index <= 0xFFFF:
            self._hint_label.setText("Index muss zwischen 0x0000 und 0xFFFF liegen.")
            return
        fmt = self._format_combo.currentData()
        switch = self._switch_combo.currentData()
        if fmt == "short" and index > 0xFF:
            self._hint_label.setText("Kurzformat nur fuer Index bis 0xFF; bitte Format auto oder lang waehlen.")
            return
        src, src_error = self._parse_src()
        if src is None:
            self._hint_label.setText(src_error)
            return
        if src == member:
            self._hint_label.setText("Absender und Empfaenger duerfen nicht gleich sein.")
            return
        self._read_src = src
        self._read_timeout = self._timeout_spin.value()
        self._hint_label.setText(hint)
        if switch == SWITCH_ALL:
            self._start_sequence(member, index, fmt)
            return
        self._alle_key = None  # Einzelabfrage: Summenzeile von 'alle' nicht mehr aktuell
        self._info_label.setText(f"Anfrage gesendet: Empfaenger 0x{member:03x}, Absender 0x{src:03x}, "
                                 f"Index 0x{index:04x} ...")
        self._pending = (member, index, switch)
        self._bridge.send_read(member, index, fmt, switch, **self._read_extra())

    def _start_sequence(self, member: int, index: int, fmt: str) -> None:
        self._alle_key = (member, index)
        self._alle_results = {sw: None for _label, sw in SUMMARY_LABELS}
        self._seq = {"member": member, "index": index, "fmt": fmt,
                     "queue": list(SEQ_SWITCHES[1:]), "current": SEQ_SWITCHES[0]}
        self._info_label.setText(self._summary_text())
        self._apply_lock()
        self._send_step()

    def _send_step(self) -> None:
        seq = self._seq
        self._pending = (seq["member"], seq["index"], seq["current"])
        # Laengeres Firmware-Timeout: GUI wartet entsprechend laenger.
        wait = SEQ_TIMEOUT_MS if self._read_timeout <= 1000 else max(SEQ_TIMEOUT_MS, self._read_timeout + 1000)
        self._seq_timer.start(wait)
        self._bridge.send_read(seq["member"], seq["index"], seq["fmt"], seq["current"], **self._read_extra())

    def _seq_advance(self) -> None:
        seq = self._seq
        if seq["queue"]:
            seq["current"] = seq["queue"].pop(0)
            self._send_step()
            return
        self._seq = None
        self._apply_lock()
        self._hint_label.setText("Abfrage 'alle' abgeschlossen.")

    def _seq_abort(self, text: str) -> None:
        self._seq_timer.stop()
        self._seq = None
        self._hint_label.setText(text)
        self._apply_lock()

    def _on_seq_timeout(self) -> None:
        seq = self._seq
        if seq is None:
            return
        if self._alle_results.get(seq["current"]) is None:
            self._alle_results[seq["current"]] = NO_ANSWER
        self._info_label.setText(self._summary_text())
        self._seq_advance()

    def _summary_text(self) -> str:
        parts = []
        for label, sw in SUMMARY_LABELS:
            value = self._alle_results.get(sw)
            parts.append(f"{label}: {value if value is not None else '-'}")
        return " | ".join(parts)

    @staticmethod
    def _alle_text(status: str, value_text: str) -> str:
        if status == "ok":
            return value_text
        if status == "unsupported":
            return "nicht unterstuetzt"
        if status == "timeout":
            return NO_ANSWER
        return STATUS_TEXT.get(status, status) or NO_ANSWER

    def _on_clear(self):
        self._table.setRowCount(0)

    def _on_read_result(self, payload: dict):
        member = _to_int(payload.get("member"))
        index = _to_int(payload.get("index"))
        raw = _to_int(payload.get("raw"))
        status = str(payload.get("status", ""))
        switch = _to_int(payload.get("switch"))
        if switch is None:
            switch = DEFAULT_SWITCH  # aeltere Firmware ohne Feld
        if index is None:
            return  # Ergebnis ohne Index ist nicht zuordenbar

        sig = self._db.signal_by_index(index)
        if sig is not None:
            name = sig.get("name", self._db.signal_name(index))
            type_name = sig.get("type") or DEFAULT_TYPE
        else:
            name = self._db.signal_name(index)
            type_name = DEFAULT_TYPE

        if status == "unsupported" and raw is None:
            raw = 0x8000
        value_text = self._decode(type_name, raw, index) if status != "timeout" else "–"
        member_text = self._db.member_name(member) if member is not None else "–"
        status_text = STATUS_TEXT.get(status, status or "–")
        raw_hex = f"0x{raw:04x}" if raw is not None else "–"
        raw_dec = str(raw) if raw is not None else "–"
        sw_text = switch_label(switch)
        src = _to_int(payload.get("src"))
        src_text = f" von 0x{src:03x}" if src is not None and src != OWN_CAN_ID else ""
        if member is not None and status == "ok" and raw is not None:
            self._last_read[(member, index)] = f"{value_text} ({raw_hex})"

        # Antwort einer Sequenz 'alle': Summenzeile aktualisieren, bei aktuellem Switch weiter.
        if member is not None and (member, index) == self._alle_key and switch in self._alle_results:
            self._alle_results[switch] = self._alle_text(status, value_text)
            self._info_label.setText(self._summary_text())
            if self._seq is not None and switch == self._seq["current"]:
                self._seq_timer.stop()
                self._seq_advance()
        # Nur die Antwort auf die aktuelle Abfrage (Member, Index, Switch) fuellt das
        # "Letzte Ergebnis"-Feld; andere Switch-Antworten landen nur im Verlauf.
        elif self._pending == (member, index, switch):
            self._info_label.setText(
                f"Name: {name}\n"
                f"Empfaenger: {member_text}{src_text}   Index: 0x{index:04x}   Switch: {sw_text or 'aktuell'}\n"
                f"Typ: {type_name}\n"
                f"Roh: {raw_hex} ({raw_dec})   Wert: {value_text}\n"
                f"Status: {status_text}"
            )
        elif self._pending is not None:
            self._hint_label.setText(
                f"Antwort fuer Index 0x{index:04x}, Switch {sw_text or 'aktuell'} steht nur im Verlauf "
                "(passt nicht zur letzten Abfrage)."
            )

        self._add_history_row(
            [
                datetime.now().strftime("%H:%M:%S"),
                "Lesen" + src_text,
                member_text,
                f"0x{index:04x}",
                sw_text,
                name,
                type_name,
                raw_hex,
                raw_dec,
                value_text,
                "–",
                "–",
                status_text,
            ]
        )

    def _add_history_row(self, cells):
        self._table.insertRow(0)
        for col, text in enumerate(cells):
            self._table.setItem(0, col, QTableWidgetItem(str(text)))
        while self._table.rowCount() > MAX_HISTORY:
            self._table.removeRow(self._table.rowCount() - 1)
