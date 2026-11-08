"""Saleae-Logic-Daten fuer die mk1-i2c-GUI laden (kein Qt).

Zwei Formate, am Header erkannt:
- Raw-Export (digital.csv): "Time [s]" + Kanalspalten, eine Zeile je Flanke. Die Kanaele SDA/SCL
  werden automatisch erkannt (oder vorgegeben) und laufen durch i2c_ref_decoder, also dieselbe
  Logik wie die Firmware.
- Analyzer-Export (I2C-Tabelle, Logic 1/2): ueber export_io.load_saleae_i2c_csv.

Zeitstempel: Saleae liefert relative Sekunden. Basis ist die Aenderungszeit der Datei minus
Aufnahmedauer (Naeherung fuer die Uhrzeit), ts_src = "logic".
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

import i2c_ref_decoder as rd
from export_io import load_saleae_i2c_csv

TS_SOURCE = "logic"
CYC_PER_US = 240  # Zeitbasis wie Firmware (240 MHz), nur fuer den Decoder


def _read_table(path) -> tuple[list[str], list[list[str]]]:
    text = Path(path).read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    if not lines:
        return [], []
    delim = ";" if lines[0].count(";") > lines[0].count(",") else ","
    table = list(csv.reader(lines, delimiter=delim))
    return [h.strip() for h in table[0]], [r for r in table[1:] if r]


def detect_format(path) -> str:
    """'raw', 'analyzer' oder 'unknown'."""
    with open(path, encoding="utf-8-sig") as fh:
        header = [h.strip().lower() for h in fh.readline().replace(";", ",").split(",")]
    if "packet id" in header or "type" in header:
        return "analyzer"
    if header and header[0].startswith("time") and len(header) >= 3:
        return "raw"
    return "unknown"


def _parse_time(text: str) -> float:
    return float(text.strip().replace(",", "."))


def raw_channels(path) -> tuple[list[str], dict[str, int]]:
    """Kanalnamen und Flankenzahl je Kanal eines Raw-Exports."""
    header, rows = _read_table(path)
    chans = header[1:]
    edges = {c: 0 for c in chans}
    prev = None
    for row in rows:
        if prev is not None:
            for i, c in enumerate(chans, start=1):
                if i < len(row) and i < len(prev) and row[i] != prev[i]:
                    edges[c] += 1
        prev = row
    return chans, edges


def _decode_raw(header, rows, sda: str, scl: str) -> tuple[list[dict], dict, list[float]]:
    i_sda, i_scl = header.index(sda), header.index(scl)
    times: list[float] = []
    events: list[tuple[int, int]] = []
    for row in rows:
        try:
            t = _parse_time(row[0])
            lvl = (1 if row[i_sda].strip() == "1" else 0) | ((1 if row[i_scl].strip() == "1" else 0) << 1)
        except (ValueError, IndexError):
            continue
        times.append(t)
        events.append((int(round(t * CYC_PER_US * 1e6)) & 0xFFFFFFFF, lvl))
    frames, counters = rd.decode_events(events, CYC_PER_US)
    return frames, counters, times


def _scl_khz(header, rows, scl: str) -> float | None:
    """Median der SCL-Periode (steigende Flanken < 1 ms auseinander) als Frequenz in kHz."""
    i_scl = header.index(scl)
    rises = []
    prev = None
    for row in rows:
        try:
            v = row[i_scl].strip()
        except IndexError:
            continue
        if prev == "0" and v == "1":
            rises.append(_parse_time(row[0]))
        prev = v
    per = sorted(b - a for a, b in zip(rises, rises[1:]) if 0 < b - a < 1e-3)
    if not per:
        return None
    return round(1e-3 / per[len(per) // 2], 2)


def _ts_base(path, last_t: float) -> float:
    return os.path.getmtime(path) - last_t


def load_logic_raw(path, sda: str | None = None, scl: str | None = None) -> tuple[list[dict], dict]:
    """Raw-Export dekodieren. Ohne sda/scl: die zwei aktivsten Kanaele, beide Zuordnungen probiert,
    gewaehlt wird die mit weniger Bitfehlern (dann mehr Frames).

    Rueckgabe: (rows im GUI-Format, info). info: sda, scl, scl_khz, frames, counters, duration_s, edges.
    """
    header, rows = _read_table(path)
    if len(header) < 3:
        raise ValueError("kein Raw-Export (zu wenige Spalten)")
    chans, edges = raw_channels(path)
    if sda is None or scl is None:
        active = [c for c, n in sorted(edges.items(), key=lambda kv: -kv[1]) if n > 0][:2]
        if len(active) < 2:
            raise ValueError("weniger als zwei Kanaele mit Flanken")
        best = None
        for cand_scl, cand_sda in ((active[0], active[1]), (active[1], active[0])):
            frames, counters, times = _decode_raw(header, rows, cand_sda, cand_scl)
            key = (counters["err_bits"], -len(frames))
            if best is None or key < best[0]:
                best = (key, cand_sda, cand_scl, frames, counters, times)
        _, sda, scl, frames, counters, times = best
    else:
        if sda not in header or scl not in header or sda == scl:
            raise ValueError("SDA/SCL-Kanal ungueltig")
        frames, counters, times = _decode_raw(header, rows, sda, scl)
    last_t = times[-1] if times else 0.0
    t0 = times[0] if times else 0.0
    base = _ts_base(path, last_t)
    out = []
    for f in frames:
        t_s = t0 + int(f["t_ms"]) / 1000.0
        out.append({"t": int(round(t_s * 1000)), "u": 0, "data": bytes(f["data"]), "ack": f["ack"],
                    "flags": int(f["flags"]), "ts": base + t_s, "ts_src": TS_SOURCE})
    info = {"format": "raw", "sda": sda, "scl": scl, "scl_khz": _scl_khz(header, rows, scl),
            "frames": len(out), "counters": counters, "duration_s": round(last_t - t0, 3), "edges": edges}
    return out, info


def load_logic_analyzer(path) -> tuple[list[dict], dict]:
    """Analyzer-Export (I2C-Tabelle) als Frames laden."""
    rows = load_saleae_i2c_csv(path)
    last_t = max((r["t"] for r in rows), default=0) / 1000.0
    base = _ts_base(path, last_t)
    for r in rows:
        r["ts"] = base + r["t"] / 1000.0
        r["ts_src"] = TS_SOURCE
    return rows, {"format": "analyzer", "frames": len(rows), "duration_s": round(last_t, 3)}


def load_logic(path, sda: str | None = None, scl: str | None = None) -> tuple[list[dict], dict]:
    """Format erkennen und laden. ValueError bei unbekanntem Format."""
    fmt = detect_format(path)
    if fmt == "raw":
        return load_logic_raw(path, sda, scl)
    if fmt == "analyzer":
        return load_logic_analyzer(path)
    raise ValueError("unbekanntes Format (weder Saleae-Raw noch I2C-Analyzer-Export)")
