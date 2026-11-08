import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

GUI_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "gui"))
if GUI_DIR not in sys.path:
    sys.path.insert(0, GUI_DIR)

import pytest  # noqa: E402

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

import display_model  # noqa: E402
from display_widget import DisplayWidget  # noqa: E402

# Mitschnitt-Paket f7 20 (28 Byte, ohne Adressbyte): P1, 26.0 Grad C
PAKET = bytes.fromhex(
    "f7 20 0b 20 6d 00 73 00 06 00 5b 7d 3f 15 55 00 00 00 00 00 00 00 05 00 00 00 00 6a"
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _state():
    return display_model.decode_display(PAKET, display_model.load_layout())


def test_paket_laenge():
    assert len(PAKET) == 28


def test_grab_und_png(app, tmp_path):
    w = DisplayWidget()
    try:
        w.resize(1000, 500)
        w.set_state(_state())
        w.set_keys([6])
        pix = w.grab()
        assert pix.width() > 0
        out = tmp_path / "widget.png"
        assert pix.save(str(out))
        assert out.stat().st_size > 0
    finally:
        w.close()


def test_photo_background_an_aus(app):
    w = DisplayWidget()
    try:
        w.resize(1000, 500)
        w.set_state(_state())
        w.set_photo_background(True)
        assert w.grab().width() > 0
        w.set_photo_background(False)
        assert w.grab().width() > 0
    finally:
        w.close()


def test_fehlendes_foto_kein_fehler(app):
    layout = dict(display_model.load_layout(), photo="gibt_es_nicht.jpg")
    w = DisplayWidget(layout=layout)
    try:
        w.resize(800, 400)
        w.set_state(_state())
        w.set_photo_background(True)
        assert w.grab().width() > 0
    finally:
        w.close()


def test_state_none_rendert(app):
    w = DisplayWidget()
    try:
        w.resize(800, 400)
        w.set_state(None)
        assert w.grab().width() > 0
    finally:
        w.close()


def test_zustand_und_none_unterschiedlich(app):
    w = DisplayWidget()
    try:
        w.resize(800, 400)
        w.set_state(_state())
        mit_zustand = w.grab().toImage()
        w.set_state(None)
        ohne_zustand = w.grab().toImage()
        assert mit_zustand != ohne_zustand
    finally:
        w.close()


def test_taste_leuchtet(app):
    w = DisplayWidget()
    try:
        w.resize(800, 400)
        w.set_state(_state())
        w.set_keys([])
        ohne_taste = w.grab().toImage()
        w.set_keys([6])
        mit_taste = w.grab().toImage()
        assert mit_taste != ohne_taste
    finally:
        w.close()


def test_height_for_width(app):
    w = DisplayWidget()
    try:
        h = w.heightForWidth(1000)
        assert h > 0
        assert h < 1000
    finally:
        w.close()
