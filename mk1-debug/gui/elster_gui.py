"""Hauptfenster der mk1-debug-GUI (INTERFACES.md, Abschnitt 5.3 elster_gui.py).

Start: .venv\\Scripts\\python.exe -I gui\\elster_gui.py
Die MQTT-Daten (Host, Port, Benutzer, Passwort) kommen aus mk1-debug/secrets.yaml (oder $STIEBEL_SECRETS); sie sind im Verbindungsdialog vorbelegt und aenderbar, werden nicht gespeichert und nicht geloggt.
"""

from __future__ import annotations

import os
import sys

# Mit -I ist das Skriptverzeichnis nicht im sys.path: Module aus gui/ explizit einbinden.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QSpinBox,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
)

from elster import ElsterDb
from export_io import load_frames_jsonl, save_frames_csv, save_frames_jsonl
from mqtt_secrets import load_mqtt_settings
from mqtt_client import FW_DEBUG, FW_PROD, FW_UNKNOWN, MqttBridge
from tab_live import LiveTab
from tab_query import QueryTab
from tab_scanner import ScannerTab
from tab_status import StatusTab, firmware_label

DEFAULT_HOST = os.environ.get("STIEBEL_MQTT_HOST", "localhost")
DEFAULT_PORT = 31883
DEFAULT_USER = os.environ.get("STIEBEL_MQTT_USER", "")


class ConnectDialog(QDialog):
    """Abfrage der Verbindungsdaten. Nichts davon wird gespeichert."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mit MQTT-Broker verbinden")

        # Vorbelegung aus mk1-debug/secrets.yaml (oder $STIEBEL_SECRETS), sonst feste Standardwerte
        host, port, user, password = load_mqtt_settings() or (DEFAULT_HOST, DEFAULT_PORT, DEFAULT_USER, "")
        self._host = QLineEdit(host)
        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(port)
        self._user = QLineEdit(user)
        self._password = QLineEdit(password)
        self._password.setEchoMode(QLineEdit.EchoMode.Password)

        form = QFormLayout()
        form.addRow("Broker-Host:", self._host)
        form.addRow("Port:", self._port)
        form.addRow("Benutzer:", self._user)
        form.addRow("Passwort:", self._password)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, int, str, str]:
        return (
            self._host.text().strip(),
            self._port.value(),
            self._user.text().strip(),
            self._password.text(),
        )


class MainWindow(QMainWindow):
    def __init__(self, db: ElsterDb, bridge: MqttBridge):
        super().__init__()
        self._db = db
        self._bridge = bridge
        self._connected = False
        self._esp = "unbekannt"
        self._fw = FW_UNKNOWN  # Firmware-Art aus stiebel-debug/status: debug | prod | unknown

        self.setWindowTitle("mk1-debug GUI")
        self.resize(1200, 800)

        self.live_tab = LiveTab(db, bridge)
        self.query_tab = QueryTab(db, bridge)
        self.scanner_tab = ScannerTab(db, bridge)
        self.status_tab = StatusTab(bridge)

        self._tabs = QTabWidget()
        self._tabs.addTab(self.live_tab, "Live")
        self._tabs.addTab(self.query_tab, "Abfrage")
        self._tabs.addTab(self.scanner_tab, "Scanner")
        self._tabs.addTab(self.status_tab, "Status")
        self.setCentralWidget(self._tabs)

        self._build_menu()

        self._lbl_conn = QLabel()
        self._lbl_esp = QLabel()
        bar = QStatusBar()
        bar.addPermanentWidget(self._lbl_conn)
        bar.addPermanentWidget(self._lbl_esp)
        self.setStatusBar(bar)
        self._update_status_bar()

        bridge.connected.connect(self._on_connected)
        bridge.availability.connect(self._on_availability)
        bridge.firmware_kind.connect(self._on_firmware)

    # ----- Menue -----------------------------------------------------------

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("&Datei")

        act_connect = QAction("Verbinden...", self)
        act_connect.triggered.connect(self._on_connect)
        menu.addAction(act_connect)

        act_disconnect = QAction("Trennen", self)
        act_disconnect.triggered.connect(self._on_disconnect)
        menu.addAction(act_disconnect)
        menu.addSeparator()

        act_csv = QAction("Mitschnitt exportieren (CSV)", self)
        act_csv.triggered.connect(self._on_export_csv)
        menu.addAction(act_csv)

        act_jsonl = QAction("Mitschnitt exportieren (JSONL)", self)
        act_jsonl.triggered.connect(self._on_export_jsonl)
        menu.addAction(act_jsonl)

        act_load = QAction("Offline-Log laden (JSONL)", self)
        act_load.triggered.connect(self._on_load_offline)
        menu.addAction(act_load)
        menu.addSeparator()

        act_quit = QAction("Beenden", self)
        act_quit.triggered.connect(self.close)
        menu.addAction(act_quit)

    # ----- Verbindung ------------------------------------------------------

    def _on_connect(self) -> None:
        dlg = ConnectDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            dlg.deleteLater()
            return
        host, port, user, password = dlg.values()
        dlg.deleteLater()  # Passwort-Widget mit dem Dialog verwerfen

        if not host:
            QMessageBox.warning(self, "Verbinden", "Bitte einen Broker-Host angeben.")
            return
        self._bridge.connect_to(host, port, user, password)
        self.statusBar().showMessage(f"Verbindung zu {host}:{port} wird aufgebaut ...", 5000)

    def _on_disconnect(self) -> None:
        self._bridge.disconnect_from()
        self._connected = False
        self._esp = "unbekannt"
        self._fw = FW_UNKNOWN
        self._update_status_bar()

    def _on_connected(self, ok: bool) -> None:
        self._connected = bool(ok)
        if not ok:
            self._esp = "unbekannt"
            self._fw = FW_UNKNOWN
        self._update_status_bar()

    def _on_availability(self, text: str) -> None:
        self._esp = text
        self._update_status_bar()

    def _on_firmware(self, kind: str) -> None:
        self._fw = kind
        self._update_status_bar()

    def _update_status_bar(self) -> None:
        self._lbl_conn.setText("MQTT: verbunden" if self._connected else "MQTT: getrennt")
        esp_text = f"ESP: {self._esp}"
        if self._fw in (FW_DEBUG, FW_PROD):
            esp_text += f" ({firmware_label(self._fw)})"
        self._lbl_esp.setText(esp_text)

    # ----- Export / Offline-Log -------------------------------------------

    def _on_export_csv(self) -> None:
        rows = self.live_tab.rows()
        if not rows:
            QMessageBox.information(self, "Export", "Keine Frames im Mitschnitt.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Mitschnitt exportieren (CSV)", "mitschnitt.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            save_frames_csv(path, rows, self._db)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Export", f"Speichern fehlgeschlagen:\n{exc}")
            return
        self.statusBar().showMessage(f"{len(rows)} Frames exportiert: {path}", 5000)

    def _on_export_jsonl(self) -> None:
        rows = self.live_tab.rows()
        if not rows:
            QMessageBox.information(self, "Export", "Keine Frames im Mitschnitt.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Mitschnitt exportieren (JSONL)", "mitschnitt.jsonl", "JSONL (*.jsonl)"
        )
        if not path:
            return
        try:
            save_frames_jsonl(path, rows)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Export", f"Speichern fehlgeschlagen:\n{exc}")
            return
        self.statusBar().showMessage(f"{len(rows)} Frames exportiert: {path}", 5000)

    def _on_load_offline(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Offline-Log laden (JSONL)", "", "JSONL (*.jsonl);;Alle Dateien (*)"
        )
        if not path:
            return
        self._load_offline_log(path)

    def _load_offline_log(self, path: str) -> int:
        """Liest eine JSONL-Datei und zeigt sie im Live-Tab. Gibt die Anzahl Frames zurueck."""
        try:
            rows = load_frames_jsonl(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Offline-Log", f"Laden fehlgeschlagen:\n{exc}")
            return 0
        self.live_tab.load_rows(rows)
        self._tabs.setCurrentWidget(self.live_tab)
        self.statusBar().showMessage(f"{len(rows)} Frames geladen: {path}", 5000)
        return len(rows)

    # ----- Beenden ---------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._bridge.disconnect_from()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    db = ElsterDb()
    bridge = MqttBridge()
    win = MainWindow(db, bridge)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
