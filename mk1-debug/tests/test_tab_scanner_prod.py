"""Tests fuer den Scanner-Tab bei Produktiv-Firmware und die Teilnehmer-Suche (scan/members).

Fake-Bridge mit denselben Signalen und Sendemethoden wie MqttBridge; es gibt keine MQTT-Verbindung.
Qt laeuft offscreen. Dateien fuer den CSV-Export landen in tmp_path.

Ausfuehren: QT_QPA_PLATFORM=offscreen python -I -m pytest tests/test_tab_scanner_prod.py -q
"""

import csv
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

GUI_DIR = Path(__file__).resolve().parent.parent / "gui"
sys.path.insert(0, str(GUI_DIR))

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import tab_scanner  # noqa: E402
from elster import ElsterDb  # noqa: E402

FA, F8, F9 = 0xFA, 0xF8, 0xF9
PROD_TEXT_START = "Im Produktiv-Modus nicht verfuegbar"
UNKNOWN_TEXT = "Warte auf Status der Firmware..."


class FakeBridge(QObject):
    connected = Signal(bool)
    rx_frames = Signal(list)
    read_result = Signal(dict)
    scan_progress = Signal(dict)
    scan_result = Signal(dict)
    member_result = Signal(dict)
    status = Signal(dict)
    availability = Signal(str)
    availability_source = Signal(str)
    firmware_kind = Signal(str)
    can_debug_state = Signal(bool)

    def __init__(self):
        super().__init__()
        self.scans = []
        self.member_scans = []
        self.stops = 0
        self.member_error = None  # Wenn gesetzt, wirft send_member_scan ValueError

    def send_scan(self, member, from_idx, to_idx, delay_ms, fmt=None, switch=None):
        self.scans.append((member, from_idx, to_idx, delay_ms, fmt, switch))

    def send_member_scan(self, lo=0, hi=0x7FF, index=0x000B):
        if self.member_error is not None:
            raise ValueError(self.member_error)
        self.member_scans.append((lo, hi, index))

    def send_stop(self):
        self.stops += 1


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="session")
def db():
    return ElsterDb()


@pytest.fixture
def warnings(monkeypatch):
    calls = []
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: calls.append(a) or QMessageBox.No)
    return calls


def _tab(db, fake, fw="prod"):
    tab = tab_scanner.ScannerTab(db, fake)
    fake.firmware_kind.emit(fw)
    return tab


def _range_tab(db, fake, fw="prod", switch=FA):
    """Tab mit kleinem Bereich 0x4e00..0x4e0f (16 Indizes)."""
    tab = _tab(db, fake, fw)
    tab._from.setText("0x4e00")
    tab._to.setText("0x4e0f")
    tab._switch.setCurrentIndex(tab._switch.findData(switch))
    return tab


def _progress(switch, done, state="running", total=16, mode="range"):
    return {"mode": mode, "state": state, "done": done, "total": total, "switch": switch,
            "member": "KESSEL", "hits": 0, "seconds": 1}


def _member_progress(done, state="running", total=2048):
    return {"mode": "members", "state": state, "done": done, "total": total, "switch": FA,
            "member": "members", "hits": 0, "seconds": 1}


# ---------------------------------------------------------------- Sperre und Format/Pause

def test_lock_unknown_closes_everything(qapp, db):
    fake = FakeBridge()
    tab = tab_scanner.ScannerTab(db, fake)
    assert not tab._start_btn.isEnabled()
    assert not tab._member_hex.isEnabled()
    assert not tab._format.isEnabled()
    assert not tab._delay.isEnabled()
    assert not tab._mem_start_btn.isEnabled()
    assert not tab._fw_label.isHidden()
    assert tab._fw_label.text() == UNKNOWN_TEXT


def test_lock_debug_opens_format_and_delay_without_tooltip(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake, "debug")
    assert tab._start_btn.isEnabled()
    assert tab._format.isEnabled()
    assert tab._delay.isEnabled()
    assert tab._format.toolTip() == ""
    assert tab._delay.toolTip() == ""
    assert tab._fw_label.isHidden()


def test_lock_prod_disables_format_and_delay_with_tooltip(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake, "prod")
    assert not tab._format.isEnabled()
    assert not tab._delay.isEnabled()
    assert tab._format.toolTip().startswith(PROD_TEXT_START)
    assert tab._delay.toolTip().startswith(PROD_TEXT_START)
    assert "150 ms" in tab._delay.toolTip()


def test_lock_prod_keeps_start_and_inputs_open(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake, "prod")
    for widget in (tab._member, tab._member_hex, tab._from, tab._to, tab._switch, tab._start_btn):
        assert widget.isEnabled()
    assert tab._fw_label.isHidden()
    assert "Debug-Firmware" not in tab._fw_label.text()


def test_prod_text_has_no_debug_hint_anywhere(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake, "prod")
    assert "Nur mit Debug-Firmware" not in tab_scanner.FW_LOCK_TEXT.values()
    assert tab._fw_label.text() == ""


def test_prod_resets_format_to_auto(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake, "debug")
    tab._format.setCurrentIndex(tab._format.findData("long"))
    fake.firmware_kind.emit("prod")
    assert tab._format.currentData() == "auto"


def test_debug_reenables_format_and_delay_after_prod(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake, "prod")
    fake.firmware_kind.emit("debug")
    assert tab._format.isEnabled()
    assert tab._delay.isEnabled()
    assert tab._format.toolTip() == ""
    assert tab._delay.toolTip() == ""


def test_member_start_only_open_in_prod(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake, "debug")
    assert not tab._mem_start_btn.isEnabled()
    assert tab._mem_start_btn.toolTip() == "Nur mit Produktiv-Firmware (scan/members)"
    fake.firmware_kind.emit("prod")
    assert tab._mem_start_btn.isEnabled()
    assert tab._mem_start_btn.toolTip() == ""


def test_lock_during_range_scan_blocks_member_search(qapp, db):
    fake = FakeBridge()
    tab = _range_tab(db, fake)
    tab._on_start()
    assert not tab._start_btn.isEnabled()
    assert not tab._member_hex.isEnabled()
    assert not tab._mem_start_btn.isEnabled()
    assert tab._stop_btn.isEnabled()  # Stopp bleibt bedienbar


def test_lock_during_member_scan_blocks_range_start(qapp, db):
    fake = FakeBridge()
    tab = _range_tab(db, fake)
    tab._mem_start_btn.click()
    assert fake.member_scans == [(0x000, 0x7FF, 0x000B)]
    assert not tab._start_btn.isEnabled()
    assert not tab._member_hex.isEnabled()
    assert not tab._mem_start_btn.isEnabled()
    assert tab._mem_stop_btn.isEnabled()
    assert not tab._mem_from.isEnabled()


def test_estimate_prod_uses_fixed_150_ms(qapp, db):
    fake = FakeBridge()
    tab = _range_tab(db, fake, fw="prod")
    tab._delay.setValue(400)  # in Produktiv wirkungslos
    assert "16 Indizes x 150 ms = ca. 2.4 s" in tab._estimate.text()


def test_estimate_debug_uses_delay(qapp, db):
    fake = FakeBridge()
    tab = _range_tab(db, fake, fw="debug")
    tab._delay.setValue(200)
    assert "16 Indizes x 200 ms = ca. 3.2 s" in tab._estimate.text()


# ---------------------------------------------------------------- Hex-ID

def test_parse_member_id_accepts_valid_and_rejects_invalid():
    assert tab_scanner.parse_member_id("0x7ff") == 0x7FF
    assert tab_scanner.parse_member_id("0x000") == 0
    assert tab_scanner.parse_member_id("0x180") == 0x180
    assert tab_scanner.parse_member_id("0x680") is None  # PC-ID
    assert tab_scanner.parse_member_id("0x800") is None  # ueber 0x7FF
    assert tab_scanner.parse_member_id("xyz") is None
    assert tab_scanner.parse_member_id("") is None


def test_hex_id_overrides_combo_in_prod(qapp, db):
    fake = FakeBridge()
    tab = _range_tab(db, fake)
    tab._member_hex.setText("0x7ff")
    tab._on_start()
    assert fake.scans[0][0] == 0x7FF


def test_hex_id_0x680_is_rejected_with_warning(qapp, db, warnings):
    fake = FakeBridge()
    tab = _range_tab(db, fake)
    tab._member_hex.setText("0x680")
    tab._on_start()
    assert fake.scans == []
    assert len(warnings) == 1


def test_hex_id_invalid_text_is_rejected_with_warning(qapp, db, warnings):
    fake = FakeBridge()
    tab = _range_tab(db, fake)
    tab._member_hex.setText("0x800")
    tab._on_start()
    assert fake.scans == []
    assert len(warnings) == 1


def test_hex_id_empty_uses_combo(qapp, db, warnings):
    fake = FakeBridge()
    tab = _range_tab(db, fake)
    tab._member_hex.setText("   ")
    tab._on_start()
    assert fake.scans[0][0] == tab._member.currentData()
    assert warnings == []


def test_hex_id_in_debug_sends_member_as_before(qapp, db):
    fake = FakeBridge()
    tab = _range_tab(db, fake, fw="debug")
    tab._member_hex.setText("0x180")
    tab._on_start()
    assert fake.scans[0][0] == 0x180


# ---------------------------------------------------------------- Bereichs-Scan in Prod

def test_prod_send_scan_arguments(qapp, db):
    fake = FakeBridge()
    tab = _range_tab(db, fake, switch=F9)
    tab._on_start()
    member = tab._member.currentData()
    assert fake.scans == [(member, 0x4E00, 0x4E0F, tab._delay.value(), "auto", F9)]


def test_prod_send_scan_format_is_auto_even_if_combo_was_long(qapp, db):
    fake = FakeBridge()
    tab = _range_tab(db, fake, fw="debug")
    tab._format.setCurrentIndex(tab._format.findData("long"))
    fake.firmware_kind.emit("prod")
    tab._on_start()
    assert fake.scans[0][4] == "auto"


def test_prod_alle_runs_three_scans_in_order(qapp, db):
    fake = FakeBridge()
    tab = _range_tab(db, fake, switch=tab_scanner.SWITCH_ALL)
    tab._on_start()
    assert [s[5] for s in fake.scans] == [FA]
    fake.scan_progress.emit(_progress(FA, 16, state="done"))
    assert [s[5] for s in fake.scans] == [FA, F8]
    fake.scan_progress.emit(_progress(F8, 16, state="done"))
    assert [s[5] for s in fake.scans] == [FA, F8, F9]
    assert all(s[4] == "auto" for s in fake.scans)
    fake.scan_progress.emit(_progress(F9, 16, state="done"))
    assert [s[5] for s in fake.scans] == [FA, F8, F9]
    assert tab._start_btn.isEnabled()


def test_progress_ignores_members_mode(qapp, db):
    fake = FakeBridge()
    tab = _range_tab(db, fake)
    tab._on_start()
    fake.scan_progress.emit(_member_progress(500))
    assert tab._progress.value() == 0
    assert tab._progress.maximum() == 16
    assert not tab._start_btn.isEnabled()  # Bereichs-Scan laeuft noch
    fake.scan_progress.emit(_member_progress(2048, state="done"))
    assert tab._progress.value() == 0
    assert not tab._start_btn.isEnabled()


def test_progress_members_mode_does_not_end_alle_sequence(qapp, db):
    fake = FakeBridge()
    tab = _range_tab(db, fake, switch=tab_scanner.SWITCH_ALL)
    tab._on_start()
    fake.scan_progress.emit(_member_progress(100))
    fake.scan_progress.emit(_progress(FA, 16, state="done"))
    assert [s[5] for s in fake.scans] == [FA, F8]


def test_scan_result_items_without_format_are_shown(qapp, db):
    fake = FakeBridge()
    tab = _range_tab(db, fake)
    tab._on_start()
    fake.scan_result.emit({
        "member": 0x180, "switch": FA,
        "items": [{"index": 0x0013, "raw": 0x0208, "status": "ok", "switch": FA,
                   "name": "X", "value": "Y"}],
    })
    assert tab._table.rowCount() == 1
    assert tab._table.item(0, 0).text() == "0x0013"
    assert tab._table.item(0, 3).text() == "0x0208"
    assert tab._table.item(0, 4).text() == "ok"
    key = (0x0013, FA)
    assert tab._items[key]["format"] == "auto"  # Fallback auf Scan-Format


def test_scan_result_items_keep_int_raw_and_switch(qapp, db):
    fake = FakeBridge()
    tab = _range_tab(db, fake)
    tab._on_start()
    fake.scan_result.emit({
        "member": 0x180, "switch": F8,
        "items": [{"index": 0x0013, "raw": 0x0208, "status": "ok", "switch": F8}],
    })
    item = tab._items[(0x0013, F8)]
    assert isinstance(item["raw"], int) and item["raw"] == 0x0208
    assert isinstance(item["switch"], int) and item["switch"] == F8


def test_scan_csv_export_with_prod_items(qapp, db, tmp_path, monkeypatch):
    fake = FakeBridge()
    tab = _range_tab(db, fake)
    tab._on_start()
    fake.scan_result.emit({
        "member": 0x180, "switch": FA,
        "items": [{"index": 0x0013, "raw": 0x0208, "status": "ok", "switch": FA,
                   "name": "X", "value": "Y"}],
    })
    path = tmp_path / "scan.csv"
    monkeypatch.setattr(tab_scanner.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(path), "CSV (*.csv)"))
    tab._on_export()
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh, delimiter=";"))
    assert rows[0] == ["index", "switch", "name", "raw", "status", "wert"]
    assert len(rows) == 2
    assert rows[1][0] == "0x0013"
    assert rows[1][3] == "0x0208"
    assert rows[1][4] == "ok"


# ---------------------------------------------------------------- Teilnehmer-Suche

def test_member_start_sends_default_range(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake)
    tab._mem_start_btn.click()
    assert fake.member_scans == [(0x000, 0x7FF, 0x000B)]


def test_member_start_sends_custom_range_and_index(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake)
    tab._mem_from.setText("0x100")
    tab._mem_to.setText("0x1ff")
    tab._mem_index.setText("0x0010")
    tab._mem_start_btn.click()
    assert fake.member_scans == [(0x100, 0x1FF, 0x10)]


def test_member_start_invalid_range_warns_and_does_not_send(qapp, db, warnings):
    fake = FakeBridge()
    tab = _tab(db, fake)
    tab._mem_from.setText("0x200")
    tab._mem_to.setText("0x100")
    tab._mem_start_btn.click()
    assert fake.member_scans == []
    assert len(warnings) == 1
    assert tab._mem_start_btn.isEnabled()


def test_member_start_range_above_7ff_is_invalid(qapp, db, warnings):
    fake = FakeBridge()
    tab = _tab(db, fake)
    tab._mem_to.setText("0x800")
    tab._mem_start_btn.click()
    assert fake.member_scans == []
    assert len(warnings) == 1


def test_member_start_invalid_index_warns(qapp, db, warnings):
    fake = FakeBridge()
    tab = _tab(db, fake)
    tab._mem_index.setText("nix")
    tab._mem_start_btn.click()
    assert fake.member_scans == []
    assert len(warnings) == 1


def test_member_start_bridge_error_unlocks_and_warns(qapp, db, warnings):
    fake = FakeBridge()
    fake.member_error = "nur Produktiv-Firmware"
    tab = _tab(db, fake)
    tab._mem_start_btn.click()
    assert len(warnings) == 1
    assert tab._mem_start_btn.isEnabled()
    assert not tab._mem_stop_btn.isEnabled()


def test_member_result_fills_table(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake)
    tab._mem_start_btn.click()
    fake.member_result.emit({"id": 0x180, "index": 0x000B, "raw": 5})
    assert tab._mem_table.rowCount() == 1
    cells = [tab._mem_table.item(0, c).text() for c in range(5)]
    assert cells == ["0x180", "KESSEL", "0x000b", "0x0005", "5"]


def test_member_name_resolution_prod_table_first(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake)
    assert tab._member_name(0x514) == "WPUMPE"


def test_member_name_resolution_falls_back_to_db(qapp, db, monkeypatch):
    fake = FakeBridge()
    tab = _tab(db, fake)
    monkeypatch.setattr(db, "member_by_id",
                        lambda can_id: {"name": "NUR_IN_DB"} if can_id == 0x7FE else None)
    assert tab._member_name(0x7FE) == "NUR_IN_DB"
    assert tab._member_name(0x7FD) == "-"


def test_member_name_dash_when_unknown(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake)
    assert tab._member_name(0x7FD) == "-"


def test_member_result_not_supported_is_marked(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake)
    tab._mem_start_btn.click()
    fake.member_result.emit({"id": 0x180, "index": 0x000B, "raw": 0x8000})
    assert tab._mem_table.item(0, 1).text() == "KESSEL (nicht unterstuetzt)"
    assert tab._mem_table.item(0, 3).text() == "0x8000"


def test_member_progress_updates_bar_and_estimate(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake)
    assert "2048 IDs" in tab._mem_estimate.text()
    assert "ca. 150 ms pro ID" in tab._mem_estimate.text()
    tab._mem_start_btn.click()
    fake.scan_progress.emit(_member_progress(100))
    assert tab._mem_progress.maximum() == 2048
    assert tab._mem_progress.value() == 100


def test_member_stop_sends_stop_and_unlocks(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake)
    tab._mem_start_btn.click()
    assert tab._mem_stop_btn.isEnabled()
    tab._mem_stop_btn.click()
    assert fake.stops == 1
    assert tab._mem_start_btn.isEnabled()
    assert tab._start_btn.isEnabled()
    assert not tab._mem_stop_btn.isEnabled()


@pytest.mark.parametrize("state", ["done", "stopped"])
def test_member_scan_end_unlocks(qapp, db, state):
    fake = FakeBridge()
    tab = _tab(db, fake)
    tab._mem_start_btn.click()
    fake.scan_progress.emit(_member_progress(10))
    assert not tab._start_btn.isEnabled()
    fake.scan_progress.emit(_member_progress(2048, state=state))
    assert tab._start_btn.isEnabled()
    assert tab._mem_start_btn.isEnabled()
    assert not tab._mem_stop_btn.isEnabled()


def test_member_scan_end_keeps_final_progress(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake)
    tab._mem_start_btn.click()
    fake.scan_progress.emit(_member_progress(2048, state="done"))
    assert tab._mem_progress.value() == 2048


def test_member_start_clears_previous_table(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake)
    tab._mem_start_btn.click()
    fake.member_result.emit({"id": 0x180, "index": 0x000B, "raw": 5})
    fake.scan_progress.emit(_member_progress(2048, state="done"))
    tab._mem_start_btn.click()
    assert tab._mem_table.rowCount() == 0


def test_member_double_click_sets_hex_id_field(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake)
    tab._mem_start_btn.click()
    fake.member_result.emit({"id": 0x514, "index": 0x000B, "raw": 7})
    tab._mem_table.itemDoubleClicked.emit(tab._mem_table.item(0, 0))
    assert tab._member_hex.text() == "0x514"
    assert fake.scans == []  # Uebernahme startet nichts


def test_member_csv_export(qapp, db, tmp_path, monkeypatch):
    fake = FakeBridge()
    tab = _tab(db, fake)
    tab._mem_start_btn.click()
    fake.member_result.emit({"id": 0x180, "index": 0x000B, "raw": 5})
    fake.member_result.emit({"id": 0x514, "index": 0x000B, "raw": 0x8000})
    path = tmp_path / "teilnehmer.csv"
    monkeypatch.setattr(tab_scanner.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(path), "CSV (*.csv)"))
    tab._mem_export_btn.click()
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh, delimiter=";"))
    assert rows[0] == ["id", "name", "index", "raw"]
    assert rows[1] == ["0x180", "KESSEL", "0x000b", "5"]
    assert rows[2] == ["0x514", "WPUMPE (nicht unterstuetzt)", "0x000b", "32768"]


def test_member_export_without_results_informs(qapp, db, monkeypatch):
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a))
    fake = FakeBridge()
    tab = _tab(db, fake)
    tab._mem_export_btn.click()
    assert len(infos) == 1


def test_member_scan_estimate_text_for_custom_range(qapp, db):
    fake = FakeBridge()
    tab = _tab(db, fake)
    tab._mem_from.setText("0x000")
    tab._mem_to.setText("0x00f")
    assert "16 IDs" in tab._mem_estimate.text()
    assert "ca. 2 s" in tab._mem_estimate.text()
