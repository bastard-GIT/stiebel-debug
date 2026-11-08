import csv
import os
import sys
from datetime import datetime

GUI_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "gui"))
if GUI_DIR not in sys.path:
    sys.path.insert(0, GUI_DIR)

import export_io  # noqa: E402


ROWS = [
    {"t": 123456, "id": 0x180, "data": bytes.fromhex("d200fa01120200"), "tx": False},
    {"t": 123500, "id": 0x680, "data": bytes.fromhex("3100fa01120000"), "tx": True},
]

# Langlayout mit Switch 0xF8 (Minimum), Index 0x0003, Wert 0x0100
ROW_F8 = {"t": 123600, "id": 0x180,
          "data": bytes([0xD2, 0x00, 0xF8, 0x00, 0x03, 0x01, 0x00]), "tx": False}
# Systemnachricht (msg_type 6), d2 = 0xFE
ROW_SYS = {"t": 123700, "id": 0x180,
           "data": bytes([0x06, 0x01, 0xFE, 0x00, 0x00, 0x00, 0x00]), "tx": False}


class _FakeDb:
    """Minimaler ElsterDb-Ersatz: nur die Methoden, die export_io nutzt."""

    def signal_name(self, index):
        return f"SIG_{index:04x}"

    def signal_by_index(self, index):
        return {"type": "et_default"}


def test_jsonl_roundtrip(tmp_path):
    path = tmp_path / "frames.jsonl"
    export_io.save_frames_jsonl(str(path), ROWS)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"t":123456,"id":"0x180","d":"d200fa01120200","tx":false}'

    loaded = export_io.load_frames_jsonl(str(path))
    assert loaded == ROWS


def test_csv_header(tmp_path):
    path = tmp_path / "frames.csv"
    export_io.save_frames_csv(str(path), ROWS)

    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM fehlt"

    text = raw.decode("utf-8-sig")
    reader = csv.reader(text.splitlines(), delimiter=";")
    header = next(reader)
    assert header == ["t", "zeit_unix", "zeit_lokal", "richtung", "can_id", "data_hex",
                      "absender", "empfaenger", "msg_type", "index", "switch", "name"]

    first = next(reader)
    assert first[:6] == ["123456", "", "", "RX", "0x180", "d200fa01120200"]
    second = next(reader)
    assert second[:5] == ["123500", "", "", "TX", "0x680"]


def test_csv_header_constant():
    assert export_io.FRAME_CSV_HEADER.index("switch") == \
        export_io.FRAME_CSV_HEADER.index("index") + 1
    assert export_io.SCAN_CSV_HEADER == ["index", "switch", "name", "raw", "status", "wert"]


def test_csv_f8_frame_switch_min(tmp_path):
    path = tmp_path / "f8.csv"
    export_io.save_frames_csv(str(path), [ROW_F8], db=_FakeDb())

    rows = list(csv.reader(path.read_bytes().decode("utf-8-sig").splitlines(), delimiter=";"))
    row = rows[1]
    assert row[0] == "123600"
    assert row[3:6] == ["RX", "0x180", "d200f800030100"]
    assert row[8] == "2"                       # msg_type response
    assert row[9] == "0x0003"                  # index
    assert row[10] == "min"                    # switch
    assert row[11] == "SIG_0003"


def test_csv_fa_frame_switch_empty(tmp_path):
    path = tmp_path / "fa.csv"
    export_io.save_frames_csv(str(path), ROWS[:1], db=_FakeDb())

    row = list(csv.reader(path.read_bytes().decode("utf-8-sig").splitlines(), delimiter=";"))[1]
    assert row[10] == ""                       # 0xFA -> leer


def test_csv_system_frame(tmp_path):
    path = tmp_path / "sys.csv"
    export_io.save_frames_csv(str(path), [ROW_SYS], db=_FakeDb())

    row = list(csv.reader(path.read_bytes().decode("utf-8-sig").splitlines(), delimiter=";"))[1]
    assert row[8] == "6"                       # msg_type system
    assert row[10] == ""                       # kein Switch bei Systemnachricht


def test_csv_without_db_switch_empty(tmp_path):
    path = tmp_path / "nodb.csv"
    export_io.save_frames_csv(str(path), [ROW_F8])

    row = list(csv.reader(path.read_bytes().decode("utf-8-sig").splitlines(), delimiter=";"))[1]
    assert row[10] == ""


TS_ROWS = [
    {"t": 123456, "ts": 1761038123.456, "id": 0x180,
     "data": bytes.fromhex("d200fa01120200"), "tx": False, "ts_src": "rx"},
    {"t": 123500, "id": 0x680, "data": bytes.fromhex("3100fa01120000"), "tx": True},
]


def test_csv_zeit_columns_with_ts(tmp_path):
    path = tmp_path / "ts.csv"
    export_io.save_frames_csv(str(path), TS_ROWS)

    rows = list(csv.reader(path.read_bytes().decode("utf-8-sig").splitlines(), delimiter=";"))
    assert rows[1][0] == "123456"
    assert rows[1][1] == "1761038123.456"
    assert rows[1][2] == datetime.fromtimestamp(1761038123.456).strftime("%Y-%m-%d %H:%M:%S.456")
    assert rows[1][2][19] == "." and len(rows[1][2]) == 23


def test_csv_zeit_columns_empty_without_ts(tmp_path):
    path = tmp_path / "nots.csv"
    export_io.save_frames_csv(str(path), TS_ROWS)

    rows = list(csv.reader(path.read_bytes().decode("utf-8-sig").splitlines(), delimiter=";"))
    assert rows[2][1] == "" and rows[2][2] == ""
    assert rows[2][3] == "TX"


def test_jsonl_roundtrip_with_ts(tmp_path):
    path = tmp_path / "ts.jsonl"
    export_io.save_frames_jsonl(str(path), TS_ROWS)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ('{"t":123456,"ts":1761038123.456,"id":"0x180","d":"d200fa01120200",'
                        '"tx":false,"ts_src":"rx"}')
    assert lines[1] == '{"t":123500,"id":"0x680","d":"3100fa01120000","tx":true}'

    loaded = export_io.load_frames_jsonl(str(path))
    assert loaded == TS_ROWS


def test_jsonl_loads_old_line_without_ts(tmp_path):
    path = tmp_path / "old.jsonl"
    path.write_text('{"t":1,"id":"0x180","d":"0102","tx":false}\n', encoding="utf-8")

    loaded = export_io.load_frames_jsonl(str(path))
    assert loaded == [{"t": 1, "id": 0x180, "data": b"\x01\x02", "tx": False}]
    assert "ts" not in loaded[0] and "ts_src" not in loaded[0]


def test_jsonl_invalid_ts_dropped_line_kept(tmp_path):
    path = tmp_path / "badts.jsonl"
    path.write_text('{"t":1,"ts":"abc","ts_src":5,"id":"0x180","d":"0102","tx":false}\n',
                    encoding="utf-8")

    loaded = export_io.load_frames_jsonl(str(path))
    assert loaded == [{"t": 1, "id": 0x180, "data": b"\x01\x02", "tx": False}]


def test_scan_csv_switch_column(tmp_path):
    path = tmp_path / "scan.csv"
    items = [
        {"index": 3, "raw": 0x0100, "status": "ok", "switch": 0xF8},
        {"index": 3, "raw": 0x0100, "status": "ok"},                 # Standard 0xFA
        {"index": "0x0003", "raw": "0x0100", "status": "ok", "switch": "0xf9"},
        {"index": 3, "raw": 0x0100, "status": "ok", "switch": 0xF7},
    ]
    export_io.save_scan_csv(str(path), items, _FakeDb())

    rows = list(csv.reader(path.read_bytes().decode("utf-8-sig").splitlines(), delimiter=";"))
    assert rows[0] == ["index", "switch", "name", "raw", "status", "wert"]
    assert rows[1] == ["0x0003", "min", "SIG_0003", "0x0100", "ok", "256"]
    assert rows[2] == ["0x0003", "", "SIG_0003", "0x0100", "ok", "256"]
    assert rows[3] == ["0x0003", "max", "SIG_0003", "0x0100", "ok", "256"]
    assert rows[4] == ["0x0003", "f7", "SIG_0003", "0x0100", "ok", "256"]


def test_jsonl_skips_bad_lines(tmp_path):
    path = tmp_path / "bad.jsonl"
    content = "\n".join([
        '{"t":1,"id":"0x180","d":"d200fa01120200","tx":false}',
        "das ist kein json",
        '{"t":2,"id":"0x180","tx":false}',                  # fehlt "d"
        '{"t":3,"id":"0x180","d":"zz","tx":false}',         # ungueltiger Hex
        '{"t":"4","id":"0x180","d":"00","tx":false}',       # falscher Typ
        "",
        '{"t":5,"id":"0x181","d":"0102","tx":true}',
    ]) + "\n"
    path.write_text(content, encoding="utf-8")

    loaded = export_io.load_frames_jsonl(str(path))
    assert [r["t"] for r in loaded] == [1, 5]
    assert loaded[1] == {"t": 5, "id": 0x181, "data": b"\x01\x02", "tx": True}
