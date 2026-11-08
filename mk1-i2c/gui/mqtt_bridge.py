"""MQTT-Bruecke fuer die mk1-i2c-GUI (Schnittstelle laut INTERFACES.md, Abschnitt 2 und 4.2).

paho-mqtt 1.6.x: Client(...) ohne CallbackAPIVersion. paho laeuft ueber loop_start
im eigenen Thread; alle Ergebnisse gehen ausschliesslich ueber Qt-Signale an die GUI.
Das Passwort wird weder gespeichert noch geloggt.
"""

from __future__ import annotations

import json
import time

import paho.mqtt.client as mqtt
from PySide6.QtCore import QObject, Signal

TOPIC_PREFIX = "stiebel-i2c/"
RX_TOPICS = ("rx", "status", "scan/result", "availability")
SNIFF_MODES = ("all", "changed")


class ClockSync:
    """Zeitschaetzung ohne ESP-Zeitstempel (Fallback 'pc').

    Pro Frame gilt: Ankunft (PC) - t/1000 = Offset + Latenz. Der kleinste Wert
    seit dem letzten Reset hat die geringste Latenz und wird benutzt.
    Reset, wenn ein Batch Zeitwerte kleiner als das bisher groesste t enthaelt
    (ESP-Neustart).
    """

    def __init__(self) -> None:
        self._offset: float | None = None
        self._last_t: int | None = None

    def update(self, arrival_unix_s: float, t_ms_list) -> None:
        if not t_ms_list:
            return
        t_min = min(t_ms_list)
        if self._last_t is not None and t_min < self._last_t:
            self._offset = None
            self._last_t = None
        candidate = min(arrival_unix_s - t / 1000 for t in t_ms_list)
        if self._offset is None or candidate < self._offset:
            self._offset = candidate
        self._last_t = max(t_ms_list) if self._last_t is None else max(self._last_t, max(t_ms_list))

    def estimate(self, t_ms: int) -> float | None:
        if self._offset is None:
            return None
        return t_ms / 1000 + self._offset


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class MqttBridge(QObject):
    connected = Signal(bool)
    rx_frames = Signal(list)         # Liste von Frame-Zeilen (mit ts, ts_src)
    status = Signal(dict)
    availability = Signal(str)       # "online" | "offline"
    scan_result = Signal(dict)       # Ergebnis des Bus-Scans (scan/result)

    _now = time.time                 # Ankunftszeit; in Tests ersetzbar

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._client = None
        self._clock = ClockSync()

    # ------------------------------------------------------------------
    # Verbindung
    # ------------------------------------------------------------------
    def connect_to(self, host: str, port: int, user: str, password: str) -> None:
        self.disconnect_from()
        self._clock = ClockSync()
        client = mqtt.Client(client_id="", clean_session=True)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        if user:
            client.username_pw_set(user, password)
        self._client = client
        try:
            client.connect(host, int(port), keepalive=30)
        except Exception:
            self._client = None
            self.connected.emit(False)
            return
        client.loop_start()

    def disconnect_from(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            client.disconnect()
        except Exception:
            pass
        try:
            client.loop_stop()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Senden (qos 0)
    # ------------------------------------------------------------------
    def _publish(self, suffix: str, payload: dict) -> None:
        client = self._client
        if client is None:
            return
        try:
            client.publish(TOPIC_PREFIX + suffix, json.dumps(payload, separators=(",", ":")), qos=0)
        except Exception:
            pass

    def send_sniff(self, on: bool, mode: str = "changed") -> None:
        """mode 'all' oder 'changed', sonst ValueError."""
        if mode not in SNIFF_MODES:
            raise ValueError(f"unbekannter Modus: {mode!r}")
        self._publish("cmd/sniff", {"on": bool(on), "mode": mode})

    def send_scan(self, from_addr: int | None = None, to_addr: int | None = None) -> None:
        """Bus-Scan anfordern (ESP wird kurz I2C-Master). Ohne Angabe: Firmware-Standard 0x08..0x77."""
        payload: dict = {}
        if from_addr is not None:
            payload["from"] = int(from_addr)
        if to_addr is not None:
            payload["to"] = int(to_addr)
        self._publish("cmd/scan", payload)

    # ------------------------------------------------------------------
    # paho-Callbacks (laufen im paho-Thread)
    # ------------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            for topic in RX_TOPICS:
                try:
                    client.subscribe(TOPIC_PREFIX + topic, qos=0)
                except Exception:
                    pass
        self.connected.emit(rc == 0)

    def _on_disconnect(self, client, userdata, rc):
        self.connected.emit(False)

    def _on_message(self, client, userdata, msg) -> None:
        """Nachricht verarbeiten. Fehlerhafte Nachrichten werden ignoriert.

        Ohne Netzwerk testbar: Tests rufen diese Methode mit einem Fake-Objekt
        (Attribute topic, payload) direkt auf.
        """
        try:
            topic = msg.topic
            if not topic.startswith(TOPIC_PREFIX):
                return
            suffix = topic[len(TOPIC_PREFIX):]
            if suffix not in RX_TOPICS:
                return
            text = msg.payload.decode("utf-8")
            if suffix == "availability":
                value = text.strip()
                if value in ("online", "offline"):
                    self.availability.emit(value)
                return

            data = json.loads(text)

            if suffix == "rx":
                if not isinstance(data, list):
                    return
                self._handle_rx(data)
            elif suffix == "status":
                if isinstance(data, dict):
                    self.status.emit(data)
            elif suffix == "scan/result":
                if isinstance(data, dict):
                    self.scan_result.emit(data)
        except Exception:
            # Fehlerhafte Nachricht (kein UTF-8, kein JSON, falsche Felder): ignorieren.
            return

    def _handle_rx(self, data: list) -> None:
        """rx-Batch: Items pruefen, Zeit zuordnen, als Frame-Zeilen emittieren."""
        arrival = self._now()
        pending = []  # (Item, Frame) ohne Zeitfelder
        for item in data:
            try:
                t = int(item["t"])
                raw = item["d"]
                ack = item["a"]
                flags = item["f"]
                u_raw = item.get("u")
                u = 0 if u_raw is None else int(u_raw)
                if not isinstance(raw, str) or not isinstance(ack, str) or not _is_int(flags):
                    continue
                payload = bytes.fromhex(raw)
                if len(ack) != len(payload):
                    continue
                pending.append((u, {"t": t, "u": u, "data": payload, "ack": ack, "flags": flags}))
            except Exception:
                continue
        # Fallback 'pc': nur Frames ohne ESP-Zeit speisen die Schaetzung.
        pc_t = [frame["t"] for u, frame in pending if u <= 0]
        if pc_t:
            self._clock.update(arrival, pc_t)
        frames = []
        for u, frame in pending:
            if u > 0:
                frame["ts"] = round(u / 1000, 3)
                frame["ts_src"] = "esp"
            else:
                est = self._clock.estimate(frame["t"])
                if est is None:
                    continue
                frame["ts"] = round(est, 3)
                frame["ts_src"] = "pc"
            frames.append(frame)
        if frames:
            self.rx_frames.emit(frames)
