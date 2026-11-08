import os
import sys
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

GUI_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "gui"))
if GUI_DIR not in sys.path:
    sys.path.insert(0, GUI_DIR)

import pytest  # noqa: E402

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import tab_live  # noqa: E402
from elster import ElsterDb, default_reference_path  # noqa: E402

DB_JSON = str(default_reference_path())

TS = 1761038123.456
F8 = {"t": 1000, "id": 0x180, "data": bytes([0xD2, 0x00, 0xF8, 0x00, 0x03, 0x01, 0x00]), "tx": False}
F8_TS = dict(F8, ts=TS, ts_src="esp")
F8_PC = dict(F8, ts=TS, ts_src="pc")


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


def _clock(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]


def test_uhrzeit_column_follows_zeit():
    assert tab_live.COLUMNS[0] == "Zeit (ms)"
    assert tab_live.COLUMNS[1] == "Uhrzeit"


def test_time_without_ts_shows_t_and_no_clock(app, db):
    model = _model(db, [F8])
    assert _cell(model, 0, _col("Zeit (ms)")) == "1000"
    assert _cell(model, 0, _col("Uhrzeit")) == ""
    assert model.data(model.index(0, _col("Zeit (ms)")), Qt.ToolTipRole) is None


def test_time_with_ts_shows_unix_seconds_three_decimals(app, db):
    model = _model(db, [F8_TS])
    assert _cell(model, 0, _col("Zeit (ms)")) == "1761038123.456"
    assert _cell(model, 0, _col("Uhrzeit")) == _clock(TS)
    assert len(_clock(TS)) == 12  # HH:MM:SS.mmm
    assert model.data(model.index(0, _col("Uhrzeit")), Qt.ToolTipRole) is None


def test_ts_three_decimals_padded(app, db):
    model = _model(db, [dict(F8, ts=1761038123.5)])
    assert _cell(model, 0, _col("Zeit (ms)")) == "1761038123.500"


def test_pc_source_marks_clock_and_tooltip(app, db):
    model = _model(db, [F8_PC])
    idx = model.index(0, _col("Uhrzeit"))
    assert _cell(model, 0, _col("Uhrzeit")) == _clock(TS) + " ~"
    assert model.data(idx, Qt.ToolTipRole) == "PC-Naehrung"


def test_sort_key_numeric_by_ts(app, db):
    # Lexikografisch wuerde "1000.250" vor "999.500" stehen; numerisch nicht.
    a = dict(F8, t=10, ts=1000.25)
    b = dict(F8, t=5000, ts=999.5)
    model = _model(db, [a, b])
    assert model.data(model.index(0, 0), tab_live.SORT_ROLE) == 1000.25
    proxy = tab_live._FilterProxy()
    proxy.setSourceModel(model)
    proxy.sort(0, Qt.AscendingOrder)
    order = [model.record(proxy.mapToSource(proxy.index(r, 0)).row()).ts for r in range(2)]
    assert order == [999.5, 1000.25]


def test_sort_key_falls_back_to_t(app, db):
    model = _model(db, [dict(F8, t=2000), dict(F8, t=1500)])
    assert model.data(model.index(0, 0), tab_live.SORT_ROLE) == 2000.0
    proxy = tab_live._FilterProxy()
    proxy.setSourceModel(model)
    proxy.sort(1, Qt.AscendingOrder)
    order = [model.record(proxy.mapToSource(proxy.index(r, 0)).row()).t for r in range(2)]
    assert order == [1500, 2000]


def test_rows_roundtrip_keeps_ts_and_src(app, db):
    model = _model(db, [F8_TS, dict(F8, t=1040)])
    rows = model.rows()
    assert set(rows[0]) == {"t", "ts", "ts_src", "id", "data", "tx"}
    assert rows[0]["ts"] == TS and rows[0]["ts_src"] == "esp"
    assert rows[1]["ts"] is None and rows[1]["ts_src"] is None

    again = _model(db, rows)
    assert again.rows() == rows
    assert _cell(again, 0, _col("Zeit (ms)")) == "1761038123.456"
    assert _cell(again, 1, _col("Zeit (ms)")) == "1040"


def test_load_rows_accepts_legacy_without_ts(app, db):
    model = _model(db, [F8])
    rows = model.rows()
    assert rows[0]["ts"] is None
    legacy = [{"t": 1000, "id": 0x180, "data": F8["data"], "tx": False}]
    model.load_rows(legacy)
    assert _cell(model, 0, _col("Uhrzeit")) == ""
    assert model.rows()[0]["ts"] is None
