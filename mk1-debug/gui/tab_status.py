"""Status-Tab: Verfuegbarkeit, MQTT-Verbindung und ESP-Status (kein ElsterDb-Zugriff)."""
import time

from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from mqtt_client import FW_DEBUG, FW_PROD, MqttBridge

COLOR_OK = "#2e7d32"
COLOR_WARN = "#c62828"
COLOR_NEUTRAL = "#616161"

# Monotone Uhr, als Modulattribut, damit Tests die Zeit steuern koennen.
_now = time.monotonic


def _colored(label: QLabel, text: str, color: str | None = None, bold: bool = False) -> None:
    label.setText(text)
    style = []
    if color:
        style.append(f"color: {color};")
    if bold or color:
        style.append("font-weight: bold;")
    label.setStyleSheet(" ".join(style))


SOURCE_LABELS = {"debug": "Debug", "produktiv": "Produktiv"}


def source_label(source: str | None) -> str:
    """Anzeigename der Verfuegbarkeitsquelle; unbekannt/None -> "-"."""
    return SOURCE_LABELS.get(source or "", "-")


FIRMWARE_LABELS = {FW_DEBUG: "Debug", FW_PROD: "Produktiv"}


def firmware_label(kind: str | None) -> str:
    """Anzeigename der Firmware-Art; debug/prod/sonst -> Debug/Produktiv/unbekannt."""
    return FIRMWARE_LABELS.get(kind or "", "unbekannt")


class StatusTab(QWidget):
    """Zeigt Verfuegbarkeit, MQTT-Verbindung und die Werte aus bridge.status."""

    def __init__(self, bridge: MqttBridge):
        super().__init__()
        self._bridge = bridge

        # Vorheriger Stand fuer Frames/s und Fehler-Trend.
        self._prev_rx: int | None = None
        self._prev_rx_time: float | None = None
        self._prev_twai_err: int | None = None

        self._lbl_avail = QLabel()
        self._lbl_fw = QLabel(firmware_label(None))
        self._lbl_source = QLabel("-")
        self._lbl_conn = QLabel()
        self._lbl_rx = QLabel("-")
        self._lbl_tx = QLabel("-")
        self._lbl_fps = QLabel("-")
        self._bar_rx = QProgressBar()
        self._bar_rx.setRange(0, 100)
        self._bar_rx.setValue(0)
        self._bar_rx.setFormat("%p%")
        self._lbl_rx_fill = QLabel("-")
        self._lbl_overflow = QLabel("-")
        self._lbl_twai = QLabel("-")
        self._lbl_bus_off = QLabel("-")
        self._lbl_heap = QLabel("-")

        _colored(self._lbl_avail, "unbekannt", COLOR_NEUTRAL)
        _colored(self._lbl_conn, "getrennt", COLOR_NEUTRAL)

        form = QFormLayout()
        form.addRow("Verfuegbarkeit:", self._lbl_avail)
        form.addRow("Firmware:", self._lbl_fw)
        form.addRow("Quelle:", self._lbl_source)
        form.addRow("MQTT-Verbindung:", self._lbl_conn)
        form.addRow("RX-Frames (gesamt):", self._lbl_rx)
        form.addRow("TX-Frames (gesamt):", self._lbl_tx)
        form.addRow("Frames/s (RX):", self._lbl_fps)
        form.addRow("RX-Puffer:", self._bar_rx)
        form.addRow("RX-Puffer belegt:", self._lbl_rx_fill)
        form.addRow("Overflow:", self._lbl_overflow)
        form.addRow("TWAI-Fehler:", self._lbl_twai)
        form.addRow("Bus-Off:", self._lbl_bus_off)
        form.addRow("Heap (Bytes):", self._lbl_heap)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addStretch(1)

        bridge.availability.connect(self._on_availability)
        bridge.availability_source.connect(self._on_source)
        bridge.firmware_kind.connect(self._on_firmware)
        bridge.connected.connect(self._on_connected)
        bridge.status.connect(self._on_status)

    # ----- Slots -----

    def _on_source(self, source: str) -> None:
        self._lbl_source.setText(source_label(source))

    def _on_firmware(self, kind: str) -> None:
        self._lbl_fw.setText(firmware_label(kind))

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
        now = _now()

        rx = int(st.get("rx", 0))
        tx = int(st.get("tx", 0))
        fill = int(st.get("rx_fill", 0))
        cap = int(st.get("rx_cap", 0))
        overflow = int(st.get("overflow", 0))
        twai_err = int(st.get("twai_err", 0))
        bus_off = bool(st.get("bus_off", False))
        heap = int(st.get("heap", 0))

        # Frames/s aus der Differenz zu vorheriger Meldung. Zaehler-Reset
        # (ESP-Neustart, rx kleiner als zuvor) gilt als neue Basis.
        if self._prev_rx is None or self._prev_rx_time is None or rx < self._prev_rx:
            fps_text = "-"
        else:
            dt = now - self._prev_rx_time
            if dt > 0:
                fps_text = f"{(rx - self._prev_rx) / dt:.1f}"
            else:
                fps_text = "-"
        self._prev_rx = rx
        self._prev_rx_time = now

        self._lbl_rx.setText(str(rx))
        self._lbl_tx.setText(str(tx))
        self._lbl_fps.setText(fps_text)

        percent = int(fill * 100 / cap) if cap > 0 else 0
        percent = max(0, min(100, percent))
        self._bar_rx.setValue(percent)
        self._lbl_rx_fill.setText(f"{fill} / {cap}")
        if overflow > 0:
            _colored(self._lbl_overflow, str(overflow), COLOR_WARN)
        else:
            _colored(self._lbl_overflow, str(overflow), COLOR_OK)

        # Fehler steigend: Warnfarbe nur bei Anstieg gegenueber letzter Meldung.
        rising = self._prev_twai_err is not None and twai_err > self._prev_twai_err
        self._prev_twai_err = twai_err
        if rising:
            _colored(self._lbl_twai, str(twai_err), COLOR_WARN)
        else:
            _colored(self._lbl_twai, str(twai_err))

        if bus_off:
            _colored(self._lbl_bus_off, "JA", COLOR_WARN)
        else:
            _colored(self._lbl_bus_off, "nein", COLOR_OK)

        self._lbl_heap.setText(str(heap))
