"""Messdaten-Modell fuer die Auswerte-GUI (kein Qt).

Quellen: auto_*_state.json (volle Rohwerte, Min/Max), *_bekannt.csv / *_unbekannt.csv
(zusammengefasste Tabellen, nur letzter Rohwert) und optional auto_*_rx.jsonl (Antwortzeiten).
State-Daten haben Vorrang vor CSV-Daten.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

GUI_DIR = Path(__file__).resolve().parent
TOOLS_DIR = GUI_DIR.parent / "tools"

PRIO_CSV = 1
PRIO_STATE = 2

_RAW_RE = re.compile(r"0x([0-9a-fA-F]{4})")
_MINMAX_RAW_RE = re.compile(r"min 0x([0-9a-fA-F]{4}) / max 0x([0-9a-fA-F]{4})")
_MEMBER_RE = re.compile(r"^0x[0-9a-fA-F]{3}$")
_QUOTE_RE = re.compile(r"^(\d+)")


def _num(value) -> int:
    if isinstance(value, int):
        return value
    text = str(value).strip()
    return int(text, 16) if text.lower().startswith("0x") else int(text, 10)


@dataclass
class Cell:
    """Antwort eines Members auf einen Index."""
    status: str = "none"          # ok | unsupported | none
    raws: list[int] = field(default_factory=list)   # alle ok-Rohwerte, chronologisch
    min_raw: int | None = None
    max_raw: int | None = None
    mm_text: str = ""             # nur CSV: Min/Max-Text, wenn keine Rohwerte vorliegen
    prio: int = PRIO_CSV

    @property
    def raw(self) -> int | None:
        return self.raws[-1] if self.raws else None


class Measurements:
    def __init__(self) -> None:
        self.by_index: dict[int, dict[int, Cell]] = {}
        self.latency: dict[int, dict[str, float]] = {}   # member -> {med,p90,max,quote}
        self.sources: list[str] = []

    # ---- Zugriff ----
    @property
    def members(self) -> list[int]:
        return sorted({m for cells in self.by_index.values() for m, c in cells.items()
                       if c.status == "ok"})

    @property
    def indices(self) -> list[int]:
        """Nur Indizes, auf die mindestens ein Member mit einem Wert geantwortet hat."""
        return sorted(i for i, cells in self.by_index.items()
                      if any(c.status == "ok" for c in cells.values()))

    def cells(self, index: int) -> dict[int, Cell]:
        return self.by_index.get(index, {})

    def all_raws(self, index: int) -> list[int]:
        out: list[int] = []
        for c in self.cells(index).values():
            out += c.raws
        return out

    def _put(self, index: int, member: int, cell: Cell) -> None:
        row = self.by_index.setdefault(index, {})
        old = row.get(member)
        if old is None or cell.prio > old.prio:
            row[member] = cell
        elif cell.prio == old.prio:
            # gleiche Prioritaet: Daten zusammenfuehren
            old.raws += cell.raws
            if cell.status == "ok":
                old.status = "ok"
            elif old.status == "none":
                old.status = cell.status
            if cell.min_raw is not None:
                old.min_raw = cell.min_raw
            if cell.max_raw is not None:
                old.max_raw = cell.max_raw
        elif not old.mm_text and cell.mm_text:
            old.mm_text = cell.mm_text

    # ---- Laden ----
    def load_state(self, path: str) -> int:
        if os.path.basename(path) in self.sources:   # gleiche Datei nicht doppelt zaehlen
            return 0
        with open(path, encoding="utf-8") as fh:
            st = json.load(fh)
        cells: dict[tuple[int, int], Cell] = {}

        def cell(m: int, i: int) -> Cell:
            return cells.setdefault((m, i), Cell(prio=PRIO_STATE))

        for task in st.get("tasks", []):
            sw = 0xFA if task.get("switch") is None else _num(task["switch"])
            if sw != 0xFA:
                continue
            m = _num(task["member"])
            for it in task.get("items", []):
                c = cell(m, _num(it["index"]))
                if it["status"] == "ok":
                    c.raws.append(_num(it["raw"]))
                    c.status = "ok"
                elif it["status"] == "unsupported" and c.status != "ok":
                    c.status = "unsupported"
        for r in st.get("rereads", []):
            sw = 0xFA if r.get("switch") is None else _num(r["switch"])
            if r.get("status") == "ok" and sw == 0xFA:
                c = cell(_num(r["member"]), _num(r["index"]))
                c.raws.append(_num(r["raw"]))
                c.status = "ok"
        for e in st.get("minmax", []):
            if e.get("status") != "ok" or e.get("raw") is None:
                continue
            c = cell(_num(e["member"]), _num(e["index"]))
            if _num(e["switch"]) == 0xF8:
                c.min_raw = _num(e["raw"])
            elif _num(e["switch"]) == 0xF9:
                c.max_raw = _num(e["raw"])
        for (m, i), c in cells.items():
            self._put(i, m, c)
        self.sources.append(os.path.basename(path))
        return len(cells)

    def load_csv(self, path: str) -> int:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh, delimiter=";"))
        if not rows:
            return 0
        header = rows[0]
        mcols = {k: _num(h) for k, h in enumerate(header) if _MEMBER_RE.match(h.strip())}
        n = 0
        for row in rows[1:]:
            if not row:
                continue
            label = row[1] if len(row) > 1 else ""
            if not row[0].strip() and label.startswith("ANTWORTZEIT"):
                self._csv_latency(label, row, mcols)
                continue
            if not row[0].strip().lower().startswith("0x"):
                continue
            index = _num(row[0])
            for k, m in mcols.items():
                text = row[k].strip() if k < len(row) else ""
                if not text:
                    continue
                if text.startswith("nicht unterst"):
                    self._put(index, m, Cell(status="unsupported", prio=PRIO_CSV))
                    continue
                mm = _RAW_RE.search(text)
                if not mm:
                    continue
                c = Cell(status="ok", raws=[int(mm.group(1), 16)], prio=PRIO_CSV)
                mr = _MINMAX_RAW_RE.search(text)
                if mr:
                    c.min_raw, c.max_raw = int(mr.group(1), 16), int(mr.group(2), 16)
                elif "(min " in text:
                    c.mm_text = text[text.index("(min "):]
                self._put(index, m, c)
                n += 1
        self.sources.append(os.path.basename(path))
        return n

    def _csv_latency(self, label: str, row: list[str], mcols: dict[int, int]) -> None:
        key = ("med" if "Median" in label else "p90" if "p90" in label
               else "max" if "max" in label else "quote" if "quote" in label else None)
        if key is None:
            return
        for k, m in mcols.items():
            text = row[k].strip() if k < len(row) else ""
            mq = _QUOTE_RE.match(text)
            if mq:
                self.latency.setdefault(m, {})[key] = float(mq.group(1))

    def load_rx(self, path: str) -> int:
        if str(TOOLS_DIR) not in sys.path:
            sys.path.insert(0, str(TOOLS_DIR))
        from latency import latencies, stats  # noqa: E402
        lat, sent = latencies([path])
        for m, values in lat.items():
            st = stats(values, sent.get(m, 0))
            if st:
                self.latency[m] = {"med": st["med"], "p90": st["p90"], "max": st["max"],
                                   "quote": st["quote"]}
        self.sources.append(os.path.basename(path))
        return len(lat)

    def load_any(self, path: str) -> int:
        low = path.lower()
        if low.endswith(".csv"):
            return self.load_csv(path)
        if low.endswith(".jsonl"):
            return self.load_rx(path)
        return self.load_state(path)
