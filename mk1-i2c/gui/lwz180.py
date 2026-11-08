"""Dekoder fuer das LWZ-180-I2C-Protokoll (Bus Lueftungsgeraet zu Display), laut INTERFACES.md 4.4.

Parameter (CRC, Typtabelle, Tastencodes) stehen in referenz/lwz180_types.json (WP1, Quelle
kdarnok/lwz180, Status unverifiziert). Mitgeschnittene Frames beginnen mit dem Adressbyte 0x00
(General Call). Danach kommen Header, Payload und CRC-Byte. Die CRC deckt Header + Payload ab
(ohne Adressbyte), Startwert 0, Polynom 0x49, MSB first.

Frame-Layouts (Bytes inkl. Adressbyte):
  Istwert: 00 e3 20 10 <typ> <hi> <lo> <crc>     (8 Byte)
  Tasten:  00 e3 30 20 00 00 <code> <crc>        (8 Byte)
  Display: 00 f7 20 <25 Byte> <crc>              (29 Byte, 28 ohne Adressbyte)

Annahme Display: Das letzte Byte ist eine CRC ueber Byte 1..27 (ohne Adressbyte). Die Quelle
nennt dafuer keine Belegung. crc_ok ist deshalb nur informativ: Ein Display-Paket mit falscher
CRC bleibt ok=True und liefert segments.
"""
from __future__ import annotations

import json
from pathlib import Path

TYPES_PATH = Path(__file__).resolve().parents[1] / "referenz" / "lwz180_types.json"

ADDR_GENERAL_CALL = 0x00
HDR_ISTWERT = bytes([0xE3, 0x20, 0x10])
HDR_TASTEN = bytes([0xE3, 0x30, 0x20])
HDR_DISPLAY = bytes([0xF7, 0x20])
LEN_ISTWERT = 8
LEN_TASTEN = 8
LEN_DISPLAY = 29

KIND_ISTWERT = "istwert"
KIND_TASTEN = "tasten"
KIND_DISPLAY = "display"
KIND_UNKNOWN = "unknown"


def _to_int(value) -> int:
    """Zahl oder String wie '0x49' zu int."""
    return int(value, 0) if isinstance(value, str) else int(value)


def _load_spec() -> dict:
    with open(TYPES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


_SPEC = _load_spec()

CRC_PARAMS: dict = {
    "poly": _to_int(_SPEC["crc"]["poly"]),
    "init": _to_int(_SPEC["crc"]["init"]),
    "refin": bool(_SPEC["crc"]["refin"]),
    "refout": bool(_SPEC["crc"]["refout"]),
    "xorout": _to_int(_SPEC["crc"]["xorout"]),
    "covers": _SPEC["crc"]["covers"],
    "verified": bool(_SPEC["crc"]["verified"]),
    "note": _SPEC["crc"]["note"],
}

# Typ-Byte (int) -> {"name","unit","scale","signed","confirmed"}
TYPES: dict[int, dict] = {_to_int(k): v for k, v in _SPEC["types"].items()}
# Tastencode (int) -> Tastenname laut JSON
KEY_NAMES: dict[int, str] = {_to_int(k): v for k, v in _SPEC["keys"].items()}


def _reflect8(value: int) -> int:
    """Bits eines Bytes umkehren (fuer refin/refout)."""
    out = 0
    for i in range(8):
        if value & (1 << i):
            out |= 0x80 >> i
    return out


def crc8(data: bytes, poly: int = CRC_PARAMS["poly"], init: int = CRC_PARAMS["init"],
         refin: bool = False, refout: bool = False, xorout: int = 0) -> int:
    """CRC-8 mit den Parametern aus lwz180_types.json (Standard: poly 0x49, init 0, MSB first)."""
    crc = init & 0xFF
    for byte in bytes(data):
        if refin:
            byte = _reflect8(byte)
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ poly) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    if refout:
        crc = _reflect8(crc)
    return (crc ^ xorout) & 0xFF


def build_packet(header: bytes, payload: bytes, addr: int = ADDR_GENERAL_CALL) -> bytes:
    """Paket aus Adressbyte, Header, Payload und CRC-Byte (CRC ueber Header + Payload)."""
    body = bytes(header) + bytes(payload)
    return bytes([addr & 0xFF]) + body + bytes([crc8(body)])


def build_istwert(type_id: int, raw: int) -> bytes:
    """Istwert-Paket (8 Byte mit Adressbyte). raw wird als int16 Big-Endian kodiert."""
    payload = bytes([type_id & 0xFF]) + int(raw).to_bytes(2, "big", signed=True)
    return build_packet(HDR_ISTWERT, payload)


def build_taste(code: int) -> bytes:
    """Tasten-Paket (8 Byte mit Adressbyte) fuer einen Tastencode."""
    return build_packet(HDR_TASTEN, bytes([0x00, 0x00, code & 0xFF]))


def build_display(payload25: bytes) -> bytes:
    """Display-Paket (29 Byte mit Adressbyte): Header f7 20, 25 Payload-Bytes, CRC."""
    if len(payload25) != LEN_DISPLAY - 4:
        raise ValueError("Display-Payload muss 25 Byte lang sein")
    return build_packet(HDR_DISPLAY, payload25)


def _hex(header: bytes) -> str:
    return header.hex(" ")


def _result(**overrides) -> dict:
    """Ergebnis-Dict mit allen Schluesseln (INTERFACES.md 4.4), Standard None bzw. False."""
    res = {
        "ok": False, "addr": None, "header": None, "kind": KIND_UNKNOWN,
        "payload": b"", "crc_rx": None, "crc_calc": None, "crc_ok": None,
        "type": None, "name": None, "raw": None, "value": None, "unit": None,
        "keys": None, "segments": None, "error": None,
    }
    res.update(overrides)
    return res


def _crc_into(data: bytes, res: dict) -> None:
    """CRC-Felder setzen: crc_rx = letztes Byte, crc_calc = CRC ueber Byte 1..-2."""
    res["crc_rx"] = data[-1]
    res["crc_calc"] = crc8(data[1:-1])
    res["crc_ok"] = res["crc_rx"] == res["crc_calc"]


def _decode_istwert(data: bytes, res: dict) -> dict:
    res["kind"] = KIND_ISTWERT
    res["header"] = _hex(HDR_ISTWERT)
    if len(data) != LEN_ISTWERT:
        res["error"] = f"Laenge {len(data)} (erwartet {LEN_ISTWERT})"
        return res
    res["payload"] = data[4:7]
    _crc_into(data, res)
    if not res["crc_ok"]:
        res["error"] = "CRC-Fehler"
        return res
    type_id = data[4]
    raw = int.from_bytes(data[5:7], "big", signed=True)
    info = TYPES.get(type_id)
    if info is None:
        res["name"] = f"UNK_0x{type_id:02x}"
        res["value"] = float(raw)
        res["unit"] = None
    else:
        if not info.get("signed", True):
            raw = int.from_bytes(data[5:7], "big", signed=False)
        res["name"] = info["name"]
        res["unit"] = info["unit"]
        res["value"] = round(raw * float(info["scale"]), 4)
    res["type"] = type_id
    res["raw"] = raw
    res["ok"] = True
    return res


def _decode_tasten(data: bytes, res: dict) -> dict:
    res["kind"] = KIND_TASTEN
    res["header"] = _hex(HDR_TASTEN)
    if len(data) != LEN_TASTEN:
        res["error"] = f"Laenge {len(data)} (erwartet {LEN_TASTEN})"
        return res
    res["payload"] = data[4:7]
    _crc_into(data, res)
    if not res["crc_ok"]:
        res["error"] = "CRC-Fehler"
        return res
    res["keys"] = [data[6]]
    res["ok"] = True
    return res


def _decode_display(data: bytes, res: dict) -> dict:
    res["kind"] = KIND_DISPLAY
    res["header"] = _hex(HDR_DISPLAY)
    if len(data) != LEN_DISPLAY:
        res["error"] = f"Laenge {len(data)} (erwartet {LEN_DISPLAY})"
        return res
    res["payload"] = data[3:-1]
    _crc_into(data, res)  # nur informativ, siehe Modul-Docstring
    res["segments"] = bytes(data[1:])
    res["ok"] = True
    return res


def decode(frame_bytes: bytes) -> dict:
    """Mitgelesenen Frame (inkl. Adressbyte) dekodieren. Wirft nie eine Exception.

    Rueckgabe-Dict mit allen Schluesseln aus INTERFACES.md 4.4. ok ist True nur bei bekanntem
    Header, passender Laenge und gueltiger CRC (Display: CRC nur informativ).
    """
    try:
        data = bytes(frame_bytes)
    except (TypeError, ValueError):
        return _result(error="kein Byte-Objekt")
    if not data:
        return _result(error="leerer Frame")
    addr = data[0]
    res = _result(addr=addr)
    if addr != ADDR_GENERAL_CALL:
        res["error"] = f"Adresse 0x{addr:02x} (erwartet 0x00)"
        return res
    if len(data) < 4:
        res["error"] = "zu kurz"
        return res
    head = data[1:4]
    if head == HDR_ISTWERT:
        return _decode_istwert(data, res)
    if head == HDR_TASTEN:
        return _decode_tasten(data, res)
    if data[1:3] == HDR_DISPLAY:
        return _decode_display(data, res)
    res["error"] = f"unbekannter Header {_hex(data[1:4])}"
    return res
