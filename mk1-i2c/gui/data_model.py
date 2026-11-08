"""Datenmodell fuer die mk1-i2c-GUI (kein Qt).

Nimmt Frame-Zeilen entgegen (t, u, data, ack, flags, optional ts, ts_src) und dekodiert
jede Zeile ueber eine uebergebene decode-Funktion (lwz180.decode, Rueckgabe-Dict siehe
INTERFACES.md 4.4). Ohne decode-Funktion bleiben nur die Rohdaten.
"""
from __future__ import annotations

from typing import Callable

DISPLAY_LEN = 28
KIND_ISTWERT = "istwert"
KIND_TASTEN = "tasten"
KIND_DISPLAY = "display"


def _row_ts(row: dict) -> float:
    """Zeitstempel (Unix-s) der Zeile: ts, sonst t/1000 als Fallback."""
    ts = row.get("ts")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return float(ts)
    return int(row["t"]) / 1000


def _header_text(res: dict | None) -> str | None:
    if res is None:
        return None
    header = res.get("header")
    return header if isinstance(header, str) else None


class DataModel:
    def __init__(self, decode: Callable[[bytes], dict] | None = None, max_frames: int = 200000) -> None:
        self._decode = decode
        self._max_frames = max(1, int(max_frames))
        self.clear()

    def clear(self) -> None:
        self.frames: list[dict] = []
        self.decoded: list[dict | None] = []
        self.latest: dict[int, dict] = {}
        self._history: dict[int, list[tuple[float, float]]] = {}
        self.display_current: bytes | None = None
        self.display_previous: bytes | None = None
        self.key_log: list[tuple[float, list[int]]] = []
        self.display_history: list[tuple[float, bytes]] = []
        self.counts_by_header: dict[str, int] = {}
        self.crc_total = 0
        self.crc_bad = 0

    # ------------------------------------------------------------------
    # Eingang
    # ------------------------------------------------------------------
    def _decode_row(self, row: dict) -> dict | None:
        if self._decode is None:
            return None
        try:
            res = self._decode(bytes(row["data"]))
        except Exception:
            return None
        return res if isinstance(res, dict) else None

    def add_frames(self, rows: list[dict]) -> list[dict | None]:
        """Zeilen speichern und dekodieren. Rueckgabe: Dekodierergebnis je Zeile (None ohne Dekoder)."""
        results: list[dict | None] = []
        for row in rows:
            res = self._decode_row(row)
            self.frames.append(row)
            self.decoded.append(res)
            results.append(res)
            self._account(row, res)
        self._trim()
        return results

    def _trim(self) -> None:
        extra = len(self.frames) - self._max_frames
        if extra > 0:
            del self.frames[:extra]
            del self.decoded[:extra]

    def _account(self, row: dict, res: dict | None) -> None:
        if res is None:
            return
        header = _header_text(res)
        if header is not None:
            self.counts_by_header[header] = self.counts_by_header.get(header, 0) + 1
        if res.get("crc_ok") is not None:
            self.crc_total += 1
            if res["crc_ok"] is False:
                self.crc_bad += 1
        kind = res.get("kind")
        ts = _row_ts(row)
        if kind == KIND_ISTWERT:
            self._account_istwert(ts, res)
        elif kind == KIND_TASTEN:
            keys = res.get("keys")
            if keys is not None:
                self.key_log.append((ts, [int(k) for k in keys]))
                self._cap(self.key_log)
        elif kind == KIND_DISPLAY:
            seg = res.get("segments")
            if seg is not None and len(seg) == DISPLAY_LEN:
                if self.display_current is not None:
                    self.display_previous = self.display_current
                self.display_current = bytes(seg)
                self.display_history.append((ts, bytes(seg)))
                self._cap(self.display_history)

    def _account_istwert(self, ts: float, res: dict) -> None:
        type_id = res.get("type")
        value = res.get("value")
        if type_id is None or value is None:
            return
        self.latest[int(type_id)] = {"name": res.get("name"), "value": value,
                                     "unit": res.get("unit"), "raw": res.get("raw"), "ts": ts}
        hist = self._history.setdefault(int(type_id), [])
        hist.append((ts, float(value)))
        self._cap(hist)

    def _cap(self, items: list) -> None:
        extra = len(items) - self._max_frames
        if extra > 0:
            del items[:extra]

    # ------------------------------------------------------------------
    # Auswertung
    # ------------------------------------------------------------------
    def history(self, type_id: int) -> list[tuple[float, float]]:
        return list(self._history.get(int(type_id), []))

    def display_changed_bits(self) -> list[int]:
        """Bitindizes (MSB zuerst je Byte: Index = Byte*8 + Bit) die sich gegenueber previous aendern."""
        cur, prev = self.display_current, self.display_previous
        if cur is None or prev is None:
            return []
        out: list[int] = []
        for i in range(min(len(cur), len(prev))):
            diff = cur[i] ^ prev[i]
            for bit in range(8):
                if diff & (0x80 >> bit):
                    out.append(i * 8 + bit)
        return out

    def crc_error_rate(self) -> float:
        if self.crc_total == 0:
            return 0.0
        return self.crc_bad / self.crc_total

    def filter_rows(self, header: str | None = None, text: str | None = None) -> list[dict]:
        """Zeilen filtern. header: Vergleich mit dem dekodierten Header (ohne Gross/Klein). text: Teilstring
        in Hex-Daten, Name oder Header (ohne Gross/Klein)."""
        want_header = header.strip().lower() if header else None
        needle = text.strip().lower() if text else None
        out: list[dict] = []
        for row, res in zip(self.frames, self.decoded):
            if want_header is not None:
                head = _header_text(res)
                if head is None or head.lower() != want_header:
                    continue
            if needle is not None:
                parts = [bytes(row["data"]).hex(), _header_text(res) or ""]
                if res is not None and isinstance(res.get("name"), str):
                    parts.append(res["name"])
                if needle not in " ".join(parts).lower():
                    continue
            out.append(row)
        return out
