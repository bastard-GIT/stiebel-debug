"""MQTT-Bruecke fuer die mk1-debug-GUI (Schnittstelle laut INTERFACES.md, Abschnitt 5.2 und 8.3).

paho-mqtt 1.6.x: Client(...) ohne CallbackAPIVersion. paho laeuft ueber loop_start
im eigenen Thread; alle Ergebnisse gehen ausschliesslich ueber Qt-Signale an die GUI.
Das Passwort wird weder gespeichert noch geloggt.
"""

import json
import time

import paho.mqtt.client as mqtt
from PySide6.QtCore import QObject, Signal

TOPIC_PREFIX = "stiebel-debug/"

RX_TOPICS = (
    "rx",
    "read/result",
    "write/result",
    "scan/progress",
    "scan/result",
    "status",
    "availability",
)

# Verfuegbarkeit: Debug-Firmware (unter Praefix) und Produktiv-Firmware (eigenes Topic).
PROD_AVAILABILITY_TOPIC = "heatingpump/status"
AVAILABILITY_SOURCES = {
    "debug": TOPIC_PREFIX + "availability",
    "produktiv": PROD_AVAILABILITY_TOPIC,
}

# Firmware-Art aus stiebel-debug/status (Feld "fw"); fehlt das Feld, gilt "prod".
FW_DEBUG = "debug"
FW_PROD = "prod"
FW_UNKNOWN = "unknown"

SWITCH_DEFAULT = 0xFA            # aktueller Wert (Standard)
CMD_SWITCH_VALUES = (0xFA, 0xF8, 0xF9)
CMD_FORMAT_VALUES = (None, "auto", "short", "long")


def _to_int(value):
    """Hex-String ("0x180", "0X4E00", "4e00") oder int in int wandeln. Sonst ValueError/TypeError."""
    if isinstance(value, bool):
        raise TypeError("bool ist kein Hex-Wert")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value.strip(), 16)
    raise TypeError("unerwarteter Typ fuer Hex-Wert")


def _hex_id(value):
    return f"0x{value:03x}"


def _hex_index(value):
    return f"0x{value:04x}"


def _opt_int(value):
    """Wie _to_int, aber fehlendes Feld (None) bleibt None."""
    return None if value is None else _to_int(value)


class ClockSync:
    """Zeitschaetzung ohne ESP-Zeitstempel (Fallback 'pc').

    Pro Frame gilt: Ankunft (PC) - t/1000 = Offset + Latenz. Der kleinste Wert
    seit dem letzten Reset hat die geringste Latenz und wird benutzt.
    Reset, wenn ein Batch Zeitwerte kleiner als das bisher groesste t enthaelt
    (ESP-Neustart).
    """

    def __init__(self):
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


def _parse_switch(data: dict, default: int = SWITCH_DEFAULT) -> int:
    """Switch aus Payload; fehlt das Feld (aeltere Firmware), gilt default (0xFA)."""
    raw = data.get("switch")
    if raw is None:
        return default
    value = _to_int(raw)
    if not 0 <= value <= 0xFF:
        raise ValueError(f"Switch ausserhalb 8 Bit: {value}")
    return value


def _command_fields(fmt, switch) -> dict:
    """Optionale Payload-Felder format/switch fuer cmd/read und cmd/scan. Standardwerte werden weggelassen."""
    fields = {}
    if fmt not in CMD_FORMAT_VALUES:
        raise ValueError(f"unbekanntes Format: {fmt!r}")
    if fmt not in (None, "auto"):
        fields["format"] = fmt
    if switch is not None:
        if switch not in CMD_SWITCH_VALUES:
            raise ValueError(f"unbekannter Switch: {switch!r}")
        if switch != SWITCH_DEFAULT:
            fields["switch"] = f"0x{switch:02x}"
    return fields


class MqttBridge(QObject):
    connected = Signal(bool)
    rx_frames = Signal(list)         # Liste von dict {"t","id"(int),"data"(bytes),"tx"(bool)}
    read_result = Signal(dict)       # Payload von read/result, member/index/raw/switch als int
    write_result = Signal(dict)      # write/result: member/index/value/ack/readback als int|None, status str|None
    scan_progress = Signal(dict)     # index/switch als int
    scan_result = Signal(dict)       # items mit index/raw/switch als int
    status = Signal(dict)
    availability = Signal(str)       # kombiniert: "offline" gewinnt, sonst "online"
    availability_source = Signal(str)  # "debug" oder "produktiv", unmittelbar vor availability
    firmware_kind = Signal(str)      # "debug" | "prod" | "unknown"; nur bei Aenderung

    def __init__(self, parent=None):
        super().__init__(parent)
        self._client = None
        self._avail_by_source: dict[str, str | None] = {"debug": None, "produktiv": None}
        self._fw: str = FW_UNKNOWN
        self._clock = ClockSync()
        self._now = time.time  # Ankunftszeit; in Tests ersetzbar

    # ------------------------------------------------------------------
    # Verbindung
    # ------------------------------------------------------------------
    def connect_to(self, host: str, port: int, user: str, password: str) -> None:
        self.disconnect_from()
        self._avail_by_source = {"debug": None, "produktiv": None}
        self._clock = ClockSync()
        self._set_firmware_kind(FW_UNKNOWN)
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
        self._set_firmware_kind(FW_UNKNOWN)
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
            client.publish(TOPIC_PREFIX + suffix, json.dumps(payload), qos=0)
        except Exception:
            pass

    def send_sniff(self, on: bool, ids: list[int], types: list[int]) -> None:
        self._publish("cmd/sniff", {
            "on": bool(on),
            "ids": [_hex_id(i) for i in ids],
            "types": [int(t) for t in types],
        })

    def send_read(self, member: int, index: int, fmt=None, switch=None, src=None, timeout_ms=None) -> None:
        """fmt: None|"auto"|"short"|"long"; switch: None|0xFA|0xF8|0xF9. Ungueltige Werte: ValueError.
        src: Absender-CAN-ID (None/0x680 = Standard), timeout_ms: 100..10000 (None/1000 = Standard)."""
        payload = {
            "member": _hex_id(member),
            "index": _hex_index(index),
        }
        payload.update(_command_fields(fmt, switch))
        if src is not None and src != 0x680:
            if not 0 <= src <= 0x7FF or src == member:
                raise ValueError(f"ungueltiger Absender: {src!r}")
            payload["src"] = _hex_id(src)
        if timeout_ms is not None and timeout_ms != 1000:
            if not 100 <= timeout_ms <= 10000:
                raise ValueError(f"Timeout ausserhalb 100..10000 ms: {timeout_ms!r}")
            payload["timeout_ms"] = int(timeout_ms)
        self._publish("cmd/read", payload)

    def send_write(self, member: int, index: int, value: int, fmt='auto') -> None:
        """Schreibbefehl. Member 0..0x7FF (nicht 0x680), Index/Wert 0..0xFFFF, sonst ValueError."""
        if not 0 <= member <= 0x7FF or member == 0x680:
            raise ValueError(f"ungueltiger Member: {member!r}")
        if not 0 <= index <= 0xFFFF:
            raise ValueError(f"Index ausserhalb 16 Bit: {index!r}")
        if not 0 <= value <= 0xFFFF:
            raise ValueError(f"Wert ausserhalb 16 Bit: {value!r}")
        payload = {
            "member": _hex_id(member),
            "index": _hex_index(index),
            "value": _hex_index(value),
        }
        if fmt != 'auto':
            payload.update(_command_fields(fmt, None))
        self._publish("cmd/write", payload)

    def send_scan(self, member: int, from_idx: int, to_idx: int, delay_ms: int,
                  fmt=None, switch=None) -> None:
        """Wie send_read; ungueltige fmt/switch-Werte: ValueError."""
        payload = {
            "member": _hex_id(member),
            "from": _hex_index(from_idx),
            "to": _hex_index(to_idx),
            "delay_ms": int(delay_ms),
        }
        payload.update(_command_fields(fmt, switch))
        self._publish("cmd/scan", payload)

    def send_stop(self) -> None:
        self._publish("cmd/stop", {})

    # ------------------------------------------------------------------
    # Verfuegbarkeit
    # ------------------------------------------------------------------
    def _combined_availability(self) -> str | None:
        """offline gewinnt; online nur wenn keine Quelle offline meldet und mindestens eine online.

        Bei Debug-Firmware zaehlt nur stiebel-debug/availability (heatingpump/status wird ignoriert).
        """
        if self._fw == FW_DEBUG:
            states = [self._avail_by_source["debug"]]
        else:
            states = list(self._avail_by_source.values())
        states = [s for s in states if s is not None]
        if "offline" in states:
            return "offline"
        if "online" in states:
            return "online"
        return None

    def _set_firmware_kind(self, kind: str) -> None:
        """Firmware-Art setzen; Signal nur bei Aenderung. Wechsel zu debug/prod wertet Verfuegbarkeit neu aus."""
        if kind == self._fw:
            return
        self._fw = kind
        self.firmware_kind.emit(kind)
        if kind in (FW_DEBUG, FW_PROD):
            combined = self._combined_availability()
            if combined is not None:
                self.availability.emit(combined)

    def _handle_availability(self, source: str, text: str) -> None:
        value = text.strip()
        if value not in ("online", "offline"):
            return
        self._avail_by_source[source] = value  # auch merken, falls die Firmware spaeter wechselt
        if source == "produktiv" and self._fw == FW_DEBUG:
            return  # Debug-Firmware: heatingpump/status wird ignoriert
        self.availability_source.emit(source)
        combined = self._combined_availability()
        if combined is not None:
            self.availability.emit(combined)

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
            try:
                client.subscribe(PROD_AVAILABILITY_TOPIC, qos=0)
            except Exception:
                pass
        self.connected.emit(rc == 0)

    def _on_disconnect(self, client, userdata, rc):
        self._set_firmware_kind(FW_UNKNOWN)
        self.connected.emit(False)

    def _on_message(self, client, userdata, msg):
        """Nachricht verarbeiten. Fehlerhafte Nachrichten werden ignoriert.

        Ohne Netzwerk testbar: Tests rufen diese Methode mit einem Fake-Objekt
        (Attribute topic, payload) direkt auf.
        """
        try:
            topic = msg.topic
            if topic == PROD_AVAILABILITY_TOPIC:
                self._handle_availability("produktiv", msg.payload.decode("utf-8"))
                return
            if not topic.startswith(TOPIC_PREFIX):
                return
            suffix = topic[len(TOPIC_PREFIX):]
            if suffix not in RX_TOPICS:
                return
            text = msg.payload.decode("utf-8")
            if suffix == "availability":
                self._handle_availability("debug", text)
                return

            data = json.loads(text)

            if suffix == "rx":
                if not isinstance(data, list):
                    return
                arrival = self._now()
                pending = []  # (Item, Frame) ohne Zeitfelder
                for item in data:
                    try:
                        pending.append((item, {
                            "t": int(item["t"]),
                            "id": _to_int(item["id"]),
                            "data": bytes.fromhex(item["d"]),
                            "tx": bool(item["tx"]),
                        }))
                    except Exception:
                        continue
                # Fallback 'pc': nur Frames ohne ESP-Zeit speisen die Schaetzung.
                pc_t = [frame["t"] for item, frame in pending if item.get("u") is None]
                if pc_t:
                    self._clock.update(arrival, pc_t)
                frames = []
                for item, frame in pending:
                    try:
                        if item.get("u") is not None:
                            frame["ts"] = round(float(item["u"]) / 1000, 3)
                            frame["ts_src"] = "esp"
                        else:
                            frame["ts"] = round(self._clock.estimate(frame["t"]), 3)
                            frame["ts_src"] = "pc"
                    except Exception:
                        continue
                    frames.append(frame)
                if frames:
                    self.rx_frames.emit(frames)

            elif suffix == "read/result":
                if not isinstance(data, dict):
                    return
                out = dict(data)
                out["member"] = _to_int(data["member"])
                out["index"] = _to_int(data["index"])
                out["raw"] = _to_int(data["raw"])
                out["switch"] = _parse_switch(data)
                out["src"] = _to_int(data.get("src", "0x680"))  # fehlt = 0x680
                self.read_result.emit(out)

            elif suffix == "write/result":
                if not isinstance(data, dict):
                    return
                self.write_result.emit({
                    "member": _opt_int(data.get("member")),
                    "index": _opt_int(data.get("index")),
                    "value": _opt_int(data.get("value")),
                    "status": data.get("status"),
                    "ack": _opt_int(data.get("ack")),
                    "readback": _opt_int(data.get("readback")),
                })

            elif suffix == "scan/progress":
                if not isinstance(data, dict):
                    return
                out = dict(data)
                out["index"] = _to_int(data["index"])
                out["switch"] = _parse_switch(data)
                self.scan_progress.emit(out)

            elif suffix == "scan/result":
                if not isinstance(data, dict):
                    return
                out = dict(data)
                out["member"] = _to_int(data["member"])
                top_switch = _parse_switch(data)
                out["switch"] = top_switch
                items = []
                for item in data.get("items", []):
                    try:
                        entry = dict(item)
                        entry["index"] = _to_int(item["index"])
                        entry["raw"] = _to_int(item["raw"])
                        entry["switch"] = _parse_switch(item, default=top_switch)
                        items.append(entry)
                    except Exception:
                        continue
                out["items"] = items
                self.scan_result.emit(out)

            elif suffix == "status":
                if not isinstance(data, dict):
                    return
                self._set_firmware_kind(FW_DEBUG if data.get("fw") == FW_DEBUG else FW_PROD)
                self.status.emit(data)
        except Exception:
            # Fehlerhafte Nachricht (kein UTF-8, kein JSON, falsche Felder): ignorieren.
            return
