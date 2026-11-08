import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

GUI_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "gui"))
if GUI_DIR not in sys.path:
    sys.path.insert(0, GUI_DIR)

import pytest  # noqa: E402

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

import lwz180  # noqa: E402
from data_model import DataModel  # noqa: E402
from logic_import import load_logic  # noqa: E402
from tab_bedienteil import BedienteilTab  # noqa: E402

DIGITS_LOG = Path(os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "Salae-logic-data", "digital.csv")))

SEG_Z1_STELLE2 = 7    # Zeile 1 (gross) Stelle 2 (Element e)
SEG_LEVEL = 8         # Zeile 1 (gross) Stelle 3, Lueftungsstufe (Element f)
SEG_UNMAPPED = 17     # kein Element: Byte 17 Bit 0 ist "unbekannt"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _display_row(ts: float, digit: int, level: int) -> dict:
    """Display-Zeile mit Payload: seg[i] = payload25[i - 2], seg = data[1:]."""
    payload = bytearray(25)
    payload[SEG_Z1_STELLE2 - 2] = digit
    payload[SEG_LEVEL - 2] = level
    payload[SEG_UNMAPPED - 2] = 0x01
    data = lwz180.build_display(bytes(payload))
    return {"t": int(round(ts * 1000)), "u": 0, "data": data, "ack": "+" * len(data), "flags": 0, "ts": ts}


def _key_row(ts: float, code: int) -> dict:
    data = lwz180.build_taste(code)
    return {"t": int(round(ts * 1000)), "u": 0, "data": data, "ack": "+" * len(data), "flags": 0, "ts": ts}


def _synthetic_model() -> DataModel:
    """Display 100.0 (5), 100.2 (2), Taste 6 bei 100.3, Display 100.4 (0), 101.0 (0 mit Stufe 3)."""
    dm = DataModel(decode=lwz180.decode)
    dm.add_frames([
        _display_row(100.0, 0x6D, 0x5B),
        _display_row(100.2, 0x5B, 0x5B),
        _key_row(100.3, 6),
        _display_row(100.4, 0x3F, 0x5B),
        _display_row(101.0, 0x3F, 0x4F),
    ])
    return dm


def _spy_keys(tab: BedienteilTab) -> list:
    calls: list = []
    orig = tab.widget.set_keys

    def spy(codes):
        calls.append(list(codes))
        orig(codes)

    tab.widget.set_keys = spy
    return calls


def test_zeitleiste_und_positionen(app):
    dm = _synthetic_model()
    tab = BedienteilTab(dm)
    try:
        assert tab.slider.maximum() + 1 == 4
        assert tab.slider.isEnabled() and tab.play_btn.isEnabled()
        tab.set_position(0)
        assert tab.widget._state["digits"]["e"]["char"] == "5"
        assert tab.widget._state["digits"]["e"]["raw"] == 0x6D
        assert tab.current_ts() == 100.0
        assert tab.time_label.text() != ""
        assert tab.slider.value() == 0
        tab.set_position(1)
        assert tab.widget._state["digits"]["e"]["char"] == "2"
        assert tab.current_ts() == 100.2
        assert "17/0" in tab.unknown_list.item(0).text()
        tab.set_position(3)
        assert tab.widget._state["digits"]["e"]["char"] == "0"
        assert tab.current_ts() == 101.0
        tab.set_position(99)                       # Grenzen werden eingehalten
        assert tab.slider.value() == 3
    finally:
        tab.close()


def test_overlay_taste_im_fenster(app):
    dm = _synthetic_model()
    tab = BedienteilTab(dm)
    try:
        calls = _spy_keys(tab)
        tab.set_position(2)                        # Paket 100.4, Taste 100.3 im Fenster [99.9, 100.4]
        assert calls[-1] == [6]
        tab.set_position(1)                        # Paket 100.2, Fenster [99.7, 100.2]: keine Taste
        assert calls[-1] == []
        tab.set_position(3)                        # Paket 101.0, Fenster [100.5, 101.0]: keine Taste
        assert calls[-1] == []
    finally:
        tab.close()


def test_playback_20x_erreicht_ende_und_pausiert(app):
    dm = _synthetic_model()
    tab = BedienteilTab(dm)
    clock = [100.0]
    tab._now = lambda: clock[0]                    # simulierte Zeit
    try:
        tab.live.setChecked(False)
        tab.set_position(0)
        tab.speed.setCurrentText("20x")
        tab.play_btn.click()
        assert tab.play_btn.text() == "Pause"
        steps = 0
        while tab.play_btn.text() == "Pause" and steps < 200:
            clock[0] += 0.05
            tab.step()
            steps += 1
        assert steps < 200                         # Ende erreicht, nicht Timeout
        assert tab.play_btn.text() == "Play"
        assert tab.slider.value() == 3
        assert tab.current_ts() == 101.0
        tab.step()                                 # ohne Wiedergabe kein Effekt
        assert tab.slider.value() == 3
    finally:
        tab.close()


def test_playback_1x_rueckt_nach_ts_differenz_vor(app):
    dm = _synthetic_model()
    tab = BedienteilTab(dm)
    clock = [100.0]
    tab._now = lambda: clock[0]
    try:
        tab.live.setChecked(False)
        tab.set_position(0)
        tab.play_btn.click()
        tab.step()                                 # erster Schritt setzt nur die Wandzeit
        assert tab.current_ts() == 100.0
        clock[0] += 0.25                           # 0.25 s bei 1x: Paket 100.2 erreicht, 100.4 noch nicht
        tab.step()
        assert tab.current_ts() == 100.2
        tab.play_btn.click()                       # Pause
        assert tab.play_btn.text() == "Play"
        clock[0] += 5.0
        tab.step()
        assert tab.current_ts() == 100.2           # pausiert: keine Bewegung
    finally:
        tab.close()


def test_live_folgt_neuestem_paket_und_slider_schaltet_live_aus(app):
    dm = _synthetic_model()
    tab = BedienteilTab(dm)
    try:
        assert tab.live.isChecked()
        assert tab.slider.value() == 3 and tab.current_ts() == 101.0
        new = _display_row(102.0, 0x5B, 0x5B)
        dm.add_frames([new])
        tab.on_frames([new])
        assert tab.slider.value() == 4 and tab.current_ts() == 102.0
        tab.slider.setValue(0)                     # Nutzer bewegt den Slider
        assert not tab.live.isChecked()
        assert tab.current_ts() == 100.0
        later = _display_row(103.0, 0x5B, 0x5B)
        dm.add_frames([later])
        tab.on_frames([later])
        assert tab.slider.value() == 0 and tab.current_ts() == 100.0
    finally:
        tab.close()


def test_leeres_modell_crasht_nicht(app):
    tab = BedienteilTab(DataModel(decode=lwz180.decode))
    try:
        assert not tab.slider.isEnabled()
        assert not tab.play_btn.isEnabled()
        assert tab.current_ts() is None
        assert tab.widget._state is None
        assert tab.time_label.text() != ""
        tab.refresh()
        tab.on_frames([])
        tab.set_position(0)
        tab.step()
        tab.play_btn.click()                       # deaktiviert, kein Effekt
        assert tab.play_btn.text() == "Play"
    finally:
        tab.close()


@pytest.mark.skipif(not DIGITS_LOG.exists(), reason="Mitschnitt Salae-logic-data/digital.csv fehlt")
def test_mitschnitt_display_pakete(app):
    rows, _info = load_logic(str(DIGITS_LOG))
    dm = DataModel(decode=lwz180.decode)
    dm.add_frames(rows)
    assert len(dm.display_history) == 455
    tab = BedienteilTab(dm)
    try:
        assert tab.slider.maximum() + 1 == 455
        assert tab.widget._state is not None
        tab.set_position(0)                             # erstes Paket: Standby, Stufe 1 mit Lueftersymbol
        assert tab.widget._state["rows"] == {1: "  1", 2: "    "}
        assert tab.widget._state["level"] == 1
        assert tab.widget._state["icons"]["v"] is True
        assert tab.unknown_list.count() == 0
    finally:
        tab.close()


def test_hauptfenster_hat_bedienteil_tab(app):
    import i2c_gui
    from mqtt_bridge import MqttBridge

    win = i2c_gui.I2cMainWindow(bridge=MqttBridge())
    try:
        titles = [win._tabs.tabText(i) for i in range(win._tabs.count())]
        assert "Bedienteil" in titles
        assert titles.index("Bedienteil") == titles.index("Display") + 1
        assert win.bedienteil in win._tab_list()
    finally:
        win.close()
