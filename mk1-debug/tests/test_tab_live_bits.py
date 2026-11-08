import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

GUI_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "gui"))
if GUI_DIR not in sys.path:
    sys.path.insert(0, GUI_DIR)

import pytest  # noqa: E402

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

import tab_live  # noqa: E402
from elster import ElsterDb, default_reference_path  # noqa: E402

DB_JSON = str(default_reference_path())

# Langformat Index 0x0176, Switch 0xFA (kein Switch-Label), Wert 0x0041
BIT_FA = {"t": 1000, "id": 0x180, "data": bytes([0xD2, 0x00, 0xFA, 0x01, 0x76, 0x00, 0x41]), "tx": False}
# Gleicher Index, Switch 0xF8 (Minimum) -> Zahl statt Bitfeld
BIT_F8 = {"t": 1010, "id": 0x180, "data": bytes([0xD2, 0x00, 0xF8, 0x01, 0x76, 0x00, 0x41]), "tx": False}
# Normaler Wert Index 0x0003, Switch 0xFA, Wert 0x0208 -> 52.0
NORMAL = {"t": 1020, "id": 0x180, "data": bytes([0xD2, 0x00, 0xFA, 0x00, 0x03, 0x02, 0x08]), "tx": False}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def db():
    return ElsterDb(DB_JSON)


def _model(db, rows):
    model = tab_live.FrameTableModel(db)
    model.load_rows(rows)
    return model


def _wert(model, row):
    return model.data(model.index(row, tab_live.COLUMNS.index("Wert")))


def test_bitfeld_bei_aktuellem_wert(app, db):
    model = _model(db, [BIT_FA])
    assert _wert(model, 0).startswith("0x0041: VERDICHTER")


def test_min_switch_bleibt_zahl(app, db):
    model = _model(db, [BIT_F8])
    assert _wert(model, 0) == "65"


def test_normaler_index_unveraendert(app, db):
    model = _model(db, [NORMAL])
    assert _wert(model, 0) == "52.0"
