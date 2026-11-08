"""Tests fuer gui/i2c_ref_decoder.py (Referenzdecoder fuer LA-Importe, ohne Qt, ohne Netz).

Ausfuehren: C:\\temp\\stiebel-Remote\\.venv\\Scripts\\python.exe -I -B -m pytest C:\\temp\\stiebel-Remote\\mk1-i2c\\tests\\test_i2c_ref_decoder.py -q
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

GUI_DIR = Path(__file__).resolve().parents[1] / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

import pytest  # noqa: E402

from i2c_ref_decoder import (  # noqa: E402
    FLAG_BITERR,
    FLAG_NOSTOP,
    FLAG_OVERLEN,
    FLAG_REPSTART,
    FLAG_RING_OVF,
    ChangeFilter,
    I2cRefDecoder,
    bits_to_events,
    decode_events,
    frame_to_row,
    synth_events,
)

CPU = 240                 # MHz
HALF_BIT = 3840           # Zyklen je halbem Bit bei 240 MHz (31,25 kHz)
ERR_KEYS = ("err_nostop", "err_bits", "err_overlen", "err_ring", "err_skew")


def _byte_bits(value: int) -> list[int]:
    return [(value >> (7 - k)) & 1 for k in range(8)]


def test_start_stop_basic():
    ev = synth_events(b"\x00\xe3\x20\x10")
    frames, c = decode_events(ev)
    assert len(frames) == 1
    assert frames[0]["data"] == b"\x00\xe3\x20\x10"
    assert frames[0]["ack"] == "++++"
    assert frames[0]["flags"] == 0
    assert c["starts"] == 1 and c["stops"] == 1 and c["frames"] == 1
    assert c["events"] == len(ev)
    assert all(c[k] == 0 for k in ERR_KEYS)


def test_ack_and_nack_per_byte():
    frames, c = decode_events(synth_events(b"\x01\x02\x03", ack=[True, False, True]))
    assert frames[0]["data"] == b"\x01\x02\x03"
    assert frames[0]["ack"] == "+-+"
    assert c["err_nostop"] == 0


def test_nack_on_last_byte_then_stop():
    frames, _ = decode_events(synth_events(b"\xaa", ack=[False]))
    assert frames[0]["ack"] == "-"
    assert frames[0]["flags"] == 0


def test_frame_with_28_bytes():
    data = bytes(range(28))
    frames, c = decode_events(synth_events(data))
    assert frames[0]["data"] == data
    assert frames[0]["ack"] == "+" * 28
    assert c["frames"] == 1


def test_empty_frame_start_stop_is_discarded():
    frames, c = decode_events(synth_events(b""))
    assert frames == []
    assert c["starts"] == 1 and c["stops"] == 1 and c["frames"] == 0


def test_stop_without_open_frame_is_ignored():
    dec = I2cRefDecoder(CPU)
    dec.feed(0, 3)
    dec.feed(HALF_BIT, 1)   # SDA steigt bei SCL high ohne START: kein Frame
    assert dec.counters["stops"] == 0
    assert dec.pending() == 0


def test_repeated_start_emits_previous_frame_nostop():
    d1 = b"\x00\x20"
    d2 = b"\x00\xe3\x20\x10"
    ev1 = synth_events(d1, stop=False)
    ev2 = synth_events(d2, t0=len(ev1) * HALF_BIT, repstart=True)
    frames, c = decode_events(ev1 + ev2)
    assert [f["data"] for f in frames] == [d1, d2]
    assert frames[0]["flags"] == FLAG_NOSTOP
    assert frames[1]["flags"] == FLAG_REPSTART
    assert c["starts"] == 2
    assert c["err_nostop"] == 1
    assert c["err_bits"] == 0


def test_no_stop_frame_pending_until_flushed():
    frames, c = decode_events(synth_events(b"\x01\x02", stop=False))
    assert frames == []
    assert c["frames"] == 0 and c["stops"] == 0


def test_bit_count_not_multiple_of_9():
    bits = _byte_bits(0x5A) + [0] + [1, 0, 1]   # ein volles Byte mit ACK plus 3 Bit
    frames, c = decode_events(bits_to_events(bits))
    assert len(frames) == 1
    assert frames[0]["data"] == b"\x5a"
    assert frames[0]["ack"] == "+"
    assert frames[0]["flags"] & FLAG_BITERR
    assert c["err_bits"] == 1


def test_overlength_frame_is_truncated_to_64():
    data = bytes(range(65))
    frames, c = decode_events(synth_events(data))
    assert len(frames) == 1
    assert frames[0]["data"] == data[:64]
    assert len(frames[0]["ack"]) == 64
    assert frames[0]["flags"] & FLAG_OVERLEN
    assert c["err_overlen"] == 1


def test_exactly_64_bytes_is_not_overlength():
    data = bytes(64)
    frames, c = decode_events(synth_events(data))
    assert frames[0]["data"] == data
    assert frames[0]["flags"] == 0
    assert c["err_overlen"] == 0


def test_mark_overflow_emits_open_frame_and_resets_baseline():
    dec = I2cRefDecoder(CPU)
    ev = synth_events(b"\x00\xe3", stop=False)
    for cyc, lvl in ev:
        dec.feed(cyc, lvl)
    assert dec.pending() == 0
    dec.mark_overflow()
    frame = dec.pop()
    assert frame is not None
    assert frame["data"] == b"\x00"  # 9. Bit von e3 war tentativ, wird verworfen
    assert frame["flags"] == FLAG_RING_OVF | FLAG_NOSTOP
    assert dec.pop() is None
    assert dec.counters["err_ring"] == 1

    # Nach dem Overflow beginnt eine neue Baseline: die naechste Folge wird sauber dekodiert.
    ev2 = synth_events(b"\x10", t0=ev[-1][0] + HALF_BIT)
    for cyc, lvl in ev2:
        dec.feed(cyc, lvl)
    frame2 = dec.pop()
    assert frame2["data"] == b"\x10"
    assert frame2["flags"] == 0


def test_mark_overflow_without_open_frame_only_counts():
    dec = I2cRefDecoder(CPU)
    dec.mark_overflow()
    assert dec.counters["err_ring"] == 1
    assert dec.pending() == 0


def test_skew_both_lines_same_event_no_start():
    dec = I2cRefDecoder(CPU)
    dec.feed(0, 3)
    dec.feed(10, 0)   # SDA und SCL fallen gleichzeitig
    assert dec.counters["err_skew"] == 1
    assert dec.counters["starts"] == 0
    assert dec.pending() == 0


def test_skew_scl_rising_samples_new_sda():
    dec = I2cRefDecoder(CPU)
    for cyc, lvl in [(0, 3), (1, 2)]:      # Baseline, START
        dec.feed(cyc, lvl)
    dec.feed(2, 0)                          # SCL fallt, SDA low
    dec.feed(3, 3)                          # SDA und SCL steigen gleichzeitig: Abtastung mit SDA=1
    assert dec.counters["err_skew"] == 1
    assert dec.counters["stops"] == 0
    assert dec.counters["starts"] == 1


def test_timestamp_with_32bit_cycle_wrap():
    origin_cyc = 0xFFFFF000
    per_event = 10 * 1000 * CPU   # 10 ms je Event
    ev = synth_events(b"\x00", t0=origin_cyc, half_bit_cyc=per_event)
    assert ev[1][0] < origin_cyc, "der Zyklenzaehler muss ueberlaufen"
    dec = I2cRefDecoder(CPU, origin_cyc=origin_cyc, origin_ms=1000)
    for cyc, lvl in ev:
        dec.feed(cyc, lvl)
    frame = dec.pop()
    assert frame["t_ms"] == 1010   # START ist das zweite Event: 1000 ms + 10 ms


def test_timestamp_grows_over_many_events_across_wrap():
    origin_cyc = 0xFFFFFF00
    per_event = 1000 * CPU        # 1 ms je Event
    dec = I2cRefDecoder(CPU, origin_cyc=origin_cyc, origin_ms=0)
    ev1 = synth_events(b"\x01", t0=origin_cyc, half_bit_cyc=per_event)
    ev2 = synth_events(b"\x02", t0=ev1[-1][0] + per_event, half_bit_cyc=per_event, repstart=False)
    for cyc, lvl in ev1 + ev2:
        dec.feed(cyc, lvl)
    first = dec.pop()
    second = dec.pop()
    assert first["data"] == b"\x01" and second["data"] == b"\x02"
    # Event i liegt bei i ms (ein Event = 1 ms). START ist Event 1 (erster Frame) bzw.
    # Event len(ev1)+1 (zweiter Frame, Baseline-Event liegt bei len(ev1)).
    assert first["t_ms"] == 1
    assert second["t_ms"] == len(ev1) + 1


def test_negative_cycle_difference_counts_zero():
    dec = I2cRefDecoder(CPU, origin_cyc=0, origin_ms=0)
    dec.advance(5 * 1000 * CPU)   # 5 ms
    dec.advance(2 * 1000 * CPU)   # liegt davor: zaehlt 0
    dec.advance(6 * 1000 * CPU)   # 1 ms weiter
    assert dec._now_ms() == 6


def test_frame_to_row_fields():
    row = frame_to_row({"t_ms": 5, "data": b"\x01", "ack": "+", "flags": 0}, unix_ms=7)
    assert row == {"t": 5, "u": 7, "data": b"\x01", "ack": "+", "flags": 0}


def test_synth_ack_length_mismatch_raises():
    with pytest.raises(ValueError):
        synth_events(b"\x01\x02", ack=[True])


# ---------------------------------------------------------------- ChangeFilter

def _row(data: bytes, flags: int = 0) -> dict:
    return {"data": data, "flags": flags}


def test_change_filter_content_compare():
    f = ChangeFilter()
    a = b"\x00\x20\x10\x01\x02"
    assert f.pass_(_row(a)) is True      # neu
    assert f.pass_(_row(a)) is False     # gleich
    b = a[:-1] + b"\x03"
    assert f.pass_(_row(b)) is True      # gleicher Schluessel, anderer Inhalt


def test_change_filter_istwert_type_is_part_of_key():
    f = ChangeFilter()
    t1 = b"\x00\xe3\x20\x10\x01\x05"
    t2 = b"\x00\xe3\x20\x10\x02\x05"
    assert f.pass_(_row(t1)) is True
    assert f.pass_(_row(t2)) is True     # anderer Typ, eigener Schluessel
    assert f.pass_(_row(t1)) is False
    assert f.pass_(_row(b"\x00\xe3\x20\x10\x01\x06")) is True


def test_change_filter_flags_always_pass():
    f = ChangeFilter()
    a = b"\x00\x20\x10"
    assert f.pass_(_row(a)) is True
    assert f.pass_(_row(a, flags=FLAG_BITERR)) is True
    assert f.pass_(_row(a, flags=FLAG_BITERR)) is True


def test_change_filter_round_robin_eviction():
    f = ChangeFilter()
    keys = [b"\x00" + bytes([0x20, i, 0x00]) for i in range(65)]
    for k in keys:
        f.pass_(_row(k))
    assert f.pass_(_row(keys[1])) is False      # noch in der Tabelle
    assert f.pass_(_row(keys[0])) is True       # aeltester Eintrag wurde verdraengt


def test_change_filter_clear():
    f = ChangeFilter()
    a = b"\x00\x20\x10"
    f.pass_(_row(a))
    f.clear()
    assert f.pass_(_row(a)) is True
