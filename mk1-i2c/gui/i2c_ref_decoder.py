"""Python-Referenzdecoder fuer Logic-Analyzer-Importe (Saleae), Nachbau des frueheren Firmware-Decoders (entfernt 2026-10-30; INTERFACES.md, Abschnitt 1.2).

Dient nur der Auswertung externer LA-Rohdaten (SDA/SCL-Flanken), nicht der Firmware.
Kein Qt, kein paho, kein Netzwerk.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from typing import Iterable

FLAG_NOSTOP = 0x01    # Frame durch (Repeated) START statt STOP beendet
FLAG_BITERR = 0x02    # Bitzahl nicht durch 9 teilbar
FLAG_OVERLEN = 0x04   # mehr als 64 Bytes, Rest verworfen
FLAG_RING_OVF = 0x08  # Ring-Ueberlauf waehrend des Frames
FLAG_REPSTART = 0x10  # Frame begann mit Repeated START

MAX_FRAME_BYTES = 64
QUEUE_MAX = 64
CHANGE_TABLE_MAX = 64
TYPE_HEADER = b"\xe3\x20\x10"  # Istwerte (LWZ 180)

_COUNTER_KEYS = ("events", "starts", "stops", "frames", "frames_dropped",
                 "err_nostop", "err_bits", "err_overlen", "err_ring", "err_skew")
_U32 = 0xFFFFFFFF
_HALF_RANGE = 0x80000000


class I2cRefDecoder:
    """Zustandsmaschine (frueher i2c_decoder.h). Ein Frame ist ein dict {"t_ms","data","ack","flags"}."""

    def __init__(self, cycles_per_us: int = 240, origin_cyc: int = 0, origin_ms: int = 0) -> None:
        if cycles_per_us <= 0:
            raise ValueError("cycles_per_us muss > 0 sein")
        self._cycles_per_us = int(cycles_per_us)
        self._origin_ms = int(origin_ms)
        self._last_cyc = int(origin_cyc) & _U32
        self._acc = 0  # aufsummierte Zyklen seit origin
        self.counters: dict[str, int] = {}
        self._queue: deque[dict] = deque()
        self.reset(keep_counters=False)

    # ------------------------------------------------------------------
    # Zeit
    # ------------------------------------------------------------------
    def _now_ms(self) -> int:
        return self._origin_ms + self._acc // (self._cycles_per_us * 1000)

    def advance(self, now_cyc: int) -> None:
        """Zeitbasis aktualisieren. Differenzen als uint32 gerechnet, negative Differenz zaehlt 0."""
        now = int(now_cyc) & _U32
        diff = (now - self._last_cyc) & _U32
        if diff >= _HALF_RANGE:
            return  # negativ: zaehlt 0, Basis bleibt auf dem neueren Wert
        self._acc += diff
        self._last_cyc = now

    # ------------------------------------------------------------------
    # Eingang
    # ------------------------------------------------------------------
    def feed(self, cyc: int, lvl: int) -> None:
        self.counters["events"] += 1
        self.advance(cyc)
        lvl &= 3
        prev = self._prev
        self._prev = lvl
        if prev is None:
            return  # erstes Event nach reset/Overflow: nur Baseline
        diff = lvl ^ prev
        dsda = diff & 1
        dscl = diff & 2
        scl_high = bool(lvl & 2)
        if dsda and dscl:
            self.counters["err_skew"] += 1
            if scl_high:
                self._take_sample(lvl & 1)
            else:
                self._commit()
            return
        if dscl:
            if scl_high:
                self._take_sample(lvl & 1)
            else:
                self._commit()
            return
        if dsda and scl_high:
            # SDA aendert sich bei SCL high: START/STOP. Ein offener Abtastwert ist der
            # Setup-Puls davor (SCL high vor Sr/STOP) und zaehlt nicht als Bit.
            self._pending = None
            if lvl & 1:
                self._stop()
            else:
                self._start()

    def mark_overflow(self) -> None:
        """Ring-Ueberlauf: offenen Frame mit Rest abschliessen, Zustand IDLE, Neu-Baseline."""
        self.counters["err_ring"] += 1
        self._pending = None  # tentatives Bit verwerfen (INTERFACES 1.2)
        if self._open and len(self._bytes) > 0:
            self._emit(self._flags | FLAG_RING_OVF | FLAG_NOSTOP)
        self._clear_frame()
        self._prev = None

    def pop(self) -> dict | None:
        if not self._queue:
            return None
        return self._queue.popleft()

    def pending(self) -> int:
        return len(self._queue)

    def reset(self, keep_counters: bool = False) -> None:
        """Zustand nullen. Zeitbasis bleibt erhalten. Zaehler nur nullen, wenn keep_counters False."""
        self._prev: int | None = None
        self._queue.clear()
        self._clear_frame()
        self._prev = None
        if not keep_counters:
            self.counters = {k: 0 for k in _COUNTER_KEYS}

    # ------------------------------------------------------------------
    # Interne Zustandsmaschine
    # ------------------------------------------------------------------
    def _clear_frame(self) -> None:
        self._open = False
        self._pending: int | None = None
        self._t_ms = 0
        self._cur = 0
        self._bitcnt = 0
        self._bytes = bytearray()
        self._ack_bits = 0
        self._flags = 0

    def _take_sample(self, sda: int) -> None:
        """SCL steigt: SDA merken. Festgeschrieben wird erst bei SCL fallend (_commit).

        Abweichung von der reinen Spezifikation (siehe Bericht): Der STOP- bzw. Sr-Setup-Puls
        (SCL high mit SDA low bzw. high, kurz vor SDA-Flanke bei SCL high) wuerde sonst als
        zusaetzliches Datenbit zaehlen und jedes Frame mit F_BITERR markieren.
        """
        if self._open:
            self._pending = sda

    def _commit(self) -> None:
        """SCL faellt: gemerkten Abtastwert als Bit verarbeiten."""
        if self._pending is None:
            return
        sda = self._pending
        self._pending = None
        self._sample(sda)

    def _sample(self, sda: int) -> None:
        """Bit verarbeiten (Bit 0..7 Daten, Bit 8 ACK/NACK)."""
        if not self._open:
            return
        if self._bitcnt % 9 < 8:
            self._cur = ((self._cur << 1) | sda) & 0xFF
        else:
            n = len(self._bytes)
            if n < MAX_FRAME_BYTES:
                self._bytes.append(self._cur)
                if sda == 0:
                    self._ack_bits |= 1 << n
            else:
                self._flags |= FLAG_OVERLEN
            self._cur = 0
        self._bitcnt += 1

    def _emit(self, flags: int) -> None:
        """Frame in die Ausgabe-Queue legen. Zaehler frames, err_overlen, frames_dropped."""
        if flags & FLAG_OVERLEN:
            self.counters["err_overlen"] += 1
        self.counters["frames"] += 1
        if len(self._queue) >= QUEUE_MAX:
            self.counters["frames_dropped"] += 1
            return
        n = len(self._bytes)
        ack = "".join("+" if (self._ack_bits >> i) & 1 else "-" for i in range(n))
        self._queue.append({"t_ms": self._t_ms, "data": bytes(self._bytes),
                            "ack": ack, "flags": flags})

    def _start(self) -> None:
        self.counters["starts"] += 1
        was_open = self._open
        if was_open and self._bitcnt > 0:
            flags = self._flags | FLAG_NOSTOP
            if self._bitcnt % 9:
                flags |= FLAG_BITERR
            self.counters["err_nostop"] += 1
            self._emit(flags)
        # Neuer Frame. War der vorige offen (auch leer), ist es ein Repeated START.
        self._bytes = bytearray()
        self._cur = 0
        self._bitcnt = 0
        self._ack_bits = 0
        self._open = True
        self._flags = FLAG_REPSTART if was_open else 0
        self._t_ms = self._now_ms()

    def _stop(self) -> None:
        if not self._open:
            return
        self.counters["stops"] += 1
        if self._bitcnt == 0:
            self._clear_frame()
            return
        flags = self._flags
        if self._bitcnt % 9:
            flags |= FLAG_BITERR
            self.counters["err_bits"] += 1
        self._emit(flags)
        self._clear_frame()


def decode_events(events: Iterable[tuple[int, int]], cycles_per_us: int = 240) -> tuple[list[dict], dict]:
    """Event-Folge (cyc, lvl) dekodieren. Liefert (frames, counters). Zeitbasis = erstes Event."""
    evs = list(events)
    origin = evs[0][0] if evs else 0
    dec = I2cRefDecoder(cycles_per_us, origin_cyc=origin, origin_ms=0)
    frames: list[dict] = []
    for cyc, lvl in evs:
        dec.feed(cyc, lvl)
        frame = dec.pop()
        while frame is not None:
            frames.append(frame)
            frame = dec.pop()
    frame = dec.pop()
    while frame is not None:
        frames.append(frame)
        frame = dec.pop()
    return frames, dict(dec.counters)


def bits_to_events(bits: Iterable[int], t0: int = 0, half_bit_cyc: int = 3840, stop: bool = True,
                   repstart: bool = False) -> list[tuple[int, int]]:
    """Bitfolge (9 Bit je Byte: 8 Daten + ACK, MSB zuerst) in Event-Folge (cyc, lvl) wandeln.

    Ohne repstart beginnt die Folge mit Bus-Ruhe (SDA=1, SCL=1) als Baseline, dann START.
    repstart=True: Folge beginnt mit SCL low, SDA high, SCL high (Sr-Vorbereitung), dann START.
    Ende: stop=True mit STOP, sonst nach dem letzten Bit (SCL high).
    """
    levels: list[int] = [0, 1, 3] if repstart else [3]
    cur = 3

    def go(lvl: int) -> None:
        nonlocal cur
        if lvl != cur:
            levels.append(lvl)
            cur = lvl

    go(2)  # START: SDA fallt bei SCL high
    for bit in bits:
        go(cur & 1)        # SCL fallt, SDA unveraendert
        go(bit & 1)        # SDA setzen, SCL low
        go((bit & 1) | 2)  # SCL steigt: Abtastung
    if stop:
        go(cur & 1)
        go(0)
        go(2)
        go(3)             # STOP: SDA steigt bei SCL high
    return [(((t0 + i * half_bit_cyc) & _U32), lvl) for i, lvl in enumerate(levels)]


def synth_events(data: bytes, ack: list[bool] | None = None, stop: bool = True, t0: int = 0,
                 half_bit_cyc: int = 3840, repstart: bool = False) -> list[tuple[int, int]]:
    """Synthetische Event-Folge fuer ein Frame. Standard: alle Bytes ACK. Siehe bits_to_events."""
    data = bytes(data)
    if ack is None:
        ack = [True] * len(data)
    if len(ack) != len(data):
        raise ValueError("ack muss so lang wie data sein")
    bits: list[int] = []
    for i, byte in enumerate(data):
        bits += [(byte >> (7 - k)) & 1 for k in range(8)]
        bits.append(0 if ack[i] else 1)
    return bits_to_events(bits, t0=t0, half_bit_cyc=half_bit_cyc, stop=stop, repstart=repstart)


def frame_to_row(frame: dict, unix_ms: int = 0) -> dict:
    """Ref-Frame in Frame-Zeile wandeln: t, u, data, ack, flags."""
    return {"t": int(frame["t_ms"]), "u": int(unix_ms), "data": bytes(frame["data"]),
            "ack": frame["ack"], "flags": int(frame["flags"])}


def _change_key(data: bytes) -> bytes:
    """Schluessel fuer ChangeFilter: Header (3 Byte), bei Istwerten inkl. Typ-Byte."""
    start = 1 if data[:1] == b"\x00" else 0
    if len(data) < start + 3:
        return data
    key = data[start:start + 3]
    if key == TYPE_HEADER and len(data) > start + 3:
        key = data[start:start + 4]
    return key


class ChangeFilter:
    """Gleiche Regeln wie ChangeFilter in i2c_frame.h (Round-Robin-Tabelle, 64 Eintraege)."""

    def __init__(self) -> None:
        self._table: OrderedDict[bytes, bytes] = OrderedDict()

    def pass_(self, row: dict) -> bool:
        data = bytes(row["data"])
        key = _change_key(data)
        flags = int(row.get("flags", 0))
        if key in self._table:
            last = self._table[key]
            self._table[key] = data
        else:
            last = None
            if len(self._table) >= CHANGE_TABLE_MAX:
                self._table.popitem(last=False)
            self._table[key] = data
        if flags != 0:
            return True
        return last is None or last != data

    def clear(self) -> None:
        self._table.clear()
