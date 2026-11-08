"""Status-Tab: Verfuegbarkeit, MQTT-Verbindung, ESP-Zaehler aus 'status', CRC-Fehlerquote.

Warnung (rot), wenn der Slave-Ring ueberlaeuft, Slave-Fehler auftreten oder die Firmware nicht 'i2c' meldet.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

COLOR_OK = "#2e7d32"
COLOR_WARN = "#c62828"
COLOR_NEUTRAL = "#616161"

STATUS_FIELDS = [
    ("uptime_s", "Laufzeit (s)"),
    ("frames", "Frames"),
    ("frames_dropped", "Frames verworfen"),
    ("ring_fill", "Ring belegt"),
    ("ring_cap", "Ring Kapazitaet"),
    ("heap", "Heap (Bytes)"),
    ("pullup", "Interner Pull-up"),
    ("sniff", "Streaming an"),
    ("mode", "Filter-Modus"),
    ("ntp", "NTP synchron"),
    ("slave_addr", "Slave-Adresse"),
    ("slave_frames", "Slave: Frames"),
    ("slave_bytes", "Slave: Bytes"),
    ("slave_overflow", "Slave: Ueberlauf"),
    ("slave_errors", "Slave: Fehler"),
    ("lvl_sda", "Pegel SDA"),
    ("lvl_scl", "Pegel SCL"),
    ("scan_running", "Bus-Scan laeuft"),
]


def _colored(label: QLabel, text: str, color: str | None = None) -> None:
    label.setText(text)
    style = f"color: {color}; font-weight: bold;" if color else ""
    label.setStyleSheet(style)


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "ja" if value else "nein"
    return "-" if value is None else str(value)


class StatusTab(QWidget):
    """Zeigt die Werte aus bridge.status und die CRC-Fehlerquote des Modells."""

    def __init__(self, model, bridge, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self._bridge = bridge
        self._status: dict = {}

        self._lbl_avail = QLabel()
        self._lbl_conn = QLabel()
        self._lbl_fw = QLabel("-")
        self._lbl_warn = QLabel("")
        self._lbl_warn.setWordWrap(True)
        self._lbl_crc = QLabel("")
        self._field_labels: dict[str, QLabel] = {}

        _colored(self._lbl_avail, "unbekannt", COLOR_NEUTRAL)
        _colored(self._lbl_conn, "getrennt", COLOR_NEUTRAL)

        form = QFormLayout()
        form.addRow("Verfuegbarkeit:", self._lbl_avail)
        form.addRow("MQTT-Verbindung:", self._lbl_conn)
        form.addRow("Firmware:", self._lbl_fw)
        for key, label in STATUS_FIELDS:
            value = QLabel("-")
            self._field_labels[key] = value
            form.addRow(label + ":", value)
        form.addRow("CRC-Fehlerquote:", self._lbl_crc)

        # Bus-Scan: ESP wird kurz I2C-Master (Slave pausiert, Frames fehlen waehrenddessen).
        self._lbl_scan = QLabel("Noch kein Scan")
        self._lbl_scan.setWordWrap(True)
        self._scan_btn = QPushButton("Bus-Scan")
        self._scan_btn.setToolTip("ESP wird kurz I2C-Master und sucht Adressen 0x08..0x77 (Slave pausiert)")
        self._scan_btn.clicked.connect(self._on_scan_clicked)
        scan_row = QHBoxLayout()
        scan_row.addWidget(self._scan_btn)
        scan_row.addWidget(self._lbl_scan, 1)

        layout = QVBoxLayout(self)
        layout.addWidget(self._lbl_warn)
        layout.addLayout(form)
        layout.addLayout(scan_row)
        layout.addStretch(1)

        bridge.availability.connect(self._on_availability)
        bridge.connected.connect(self._on_connected)
        bridge.status.connect(self._on_status)
        bridge.scan_result.connect(self._on_scan_result)
        self.refresh()

    # ----- Oeffentliche Hooks -----

    def on_frames(self, rows: list[dict]) -> None:
        self.refresh()

    def refresh(self) -> None:
        total = self._model.crc_total
        bad = self._model.crc_bad
        rate = self._model.crc_error_rate() * 100
        self._lbl_crc.setText(f"{rate:.1f} % ({bad} von {total} Paketen mit CRC)")
        self._update_warnings()

    # ----- Slots -----

    def _on_availability(self, text: str) -> None:
        if text == "online":
            _colored(self._lbl_avail, "online", COLOR_OK)
        elif text == "offline":
            _colored(self._lbl_avail, "offline", COLOR_WARN)
        else:
            _colored(self._lbl_avail, str(text), COLOR_NEUTRAL)

    def _on_connected(self, ok: bool) -> None:
        if ok:
            _colored(self._lbl_conn, "verbunden", COLOR_OK)
        else:
            _colored(self._lbl_conn, "getrennt", COLOR_WARN)

    def _on_status(self, st: dict) -> None:
        self._status = dict(st)
        for key, _label in STATUS_FIELDS:
            self._field_labels[key].setText(_fmt(st.get(key)))
        fw = st.get("fw")
        _colored(self._lbl_fw, "i2c" if fw == "i2c" else f"{_fmt(fw)} (kein i2c-Firmware)",
                 COLOR_OK if fw == "i2c" else COLOR_WARN)
        self._update_warnings()

    def _on_scan_clicked(self) -> None:
        self._bridge.send_scan()
        self._lbl_scan.setText("Scan angefordert ...")

    def _on_scan_result(self, res: dict) -> None:
        found = res.get("found", [])
        addrs = ", ".join(f"0x{int(a):02x}" for a in found if isinstance(a, int)) or "keine"
        text = f"Gefunden: {addrs} | nack {res.get('nack', 0)}, Arb.-Verlust {res.get('arb_lost', 0)}, "                f"Wiederholungen {res.get('retries', 0)}, {res.get('ms', 0)} ms"
        failed = res.get("failed") or []
        if failed:
            text += " | ohne Ergebnis: " + ", ".join(f"0x{int(a):02x}" for a in failed if isinstance(a, int))
        ok = bool(res.get("ok"))
        if not ok:
            text += f" | Fehler: {res.get('err', 'unbekannt')}"
        _colored(self._lbl_scan, text, COLOR_OK if ok else COLOR_WARN)

    # ----- Intern -----

    def _update_warnings(self) -> None:
        st = self._status
        problems = []
        if st and int(st.get("slave_overflow", 0) or 0) > 0:
            problems.append(f"Slave-Ring ueberlaufen ({st.get('slave_overflow')}): Frames fehlen")
        if st and int(st.get("slave_errors", 0) or 0) > 0:
            problems.append(f"Slave-Fehler ({st.get('slave_errors')})")
        if problems:
            _colored(self._lbl_warn, " | ".join(problems), COLOR_WARN)
        else:
            _colored(self._lbl_warn, "")
