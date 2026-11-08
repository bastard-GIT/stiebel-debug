"""Tests fuer Bitfelder in gui/elster.py (ohne Qt, ohne paho)."""
import json
import shutil
import sys
from pathlib import Path

GUI_DIR = Path(__file__).resolve().parents[1] / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

import pytest  # noqa: E402

from elster import (  # noqa: E402
    BUILTIN_BITS,
    DEFAULT_JSON,
    ElsterDb,
    decode_bits,
    set_bit_names,
)


def test_set_bit_names_defined_bits():
    bits = BUILTIN_BITS[0x0176]
    assert set_bit_names(0x0041, bits) == [(0, "VERDICHTER"), (6, "SPEICHERLADEPUMPE")]


def test_set_bit_names_undefined_bit_is_b_n():
    assert set_bit_names(0x0002, []) == [(1, "b1")]


def test_decode_bits_two_bits():
    assert decode_bits(0x0041, BUILTIN_BITS[0x0176]) == "0x0041: VERDICHTER, SPEICHERLADEPUMPE"


def test_decode_bits_zero():
    assert decode_bits(0x0000, BUILTIN_BITS[0x0176]) == "0x0000: -"


def test_decode_bits_not_supported():
    assert decode_bits(0x8000, BUILTIN_BITS[0x0176]) == "-255"


def test_decode_bits_warmwasser():
    assert "WARMWASSER" in decode_bits(0x4041, BUILTIN_BITS[0x0176])


def test_bits_for_builtin_and_none():
    db = ElsterDb()
    # Die Referenz hat eigene Bits fuer 0x0176 (mit Kommentaren); Bitnummern und Namen gleich der Vorgabe
    assert [(b["bit"], b["name"]) for b in db.bits_for(0x0176)] ==         [(b["bit"], b["name"]) for b in BUILTIN_BITS[0x0176]]
    assert db.bits_for(0x0003) is None


def test_decode_uses_bits_when_present():
    db = ElsterDb()
    assert db.decode(0x0176, 0x0041) == "0x0041: VERDICHTER, SPEICHERLADEPUMPE"


def test_decode_without_signal_or_bits_falls_back_to_et_default():
    db = ElsterDb()
    assert db.decode(0x0003, 0x0208) == "52.0"
    assert db.decode(0x9999, 5) == "5"


def test_bits_override_from_reference_json(tmp_path):
    src = Path(DEFAULT_JSON)
    kopie = tmp_path / "ElsterTable.json"
    shutil.copyfile(src, kopie)

    table = json.loads(kopie.read_text(encoding="utf-8"))
    hits = [s for s in table["signals"] if int(str(s["index"]), 16) == 0x0003]
    assert hits, "Signal 0x0003 fehlt in Referenz-JSON"
    for s in hits:
        s["bits"] = [{"bit": 1, "name": "X"}]
    kopie.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")

    db = ElsterDb(str(kopie))
    assert db.decode(0x0003, 2) == "0x0002: X"
