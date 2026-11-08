"""Tests fuer gui/mqtt_bridge.py (ohne Netz, ohne Broker).

Kein Broker: rx/status/availability werden ueber _on_message mit Fake-msg gefuettert,
gesendet wird an einen Fake-Client. connect_to wird nur mit einem Fake-paho-Client getestet,
der nie eine Verbindung aufbaut.

Ausfuehren: C:\\temp\\stiebel-Remote\\.venv\\Scripts\\python.exe -I -B -m pytest C:\\temp\\stiebel-Remote\\mk1-i2c\\tests\\test_mqtt_bridge.py -q
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

GUI_DIR = Path(__file__).resolve().parents[1] / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

import pytest  # noqa: E402

import mqtt_bridge  # noqa: E402
from mqtt_bridge import RX_TOPICS, TOPIC_PREFIX, ClockSync, MqttBridge  # noqa: E402


class FakeClient:
    """Ersatz fuer paho-Client: sammelt Publish/Subscribe, baut nie eine Verbindung auf."""

    def __init__(self):
        self.published = []    # (topic, payload dict)
        self.subscribed = []

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, json.loads(payload)))

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)


class FakePahoClient:
    """Ersatz fuer paho.mqtt.client.Client fuer connect_to: connect() kann scheitern oder klappen."""
    fail_connect = False
    instances = []

    def __init__(self, client_id="", clean_session=True):
        self.connected_to = None
        self.loop_started = False
        self.loop_stopped = False
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        FakePahoClient.instances.append(self)

    def username_pw_set(self, user, password):
        self._user = user  # Passwort wird bewusst nicht gespeichert

    def connect(self, host, port, keepalive=60):
        if FakePahoClient.fail_connect:
            raise OSError("kein Netz im Test")
        self.connected_to = (host, port)

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_stopped = True

    def disconnect(self):
        pass


def msg(topic: str, payload) -> SimpleNamespace:
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return SimpleNamespace(topic=topic, payload=payload)


def _collect(signal) -> list:
    got: list = []
    signal.connect(lambda *args: got.append(args[0] if len(args) == 1 else args))
    return got


def _item(t, d="00e3201001", a=None, f=0, u=1761400000000):
    item = {"t": t, "d": d, "a": a if a is not None else "+" * (len(d) // 2), "f": f}
    if u is not None:
        item["u"] = u
    return item


@pytest.fixture
def bridge():
    b = MqttBridge()
    b._client = FakeClient()
    return b


# ---------------------------------------------------------------- rx

def test_rx_with_esp_time_u_gt_0(bridge):
    rows = _collect(bridge.rx_frames)
    item = {"t": 1000, "u": 1761400000123, "d": "00e3201001", "a": "++++-", "f": 0}
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "rx", [item]))
    assert rows == [[{"t": 1000, "u": 1761400000123, "data": bytes.fromhex("00e3201001"),
                      "ack": "++++-", "flags": 0, "ts": 1761400000.123, "ts_src": "esp"}]]


def test_rx_u_zero_uses_pc_clock(bridge):
    rows = _collect(bridge.rx_frames)
    bridge._now = lambda: 1000.0
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "rx", [_item(5000, u=0)]))
    assert rows[0][0]["ts_src"] == "pc"
    assert rows[0][0]["ts"] == 1000.0            # 1000 - 5 + 5

    bridge._now = lambda: 1002.0                 # groessere Latenz: Schaetzung bleibt beim kleinsten Offset
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "rx", [_item(6000, u=0)]))
    assert rows[1][0]["ts"] == 1001.0


def test_rx_u_missing_counts_as_pc(bridge):
    rows = _collect(bridge.rx_frames)
    bridge._now = lambda: 100.0
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "rx", [_item(1000, u=None)]))
    assert rows[0][0]["ts_src"] == "pc"
    assert rows[0][0]["ts"] == 100.0


def test_rx_esp_items_do_not_feed_pc_clock(bridge):
    rows = _collect(bridge.rx_frames)
    bridge._now = lambda: 500.0
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "rx", [_item(9000, u=1761400000000)]))
    assert rows[0][0]["ts_src"] == "esp"
    assert bridge._clock.estimate(1000) is None  # Schaetzung nie gespeist


def test_clock_resets_on_esp_restart():
    clock = ClockSync()
    assert clock.estimate(1) is None
    clock.update(1010.0, [10000])                # Offset 1000
    clock.update(2000.0, [500])                  # t kleiner als bisher: Reset, neuer Offset 1999.5
    assert clock.estimate(500) == 2000.0


def test_broken_items_are_ignored(bridge):
    rows = _collect(bridge.rx_frames)
    good = _item(1)
    good2 = _item(2, d="01", a="-", f=8)
    batch = [
        good,
        {"t": "x", "d": "01", "a": "+", "f": 0, "u": 1},          # t nicht numerisch
        {"t": 3, "d": "zz", "a": "+", "f": 0, "u": 1},            # ungueltiger Hex
        {"t": 4, "d": "0102", "a": "+", "f": 0, "u": 1},          # a zu kurz
        {"t": 5, "d": "01", "a": "+", "f": True, "u": 1},         # f als bool
        {"t": 6, "d": "01", "a": "+", "u": 1},                    # f fehlt
        "kein dict",
        good2,
    ]
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "rx", batch))
    assert len(rows) == 1
    assert [r["t"] for r in rows[0]] == [1, 2]
    assert rows[0][1]["data"] == b"\x01" and rows[0][1]["ack"] == "-"


def test_rx_wrong_ack_length_item_dropped(bridge):
    rows = _collect(bridge.rx_frames)
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "rx", [_item(1, d="0102", a="+")]))
    assert rows == []


def test_rx_not_a_list_is_ignored(bridge):
    rows = _collect(bridge.rx_frames)
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "rx", {"t": 1}))
    assert rows == []


def test_rx_all_items_bad_emits_nothing(bridge):
    rows = _collect(bridge.rx_frames)
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "rx", [{"t": "x"}, None]))
    assert rows == []


# ---------------------------------------------------------------- status, scan, availability

def test_status_signal(bridge):
    got = _collect(bridge.status)
    payload = {"fw": "i2c", "frames": 5, "slave_overflow": 0}
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "status", payload))
    assert got == [payload]


def test_scan_result_signal(bridge):
    got = _collect(bridge.scan_result)
    payload = {"found": [42], "nack": 111, "arb_lost": 0, "retries": 0, "ms": 300, "ok": True}
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "scan/result", payload))
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "scan/result", [1]))   # kein dict: ignoriert
    assert got == [payload]


def test_availability_values(bridge):
    got = _collect(bridge.availability)
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "availability", "online"))
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "availability", " offline "))
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "availability", "vielleicht"))
    assert got == ["online", "offline"]


def test_unknown_topics_and_bad_payloads_ignored(bridge):
    rows = _collect(bridge.rx_frames)
    status = _collect(bridge.status)
    bridge._on_message(None, None, msg("andere/rx", [_item(1, u=5)]))
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "cmd/sniff", {"on": True}))
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "status", "kein json {"))
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "status", [1, 2]))
    bridge._on_message(None, None, msg(TOPIC_PREFIX + "rx", b"\xff\xfe"))
    assert rows == [] and status == []


def test_rx_topics_constant():
    assert RX_TOPICS == ("rx", "status", "scan/result", "availability")


# ---------------------------------------------------------------- senden

def test_send_sniff_payload(bridge):
    bridge.send_sniff(True, "all")
    bridge.send_sniff(False)
    assert bridge._client.published == [
        (TOPIC_PREFIX + "cmd/sniff", {"on": True, "mode": "all"}),
        (TOPIC_PREFIX + "cmd/sniff", {"on": False, "mode": "changed"}),
    ]


def test_send_sniff_invalid_mode_raises(bridge):
    with pytest.raises(ValueError):
        bridge.send_sniff(True, "alle")
    assert bridge._client.published == []


def test_send_scan_payload(bridge):
    bridge.send_scan()
    bridge.send_scan(0x10, 0x20)
    assert bridge._client.published == [
        (TOPIC_PREFIX + "cmd/scan", {}),
        (TOPIC_PREFIX + "cmd/scan", {"from": 16, "to": 32}),
    ]


def test_send_without_client_is_silent():
    b = MqttBridge()
    b.send_sniff(True, "changed")   # kein Fehler, nichts gesendet
    b.send_scan()


# ---------------------------------------------------------------- Verbindung (ohne Netz)

def test_on_connect_subscribes_and_reports(bridge):
    state = _collect(bridge.connected)
    client = FakeClient()
    bridge._on_connect(client, None, {}, 0)
    assert client.subscribed == [TOPIC_PREFIX + t for t in RX_TOPICS]
    assert state == [True]


def test_on_connect_refused_does_not_subscribe(bridge):
    state = _collect(bridge.connected)
    client = FakeClient()
    bridge._on_connect(client, None, {}, 5)
    assert client.subscribed == []
    assert state == [False]


def test_connect_failure_emits_false(monkeypatch):
    FakePahoClient.fail_connect = True
    FakePahoClient.instances = []
    monkeypatch.setattr(mqtt_bridge.mqtt, "Client", FakePahoClient)
    b = MqttBridge()
    state = _collect(b.connected)
    b.connect_to("192.0.2.1", 1883, "user", "geheim-xyz")
    assert state == [False]
    assert b._client is None


def test_connect_success_uses_loop_start_and_keeps_no_password(monkeypatch):
    FakePahoClient.fail_connect = False
    FakePahoClient.instances = []
    monkeypatch.setattr(mqtt_bridge.mqtt, "Client", FakePahoClient)
    b = MqttBridge()
    b.connect_to("192.0.2.1", "31883", "user", "geheim-xyz")
    client = FakePahoClient.instances[-1]
    assert client.connected_to == ("192.0.2.1", 31883)
    assert client.loop_started is True
    assert "geheim-xyz" not in repr(vars(b))
    b.disconnect_from()
    assert client.loop_stopped is True and b._client is None


def test_disconnect_without_connection_is_silent():
    MqttBridge().disconnect_from()
