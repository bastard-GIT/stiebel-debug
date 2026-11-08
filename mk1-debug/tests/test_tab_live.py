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

# Langlayout Switch 0xF8 (Minimum), Index 0x0003, Wert 0x0100; msg_type 2 (response)
F8 = {"t": 1000, "id": 0x180, "data": bytes([0xD2, 0x00, 0xF8, 0x00, 0x03, 0x01, 0x00]), "tx": False}
# Langlayout Switch 0xFA, gleicher Index wie F8
FA = {"t": 1010, "id": 0x180, "data": bytes([0xD2, 0x00, 0xFA, 0x00, 0x03, 0x01, 0x00]), "tx": False}
# Systemnachricht msg_type 6 und 7
SYS = {"t": 1020, "id": 0x180, "data": bytes([0x06, 0x01, 0xFE, 0x00, 0x00, 0x00, 0x00]), "tx": False}
SYS_RESP = {"t": 1030, "id": 0x180, "data": bytes([0x07, 0x01, 0xFE, 0x00, 0x00, 0x00, 0x00]), "tx": False}
# Kurzlayout: d2 = Index 0x01
SHORT = {"t": 1040, "id": 0x180, "data": bytes([0xD2, 0x00, 0x01, 0x00, 0x05, 0x00, 0x00]), "tx": False}


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


def _cell(model, row, col):
    return model.data(model.index(row, col))


def _col(name):
    return tab_live.COLUMNS.index(name)


def test_columns_have_switch_after_index():
    assert tab_live.COLUMNS[_col("Index") + 1] == "Switch"
    assert tab_live.COLUMNS[_col("Switch") + 1] == "Name"
    assert len(tab_live.COLUMNS) == 12


def test_switch_column_values(app, db):
    model = _model(db, [F8, FA, SHORT])
    assert _cell(model, 0, _col("Switch")) == "min"
    assert _cell(model, 1, _col("Switch")) == ""
    assert _cell(model, 2, _col("Switch")) == ""
    assert _cell(model, 0, _col("Index")) == "0x0003"
    assert _cell(model, 0, _col("Rohwert")) == "0x0100"
    assert _cell(model, 0, _col("Wert")) == "25.6"   # Index 0x0003 ist et_dec_val in ElsterTable.json


def test_system_frame_name_and_type(app, db):
    model = _model(db, [SYS, SYS_RESP])
    assert _cell(model, 0, _col("Nachrichtenart")) == "system"
    assert _cell(model, 1, _col("Nachrichtenart")) == "system-resp"
    assert _cell(model, 0, _col("Name")) == "SYSTEM_0xFE"
    assert _cell(model, 0, _col("Switch")) == ""


def test_text_filter_matches_switch(app, db):
    model = _model(db, [F8, FA])
    proxy = tab_live._FilterProxy()
    proxy.setSourceModel(model)
    proxy.set_filters([], None, "min", False)
    assert proxy.rowCount() == 1
    assert proxy.mapToSource(proxy.index(0, 0)).row() == 0


def test_latest_key_includes_switch(app, db):
    # Gleicher Sender und Index, aber verschiedener Switch: beide bleiben "letzter Wert".
    model = _model(db, [F8, FA])
    proxy = tab_live._FilterProxy()
    proxy.setSourceModel(model)
    proxy.set_filters([], None, "", True)
    assert proxy.rowCount() == 2


def test_latest_keeps_only_newest_per_key(app, db):
    newer_f8 = dict(F8, t=2000)
    model = _model(db, [F8, FA, newer_f8])
    proxy = tab_live._FilterProxy()
    proxy.setSourceModel(model)
    proxy.set_filters([], None, "", True)
    assert proxy.rowCount() == 2
    kept = [model.record(proxy.mapToSource(proxy.index(r, 0)).row()).t for r in range(2)]
    assert sorted(kept) == [1010, 2000]


def test_system_frames_not_in_latest(app, db):
    model = _model(db, [SYS, SYS_RESP])
    proxy = tab_live._FilterProxy()
    proxy.setSourceModel(model)
    proxy.set_filters([], None, "", True)
    assert proxy.rowCount() == 0
