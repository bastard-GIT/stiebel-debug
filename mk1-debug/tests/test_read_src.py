"""Tests Absender-ID und Timeout bei cmd/read (INTERFACES 12): Bridge und Abfrage-Tab.

Ausfuehren: C:\\temp\\stiebel-Remote\\.venv\\Scripts\\python.exe -I -B -m pytest C:\\temp\\stiebel-Remote\\mk1-debug\\tests\\test_read_src.py -q
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_mqtt_bridge import FakeBridge, bridge_and_client, db, msg, qapp  # noqa: E402,F401
import tab_query  # noqa: E402


class SrcBridge(FakeBridge):
    def send_read(self, member, index, fmt=None, switch=None, src=None, timeout_ms=None):
        self.reads.append((member, index, fmt, switch, src, timeout_ms))


def test_send_read_src_and_timeout(bridge_and_client):
    bridge, client = bridge_and_client
    bridge.send_read(0x100, 0x4F4D, src=0x700, timeout_ms=3000)
    assert client.published[-1] == (
        "stiebel-debug/cmd/read",
        {"member": "0x100", "index": "0x4f4d", "src": "0x700", "timeout_ms": 3000},
    )


def test_send_read_default_src_and_timeout_omitted(bridge_and_client):
    bridge, client = bridge_and_client
    bridge.send_read(0x100, 0x4F4D, src=0x680, timeout_ms=1000)
    assert client.published[-1][1] == {"member": "0x100", "index": "0x4f4d"}


@pytest.mark.parametrize("src,tmo", [(0x800, None), (0x100, None), (None, 50), (None, 20000)])
def test_send_read_invalid_src_or_timeout(bridge_and_client, src, tmo):
    bridge, _client = bridge_and_client
    with pytest.raises(ValueError):
        bridge.send_read(0x100, 0x4F4D, src=src, timeout_ms=tmo)


def test_read_result_src_parsed(bridge_and_client):
    bridge, _client = bridge_and_client
    got = []
    bridge.read_result.connect(got.append)
    bridge._on_message(None, None, msg("stiebel-debug/read/result",
                                       {"member": "0x100", "index": "0x0199", "switch": "0xfa",
                                        "raw": "0x01f6", "status": "ok", "src": "0x700"}))
    bridge._on_message(None, None, msg("stiebel-debug/read/result",
                                       {"member": "0x100", "index": "0x0199", "switch": "0xfa",
                                        "raw": "0x01f6", "status": "ok"}))
    assert got[0]["src"] == 0x700
    assert got[1]["src"] == 0x680


def test_query_tab_sends_src_and_timeout(qapp, db):
    bridge = SrcBridge()
    tab = tab_query.QueryTab(db, bridge)
    tab._member_combo.setEditText("0x100")
    tab._src_combo.setEditText("0x700")
    tab._timeout_spin.setValue(3000)
    tab._index_edit.setText("0x4f4d")
    tab._on_query()
    assert bridge.reads[-1] == (0x100, 0x4F4D, "auto", 0xFA, 0x700, 3000)


def test_query_tab_default_sends_no_src(qapp, db):
    bridge = SrcBridge()
    tab = tab_query.QueryTab(db, bridge)
    tab._member_combo.setEditText("0x100")
    tab._index_edit.setText("0x4f4d")
    tab._on_query()
    assert bridge.reads[-1][4:] == (None, None)


def test_query_tab_rejects_src_equal_member(qapp, db):
    bridge = SrcBridge()
    tab = tab_query.QueryTab(db, bridge)
    tab._member_combo.setEditText("0x100")
    tab._src_combo.setEditText("0x100")
    tab._index_edit.setText("0x4f4d")
    tab._on_query()
    assert bridge.reads == []
    assert "gleich" in tab._hint_label.text()


def test_query_tab_history_shows_src(qapp, db):
    bridge = SrcBridge()
    tab = tab_query.QueryTab(db, bridge)
    bridge.read_result.emit({"member": 0x100, "index": 0x0199, "switch": 0xFA, "raw": 0x01F6,
                             "status": "ok", "src": 0x700})
    assert tab._table.item(0, tab_query.HEADERS.index("Modus")).text() == "Lesen von 0x700"
