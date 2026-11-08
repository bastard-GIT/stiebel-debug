"""Tests fuer gui/mqtt_client.py (Switch, Format, Verfuegbarkeit) und die Tabs Abfrage/Scanner.

Kein Broker: die Bridge wird ueber _on_message (paho-Callback) gefuettert, gesendet wird an
einen Fake-Client. Die Tabs bekommen eine Fake-Bridge mit denselben Signalen.
Qt laeuft offscreen.

Ausfuehren: C:\\temp\\stiebel-Remote\\.venv\\Scripts\\python.exe -I -B -m pytest C:\\temp\\stiebel-Remote\\mk1-debug\\tests\\test_mqtt_bridge.py -q
"""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

GUI_DIR = Path(__file__).resolve().parent.parent / "gui"
sys.path.insert(0, str(GUI_DIR))

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import mqtt_client  # noqa: E402
from mqtt_client import MqttBridge  # noqa: E402
import tab_query  # noqa: E402
import tab_scanner  # noqa: E402
import tab_status  # noqa: E402
from elster import ElsterDb  # noqa: E402


# ---------------------------------------------------------------- Fakes

class FakeClient:
    def __init__(self):
        self.published = []  # (topic, payload dict)
        self.subscribed = []

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, json.loads(payload)))

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)


def msg(topic: str, payload) -> SimpleNamespace:
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return SimpleNamespace(topic=topic, payload=payload)


@pytest.fixture
def bridge_and_client():
    bridge = MqttBridge()
    client = FakeClient()
    bridge._client = client
    return bridge, client


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


# ---------------------------------------------------------------- Senden

def test_send_read_default_has_no_extra_fields(bridge_and_client):
    bridge, client = bridge_and_client
    bridge.send_read(0x180, 0x4EF0)
    assert client.published == [("stiebel-debug/cmd/read", {"member": "0x180", "index": "0x4ef0"})]


def test_send_read_with_format_and_switch(bridge_and_client):
    bridge, client = bridge_and_client
    bridge.send_read(0x180, 0x0003, fmt="long", switch=0xF8)
    assert client.published[-1] == (
        "stiebel-debug/cmd/read",
        {"member": "0x180", "index": "0x0003", "format": "long", "switch": "0xf8"},
    )


def test_send_read_auto_and_fa_are_omitted(bridge_and_client):
    bridge, client = bridge_and_client
    bridge.send_read(0x180, 0x0003, fmt="auto", switch=0xFA)
    assert "format" not in client.published[-1][1]
    assert "switch" not in client.published[-1][1]


def test_send_scan_with_format_and_switch(bridge_and_client):
    bridge, client = bridge_and_client
    bridge.send_scan(0x180, 0x4E00, 0x4E0F, 200, fmt="short", switch=0xF9)
    assert client.published[-1] == (
        "stiebel-debug/cmd/scan",
        {"member": "0x180", "from": "0x4e00", "to": "0x4e0f", "delay_ms": 200,
         "format": "short", "switch": "0xf9"},
    )


def test_send_scan_default_payload_unchanged(bridge_and_client):
    bridge, client = bridge_and_client
    bridge.send_scan(0x180, 0x4E00, 0x4E0F, 200)
    assert client.published[-1][1] == {"member": "0x180", "from": "0x4e00", "to": "0x4e0f", "delay_ms": 200}


@pytest.mark.parametrize("kwargs", [
    {"fmt": "medium"},
    {"switch": 0xF7},
    {"switch": 0x00},
])
def test_send_rejects_invalid_values(bridge_and_client, kwargs):
    bridge, client = bridge_and_client
    with pytest.raises(ValueError):
        bridge.send_read(0x180, 0x0003, **kwargs)
    with pytest.raises(ValueError):
        bridge.send_scan(0x180, 0, 1, 200, **kwargs)
    assert client.published == []


# ---------------------------------------------------------------- Empfang

def test_read_result_without_switch_defaults_to_fa(bridge_and_client):
    bridge, _ = bridge_and_client
    got = []
    bridge.read_result.connect(got.append)
    bridge._on_message(None, None, msg("stiebel-debug/read/result",
                                       {"member": "0x180", "index": "0x0003", "raw": "0x0100"}))
    assert got[0]["switch"] == 0xFA
    assert got[0]["raw"] == 0x0100


def test_read_result_switch_is_int(bridge_and_client):
    bridge, _ = bridge_and_client
    got = []
    bridge.read_result.connect(got.append)
    bridge._on_message(None, None, msg("stiebel-debug/read/result",
                                       {"member": "0x180", "index": "0x0003", "raw": "0x0100",
                                        "switch": "0xf8"}))
    assert got[0]["switch"] == 0xF8


def test_scan_progress_and_result_switch(bridge_and_client):
    bridge, _ = bridge_and_client
    progress, results = [], []
    bridge.scan_progress.connect(progress.append)
    bridge.scan_result.connect(results.append)
    bridge._on_message(None, None, msg("stiebel-debug/scan/progress",
                                       {"index": "0x0003", "done": 1, "total": 2, "switch": "0xf9"}))
    bridge._on_message(None, None, msg("stiebel-debug/scan/result", {
        "member": "0x180", "format": "long", "switch": "0xf8",
        "items": [
            {"index": "0x0003", "raw": "0x0100", "status": "ok"},
            {"index": "0x0004", "raw": "0x0200", "status": "ok", "switch": "0xfa"},
        ],
    }))
    assert progress[0]["switch"] == 0xF9
    items = results[0]["items"]
    assert [i["switch"] for i in items] == [0xF8, 0xFA]  # fehlt -> Top-Level, sonst eigener Wert
    assert items[0]["index"] == 0x0003 and items[0]["raw"] == 0x0100


def test_scan_result_without_switch_defaults_to_fa(bridge_and_client):
    bridge, _ = bridge_and_client
    results = []
    bridge.scan_result.connect(results.append)
    bridge._on_message(None, None, msg("stiebel-debug/scan/result", {
        "member": "0x180", "items": [{"index": "0x0003", "raw": "0x0100", "status": "ok"}],
    }))
    assert results[0]["switch"] == 0xFA
    assert results[0]["items"][0]["switch"] == 0xFA


def test_broken_message_is_ignored(bridge_and_client):
    bridge, _ = bridge_and_client
    got = []
    bridge.read_result.connect(got.append)
    bridge._on_message(None, None, msg("stiebel-debug/read/result", b"\xff not json"))
    bridge._on_message(None, None, msg("stiebel-debug/read/result",
                                       {"member": "0x180", "index": "0x0003", "raw": "0x0100",
                                        "switch": "0x123"}))  # ausserhalb 8 Bit
    assert got == []


# ---------------------------------------------------------------- Verfuegbarkeit

def _avail_listeners(bridge):
    events = []
    bridge.availability_source.connect(lambda s: events.append(("source", s)))
    bridge.availability.connect(lambda s: events.append(("avail", s)))
    return events


def test_availability_debug_only(bridge_and_client):
    bridge, _ = bridge_and_client
    events = _avail_listeners(bridge)
    bridge._on_message(None, None, msg("stiebel-debug/availability", "online"))
    assert events == [("source", "debug"), ("avail", "online")]


def test_availability_produktiv_topic(bridge_and_client):
    bridge, _ = bridge_and_client
    events = _avail_listeners(bridge)
    bridge._on_message(None, None, msg("heatingpump/status", "online"))
    assert events == [("source", "produktiv"), ("avail", "online")]


def test_availability_offline_wins(bridge_and_client):
    bridge, _ = bridge_and_client
    events = _avail_listeners(bridge)
    bridge._on_message(None, None, msg("stiebel-debug/availability", "online"))
    bridge._on_message(None, None, msg("heatingpump/status", "offline"))
    bridge._on_message(None, None, msg("stiebel-debug/availability", "online"))  # debug wieder online
    assert events[-1] == ("avail", "offline")
    bridge._on_message(None, None, msg("heatingpump/status", "online"))
    assert events[-1] == ("avail", "online")


def test_availability_ignores_unknown_payload(bridge_and_client):
    bridge, _ = bridge_and_client
    events = _avail_listeners(bridge)
    bridge._on_message(None, None, msg("heatingpump/status", "vielleicht"))
    assert events == []


def test_subscribes_to_produktiv_availability(bridge_and_client):
    bridge, client = bridge_and_client

    class Rc0Client(FakeClient):
        pass

    sub_client = Rc0Client()
    bridge._on_connect(sub_client, None, None, 0)
    assert "heatingpump/status" in sub_client.subscribed
    assert "stiebel-debug/availability" in sub_client.subscribed


def test_connect_resets_availability_memory(bridge_and_client):
    bridge, _ = bridge_and_client
    bridge._on_message(None, None, msg("heatingpump/status", "offline"))
    assert bridge._combined_availability() == "offline"
    bridge._avail_by_source = {"debug": None, "produktiv": None}
    assert bridge._combined_availability() is None


# ---------------------------------------------------------------- Tab Abfrage

def test_query_tab_sends_format_and_switch(qapp, db):
    fake = FakeBridge()
    tab = tab_query.QueryTab(db, fake)
    tab._index_edit.setText("0x0003")
    tab._format_combo.setCurrentIndex(tab._format_combo.findData("long"))
    tab._switch_combo.setCurrentIndex(tab._switch_combo.findData(0xF8))
    tab._on_query()
    member = tab._member_combo.currentData()
    assert fake.reads == [(member, 0x0003, "long", 0xF8)]


def test_query_tab_default_is_auto_and_aktuell(qapp, db):
    fake = FakeBridge()
    tab = tab_query.QueryTab(db, fake)
    tab._index_edit.setText("0x0003")
    tab._on_query()
    assert fake.reads[-1][2:] == ("auto", 0xFA)


def test_query_tab_blocks_short_for_index_above_ff(qapp, db):
    fake = FakeBridge()
    tab = tab_query.QueryTab(db, fake)
    tab._index_edit.setText("0x4ef0")
    tab._format_combo.setCurrentIndex(tab._format_combo.findData("short"))
    tab._on_query()
    assert fake.reads == []


def test_query_tab_history_switch_column_and_no_mixing(qapp, db):
    fake = FakeBridge()
    tab = tab_query.QueryTab(db, fake)
    member = tab._member_combo.currentData()
    tab._index_edit.setText("0x0003")
    tab._switch_combo.setCurrentIndex(tab._switch_combo.findData(0xFA))
    tab._on_query()
    info_before = tab._info_label.text()

    # Antwort mit anderem Switch: Verlauf ja, "Letztes Ergebnis" nein.
    fake.read_result.emit({"member": member, "index": 0x0003, "raw": 0x0500, "status": "ok", "switch": 0xF8})
    assert tab._info_label.text() == info_before
    assert tab._table.rowCount() == 1
    col = tab_query.HEADERS.index("Switch")
    assert tab._table.item(0, col).text() == "min"

    # Passende Antwort (aktueller Wert, ohne Feld -> 0xFA): Ergebnis wird gefuellt, Switch leer.
    fake.read_result.emit({"member": member, "index": 0x0003, "raw": 0x0100, "status": "ok"})
    assert "Roh: 0x0100" in tab._info_label.text()
    assert tab._table.rowCount() == 2
    assert tab._table.item(0, col).text() == ""
    assert tab._table.item(1, col).text() == "min"


def test_query_tab_max_row_label(qapp, db):
    fake = FakeBridge()
    tab = tab_query.QueryTab(db, fake)
    member = tab._member_combo.currentData()
    fake.read_result.emit({"member": member, "index": 0x0003, "raw": 1, "status": "ok", "switch": 0xF9})
    assert tab._table.item(0, tab_query.HEADERS.index("Switch")).text() == "max"


# ---------------------------------------------------------------- Tab Scanner

def test_scanner_sends_format_and_switch(qapp, db):
    fake = FakeBridge()
    tab = tab_scanner.ScannerTab(db, fake)
    tab._from.setText("0x0000")
    tab._to.setText("0x000f")
    tab._format.setCurrentIndex(tab._format.findData("long"))
    tab._switch.setCurrentIndex(tab._switch.findData(0xF9))
    tab._on_start()
    member = tab._member.currentData()
    assert fake.scans == [(member, 0x0000, 0x000F, tab._delay.value(), "long", 0xF9)]


def test_scanner_blocks_short_over_ff(qapp, db, monkeypatch):
    fake = FakeBridge()
    tab = tab_scanner.ScannerTab(db, fake)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.No)
    tab._from.setText("0x4e00")
    tab._to.setText("0x4e0f")
    tab._format.setCurrentIndex(tab._format.findData("short"))
    tab._on_start()
    assert fake.scans == []
    assert len(warnings) == 1


def test_scanner_results_keyed_by_index_and_switch(qapp, db):
    fake = FakeBridge()
    tab = tab_scanner.ScannerTab(db, fake)
    fake.scan_result.emit({
        "member": 0x180, "format": "long", "switch": 0xFA,
        "items": [
            {"index": 0x0003, "raw": 0x0100, "status": "ok", "switch": 0xF8},
            {"index": 0x0003, "raw": 0x0200, "status": "ok", "switch": 0xFA},
        ],
    })
    assert tab._table.rowCount() == 2
    # gleiche (index, switch) ersetzt den Treffer statt eine Zeile anzuhaengen
    fake.scan_result.emit({
        "member": 0x180, "format": "long", "switch": 0xF8,
        "items": [{"index": 0x0003, "raw": 0x0300, "status": "ok", "switch": 0xF8}],
    })
    assert tab._table.rowCount() == 2
    assert len(tab._items) == 2
    sw_col = tab_scanner.COLUMNS.index("Switch")
    raw_col = tab_scanner.COLUMNS.index("Rohwert")
    rows = {tab._table.item(r, sw_col).text(): tab._table.item(r, raw_col).text()
            for r in range(tab._table.rowCount())}
    assert rows == {"min": "0x0300", "": "0x0200"}


def test_scanner_export_items_have_switch_and_format(qapp, db, monkeypatch):
    fake = FakeBridge()
    tab = tab_scanner.ScannerTab(db, fake)
    fake.scan_result.emit({
        "member": 0x180, "format": "short", "switch": 0xFA,
        "items": [{"index": 0x0003, "raw": 0x0100, "status": "ok", "switch": 0xF9}],
    })
    saved = []
    monkeypatch.setattr(tab_scanner.QFileDialog, "getSaveFileName",
                        lambda *a, **k: ("x.csv", "CSV (*.csv)"))
    monkeypatch.setattr(tab_scanner, "save_scan_csv",
                        lambda path, items, db_: saved.append(items))
    tab._on_export()
    assert saved[0][0]["switch"] == 0xF9
    assert saved[0][0]["format"] == "short"


# ---------------------------------------------------------------- Status / Quelle

def test_status_tab_shows_source(qapp):
    fake = FakeBridge()
    tab = tab_status.StatusTab(fake)
    fake.availability_source.emit("produktiv")
    assert tab._lbl_source.text() == "Produktiv"
    fake.availability_source.emit("debug")
    assert tab._lbl_source.text() == "Debug"


def test_source_label_unknown():
    assert tab_status.source_label(None) == "-"
    assert tab_status.source_label("sonst") == "-"


# ---------------------------------------------------------------- Firmware-Art

def _fw_listeners(bridge):
    kinds = []
    bridge.firmware_kind.connect(kinds.append)
    return kinds


def test_status_without_fw_is_prod(bridge_and_client):
    bridge, _ = bridge_and_client
    kinds = _fw_listeners(bridge)
    bridge._on_message(None, None, msg("stiebel-debug/status", {"rx": 0, "tx": 0}))
    assert kinds == ["prod"]


def test_status_fw_debug_is_debug(bridge_and_client):
    bridge, _ = bridge_and_client
    kinds = _fw_listeners(bridge)
    bridge._on_message(None, None, msg("stiebel-debug/status", {"rx": 0, "fw": "debug"}))
    assert kinds == ["debug"]


def test_status_fw_prod_explicit(bridge_and_client):
    bridge, _ = bridge_and_client
    kinds = _fw_listeners(bridge)
    bridge._on_message(None, None, msg("stiebel-debug/status", {"rx": 0, "fw": "prod"}))
    assert kinds == ["prod"]


def test_firmware_kind_emitted_only_on_change(bridge_and_client):
    bridge, _ = bridge_and_client
    kinds = _fw_listeners(bridge)
    bridge._on_message(None, None, msg("stiebel-debug/status", {"fw": "debug"}))
    bridge._on_message(None, None, msg("stiebel-debug/status", {"fw": "debug", "rx": 5}))
    bridge._on_message(None, None, msg("stiebel-debug/status", {"rx": 6}))
    assert kinds == ["debug", "prod"]


def test_broken_status_does_not_change_firmware(bridge_and_client):
    bridge, _ = bridge_and_client
    kinds = _fw_listeners(bridge)
    bridge._on_message(None, None, msg("stiebel-debug/status", b"\xff"))
    bridge._on_message(None, None, msg("stiebel-debug/status", [1, 2]))
    assert kinds == []
    assert bridge._fw == "unknown"


def test_debug_ignores_produktiv_offline(bridge_and_client):
    bridge, _ = bridge_and_client
    events = _avail_listeners(bridge)
    bridge._on_message(None, None, msg("stiebel-debug/status", {"fw": "debug"}))
    bridge._on_message(None, None, msg("stiebel-debug/availability", "online"))
    bridge._on_message(None, None, msg("heatingpump/status", "offline"))  # retained LWT der Produktiv-FW
    assert events == [("source", "debug"), ("avail", "online")]  # produktiv-Meldung ohne Emission
    assert bridge._combined_availability() == "online"


def test_debug_offline_from_debug_topic_counts(bridge_and_client):
    bridge, _ = bridge_and_client
    events = _avail_listeners(bridge)
    bridge._on_message(None, None, msg("stiebel-debug/status", {"fw": "debug"}))
    bridge._on_message(None, None, msg("stiebel-debug/availability", "online"))
    bridge._on_message(None, None, msg("stiebel-debug/availability", "offline"))
    assert events[-1] == ("avail", "offline")


def test_prod_still_uses_offline_wins(bridge_and_client):
    bridge, _ = bridge_and_client
    events = _avail_listeners(bridge)
    bridge._on_message(None, None, msg("stiebel-debug/status", {"rx": 0}))  # prod
    bridge._on_message(None, None, msg("stiebel-debug/availability", "online"))
    bridge._on_message(None, None, msg("heatingpump/status", "offline"))
    assert events[-1] == ("avail", "offline")


def test_firmware_switch_recomputes_availability(bridge_and_client):
    bridge, _ = bridge_and_client
    events = _avail_listeners(bridge)
    bridge._on_message(None, None, msg("heatingpump/status", "offline"))  # unknown: Produktiv-LWT
    assert events[-1] == ("avail", "offline")
    bridge._on_message(None, None, msg("stiebel-debug/availability", "online"))
    bridge._on_message(None, None, msg("stiebel-debug/status", {"fw": "debug"}))
    assert events[-1] == ("avail", "online")


def test_firmware_back_to_prod_restores_offline(bridge_and_client):
    bridge, _ = bridge_and_client
    events = _avail_listeners(bridge)
    bridge._on_message(None, None, msg("stiebel-debug/status", {"fw": "debug"}))
    bridge._on_message(None, None, msg("stiebel-debug/availability", "online"))
    bridge._on_message(None, None, msg("heatingpump/status", "offline"))
    bridge._on_message(None, None, msg("stiebel-debug/status", {"rx": 0}))  # prod
    assert events[-1] == ("avail", "offline")


def test_disconnect_resets_firmware_to_unknown(bridge_and_client):
    bridge, client = bridge_and_client
    kinds = _fw_listeners(bridge)
    bridge._on_message(None, None, msg("stiebel-debug/status", {"fw": "debug"}))
    bridge.disconnect_from()
    assert kinds == ["debug", "unknown"]


def test_connect_resets_firmware_to_unknown(bridge_and_client, monkeypatch):
    bridge, _ = bridge_and_client
    kinds = _fw_listeners(bridge)
    bridge._on_message(None, None, msg("stiebel-debug/status", {"fw": "debug"}))

    class FakeMqtt:
        def __init__(self, client_id="", clean_session=True):
            self.on_connect = self.on_disconnect = self.on_message = None

        def username_pw_set(self, user, password):
            pass

        def connect(self, host, port, keepalive=60):
            pass

        def loop_start(self):
            pass

        def disconnect(self):
            pass

        def loop_stop(self):
            pass

    monkeypatch.setattr(mqtt_client.mqtt, "Client", FakeMqtt)
    bridge.connect_to("broker", 1883, "user", "pw")
    assert kinds == ["debug", "unknown"]
    assert bridge._fw == "unknown"
