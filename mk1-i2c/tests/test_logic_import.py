"""Tests fuer gui/logic_import.py mit dem echten Saleae-Mitschnitt (Salae-logic-data/digital.csv, 6056 Frames,
SCL = Channel 4, SDA = Spalte VDD). Ohne Datei: skip. i2c.txt gehoert zum frueheren Mitschnitt und wird
nur noch fuer die Formaterkennung herangezogen. Der Mitschnitt wird einmal pro Modul geladen."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUI_DIR = ROOT / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

import logic_import  # noqa: E402

DATA = ROOT / "Salae-logic-data"
RAW = DATA / "digital.csv"
ANALYZER = DATA / "i2c.txt"

pytestmark = pytest.mark.skipif(not RAW.exists(), reason="Mitschnitt fehlt")


@pytest.fixture(scope="module")
def loaded():
    return logic_import.load_logic(RAW)


def test_detect_format():
    assert logic_import.detect_format(RAW) == "raw"


@pytest.mark.skipif(not ANALYZER.exists(), reason="i2c.txt fehlt")
def test_detect_format_analyzer():
    assert logic_import.detect_format(ANALYZER) == "analyzer"


def test_raw_autodetects_channels(loaded):
    rows, info = loaded
    assert info["scl"] == "Channel 4"
    assert info["sda"] == "VDD"
    assert info["frames"] == 6056
    assert len(rows) == 6056
    assert info["counters"]["err_bits"] == 0
    assert 13.0 < info["scl_khz"] < 14.0
    assert all(r["ts_src"] == "logic" for r in rows)
    assert rows[0]["data"][:3] == bytes.fromhex("00f720")


def test_raw_manual_channels_swapped_gives_bit_errors():
    _, info = logic_import.load_logic(RAW, sda="Channel 4", scl="VDD")
    assert info["counters"]["err_bits"] > 0


def test_unknown_format(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("foo\n1\n")
    with pytest.raises(ValueError):
        logic_import.load_logic(p)
