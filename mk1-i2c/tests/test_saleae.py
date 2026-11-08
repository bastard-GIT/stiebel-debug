"""Tests fuer Saleae-I2C-Import (load_saleae_i2c_csv) und compare_frames (gui/export_io.py, ohne Qt).

Beispiel-CSV-Texte werden in tmp_path geschrieben. Format Logic 2 und Logic 1 (Legacy).
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

GUI_DIR = Path(__file__).resolve().parents[1] / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

import export_io  # noqa: E402

LOGIC2_HEADER = "name,type,start_time,duration,address,read,data,ack"
LOGIC2_OK = "\n".join([
    LOGIC2_HEADER,
    "ii2c,start,0.001000,0.000100,,,,",
    "ii2c,address,0.001100,0.000300,0x00,write,,ack",
    "ii2c,data,0.001500,0.000300,,,0xe3,ack",
    "ii2c,data,0.002000,0.000300,,,0x20,nack",
    "ii2c,stop,0.002500,0.000100,,,,",
    "ii2c,start,0.010000,0.000100,,,,",
    "ii2c,address,0.010100,0.000300,0x50,read,,ack",
    "ii2c,data,0.010500,0.000300,,,0x01,ack",
    "ii2c,stop,0.011000,0.000100,,,,",
]) + "\n"


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8-sig")
    return str(path)


def test_logic2_two_frames(tmp_path):
    rows = export_io.load_saleae_i2c_csv(_write(tmp_path, "l2.csv", LOGIC2_OK))
    assert len(rows) == 2
    assert rows[0] == {"t": 1, "u": 0, "data": b"\x00\xe3\x20", "ack": "++-", "flags": 0,
                       "ts_src": "saleae"}
    assert "ts" not in rows[0]
    assert rows[1]["t"] == 10
    assert rows[1]["data"] == b"\xa1\x01"      # 0x50 << 1 | read
    assert rows[1]["ack"] == "++"


def test_logic2_semicolon_and_decimal_comma(tmp_path):
    text = "\n".join([
        "name;type;start_time;duration;address;read;data;ack",
        "ii2c;start;0,002000;0,000100;;;;",
        "ii2c;address;0,002100;0,000300;0x10;write;;ack",
        "ii2c;data;0,002500;0,000300;;;0x0F;ack",
        "ii2c;stop;0,003000;0,000100;;;;",
    ]) + "\n"
    rows = export_io.load_saleae_i2c_csv(_write(tmp_path, "semi.csv", text))
    assert len(rows) == 1
    assert rows[0]["t"] == 2
    assert rows[0]["data"] == b"\x20\x0f"
    assert rows[0]["ack"] == "++"


def test_logic2_frame_without_stop_is_flushed_at_next_start(tmp_path):
    text = "\n".join([
        LOGIC2_HEADER,
        "ii2c,start,0.020000,0,,,,",
        "ii2c,address,0.020100,0,0x10,write,,ack",
        "ii2c,start,0.021000,0,,,,",
        "ii2c,address,0.021100,0,0x11,write,,nack",
        "ii2c,stop,0.021500,0,,,,",
    ]) + "\n"
    rows = export_io.load_saleae_i2c_csv(_write(tmp_path, "nostop.csv", text))
    assert [r["data"] for r in rows] == [b"\x20", b"\x22"]
    assert rows[1]["ack"] == "-"


def test_logic2_malformed_row_is_skipped(tmp_path):
    text = "\n".join([
        LOGIC2_HEADER,
        "ii2c,start,0.001000,0,,,,",
        "ii2c,address,0.001100,0,0x00,write,,ack",
        "ii2c,data,0.001500,0,,,0x99,",          # kein ACK-Wert: Zeile verworfen
        "ii2c,data,0.002000,0,,,0x20,ack",
        "ii2c,data,0.002500,0,,,zz,ack",         # ungueltiger Wert: verworfen
        "ii2c,stop,0.003000,0,,,,",
    ]) + "\n"
    rows = export_io.load_saleae_i2c_csv(_write(tmp_path, "bad.csv", text))
    assert len(rows) == 1
    assert rows[0]["data"] == b"\x00\x20"
    assert rows[0]["ack"] == "++"


def test_logic2_empty_frame_is_not_emitted(tmp_path):
    text = "\n".join([LOGIC2_HEADER, "ii2c,start,0.001000,0,,,,", "ii2c,stop,0.001100,0,,,,"]) + "\n"
    assert export_io.load_saleae_i2c_csv(_write(tmp_path, "empty.csv", text)) == []


LOGIC1_OK = "\n".join([
    "Time [s],Packet ID,Address,Data,Read/Write,ACK/NACK",
    "0.001000,1,0x00,,Write,ACK",
    "0.001100,1,,0xE3,,ACK",
    "0.001200,1,,0x20,,NACK",
    "0.002000,2,0x50,,Read,ACK",
    "0.002100,2,,0x01,,ACK",
]) + "\n"


def test_logic1_legacy_per_byte_rows(tmp_path):
    rows = export_io.load_saleae_i2c_csv(_write(tmp_path, "l1.csv", LOGIC1_OK))
    assert len(rows) == 2
    assert rows[0]["t"] == 1
    assert rows[0]["data"] == b"\x00\xe3\x20"
    assert rows[0]["ack"] == "++-"
    assert rows[0]["ts_src"] == "saleae" and rows[0]["u"] == 0 and rows[0]["flags"] == 0
    assert rows[1]["t"] == 2
    assert rows[1]["data"] == b"\xa1\x01"
    assert rows[1]["ack"] == "++"


def test_unknown_format_returns_empty(tmp_path):
    path = _write(tmp_path, "other.csv", "a,b,c\n1,2,3\n")
    assert export_io.load_saleae_i2c_csv(path) == []


def test_empty_file_returns_empty(tmp_path):
    assert export_io.load_saleae_i2c_csv(_write(tmp_path, "leer.csv", "")) == []


# ---------------------------------------------------------------- compare_frames

def _r(value: int, t: int = 0) -> dict:
    return {"t": t, "u": 0, "data": bytes([value]), "ack": "+", "flags": 0}


def test_compare_all_matched():
    ref = [_r(1), _r(2)]
    cap = [_r(1, 5), _r(2, 6)]
    res = export_io.compare_frames(cap, ref)
    assert res["matched"] == 2
    assert res["missing"] == [] and res["extra"] == [] and res["different"] == []
    assert res["match_rate"] == 1.0


def test_compare_different_missing_extra():
    ref = [_r(1), _r(2), _r(3), _r(4)]
    cap = [_r(1), _r(9), _r(3), _r(4), _r(5)]
    res = export_io.compare_frames(cap, ref)
    assert res["matched"] == 3
    assert res["different"] == [(cap[1], ref[1])]
    assert res["missing"] == []
    assert res["extra"] == [cap[4]]
    assert res["match_rate"] == 0.75


def test_compare_missing_frame():
    ref = [_r(1), _r(2), _r(3)]
    cap = [_r(1), _r(3)]
    res = export_io.compare_frames(cap, ref)
    assert res["matched"] == 2
    assert res["missing"] == [ref[1]]
    assert res["extra"] == [] and res["different"] == []
    assert res["match_rate"] == 2 / 3


def test_compare_unequal_replace_block_is_missing_and_extra():
    ref = [_r(1), _r(2), _r(3)]
    cap = [_r(1), _r(7), _r(8), _r(3)]
    res = export_io.compare_frames(cap, ref)
    assert res["matched"] == 2
    assert res["different"] == []
    assert res["missing"] == [ref[1]]
    assert res["extra"] == [cap[1], cap[2]]


def test_compare_empty_reference_rate_zero_division_safe():
    res = export_io.compare_frames([_r(1)], [])
    assert res["matched"] == 0
    assert res["extra"] == [_r(1)]
    assert res["match_rate"] == 0.0


def test_compare_ignores_time_and_ack():
    ref = [{"t": 1, "u": 0, "data": b"\x01\x02", "ack": "++", "flags": 0}]
    cap = [{"t": 99999, "u": 0, "data": b"\x01\x02", "ack": "--", "flags": 8}]
    res = export_io.compare_frames(cap, ref)
    assert res["matched"] == 1 and res["different"] == []
