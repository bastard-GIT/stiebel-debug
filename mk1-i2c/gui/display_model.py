"""Dekodierung des Display-Pakets f7 20 der LWZ 180 (kein Qt).

Eingabe ist das 28-Byte-Segmentpaket ohne Adressbyte (Index 0 = f7, 1 = 20, 27 = CRC).
Die Zuordnung Byte/Bit zu den Anzeigeelementen kommt aus
referenz/lwz180_display_layout.json (INTERFACES.md 6.1, 6.2). Nach aussen wird nie eine
Exception geworfen, auch nicht bei fehlerhaftem Paket oder Layout.
"""
from __future__ import annotations

import json
from pathlib import Path

DISPLAY_LEN = 28
LAYOUT_PATH = Path(__file__).resolve().parents[1] / "referenz" / "lwz180_display_layout.json"

SEGMENT_LETTERS = "abcdefg"   # Bit 0 = a ... Bit 6 = g (Standard-7-Segment)
DEFAULT_LEVEL_BYTE = 8        # grosse Ziffer Stelle 3 (Lueftungsstufe), falls kein role "level"
UNMAPPED_FIRST = 2            # Bytes 0, 1 (Header) und 27 (CRC) zaehlen nicht
UNMAPPED_LAST = 26

# Zeichentabelle Ziffern (referenz/lwz180_protocol.md 6.3)
CHAR_TABLE: dict[int, str] = {
    0x00: " ", 0x06: "1", 0x07: "7", 0x1C: "u", 0x30: "I", 0x39: "C",
    0x3F: "0", 0x40: "-", 0x4F: "3", 0x50: "r", 0x5B: "2", 0x5C: "o",
    0x5E: "d", 0x66: "4", 0x6D: "5", 0x6F: "9", 0x73: "P", 0x77: "A",
    0x78: "t", 0x7D: "6", 0x7F: "8",
    # Messung 2026-10-25 (Menue P1..P85, I1..I79; Anzeigen Cod, Pro, Auto, 0FF, ----, 0n)
    0x54: "n", 0x38: "L", 0x79: "E", 0x71: "F", 0x76: "H", 0x3E: "U",
    0x74: "h", 0x58: "c", 0x1E: "J",
}

# Bekannt unklar: gesetzte Bits, die nicht als Fehler gelten (Messung 2026-10-25).
# Bytes 2..5 sind in allen Zustaenden konstant (0b 20 6d 00), keine Ziffern.
# 13/x und 14/x: fast immer an (P81 beim Editieren 13/3, 13/5), im Foto nichts sichtbar,
# vermutlich nicht LCD (Tasten-LED/Blinksteuerung?).
KNOWN_UNCLEAR_BYTES = frozenset({2, 3, 4, 5})
KNOWN_UNCLEAR_BITS = frozenset({(13, b) for b in (0, 2, 3, 4, 5)} | {(14, b) for b in (0, 2, 4, 6)})

# Stufencodes nach Sketch-Regel (lwz180_protocol.md 6.2)
LEVEL_CODES: dict[int, int] = {0x06: 1, 0x5B: 2, 0x4F: 3, 0x3F: 0}


def load_layout(path: Path | str = LAYOUT_PATH) -> dict:
    """Layout-JSON laden. Wirft FileNotFoundError, wenn die Datei fehlt."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _empty(error: str) -> dict:
    return {"ok": False, "error": error, "digits": {}, "rows": {}, "icons": {},
            "level": 0, "unmapped_bits": [], "known_unclear_bits": []}


def _digit(raw: int) -> tuple[str, str]:
    """Zeichen und Zeichenkette der gesetzten Segmente a..g fuer einen Ziffern-Code."""
    char = CHAR_TABLE.get(raw, "?")
    segments = "".join(letter for i, letter in enumerate(SEGMENT_LETTERS) if raw & (1 << i))
    return char, segments


def _elements(layout: object) -> list[dict]:
    if not isinstance(layout, dict):
        return []
    elements = layout.get("elements")
    if not isinstance(elements, list):
        return []
    return [el for el in elements if isinstance(el, dict)]


def decode_display(seg: bytes, layout: dict) -> dict:
    """Display-Paket dekodieren. Rueckgabe-Schluessel siehe INTERFACES.md 6.2.

    Bei seg None oder Laenge != 28: ok False, error gesetzt, leere Strukturen (rows = {}).
    Sonst liefert rows immer die Schluessel 1 und 2.
    """
    if not isinstance(seg, (bytes, bytearray)) or len(seg) != DISPLAY_LEN:
        return _empty(f"kein gueltiges Paket (erwartet bytes der Laenge {DISPLAY_LEN})")

    digits: dict[str, dict] = {}
    row_cells: dict[int, list[tuple[int, str]]] = {1: [], 2: []}
    icons: dict[str, bool] = {}
    digit_bytes: set[int] = set()
    icon_bits: set[tuple[int, int]] = set()
    level_byte = DEFAULT_LEVEL_BYTE

    for el in _elements(layout):
        eid = el.get("id")
        kind = el.get("kind")
        byte = el.get("byte")
        if not isinstance(eid, str) or not _is_int(byte) or not 0 <= byte < DISPLAY_LEN:
            continue
        if kind == "digit7":
            raw = seg[byte]
            char, segments = _digit(raw)
            digits[eid] = {"byte": byte, "raw": raw, "char": char, "segments": segments,
                           "confirmed": bool(el.get("confirmed", False))}
            digit_bytes.add(byte)
            row, pos = el.get("row"), el.get("pos")
            if row in (1, 2) and _is_int(pos):
                row_cells[row].append((pos, char))
            if el.get("role") == "level":
                level_byte = byte
        elif kind in ("icon", "led"):
            bit = el.get("bit")
            if not _is_int(bit) or not 0 <= bit <= 7:
                continue
            icons[eid] = bool(seg[byte] & (1 << bit))
            icon_bits.add((byte, bit))

    rows = {row: "".join(ch for _, ch in sorted(cells, key=lambda c: c[0]))
            for row, cells in row_cells.items()}

    unmapped: list[tuple[int, int]] = []
    unclear: list[tuple[int, int]] = []
    for byte in range(UNMAPPED_FIRST, UNMAPPED_LAST + 1):
        if byte in digit_bytes:
            continue
        for bit in range(8):
            if seg[byte] & (1 << bit) and (byte, bit) not in icon_bits:
                if byte in KNOWN_UNCLEAR_BYTES or (byte, bit) in KNOWN_UNCLEAR_BITS:
                    unclear.append((byte, bit))
                else:
                    unmapped.append((byte, bit))

    # Stufe: nur auswertbar wenn seg[20] & 0x04 oder seg[19] & 0x10 gesetzt ist
    level = 0
    if (seg[20] & 0x04) or (seg[19] & 0x10):
        level = LEVEL_CODES.get(seg[level_byte], 0)

    return {"ok": True, "error": None, "digits": digits, "rows": rows, "icons": icons,
            "level": level, "unmapped_bits": unmapped, "known_unclear_bits": unclear}
