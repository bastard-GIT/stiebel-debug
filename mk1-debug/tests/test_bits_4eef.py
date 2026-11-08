"""Tests fuer Bitfeld BETRIEBS_STATUS_WPLA (0x4eef) in gui/elster.py und der generierten Referenz.

Ohne Qt, ohne paho. Das Original referenzen/elster_reference/ElsterTable.json wird nie beschrieben.
"""
import json
import sys
from pathlib import Path

GUI_DIR = Path(__file__).resolve().parents[1] / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

from elster import BUILTIN_BITS, ElsterDb  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BITS_JSON = ROOT / "referenz" / "ElsterTable_bits.json"


def test_decode_0x0051_heizen():
    db = ElsterDb()
    assert db.decode(0x4EEF, 0x0051) == "0x0051: HEIZKREISPUMPE_1, WP_IM_HEIZBETRIEB, VERDICHTER_IN_BETRIEB"


def test_decode_0x8000_nicht_unterstuetzt():
    db = ElsterDb()
    assert db.decode(0x4EEF, 0x8000) == "-255"


def test_bits_for_4eef_hat_12_eintraege():
    db = ElsterDb()
    bits = db.bits_for(0x4EEF)
    assert bits is not None
    assert len(bits) == 12
    assert [b["bit"] for b in bits] == list(range(12))


def test_builtin_4eef_confirmed_nur_0_4_6():
    confirmed = {b["bit"] for b in BUILTIN_BITS[0x4EEF] if b["confirmed"]}
    assert confirmed == {0, 4, 6}


def test_builtin_0x0176_unveraendert():
    assert [b["bit"] for b in BUILTIN_BITS[0x0176]] == [0, 6, 12, 14]


def test_generierte_referenz_enthaelt_4eef():
    assert BITS_JSON.exists(), "referenz/ElsterTable_bits.json fehlt (tools/make_reference_bits.py ausfuehren)"
    table = json.loads(BITS_JSON.read_text(encoding="utf-8"))
    sigs = {int(str(s["index"]), 16): s for s in table["signals"]}

    wpla = sigs[0x4EEF]
    assert wpla["name"] == "BETRIEBS_STATUS_WPLA"
    assert wpla["type"] == "et_default"
    assert wpla["owner"] == "0x480"
    assert len(wpla["bits"]) == 12

    status = sigs[0x0176]
    assert len(status["bits"]) == 4
    assert [b["bit"] for b in status["bits"]] == [0, 6, 12, 14]

