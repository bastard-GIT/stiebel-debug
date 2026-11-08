"""Bedienteil-Tab: Nachbau des LWZ-180-Displays (DisplayWidget) mit Zeitleiste ueber alle Display-Pakete.

Oben das Widget, darunter Wiedergabe (Play/Pause, Slider, Tempo 1x/5x/20x, Live), Zeitanzeige und die
Liste gesetzter Bits ohne Zuordnung. Tasten aus model.key_log leuchten im Fenster [ts - 0,5 s, ts]
des angezeigten Pakets. Quelle der Positionen: model.display_history (INTERFACES.md 6.4, 6.5).

Die Zeit fuer die Wiedergabe kommt aus self._now (standardmaessig time.monotonic), damit Tests
deterministisch sind. Die Wiedergabe laeuft ueber einen virtuellen Datenzeit-Zeiger (self._clock).
"""
from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

import display_model
from display_widget import DisplayWidget

PLAY_TICK_MS = 50
KEY_WINDOW_S = 0.5                       # Tastenpaket leuchtet im Fenster [ts - 0,5 s, ts]
SPEED_FACTORS = {"1x": 1.0, "5x": 5.0, "20x": 20.0}
UNKNOWN_LIST_MAX_H = 130                 # etwa 6 Zeilen


def _load_layout_or_empty() -> dict:
    try:
        return display_model.load_layout()
    except (OSError, ValueError):
        return {"elements": []}


class BedienteilTab(QWidget):
    """Nachbau des Bedienteils mit Zeitleiste ueber die Display-Pakete des Datenbestands."""

    def __init__(self, model, bridge=None, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self._layout = _load_layout_or_empty()
        self._now = time.monotonic        # Zeitquelle, in Tests ersetzbar
        self._pos = 0                     # aktuelle Position in display_history
        self._playing = False
        self._clock = 0.0                 # virtuelle Datenzeit der Wiedergabe
        self._last_wall: float | None = None

        self.widget = DisplayWidget(self._layout)

        self.play_btn = QPushButton("Play")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(0)
        self.speed = QComboBox()
        self.speed.addItems(list(SPEED_FACTORS))
        self.live = QCheckBox("Live")
        self.live.setChecked(True)
        self.time_label = QLabel("")
        self.unknown_list = QListWidget()
        self.unknown_list.setMaximumHeight(UNKNOWN_LIST_MAX_H)

        self._timer = QTimer(self)
        self._timer.setInterval(PLAY_TICK_MS)
        self._timer.timeout.connect(self.step)

        self.play_btn.clicked.connect(self._toggle_play)
        self.slider.valueChanged.connect(self._on_slider_value)
        self.live.toggled.connect(self._on_live_toggled)

        controls = QHBoxLayout()
        controls.addWidget(self.play_btn)
        controls.addWidget(self.slider, 1)
        controls.addWidget(self.speed)
        controls.addWidget(self.live)
        controls.addWidget(self.time_label)

        layout = QVBoxLayout(self)
        layout.addWidget(self.widget, 1)
        layout.addLayout(controls)
        layout.addWidget(QLabel("Gesetzte Bits ohne Zuordnung (Byte/Bit):"))
        layout.addWidget(self.unknown_list)
        self.refresh()

    # ----- Oeffentliche Hooks -----

    def on_frames(self, rows: list[dict]) -> None:
        self.refresh()

    def refresh(self) -> None:
        """Zeitleiste an den Datenbestand anpassen. Live: auf das neueste Paket springen."""
        hist = self._history()
        n = len(hist)
        has_data = n > 0
        self.play_btn.setEnabled(has_data)
        self.slider.setEnabled(has_data)
        self.slider.blockSignals(True)
        self.slider.setMaximum(max(0, n - 1))
        self.slider.blockSignals(False)
        if not has_data:
            self._stop()
            self._pos = 0
            self.widget.set_state(None)
            self.widget.set_keys([])
            self.unknown_list.clear()
            self.time_label.setText("Keine Display-Pakete im Datenbestand (Mitschnitt laden)")
            return
        if self.live.isChecked():
            self._pos = n - 1
        else:
            self._pos = min(max(0, self._pos), n - 1)
        self._show(self._pos)

    def set_position(self, index: int) -> None:
        """Zeitleiste auf Eintrag index (0-basiert in display_history) setzen."""
        hist = self._history()
        if not hist:
            return
        index = min(max(0, int(index)), len(hist) - 1)
        self._show(index)
        self._clock = hist[index][0]

    def current_ts(self) -> float | None:
        """Zeitstempel des angezeigten Pakets, None ohne Display-Pakete."""
        hist = self._history()
        if not hist:
            return None
        return hist[min(max(0, self._pos), len(hist) - 1)][0]

    def step(self) -> None:
        """Ein Abspielschritt: Zeitzeiger um die Wandzeit mal Tempo vorruecken, Position anpassen, am Ende pausieren."""
        hist = self._history()
        n = len(hist)
        if not self._playing or n == 0:
            return
        now = self._now()
        if self._last_wall is None:
            self._last_wall = now
        dt = max(0.0, now - self._last_wall)
        self._last_wall = now
        self._clock += dt * SPEED_FACTORS.get(self.speed.currentText(), 1.0)
        pos = self._pos
        while pos < n - 1 and hist[pos + 1][0] <= self._clock:
            pos += 1
        if pos != self._pos:
            self._show(pos)
        if self._pos >= n - 1 and hist[n - 1][0] <= self._clock:
            self._stop()

    # ----- Intern -----

    def _history(self) -> list[tuple[float, bytes]]:
        return getattr(self._model, "display_history", None) or []

    def _show(self, index: int) -> None:
        """Position setzen und Widget, Slider, Zeit, Bitliste und Tasten aktualisieren (ohne Wiedergabezeit)."""
        hist = self._history()
        if not hist:
            return
        index = min(max(0, index), len(hist) - 1)
        self._pos = index
        ts, seg = hist[index]

        self.slider.blockSignals(True)
        self.slider.setValue(index)
        self.slider.blockSignals(False)

        state = display_model.decode_display(seg, self._layout)
        self.widget.set_state(state)
        self.widget.set_keys(self._keys_at(ts))

        self.unknown_list.clear()
        for byte, bit in state["unmapped_bits"]:
            self.unknown_list.addItem(f"{byte}/{bit}  (Byte-Wert 0x{seg[byte]:02x})")

        t0 = hist[0][0]
        zeit = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        self.time_label.setText(f"{index + 1}/{len(hist)}   {zeit}   +{ts - t0:.3f} s")

    def _keys_at(self, ts: float) -> list[int]:
        """Tastencodes aus key_log, deren Zeitstempel im Fenster [ts - 0,5 s, ts] liegt."""
        if not self._history():
            return []
        codes: list[int] = []
        for kts, kcodes in getattr(self._model, "key_log", []) or []:
            if ts - KEY_WINDOW_S <= kts <= ts:
                codes.extend(kcodes)
        return codes

    def _toggle_play(self) -> None:
        if self._playing:
            self._stop()
        else:
            self._play()

    def _play(self) -> None:
        hist = self._history()
        if not hist:
            return
        if self._pos >= len(hist) - 1:
            self._show(0)
        self.live.setChecked(False)
        self._clock = hist[self._pos][0]
        self._last_wall = None
        self._playing = True
        self.play_btn.setText("Pause")
        self._timer.start()

    def _stop(self) -> None:
        self._playing = False
        self._last_wall = None
        self.play_btn.setText("Play")
        self._timer.stop()

    def _on_slider_value(self, value: int) -> None:
        """Nur vom Nutzer ausgeloest (Ziehen, Klick, Tastatur): Live aus, Position setzen."""
        if self.live.isChecked():
            self.live.setChecked(False)
        self.set_position(value)

    def _on_live_toggled(self, checked: bool) -> None:
        if not checked:
            return
        self._stop()
        if self._history():
            self.set_position(len(self._history()) - 1)
