"""Regressionstest mit echtem Saleae-Mitschnitt der LWZ 180 (Salae-logic-data/, 2026-10-25, 4 MS/s, 1168 s).

Raw-Export digital.csv (SCL = Channel 4, SDA = Spalte "VDD", 6056 Frames, Menue P1..P85 und I1..I79
durchgeblaettert) laeuft durch den Referenz-Decoder. i2c.txt gehoert zum frueheren kurzen Mitschnitt und
wird hier nicht mehr verglichen. Ohne Datei: skip. Der Mitschnitt wird einmal pro Modul geladen.
"""
import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUI_DIR = ROOT / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

import display_model  # noqa: E402
import i2c_ref_decoder as rd  # noqa: E402
import lwz180  # noqa: E402

DATA = ROOT / "Salae-logic-data"
RAW = DATA / "digital.csv"

pytestmark = pytest.mark.skipif(not RAW.exists(), reason="Mitschnitt fehlt")


@pytest.fixture(scope="module")
def decoded():
    with RAW.open(newline="") as fh:
        rows = list(csv.reader(fh))
    header = rows[0]
    i_sda, i_scl = header.index("VDD"), header.index("Channel 4")
    events = [(int(round(float(r[0]) * 240e6)) & 0xFFFFFFFF, int(r[i_sda]) | (int(r[i_scl]) << 1))
              for r in rows[1:]]
    frames, counters = rd.decode_events(events, 240)
    results = [lwz180.decode(bytes(f["data"])) for f in frames]
    return frames, counters, results


@pytest.fixture(scope="module")
def display_states(decoded):
    """Eindeutige, vollstaendige Display-Pakete (28 Byte) in Reihenfolge des Auftretens."""
    _, _, results = decoded
    seen, out = set(), []
    for r in results:
        if r["kind"] == "display" and r["segments"] is not None and r["segments"] not in seen:
            seen.add(r["segments"])
            out.append(r["segments"])
    return out


def test_raw_decodes_without_bit_errors(decoded):
    frames, counters, _ = decoded
    assert len(frames) == 6056
    assert counters["err_bits"] == 0
    assert counters["err_ring"] == 0


def test_crc_ok_for_all_complete_lwz_packets(decoded):
    frames, _, results = decoded
    complete = {"istwert": 0, "display": 0}
    truncated = 0
    for f, r in zip(frames, results):
        if r["kind"] not in complete:
            continue
        if r["crc_ok"] is None:          # abgeschnittenes Paket (err_nostop), nicht auswertbar
            truncated += 1
            continue
        assert r["crc_ok"] is True, bytes(f["data"]).hex(" ")
        complete[r["kind"]] += 1
    assert complete == {"istwert": 5328, "display": 455}
    assert truncated == 15


def _find(states, **want):
    """Erstes Paket, dessen dekodierte Zeilen/Icons zu want passen (rows1, rows2, icons)."""
    layout = display_model.load_layout()
    for seg in states:
        res = display_model.decode_display(seg, layout)
        on = {k for k, v in res["icons"].items() if v}
        if res["rows"][1] == want["rows1"] and res["rows"][2] == want["rows2"] and on == want["icons"]:
            return res
    return None


def test_display_p1_26_grad(display_states):
    res = _find(display_states, rows1="P 1", rows2=" 260", icons={"degc", "dp2"})
    assert res is not None
    assert res["unmapped_bits"] == []


def test_display_p81_uhr_doppelpunkt(display_states):
    res = _find(display_states, rows1="P81", rows2="1628", icons={"b", "colon"})
    assert res is not None
    assert res["unmapped_bits"] == []


def test_display_p3_bypass(display_states):
    assert _find(display_states, rows1="P 3", rows2="   3", icons={"z"}) is not None


def test_display_p4_filter(display_states):
    assert _find(display_states, rows1="P 4", rows2="   0", icons={"ab"}) is not None


def test_display_standby_stufe_1_luefter(display_states):
    res = _find(display_states, rows1="  1", rows2="    ", icons={"v"})
    assert res is not None
    assert res["level"] == 1


def test_display_no_unmapped_bits_for_confirmed_icons(display_states):
    """Seg[2..5] konstant, 13/x und 14/x bekannt unklar: sonst bleibt nur Unbekanntes uebrig."""
    layout = display_model.load_layout()
    for seg in display_states:
        res = display_model.decode_display(seg, layout)
        assert res["ok"] is True
        assert bytes(seg[2:6]) == bytes.fromhex("0b206d00")
        for byte, bit in res["unmapped_bits"]:
            assert byte not in (13, 14), (byte, bit)
