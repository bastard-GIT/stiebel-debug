"""Tests Tab Abfrage mit Produktiv-Firmware (fw "prod", raw/read und raw/write).

Prod-Freigabe: Lesen und Schreiben aktiv; Absender, Timeout und Format sind deaktiviert
(mit Tooltip) und stehen auf Standardwerten. Fake-Bridge mit denselben Signalen und
Sendemethoden wie MqttBridge (keine MQTT-Verbindung); Qt laeuft offscreen.

Ausfuehren: C:\\temp\\stiebel-Remote\\.venv\\Scripts\\python.exe -I -B -m pytest C:\\temp\\stiebel-Remote\\mk1-debug\\tests\\test_tab_query_prod.py -q
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

import tab_query  # noqa: E402
from elster import ElsterDb  # noqa: E402

SPEICHER = 0x201
INDEX = 0x0013  # WW_KOMFORT_TEMPERATUR, Typ et_dec_val
FA, F8, F9 = 0xFA, 0xF8, 0xF9
DEFAULT_SRC_TEXT = "PC (0x680)"
PROD_OFF_PART = "Im Produktiv-Modus nicht verfuegbar"
WARN_REJECTED = "Schreiben abgelehnt (Status rejected)"


class ProdFake(QObject):
    """Fake-Bridge: zeichnet send_read/send_write samt Zusatzfeldern auf."""
    connected = Signal(bool)
    read_result = Signal(dict)
    write_result = Signal(dict)
    firmware_kind = Signal(str)

    def __init__(self):
        super().__init__()
        self.reads = []   # (member, index, fmt, switch, extra_kwargs)
        self.writes = []  # (member, index, value, fmt)

    def send_read(self, member, index, fmt=None, switch=None, **extra):
        self.reads.append((member, index, fmt, switch, extra))

    def send_write(self, member, index, value, fmt=None):
        self.writes.append((member, index, value, fmt))


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="session")
def db():
    return ElsterDb()


@pytest.fixture
def fake_elster(monkeypatch):
    """Einfache Fakes wie in test_tab_query_write.py: Wert * 10 -> Rohwert."""
    monkeypatch.setattr(tab_query, "encode_value", lambda t, s: int(round(float(s) * 10)))
    monkeypatch.setattr(tab_query, "is_writable", lambda t: True)
    monkeypatch.setattr(tab_query, "build_write_request",
                        lambda m, i, v, f="auto": (m, bytes([i >> 8, i & 0xFF, v >> 8, v & 0xFF])))


def _tab(db, fake, fw="debug"):
    if fw is not None:
        fake.firmware_kind.emit(fw)
    tab = tab_query.QueryTab(db, fake)
    if fw is not None:
        fake.firmware_kind.emit(fw)
    return tab


def _set_read(tab, member="0x100", index="0x4f4d", switch=FA):
    tab._member_combo.setEditText(member)
    tab._index_edit.setText(index)
    tab._switch_combo.setCurrentIndex(tab._switch_combo.findData(switch))


def _write_tab(db, fake, arm=True):
    tab = _tab(db, fake, fw="prod")
    tab._mode_combo.setCurrentIndex(tab._mode_combo.findData(tab_query.MODE_WRITE))
    tab._member_combo.setEditText("SPEICHER")
    tab._index_edit.setText("0x0013")
    tab._value_edit.setText("52.0")
    tab._arm_check.setChecked(arm)
    return tab


def _write_result(status, readback=0x0208, ack=4):
    return {"member": SPEICHER, "index": INDEX, "value": 0x0208, "status": status,
            "ack": ack, "readback": readback}


def _cell(tab, row, header):
    return tab._table.item(row, tab_query.HEADERS.index(header)).text()


# ---------------------------------------------------------------- Lock und Bedienelemente

def test_prod_enables_all_query_widgets(qapp, db):
    fake = ProdFake()
    tab = _tab(db, fake, fw="prod")
    # Absender, Timeout, Format sind im Prod-Modus gesperrt (eigene Tests), alle anderen offen
    assert all(w.isEnabled() for w in tab._lockable if w not in tab._prod_off)
    assert tab._fw_label.isHidden()
    assert tab._fw_label.text() == ""


def test_unknown_keeps_query_widgets_locked_with_hint(qapp, db):
    fake = ProdFake()
    tab = _tab(db, fake, fw=None)
    assert not any(w.isEnabled() for w in tab._lockable)
    assert not tab._fw_label.isHidden()
    assert tab._fw_label.text() == "Warte auf Status der Firmware..."


def test_prod_disables_src_timeout_format_with_tooltip(qapp, db):
    fake = ProdFake()
    tab = _tab(db, fake, fw="prod")
    for widget in (tab._src_combo, tab._timeout_spin, tab._format_combo):
        assert not widget.isEnabled()
        assert PROD_OFF_PART in widget.toolTip()
    assert "Absender" in tab._src_combo.toolTip()
    assert "Timeout" in tab._timeout_spin.toolTip()
    assert "Format" in tab._format_combo.toolTip()


def test_prod_keeps_member_index_switch_query_button_enabled(qapp, db):
    fake = ProdFake()
    tab = _tab(db, fake, fw="prod")
    for widget in (tab._member_combo, tab._index_edit, tab._switch_combo, tab._query_btn,
                   tab._mode_combo):
        assert widget.isEnabled()


def test_debug_reenables_options_and_restores_tooltips(qapp, db):
    fake = ProdFake()
    tab = _tab(db, fake, fw="debug")
    src_tip = tab._src_combo.toolTip()
    timeout_tip = tab._timeout_spin.toolTip()
    fmt_tip = tab._format_combo.toolTip()
    fake.firmware_kind.emit("prod")
    fake.firmware_kind.emit("debug")
    assert tab._src_combo.isEnabled()
    assert tab._timeout_spin.isEnabled()
    assert tab._format_combo.isEnabled()
    assert tab._src_combo.toolTip() == src_tip
    assert tab._timeout_spin.toolTip() == timeout_tip
    assert tab._format_combo.toolTip() == fmt_tip
    assert PROD_OFF_PART not in tab._src_combo.toolTip()


def test_prod_resets_src_format_timeout_to_defaults(qapp, db):
    fake = ProdFake()
    tab = _tab(db, fake, fw="debug")
    tab._src_combo.setEditText("0x700")
    tab._timeout_spin.setValue(3000)
    tab._format_combo.setCurrentIndex(tab._format_combo.findData("long"))
    fake.firmware_kind.emit("prod")
    assert tab._src_combo.currentText() == DEFAULT_SRC_TEXT
    assert tab._timeout_spin.value() == 1000
    assert tab._format_combo.currentData() == "auto"


# ---------------------------------------------------------------- Lesen

def test_prod_read_sends_send_read_with_defaults(qapp, db):
    fake = ProdFake()
    tab = _tab(db, fake, fw="prod")
    _set_read(tab, switch=FA)
    tab._on_query()
    assert fake.reads == [(0x100, 0x4F4D, "auto", FA, {})]  # keine Zusatzfelder src/timeout_ms


def test_prod_read_min_switch_sends_f8(qapp, db):
    fake = ProdFake()
    tab = _tab(db, fake, fw="prod")
    _set_read(tab, switch=F8)
    tab._on_query()
    assert fake.reads == [(0x100, 0x4F4D, "auto", F8, {})]


def test_prod_read_result_ok_shows_value(qapp, db):
    fake = ProdFake()
    tab = _tab(db, fake, fw="prod")
    _set_read(tab)
    tab._on_query()
    fake.read_result.emit({"member": 0x100, "index": 0x4F4D, "switch": FA, "raw": 0x01F6,
                           "status": "ok", "src": 0x680})
    assert "Status: ok" in tab._info_label.text()
    assert "Roh: 0x01f6" in tab._info_label.text()
    assert _cell(tab, 0, "Status") == "ok"


def test_prod_read_result_timeout_shows_no_value(qapp, db):
    fake = ProdFake()
    tab = _tab(db, fake, fw="prod")
    _set_read(tab)
    tab._on_query()
    fake.read_result.emit({"member": 0x100, "index": 0x4F4D, "switch": FA, "raw": 0,
                           "status": "timeout", "src": 0x680})
    assert _cell(tab, 0, "Status") == "Timeout"
    assert _cell(tab, 0, "Wert") == "–"


def test_read_result_busy_shows_scan_hint_without_value(qapp, db):
    fake = ProdFake()
    tab = _tab(db, fake, fw="prod")
    _set_read(tab)
    tab._on_query()
    fake.read_result.emit({"member": 0x100, "index": 0x4F4D, "switch": FA, "raw": 0,
                           "status": "busy", "src": 0x680})
    assert "Status: Firmware beschäftigt (Scan läuft)" in tab._info_label.text()
    assert _cell(tab, 0, "Status") == "Firmware beschäftigt (Scan läuft)"
    assert _cell(tab, 0, "Roh dez") == "–"
    assert _cell(tab, 0, "Wert") == "–"


def test_status_text_contains_busy_entry():
    assert tab_query.STATUS_TEXT["busy"] == "Firmware beschäftigt (Scan läuft)"
    assert tab_query.STATUS_TEXT["ok"] == "ok"
    assert tab_query.STATUS_TEXT["timeout"] == "Timeout"


def test_prod_sequence_all_continues_after_busy(qapp, db, monkeypatch):
    monkeypatch.setattr(tab_query, "SEQ_TIMEOUT_MS", 30)
    fake = ProdFake()
    tab = _tab(db, fake, fw="prod")
    _set_read(tab, switch=tab_query.SWITCH_ALL)
    tab._on_query()
    assert [r[3] for r in fake.reads] == [FA]
    fake.read_result.emit({"member": 0x100, "index": 0x4F4D, "switch": FA, "raw": 0,
                           "status": "busy", "src": 0x680})
    assert [r[3] for r in fake.reads] == [FA, F8]  # busy ist kein Abbruch
    assert "aktuell: Firmware beschäftigt (Scan läuft)" in tab._info_label.text()
    fake.read_result.emit({"member": 0x100, "index": 0x4F4D, "switch": F8, "raw": 0x01F6,
                           "status": "ok", "src": 0x680})
    fake.read_result.emit({"member": 0x100, "index": 0x4F4D, "switch": F9, "raw": 0x01F7,
                           "status": "ok", "src": 0x680})
    assert [r[3] for r in fake.reads] == [FA, F8, F9]
    assert all(r[4] == {} for r in fake.reads)
    assert tab._query_btn.isEnabled()


def test_firmware_unknown_during_prod_sequence_aborts(qapp, db, monkeypatch):
    monkeypatch.setattr(tab_query, "SEQ_TIMEOUT_MS", 30)
    fake = ProdFake()
    tab = _tab(db, fake, fw="prod")
    _set_read(tab, switch=tab_query.SWITCH_ALL)
    tab._on_query()
    fake.firmware_kind.emit("unknown")
    assert "abgebrochen" in tab._hint_label.text()
    assert not tab._query_btn.isEnabled()
    fake.firmware_kind.emit("prod")
    assert tab._query_btn.isEnabled()


# ---------------------------------------------------------------- Schreiben

def test_prod_write_not_sent_without_arm(qapp, db, fake_elster):
    fake = ProdFake()
    tab = _write_tab(db, fake, arm=False)
    assert not tab._send_btn.isEnabled()
    tab._on_send()
    assert fake.writes == []


def test_prod_write_confirm_text_has_prod_line(qapp, db, fake_elster, monkeypatch):
    asked = []
    monkeypatch.setattr(tab_query.QueryTab, "_confirm_write",
                        lambda self, text: asked.append(text) or False)
    fake = ProdFake()
    tab = _write_tab(db, fake)
    tab._on_send()
    assert tab_query.PROD_CONFIRM_LINE in asked[0]
    assert "Firmware: Produktiv (raw/write, Ergebnis kann 'rejected' sein)" in asked[0]
    assert asked[0].count("Firmware: Produktiv") == 1


def test_debug_write_confirm_text_has_no_prod_line(qapp, db, fake_elster, monkeypatch):
    asked = []
    monkeypatch.setattr(tab_query.QueryTab, "_confirm_write",
                        lambda self, text: asked.append(text) or False)
    fake = ProdFake()
    tab = _tab(db, fake, fw="debug")
    tab._mode_combo.setCurrentIndex(tab._mode_combo.findData(tab_query.MODE_WRITE))
    tab._member_combo.setEditText("SPEICHER")
    tab._index_edit.setText("0x0013")
    tab._value_edit.setText("52.0")
    tab._arm_check.setChecked(True)
    tab._on_send()
    assert "Firmware: Produktiv" not in asked[0]


def test_prod_write_rejected_by_confirm_no_send(qapp, db, fake_elster, monkeypatch):
    monkeypatch.setattr(tab_query.QueryTab, "_confirm_write", lambda self, text: False)
    fake = ProdFake()
    tab = _write_tab(db, fake)
    tab._on_send()
    assert fake.writes == []
    assert tab._arm_check.isChecked()  # Abbruch im Dialog: Freischaltung bleibt


def test_prod_write_confirmed_sends_auto_format(qapp, db, fake_elster, monkeypatch):
    monkeypatch.setattr(tab_query.QueryTab, "_confirm_write", lambda self, text: True)
    fake = ProdFake()
    tab = _write_tab(db, fake)
    tab._on_send()
    assert fake.writes == [(SPEICHER, INDEX, 0x0208, "auto")]
    assert not tab._send_btn.isEnabled()  # offen bis write_result


def test_prod_write_rejected_shows_warning(qapp, db, fake_elster):
    fake = ProdFake()
    tab = _write_tab(db, fake)
    fake.write_result.emit(_write_result("rejected", readback=None, ack=None))
    assert WARN_REJECTED in tab._warn_label.text()
    assert "can_raw_write" in tab._warn_label.text()
    assert "Sperrregel" in tab._warn_label.text()
    assert _cell(tab, 0, "Status") == "rejected"
    assert not tab._arm_check.isChecked()  # nach Abschluss wieder gesperrt


def test_write_result_verified_clears_warning(qapp, db, fake_elster):
    fake = ProdFake()
    tab = _write_tab(db, fake)
    fake.write_result.emit(_write_result("rejected", readback=None, ack=None))
    assert tab._warn_label.text() != ""
    fake.write_result.emit(_write_result("verified"))
    assert tab._warn_label.text() == ""


@pytest.mark.parametrize("status", ["no_ack", "timeout", "mismatch"])
def test_write_result_other_status_no_warning(qapp, db, fake_elster, status):
    fake = ProdFake()
    tab = _write_tab(db, fake)
    fake.write_result.emit(_write_result(status, readback=None, ack=None))
    assert tab._warn_label.text() == ""
    assert _cell(tab, 0, "Status") == status


def test_write_result_verified_row_and_info(qapp, db, fake_elster):
    fake = ProdFake()
    tab = _write_tab(db, fake)
    fake.write_result.emit(_write_result("verified"))
    assert _cell(tab, 0, "Status") == "verified"
    assert _cell(tab, 0, "Ack") == "write ack"
    assert "Status: verified" in tab._info_label.text()


def test_firmware_change_disarms_prod_write(qapp, db, fake_elster):
    fake = ProdFake()
    tab = _write_tab(db, fake)
    assert tab._send_btn.isEnabled()
    fake.firmware_kind.emit("unknown")
    assert not tab._arm_check.isChecked()
    assert not tab._send_btn.isEnabled()
    fake.firmware_kind.emit("prod")
    assert not tab._send_btn.isEnabled()  # wieder aus, bis erneut freigeschaltet


def test_prod_write_format_control_locked_to_auto(qapp, db, fake_elster):
    fake = ProdFake()
    tab = _write_tab(db, fake)
    assert not tab._format_combo.isEnabled()
    assert tab._format_combo.currentData() == "auto"
    assert tab._write_plan()[0]["fmt"] == "auto"


def test_connection_loss_in_prod_disarms(qapp, db, fake_elster):
    fake = ProdFake()
    tab = _write_tab(db, fake)
    fake.connected.emit(False)
    assert not tab._arm_check.isChecked()
    assert not tab._send_btn.isEnabled()
