"""Elster-Protokoll fuer mk1-debug (kein Qt, kein paho).

Quelle der Daten: Repo bastard-GIT/elster_reference (ElsterTable.json, siehe default_reference_path).
Dekodierregeln wie in KElsterTable.cpp (SetValueType, SetDoubleType).
"""
from __future__ import annotations

import json
import re
import os
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

NOT_SUPPORTED_RAW = 0x8000  # "kein Wert" -> "-255" (ausser et_double_val/et_triple_val)

SWITCH_LONG = (0xF7, 0xF8, 0xF9, 0xFA)   # data[2] bei Langlayout (INTERFACES 8.1)
REQUEST_SWITCHES = (0xFA, 0xF8, 0xF9)    # gueltige Switch-Werte im Request
SWITCH_LABELS = {0xF7: "f7", 0xF8: "min", 0xF9: "max"}

def default_reference_path() -> Path:
    """Pfad zur ElsterTable.json: Umgebungsvariable ELSTER_REFERENCE_JSON, sonst Repo elster_reference
    neben dem stiebel-debug-Repo (../../elster_reference), sonst alter Pfad (../referenzen/elster_reference)."""
    env = os.environ.get("ELSTER_REFERENCE_JSON")
    if env:
        return Path(env)
    root = Path(__file__).resolve().parents[2]
    for cand in (root.parent / "elster_reference" / "ElsterTable.json",
                 root.parent / "referenzen" / "elster_reference" / "ElsterTable.json"):
        if cand.is_file():
            return cand
    return root.parent / "elster_reference" / "ElsterTable.json"


DEFAULT_JSON = default_reference_path()

# Bitfelder: Index -> [{"bit": n, "name": "...", "confirmed": bool}]. Eingebaute Vorgabe (Nutzerwissen
# aus altem ESPHome-Code); ein Signal der Referenz-JSON mit Feld "bits" ueberschreibt sie.
# BETRIEBS_STATUS (am Display geprueft 2026-10-21): Bit 0 Verdichter, Bit 6 Speicherladepumpe, Bit 14 Warmwasser (Wasserhahn).
# Warmwasser 0x4041, Heizen 0x0041, Notlauf (Zusatzheizung, Verdichter aus) 0x1040. Das Heizung-Symbol hat hier kein eigenes Bit.
BUILTIN_BITS = {
    0x0176: [
        {"bit": 0, "name": "VERDICHTER", "confirmed": True},
        {"bit": 6, "name": "SPEICHERLADEPUMPE", "confirmed": True},
        {"bit": 12, "name": "ZUSATZHEIZUNG", "confirmed": True},
        {"bit": 14, "name": "WARMWASSER", "confirmed": True},
    ],
    # BETRIEBS_STATUS_WPLA (0x4eef, Owner 0x480). Quelle kr0ner/OneESP32ToRuleThemAll yaml/wpl_a_base.yaml,
    # gemessen 0x0051 Heizen 2026-10-20. Bits 0, 4, 6 bestaetigt, uebrige noch nicht am Display geprueft.
    0x4EEF: [
        {"bit": 0, "name": "HEIZKREISPUMPE_1", "confirmed": True},
        {"bit": 1, "name": "HEIZKREISPUMPE_2", "confirmed": False},
        {"bit": 2, "name": "AUFHEIZPROGRAMM", "confirmed": False},
        {"bit": 3, "name": "NHZ_STUFEN_IN_BETRIEB", "confirmed": False},
        {"bit": 4, "name": "WP_IM_HEIZBETRIEB", "confirmed": True},
        {"bit": 5, "name": "WP_IM_WW_BETRIEB", "confirmed": False},
        {"bit": 6, "name": "VERDICHTER_IN_BETRIEB", "confirmed": True},
        {"bit": 7, "name": "SOMMERBETRIEB_AKTIV", "confirmed": False},
        {"bit": 8, "name": "KUEHLBETRIEB_AKTIV", "confirmed": False},
        {"bit": 9, "name": "ABTAUBETRIEB", "confirmed": False},
        {"bit": 10, "name": "SILENT_MODE_1", "confirmed": False},
        {"bit": 11, "name": "SILENT_MODE_2", "confirmed": False},
    ],
}

MSG_TYPE_NAMES = {
    0: "write",
    1: "read",
    2: "response",
    3: "ack",
    4: "write ack",
    5: "write respond",
    6: "system",
    7: "system respond",
}


def _hex(value) -> int:
    """'0x0112' / '0x180' / int -> int."""
    if isinstance(value, int):
        return value
    return int(str(value), 16)


class ElsterDb:
    def __init__(self, json_path: str | None = None):
        path = Path(json_path) if json_path else DEFAULT_JSON
        with open(path, encoding="utf-8") as f:
            table = json.load(f)

        self.members: list[dict] = table["can_members"]
        self.signals: list[dict] = table["signals"]
        self.error_list: list[dict] = table.get("error_list", [])
        self.betriebsart_list: list[dict] = table.get("betriebsart_list", [])
        self.own_can_id: int = _hex(table["frame_format"]["bus"]["own_can_id"])

        self._member_by_id: dict[int, dict] = {}
        for m in self.members:
            self._member_by_id.setdefault(_hex(m["can_id"]), m)

        self._signal_by_index: dict[int, dict] = {}
        self._bits: dict[int, list[dict]] = {k: list(v) for k, v in BUILTIN_BITS.items()}
        for s in self.signals:
            if s.get("sentinel"):  # INDEX_NOT_FOUND ist kein echtes Signal
                continue
            self._signal_by_index.setdefault(_hex(s["index"]), s)
            if s.get("bits"):
                self._bits[_hex(s["index"])] = list(s["bits"])

    def member_by_id(self, can_id: int) -> dict | None:
        return self._member_by_id.get(can_id)

    def member_name(self, can_id: int) -> str:
        m = self.member_by_id(can_id)
        return m["name"] if m else f"UNBEKANNT_0x{can_id:03x}"

    def signal_by_index(self, index: int) -> dict | None:
        return self._signal_by_index.get(index)

    def bits_for(self, index: int) -> list[dict] | None:
        """Bit-Definitionen des Index (Referenz-Feld "bits" oder eingebaute Vorgabe), sonst None."""
        return self._bits.get(index)

    def decode(self, index: int, raw: int, type_name: str | None = None) -> str:
        """Rohwert dekodieren: Bitfeld-Text, wenn der Index Bits hat, sonst nach Typ."""
        bits = self.bits_for(index)
        if bits:
            return decode_bits(raw, bits)
        if type_name is None:
            sig = self.signal_by_index(index)
            type_name = sig.get("type", "et_default") if sig else "et_default"
        return decode_value(type_name, raw)

    def signal_name(self, index: int) -> str:
        s = self.signal_by_index(index)
        return s["name"] if s else f"UNK_0x{index:04x}"

    def search_signals(self, text: str) -> list[dict]:
        needle = text.strip().lower()
        result = []
        for s in self.signals:
            if s.get("sentinel"):
                continue
            if needle in s["name"].lower() or needle in s["index"].lower():
                result.append(s)
        return result


def parse_frame(can_id: int, data: bytes) -> dict | None:
    """7-Byte-Antwort zerlegen (Abschnitt 2 / ElsterTable.README 'dekodieren')."""
    if len(data) < 7:
        return None
    d = bytes(data)
    msg_type = d[0] & 0x0F
    receiver = ((d[0] & 0xF0) << 3) | d[1]
    system = msg_type in (6, 7)  # Systemnachricht: immer Kurzlayout
    switch = None
    if not system and d[2] in SWITCH_LONG:
        switch = d[2]
        index = (d[3] << 8) | d[4]
        value = (d[5] << 8) | d[6]
    else:
        index = d[2]
        value = (d[3] << 8) | d[4]
    return {
        "sender": can_id,
        "receiver": receiver,
        "msg_type": msg_type,
        "msg_type_name": MSG_TYPE_NAMES[msg_type],
        "index": index,
        "value": value,
        "switch": switch,
        "system": system,
    }


def switch_label(switch: int | None) -> str:
    """Anzeige-Kurzform des Switch-Bytes: None/0xFA -> "", 0xF8 -> "min", 0xF9 -> "max", 0xF7 -> "f7"."""
    if switch is None or switch == 0xFA:
        return ""
    return SWITCH_LABELS.get(switch, f"0x{switch:02x}")


@lru_cache(maxsize=1)
def _lookup_tables() -> tuple[tuple[dict, ...], tuple[dict, ...]]:
    """betriebsart_list und error_list aus der Standard-Tabelle (fuer decode_value)."""
    with open(DEFAULT_JSON, encoding="utf-8") as f:
        table = json.load(f)
    return tuple(table.get("betriebsart_list", [])), tuple(table.get("error_list", []))


def decode_value(type_name: str, raw: int) -> str:
    """Rohwert (uint16) nach Typregel (KElsterTable.cpp) als Text."""
    if type_name == "et_double_val":
        return f"{raw:.3f}"
    if type_name == "et_triple_val":
        return f"{raw:.6f}"

    if raw == NOT_SUPPORTED_RAW:
        return "-255"

    raw &= 0xFFFF
    lo = raw & 0xFF
    hi = raw >> 8
    s16 = raw - 0x10000 if raw & 0x8000 else raw

    if type_name == "et_dec_val":
        return f"{s16 / 10.0:.1f}"
    if type_name == "et_cent_val":
        return f"{s16 / 100.0:.2f}"
    if type_name == "et_mil_val":
        return f"{s16 / 1000.0:.3f}"
    if type_name == "et_byte":
        return str(lo - 256 if lo & 0x80 else lo)
    if type_name in ("et_bool", "et_little_bool"):
        # Beide Byte-Reihenfolgen gelten als on (wie upstream 2.3.0/2.4.0 und Produktiv-Firmware)
        return {0x0001: "on", 0x0100: "on", 0x0000: "off"}.get(raw, "?")
    if type_name == "et_little_endian":
        return str(hi + 256 * lo)
    if type_name == "et_betriebsart":
        betriebsart, _ = _lookup_tables()
        if lo == 0 and hi < len(betriebsart):
            return betriebsart[hi]["name"]
        return "?"
    if type_name == "et_zeit":
        return f"{lo:02d}:{hi:02d}"
    if type_name == "et_datum":
        return f"{hi:02d}.{lo:02d}."
    if type_name == "et_time_domain":
        if raw & 0x8080:
            return "not used time domain"
        return (f"{hi // 4:02d}:{15 * (hi % 4):02d}-"
                f"{lo // 4:02d}:{15 * (lo % 4):02d}")
    if type_name == "et_dev_nr":
        return "--" if raw >= 0x80 else str(raw + 1)
    if type_name == "et_dev_id":
        return f"{hi}-{lo:02d}"
    if type_name == "et_err_nr":
        _, errors = _lookup_tables()
        for e in errors:
            if _hex(e["index"]) == raw:
                return e["name"]
        return f"ERR {raw}"
    # et_default und unbekannte Typen
    return str(s16)


def set_bit_names(raw: int, bits: list[dict]) -> list[tuple[int, str]]:
    """Gesetzte Bits als [(bit, name)], aufsteigend. Bits ohne Definition heissen "b<n>"."""
    names = {int(b["bit"]): b["name"] for b in bits}
    raw &= 0xFFFF
    return [(n, names.get(n, f"b{n}")) for n in range(16) if raw & (1 << n)]


def decode_bits(raw: int, bits: list[dict]) -> str:
    """Bitfeld als Text: "0x0041: VERDICHTER, SPEICHERLADEPUMPE". 0x8000 = nicht unterstuetzt."""
    if raw == NOT_SUPPORTED_RAW:
        return "-255"
    on = ", ".join(name for _, name in set_bit_names(raw, bits))
    return f"0x{raw & 0xFFFF:04X}: {on}" if on else f"0x{raw & 0xFFFF:04X}: -"


OWN_CAN_ID = 0x680  # eigene ID (ESPCLIENT), als Lese-Ziel verboten


def build_read_request(member_can_id: int, index: int,
                       db: ElsterDb | None = None, fmt: str = "auto",
                       switch: int = 0xFA) -> tuple[int, bytes]:
    """Lese-Request nach INTERFACES.md 7.1/8.1: (CAN-ID 0x680, 7 Bytes).

    d0 = ((id >> 3) & 0xF0) | 0x01, d1 = id & 0x7F (generische Formel fuer jede ID).
    fmt: "auto" (kurz wenn index <= 0xFF, nicht in SWITCH_LONG, switch == 0xFA),
    "short" (nur index <= 0xFF; Indizes in SWITCH_LONG und switch != 0xFA werden lang
    gesendet), "long" (immer [d0, d1, switch, hi, lo, 0, 0]).
    switch: 0xFA (Standard), 0xF8 (Min), 0xF9 (Max); sonst ValueError.
    db ist nur aus Kompatibilitaetsgruenden da und wird ignoriert.
    """
    if not 0 <= member_can_id <= 0x7FF:
        raise ValueError(f"CAN-ID ausserhalb 11 Bit: 0x{member_can_id:x}")
    if member_can_id == OWN_CAN_ID:
        raise ValueError("Ziel 0x680 (eigene ID) ist nicht erlaubt")
    if not 0 <= index <= 0xFFFF:
        raise ValueError(f"Index ausserhalb 16 Bit: {index}")
    if fmt not in ("auto", "short", "long"):
        raise ValueError(f"unbekanntes Format: {fmt!r}")
    if fmt == "short" and index > 0xFF:
        raise ValueError(f"Kurzformat nur fuer Index <= 0xFF, war 0x{index:04x}")
    if switch not in REQUEST_SWITCHES:
        raise ValueError(f"ungueltiger Switch: {switch!r} (erlaubt: 0xFA, 0xF8, 0xF9)")

    d0 = ((member_can_id >> 3) & 0xF0) | 0x01
    d1 = member_can_id & 0x7F
    use_short = (fmt != "long" and index <= 0xFF and switch == 0xFA
                 and index not in SWITCH_LONG)
    if use_short:
        tail = [index, 0x00, 0x00, 0x00, 0x00]
    else:
        tail = [switch, index >> 8, index & 0xFF, 0x00, 0x00]
    return OWN_CAN_ID, bytes([d0, d1, *tail])


@lru_cache(maxsize=1)
def _type_table() -> dict[str, dict]:
    """types aus der Standard-Tabelle, Name -> Eintrag."""
    with open(DEFAULT_JSON, encoding="utf-8") as f:
        table = json.load(f)
    return {t["name"]: t for t in table.get("types", [])}


def is_writable(type_name: str) -> bool:
    """types[].writable der Standardreferenz; unbekannter Typ -> False."""
    t = _type_table().get(type_name)
    return bool(t and t.get("writable"))


def _parse_int(s: str) -> int:
    if not re.fullmatch(r"[+-]?\d+", s):
        raise ValueError(f"keine Ganzzahl: {s!r}")
    return int(s)


def _parse_scaled(s: str, factor: int) -> int:
    """Dezimalzahl (Komma oder Punkt) * factor, abgeschnitten (gegen Null), als uint16."""
    if not re.fullmatch(r"[+-]?(\d+([.,]\d*)?|[.,]\d+)", s):
        raise ValueError(f"keine Dezimalzahl: {s!r}")
    scaled = int(Decimal(s.replace(",", ".")) * factor)
    if not -32767 <= scaled <= 32767:
        raise ValueError(f"Wert ausserhalb +-32767 nach Skalierung: {s!r}")
    return scaled & 0xFFFF


def _parse_onoff(s: str, on_raw: int) -> int:
    low = s.lower()
    if low == "on":
        return on_raw
    if low == "off":
        return 0
    raise ValueError(f"erwartet 'on' oder 'off': {s!r}")


def _parse_hm(h: str, m: str) -> tuple[int, int]:
    hour, minute = int(h), int(m)
    if not (hour < 24 and minute < 60):
        raise ValueError(f"Uhrzeit ausserhalb 0-23:0-59: {h}:{m}")
    return hour, minute


def _encode_text(type_name: str, s: str) -> int:
    """Text eines beschreibbaren Typs nach uint16 (Umkehrung von decode_value)."""
    if type_name == "et_default":
        n = _parse_int(s)
        if not -32767 <= n <= 65535:
            raise ValueError(f"ausserhalb -32767..65535: {s!r}")
        return n & 0xFFFF
    if type_name == "et_dec_val":
        return _parse_scaled(s, 10)
    if type_name == "et_cent_val":
        return _parse_scaled(s, 100)
    if type_name == "et_mil_val":
        return _parse_scaled(s, 1000)
    if type_name == "et_byte":
        n = _parse_int(s)
        if not 0 <= n <= 255:
            raise ValueError(f"ausserhalb 0..255: {s!r}")
        return n
    if type_name == "et_bool":
        return _parse_onoff(s, 0x0001)
    if type_name == "et_little_bool":
        return _parse_onoff(s, 0x0100)
    if type_name == "et_little_endian":
        n = _parse_int(s)
        if not 0 <= n <= 0xFFFF:
            raise ValueError(f"ausserhalb 0..65535: {s!r}")
        return ((n & 0xFF) << 8) | (n >> 8)  # Bytes tauschen
    if type_name == "et_betriebsart":
        if not s:
            raise ValueError("leerer Betriebsart-Name")
        needle = s.lower()
        betriebsart, _ = _lookup_tables()
        for idx in range(len(betriebsart) - 1, -1, -1):  # von hinten nach vorn
            if betriebsart[idx]["name"].lower().startswith(needle):
                return idx << 8
        raise ValueError(f"unbekannte Betriebsart: {s!r}")
    if type_name == "et_zeit":
        m = re.fullmatch(r"(\d{1,2}):(\d{1,2})", s)
        if not m:
            raise ValueError(f"erwartet 'H:M': {s!r}")
        hour, minute = _parse_hm(m.group(1), m.group(2))
        return (minute << 8) + hour
    if type_name == "et_datum":
        m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.?", s)
        if not m:
            raise ValueError(f"erwartet 'D.M.': {s!r}")
        tag, monat = int(m.group(1)), int(m.group(2))
        if not (1 <= tag <= 31 and 1 <= monat <= 12):
            raise ValueError(f"Datum ausserhalb Tag 1-31 / Monat 1-12: {s!r}")
        return (tag << 8) + monat
    if type_name == "et_time_domain":
        if not s:
            return 0x8080  # "not used time domain"
        m = re.fullmatch(r"(\d{1,2}):(\d{1,2})-(\d{1,2}):(\d{1,2})", s)
        if not m:
            raise ValueError(f"erwartet 'H:M-H:M': {s!r}")
        h1, m1 = _parse_hm(m.group(1), m.group(2))
        h2, m2 = _parse_hm(m.group(3), m.group(4))
        if h1 * 60 + m1 >= h2 * 60 + m2:
            raise ValueError(f"Start muss vor Ende liegen: {s!r}")
        return ((4 * h1 + m1 // 15) << 8) + (4 * h2 + m2 // 15)
    raise ValueError(f"Typ nicht beschreibbar: {type_name}")


def encode_value(type_name: str, text: str) -> int:
    """Text nach Typregel (KElsterTable.cpp / types[].encode) als uint16.

    Text '0x....' ist bei jedem Typ der Rohwert (0..0xFFFF). Ergebnis 0x8000 ist ValueError.
    """
    if type_name not in _type_table():
        raise ValueError(f"unbekannter Typ: {type_name!r}")
    s = text.strip()
    if re.fullmatch(r"0[xX][0-9a-fA-F]+", s):
        raw = int(s, 16)
    else:
        if not is_writable(type_name):
            raise ValueError(f"Typ nicht beschreibbar: {type_name}")
        raw = _encode_text(type_name, s)
    if not 0 <= raw <= 0xFFFF:
        raise ValueError(f"Rohwert ausserhalb 16 Bit: {text!r}")
    if raw == NOT_SUPPORTED_RAW:
        raise ValueError("Rohwert 0x8000 ('kein Wert') ist nicht schreibbar")
    return raw


def build_write_request(member_can_id: int, index: int, value: int,
                        fmt: str = "auto") -> tuple[int, bytes]:
    """Schreib-Request: (CAN-ID 0x680, 7 Bytes). Gegenstueck zu build_read_request.

    d0 = ((id >> 3) & 0xF0) | 0x00, d1 = id & 0x7F.
    Kurz (fmt "auto"/"short", index <= 0xFF, nicht 0xF7..0xFA): [d0, d1, idx, vh, vl, 0, 0].
    Lang (fmt "long", index > 0xFF oder 0xF7..0xFA): [d0, d1, 0xFA, ih, il, vh, vl].
    """
    if not 0 <= member_can_id <= 0x7FF:
        raise ValueError(f"CAN-ID ausserhalb 11 Bit: 0x{member_can_id:x}")
    if member_can_id == OWN_CAN_ID:
        raise ValueError("Ziel 0x680 (eigene ID) ist nicht erlaubt")
    if not 0 <= index <= 0xFFFF:
        raise ValueError(f"Index ausserhalb 16 Bit: {index}")
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"Wert ausserhalb 16 Bit: {value}")
    if fmt not in ("auto", "short", "long"):
        raise ValueError(f"unbekanntes Format: {fmt!r}")
    if fmt == "short" and index > 0xFF:
        raise ValueError(f"Kurzformat nur fuer Index <= 0xFF, war 0x{index:04x}")

    d0 = ((member_can_id >> 3) & 0xF0) | 0x00
    d1 = member_can_id & 0x7F
    use_short = fmt != "long" and index <= 0xFF and index not in SWITCH_LONG
    if use_short:
        data = [d0, d1, index, value >> 8, value & 0xFF, 0x00, 0x00]
    else:
        data = [d0, d1, 0xFA, index >> 8, index & 0xFF, value >> 8, value & 0xFF]
    return OWN_CAN_ID, bytes(data)
