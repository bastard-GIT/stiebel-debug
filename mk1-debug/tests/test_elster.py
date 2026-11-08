"""Tests fuer gui/elster.py (ohne Qt, ohne paho)."""
import sys
from pathlib import Path

GUI_DIR = Path(__file__).resolve().parents[1] / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

import pytest  # noqa: E402

from elster import (  # noqa: E402
    ElsterDb,
    build_read_request,
    decode_value,
    parse_frame,
    switch_label,
)


@pytest.fixture(scope="module")
def db():
    return ElsterDb()


def test_read_request_kessel_programmschalter(db):
    can_id, data = build_read_request(0x180, 0x0112, db)
    assert can_id == 0x680
    assert data == bytes.fromhex("31 00 fa 01 12 00 00")


def test_read_request_speicher_short_index(db):
    can_id, data = build_read_request(0x201, 0x0003, db)
    assert can_id == 0x680
    assert data == bytes.fromhex("41 01 03 00 00 00 00")


def test_read_request_raumfernfuehler_generic_formula(db):
    # Generische Formel (7.1) gilt fuer jede ID, auch wenn read_id 00 00 ist
    can_id, data = build_read_request(0x400, 0x0003, db)
    assert can_id == 0x680
    assert data == bytes.fromhex("81 00 03 00 00 00 00")


def test_read_request_wpumpe_formula(db):
    can_id, data = build_read_request(0x514, 0x0001, db)
    assert can_id == 0x680
    assert data == bytes.fromhex("a1 14 01 00 00 00 00")


def test_read_request_auto_0xfa_is_long(db):
    _, data = build_read_request(0x180, 0x00FA, db, fmt="auto")
    assert data == bytes.fromhex("31 00 fa 00 fa 00 00")


def test_read_request_long_small_index(db):
    _, data = build_read_request(0x180, 0x000C, db, fmt="long")
    assert data == bytes.fromhex("31 00 fa 00 0c 00 00")


def test_read_request_short_index_above_ff_raises(db):
    with pytest.raises(ValueError):
        build_read_request(0x180, 0x0100, db, fmt="short")


def test_read_request_short_0xfa_is_long(db):
    _, data = build_read_request(0x180, 0x00FA, db, fmt="short")
    assert data == bytes.fromhex("31 00 fa 00 fa 00 00")


def test_read_request_target_0x680_raises(db):
    with pytest.raises(ValueError):
        build_read_request(0x680, 0x0003, db)


def test_read_request_id_above_7ff_raises(db):
    with pytest.raises(ValueError):
        build_read_request(0x800, 0x0003, db)


def test_read_request_index_above_ffff_raises(db):
    with pytest.raises(ValueError):
        build_read_request(0x180, 0x10000, db)


def test_read_request_db_ignored():
    assert build_read_request(0x180, 0x0112) == build_read_request(0x180, 0x0112, None)


def test_read_request_formula_matches_read_id_for_all_members(db):
    for m in db.members:
        name = m["name"]
        if name in ("RAUMFERNFUEHLER", "OTHER"):
            continue
        can_id = int(str(m["can_id"]), 16)
        read_id = bytes(int(str(x), 16) for x in m["read_id"])
        d0 = ((can_id >> 3) & 0xF0) | 0x01
        d1 = can_id & 0x7F
        assert bytes([d0, d1]) == read_id, name


def test_parse_frame_response(db):
    p = parse_frame(0x180, bytes.fromhex("d2 00 fa 01 12 02 00"))
    assert p is not None
    assert p["msg_type"] == 2
    assert p["msg_type_name"] == "response"
    assert p["index"] == 0x0112
    assert p["value"] == 0x0200
    assert p["sender"] == 0x180
    assert p["receiver"] == 0x680


def test_parse_frame_short_index():
    p = parse_frame(0x201, bytes.fromhex("d2 00 03 00 fb 00 00"))
    assert p["index"] == 0x03
    assert p["value"] == 0x00FB


def test_parse_frame_too_short():
    assert parse_frame(0x180, bytes.fromhex("d2 00 fa 01 12 02")) is None


def test_parse_frame_response_long_has_switch_fa():
    p = parse_frame(0x180, bytes.fromhex("d2 00 fa 01 12 02 00"))
    assert p["switch"] == 0xFA
    assert p["system"] is False


def test_parse_frame_short_has_no_switch():
    p = parse_frame(0x201, bytes.fromhex("d2 00 03 00 fb 00 00"))
    assert p["switch"] is None
    assert p["system"] is False


def test_parse_frame_switch_f8_min_long():
    p = parse_frame(0x180, bytes.fromhex("d2 00 f8 00 00 80 00"))
    assert p["switch"] == 0xF8
    assert p["index"] == 0
    assert p["value"] == 0x8000
    assert p["system"] is False


def test_parse_frame_switch_f9_max_long():
    p = parse_frame(0x180, bytes.fromhex("d2 00 f9 01 12 03 e8"))
    assert p["switch"] == 0xF9
    assert p["index"] == 0x0112
    assert p["value"] == 0x03E8


def test_parse_frame_switch_f7_long():
    p = parse_frame(0x180, bytes.fromhex("d2 00 f7 00 05 00 10"))
    assert p["switch"] == 0xF7
    assert p["index"] == 0x0005
    assert p["value"] == 0x0010


@pytest.mark.parametrize("sw", [0xF0, 0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6])
def test_parse_frame_f0_f6_are_short(sw):
    p = parse_frame(0x180, bytes([0xD2, 0x00, sw, 0x12, 0x34, 0x00, 0x00]))
    assert p["switch"] is None
    assert p["index"] == sw
    assert p["value"] == 0x1234


def test_parse_frame_fb_short():
    p = parse_frame(0x180, bytes.fromhex("d2 00 fb 80 00 00 00"))
    assert p["switch"] is None
    assert p["index"] == 0xFB
    assert p["value"] == 0x8000


def test_parse_frame_system_message_fe():
    p = parse_frame(0x100, bytes.fromhex("96 00 fe 01 00 00 00"))
    assert p["msg_type"] == 6
    assert p["system"] is True
    assert p["switch"] is None
    assert p["index"] == 0xFE
    assert p["value"] == 0x0100


def test_parse_frame_system_respond_is_short_even_with_f8_byte():
    p = parse_frame(0x100, bytes.fromhex("97 00 f8 00 01 00 00"))
    assert p["msg_type"] == 7
    assert p["system"] is True
    assert p["switch"] is None
    assert p["index"] == 0xF8
    assert p["value"] == 0x0001


def test_read_request_switch_f8_long(db):
    can_id, data = build_read_request(0x180, 0x0003, db, switch=0xF8)
    assert can_id == 0x680
    assert data == bytes.fromhex("31 00 f8 00 03 00 00")


def test_read_request_switch_f9_long(db):
    _, data = build_read_request(0x180, 0x0003, db, switch=0xF9)
    assert data == bytes.fromhex("31 00 f9 00 03 00 00")


def test_read_request_switch_f8_index_fb_is_long():
    _, data = build_read_request(0x180, 0x00FB, switch=0xF8)
    assert data == bytes.fromhex("31 00 f8 00 fb 00 00")


def test_read_request_switch_fa_default_unchanged():
    assert build_read_request(0x180, 0x0003, switch=0xFA) == build_read_request(0x180, 0x0003)


@pytest.mark.parametrize("index", [0xF7, 0xF8, 0xF9, 0xFA])
def test_read_request_auto_switch_indices_are_long(index):
    _, data = build_read_request(0x180, index, fmt="auto")
    assert data == bytes([0x31, 0x00, 0xFA, 0x00, index, 0x00, 0x00])


@pytest.mark.parametrize("index", [0xF7, 0xF8, 0xF9, 0xFA])
def test_read_request_short_switch_indices_are_long(index):
    _, data = build_read_request(0x180, index, fmt="short")
    assert data == bytes([0x31, 0x00, 0xFA, 0x00, index, 0x00, 0x00])


def test_read_request_short_index_fb_stays_short():
    _, data = build_read_request(0x180, 0x00FB, fmt="short")
    assert data == bytes.fromhex("31 00 fb 00 00 00 00")


def test_read_request_auto_index_fb_stays_short():
    _, data = build_read_request(0x180, 0x00FB)
    assert data == bytes.fromhex("31 00 fb 00 00 00 00")


@pytest.mark.parametrize("switch", [0xF7, 0xFB, 0x00, None])
def test_read_request_invalid_switch_raises(switch):
    with pytest.raises(ValueError):
        build_read_request(0x180, 0x0003, switch=switch)


def test_switch_label():
    assert switch_label(None) == ""
    assert switch_label(0xFA) == ""
    assert switch_label(0xF8) == "min"
    assert switch_label(0xF9) == "max"
    assert switch_label(0xF7) == "f7"


def test_decode_dec_val():
    assert decode_value("et_dec_val", 0x00FB) == "25.1"
    assert decode_value("et_dec_val", 0xFFFE) == "-0.2"


def test_decode_cent_val():
    assert decode_value("et_cent_val", 0x0096) == "1.50"


def test_decode_mil_val():
    assert decode_value("et_mil_val", 0x03E8) == "1.000"


def test_decode_not_supported():
    assert decode_value("et_dec_val", 0x8000) == "-255"
    assert decode_value("et_default", 0x8000) == "-255"


def test_decode_default_signed():
    assert decode_value("et_default", 0xFFFE) == "-2"


def test_decode_bool():
    assert decode_value("et_bool", 0x0001) == "on"
    assert decode_value("et_bool", 0x0000) == "off"
    assert decode_value("et_bool", 0x0005) == "?"
    assert decode_value("et_little_bool", 0x0100) == "on"


def test_decode_betriebsart():
    assert decode_value("et_betriebsart", 0x0200) == "Programmbetrieb"
    assert decode_value("et_betriebsart", 0x0300) == "Komfortbetrieb"
    assert decode_value("et_betriebsart", 0x0601) == "?"


def test_decode_zeit_datum_dev():
    assert decode_value("et_zeit", 0x1E0D) == "13:30"
    assert decode_value("et_datum", 0x1405) == "20.05."
    assert decode_value("et_dev_id", 0x0201) == "2-01"
    assert decode_value("et_dev_nr", 0x0000) == "1"
    assert decode_value("et_dev_nr", 0x0080) == "--"


def test_decode_time_domain():
    assert decode_value("et_time_domain", 0x1C40) == "07:00-16:00"
    assert decode_value("et_time_domain", 0x8080) == "not used time domain"


def test_decode_err_nr():
    assert decode_value("et_err_nr", 0x0004) == "Hochdruck"
    assert decode_value("et_err_nr", 0x0999) == "ERR 2457"


def test_decode_little_endian_and_byte():
    assert decode_value("et_little_endian", 0x0100) == "1"
    assert decode_value("et_byte", 0x00FF) == "-1"
    assert decode_value("et_byte", 0x0005) == "5"


def test_decode_double_and_triple_keep_raw():
    assert decode_value("et_double_val", 0x04D2) == "1234.000"
    assert decode_value("et_triple_val", 0x04D2) == "1234.000000"


def test_signal_name_known_and_unknown(db):
    assert db.signal_name(0x0112) == "PROGRAMMSCHALTER"
    assert db.signal_name(0x7777) == "UNK_0x7777"


def test_member_name(db):
    assert db.member_name(0x180) == "KESSEL"
    assert db.member_name(0x7FF) == "UNBEKANNT_0x7ff"


def test_search_signals(db):
    hits = db.search_signals("programm")
    assert any(s["name"] == "PROGRAMMSCHALTER" for s in hits)
    hits = db.search_signals("0x0112")
    assert any(s["name"] == "PROGRAMMSCHALTER" for s in hits)
