"""Hauptfenster der mk1-i2c-GUI: Verbindung, Sniff-Steuerung, Tabs, Datei-Menue.

Start: .venv\\Scripts\\python.exe -I gui\\i2c_gui.py [--load DATEI]
Beim Start wird NICHT verbunden. Das Passwort steht nur im Eingabefeld, es wird nie gespeichert
oder geloggt. Eingehende Frames kommen ueber bridge.rx_frames (Qt-Signal aus dem paho-Thread)
und landen im DataModel (lwz180.decode). Die Tabs werden im 250-ms-Takt aktualisiert.
"""
from __future__ import annotations

import argparse
import os
import sys

# Mit -I ist das Skriptverzeichnis nicht im sys.path: Module aus gui/ explizit einbinden.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from data_model import DataModel  # noqa: E402
from export_io import (  # noqa: E402
    compare_frames,
    load_frames_jsonl,
    load_saleae_i2c_csv,
    save_frames_csv,
    save_frames_jsonl,
)
from logic_import import detect_format, load_logic, raw_channels  # noqa: E402
from lwz180 import decode  # noqa: E402
from mqtt_bridge import MqttBridge  # noqa: E402
from mqtt_secrets import load_mqtt_settings  # noqa: E402
from tab_bedienteil import BedienteilTab  # noqa: E402
from tab_display import DisplayTab  # noqa: E402
from tab_live import LiveTab  # noqa: E402
from tab_status import StatusTab  # noqa: E402
from tab_werte import WerteTab  # noqa: E402

DEFAULT_HOST = os.environ.get("STIEBEL_MQTT_HOST", "localhost")   # Broker (keine Zugangsdaten)
DEFAULT_PORT = 31883
FLUSH_MS = 250
SNIFF_MODES = ("changed", "all")  # Firmware-Standard ist changed


class I2cMainWindow(QMainWindow):
    """Hauptfenster mit Verbindungsleiste, Sniff-Steuerung, fuenf Tabs und Datei-Menue."""

    def __init__(self, bridge: MqttBridge | None = None, model: DataModel | None = None) -> None:
        super().__init__()
        self.setWindowTitle("mk1-i2c Auswertung")
        self.resize(1400, 900)
        self.model = model if model is not None else DataModel(decode=decode)
        self.bridge = bridge if bridge is not None else MqttBridge(self)
        self.last_compare: dict | None = None
        self._connected = False
        self._new_rows: list[dict] = []
        self._last_dir = ""

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(self._build_connection_bar())
        layout.addLayout(self._build_sniff_bar())

        self._tabs = QTabWidget()
        self.live = LiveTab(self.model, self.bridge)
        self.werte = WerteTab(self.model, self.bridge)
        self.display = DisplayTab(self.model, self.bridge)
        self.bedienteil = BedienteilTab(self.model, self.bridge)
        self.status = StatusTab(self.model, self.bridge)
        self._tabs.addTab(self.live, "Live")
        self._tabs.addTab(self.werte, "Werte")
        self._tabs.addTab(self.display, "Display")
        self._tabs.addTab(self.bedienteil, "Bedienteil")
        self._tabs.addTab(self.status, "Status")
        layout.addWidget(self._tabs, 1)
        self.setCentralWidget(central)

        self._build_menu()

        self.bridge.rx_frames.connect(self._on_rx_frames)
        self.bridge.connected.connect(self._on_connected)

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(FLUSH_MS)
        self._flush_timer.timeout.connect(self._flush)
        self._flush_timer.start()

        self._update_status("Bereit, nicht verbunden")

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------
    def _build_connection_bar(self) -> QHBoxLayout:
        self._host = QLineEdit(DEFAULT_HOST)
        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(DEFAULT_PORT)
        self._user = QLineEdit()
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._connect_btn = QPushButton("Verbinden")
        self._secrets_btn = QPushButton("Aus secrets laden")
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        self._secrets_btn.clicked.connect(self._on_load_secrets)

        row = QHBoxLayout()
        row.addWidget(QLabel("Broker:"))
        row.addWidget(self._host, 2)
        row.addWidget(QLabel("Port:"))
        row.addWidget(self._port)
        row.addWidget(QLabel("Benutzer:"))
        row.addWidget(self._user, 1)
        row.addWidget(QLabel("Passwort:"))
        row.addWidget(self._password, 1)
        row.addWidget(self._connect_btn)
        row.addWidget(self._secrets_btn)
        return row

    def _build_sniff_bar(self) -> QHBoxLayout:
        self._sniff_check = QCheckBox("Streaming an")
        self._sniff_check.setChecked(True)  # Firmware-Standard: on
        self._mode = QComboBox()
        self._mode.addItems(list(SNIFF_MODES))
        self._sniff_check.toggled.connect(self._send_sniff)
        self._mode.currentIndexChanged.connect(self._send_sniff)

        row = QHBoxLayout()
        row.addWidget(self._sniff_check)
        row.addWidget(QLabel("Modus:"))
        row.addWidget(self._mode)
        row.addStretch(1)
        return row

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("&Datei")
        actions = [
            ("CSV exportieren...", self._export_csv),
            ("JSONL exportieren...", self._export_jsonl),
            None,
            ("Offline-Log laden...", self._open_offline),
            ("Logic-Daten laden (Saleae Raw oder I2C-Analyzer)...", self._open_logic),
            ("Logic-Raw laden, Kanaele waehlen...", self._open_logic_manual),
            ("Saleae-CSV importieren und vergleichen...", self._import_saleae_dialog),
            None,
            ("Beenden", self.close),
        ]
        for item in actions:
            if item is None:
                menu.addSeparator()
                continue
            text, slot = item
            act = QAction(text, self)
            act.triggered.connect(slot)
            menu.addAction(act)

    # ------------------------------------------------------------------
    # Verbindung und Sniff
    # ------------------------------------------------------------------
    def _on_connect_clicked(self) -> None:
        if self._connected:
            self.bridge.disconnect_from()
            self._set_connected(False)
            return
        host = self._host.text().strip()
        if not host:
            self._update_status("Kein Broker angegeben")
            return
        self.bridge.connect_to(host, self._port.value(), self._user.text().strip(), self._password.text())
        self._update_status("Verbinde ...")

    def _on_load_secrets(self) -> None:
        settings = load_mqtt_settings()
        if settings is None:
            self._update_status("secrets.yaml fehlt oder ist unvollstaendig")
            return
        host, port, user, password = settings
        self._host.setText(host)
        self._port.setValue(port)
        self._user.setText(user)
        self._password.setText(password)
        self._update_status("Zugangsdaten aus secrets.yaml in die Felder geladen (nicht gespeichert)")

    def _on_connected(self, ok: bool) -> None:
        self._set_connected(bool(ok))

    def _set_connected(self, ok: bool) -> None:
        self._connected = ok
        self._connect_btn.setText("Trennen" if ok else "Verbinden")
        self._update_status("Verbunden mit MQTT-Broker" if ok else "Nicht verbunden")

    def _send_sniff(self, *_args) -> None:
        mode = self._mode.currentText()
        if not self._connected:
            self._update_status("Nicht verbunden: Sniff-Befehl nicht gesendet")
            return
        self.bridge.send_sniff(self._sniff_check.isChecked(), mode)
        state = "an" if self._sniff_check.isChecked() else "aus"
        self._update_status(f"Streaming {state}, Modus {mode} gesendet")

    # ------------------------------------------------------------------
    # Daten
    # ------------------------------------------------------------------
    def _on_rx_frames(self, rows: list) -> None:
        self.model.add_frames(rows)
        self._new_rows.extend(rows)

    def _tab_list(self) -> list:
        return [self.live, self.werte, self.display, self.bedienteil, self.status]

    def _flush(self) -> None:
        """Neue Frames im Takt an die Tabs geben."""
        if not self._new_rows:
            return
        batch = self._new_rows
        self._new_rows = []
        for tab in self._tab_list():
            tab.on_frames(batch)

    def refresh_all(self) -> None:
        for tab in self._tab_list():
            tab.refresh()

    def load_offline(self, path: str) -> int:
        """JSONL-Log laden. Ersetzt den aktuellen Datenbestand. Rueckgabe: Anzahl Frames."""
        rows = load_frames_jsonl(path)
        self.model.clear()
        self._new_rows.clear()
        self.model.add_frames(rows)
        self.refresh_all()
        self._update_status(f"Offline-Log geladen: {len(rows)} Frames aus {os.path.basename(path)}")
        return len(rows)

    def load_logic(self, path: str, sda: str | None = None, scl: str | None = None) -> dict:
        """Saleae-Logic-Daten (Raw oder Analyzer) dekodieren und anzeigen. Ersetzt den Datenbestand.

        Rueckgabe: info aus logic_import (Kanaele, Takt, Zaehler). ValueError bei unbekanntem Format.
        """
        rows, info = load_logic(path, sda, scl)
        self.model.clear()
        self._new_rows.clear()
        self.model.add_frames(rows)
        self.refresh_all()
        self._update_status(f"Logic-Daten geladen: {len(rows)} Frames aus {os.path.basename(path)}")
        return info

    def import_saleae(self, path: str) -> dict:
        """Saleae-I2C-CSV als Referenz einlesen und mit dem aktuellen Mitschnitt vergleichen.

        Rueckgabe: Ergebnis von compare_frames (ohne Dialog). Der Dialog steht im Menue-Handler.
        """
        reference = load_saleae_i2c_csv(path)
        result = compare_frames(self.model.frames, reference)
        self.last_compare = result
        return result

    # ------------------------------------------------------------------
    # Datei-Menue
    # ------------------------------------------------------------------
    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "CSV exportieren", self._last_dir, "CSV (*.csv)")
        if not path:
            return
        try:
            save_frames_csv(path, self.model.frames, decode=decode)
        except OSError as exc:
            QMessageBox.warning(self, "Export fehlgeschlagen", str(exc))
            return
        self._update_status(f"CSV exportiert: {len(self.model.frames)} Frames")

    def _export_jsonl(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "JSONL exportieren", self._last_dir, "JSONL (*.jsonl)")
        if not path:
            return
        try:
            save_frames_jsonl(path, self.model.frames)
        except OSError as exc:
            QMessageBox.warning(self, "Export fehlgeschlagen", str(exc))
            return
        self._update_status(f"JSONL exportiert: {len(self.model.frames)} Frames")

    def _open_offline(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Offline-Log laden", self._last_dir,
                                              "JSONL (*.jsonl *.json);;Alle Dateien (*)")
        if not path:
            return
        self._last_dir = os.path.dirname(path)
        try:
            self.load_offline(path)
        except OSError as exc:
            QMessageBox.warning(self, "Laden fehlgeschlagen", str(exc))

    def _pick_logic_file(self) -> str:
        path, _ = QFileDialog.getOpenFileName(self, "Logic-Daten laden", self._last_dir,
                                              "Saleae-Export (*.csv *.txt);;Alle Dateien (*)")
        if path:
            self._last_dir = os.path.dirname(path)
        return path

    def _run_logic_load(self, path: str, sda: str | None = None, scl: str | None = None) -> None:
        try:
            info = self.load_logic(path, sda, scl)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Laden fehlgeschlagen", str(exc))
            return
        QMessageBox.information(self, "Logic-Daten", _logic_text(info, self.model))

    def _open_logic(self) -> None:
        path = self._pick_logic_file()
        if path:
            self._run_logic_load(path)

    def _open_logic_manual(self) -> None:
        path = self._pick_logic_file()
        if not path:
            return
        try:
            if detect_format(path) != "raw":
                QMessageBox.warning(self, "Kein Raw-Export", "Kanalwahl nur fuer Saleae-Raw-Exporte (digital.csv).")
                return
            chans, edges = raw_channels(path)
        except OSError as exc:
            QMessageBox.warning(self, "Laden fehlgeschlagen", str(exc))
            return
        labels = [f"{c}  ({edges[c]} Flanken)" for c in chans]
        order = sorted(range(len(chans)), key=lambda i: -edges[chans[i]])
        scl_lbl, ok = QInputDialog.getItem(self, "Kanal waehlen", "SCL:", labels, order[0], False)
        if not ok:
            return
        sda_lbl, ok = QInputDialog.getItem(self, "Kanal waehlen", "SDA:", labels,
                                           order[1] if len(order) > 1 else 0, False)
        if not ok:
            return
        self._run_logic_load(path, chans[labels.index(sda_lbl)], chans[labels.index(scl_lbl)])

    def _import_saleae_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Saleae-CSV importieren", self._last_dir,
                                              "CSV (*.csv);;Alle Dateien (*)")
        if not path:
            return
        self._last_dir = os.path.dirname(path)
        try:
            result = self.import_saleae(path)
        except OSError as exc:
            QMessageBox.warning(self, "Import fehlgeschlagen", str(exc))
            return
        QMessageBox.information(self, "Vergleich Saleae", _compare_text(result))
        self._update_status(f"Saleae-Vergleich: Trefferquote {result['match_rate'] * 100:.1f} %")

    # ------------------------------------------------------------------
    # Sonstiges
    # ------------------------------------------------------------------
    def _update_status(self, text: str) -> None:
        self.statusBar().showMessage(f"{text}  |  Frames im Modell: {len(self.model.frames)}")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt-Name)
        self.bridge.disconnect_from()
        event.accept()


def _compare_text(result: dict) -> str:
    """Kurztext fuer den Vergleichsdialog (ohne Zugangsdaten, nur Frame-Bytes)."""
    lines = [
        f"Treffer: {result['matched']}",
        f"Fehlend (nur Referenz): {len(result['missing'])}",
        f"Zusaetzlich (nur Mitschnitt): {len(result['extra'])}",
        f"Abweichend: {len(result['different'])}",
        f"Trefferquote: {result['match_rate'] * 100:.1f} %",
    ]
    for label, rows in (("Fehlend", result["missing"]), ("Zusaetzlich", result["extra"])):
        for row in rows[:5]:
            lines.append(f"{label}: {bytes(row['data']).hex(' ')}")
    return "\n".join(lines)


def _logic_text(info: dict, model: DataModel) -> str:
    """Kurztext fuer den Logic-Dialog: Format, Kanaele, Takt, Fehler, Paketarten, CRC."""
    lines = [f"Format: {info['format']}", f"Frames: {info['frames']}", f"Dauer: {info.get('duration_s')} s"]
    if info["format"] == "raw":
        c = info["counters"]
        lines += [
            f"SCL: {info['scl']}   SDA: {info['sda']}",
            f"SCL-Takt: {info['scl_khz']} kHz" if info.get("scl_khz") else "SCL-Takt: unbekannt",
            f"Flanken: " + ", ".join(f"{k}={v}" for k, v in info["edges"].items() if v),
            f"Fehler: Bits {c['err_bits']}, ohne STOP {c['err_nostop']}, Ueberlaenge {c['err_overlen']}",
        ]
    lines.append("Pakete: " + (", ".join(f"{h}: {n}" for h, n in sorted(model.counts_by_header.items())) or "-"))
    lines.append(f"CRC: {model.crc_total - model.crc_bad} ok, {model.crc_bad} Fehler")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="mk1-i2c Auswertungs-GUI")
    ap.add_argument("--load", metavar="DATEI",
                    help="beim Start laden: Offline-Log (JSONL) oder Saleae-Export (CSV/TXT)")
    args = ap.parse_args(argv)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    win = I2cMainWindow()
    if args.load:
        try:
            if args.load.lower().endswith((".csv", ".txt")):
                win.load_logic(args.load)
            else:
                win.load_offline(args.load)
        except (OSError, ValueError) as exc:
            sys.stderr.write(f"Laden fehlgeschlagen: {exc}\n")
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
