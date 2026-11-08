"""Tests fuer gui/export_io.py (CSV/JSONL-Export und -Import, ohne Qt). Alle Dateien liegen in tmp_path."""
import csv
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

GUI_DIR = Path(__file__).resolve().parents[1] / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

import export_io  # noqa: E402

ROWS = [
    {"t": 123456, "u": 0, "data": bytes.fromhex("00e3201001"), "ack": "+++++", "flags": 0},
    {"t": 123500, "u": 0, "data": bytes.fromhex("00f7"), "ack": "+-", "flags": 2},
]

TS_ROWS = [
    {"t": 123456, "u": 0, "ts": 1761038123.456, "data": bytes.fromhex("00e3201001"),
     "ack": "++++-", "flags": 0, "ts_src": "esp"},
    {"t": 123500, "u": 0, "data": bytes.fromhex("0010"), "ack": "++", "flags": 0},
]


def _read_csv(path):
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8-sig")
    return list(csv.reader(text.splitlines(), delimiter=";"))


def fake_decode(data: bytes) -> dict:
    """Minimal-Dekoder mit Schluesseln aus INTERFACES.md 4.4."""
    base = {"ok": True, "addr": None, "header": None, "kind": "unknown", "payload": b"",
            "crc_rx": None, "crc_calc": None, "crc_ok": None, "type": None, "name": None,
            "raw": None, "value": None, "unit": None, "keys": None, "segments": None, "error": None}
    if data[:4] == b"\x00\xe3\x20\x10":
        base.update(kind="istwert", header="e3 20 10", type=data[4], name="T_VORLAUF",
                    raw=data[5], value=data[5] * 0.1, unit="C", crc_ok=True, crc_rx=data[-1],
                    crc_calc=data[-1])
    elif data[:4] == b"\x00\xe3\x30\x20":
        base.update(kind="tasten", header="e3 30 20", keys=[1, 2], crc_ok=False)
    elif data[:3] == b"\x00\xf7\x20":
        base.update(kind="display", header="f7 20", segments=data[3:-1], crc_ok=True)
    return base


def test_csv_bom_semicolon_crlf_and_header(tmp_path):
    path = tmp_path / "frames.csv"
    export_io.save_frames_csv(str(path), ROWS)

    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM fehlt"
    assert b"\r\n" in raw
    rows = _read_csv(path)
    assert rows[0] == ["t", "zeit_unix", "zeit_lokal", "adresse", "header", "typ",
                       "data_hex", "ack", "flags", "crc_ok", "name", "wert"]
    assert rows[0] == export_io.FRAME_CSV_HEADER


def test_csv_plain_row_without_decode(tmp_path):
    path = tmp_path / "plain.csv"
    export_io.save_frames_csv(str(path), ROWS)

    rows = _read_csv(path)
    assert rows[1] == ["123456", "", "", "0x00", "", "", "00e3201001", "+++++", "0", "", "", ""]
    assert rows[2] == ["123500", "", "", "0x00", "", "", "00f7", "+-", "2", "", "", ""]


def test_csv_with_decode_istwert_tasten_display(tmp_path):
    display = {"t": 1, "u": 0, "data": bytes([0x00, 0xF7, 0x20]) + bytes(28) + b"\x00",
               "ack": "+" * 32, "flags": 0}
    rows_in = [
        {"t": 10, "u": 0, "data": bytes.fromhex("00e32010011234"), "ack": "+" * 7, "flags": 0},
        {"t": 20, "u": 0, "data": bytes.fromhex("00e330200102ff"), "ack": "+" * 7, "flags": 0},
        display,
    ]
    path = tmp_path / "dec.csv"
    export_io.save_frames_csv(str(path), rows_in, decode=fake_decode)

    rows = _read_csv(path)
    ist = rows[1]
    assert ist[3] == "0x00"             # adresse
    assert ist[4] == "e3 20 10"         # header
    assert ist[5] == "0x01"             # typ
    assert ist[9] == "1"                # crc_ok
    assert ist[10] == "T_VORLAUF"       # name
    assert ist[11] == str(0x12 * 0.1)   # wert
    tast = rows[2]
    assert tast[4] == "e3 30 20"
    assert tast[9] == "0"
    assert tast[11] == "1,2"            # keys
    disp = rows[3]
    assert disp[4] == "f7 20"
    assert disp[10] == "" and disp[11] == ""


def test_csv_decode_exception_leaves_columns_empty(tmp_path):
    def boom(data):
        raise ValueError("kaputt")
    path = tmp_path / "boom.csv"
    export_io.save_frames_csv(str(path), ROWS[:1], decode=boom)
    rows = _read_csv(path)
    assert rows[1][4:6] == ["", ""] and rows[1][10:12] == ["", ""]


def test_csv_zeit_columns_from_ts(tmp_path):
    path = tmp_path / "ts.csv"
    export_io.save_frames_csv(str(path), TS_ROWS)

    rows = _read_csv(path)
    assert rows[1][0] == "123456"
    assert rows[1][1] == "1761038123.456"
    assert rows[1][2] == datetime.fromtimestamp(1761038123.456).strftime("%Y-%m-%d %H:%M:%S.456")
    assert len(rows[1][2]) == 23 and rows[1][2][19] == "."
    assert rows[2][1] == "" and rows[2][2] == ""


def test_csv_zeit_columns_from_u_ms(tmp_path):
    row = {"t": 1, "u": 1761400000123, "data": b"\x00", "ack": "+", "flags": 0}
    path = tmp_path / "u.csv"
    export_io.save_frames_csv(str(path), [row])
    assert _read_csv(path)[1][1] == "1761400000.123"


def test_jsonl_exact_line_format(tmp_path):
    path = tmp_path / "frames.jsonl"
    export_io.save_frames_jsonl(str(path), TS_ROWS[:1])
    line = path.read_text(encoding="utf-8").splitlines()[0]
    assert line == '{"t":123456,"ts":1761038123.456,"d":"00e3201001","a":"++++-","f":0,"ts_src":"esp"}'


def test_jsonl_roundtrip_with_ts_and_ts_src(tmp_path):
    path = tmp_path / "ts.jsonl"
    export_io.save_frames_jsonl(str(path), TS_ROWS)
    loaded = export_io.load_frames_jsonl(str(path))
    assert loaded == [TS_ROWS[0], TS_ROWS[1]]


def test_jsonl_roundtrip_without_ts(tmp_path):
    path = tmp_path / "nots.jsonl"
    export_io.save_frames_jsonl(str(path), ROWS)
    assert export_io.load_frames_jsonl(str(path)) == ROWS


def test_jsonl_writes_ts_from_u_when_no_ts(tmp_path):
    path = tmp_path / "u.jsonl"
    row = {"t": 7, "u": 1761400000500, "data": b"\x01", "ack": "+", "flags": 0}
    export_io.save_frames_jsonl(str(path), [row])
    assert path.read_text(encoding="utf-8").splitlines()[0] == '{"t":7,"ts":1761400000.5,"d":"01","a":"+","f":0}'


def test_jsonl_loads_raw_rx_lines_with_u(tmp_path):
    path = tmp_path / "rx.jsonl"
    path.write_text(
        '{"t":123,"u":1761400000123,"d":"00e3201001","a":"++++-","f":0}\n'
        '{"t":124,"u":0,"d":"0102","a":"++","f":8}\n',
        encoding="utf-8")
    loaded = export_io.load_frames_jsonl(str(path))
    assert loaded[0] == {"t": 123, "u": 1761400000123, "data": bytes.fromhex("00e3201001"),
                         "ack": "++++-", "flags": 0, "ts": 1761400000.123}
    assert loaded[1] == {"t": 124, "u": 0, "data": b"\x01\x02", "ack": "++", "flags": 8}
    assert "ts" not in loaded[1]


def test_jsonl_explicit_ts_wins_over_u(tmp_path):
    path = tmp_path / "both.jsonl"
    path.write_text('{"t":1,"u":5000,"ts":2.5,"d":"01","a":"+","f":0}\n', encoding="utf-8")
    assert export_io.load_frames_jsonl(str(path))[0]["ts"] == 2.5


def test_jsonl_invalid_ts_dropped_line_kept(tmp_path):
    path = tmp_path / "badts.jsonl"
    path.write_text('{"t":1,"ts":"abc","ts_src":5,"d":"0102","a":"++","f":0}\n', encoding="utf-8")
    loaded = export_io.load_frames_jsonl(str(path))
    assert loaded == [{"t": 1, "u": 0, "data": b"\x01\x02", "ack": "++", "flags": 0}]


def test_jsonl_skips_bad_lines(tmp_path):
    path = tmp_path / "bad.jsonl"
    content = "\n".join([
        '{"t":1,"d":"0102","a":"++","f":0}',
        "das ist kein json",
        '{"t":2,"d":"0102"}',                       # fehlt "a"
        '{"t":3,"d":"zz","a":"+","f":0}',           # ungueltiger Hex
        '{"t":"4","d":"00","a":"+","f":0}',         # falscher Typ fuer t
        '{"t":5,"d":"0102","a":"+","f":0}',         # a zu kurz
        '{"t":6,"d":"01","a":"+","f":"0"}',         # f falscher Typ
        "",
        '[1,2,3]',
        '{"t":9,"d":"01","a":"-","f":0}',
    ]) + "\n"
    path.write_text(content, encoding="utf-8")
    loaded = export_io.load_frames_jsonl(str(path))
    assert [r["t"] for r in loaded] == [1, 9]
    assert loaded[1]["data"] == b"\x01" and loaded[1]["ack"] == "-"

