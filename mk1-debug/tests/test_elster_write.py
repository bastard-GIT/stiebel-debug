"""Tests fuer Schreib-Funktionen in gui/elster.py (ohne Qt, ohne paho)."""
import sys
from pathlib import Path

GUI_DIR = Path(__file__).resolve().parents[1] / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

import pytest  # noqa: E402

from elster import (  # noqa: E402
    build_write_request,
    decode_value,
    encode_value,
    is_writable,
)


# --- Encode-Beispiele je Typ ---------------------------------------------

@pytest.mark.parametrize("text, raw", [
    ("-2", 0xFFFE),
    ("0", 0x0000),
    ("65535", 0xFFFF),
    ("-32767", 0x8001),
])
def test_encode_default(text, raw):
    assert encode_value("et_default", text) == raw


@pytest.mark.parametrize("text, raw", [
    ("21.0", 0x00D2),
    ("21,0", 0x00D2),
    ("-2.5", 0xFFE7),
    ("21.09", 0x00D2),      # abgeschnitten
    ("-0.05", 0x0000),      # gegen Null abgeschnitten
])
def test_encode_dec_val(text, raw):
    assert encode_value("et_dec_val", text) == raw


def test_encode_cent_val():
    assert encode_value("et_cent_val", "1.50") == 0x0096


def test_encode_mil_val():
    assert encode_value("et_mil_val", "1.000") == 0x03E8


@pytest.mark.parametrize("text, raw", [("5", 0x0005), ("255", 0x00FF), ("0", 0x0000)])
def test_encode_byte(text, raw):
    assert encode_value("et_byte", text) == raw


def test_encode_bool():
    assert encode_value("et_bool", "on") == 0x0001
    assert encode_value("et_bool", "OFF") == 0x0000


def test_encode_little_bool():
    assert encode_value("et_little_bool", "on") == 0x0100
    assert encode_value("et_little_bool", "off") == 0x0000


def test_encode_little_endian():
    assert encode_value("et_little_endian", "1") == 0x0100
    assert encode_value("et_little_endian", "258") == 0x0201


def test_encode_betriebsart_komfort_is_0300():
    assert encode_value("et_betriebsart", "Komfortbetrieb") == 0x0300


def test_encode_betriebsart_prefix_case_insensitive():
    assert encode_value("et_betriebsart", "programm") == 0x0200
    assert encode_value("et_betriebsart", "KOMFORT") == 0x0300


def test_encode_betriebsart_notbetrieb_takes_last_entry():
    # Notbetrieb steht doppelt (0x0000 und 0x0600); von hinten gewinnt 0x0600
    assert encode_value("et_betriebsart", "Notbetrieb") == 0x0600


def test_encode_zeit():
    assert encode_value("et_zeit", "13:30") == 0x1E0D


def test_encode_datum():
    assert encode_value("et_datum", "20.05.") == 0x1405
    assert encode_value("et_datum", "20.5.") == 0x1405


def test_encode_time_domain():
    assert encode_value("et_time_domain", "07:00-16:00") == 0x1C40


def test_encode_time_domain_empty_is_not_used():
    assert encode_value("et_time_domain", "") == 0x8080


# --- Round-Trip: decode_value(encode_value(...)) -------------------------

@pytest.mark.parametrize("type_name, text", [
    ("et_default", "-2"),
    ("et_dec_val", "21.0"),
    ("et_cent_val", "1.50"),
    ("et_mil_val", "1.000"),
    ("et_byte", "5"),
    ("et_bool", "on"),
    ("et_bool", "off"),
    ("et_little_bool", "on"),
    ("et_little_endian", "1"),
    ("et_betriebsart", "Programmbetrieb"),
    ("et_zeit", "13:30"),
    ("et_datum", "20.05."),
    ("et_time_domain", "07:00-16:00"),
])
def test_round_trip(type_name, text):
    assert decode_value(type_name, encode_value(type_name, text)) == text


# --- Hex-Rohwert ---------------------------------------------------------

def test_hex_raw_for_any_type():
    assert encode_value("et_default", "0x0300") == 0x0300
    assert encode_value("et_zeit", "0xFFFE") == 0xFFFE
    assert encode_value("et_double_val", "0x04D2") == 0x04D2  # nicht schreibbar, Hex erlaubt


def test_hex_raw_8000_rejected():
    with pytest.raises(ValueError):
        encode_value("et_default", "0x8000")


def test_hex_raw_out_of_range_rejected():
    with pytest.raises(ValueError):
        encode_value("et_default", "0x10000")


def test_hex_invalid_rejected():
    with pytest.raises(ValueError):
        encode_value("et_default", "0xZZ")


# --- Fehlerfaelle --------------------------------------------------------

@pytest.mark.parametrize("type_name, text", [
    ("et_double_val", "1.0"),
    ("et_triple_val", "1.0"),
    ("et_dev_nr", "1"),
    ("et_err_nr", "Hochdruck"),
    ("et_dev_id", "2-01"),
    ("et_default", "32768"),       # 0x8000 = kein Wert
    ("et_default", "-32768"),      # ausserhalb -32767..
    ("et_default", "1.5"),
    ("et_default", "abc"),
    ("et_dec_val", "3276.8"),      # *10 = 32768, ausserhalb +-32767
    ("et_dec_val", "x"),
    ("et_cent_val", "400.00"),     # *100 = 40000
    ("et_mil_val", "32.768"),      # *1000 = 32768
    ("et_byte", "256"),
    ("et_byte", "-1"),
    ("et_bool", "yes"),
    ("et_little_bool", "1"),
    ("et_little_endian", "65536"),
    ("et_betriebsart", "Unbekannt"),
    ("et_betriebsart", ""),
    ("et_zeit", "24:00"),
    ("et_zeit", "12:60"),
    ("et_zeit", "1230"),
    ("et_datum", "32.01."),
    ("et_datum", "10.13."),
    ("et_datum", "00.05."),
    ("et_time_domain", "16:00-07:00"),   # Start nach Ende
    ("et_time_domain", "07:00-07:00"),   # Start gleich Ende
    ("et_time_domain", "07:00"),
    ("et_unbekannt", "1"),
])
def test_encode_errors(type_name, text):
    with pytest.raises(ValueError):
        encode_value(type_name, text)


def test_is_writable():
    assert is_writable("et_default") is True
    assert is_writable("et_betriebsart") is True
    assert is_writable("et_double_val") is False
    assert is_writable("et_triple_val") is False
    assert is_writable("et_dev_nr") is False
    assert is_writable("et_err_nr") is False
    assert is_writable("et_dev_id") is False
    assert is_writable("gibt_es_nicht") is False


# --- Write-Frames --------------------------------------------------------

def test_write_programmschalter_long_example():
    can_id, data = build_write_request(0x180, 0x0112, 0x0300)
    assert can_id == 0x680
    assert data == bytes.fromhex("3000fa0112 0300".replace(" ", ""))


def test_write_short_frame():
    can_id, data = build_write_request(0x201, 0x0013, 0x0208)
    assert can_id == 0x680
    assert data == bytes.fromhex("4001130208" + "0000")


def test_write_short_forced_long_for_switch_index():
    # 0xF7..0xFA sind immer Langformat, auch bei fmt "auto"/"short"
    _, data = build_write_request(0x180, 0x00F7, 0x0001, fmt="short")
    assert data == bytes.fromhex("3000fa00f70001")


def test_write_long_format_for_short_index():
    _, data = build_write_request(0x180, 0x0013, 0x0208, fmt="long")
    assert data == bytes.fromhex("3000fa001302 08".replace(" ", ""))


def test_write_auto_long_for_big_index():
    _, data = build_write_request(0x201, 0x0112, 0x0300)
    assert data == bytes.fromhex("4001fa01120300")


def test_write_unknown_member_id_0x4ab():
    _, data = build_write_request(0x4AB, 0x0013, 0x0001)
    assert data[0] == ((0x4AB >> 3) & 0xF0)
    assert data[1] == 0x4AB & 0x7F
    assert data == bytes([0x90, 0x2B, 0x13, 0x00, 0x01, 0x00, 0x00])


@pytest.mark.parametrize("member, index, value, fmt", [
    (0x680, 0x0013, 0x0001, "auto"),   # eigene ID
    (0x800, 0x0013, 0x0001, "auto"),   # > 11 Bit
    (0x180, 0x0013, 0x10000, "auto"),  # Wert > 16 Bit
    (0x180, 0x0013, -1, "auto"),
    (0x180, 0x0013, 0x0001, "xyz"),    # unbekanntes Format
    (0x180, 0x0112, 0x0001, "short"),  # short mit Index > 0xFF
])
def test_write_request_errors(member, index, value, fmt):
    with pytest.raises(ValueError):
        build_write_request(member, index, value, fmt=fmt)


def test_write_error_own_id_0x680():
    with pytest.raises(ValueError):
        build_write_request(0x680, 0x0013, 0x0001)
