import os
import random
import sys

GUI_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "gui"))
if GUI_DIR not in sys.path:
    sys.path.insert(0, GUI_DIR)

import lwz180  # noqa: E402
from lwz180 import (  # noqa: E402
    build_display,
    build_istwert,
    build_packet,
    build_taste,
    crc8,
    decode,
)


def test_crc_parameters_from_json():
    assert lwz180.CRC_PARAMS["poly"] == 0x49
    assert lwz180.CRC_PARAMS["init"] == 0x00
    assert lwz180.CRC_PARAMS["refin"] is False
    assert lwz180.CRC_PARAMS["refout"] is False
    assert lwz180.CRC_PARAMS["xorout"] == 0
    assert lwz180.CRC_PARAMS["verified"] is True  # Saleae-Mitschnitt 2026-10-25


def test_crc_control_values():
    assert crc8([5]) == 0x24
    assert crc8([0x06]) == 0xFF
    assert crc8([0x0C]) == 0xB7
    assert crc8([0x0D]) == 0xFE
    assert crc8([0xE3, 0x30, 0x20]) == 0


def test_crc_zero_bytes_do_not_change_init0():
    # Fuehrendes 0x00 bzw. Nullbytes aendern die CRC bei init 0 nicht
    assert crc8([0x00, 0xE3, 0x30, 0x20, 0x00, 0x00, 0x05]) == crc8([0x05])


def test_tasten_packet_matches_json_example():
    # Beispiel aus referenz/lwz180_types.json (Taste 5 = menu), ohne Adressbyte
    assert build_packet(bytes([0xE3, 0x30, 0x20]), bytes([0, 0, 5]))[1:] == bytes.fromhex("e3302000000524")


def test_istwert_roundtrip_negative():
    frame = build_istwert(0x06, -25)  # -2.5 Grad Aussentemperatur
    assert frame.hex() == "00e3201006ffe74f"
    res = decode(frame)
    assert res["ok"] is True
    assert res["kind"] == "istwert"
    assert res["addr"] == 0x00
    assert res["header"] == "e3 20 10"
    assert res["type"] == 0x06
    assert res["raw"] == -25
    assert res["value"] == -2.5
    assert res["unit"] == "°C"
    assert res["crc_ok"] is True
    assert res["crc_rx"] == res["crc_calc"]
    assert res["error"] is None


def test_istwert_positive_scaled_and_unscaled():
    res = decode(build_istwert(0x10, 120))  # Zuluftvolumenstrom, Skalierung 1
    assert res["ok"] is True
    assert res["value"] == 120.0
    assert res["unit"] == "m3/h"
    res = decode(build_istwert(0x14, 350))  # Skalierung 0.1
    assert res["value"] == 35.0


def test_unknown_type_is_unk_without_scale():
    res = decode(build_istwert(0x7e, 258))
    assert res["ok"] is True
    assert res["name"] == "UNK_0x7e"
    assert res["value"] == 258.0
    assert res["unit"] is None
    assert res["type"] == 0x7e


def test_bad_crc_istwert():
    frame = bytearray(build_istwert(0x07, 215))
    frame[-1] ^= 0x01
    res = decode(bytes(frame))
    assert res["ok"] is False
    assert res["crc_ok"] is False
    assert res["error"]
    assert res["value"] is None
    assert res["type"] is None


def test_tasten_roundtrip():
    res = decode(build_taste(5))
    assert res["ok"] is True
    assert res["kind"] == "tasten"
    assert res["keys"] == [5]
    assert res["crc_ok"] is True


def test_tasten_bad_crc():
    frame = bytearray(build_taste(6))
    frame[-1] ^= 0xFF
    res = decode(bytes(frame))
    assert res["ok"] is False
    assert res["keys"] is None


def _display_frame():
    seg = bytearray(28)
    seg[0:2] = bytes([0xF7, 0x20])
    seg[3:10] = bytes([0x06, 0x5B, 0x4F, 0x3F, 0x6D, 0x66, 0x5B])
    seg[27] = crc8(bytes(seg[0:27]))
    return build_display(bytes(seg[2:27])), bytes(seg)


def test_display_roundtrip():
    frame, seg = _display_frame()
    assert len(frame) == 29
    res = decode(frame)
    assert res["ok"] is True
    assert res["kind"] == "display"
    assert res["header"] == "f7 20"
    assert res["segments"] == seg
    assert len(res["segments"]) == 28
    assert res["crc_ok"] is True


def test_display_crc_only_informative():
    frame, seg = _display_frame()
    bad = bytearray(frame)
    bad[-1] ^= 0x01
    res = decode(bytes(bad))
    assert res["kind"] == "display"
    assert res["crc_ok"] is False
    assert res["ok"] is True          # CRC beim Display nur informativ
    assert res["segments"] == bytes(bad[1:])  # Segmente enthalten das verfaelschte Byte unveraendert
    assert res["segments"] != seg


def test_short_frame_no_exception():
    res = decode(bytes([0x00, 0xE3, 0x20]))
    assert res["ok"] is False
    assert res["error"]
    assert decode(b"")["ok"] is False
    assert decode(b"")["error"]


def test_wrong_length_istwert():
    res = decode(bytes([0x00, 0xE3, 0x20, 0x10, 0x06, 0xFF]))
    assert res["ok"] is False
    assert res["kind"] == "istwert"
    assert res["error"]


def test_address_not_zero_rejected():
    frame = bytearray(build_istwert(0x06, 10))
    frame[0] = 0x2A
    res = decode(bytes(frame))
    assert res["ok"] is False
    assert res["addr"] == 0x2A
    assert res["error"]


def test_frame_without_leading_address_is_not_accepted():
    # Ohne fuehrendes 0x00 ist das erste Byte die Adresse: e3 != 0x00, also kein gueltiges Paket
    frame = build_istwert(0x06, 10)[1:]
    res = decode(frame)
    assert res["ok"] is False


def test_unknown_header():
    res = decode(bytes([0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77]))
    assert res["ok"] is False
    assert res["kind"] == "unknown"
    assert res["error"]


def test_decode_never_raises():
    rnd = random.Random(1)
    for _ in range(500):
        n = rnd.randint(0, 40)
        data = bytes(rnd.randint(0, 255) for _ in range(n))
        res = decode(data)
        assert isinstance(res, dict)
        assert res["ok"] in (True, False)
    assert decode(None)["ok"] is False
