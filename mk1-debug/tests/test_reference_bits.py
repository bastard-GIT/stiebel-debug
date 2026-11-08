"""Tests fuer ReferenceModel.bits() / set_bits() (ohne Qt).

Schreibtests arbeiten auf einer Kopie in tmp_path. Das Original
referenzen/elster_reference/ElsterTable.json wird nie beschrieben.
"""
import hashlib
import shutil
import sys
from pathlib import Path

GUI_DIR = Path(__file__).resolve().parents[1] / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

import pytest  # noqa: E402

from elster import DEFAULT_JSON  # noqa: E402
from reference_model import ReferenceError, ReferenceModel  # noqa: E402


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def original_unveraendert():
    before = _sha(DEFAULT_JSON)
    yield
    assert _sha(DEFAULT_JSON) == before, "Original ElsterTable.json wurde veraendert"


@pytest.fixture
def model(tmp_path):
    dst = tmp_path / "ElsterTable.json"
    shutil.copy(DEFAULT_JSON, dst)
    return ReferenceModel(str(dst))


def _free_index(model) -> int:
    used = model.indices()
    for i in range(0x0FFF, 0x0000, -1):
        if i not in used:
            return i
    raise AssertionError("kein freier Index")


# ---- bits() ----

def test_bits_0x0176_liefert_vorgabe(model):
    bits = model.bits(0x0176)
    assert [b["bit"] for b in bits] == [0, 6, 12, 14]
    assert bits[0]["name"] == "VERDICHTER"
    assert all(b["confirmed"] is True for b in bits)


def test_bits_unbekannter_index_liefert_leer(model):
    assert model.bits(_free_index(model)) == []


# ---- set_bits() ----

def test_set_bits_legt_neues_signal_an_und_setzt_dirty(model):
    idx = _free_index(model)
    before = len(model.data["signals"])
    assert model.dirty is False

    model.set_bits(idx, [{"bit": 3, "name": "TEST_A"}], owner=0x180)

    assert model.dirty is True
    assert len(model.data["signals"]) == before + 1
    sig = model.signal(idx)
    assert sig is not None
    assert sig["name"] == "UNK_0x%04X" % idx
    assert sig["type"] == "et_default"
    assert sig["owner"] == "0x180"
    assert sig["bits"] == [{"bit": 3, "name": "TEST_A"}]


def test_set_bits_ersetzt_bestehende_liste(model):
    idx = _free_index(model)
    model.set_bits(idx, [{"bit": 1, "name": "ALT"}])
    model.set_bits(idx, [{"bit": 2, "name": "NEU"}])
    assert model.signal(idx)["bits"] == [{"bit": 2, "name": "NEU"}]
    assert [b["name"] for b in model.bits(idx)] == ["NEU"]


def test_set_bits_sortiert_nach_bit(model):
    idx = _free_index(model)
    model.set_bits(idx, [
        {"bit": 9, "name": "C"},
        {"bit": 0, "name": "A", "confirmed": True, "comment": "x"},
        {"bit": 4, "name": "B"},
    ])
    assert [b["bit"] for b in model.signal(idx)["bits"]] == [0, 4, 9]


def test_set_bits_kopiert_nur_gesetzte_schluessel(model):
    idx = _free_index(model)
    model.set_bits(idx, [{"bit": 5, "name": "K", "comment": "c"}])
    assert model.signal(idx)["bits"] == [{"bit": 5, "name": "K", "comment": "c"}]


def test_set_bits_leerliste_entfernt_feld(model):
    idx = _free_index(model)
    model.set_bits(idx, [{"bit": 1, "name": "X"}])
    assert "bits" in model.signal(idx)
    model.set_bits(idx, [])
    assert "bits" not in model.signal(idx)
    assert model.bits(idx) == []


@pytest.mark.parametrize("bit", [16, -1, "3", 1.0, True])
def test_set_bits_ungueltiges_bit_wirft_fehler(model, bit):
    idx = _free_index(model)
    with pytest.raises(ReferenceError):
        model.set_bits(idx, [{"bit": bit, "name": "X"}])
    assert model.dirty is False
    assert model.signal(idx) is None


def test_set_bits_leerer_name_wirft_fehler(model):
    idx = _free_index(model)
    with pytest.raises(ReferenceError):
        model.set_bits(idx, [{"bit": 1, "name": "   "}])
    assert model.dirty is False
    assert model.signal(idx) is None


def test_set_bits_doppeltes_bit_wirft_fehler(model):
    idx = _free_index(model)
    with pytest.raises(ReferenceError):
        model.set_bits(idx, [{"bit": 2, "name": "A"}, {"bit": 2, "name": "B"}])
    assert model.dirty is False
    assert model.signal(idx) is None


def test_set_bits_doppelter_name_wirft_fehler(model):
    idx = _free_index(model)
    with pytest.raises(ReferenceError):
        model.set_bits(idx, [{"bit": 1, "name": "A"}, {"bit": 2, "name": "A"}])
    assert model.dirty is False
    assert model.signal(idx) is None


def test_bits_nach_set_bits_liefert_gesetzte_liste(model):
    idx = _free_index(model)
    model.set_bits(idx, [{"bit": 15, "name": "TOP"}, {"bit": 0, "name": "LOW", "confirmed": False}])
    assert model.bits(idx) == [
        {"bit": 0, "name": "LOW", "confirmed": False},
        {"bit": 15, "name": "TOP"},
    ]
