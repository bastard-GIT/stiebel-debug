"""Tests fuer die Produktiv-API der MqttBridge (gui/mqtt_client.py, Bus-Analyse).

Kein Broker: gesendet wird an einen Fake-Client, empfangen per _on_message mit Fake-msg.
Gilt nur bei fw "prod"; Debug und unbekannte Firmware bleiben beim Praefix stiebel-debug/.

Ausfuehren: QT_QPA_PLATFORM=offscreen python -I -m pytest -q tests/test_mqtt_prod.py
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

import mqtt_client  # noqa: E402
from mqtt_client import MqttBridge, FW_PROD, FW_DEBUG  # noqa: E402


# ---------------------------------------------------------------- Fakes

class FakeClient:
    """Speichert Payloads als dict (JSON-Topics stiebel-debug/...)."""

    def __init__(self):
        self.published = []
        self.subscribed = []

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, json.loads(payload)))

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)


class RawClient(FakeClient):
    """Speichert den Rohtext (Produktiv-Topics und Schalter-Payloads)."""

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload))


class FakeMqttClass:
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


def msg(topic: str, payload) -> SimpleNamespace:
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return SimpleNamespace(topic=topic, payload=payload)


@pytest.fixture
def prod():
    bridge = MqttBridge()
    client = RawClient()
    bridge._client = client
    bridge._set_firmware_kind(FW_PROD)
    return bridge, client


@pytest.fixture
def debug():
    bridge = MqttBridge()
    client = FakeClient()
    bridge._client = client
    bridge._set_firmware_kind(FW_DEBUG)
    return bridge, client


@pytest.fixture
def unknown():
    bridge = MqttBridge()
    client = FakeClient()
    bridge._client = client
    return bridge, client


def collect(bridge):
    sig = {name: [] for name in ("read_result", "write_result", "scan_progress",
                                 "scan_result", "member_result")}
    for name, lst in sig.items():
        getattr(bridge, name).connect(lst.append)
    return sig


def _status(bridge, payload):
    bridge._on_message(None, None, msg("heatingpump/scan/status", payload))


def _scan_status(state, mode="range", member="KESSEL", done=0, total=10, hits=0, seconds=1):
    return json.dumps({"state": state, "mode": mode, "member": member, "done": done,
                       "total": total, "hits": hits, "seconds": seconds})


def _result(bridge, topic_suffix, payload):
    bridge._on_message(None, None, msg("heatingpump/scan/" + topic_suffix, payload))


# ---------------------------------------------------------------- Senden: read

def test_send_read_prod_publishes_raw_text(prod):
    bridge, raw = prod
    bridge.send_read(0x514, 0x0013)
    assert raw.published == [("heatingpump/raw/read", "0x514 0x0013")]


def test_send_read_prod_switch_suffix(prod):
    bridge, raw = prod
    bridge.send_read(0x180, 0x0013, switch=0xF8)
    assert raw.published[-1] == ("heatingpump/raw/read", "0x180 0x0013 f8")


def test_send_read_prod_switch_fa_has_no_suffix(prod):
    bridge, raw = prod
    bridge.send_read(0x180, 0x0013, switch=0xFA)
    assert raw.published[-1] == ("heatingpump/raw/read", "0x180 0x0013")


def test_send_read_prod_unknown_id_0x7ff(prod):
    bridge, raw = prod
    bridge.send_read(0x7FF, 0x0001)
    assert raw.published[-1] == ("heatingpump/raw/read", "0x7ff 0x0001")


def test_send_read_prod_timeout_is_ignored(prod):
    bridge, raw = prod
    bridge.send_read(0x514, 0x0013, timeout_ms=5000)
    assert raw.published[-1] == ("heatingpump/raw/read", "0x514 0x0013")


def test_send_read_prod_fmt_auto_and_src_0x680_allowed(prod):
    bridge, raw = prod
    bridge.send_read(0x514, 0x0013, fmt="auto", src=0x680)
    assert raw.published == [("heatingpump/raw/read", "0x514 0x0013")]


@pytest.mark.parametrize("fmt", ["short", "long"])
def test_send_read_prod_rejects_format(prod, fmt):
    bridge, raw = prod
    with pytest.raises(ValueError, match="im Produktiv-Modus nicht verfuegbar"):
        bridge.send_read(0x514, 0x0013, fmt=fmt)
    assert raw.published == []


def test_send_read_prod_rejects_unknown_format(prod):
    bridge, raw = prod
    with pytest.raises(ValueError):
        bridge.send_read(0x514, 0x0013, fmt="medium")
    assert raw.published == []


def test_send_read_prod_rejects_src(prod):
    bridge, raw = prod
    with pytest.raises(ValueError, match="im Produktiv-Modus nicht verfuegbar"):
        bridge.send_read(0x514, 0x0013, src=0x500)
    assert raw.published == []


def test_send_read_prod_rejects_pc_member(prod):
    bridge, raw = prod
    with pytest.raises(ValueError):
        bridge.send_read(0x680, 0x0013)
    assert raw.published == []


def test_send_read_prod_rejects_index_over_16_bit(prod):
    bridge, raw = prod
    with pytest.raises(ValueError):
        bridge.send_read(0x514, 0x10000)
    assert raw.published == []


def test_send_read_prod_rejects_invalid_switch(prod):
    bridge, raw = prod
    with pytest.raises(ValueError):
        bridge.send_read(0x514, 0x0013, switch=0xF7)
    assert raw.published == []


# ---------------------------------------------------------------- Senden: write

def test_send_write_prod_publishes_raw_text(prod):
    bridge, raw = prod
    bridge.send_write(0x180, 0x0013, 0x0208)
    assert raw.published == [("heatingpump/raw/write", "0x180 0x0013 0x0208")]


def test_send_write_prod_rejects_format_other_than_auto(prod):
    bridge, raw = prod
    with pytest.raises(ValueError):
        bridge.send_write(0x180, 0x0013, 0x0208, fmt="long")
    assert raw.published == []


def test_send_write_prod_rejects_value_over_16_bit(prod):
    bridge, raw = prod
    with pytest.raises(ValueError):
        bridge.send_write(0x180, 0x0013, 0x10000)
    assert raw.published == []


def test_send_write_prod_rejects_pc_member(prod):
    bridge, raw = prod
    with pytest.raises(ValueError):
        bridge.send_write(0x680, 0x0013, 1)
    assert raw.published == []


# ---------------------------------------------------------------- Senden: Bus-Scan

def test_send_scan_prod_payload(prod):
    bridge, raw = prod
    bridge.send_scan(0x180, 0, 0xFF, 200, "auto", 0xF8)
    assert raw.published[-1] == ("heatingpump/scan/start", "KESSEL 0x0000 0x00ff f8")


def test_send_scan_prod_unknown_id_0x7ff(prod):
    bridge, raw = prod
    bridge.send_scan(0x7FF, 0x10, 0x1F, 200)
    assert raw.published[-1] == ("heatingpump/scan/start", "0x7ff 0x0010 0x001f")


def test_send_scan_prod_default_switch_omitted(prod):
    bridge, raw = prod
    bridge.send_scan(0x180, 0, 0xFF, 200)
    assert raw.published[-1] == ("heatingpump/scan/start", "KESSEL 0x0000 0x00ff")


def test_send_scan_prod_delay_is_ignored(prod):
    bridge, raw = prod
    bridge.send_scan(0x180, 0, 0xFF, 1)
    bridge.send_scan(0x180, 0, 0xFF, 9999)
    assert [p for _, p in raw.published] == ["KESSEL 0x0000 0x00ff", "KESSEL 0x0000 0x00ff"]


def test_send_scan_prod_switch_f9_suffix(prod):
    bridge, raw = prod
    bridge.send_scan(0x180, 0x0010, 0x0020, 200, switch=0xF9)
    assert raw.published[-1] == ("heatingpump/scan/start", "KESSEL 0x0010 0x0020 f9")


@pytest.mark.parametrize("fmt", ["short", "long"])
def test_send_scan_prod_rejects_format(prod, fmt):
    bridge, raw = prod
    with pytest.raises(ValueError):
        bridge.send_scan(0x180, 0, 0xFF, 200, fmt=fmt)
    assert raw.published == []


def test_send_scan_prod_rejects_pc_member(prod):
    bridge, raw = prod
    with pytest.raises(ValueError):
        bridge.send_scan(0x680, 0, 0xFF, 200)
    assert raw.published == []


def test_send_scan_prod_rejects_invalid_switch(prod):
    bridge, raw = prod
    with pytest.raises(ValueError):
        bridge.send_scan(0x180, 0, 0xFF, 200, switch=0xF7)
    assert raw.published == []


def test_send_scan_prod_rejects_from_above_to(prod):
    bridge, raw = prod
    with pytest.raises(ValueError):
        bridge.send_scan(0x180, 0x20, 0x10, 200)
    assert raw.published == []


def test_send_stop_prod_publishes_stop(prod):
    bridge, raw = prod
    bridge.send_stop()
    assert raw.published == [("heatingpump/scan/stop", "stop")]


# ---------------------------------------------------------------- Senden: Member-Scan

def test_send_member_scan_default_payload(prod):
    bridge, raw = prod
    bridge.send_member_scan()
    assert raw.published == [("heatingpump/scan/members", "0x000 0x7ff 0x000b")]


def test_send_member_scan_range_payload(prod):
    bridge, raw = prod
    bridge.send_member_scan(0x100, 0x1FF, 0x0010)
    assert raw.published[-1] == ("heatingpump/scan/members", "0x100 0x1ff 0x0010")


def test_send_member_scan_rejects_lo_above_hi(prod):
    bridge, raw = prod
    with pytest.raises(ValueError):
        bridge.send_member_scan(0x200, 0x100)
    assert raw.published == []


def test_send_member_scan_rejects_debug(debug):
    bridge, client = debug
    with pytest.raises(ValueError, match="nur Produktiv-Firmware"):
        bridge.send_member_scan()
    assert client.published == []


def test_send_member_scan_rejects_unknown(unknown):
    bridge, client = unknown
    with pytest.raises(ValueError, match="nur Produktiv-Firmware"):
        bridge.send_member_scan()
    assert client.published == []


# ---------------------------------------------------------------- Debug / unbekannt unveraendert

def test_debug_send_read_uses_cmd_read(debug):
    bridge, client = debug
    bridge.send_read(0x180, 0x0003, fmt="long", switch=0xF8)
    assert client.published == [("stiebel-debug/cmd/read", {
        "member": "0x180", "index": "0x0003", "format": "long", "switch": "0xf8"})]


def test_debug_send_scan_uses_cmd_scan(debug):
    bridge, client = debug
    bridge.send_scan(0x180, 0x4E00, 0x4E0F, 200)
    assert client.published == [("stiebel-debug/cmd/scan", {
        "member": "0x180", "from": "0x4e00", "to": "0x4e0f", "delay_ms": 200})]


def test_debug_send_write_uses_cmd_write(debug):
    bridge, client = debug
    bridge.send_write(0x180, 0x0013, 0x0208, fmt="long")
    assert client.published[-1][0] == "stiebel-debug/cmd/write"
    assert client.published[-1][1]["format"] == "long"


def test_debug_stop_uses_cmd_stop(debug):
    bridge, client = debug
    bridge.send_stop()
    assert client.published == [("stiebel-debug/cmd/stop", {})]


def test_unknown_send_read_uses_debug_topic(unknown):
    bridge, client = unknown
    bridge.send_read(0x180, 0x0003)
    assert client.published == [("stiebel-debug/cmd/read", {"member": "0x180", "index": "0x0003"})]


def test_unknown_send_write_and_stop_use_debug_topics(unknown):
    bridge, client = unknown
    bridge.send_write(0x180, 0x0003, 0x0001)
    bridge.send_stop()
    assert [t for t, _ in client.published] == ["stiebel-debug/cmd/write", "stiebel-debug/cmd/stop"]


def test_unknown_send_scan_uses_debug_topic(unknown):
    bridge, client = unknown
    bridge.send_scan(0x180, 0x4E00, 0x4E0F, 200, fmt="short")
    assert client.published == [("stiebel-debug/cmd/scan", {
        "member": "0x180", "from": "0x4e00", "to": "0x4e0f", "delay_ms": 200, "format": "short"})]


def test_debug_ignores_prod_scan_topics(debug):
    bridge, _ = debug
    sig = collect(bridge)
    _status(bridge, _scan_status("running"))
    _result(bridge, "kessel/0x0003", json.dumps({"name": "X", "raw": 1, "value": "v"}))
    _result(bridge, "members/0x180", json.dumps({"index": "0x000b", "raw": 5}))
    assert all(v == [] for v in sig.values())
    assert bridge.stale_ignored == 0


def test_debug_does_not_send_prod_topics(debug):
    bridge, client = debug
    bridge.send_read(0x514, 0x0013)
    assert all(not t.startswith("heatingpump/") for t, _ in client.published)


def test_raw_result_is_processed_without_firmware_gating(unknown):
    bridge, _ = unknown
    sig = collect(bridge)
    bridge._on_message(None, None, msg("heatingpump/raw/result", json.dumps(
        {"op": "read", "id": "0x514", "index": "0x0013", "switch": "fa", "raw": "0x0208",
         "status": "ok"})))
    assert len(sig["read_result"]) == 1


# ---------------------------------------------------------------- raw/result

def test_raw_result_read_ok(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge._on_message(None, None, msg("heatingpump/raw/result", json.dumps(
        {"op": "read", "id": "0x514", "index": "0x0013", "switch": "fa", "raw": "0x0208",
         "status": "ok"})))
    assert sig["read_result"] == [{
        "member": 0x514, "index": 0x13, "raw": 0x208, "switch": 0xFA, "src": 0x680,
        "status": "ok"}]


def test_raw_result_read_timeout_without_raw_gives_zero(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge._on_message(None, None, msg("heatingpump/raw/result", json.dumps(
        {"op": "read", "id": "0x514", "index": "0x0013", "switch": "f8", "status": "timeout"})))
    got = sig["read_result"][0]
    assert got["raw"] == 0
    assert got["switch"] == 0xF8
    assert got["status"] == "timeout"


def test_raw_result_write_verified(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge._on_message(None, None, msg("heatingpump/raw/result", json.dumps(
        {"op": "write", "id": "0x180", "index": "0x0013", "value": "0x0208",
         "status": "verified", "ack": 1, "readback": "0x0208"})))
    assert sig["write_result"] == [{
        "member": 0x180, "index": 0x13, "value": 0x208, "status": "verified", "ack": 1,
        "readback": 0x208}]


def test_raw_result_write_rejected(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge._on_message(None, None, msg("heatingpump/raw/result", json.dumps(
        {"op": "write", "id": "0x180", "index": "0x0013", "value": "0x0208",
         "status": "rejected", "ack": 0})))
    got = sig["write_result"][0]
    assert got["status"] == "rejected"
    assert got["ack"] == 0
    assert got["readback"] is None


def test_raw_result_write_timeout_without_ack(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge._on_message(None, None, msg("heatingpump/raw/result", json.dumps(
        {"op": "write", "id": "0x180", "index": "0x0013", "value": "0x0208", "status": "timeout"})))
    got = sig["write_result"][0]
    assert got["ack"] is None and got["readback"] is None


@pytest.mark.parametrize("payload", [
    b"\xff kein json",
    json.dumps({"op": "read", "id": "0x514", "index": "0x0013", "switch": "f7", "status": "ok"}),
    json.dumps({"op": "read", "id": "0x514", "index": "0x0013", "switch": "fa"}),
    json.dumps({"op": "other", "id": "0x514", "index": "0x0013", "status": "ok"}),
    json.dumps({"op": "write", "id": "0x180", "index": "0x0013", "status": "ok"}),
    json.dumps([1, 2]),
])
def test_raw_result_invalid_is_ignored(prod, payload):
    bridge, _ = prod
    sig = collect(bridge)
    bridge._on_message(None, None, msg("heatingpump/raw/result", payload))
    assert sig["read_result"] == [] and sig["write_result"] == []


# ---------------------------------------------------------------- Subscribe

def test_prod_subscribes_raw_result_and_scan_wildcard(unknown):
    bridge, _ = unknown
    sub = FakeClient()
    bridge._on_connect(sub, None, None, 0)
    assert "heatingpump/raw/result" in sub.subscribed
    assert "heatingpump/scan/#" in sub.subscribed
    assert "stiebel-debug/rx" in sub.subscribed


def test_connect_failure_does_not_subscribe(unknown):
    bridge, _ = unknown
    sub = FakeClient()
    bridge._on_connect(sub, None, None, 5)
    assert sub.subscribed == []


# ---------------------------------------------------------------- Retained-Gating: Bus-Scan

def test_retained_status_and_result_before_scan_are_ignored(prod):
    bridge, _ = prod
    sig = collect(bridge)
    _status(bridge, _scan_status("done", member="KESSEL", done=10, total=10, hits=3))
    _result(bridge, "kessel/0x0003", json.dumps({"name": "X", "raw": 1, "value": "v"}))
    _result(bridge, "members/0x180", json.dumps({"index": "0x000b", "raw": 5}))
    assert all(v == [] for v in sig.values())
    assert bridge.stale_ignored == 3


def test_retained_running_before_scan_is_ignored(prod):
    bridge, _ = prod
    sig = collect(bridge)
    _status(bridge, _scan_status("running"))
    assert sig["scan_progress"] == []
    assert bridge.stale_ignored == 1


def test_old_retained_done_after_scan_is_ignored(prod):
    bridge, raw = prod
    sig = collect(bridge)
    bridge.send_scan(0x180, 0, 0xFF, 200)
    _status(bridge, _scan_status("done", member="KESSEL", done=10, total=10, hits=3))
    assert sig["scan_progress"] == []
    assert bridge.stale_ignored == 1


def test_running_then_hit_emits_result(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_scan(0x180, 0, 0xFF, 200)
    _status(bridge, _scan_status("running", member="KESSEL"))
    assert len(sig["scan_progress"]) == 1
    assert sig["scan_progress"][0]["state"] == "running"
    assert sig["scan_progress"][0]["switch"] == 0xFA
    _result(bridge, "kessel/0x0003", json.dumps({"name": "Vorlauf", "raw": 258, "value": "25.8"}))
    assert sig["scan_result"] == [{
        "member": 0x180, "switch": 0xFA,
        "items": [{"index": 0x0003, "raw": 258, "status": "ok", "switch": 0xFA,
                   "name": "Vorlauf", "value": "25.8"}],
    }]


def test_scan_name_match_is_case_insensitive(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_scan(0x180, 0, 0xFF, 200)
    _status(bridge, _scan_status("running", member="KESSEL"))
    _result(bridge, "KESSEL/0x0004", json.dumps({"name": "A", "raw": 1, "value": "1"}))
    assert len(sig["scan_result"]) == 1
    assert sig["scan_result"][0]["items"][0]["index"] == 0x0004


def test_scan_result_wrong_switch_suffix_is_ignored(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_scan(0x180, 0, 0xFF, 200)  # Switch fa
    _status(bridge, _scan_status("running", member="KESSEL"))
    _result(bridge, "kessel/0x0003/f8", json.dumps({"name": "A", "raw": 1, "value": "1"}))
    assert sig["scan_result"] == []
    assert bridge.stale_ignored == 1


def test_scan_result_wrong_name_is_ignored(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_scan(0x180, 0, 0xFF, 200)
    _status(bridge, _scan_status("running", member="KESSEL"))
    _result(bridge, "atez/0x0003", json.dumps({"name": "A", "raw": 1, "value": "1"}))
    assert sig["scan_result"] == []


def test_scan_switch_f8_matches_suffix_f8(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_scan(0x180, 0, 0xFF, 200, switch=0xF8)
    _status(bridge, _scan_status("running", member="KESSEL"))
    assert sig["scan_progress"][0]["switch"] == 0xF8
    _result(bridge, "kessel/0x0003/f8", json.dumps({"name": "A", "raw": 1, "value": "1"}))
    assert sig["scan_result"][0]["switch"] == 0xF8
    assert sig["scan_result"][0]["items"][0]["switch"] == 0xF8


def test_scan_result_before_running_is_ignored(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_scan(0x180, 0, 0xFF, 200)
    _result(bridge, "kessel/0x0003", json.dumps({"name": "A", "raw": 1, "value": "1"}))
    assert sig["scan_result"] == []


def test_scan_running_with_other_mode_or_member_is_ignored(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_scan(0x180, 0, 0xFF, 200)
    _status(bridge, _scan_status("running", mode="members", member="KESSEL"))
    _status(bridge, _scan_status("running", member="ATEZ"))
    assert sig["scan_progress"] == []
    assert bridge.stale_ignored == 2
    _status(bridge, _scan_status("running", member="KESSEL"))
    assert len(sig["scan_progress"]) == 1


def test_scan_done_emits_progress_then_ignores_results(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_scan(0x180, 0, 0xFF, 200)
    _status(bridge, _scan_status("running", member="KESSEL"))
    _status(bridge, _scan_status("done", member="KESSEL", done=256, total=256, hits=1))
    assert [p["state"] for p in sig["scan_progress"]] == ["running", "done"]
    assert sig["scan_progress"][1]["done"] == 256
    assert sig["scan_progress"][1]["switch"] == 0xFA
    _result(bridge, "kessel/0x0003", json.dumps({"name": "A", "raw": 1, "value": "1"}))
    assert sig["scan_result"] == []
    assert bridge.stale_ignored == 1


def test_scan_stopped_clears_scan(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_scan(0x180, 0, 0xFF, 200)
    _status(bridge, _scan_status("running", member="KESSEL"))
    _status(bridge, _scan_status("stopped", member="KESSEL"))
    assert sig["scan_progress"][-1]["state"] == "stopped"
    assert bridge._prod_scan is None


def test_scan_done_before_running_is_ignored_and_keeps_scan(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_scan(0x180, 0, 0xFF, 200)
    _status(bridge, _scan_status("done", member="KESSEL"))
    assert sig["scan_progress"] == []
    assert bridge._prod_scan is not None
    assert bridge._prod_scan["running"] is False


def test_broken_scan_result_payload_is_ignored(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_scan(0x180, 0, 0xFF, 200)
    _status(bridge, _scan_status("running", member="KESSEL"))
    _result(bridge, "kessel/0x0003", b"\xff kein json")
    _result(bridge, "kessel/0x0003", json.dumps({"name": "A", "raw": "x", "value": "1"}))
    assert sig["scan_result"] == []


def test_broken_scan_status_is_ignored(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_scan(0x180, 0, 0xFF, 200)
    _status(bridge, b"\xff kein json")
    _status(bridge, json.dumps({"state": "running", "mode": "range", "member": "KESSEL"}))  # ohne Zaehler
    assert sig["scan_progress"] == []


# ---------------------------------------------------------------- Retained-Gating: Member-Scan

def test_member_scan_running_then_results(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_member_scan()
    _status(bridge, _scan_status("running", mode="members", member="members"))
    assert sig["scan_progress"][0]["switch"] == 0xFA
    assert sig["scan_progress"][0]["mode"] == "members"
    _result(bridge, "members/0x180", json.dumps({"index": "0x000b", "raw": 5}))
    _result(bridge, "members/0x514", json.dumps({"index": "0x000b", "raw": 7}))
    assert sig["member_result"] == [
        {"id": 0x180, "index": 0x000B, "raw": 5},
        {"id": 0x514, "index": 0x000B, "raw": 7},
    ]


def test_member_result_before_running_is_ignored(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_member_scan()
    _result(bridge, "members/0x180", json.dumps({"index": "0x000b", "raw": 5}))
    assert sig["member_result"] == []
    assert bridge.stale_ignored == 1


def test_retained_member_result_before_scan_is_ignored(prod):
    bridge, _ = prod
    sig = collect(bridge)
    _result(bridge, "members/0x180", json.dumps({"index": "0x000b", "raw": 5}))
    assert sig["member_result"] == []
    assert bridge.stale_ignored == 1


def test_member_scan_ignores_range_status_and_range_results(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_member_scan()
    _status(bridge, _scan_status("running", mode="range", member="KESSEL"))
    _result(bridge, "kessel/0x0003", json.dumps({"name": "A", "raw": 1, "value": "1"}))
    assert sig["scan_progress"] == [] and sig["scan_result"] == []
    assert bridge.stale_ignored == 2


def test_range_scan_ignores_member_results(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_scan(0x180, 0, 0xFF, 200)
    _status(bridge, _scan_status("running", member="KESSEL"))
    _result(bridge, "members/0x180", json.dumps({"index": "0x000b", "raw": 5}))
    assert sig["member_result"] == []


def test_member_scan_done_clears_and_ignores_results(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_member_scan()
    _status(bridge, _scan_status("running", mode="members", member="members"))
    _status(bridge, _scan_status("done", mode="members", member="members"))
    assert sig["scan_progress"][-1]["state"] == "done"
    _result(bridge, "members/0x180", json.dumps({"index": "0x000b", "raw": 5}))
    assert sig["member_result"] == []


def test_member_result_with_bad_index_is_ignored(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_member_scan()
    _status(bridge, _scan_status("running", mode="members", member="members"))
    _result(bridge, "members/0x180", json.dumps({"index": "000b", "raw": 5}))
    _result(bridge, "members/0x180", json.dumps({"index": "0x000b", "raw": "5"}))
    assert sig["member_result"] == []


# ---------------------------------------------------------------- Scan-Zustand loeschen

def test_disconnect_clears_scan(prod):
    bridge, _ = prod
    bridge.send_scan(0x180, 0, 0xFF, 200)
    bridge.disconnect_from()
    assert bridge._prod_scan is None


def test_connect_clears_scan(prod, monkeypatch):
    bridge, _ = prod
    monkeypatch.setattr(mqtt_client.mqtt, "Client", FakeMqttClass)
    bridge.send_scan(0x180, 0, 0xFF, 200)
    bridge.connect_to("broker", 1883, "", "")
    assert bridge._prod_scan is None


def test_firmware_change_clears_scan(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_scan(0x180, 0, 0xFF, 200)
    bridge._on_message(None, None, msg("stiebel-debug/status", {"fw": "debug"}))
    assert bridge._prod_scan is None
    _status(bridge, _scan_status("running", member="KESSEL"))
    assert sig["scan_progress"] == []


def test_stale_scan_after_disconnect_is_ignored(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_scan(0x180, 0, 0xFF, 200)
    bridge.disconnect_from()
    bridge._set_firmware_kind(FW_PROD)
    _status(bridge, _scan_status("running", member="KESSEL"))
    _status(bridge, _scan_status("done", member="KESSEL"))
    assert sig["scan_progress"] == []


# ---------------------------------------------------------------- Empfang: Fehler

def test_prod_scan_status_broken_fields_not_emitted(prod):
    bridge, _ = prod
    sig = collect(bridge)
    bridge.send_scan(0x180, 0, 0xFF, 200)
    bridge._on_message(None, None, msg("heatingpump/scan/status", json.dumps(
        {"state": "running", "mode": "range", "member": "KESSEL", "done": "x", "total": 1,
         "hits": 0, "seconds": 0})))
    assert sig["scan_progress"] == []
