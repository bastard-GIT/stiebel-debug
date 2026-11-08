"""Smoke: Bits-Reiter der Auswerte-GUI (offscreen)."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
GUI_DIR = Path(__file__).resolve().parents[1] / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

import json  # noqa: E402

from PySide6.QtWidgets import QApplication, QTableWidget  # noqa: E402

from data_model import Cell, Measurements  # noqa: E402
from reference_model import ReferenceModel  # noqa: E402
from tab_analyse import AnalysePanel  # noqa: E402

_app = QApplication.instance() or QApplication([])


def test_bits_tab_zeigt_gesetzte_bits():
    m = Measurements()
    m._put(0x176, 0x180, Cell(status="ok", raws=[0x0000, 0x0041], prio=2))
    panel = AnalysePanel(m, ReferenceModel(), {})
    panel.set_index(0x176)
    tables = [t for t in panel.findChildren(QTableWidget) if t.rowCount() == 16]
    assert tables
    t = tables[0]
    assert t.item(0, 1).text() == "VERDICHTER"
    assert t.item(0, 3).text() == "1" and t.item(14, 3).text() == "0"
    assert t.item(0, t.columnCount() - 1).text() == "ja"   # Bit 0 wechselt 0 -> 1
    assert t.item(14, t.columnCount() - 1).text() == "nein"
