"""Tests fuer gui/display_model.py (ohne Qt). Das Test-Layout ist ein Dict im Test, damit
die Tests nicht von referenz/lwz180_display_layout.json (WP A) abhaengen. Nur der
load_layout-Test nutzt die echte Datei und wird uebersprungen, wenn sie fehlt."""
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

GUI_DIR = Path(__file__).resolve().parents[1] / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

from display_model import LAYOUT_PATH, decode_display, load_layout  # noqa: E402

# Pakete aus Salae-logic-data/digital.csv (Messung 2026-10-25, 28 Byte, ohne Adressbyte)
STANDBY = bytes.fromhex("f7 20 0b 20 6d 00 00 00 06 00 00 00 00 15 00 00 00 00 00 10 00 00 00 00 00 00 00 89")
P1_260 = bytes.fromhex("f7 20 0b 20 6d 00 73 00 06 00 5b 7d 3f 15 55 00 00 00 00 00 00 00 05 00 00 00 00 6a")
P81_1628 = bytes.fromhex("f7 20 0b 20 6d 00 73 7f 06 06 7d 5b 7f 29 55 01 00 00 00 00 00 00 10 00 00 00 00 bb")
P3_BYPASS = bytes.fromhex("f7 20 0b 20 6d 00 73 00 4f 00 00 00 4f 15 55 00 00 00 00 00 00 00 00 00 04 00 00 aa")
P4_FILTER = bytes.fromhex("f7 20 0b 20 6d 00 73 00 66 00 00 00 3f 15 55 00 00 00 00 00 00 00 00 01 00 00 00 ff")

TEST_LAYOUT = {
    "source": "test", "photo": None, "photo_size": [1, 1], "note": "Test-Layout",
    "elements": [
        {"id": "d", "name": "Zeile 1 Stelle 1", "kind": "digit7", "byte": 6, "row": 1, "pos": 1, "confirmed": True},
        {"id": "e", "name": "Zeile 1 Stelle 2", "kind": "digit7", "byte": 7, "row": 1, "pos": 2, "confirmed": True},
        {"id": "f", "name": "Zeile 1 Stelle 3 (Lueftungsstufe)", "kind": "digit7", "byte": 8, "row": 1, "pos": 3,
         "role": "level", "confirmed": True},
        {"id": "p", "name": "Zeile 2 Stelle 1", "kind": "digit7", "byte": 9, "row": 2, "pos": 1, "confirmed": True},
        {"id": "q", "name": "Zeile 2 Stelle 2", "kind": "digit7", "byte": 10, "row": 2, "pos": 2, "confirmed": True},
        {"id": "s", "name": "Zeile 2 Stelle 3", "kind": "digit7", "byte": 11, "row": 2, "pos": 3, "confirmed": True},
        {"id": "t", "name": "Zeile 2 Stelle 4", "kind": "digit7", "byte": 12, "row": 2, "pos": 4, "confirmed": True},
        {"id": "b", "name": "Uhr", "kind": "icon", "byte": 15, "bit": 0, "confirmed": True},
        {"id": "v", "name": "Luefter", "kind": "icon", "byte": 19, "bit": 4, "confirmed": True},
        {"id": "degc", "name": "Grad C", "kind": "icon", "byte": 22, "bit": 0, "confirmed": True},
        {"id": "pv2", "name": "LED Power Vent (Sketch)", "kind": "led", "byte": 26, "bit": 0, "confirmed": False},
        {"id": "btn_ok", "name": "OK", "kind": "button", "code": 6, "codes": [6, 10]},
    ],
}


def _seg(changes: dict | None = None) -> bytes:
    """28-Byte-Paket mit Header f7 20, CRC-Platzhalter 0x00 und optionalen Byte-Aenderungen."""
    buf = bytearray(28)
    buf[0], buf[1] = 0xF7, 0x20
    for idx, value in (changes or {}).items():
        buf[idx] = value
    return bytes(buf)


def test_standby_rows_and_level_digit():
    res = decode_display(STANDBY, TEST_LAYOUT)
    assert res["ok"] is True and res["error"] is None
    assert res["rows"][1] == "  1"
    assert res["rows"][2] == "    "
    f = res["digits"]["f"]
    assert f["byte"] == 8 and f["raw"] == 0x06 and f["char"] == "1" and f["segments"] == "bc"
    assert f["confirmed"] is True


def test_unknown_single_segment_char():
    res = decode_display(_seg({6: 0x20}), TEST_LAYOUT)
    d = res["digits"]["d"]
    assert d["raw"] == 0x20 and d["char"] == "?" and d["segments"] == "f"
    assert res["rows"][1] == "?  "


def test_standby_icons_unmapped_and_known_unclear():
    res = decode_display(STANDBY, TEST_LAYOUT)
    assert res["icons"]["v"] is True
    assert res["icons"]["b"] is False and res["icons"]["degc"] is False and res["icons"]["pv2"] is False
    assert res["unmapped_bits"] == []
    assert res["known_unclear_bits"] == [(2, 0), (2, 1), (2, 3), (3, 5), (4, 0), (4, 2), (4, 3), (4, 5), (4, 6),
                                         (13, 0), (13, 2), (13, 4)]


def test_unknown_bit_is_unmapped_not_known_unclear():
    res = decode_display(_seg({16: 0x02, 13: 0x01, 14: 0x10}), TEST_LAYOUT)
    assert res["unmapped_bits"] == [(16, 1)]
    assert (13, 0) in res["known_unclear_bits"] and (14, 4) in res["known_unclear_bits"]


def test_standby_level_is_1():
    assert decode_display(STANDBY, TEST_LAYOUT)["level"] == 1


def test_level_zero_without_condition():
    res = decode_display(_seg({8: 0x5B}), TEST_LAYOUT)
    assert res["level"] == 0


@pytest.mark.parametrize("changes, expected", [
    ({8: 0x06, 20: 0x04}, 1),           # Bedingung seg[20] & 0x04
    ({8: 0x4F, 19: 0x10}, 3),           # Bedingung seg[19] & 0x10
    ({8: 0x3F, 19: 0x10}, 0),           # Code 0x3f = 0
    ({8: 0x7F, 19: 0x10}, 0),           # unbekannter Stufencode
])
def test_level_values(changes, expected):
    assert decode_display(_seg(changes), TEST_LAYOUT)["level"] == expected


@pytest.mark.parametrize("bad", [None, b"", bytes(27), bytes(29), [0] * 28, "abc"])
def test_invalid_input_is_not_ok_and_no_exception(bad):
    res = decode_display(bad, TEST_LAYOUT)
    assert res["ok"] is False
    assert isinstance(res["error"], str) and res["error"]
    assert res["digits"] == {} and res["rows"] == {} and res["icons"] == {}
    assert res["level"] == 0 and res["unmapped_bits"] == [] and res["known_unclear_bits"] == []


def test_empty_layout_gives_no_elements_but_no_exception():
    res = decode_display(STANDBY, {})
    assert res["ok"] is True
    assert res["digits"] == {} and res["icons"] == {}
    assert res["rows"] == {1: "", 2: ""}
    # ohne Layout ist jedes gesetzte Bit ausser den bekannt unklaren unzugeordnet
    assert (19, 4) in res["unmapped_bits"] and (8, 1) in res["unmapped_bits"]
    assert (2, 0) in res["known_unclear_bits"] and (13, 0) in res["known_unclear_bits"]
    assert all(2 <= byte <= 26 for byte, _ in res["unmapped_bits"] + res["known_unclear_bits"])


@pytest.mark.parametrize("code, char, segments", [
    (0x3F, "0", "abcdef"),
    (0x06, "1", "bc"),
    (0x5B, "2", "abdeg"),
    (0x4F, "3", "abcdg"),
    (0x66, "4", "bcfg"),
    (0x6D, "5", "acdfg"),
    (0x7D, "6", "acdefg"),
    (0x07, "7", "abc"),
    (0x7F, "8", "abcdefg"),
    (0x6F, "9", "abcdfg"),
    (0x40, "-", "g"),
])
def test_all_digits_and_minus(code, char, segments):
    res = decode_display(_seg({7: code}), TEST_LAYOUT)
    assert res["digits"]["e"]["char"] == char
    assert res["digits"]["e"]["segments"] == segments
    assert res["rows"][1] == f" {char} "  # Stellen 1 und 3 sind leer (0x00)


@pytest.mark.parametrize("code, char", [
    (0x54, "n"), (0x38, "L"), (0x79, "E"), (0x71, "F"), (0x76, "H"), (0x3E, "U"),
    (0x74, "h"), (0x58, "c"), (0x1E, "J"), (0x5E, "d"), (0x77, "A"), (0x1C, "u"),
])
def test_additional_chars(code, char):
    assert decode_display(_seg({7: code}), TEST_LAYOUT)["digits"]["e"]["char"] == char


def _real(seg):
    if not Path(LAYOUT_PATH).exists():
        pytest.skip("Layout fehlt")
    res = decode_display(seg, load_layout())
    on = {k for k, v in res["icons"].items() if v}
    return res, on


def test_real_layout_p1_26_grad():
    res, on = _real(P1_260)
    assert res["rows"] == {1: "P 1", 2: " 260"}
    assert on == {"degc", "dp2"}
    assert res["unmapped_bits"] == []


def test_real_layout_p81_uhr_doppelpunkt():
    res, on = _real(P81_1628)
    assert res["rows"] == {1: "P81", 2: "1628"}
    assert on == {"b", "colon"}
    assert res["unmapped_bits"] == []
    assert (13, 3) in res["known_unclear_bits"]


def test_real_layout_p3_bypass():
    res, on = _real(P3_BYPASS)
    assert res["rows"] == {1: "P 3", 2: "   3"}
    assert on == {"z"}


def test_real_layout_p4_filter():
    res, on = _real(P4_FILTER)
    assert res["rows"] == {1: "P 4", 2: "   0"}
    assert on == {"ab"}


def test_real_layout_standby_level_1_with_fan():
    res, on = _real(STANDBY)
    assert res["rows"] == {1: "  1", 2: "    "}
    assert res["level"] == 1
    assert on == {"v"}
    assert res["unmapped_bits"] == []


def test_load_layout_real_file():
    if not Path(LAYOUT_PATH).exists():
        pytest.skip(f"Layout fehlt noch (WP A): {LAYOUT_PATH}")
    layout = load_layout()
    assert isinstance(layout.get("elements"), list)
    assert layout["elements"], "Layout ohne Elemente"
