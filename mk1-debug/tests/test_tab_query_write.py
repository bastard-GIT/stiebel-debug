"""Tests fuer den Schreibmodus des Tabs Abfrage und die freie Member-Eingabe.

Fake-Bridge mit denselben Signalen und Sendemethoden wie MqttBridge; Qt laeuft offscreen.
encode_value/build_write_request/is_writable werden fuer die meisten Tests ueber
monkeypatch durch einfache Fakes ersetzt; ein Test prueft die echte encode_value, falls vorhanden.

Ausfuehren: C:\\temp\\stiebel-Remote\\.venv\\Scripts\\python.exe -I -B -m pytest C:\\temp\\stiebel-Remote\\mk1-debug\\tests\\test_tab_query_write.py -q
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

import elster  # noqa: E402
import tab_query  # noqa: E402
from elster import ElsterDb  # noqa: E402

SPEICHER = 0x201
INDEX = 0x0013  # WW_KOMFORT_TEMPERATUR, Typ et_dec_val


class FakeBridge(QObject):
    connected = Signal(bool)
    rx_frames = Signal(list)
    read_result = Signal(dict)
    write_result = Signal(dict)
    scan_progress = Signal(dict)
    scan_result = Signal(dict)
    status = Signal(dict)
    availability = Signal(str)
    availability_source = Signal(str)
    firmware_kind = Signal(str)

    def __init__(self):
        super().__init__()
        self.reads = []
        self.writes = []

    def send_read(self, member, index, fmt=None, switch=None):
        self.reads.append((member, index, fmt, switch))

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
    """Einfache Fakes: Typ et_dec_val, Wert * 10 -> Rohwert; Frame = Member, Index, Rohwert."""
    monkeypatch.setattr(tab_query, "encode_value", lambda t, s: int(round(float(s) * 10)))
    monkeypatch.setattr(tab_query, "is_writable", lambda t: True)
    monkeypatch.setattr(tab_query, "build_write_request",
                        lambda m, i, v, f="auto": (m, bytes([i >> 8, i & 0xFF, v >> 8, v & 0xFF])))


def _write_tab(db, fake, member_text="SPEICHER", index_text="0x0013", value="52.0", arm=True):
    fake.firmware_kind.emit("debug")
    tab = tab_query.QueryTab(db, fake)
    fake.firmware_kind.emit("debug")
    tab._mode_combo.setCurrentIndex(tab._mode_combo.findData(tab_query.MODE_WRITE))
    tab._member_combo.setEditText(member_text)
    tab._index_edit.setText(index_text)
    tab._value_edit.setText(value)
    tab._arm_check.setChecked(arm)
    return tab


def _write_result(status="verified", value=0x0208, readback=0x0208, ack=4):
    return {"member": SPEICHER, "index": INDEX, "value": value, "status": status,
            "ack": ack, "readback": readback}


# ---------------------------------------------------------------- freie Member-Eingabe

def test_member_free_hex_accepted_and_read_sent(qapp, db):
    fake = FakeBridge()
    fake.firmware_kind.emit("debug")
    tab = tab_query.QueryTab(db, fake)
    fake.firmware_kind.emit("debug")
    tab._member_combo.setEditText("0x4ab")
    assert tab._current_member() == 0x4AB
    tab._index_edit.setText("0x0003")
    tab._on_query()
    assert fake.reads == [(0x4AB, 0x0003, "auto", 0xFA)]


def test_member_decimal_and_reference_name_accepted(qapp, db):
    fake = FakeBridge()
    tab = tab_query.QueryTab(db, fake)
    tab._member_combo.setEditText("1195")
    assert tab._current_member() == 1195
    tab._member_combo.setEditText("speicher")
    assert tab._current_member() == SPEICHER
    tab._member_combo.setEditText("SPEICHER (0x201)")
    assert tab._current_member() == SPEICHER


@pytest.mark.parametrize("text", ["0x680", "0x800", "Text", ""])
def test_member_invalid_rejected_without_send(qapp, db, text):
    fake = FakeBridge()
    fake.firmware_kind.emit("debug")
    tab = tab_query.QueryTab(db, fake)
    fake.firmware_kind.emit("debug")
    tab._member_combo.setEditText(text)
    tab._index_edit.setText("0x0003")
    assert tab._current_member() is None
    tab._on_query()
    assert fake.reads == []
    assert tab._hint_label.text() != ""


def test_unknown_member_shown_as_unbekannt_in_result(qapp, db):
    fake = FakeBridge()
    fake.firmware_kind.emit("debug")
    tab = tab_query.QueryTab(db, fake)
    fake.firmware_kind.emit("debug")
    tab._member_combo.setEditText("0x4ab")
    tab._index_edit.setText("0x0003")
    tab._on_query()
    fake.read_result.emit({"member": 0x4AB, "index": 0x0003, "raw": 0x0100,
                           "status": "ok", "switch": 0xFA})
    assert "UNBEKANNT_0x4ab" in tab._info_label.text()
    assert tab._table.item(0, tab_query.HEADERS.index("Member")).text() == "UNBEKANNT_0x4ab"


# ---------------------------------------------------------------- Modus und Sperre

def test_write_mode_shows_write_controls(qapp, db):
    fake = FakeBridge()
    tab = _write_tab(db, fake)
    assert tab._switch_combo.isHidden()  # Switch im Schreibmodus ausgeblendet
    assert not tab._value_edit.isHidden()
    assert not tab._send_btn.isHidden()
    assert tab._query_btn.isHidden()
    tab._mode_combo.setCurrentIndex(tab._mode_combo.findData(tab_query.MODE_READ))
    assert not tab._switch_combo.isHidden()
    assert tab._value_edit.isHidden()
    assert not tab._query_btn.isHidden()


def test_send_blocked_without_arm(qapp, db, fake_elster):
    fake = FakeBridge()
    tab = _write_tab(db, fake, arm=False)
    assert not tab._send_btn.isEnabled()
    tab._on_send()
    assert fake.writes == []


def test_send_enabled_with_arm_and_debug(qapp, db, fake_elster):
    fake = FakeBridge()
    tab = _write_tab(db, fake)
    assert tab._send_btn.isEnabled()
    assert "0x0208" in tab._preview_label.text()


def test_send_blocked_in_prod_firmware(qapp, db, fake_elster):
    fake = FakeBridge()
    tab = _write_tab(db, fake)
    fake.firmware_kind.emit("prod")
    assert not tab._send_btn.isEnabled()
    assert not tab._value_edit.isEnabled()
    assert not tab._arm_check.isEnabled()
    assert not tab._fw_label.isHidden()
    tab._on_send()
    assert fake.writes == []


def test_arm_reset_on_firmware_change(qapp, db, fake_elster):
    fake = FakeBridge()
    tab = _write_tab(db, fake)
    fake.firmware_kind.emit("unknown")
    assert not tab._arm_check.isChecked()
    fake.firmware_kind.emit("debug")
    assert not tab._send_btn.isEnabled()  # wieder aus, bis erneut freigeschaltet


def test_send_blocked_for_invalid_member(qapp, db, fake_elster):
    fake = FakeBridge()
    tab = _write_tab(db, fake, member_text="0x680")
    assert not tab._send_btn.isEnabled()
    assert "0x680" in tab._preview_label.text()  # Fehlertext in der Vorschau


def test_send_blocked_for_non_writable_type(qapp, db, fake_elster, monkeypatch):
    monkeypatch.setattr(tab_query, "is_writable", lambda t: False)
    fake = FakeBridge()
    tab = _write_tab(db, fake)
    assert not tab._send_btn.isEnabled()
    assert "nicht schreibbar" in tab._preview_label.text()


def test_raw_mode_sends_without_type_check(qapp, db, fake_elster, monkeypatch):
    monkeypatch.setattr(tab_query, "is_writable", lambda t: False)
    fake = FakeBridge()
    tab = _write_tab(db, fake, value="0x0208")
    tab._raw_check.setChecked(True)
    assert tab._send_btn.isEnabled()
    assert "0x0208" in tab._preview_label.text()


def test_enter_in_write_mode_never_sends(qapp, db, fake_elster):
    fake = FakeBridge()
    tab = _write_tab(db, fake)
    tab._on_query()
    assert fake.reads == []
    assert fake.writes == []


# ---------------------------------------------------------------- Senden und Bestaetigung

def test_confirm_rejected_no_send(qapp, db, fake_elster, monkeypatch):
    asked = []
    monkeypatch.setattr(tab_query.QueryTab, "_confirm_write",
                        lambda self, text: asked.append(text) or False)
    fake = FakeBridge()
    tab = _write_tab(db, fake)
    tab._on_send()
    assert len(asked) == 1
    assert fake.writes == []
    assert tab._arm_check.isChecked()  # Abbruch: Freischaltung bleibt


def test_confirm_text_contains_details(qapp, db, fake_elster, monkeypatch):
    asked = []
    monkeypatch.setattr(tab_query.QueryTab, "_confirm_write",
                        lambda self, text: asked.append(text) or False)
    fake = FakeBridge()
    tab = _write_tab(db, fake, member_text="0x4ab")
    tab._on_send()  # Member 0x4ab ist unbekannt, aber gueltig
    text = asked[0]
    assert "Member: UNBEKANNT_0x4ab (0x4ab)" in text
    assert "Unbekannter Member" in text
    assert "Unbekanntes Signal" not in text
    assert "0x0013" in text and "Rohwert: 0x0208" in text


def test_confirm_text_names_signal_and_member(qapp, db, fake_elster, monkeypatch):
    asked = []
    monkeypatch.setattr(tab_query.QueryTab, "_confirm_write",
                        lambda self, text: asked.append(text) or False)
    fake = FakeBridge()
    tab = _write_tab(db, fake)
    tab._on_send()
    text = asked[0]
    assert "SPEICHER (0x201)" in text
    assert "WW_KOMFORT_TEMPERATUR" in text
    assert "Typ: et_dec_val" in text
    assert "Unbekannter" not in text


def test_confirm_ok_sends_raw(qapp, db, fake_elster, monkeypatch):
    monkeypatch.setattr(tab_query.QueryTab, "_confirm_write", lambda self, text: True)
    fake = FakeBridge()
    tab = _write_tab(db, fake)
    tab._on_send()
    assert fake.writes == [(SPEICHER, INDEX, 0x0208, "auto")]
    assert not tab._send_btn.isEnabled()  # Pending: kein zweiter Sendeauftrag


def test_real_encode_52_0_gives_raw_0x0208(qapp, db, monkeypatch):
    if not hasattr(elster, "encode_value"):
        pytest.skip("elster.encode_value fehlt noch")
    monkeypatch.setattr(tab_query.QueryTab, "_confirm_write", lambda self, text: True)
    fake = FakeBridge()
    tab = _write_tab(db, fake, value="52.0")
    assert "0x0208" in tab._preview_label.text()
    tab._on_send()
    assert fake.writes == [(SPEICHER, INDEX, 0x0208, "auto")]


# ---------------------------------------------------------------- write_result

def test_write_result_adds_row_and_locks(qapp, db, fake_elster, monkeypatch):
    monkeypatch.setattr(tab_query.QueryTab, "_confirm_write", lambda self, text: True)
    fake = FakeBridge()
    tab = _write_tab(db, fake)
    tab._on_send()
    fake.write_result.emit(_write_result())
    assert tab._table.rowCount() == 1
    cell = lambda h: tab._table.item(0, tab_query.HEADERS.index(h)).text()  # noqa: E731
    assert cell("Modus") == "Schreiben"
    assert cell("Member") == "SPEICHER"
    assert cell("Index") == "0x0013"
    assert cell("Ack") == "write ack"
    assert cell("Rücklesewert") == "52.0"
    assert cell("Status") == "verified"
    assert "verified" in tab._info_label.text()
    assert not tab._arm_check.isChecked()  # Freischaltung wieder aus
    assert not tab._send_btn.isEnabled()


def test_write_result_without_ack_and_readback(qapp, db, fake_elster):
    fake = FakeBridge()
    tab = _write_tab(db, fake)
    fake.write_result.emit(_write_result(status="no_ack", readback=None, ack=None))
    cell = lambda h: tab._table.item(0, tab_query.HEADERS.index(h)).text()  # noqa: E731
    assert cell("Ack") == "-"
    assert cell("Rücklesewert") == "–"
    assert cell("Status") == "no_ack"
    assert not tab._arm_check.isChecked()


def test_write_result_mismatch_status_shown(qapp, db, fake_elster):
    fake = FakeBridge()
    tab = _write_tab(db, fake)
    fake.write_result.emit(_write_result(status="mismatch", readback=0x01F4))  # 500 -> 50.0
    assert tab._table.item(0, tab_query.HEADERS.index("Status")).text() == "mismatch"
    assert tab._table.item(0, tab_query.HEADERS.index("Rücklesewert")).text() == "50.0"
