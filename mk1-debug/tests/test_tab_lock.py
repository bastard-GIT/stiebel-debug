"""Tests fuer die Firmware-Sperre der Tabs Abfrage/Scanner, Status-Tab und Statuszeile.

Fake-Bridge mit denselben Signalen wie MqttBridge; Qt laeuft offscreen.

Ausfuehren: C:\\temp\\stiebel-Remote\\.venv\\Scripts\\python.exe -I -B -m pytest C:\\temp\\stiebel-Remote\\mk1-debug\\tests\\test_tab_lock.py -q
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

GUI_DIR = Path(__file__).resolve().parent.parent / "gui"
sys.path.insert(0, str(GUI_DIR))

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import elster_gui  # noqa: E402
import tab_query  # noqa: E402
import tab_scanner  # noqa: E402
import tab_status  # noqa: E402
from elster import ElsterDb  # noqa: E402

PROD_TEXT = "Nur mit Debug-Firmware moeglich: die Produktiv-Firmware beantwortet keine Abfragen."
UNKNOWN_TEXT = "Warte auf Status der Firmware..."


class FakeBridge(QObject):
    connected = Signal(bool)
    rx_frames = Signal(list)
    read_result = Signal(dict)
    scan_progress = Signal(dict)
    scan_result = Signal(dict)
    status = Signal(dict)
    availability = Signal(str)
    availability_source = Signal(str)
    firmware_kind = Signal(str)

    def __init__(self):
        super().__init__()
        self.reads = []
        self.scans = []

    def send_read(self, member, index, fmt=None, switch=None):
        self.reads.append((member, index, fmt, switch))

    def send_scan(self, member, from_idx, to_idx, delay_ms, fmt=None, switch=None):
        self.scans.append((member, from_idx, to_idx, delay_ms, fmt, switch))

    def send_stop(self):
        pass


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="session")
def db():
    return ElsterDb()


def _query_controls(tab):
    return [tab._member_combo, tab._index_edit, tab._format_combo, tab._switch_combo, tab._query_btn]


def _scanner_controls(tab):
    return [tab._member, tab._from, tab._to, tab._format, tab._switch, tab._delay, tab._start_btn]


def _assert_query_locked(tab, text):
    assert all(not w.isEnabled() for w in _query_controls(tab))
    assert not tab._fw_label.isHidden()
    assert tab._fw_label.text() == text


def _assert_query_open(tab):
    assert all(w.isEnabled() for w in _query_controls(tab))
    assert tab._fw_label.isHidden()


def _assert_scanner_locked(tab, text):
    assert all(not w.isEnabled() for w in _scanner_controls(tab))
    assert not tab._fw_label.isHidden()
    assert tab._fw_label.text() == text


def _assert_scanner_open(tab):
    assert all(w.isEnabled() for w in _scanner_controls(tab))
    assert tab._fw_label.isHidden()


# ---------------------------------------------------------------- Tab Abfrage

def test_query_locked_when_unknown(qapp, db):
    fake = FakeBridge()
    tab = tab_query.QueryTab(db, fake)
    _assert_query_locked(tab, UNKNOWN_TEXT)


def test_query_locked_when_prod(qapp, db):
    fake = FakeBridge()
    tab = tab_query.QueryTab(db, fake)
    fake.firmware_kind.emit("prod")
    _assert_query_locked(tab, PROD_TEXT)


def test_query_open_when_debug(qapp, db):
    fake = FakeBridge()
    tab = tab_query.QueryTab(db, fake)
    fake.firmware_kind.emit("debug")
    _assert_query_open(tab)


def test_query_relocks_when_prod_after_debug(qapp, db):
    fake = FakeBridge()
    tab = tab_query.QueryTab(db, fake)
    fake.firmware_kind.emit("debug")
    fake.firmware_kind.emit("prod")
    _assert_query_locked(tab, PROD_TEXT)
    fake.firmware_kind.emit("unknown")
    _assert_query_locked(tab, UNKNOWN_TEXT)


# ---------------------------------------------------------------- Tab Scanner

def test_scanner_locked_when_unknown(qapp, db):
    fake = FakeBridge()
    tab = tab_scanner.ScannerTab(db, fake)
    _assert_scanner_locked(tab, UNKNOWN_TEXT)


def test_scanner_locked_when_prod(qapp, db):
    fake = FakeBridge()
    tab = tab_scanner.ScannerTab(db, fake)
    fake.firmware_kind.emit("prod")
    _assert_scanner_locked(tab, PROD_TEXT)


def test_scanner_open_when_debug(qapp, db):
    fake = FakeBridge()
    tab = tab_scanner.ScannerTab(db, fake)
    fake.firmware_kind.emit("debug")
    _assert_scanner_open(tab)
    fake.firmware_kind.emit("prod")
    _assert_scanner_locked(tab, PROD_TEXT)


def test_scanner_stop_stays_usable_while_scanning(qapp, db):
    fake = FakeBridge()
    tab = tab_scanner.ScannerTab(db, fake)
    fake.firmware_kind.emit("debug")
    tab._on_start()
    assert not tab._start_btn.isEnabled()
    assert tab._stop_btn.isEnabled()
    fake.firmware_kind.emit("prod")
    assert not tab._start_btn.isEnabled()
    assert tab._stop_btn.isEnabled()
    tab._on_stop()
    assert all(not w.isEnabled() for w in _scanner_controls(tab))  # prod: gesperrt
    assert not tab._stop_btn.isEnabled()


def test_scanner_reopens_after_scan_when_debug(qapp, db):
    fake = FakeBridge()
    tab = tab_scanner.ScannerTab(db, fake)
    fake.firmware_kind.emit("debug")
    tab._on_start()
    fake.scan_progress.emit({"index": 1, "done": 1, "total": 1, "switch": 0xFA})  # Scan fertig
    _assert_scanner_open(tab)
    assert not tab._stop_btn.isEnabled()


# ---------------------------------------------------------------- Status-Tab

@pytest.mark.parametrize("kind, text", [
    ("debug", "Debug"),
    ("prod", "Produktiv"),
    ("unknown", "unbekannt"),
])
def test_status_tab_firmware_label(qapp, kind, text):
    fake = FakeBridge()
    tab = tab_status.StatusTab(fake)
    fake.firmware_kind.emit(kind)
    assert tab._lbl_fw.text() == text


# ---------------------------------------------------------------- Statuszeile

def test_status_bar_shows_firmware(qapp, db):
    fake = FakeBridge()
    win = elster_gui.MainWindow(db, fake)
    fake.availability.emit("online")
    assert win._lbl_esp.text() == "ESP: online"
    fake.firmware_kind.emit("debug")
    assert win._lbl_esp.text() == "ESP: online (Debug)"
    fake.firmware_kind.emit("prod")
    assert win._lbl_esp.text() == "ESP: online (Produktiv)"
    fake.firmware_kind.emit("unknown")
    assert win._lbl_esp.text() == "ESP: online"
