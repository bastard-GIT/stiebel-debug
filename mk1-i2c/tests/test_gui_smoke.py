import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

GUI_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "gui"))
if GUI_DIR not in sys.path:
    sys.path.insert(0, GUI_DIR)

import pytest  # noqa: E402

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

import i2c_gui  # noqa: E402
from data_model import DataModel  # noqa: E402
from lwz180 import decode  # noqa: E402
from mqtt_bridge import MqttBridge  # noqa: E402

LOG = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "referenz", "beispiel_log.jsonl"))


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_window_loads_offline_log(app):
    bridge = MqttBridge()
    win = i2c_gui.I2cMainWindow(bridge=bridge)
    try:
        assert win._connected is False          # kein Autoconnect
        assert win.model.frames == []
        count = win.load_offline(LOG)
        assert count >= 20
        model = win.model
        assert len(model.frames) == count
        assert len(model.latest) >= 5           # mehrere Istwert-Typen
        assert 0x06 in model.latest
        assert model.latest[0x06]["value"] == -2.1      # letzter Wert Aussentemperatur im Beispiel-Log
        assert len(model.history(0x06)) >= 3
        assert model.crc_bad >= 1
        assert model.display_current is not None
        assert len(model.display_current) == 28
        assert model.key_log
        for tab in (win.live, win.werte, win.display, win.status):
            tab.refresh()
            tab.on_frames([])
        win.refresh_all()
        assert win.live._table.rowCount() > 0
        assert win.werte._table.rowCount() == len(model.latest)
        assert win.display._keys.rowCount() == len(model.key_log)
    finally:
        win.close()


def test_window_model_without_decoder_accepts_rows(app):
    bridge = MqttBridge()
    win = i2c_gui.I2cMainWindow(bridge=bridge, model=DataModel(decode=decode))
    try:
        rows = [{"t": 1, "u": 0, "data": bytes.fromhex("00e3201006ffe74f"), "ack": "++++++++", "flags": 0}]
        win._on_rx_frames(rows)
        win._flush()
        assert len(win.model.frames) == 1
        assert win.model.latest[0x06]["value"] == -2.5
        win.refresh_all()
    finally:
        win.close()


def test_import_saleae_compare_on_offline_log(app, tmp_path):
    win = i2c_gui.I2cMainWindow(bridge=MqttBridge())
    try:
        win.load_offline(LOG)
        ref = tmp_path / "saleae.csv"
        ref.write_text(
            "name,type,start_time,duration,address,data,read,ack\n"
            "I2C,start,0.0,0.0,,,,\n"
            "I2C,address,0.001,0.001,0x00,,write,ack\n"
            "I2C,data,0.002,0.001,,0xe3,,ack\n"
            "I2C,stop,0.003,0.0,,,,\n",
            encoding="utf-8")
        result = win.import_saleae(str(ref))
        assert result["matched"] == 0
        assert len(result["missing"]) == 1
        assert result["match_rate"] == 0.0
    finally:
        win.close()



def test_status_tab_scan_result(app):
    bridge = MqttBridge()
    win = i2c_gui.I2cMainWindow(bridge=bridge)
    try:
        bridge.scan_result.emit({"found": [42, 80], "nack": 110, "arb_lost": 1, "retries": 1, "ms": 350, "ok": True})
        text = win.status._lbl_scan.text()
        assert "0x2a" in text and "0x50" in text and "350 ms" in text
        bridge.scan_result.emit({"found": [], "nack": 0, "arb_lost": 0, "retries": 0, "ms": 0, "ok": False,
                                 "err": "Scan laeuft schon"})
        assert "Fehler: Scan laeuft schon" in win.status._lbl_scan.text()
    finally:
        win.close()
