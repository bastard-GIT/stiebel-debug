"""Tests fuer gui/data_model.py (ohne Qt). Dekodierung ueber eine Fake-decode-Funktion
mit dem Rueckgabe-Dict aus INTERFACES.md 4.4 (lwz180.py entsteht erst in WP5)."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

GUI_DIR = Path(__file__).resolve().parents[1] / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

from data_model import DataModel  # noqa: E402

ISTWERT_HDR = b"\xe3\x20\x10"
TASTEN_HDR = b"\xe3\x30\x20"
DISPLAY_HDR = b"\xf7\x20"


def _result(**kw) -> dict:
    base = {"ok": True, "addr": None, "header": None, "kind": "unknown", "payload": b"",
            "crc_rx": None, "crc_calc": None, "crc_ok": None, "type": None, "name": None,
            "raw": None, "value": None, "unit": None, "keys": None, "segments": None, "error": None}
    base.update(kw)
    return base


def _hex_header(hdr: bytes) -> str:
    return " ".join(f"{b:02x}" for b in hdr)


def fake_decode(data: bytes) -> dict:
    """Fake-lwz180.decode: Adresse 0x00, Header ab data[1], CRC = Summe der Bytes 1..n-2 (mod 256)."""
    if len(data) < 5 or data[0] != 0x00:
        return _result(ok=False, error="zu kurz oder falsche Adresse")
    crc_rx = data[-1]
    crc_calc = sum(data[1:-1]) & 0xFF
    crc = {"crc_rx": crc_rx, "crc_calc": crc_calc, "crc_ok": crc_rx == crc_calc}
    hdr = data[1:4]
    if hdr == ISTWERT_HDR:
        type_id = data[4]
        raw = data[5]
        common = {"addr": 0, "header": _hex_header(hdr), "payload": data[4:-1], **crc}
        if type_id == 0x01:
            return _result(kind="istwert", type=type_id, name="T_VORLAUF", raw=raw,
                           value=raw * 0.1, unit="C", **common)
        return _result(kind="istwert", type=type_id, name=f"UNK_0x{type_id:02x}", raw=raw,
                       value=raw, unit=None, **common)
    if hdr == TASTEN_HDR:
        common = {"addr": 0, "header": _hex_header(hdr), "payload": data[4:-1], **crc}
        return _result(kind="tasten", keys=list(data[4:-1]), **common)
    if data[1:3] == DISPLAY_HDR:  # 2-Byte-Header
        common = {"addr": 0, "header": _hex_header(DISPLAY_HDR), "payload": data[3:-1], **crc}
        return _result(kind="display", segments=data[3:-1], **common)
    return _result(addr=0, payload=data[4:-1], **crc)


def _frame(body: bytes, ts: float | None = None, t: int = 0) -> dict:
    """Frame-Zeile. body ist Adresse+Header+Payload ohne CRC; CRC wird angehaengt."""
    crc = sum(body[1:]) & 0xFF
    data = body + bytes([crc])
    row = {"t": t, "u": 0, "data": data, "ack": "+" * len(data), "flags": 0}
    if ts is not None:
        row["ts"] = ts
    return row


def istwert(type_id: int, raw: int, t: int, ts: float | None = None, bad_crc: bool = False) -> dict:
    row = _frame(b"\x00" + ISTWERT_HDR + bytes([type_id, raw]), ts=ts, t=t)
    if bad_crc:
        row["data"] = row["data"][:-1] + bytes([(row["data"][-1] + 1) & 0xFF])
    return row


def test_add_frames_returns_decode_results_aligned():
    dm = DataModel(decode=fake_decode)
    rows = [istwert(0x01, 0x23, t=1000), _frame(b"\x00" + DISPLAY_HDR + bytes(28), t=1100)]
    res = dm.add_frames(rows)
    assert len(res) == 2
    assert res[0]["kind"] == "istwert" and res[1]["kind"] == "display"
    assert dm.frames == rows
    assert len(dm.decoded) == 2


def test_without_decode_only_raw_data():
    dm = DataModel()
    res = dm.add_frames([istwert(0x01, 0x23, t=1)])
    assert res == [None]
    assert dm.decoded == [None]
    assert dm.latest == {} and dm.crc_total == 0 and dm.counts_by_header == {}


def test_decode_exception_is_treated_as_none():
    def boom(data):
        raise RuntimeError("kaputt")
    dm = DataModel(decode=boom)
    assert dm.add_frames([istwert(1, 2, t=1)]) == [None]


def test_latest_and_history_istwert():
    dm = DataModel(decode=fake_decode)
    dm.add_frames([
        istwert(0x01, 0x10, t=1000, ts=1761000000.5),
        istwert(0x01, 0x20, t=2000, ts=1761000001.5),
        istwert(0x07, 0x05, t=2500, ts=1761000001.5),
    ])
    assert dm.latest[0x01]["value"] == 0x20 * 0.1
    assert dm.latest[0x01]["name"] == "T_VORLAUF"
    assert dm.latest[0x01]["unit"] == "C"
    assert dm.latest[0x01]["ts"] == 1761000001.5
    assert dm.latest[0x07]["name"] == "UNK_0x07"
    assert dm.latest[0x07]["unit"] is None
    assert dm.latest[0x07]["value"] == 0x05
    assert dm.history(0x01) == [(1761000000.5, 0x10 * 0.1), (1761000001.5, 0x20 * 0.1)]
    assert dm.history(0x99) == []


def test_history_falls_back_to_t_over_1000():
    dm = DataModel(decode=fake_decode)
    dm.add_frames([istwert(0x01, 0x10, t=1500)])
    assert dm.history(0x01) == [(1.5, 0x10 * 0.1)]
    assert dm.latest[0x01]["ts"] == 1.5


def test_crc_counters_and_rate():
    dm = DataModel(decode=fake_decode)
    assert dm.crc_error_rate() == 0.0
    dm.add_frames([istwert(1, 2, t=1), istwert(1, 3, t=2, bad_crc=True),
                   istwert(1, 4, t=3), _frame(b"\x00\x20\x10\x01", t=4)])
    assert dm.crc_total == 4
    assert dm.crc_bad == 1
    assert dm.crc_error_rate() == 0.25


def test_crc_none_is_not_counted():
    dm = DataModel(decode=lambda d: _result(kind="unknown", crc_ok=None))
    dm.add_frames([_frame(b"\x00\x20\x10\x01", t=1)])
    assert dm.crc_total == 0 and dm.crc_bad == 0


def test_key_log_from_tasten():
    dm = DataModel(decode=fake_decode)
    keys_row = _frame(b"\x00" + TASTEN_HDR + bytes([0x01, 0x02]), ts=10.0, t=5)
    dm.add_frames([keys_row])
    assert dm.key_log == [(10.0, [0x01, 0x02])]
    assert dm.counts_by_header == {"e3 30 20": 1}


def test_display_current_previous_and_changed_bits():
    dm = DataModel(decode=fake_decode)
    first = bytes(28)
    second = bytearray(28)
    second[0] = 0x80          # Byte 0, MSB -> Index 0
    second[27] = 0x01         # Byte 27, LSB -> Index 27*8+7
    dm.add_frames([_frame(b"\x00" + DISPLAY_HDR + first, t=1)])
    assert dm.display_current == first
    assert dm.display_previous is None
    assert dm.display_changed_bits() == []
    dm.add_frames([_frame(b"\x00" + DISPLAY_HDR + bytes(second), t=2)])
    assert dm.display_previous == first
    assert dm.display_current == bytes(second)
    assert dm.display_changed_bits() == [0, 27 * 8 + 7]


def test_display_wrong_length_is_ignored():
    dm = DataModel(decode=fake_decode)
    dm.add_frames([_frame(b"\x00" + DISPLAY_HDR + bytes(10), t=1)])
    assert dm.display_current is None


def test_filter_rows_by_header_and_text():
    dm = DataModel(decode=fake_decode)
    a = istwert(0x01, 0x10, t=1)
    b = _frame(b"\x00" + TASTEN_HDR + bytes([0x0f]), t=2)
    c = _frame(b"\x00\x42\x42", t=3)
    dm.add_frames([a, b, c])
    assert dm.filter_rows(header="e3 20 10") == [a]
    assert dm.filter_rows(header="E3 30 20") == [b]
    assert dm.filter_rows(text="0f") == [b]
    assert dm.filter_rows(text="t_vorlauf") == [a]
    assert dm.filter_rows() == [a, b, c]
    assert dm.filter_rows(header="ff ff ff") == []


def test_max_frames_drops_oldest():
    dm = DataModel(decode=fake_decode, max_frames=3)
    rows = [istwert(1, i, t=i) for i in range(5)]
    dm.add_frames(rows)
    assert dm.frames == rows[2:]
    assert len(dm.decoded) == 3
    assert dm.latest[1]["raw"] == 4


def test_clear_resets_everything():
    dm = DataModel(decode=fake_decode)
    dm.add_frames([istwert(1, 2, t=1, bad_crc=True),
                   _frame(b"\x00" + DISPLAY_HDR + bytes(28), t=2)])
    dm.clear()
    assert dm.frames == [] and dm.decoded == [] and dm.latest == {}
    assert dm.display_current is None and dm.key_log == []
    assert dm.crc_total == 0 and dm.crc_bad == 0 and dm.counts_by_header == {}
    assert dm.history(1) == []


def test_display_history_keeps_every_display_frame_and_caps():
    dm = DataModel(decode=fake_decode, max_frames=2)
    d1 = _frame(b"\x00" + DISPLAY_HDR + bytes([1]) + bytes(27), ts=10.0, t=1)
    d2 = _frame(b"\x00" + DISPLAY_HDR + bytes([1]) + bytes(27), ts=10.2, t=2)
    d3 = _frame(b"\x00" + DISPLAY_HDR + bytes([2]) + bytes(27), ts=10.4, t=3)
    dm.add_frames([d1, istwert(1, 2, t=4), d2, d3])
    assert len(dm.display_history) == 2
    ts, seg = dm.display_history[-1]
    assert ts == 10.4 and seg == bytes([2]) + bytes(27)
    dm.clear()
    assert dm.display_history == []
