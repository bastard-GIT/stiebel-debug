"""Tests fuer Switch 'alle' in Tab Abfrage und Tab Scanner.

'alle' sendet drei normale Kommandos nacheinander (Switch 0xFA, 0xF8, 0xF9); die Firmware
kennt kein 'alle'. Fake-Bridge mit denselben Signalen und Sendemethoden wie MqttBridge.
Qt laeuft offscreen; das Query-Timeout wird ueber tab_query.SEQ_TIMEOUT_MS verkuerzt.

Ausfuehren: C:\\temp\\stiebel-Remote\\.venv\\Scripts\\python.exe -I -B -m pytest C:\\temp\\stiebel-Remote\\mk1-debug\\tests\\test_switch_all.py -q
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

GUI_DIR = Path(__file__).resolve().parent.parent / "gui"
sys.path.insert(0, str(GUI_DIR))

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import tab_query  # noqa: E402
import tab_scanner  # noqa: E402
from elster import ElsterDb  # noqa: E402

FA, F8, F9 = 0xFA, 0xF8, 0xF9
INDEX = 0x0003


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
        self.stops = 0

    def send_read(self, member, index, fmt=None, switch=None):
        self.reads.append((member, index, fmt, switch))

    def send_scan(self, member, from_idx, to_idx, delay_ms, fmt=None, switch=None):
        self.scans.append((member, from_idx, to_idx, delay_ms, fmt, switch))

    def send_stop(self):
        self.stops += 1


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="session")
def db():
    return ElsterDb()


def _wait_until(cond, timeout_ms=2000):
    deadline = time.monotonic() + timeout_ms / 1000.0
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("Timeout beim Warten auf Bedingung")
        QTest.qWait(5)


def _switches(reads):
    return [r[3] for r in reads]


# ---------------------------------------------------------------- Tab Abfrage

def _query_tab(db, fake, switch=tab_query.SWITCH_ALL):
    fake.firmware_kind.emit("debug")
    tab = tab_query.QueryTab(db, fake)
    fake.firmware_kind.emit("debug")
    tab._index_edit.setText(f"0x{INDEX:04x}")
    tab._switch_combo.setCurrentIndex(tab._switch_combo.findData(switch))
    return tab


def _read(tab, switch, status="ok", raw=0x0100):
    return {"member": tab._member_combo.currentData(), "index": INDEX, "raw": raw,
            "status": status, "switch": switch}


def test_query_all_sends_fa_f8_f9_in_order(qapp, db):
    fake = FakeBridge()
    tab = _query_tab(db, fake)
    tab._on_query()
    member = tab._member_combo.currentData()
    assert fake.reads == [(member, INDEX, "auto", FA)]
    fake.read_result.emit(_read(tab, FA))
    assert _switches(fake.reads) == [FA, F8]
    fake.read_result.emit(_read(tab, F8))
    assert _switches(fake.reads) == [FA, F8, F9]
    fake.read_result.emit(_read(tab, F9))
    assert _switches(fake.reads) == [FA, F8, F9]  # keine weiteren Anfragen
    assert tab._query_btn.isEnabled()


def test_query_all_timeout_goes_to_next(qapp, db, monkeypatch):
    monkeypatch.setattr(tab_query, "SEQ_TIMEOUT_MS", 30)
    fake = FakeBridge()
    tab = _query_tab(db, fake)
    tab._on_query()
    fake.read_result.emit(_read(tab, FA))  # aktuell kommt, min kommt nicht
    _wait_until(lambda: len(fake.reads) == 3)
    assert _switches(fake.reads) == [FA, F8, F9]
    fake.read_result.emit(_read(tab, F9))
    assert tab._info_label.text().split(" | ")[1] == "min: keine Antwort"
    assert tab._query_btn.isEnabled()


def test_query_all_summary_line(qapp, db):
    fake = FakeBridge()
    tab = _query_tab(db, fake)
    tab._on_query()
    fake.read_result.emit(_read(tab, FA, raw=0x0100))
    fake.read_result.emit(_read(tab, F8, status="unsupported", raw=None))
    parts = tab._info_label.text().split(" | ")
    assert len(parts) == 3
    assert parts[0].startswith("aktuell: ") and parts[0] != "aktuell: -"
    assert parts[1] == "min: nicht unterstuetzt"
    assert parts[2] == "max: -"  # noch nicht eingetroffen
    fake.read_result.emit(_read(tab, F9, status="timeout", raw=None))
    assert tab._info_label.text().split(" | ")[2] == "max: keine Antwort"


def test_query_all_history_gets_one_row_per_result(qapp, db):
    fake = FakeBridge()
    tab = _query_tab(db, fake)
    tab._on_query()
    for sw in (FA, F8, F9):
        fake.read_result.emit(_read(tab, sw))
    assert tab._table.rowCount() == 3
    sw_col = tab_query.HEADERS.index("Switch")
    labels = {tab._table.item(r, sw_col).text() for r in range(3)}
    assert labels == {"", "min", "max"}  # switch_label(0xFA) ist leer, wie bisher im Verlauf


def test_query_all_button_locked_during_sequence(qapp, db, monkeypatch):
    monkeypatch.setattr(tab_query, "SEQ_TIMEOUT_MS", 30000)
    fake = FakeBridge()
    tab = _query_tab(db, fake)
    tab._on_query()
    assert not tab._query_btn.isEnabled()
    tab._on_query()  # z.B. Enter im Indexfeld: keine zweite Sequenz
    assert len(fake.reads) == 1
    fake.read_result.emit(_read(tab, FA))
    assert not tab._query_btn.isEnabled()
    tab._on_query()
    assert len(fake.reads) == 2
    fake.read_result.emit(_read(tab, F8))
    fake.read_result.emit(_read(tab, F9))
    assert tab._query_btn.isEnabled()


def test_query_all_firmware_prod_aborts(qapp, db, monkeypatch):
    monkeypatch.setattr(tab_query, "SEQ_TIMEOUT_MS", 30)
    fake = FakeBridge()
    tab = _query_tab(db, fake)
    tab._on_query()
    fake.firmware_kind.emit("prod")
    assert not tab._query_btn.isEnabled()
    fake.read_result.emit(_read(tab, FA))
    QTest.qWait(120)  # Timeout-Timer darf nach dem Abbruch nichts mehr senden
    assert len(fake.reads) == 1
    fake.firmware_kind.emit("debug")
    assert tab._query_btn.isEnabled()


def test_query_single_aktuell_sends_one_read(qapp, db):
    fake = FakeBridge()
    tab = _query_tab(db, fake, switch=FA)
    tab._on_query()
    member = tab._member_combo.currentData()
    assert fake.reads == [(member, INDEX, "auto", FA)]
    fake.read_result.emit(_read(tab, FA))
    assert len(fake.reads) == 1
    assert tab._query_btn.isEnabled()


# ---------------------------------------------------------------- Tab Scanner

def _scanner_tab(db, fake, switch=tab_scanner.SWITCH_ALL):
    fake.firmware_kind.emit("debug")
    tab = tab_scanner.ScannerTab(db, fake)
    fake.firmware_kind.emit("debug")
    tab._from.setText("0x4e00")
    tab._to.setText("0x4e0f")  # 16 Indizes je Scan
    tab._switch.setCurrentIndex(tab._switch.findData(switch))
    return tab


def _progress(switch, done, state="running", total=16):
    return {"index": 0x4E00, "done": done, "total": total, "state": state, "switch": switch}


def test_scanner_all_runs_three_scans_in_order(qapp, db):
    fake = FakeBridge()
    tab = _scanner_tab(db, fake)
    tab._on_start()
    member = tab._member.currentData()
    assert fake.scans == [(member, 0x4E00, 0x4E0F, tab._delay.value(), "auto", FA)]
    fake.scan_progress.emit(_progress(FA, 8))
    assert len(fake.scans) == 1  # Scan noch nicht fertig
    fake.scan_progress.emit(_progress(FA, 16, state="done"))
    assert [s[5] for s in fake.scans] == [FA, F8]
    fake.scan_progress.emit(_progress(F8, 16, state="done"))
    assert [s[5] for s in fake.scans] == [FA, F8, F9]
    assert all(s[:5] == fake.scans[0][:5] for s in fake.scans)  # gleicher Bereich, Pause, Format
    fake.scan_progress.emit(_progress(F9, 16, state="done"))
    assert [s[5] for s in fake.scans] == [FA, F8, F9]  # nichts mehr
    assert tab._start_btn.isEnabled()
    assert not tab._stop_btn.isEnabled()


def test_scanner_all_total_progress(qapp, db):
    fake = FakeBridge()
    tab = _scanner_tab(db, fake)
    tab._on_start()
    assert tab._progress.maximum() == 48  # 3 x 16
    fake.scan_progress.emit(_progress(FA, 8))
    assert tab._progress.value() == 8
    fake.scan_progress.emit(_progress(FA, 16, state="done"))
    assert tab._progress.value() == 16
    fake.scan_progress.emit(_progress(F8, 4))
    assert tab._progress.value() == 20
    fake.scan_progress.emit(_progress(F8, 16, state="done"))
    assert tab._progress.value() == 32
    fake.scan_progress.emit(_progress(F9, 16, state="done"))
    assert tab._progress.value() == 48


def test_scanner_all_stop_aborts(qapp, db):
    fake = FakeBridge()
    tab = _scanner_tab(db, fake)
    tab._on_start()
    tab._on_stop()
    assert fake.stops == 1
    fake.scan_progress.emit(_progress(FA, 16, state="done"))
    assert len(fake.scans) == 1  # kein weiterer Scan nach Stopp
    assert not tab._stop_btn.isEnabled()


def test_scanner_all_firmware_stopped_state_ends_sequence(qapp, db):
    fake = FakeBridge()
    tab = _scanner_tab(db, fake)
    tab._on_start()
    fake.scan_progress.emit(_progress(FA, 4, state="stopped"))
    fake.scan_progress.emit(_progress(FA, 16, state="done"))
    assert len(fake.scans) == 1
    assert tab._start_btn.isEnabled()


def test_scanner_all_results_keyed_by_index_and_switch(qapp, db):
    fake = FakeBridge()
    tab = _scanner_tab(db, fake)
    tab._on_start()
    for sw in (FA, F8, F9):
        fake.scan_result.emit({
            "format": "auto", "switch": sw,
            "items": [{"index": INDEX, "raw": 0x0100, "status": "ok", "switch": sw}],
        })
        if sw != F9:
            fake.scan_progress.emit(_progress(sw, 16, state="done"))
    assert len(tab._items) == 3
    assert tab._table.rowCount() == 3


def test_scanner_all_estimate_counts_three_scans(qapp, db):
    fake = FakeBridge()
    tab = _scanner_tab(db, fake, switch=tab_scanner.SWITCH_ALL)
    tab._from.setText("0x0000")
    tab._to.setText("0x0fff")  # 4096 Indizes je Scan
    assert "12288 Indizes" in tab._estimate.text()
    tab._switch.setCurrentIndex(tab._switch.findData(FA))
    assert "4096 Indizes" in tab._estimate.text()
    assert "Scans" not in tab._estimate.text()


def test_scanner_all_warning_uses_total(qapp, db, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: warnings.append(a) or QMessageBox.No)
    fake = FakeBridge()
    tab = _scanner_tab(db, fake)
    tab._from.setText("0x0000")
    tab._to.setText("0x0aff")  # 2816 je Scan: einzeln unter 4096, als 'alle' 8448
    tab._on_start()
    assert len(warnings) == 1
    assert "8448" in warnings[0][2]
    assert fake.scans == []
    tab._switch.setCurrentIndex(tab._switch.findData(FA))
    tab._on_start()
    assert len(warnings) == 1  # Einzelscan unter Limit: keine Warnung
    assert len(fake.scans) == 1
