"""Tests fuer gui/mqtt_client.py: send_write, write/result und Zeitstempel der rx-Frames.

Kein Broker: die Bridge wird ueber _on_message (paho-Callback) gefuettert, gesendet wird an
einen Fake-Client. Die Ankunftszeit (time.time) ist ueber bridge._now ersetzbar.

Ausfuehren: C:\\temp\\stiebel-Remote\\.venv\\Scripts\\python.exe -I -B -m pytest C:\\temp\\stiebel-Remote\\mk1-debug\\tests\\test_mqtt_write_time.py -q
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
from mqtt_client import ClockSync, MqttBridge  # noqa: E402


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


# ---------------------------------------------------------------- send_write

def test_send_write_payload(bridge_and_client):
    bridge, client = bridge_and_client
    bridge.send_write(0x201, 0x0013, 0x0208)
    assert client.published == [
        ("stiebel-debug/cmd/write", {"member": "0x201", "index": "0x0013", "value": "0x0208"}),
    ]


def test_send_write_format_only_when_not_auto(bridge_and_client):
    bridge, client = bridge_and_client
    bridge.send_write(0x201, 0x0013, 0x0208, fmt="short")
    assert client.published[-1] == (
        "stiebel-debug/cmd/write",
        {"member": "0x201", "index": "0x0013", "value": "0x0208", "format": "short"},
    )


@pytest.mark.parametrize("member", [0x680, 0x800, -1])
def test_send_write_rejects_bad_member(bridge_and_client, member):
    bridge, client = bridge_and_client
    with pytest.raises(ValueError):
        bridge.send_write(member, 0x0013, 0x0208)
    assert client.published == []


@pytest.mark.parametrize("value", [0x10000, -1])
def test_send_write_rejects_value_out_of_16_bit(bridge_and_client, value):
    bridge, client = bridge_and_client
    with pytest.raises(ValueError):
        bridge.send_write(0x201, 0x0013, value)
    assert client.published == []


def test_send_write_rejects_unknown_format(bridge_and_client):
    bridge, client = bridge_and_client
    with pytest.raises(ValueError):
        bridge.send_write(0x201, 0x0013, 0x0208, fmt="medium")
    assert client.published == []


# ---------------------------------------------------------------- write/result

def test_write_result_parsing_to_int(bridge_and_client):
    bridge, _ = bridge_and_client
    got = []
    bridge.write_result.connect(got.append)
    bridge._on_message(None, None, msg("stiebel-debug/write/result", {
        "member": "0x201", "index": "0x0013", "value": "0x0208",
        "status": "ok", "ack": "0x00", "readback": "0x0208",
    }))
    assert got == [{
        "member": 0x201, "index": 0x0013, "value": 0x0208,
        "status": "ok", "ack": 0, "readback": 0x0208,
    }]


def test_write_result_missing_fields_are_none(bridge_and_client):
    bridge, _ = bridge_and_client
    got = []
    bridge.write_result.connect(got.append)
    bridge._on_message(None, None, msg("stiebel-debug/write/result", {"status": "timeout"}))
    assert got == [{
        "member": None, "index": None, "value": None,
        "status": "timeout", "ack": None, "readback": None,
    }]


def test_write_result_topic_is_subscribed(bridge_and_client):
    bridge, _ = bridge_and_client
    sub_client = FakeClient()
    bridge._on_connect(sub_client, None, None, 0)
    assert "stiebel-debug/write/result" in sub_client.subscribed


def test_broken_write_result_is_ignored(bridge_and_client):
    bridge, _ = bridge_and_client
    got = []
    bridge.write_result.connect(got.append)
    bridge._on_message(None, None, msg("stiebel-debug/write/result", b"\xff not json"))
    bridge._on_message(None, None, msg("stiebel-debug/write/result", [1, 2]))
    assert got == []


# ---------------------------------------------------------------- rx-Zeitstempel

def _rx_item(t, **extra):
    item = {"t": t, "id": "0x180", "d": "0102", "tx": False}
    item.update(extra)
    return item


def test_rx_with_u_is_esp_time(bridge_and_client):
    bridge, _ = bridge_and_client
    frames = []
    bridge.rx_frames.connect(frames.append)
    bridge._now = lambda: 5000.0  # darf bei esp-Zeit keine Rolle spielen
    bridge._on_message(None, None, msg("stiebel-debug/rx", [
        _rx_item(1234, u=1700000000123),
    ]))
    f = frames[0][0]
    assert f["ts_src"] == "esp"
    assert f["ts"] == round(1700000000123 / 1000, 3)
    assert f["t"] == 1234 and f["id"] == 0x180 and f["data"] == b"\x01\x02"


def test_rx_without_u_is_pc_time(bridge_and_client):
    bridge, _ = bridge_and_client
    frames = []
    bridge.rx_frames.connect(frames.append)
    fake_now = 1_700_000_000.0
    bridge._now = lambda: fake_now
    bridge._on_message(None, None, msg("stiebel-debug/rx", [_rx_item(500)]))
    f = frames[0][0]
    assert f["ts_src"] == "pc"
    assert f["ts"] == pytest.approx(fake_now, abs=0.01)  # Ankunft 1.7e9 s, t=500 ms
    assert isinstance(f["ts"], float)


def test_rx_mixed_batch(bridge_and_client):
    bridge, _ = bridge_and_client
    frames = []
    bridge.rx_frames.connect(frames.append)
    bridge._now = lambda: 1000.0
    bridge._on_message(None, None, msg("stiebel-debug/rx", [
        _rx_item(500), _rx_item(600, u=2000500),
    ]))
    by_src = {f["ts_src"]: f for f in frames[0]}
    assert by_src["esp"]["ts"] == 2000.5
    assert by_src["pc"]["ts"] == pytest.approx(1000.0, abs=0.01)  # Ankunft 1000.0, t=500 ms


# ---------------------------------------------------------------- ClockSync

def test_clocksync_no_estimate_before_update():
    assert ClockSync().estimate(1000) is None


def test_clocksync_keeps_minimum_offset():
    cs = ClockSync()
    cs.update(100.0, [1000])      # Offset 99.0
    cs.update(105.0, [2000])      # Offset 103.0 (mehr Latenz) -> verworfen
    assert cs.estimate(3000) == pytest.approx(3.0 + 99.0)
    cs.update(100.5, [4000])      # Offset 96.5 -> neues Minimum
    assert cs.estimate(5000) == pytest.approx(5.0 + 96.5)


def test_clocksync_resets_on_smaller_t():
    cs = ClockSync()
    cs.update(100.0, [5000])      # Offset 95.0
    assert cs.estimate(5000) == pytest.approx(100.0)
    cs.update(101.0, [1000])      # t kleiner als 5000 -> ESP-Neustart, Reset
    assert cs.estimate(2000) == pytest.approx(2.0 + 100.0)


def test_clocksync_empty_batch_is_noop():
    cs = ClockSync()
    cs.update(100.0, [])
    assert cs.estimate(1000) is None


# ---------------------------------------------------------------- Status (ntp)

def test_status_passes_ntp_unchanged(bridge_and_client):
    bridge, _ = bridge_and_client
    got = []
    bridge.status.connect(got.append)
    bridge._on_message(None, None, msg("stiebel-debug/status", {"rx": 1, "ntp": True}))
    assert got[0]["ntp"] is True
