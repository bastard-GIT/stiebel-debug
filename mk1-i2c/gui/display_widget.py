"""Bedienteil-Nachbau des LWZ-180-Displays als QWidget (PySide6, ohne Netzwerk).

Zeichnet das LCD nach referenz/lwz180_display_layout.json: 7-Segment-Ziffern, Icons,
LEDs und den Tastenstreifen darunter. Zustand kommt aus display_model.decode_display,
leuchtende Tasten aus set_keys. Optional wird das Foto statt des gezeichneten
Hintergrunds gezeigt. Siehe INTERFACES.md 6.3 und Zusatz 6.1.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

import display_model

DEFAULT_PHOTO_SIZE = (1772, 708)
SEG_ORDER = "abcdefg"
BOX_DIGITS = {"i": "2", "k": "3", "l": "4", "m": "5", "n": "6", "o": "7"}

COLOR_LCD = QColor("#b9d4ff")
COLOR_FRAME = QColor("#3a3d42")
COLOR_INK = QColor("#1a1f6b")
COLOR_OFF = QColor(0x1A, 0x1F, 0x6B, 64)          # aus: ca. 25 % Alpha
COLOR_LED_ON = QColor("#ff9f1a")
COLOR_KEY = QColor("#8a8f98")
COLOR_KEY_LIT = QColor("#ffd54a")
COLOR_KEY_TEXT = QColor("#ffffff")
COLOR_KEY_TEXT_LIT = QColor("#1a1f6b")


def _num(value: object, default: float = 0.0) -> float:
    """Zahl aus dem Layout, sonst default. Nie Exception."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _codes(element: dict) -> set[int]:
    """Alle Tastencodes eines Button-Elements (Liste codes und Einzelwert code)."""
    result: set[int] = set()
    codes = element.get("codes")
    if isinstance(codes, list):
        for code in codes:
            value = _int_or_none(code)
            if value is not None:
                result.add(value)
    single = _int_or_none(element.get("code"))
    if single is not None:
        result.add(single)
    return result


def _segments_of(state: dict | None, eid: str) -> str:
    """Gesetzte Segmente a..g einer Ziffer aus dem Zustand, sonst leer (alles aus)."""
    if not isinstance(state, dict):
        return ""
    digits = state.get("digits")
    if not isinstance(digits, dict):
        return ""
    info = digits.get(eid)
    if not isinstance(info, dict):
        return ""
    segments = info.get("segments")
    return segments if isinstance(segments, str) else ""


def _icon_on(state: dict | None, eid: str) -> bool:
    """Zustand eines Icons oder einer LED aus dem Zustand, sonst aus."""
    if not isinstance(state, dict):
        return False
    icons = state.get("icons")
    if not isinstance(icons, dict):
        return False
    return bool(icons.get(eid, False))


def _poly(points: list[tuple[float, float]], ox: float, oy: float) -> QPolygonF:
    return QPolygonF([QPointF(ox + x, oy + y) for x, y in points])


def _hbar(x1: float, x2: float, y: float, th: float, ox: float, oy: float) -> QPolygonF:
    """Horizontales Segment mit spitzen Enden."""
    b = th / 2.0
    pts = [(x1, y + b), (x1 + b, y), (x2 - b, y), (x2, y + b), (x2 - b, y + th), (x1 + b, y + th)]
    return _poly(pts, ox, oy)


def _vbar(x: float, y1: float, y2: float, th: float, ox: float, oy: float) -> QPolygonF:
    """Vertikales Segment mit spitzen Enden."""
    b = th / 2.0
    pts = [(x + b, y1), (x + th, y1 + b), (x + th, y2 - b), (x + b, y2), (x, y2 - b), (x, y1 + b)]
    return _poly(pts, ox, oy)


def _draw_digit(p: QPainter, r: QRectF, segments: str) -> None:
    """7-Segment-Ziffer als Polygone. Gesetzte Segmente dunkel, sonst schwach."""
    width, height = r.width(), r.height()
    if width <= 0 or height <= 0:
        return
    th = max(2.0, min(width * 0.16, height * 0.09))
    gap = th * 0.2
    mid = (height - th) / 2.0
    ox, oy = r.x(), r.y()
    shapes = {
        "a": _hbar(gap, width - gap, 0.0, th, ox, oy),
        "b": _vbar(width - th, th + gap, mid - gap, th, ox, oy),
        "c": _vbar(width - th, mid + th + gap, height - th - gap, th, ox, oy),
        "d": _hbar(gap, width - gap, height - th, th, ox, oy),
        "e": _vbar(0.0, mid + th + gap, height - th - gap, th, ox, oy),
        "f": _vbar(0.0, th + gap, mid - gap, th, ox, oy),
        "g": _hbar(gap, width - gap, mid, th, ox, oy),
    }
    p.setPen(Qt.PenStyle.NoPen)
    for letter in SEG_ORDER:
        p.setBrush(COLOR_INK if letter in segments else COLOR_OFF)
        p.drawPolygon(shapes[letter])


def _text(p: QPainter, r: QRectF, text: str, color: QColor, px: float) -> None:
    font = QFont()
    font.setBold(True)
    font.setPixelSize(max(4, int(px)))
    p.setFont(font)
    p.setPen(QPen(color))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)


def _draw_icon(p: QPainter, eid: str, kind: str, r: QRectF, on: bool) -> None:
    """Vektor-Icon oder LED. an = dunkelblau gefuellt/Strich, aus = schwach."""
    width, height = r.width(), r.height()
    if width <= 0 or height <= 0:
        return
    ink = COLOR_INK if on else COLOR_OFF
    cx, cy = r.center().x(), r.center().y()
    short = min(width, height)
    pen = QPen(ink)
    pen.setWidthF(max(1.5, short * 0.09))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    def fill_or_stroke() -> None:
        """Bei an gefuellt ohne Strich, bei aus nur Strich."""
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink)
        else:
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)

    if kind == "led" or eid in ("pv", "pv2"):
        d = short * 0.6
        rect = QRectF(cx - d / 2, cy - d / 2, d, d)
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(COLOR_LED_ON)
        p.drawEllipse(rect)
        return

    if eid == "b":                                   # Uhr: Kreis und Zeiger
        d = short * 0.9
        p.drawEllipse(QRectF(cx - d / 2, cy - d / 2, d, d))
        p.drawLine(QPointF(cx, cy), QPointF(cx, cy - d * 0.3))
        p.drawLine(QPointF(cx, cy), QPointF(cx + d * 0.25, cy))
        return

    if eid == "a":                                   # Leistung: 4 Treppenbalken
        n = 4
        slot = width / n
        bar_w = slot * 0.78
        for i in range(n):
            bar_h = height * (i + 1) / n
            x = r.x() + i * slot + (slot - bar_w) / 2
            fill_or_stroke()
            p.drawRect(QRectF(x, r.y() + height - bar_h, bar_w, bar_h))
        return

    if eid in BOX_DIGITS:                            # Ziffernleiste: Kaestchen mit Ziffer
        rad = short * 0.2
        fill_or_stroke()
        p.drawRoundedRect(QRectF(r), rad, rad)
        _text(p, r, BOX_DIGITS[eid], COLOR_LCD if on else COLOR_OFF, height * 0.75)
        return

    if eid == "c":                                   # Schloss: Buegel und Koerper
        rad = short * 0.12
        body = QRectF(r.x() + width * 0.1, r.y() + height * 0.42, width * 0.8, height * 0.58)
        shackle = QRectF(r.x() + width * 0.25, r.y() + height * 0.02, width * 0.5, height * 0.8)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(shackle, 0, 180 * 16)
        fill_or_stroke()
        p.drawRoundedRect(body, rad, rad)
        return

    if eid == "v":                                   # Luefter: vier Fluegel
        radius = short * 0.45
        fill_or_stroke()
        for k in range(4):
            p.save()
            p.translate(cx, cy)
            p.rotate(90 * k + 20)
            p.drawEllipse(QRectF(radius * 0.05, -radius * 0.3, radius * 0.95, radius * 0.6))
            p.restore()
        return

    if eid == "colon":                               # Doppelpunkt: zwei Punkte oben/unten
        d = min(width, height * 0.3) * 0.9
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink)
        for yy in (r.y() + d / 2, r.bottom() - d / 2):
            p.drawEllipse(QRectF(cx - d / 2, yy - d / 2, d, d))
        return

    if eid == "degc":                                # Einheit Grad C
        _text(p, r, "°C", ink, height * 0.9)
        return

    if eid in ("g", "dp2"):                          # Dezimalpunkt
        d = short * 0.7
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(COLOR_OFF)
        p.drawEllipse(QRectF(cx - d / 2, cy - d / 2, d, d))
        return

    if eid in ("y", "x"):                            # Einheiten als Text
        text = "m3/h" if eid == "y" else "%"
        _text(p, r, text, ink, height * 0.85)
        return

    if eid == "ab":                                  # Filter: kariertes Quadrat (Rauten im Raster)
        side = short
        sq = QRectF(cx - side / 2, cy - side / 2, side, side)
        fill_or_stroke()
        p.drawRoundedRect(sq, side * 0.08, side * 0.08)
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(COLOR_LCD)
            n = 5
            step = side / n
            half = step * 0.3
            for i in range(n):
                for j in range(n):
                    px = sq.x() + (i + 0.5) * step
                    py = sq.y() + (j + 0.5) * step
                    if (i + j) % 2 == 0:
                        p.drawPolygon(QPolygonF([QPointF(px, py - half), QPointF(px + half, py),
                                                 QPointF(px, py + half), QPointF(px - half, py)]))
        return

    if eid == "aa":                                  # Frost: Schneeflocke aus 3 Linien
        radius = short * 0.45
        for k in range(3):
            ang = math.radians(60 * k)
            dx, dy = math.cos(ang) * radius, math.sin(ang) * radius
            p.drawLine(QPointF(cx - dx, cy - dy), QPointF(cx + dx, cy + dy))
        return

    if eid == "z":                                   # Bypass: zwei Pfeile nach oben, rechter mit Phi
        bw = max(1.2, short * 0.07)
        line = QPen(ink)
        line.setWidthF(bw)
        line.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(line)
        p.setBrush(Qt.BrushStyle.NoBrush)
        head = width * 0.1
        for ax in (r.x() + width * 0.12, r.x() + width * 0.68):
            p.drawLine(QPointF(ax, r.y() + head), QPointF(ax, r.bottom()))
            p.drawLine(QPointF(ax - head, r.y() + head * 2.2), QPointF(ax, r.y()))
            p.drawLine(QPointF(ax + head, r.y() + head * 2.2), QPointF(ax, r.y()))
        phi_w = width * 0.2
        p.drawEllipse(QRectF(r.x() + width * 0.68 - phi_w, cy - height * 0.17, phi_w * 2, height * 0.34))
        return

    # Unbekannte id: Rechteck mit Beschriftung der id
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(QRectF(r))
    _text(p, r, eid, ink, height * 0.5)


class DisplayWidget(QWidget):
    """LCD-Nachbau mit Ziffern, Icons, LEDs und Tastenstreifen."""

    def __init__(self, layout: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        if layout is None:
            try:
                layout = display_model.load_layout()
            except (OSError, ValueError):
                layout = {"elements": []}
        if not isinstance(layout, dict):
            layout = {"elements": []}
        self._layout = layout

        raw = layout.get("elements", [])
        self._elements = [el for el in raw if isinstance(el, dict)] if isinstance(raw, list) else []

        size = layout.get("photo_size")
        self._photo_w, self._photo_h = DEFAULT_PHOTO_SIZE
        if isinstance(size, list) and len(size) == 2:
            w, h = _num(size[0]), _num(size[1])
            if w > 0 and h > 0:
                self._photo_w, self._photo_h = int(w), int(h)

        # Tastenstreifen: Hoehe = Foto * max(1.0, max(y+h) + 0.02), siehe Zusatz 6.1
        max_bottom = max([_num(el.get("y")) + _num(el.get("h")) for el in self._elements], default=0.0)
        self._fac = max(1.0, max_bottom + 0.02)

        self._state: dict | None = None
        self._keys: set[int] = set()
        self._photo_on = False
        self._photo: QPixmap | None = None
        self._photo_tried = False

        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumWidth(240)

    # ----- Oeffentliche Schnittstelle (INTERFACES.md 6.3) -----

    def set_state(self, state: dict | None) -> None:
        """Rueckgabe von decode_display, None = alles aus."""
        self._state = state if isinstance(state, dict) else None
        self.update()

    def set_keys(self, codes) -> None:
        """Aktuell leuchtende Tastencodes."""
        self._keys = {c for c in (codes or ()) if _int_or_none(c) is not None}
        self.update()

    def set_photo_background(self, on: bool) -> None:
        """Foto statt gezeichnetem Hintergrund anzeigen."""
        self._photo_on = bool(on)
        self.update()

    def heightForWidth(self, width: int) -> int:
        return max(1, round(width * self._photo_h * self._fac / self._photo_w))

    def sizeHint(self) -> QSize:
        return QSize(900, self.heightForWidth(900))

    # ----- Intern -----

    def _photo_pixmap(self) -> QPixmap | None:
        """Foto einmal laden. Fehlt die Datei, bleibt es None (kein Fehler)."""
        if not self._photo_tried:
            self._photo_tried = True
            name = self._layout.get("photo")
            if isinstance(name, str) and name:
                path = display_model.LAYOUT_PATH.parent / name
                if path.is_file():
                    pix = QPixmap(str(path))
                    if not pix.isNull():
                        self._photo = pix
        return self._photo

    def _geometry(self) -> tuple[float, float, float, float]:
        """Zeichenflaeche: (x0, y0, dw, dh). dw = Breite des Fotos, dh = Fotohoehe."""
        w, h = float(self.width()), float(self.height())
        ratio = self._photo_h / self._photo_w * self._fac
        dw = min(w, h / ratio) if h > 0 else w
        dw = max(dw, 0.0)
        dh = dw * self._photo_h / self._photo_w
        x0 = (w - dw) / 2.0
        y0 = (h - dh * self._fac) / 2.0
        return x0, y0, dw, dh

    @staticmethod
    def _rect(el: dict, geo: tuple[float, float, float, float]) -> QRectF:
        x0, y0, dw, dh = geo
        return QRectF(x0 + _num(el.get("x")) * dw, y0 + _num(el.get("y")) * dh,
                      _num(el.get("w")) * dw, _num(el.get("h")) * dh)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        geo = self._geometry()
        x0, y0, dw, dh = geo

        p.fillRect(self.rect(), COLOR_FRAME)

        lcd = QRectF(x0, y0, dw, dh)
        photo = self._photo_pixmap() if self._photo_on else None
        if photo is not None and dw > 0:
            p.drawPixmap(lcd, photo, QRectF(photo.rect()))
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(COLOR_LCD)
            rad = dh * 0.03
            p.drawRoundedRect(lcd, rad, rad)

        buttons = []
        for el in self._elements:
            kind = el.get("kind")
            if kind == "button":
                buttons.append(el)
                continue
            eid = el.get("id")
            if not isinstance(eid, str):
                continue
            rect = self._rect(el, geo)
            if kind == "digit7":
                _draw_digit(p, rect, _segments_of(self._state, eid))
            elif kind in ("icon", "led"):
                _draw_icon(p, eid, kind, rect, _icon_on(self._state, eid))

        for el in buttons:
            rect = self._rect(el, geo)
            if rect.width() <= 0 or rect.height() <= 0:
                continue
            lit = bool(self._keys & _codes(el))
            rad = rect.height() * 0.2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(COLOR_KEY_LIT if lit else COLOR_KEY)
            p.drawRoundedRect(rect, rad, rad)
            label = el.get("name")
            if not isinstance(label, str):
                label = str(el.get("id", ""))
            _text(p, rect, label, COLOR_KEY_TEXT_LIT if lit else COLOR_KEY_TEXT,
                  rect.height() * 0.4)

        p.end()
