"""Tests fuer gui/prod_api.py (Bus-Analyse-API der Produktiv-Firmware). Ohne Qt, ohne Broker.

Prueft Mitglieder-Tabelle, Namen, Switch-Bytes, Topic-Konstanten, Payload-Bau und die Parser
fuer Scan-Status, Scan-Ergebnisse, Member-Scan und Raw-Antworten.

Ausfuehren: C:\\Sandbox\\StiebelEltron_WP\\.venv-gui\\Scripts\\python.exe -I -B -m pytest tests\\test_prod_api.py -q
"""

import json
import sys
from pathlib import Path

import pytest

GUI_DIR = Path(__file__).resolve().parent.parent / "gui"
sys.path.insert(0, str(GUI_DIR))

import prod_api  # noqa: E402
from prod_api import (  # noqa: E402
    PC_CAN_ID,
    PROD_MEMBERS,
    PROD_ROOT,
    members_payload,
    member_name,
    member_topic_name,
    parse_member_result,
    parse_member_topic,
    parse_raw_result,
    parse_scan_result,
    parse_scan_result_topic,
    parse_scan_status,
    raw_read_payload,
    raw_write_payload,
    scan_start_payload,
    switch_name,
    switch_suffix,
)


def _j(obj):
    """Dict als JSON-Text, wie ihn die Firmware sendet."""
    return json.dumps(obj)


# ---------------------------------------------------------------- Konstanten und Tabelle

def test_root_and_constants():
    assert PROD_ROOT == "heatingpump"
    assert PC_CAN_ID == 0x680
    assert prod_api.DEFAULT_MEMBER_INDEX == 0x000B
    assert prod_api.SWITCH_VALUE == 0xFA
    assert prod_api.SWITCH_MIN == 0xF8
    assert prod_api.SWITCH_MAX == 0xF9


def test_topic_constants():
    assert prod_api.TOPIC_SCAN_START == "heatingpump/scan/start"
    assert prod_api.TOPIC_SCAN_STOP == "heatingpump/scan/stop"
    assert prod_api.TOPIC_SCAN_STATUS == "heatingpump/scan/status"
    assert prod_api.TOPIC_SCAN_MEMBERS == "heatingpump/scan/members"
    assert prod_api.TOPIC_SCAN_WILDCARD == "heatingpump/scan/#"
    assert prod_api.TOPIC_RAW_READ == "heatingpump/raw/read"
    assert prod_api.TOPIC_RAW_WRITE == "heatingpump/raw/write"
    assert prod_api.TOPIC_RAW_RESULT == "heatingpump/raw/result"
    assert prod_api.TOPIC_CAN_DEBUG_SET == "heatingpump/can_debug/set"
    assert prod_api.TOPIC_CAN_DEBUG_STATE == "heatingpump/can_debug/state"
    assert prod_api.TOPIC_CAN_DEBUG_FRAMES == "heatingpump/can_debug/frames"


def test_members_table_has_21_entries_without_pc():
    assert len(PROD_MEMBERS) == 21
    assert all(can_id != PC_CAN_ID for _, can_id in PROD_MEMBERS)
    assert all(name != "PC" for name, _ in PROD_MEMBERS)


def test_members_ids_and_names_are_unique():
    names = [name for name, _ in PROD_MEMBERS]
    ids = [can_id for _, can_id in PROD_MEMBERS]
    assert len(set(names)) == len(names)
    assert len(set(ids)) == len(ids)


def test_members_contain_wpla_names():
    table = dict(PROD_MEMBERS)
    assert table["WPUMPE"] == 0x514
    assert table["SPEICHER"] == 0x201
    assert table["FES"] == 0x100
    assert table["OTHER"] == 0x000
    assert table["MANAGER"] == 0x480
    assert table["MISCHERMODUL_2"] == 0x601


def test_member_name_known_ids():
    assert member_name(0x514) == "WPUMPE"
    assert member_name(0x201) == "SPEICHER"
    assert member_name(0x180) == "KESSEL"
    assert member_name(0x000) == "OTHER"


def test_member_name_unknown_id_is_none():
    assert member_name(0x123) is None


def test_member_name_pc_is_none():
    assert member_name(0x680) is None


def test_member_topic_name_table_member():
    assert member_topic_name(0x500) == "HEIZMODUL"
    assert member_topic_name(0x514) == "WPUMPE"


def test_member_topic_name_unknown_hex_is_lower_case_three_digits():
    assert member_topic_name(0x123) == "0x123"
    assert member_topic_name(0x7FF) == "0x7ff"
    assert member_topic_name(0x00A) == "0x00a"


def test_member_topic_name_pc_raises():
    with pytest.raises(ValueError):
        member_topic_name(0x680)


@pytest.mark.parametrize("can_id", [-1, 0x800, 0xFFF])
def test_member_topic_name_out_of_range_raises(can_id):
    with pytest.raises(ValueError):
        member_topic_name(can_id)


def test_switch_name_values():
    assert switch_name(0xFA) == "fa"
    assert switch_name(0xF8) == "f8"
    assert switch_name(0xF9) == "f9"


@pytest.mark.parametrize("sw", [0x00, 0xF7, 0xFB, 0x100])
def test_switch_name_invalid_raises(sw):
    with pytest.raises(ValueError):
        switch_name(sw)


def test_switch_suffix_values():
    assert switch_suffix(0xFA) == ""
    assert switch_suffix(0xF8) == "/f8"
    assert switch_suffix(0xF9) == "/f9"


def test_switch_suffix_invalid_raises():
    with pytest.raises(ValueError):
        switch_suffix(0xFB)


# ---------------------------------------------------------------- Scan-Start

def test_scan_start_name_only():
    assert scan_start_payload(0x180) == "KESSEL"


def test_scan_start_range_fa_omitted():
    assert scan_start_payload(0x180, 0x0000, 0x00FF) == "KESSEL 0x0000 0x00ff"


def test_scan_start_range_with_f8():
    assert scan_start_payload(0x180, 0x0000, 0x00FF, 0xF8) == "KESSEL 0x0000 0x00ff f8"


def test_scan_start_no_range_with_f9():
    assert scan_start_payload(0x514, switch=0xF9) == "WPUMPE f9"


def test_scan_start_unknown_hex_id_uses_hex_name():
    assert scan_start_payload(0x7FF, 0x0000, 0x00FF) == "0x7ff 0x0000 0x00ff"


def test_scan_start_lo_without_hi_raises():
    with pytest.raises(ValueError):
        scan_start_payload(0x180, lo=0x10)


def test_scan_start_hi_without_lo_raises():
    with pytest.raises(ValueError):
        scan_start_payload(0x180, hi=0x10)


def test_scan_start_lo_greater_than_hi_raises():
    with pytest.raises(ValueError):
        scan_start_payload(0x180, 0x0100, 0x00FF)


def test_scan_start_range_above_ffff_raises():
    with pytest.raises(ValueError):
        scan_start_payload(0x180, 0x0000, 0x10000)


def test_scan_start_negative_lo_raises():
    with pytest.raises(ValueError):
        scan_start_payload(0x180, -1, 0x10)


def test_scan_start_pc_raises():
    with pytest.raises(ValueError):
        scan_start_payload(0x680)


@pytest.mark.parametrize("can_id", [-1, 0x800])
def test_scan_start_id_out_of_range_raises(can_id):
    with pytest.raises(ValueError):
        scan_start_payload(can_id)


def test_scan_start_invalid_switch_raises():
    with pytest.raises(ValueError):
        scan_start_payload(0x180, switch=0xFB)


# ---------------------------------------------------------------- Member-Scan und Raw-Payloads

def test_members_payload_default():
    assert members_payload() == "0x000 0x7ff 0x000b"


def test_members_payload_range_and_index():
    assert members_payload(0x100, 0x1FF, 0x0013) == "0x100 0x1ff 0x0013"


@pytest.mark.parametrize("args", [(0x100, 0x0FF), (0, 0x800), (-1, 0x10), (0, 0x7FF, 0x10000)])
def test_members_payload_invalid_raises(args):
    with pytest.raises(ValueError):
        members_payload(*args)


def test_raw_read_payload_default_switch():
    assert raw_read_payload(0x180, 0x0013) == "0x180 0x0013"


def test_raw_read_payload_f8_and_f9_suffix():
    assert raw_read_payload(0x180, 0x0013, 0xF8) == "0x180 0x0013 f8"
    assert raw_read_payload(0x180, 0x0013, 0xF9) == "0x180 0x0013 f9"


def test_raw_read_payload_pc_raises():
    with pytest.raises(ValueError):
        raw_read_payload(0x680, 0x0013)


def test_raw_read_payload_index_too_big_raises():
    with pytest.raises(ValueError):
        raw_read_payload(0x180, 0x10000)


def test_raw_read_payload_invalid_switch_raises():
    with pytest.raises(ValueError):
        raw_read_payload(0x180, 0x0013, 0x00)


def test_raw_write_payload():
    assert raw_write_payload(0x180, 0x0013, 0x0208) == "0x180 0x0013 0x0208"


@pytest.mark.parametrize("args", [(0x680, 0x13, 0x208), (0x800, 0x13, 0x208),
                                  (0x180, 0x10000, 0), (0x180, 0x13, 0x10000), (0x180, -1, 0)])
def test_raw_write_payload_invalid_raises(args):
    with pytest.raises(ValueError):
        raw_write_payload(*args)


# ---------------------------------------------------------------- Parser: Scan-Status

def test_scan_status_valid():
    text = _j({"state": "running", "mode": "members", "member": "", "done": 5, "total": 2048,
               "hits": 1, "seconds": 12})
    result = parse_scan_status(text)
    assert result == {"state": "running", "mode": "members", "member": "", "done": 5,
                      "total": 2048, "hits": 1, "seconds": 12}


def test_scan_status_done_table():
    text = _j({"state": "done", "mode": "table", "member": "KESSEL", "done": 3600, "total": 3600,
               "hits": 42, "seconds": 540})
    assert parse_scan_status(text)["member"] == "KESSEL"


def test_scan_status_broken_json_is_none():
    assert parse_scan_status("{kaputt") is None


def test_scan_status_list_is_none():
    assert parse_scan_status("[1, 2]") is None


def test_scan_status_missing_field_is_none():
    text = _j({"state": "done", "mode": "table", "member": "KESSEL", "done": 1, "total": 1,
               "hits": 0})
    assert parse_scan_status(text) is None


def test_scan_status_unknown_state_is_none():
    text = _j({"state": "paused", "mode": "table", "member": "", "done": 0, "total": 1,
               "hits": 0, "seconds": 0})
    assert parse_scan_status(text) is None


def test_scan_status_unknown_mode_is_none():
    text = _j({"state": "running", "mode": "all", "member": "", "done": 0, "total": 1,
               "hits": 0, "seconds": 0})
    assert parse_scan_status(text) is None


def test_scan_status_bool_as_int_is_none():
    text = _j({"state": "running", "mode": "range", "member": "", "done": True, "total": 1,
               "hits": 0, "seconds": 0})
    assert parse_scan_status(text) is None


def test_scan_status_non_str_input_is_none():
    assert parse_scan_status(None) is None
    assert parse_scan_status(b"{}") is None


# ---------------------------------------------------------------- Parser: Scan-Topic und -Ergebnis

def test_scan_result_topic_fa():
    assert parse_scan_result_topic("heatingpump/scan/KESSEL/0x0013") == ("KESSEL", 0x13, 0xFA)


def test_scan_result_topic_f8_and_f9():
    assert parse_scan_result_topic("heatingpump/scan/WPUMPE/0x00ff/f8") == ("WPUMPE", 0xFF, 0xF8)
    assert parse_scan_result_topic("heatingpump/scan/WPUMPE/0x00ff/f9") == ("WPUMPE", 0xFF, 0xF9)


def test_scan_result_topic_hex_name():
    assert parse_scan_result_topic("heatingpump/scan/0x123/0xfd00") == ("0x123", 0xFD00, 0xFA)


@pytest.mark.parametrize("topic", [
    "heatingpump/scan/status",
    "heatingpump/scan/start",
    "heatingpump/scan/stop",
    "heatingpump/scan/members",
    "heatingpump/scan/members/0x180",
    "heatingpump/scan/status/0x0013",
    "heatingpump/scan/KESSEL/0x013",
    "heatingpump/scan/KESSEL/0x00013",
    "heatingpump/scan/KESSEL/0x00FF",
    "heatingpump/scan/KESSEL/0x0013/fa",
    "heatingpump/scan/KESSEL/0x0013/f7",
    "heatingpump/scan/KESSEL/0x0013/f8/x",
    "heatingpump/scan/KESSEL/zz",
    "heatingpump/raw/result",
    "other/scan/KESSEL/0x0013",
    "heatingpump/scan/",
    "",
])
def test_scan_result_topic_rejects(topic):
    assert parse_scan_result_topic(topic) is None


def test_scan_result_topic_non_str_is_none():
    assert parse_scan_result_topic(None) is None


def test_scan_result_valid():
    text = _j({"name": "KESSEL", "raw": 520, "value": "52.0"})
    assert parse_scan_result(text) == {"name": "KESSEL", "raw": 520, "value": "52.0"}


def test_scan_result_empty_name_is_valid():
    text = _j({"name": "", "raw": 7, "value": "7"})
    assert parse_scan_result(text) == {"name": "", "raw": 7, "value": "7"}


def test_scan_result_missing_raw_is_none():
    assert parse_scan_result(_j({"name": "KESSEL", "value": "52.0"})) is None


def test_scan_result_bool_raw_is_none():
    assert parse_scan_result(_j({"name": "KESSEL", "raw": True, "value": "1"})) is None


def test_scan_result_broken_json_is_none():
    assert parse_scan_result("{\"name\":") is None


# ---------------------------------------------------------------- Parser: Member-Scan

def test_member_topic_valid():
    assert parse_member_topic("heatingpump/scan/members/0x180") == 0x180
    assert parse_member_topic("heatingpump/scan/members/0x000") == 0x000


def test_member_topic_rejects():
    assert parse_member_topic("heatingpump/scan/members/0x18") is None
    assert parse_member_topic("heatingpump/scan/members/0x7FF") is None
    assert parse_member_topic("heatingpump/scan/members/0x800") is None
    assert parse_member_topic("heatingpump/scan/KESSEL/0x180") is None
    assert parse_member_topic("heatingpump/scan/members") is None


def test_member_result_valid():
    assert parse_member_result(_j({"index": "0x000b", "raw": 1234})) == {"index": 0x0B, "raw": 1234}


def test_member_result_bad_index_is_none():
    assert parse_member_result(_j({"index": "11", "raw": 1})) is None
    assert parse_member_result(_j({"index": "0x10000", "raw": 1})) is None


def test_member_result_missing_raw_is_none():
    assert parse_member_result(_j({"index": "0x000b"})) is None


def test_member_result_broken_json_is_none():
    assert parse_member_result("nicht json") is None


# ---------------------------------------------------------------- Parser: Raw-Antworten

def test_raw_read_ok():
    text = _j({"op": "read", "id": "0x180", "index": "0x0013", "switch": "fa", "raw": "0x0208",
               "status": "ok"})
    assert parse_raw_result(text) == {"op": "read", "id": 0x180, "index": 0x13, "switch": 0xFA,
                                      "raw": 0x208, "status": "ok"}


def test_raw_read_f8_unsupported_keeps_raw_8000():
    text = _j({"op": "read", "id": "0x180", "index": "0x0013", "switch": "f8", "raw": "0x8000",
               "status": "unsupported"})
    result = parse_raw_result(text)
    assert result["switch"] == 0xF8
    assert result["raw"] == 0x8000
    assert result["status"] == "unsupported"


def test_raw_read_timeout_without_raw_is_none_raw():
    text = _j({"op": "read", "id": "0x180", "index": "0x0013", "switch": "f9", "status": "timeout"})
    result = parse_raw_result(text)
    assert result["raw"] is None
    assert result["switch"] == 0xF9
    assert result["status"] == "timeout"


def test_raw_read_busy_without_raw_is_none_raw():
    text = _j({"op": "read", "id": "0x180", "index": "0x0013", "switch": "fa", "status": "busy"})
    assert parse_raw_result(text)["raw"] is None


def test_raw_read_invalid_switch_is_none():
    text = _j({"op": "read", "id": "0x180", "index": "0x0013", "switch": "f7", "status": "ok"})
    assert parse_raw_result(text) is None


def test_raw_read_broken_raw_field_is_none():
    text = _j({"op": "read", "id": "0x180", "index": "0x0013", "switch": "fa", "raw": "208",
               "status": "ok"})
    assert parse_raw_result(text) is None


def test_raw_write_verified():
    text = _j({"op": "write", "id": "0x180", "index": "0x0013", "value": "0x0208",
               "status": "verified", "ack": 4, "readback": "0x0208"})
    assert parse_raw_result(text) == {"op": "write", "id": 0x180, "index": 0x13, "value": 0x208,
                                      "status": "verified", "ack": 4, "readback": 0x208}


def test_raw_write_no_ack_has_none_fields():
    text = _j({"op": "write", "id": "0x180", "index": "0x0013", "value": "0x0208",
               "status": "no_ack"})
    result = parse_raw_result(text)
    assert result["ack"] is None
    assert result["readback"] is None
    assert result["status"] == "no_ack"


def test_raw_write_mismatch_keeps_readback():
    text = _j({"op": "write", "id": "0x180", "index": "0x0013", "value": "0x0208",
               "status": "mismatch", "ack": 2, "readback": "0x0100"})
    result = parse_raw_result(text)
    assert result["readback"] == 0x100
    assert result["ack"] == 2


def test_raw_write_rejected_without_ack():
    text = _j({"op": "write", "id": "0x680", "index": "0x0013", "value": "0x0208",
               "status": "rejected"})
    assert parse_raw_result(text)["status"] == "rejected"


def test_raw_unknown_op_is_none():
    text = _j({"op": "erase", "id": "0x180", "index": "0x0013", "status": "ok"})
    assert parse_raw_result(text) is None


def test_raw_broken_json_is_none():
    assert parse_raw_result("{\"op\":") is None


def test_raw_id_above_7ff_is_none():
    text = _j({"op": "read", "id": "0x800", "index": "0x0013", "switch": "fa", "status": "timeout"})
    assert parse_raw_result(text) is None


def test_raw_missing_status_is_none():
    text = _j({"op": "read", "id": "0x180", "index": "0x0013", "switch": "fa"})
    assert parse_raw_result(text) is None
