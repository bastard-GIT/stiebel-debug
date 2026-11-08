"""Export/Import fuer mk1-i2c (kein Qt).

- CSV: Semikolon-Trennzeichen, UTF-8 mit BOM, CRLF (Excel-kompatibel).
- JSONL: eine Zeile je Frame, Schluessel t, ts (optional), d, a, f, ts_src (optional),
  siehe INTERFACES.md Abschnitt 3. Der Lader akzeptiert auch rohe rx-Zeilen mit u (ms).
- Logic-Analyzer-CSV (Saleae, Logic 1 und Logic 2) und Vergleich mit einem Mitschnitt.
"""

from __future__ import annotations

import csv
import difflib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Callable

FRAME_CSV_HEADER = [
    "t",
    "zeit_unix",
    "zeit_lokal",
    "adresse",
    "header",
    "typ",
    "data_hex",
    "ack",
    "flags",
    "crc_ok",
    "name",
    "wert",
]

_CSV_ENCODING = "utf-8-sig"  # UTF-8 mit BOM
_JSONL_ENCODING = "utf-8"
SALEAE_SOURCE = "saleae"
SALEAE_GAP_S = 0.001  # Legacy-Export ohne Packet ID: Zeitluecke, ab der ein neuer Frame beginnt
                      # (LWZ 180: Byteabstand 0,67 ms, Display -> Istwert nur ~1,3 ms)


def _ts_or_none(value):
    """Optionaler Zeitstempel (Unix-Sekunden) -> float oder None bei fehlend/ungueltig."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _row_ts(row):
    """ts der Zeile oder aus u (ms) abgeleitet, sonst None."""
    ts = _ts_or_none(row.get("ts"))
    if ts is None:
        u = row.get("u")
        if isinstance(u, int) and not isinstance(u, bool) and u > 0:
            ts = u / 1000
    return None if ts is None else round(ts, 3)


def _decode_columns(data: bytes, decode: Callable[[bytes], dict] | None) -> tuple[str, str, str, str, str]:
    """(header, typ, crc_ok, name, wert) aus decode(data). Ohne decode oder bei Fehler: 5x ''."""
    if decode is None:
        return "", "", "", "", ""
    try:
        res = decode(data)
    except Exception:
        return "", "", "", "", ""
    if not isinstance(res, dict):
        return "", "", "", "", ""
    header = res.get("header") or ""
    type_id = res.get("type")
    typ = "" if type_id is None else f"0x{int(type_id):02x}"
    crc_ok = res.get("crc_ok")
    crc = "" if crc_ok is None else ("1" if crc_ok else "0")
    name = res.get("name") or ""
    if res.get("value") is not None:
        wert = str(res["value"])
    elif res.get("keys") is not None:
        wert = ",".join(str(k) for k in res["keys"])
    else:
        wert = ""
    return header, typ, crc, name, wert


def save_frames_csv(path, rows, decode=None) -> None:
    """Schreibt Frame-Zeilen als CSV (siehe FRAME_CSV_HEADER).

    rows: Iterable von {"t","u","data","ack","flags"}, optional "ts" (Unix-s).
    decode: optional, Callable(bytes) -> dict wie lwz180.decode. Ohne decode bleiben die Dekodierspalten leer.
    """
    with open(path, "w", encoding=_CSV_ENCODING, newline="") as fh:
        writer = csv.writer(fh, delimiter=";", lineterminator="\r\n")
        writer.writerow(FRAME_CSV_HEADER)
        for row in rows:
            data = bytes(row["data"])
            ts = _row_ts(row)
            if ts is None:
                zeit_unix = zeit_lokal = ""
            else:
                zeit_unix = f"{ts:.3f}"
                zeit_lokal = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            adresse = f"0x{data[0]:02x}" if data else ""
            header, typ, crc_ok, name, wert = _decode_columns(data, decode)
            writer.writerow([
                int(row["t"]),
                zeit_unix,
                zeit_lokal,
                adresse,
                header,
                typ,
                data.hex(),
                row.get("ack", ""),
                int(row.get("flags", 0)),
                crc_ok,
                name,
                wert,
            ])


def save_frames_jsonl(path, rows) -> None:
    """Schreibt Frame-Zeilen als JSONL, eine Zeile je Frame.

    Zeile: {"t":123,"ts":1761038123.456,"d":"hex","a":"+-","f":0,"ts_src":"esp"}
    ts nur, wenn vorhanden oder u > 0 (dann ts = u/1000). ts_src nur, wenn vorhanden.
    """
    with open(path, "w", encoding=_JSONL_ENCODING, newline="\n") as fh:
        for row in rows:
            obj = {"t": int(row["t"])}
            ts = _row_ts(row)
            if ts is not None:
                obj["ts"] = ts
            obj["d"] = bytes(row["data"]).hex()
            obj["a"] = str(row["ack"])
            obj["f"] = int(row.get("flags", 0))
            if row.get("ts_src") is not None:
                obj["ts_src"] = str(row["ts_src"])
            fh.write(json.dumps(obj, separators=(",", ":")) + "\n")


def _int_or_none(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def load_frames_jsonl(path) -> list[dict]:
    """Liest JSONL (eigenes Format oder rohe rx-Zeilen mit u) und liefert Frame-Zeilen.

    Pflicht je Zeile: t (int), d (Hex), a (String, Laenge == Bytes in d). f fehlt: 0.
    Fehlerhafte Zeilen werden uebersprungen. Ungueltiges ts/ts_src wird weggelassen, die Zeile bleibt.
    Fehlt ts und ist u > 0, gilt ts = u/1000.
    """
    rows = []
    with open(path, "r", encoding=_JSONL_ENCODING) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    continue
                t = obj["t"]
                if isinstance(t, bool) or not isinstance(t, int):
                    continue
                data = bytes.fromhex(obj["d"])
                ack = obj["a"]
                if not isinstance(ack, str) or len(ack) != len(data):
                    continue
                flags = obj.get("f", 0)
                if isinstance(flags, bool) or not isinstance(flags, int):
                    continue
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            u = _int_or_none(obj.get("u"))
            row = {"t": t, "u": u if u is not None else 0, "data": data, "ack": ack, "flags": flags}
            ts = _ts_or_none(obj.get("ts"))
            if ts is None and row["u"] > 0:
                ts = round(row["u"] / 1000, 3)
            if ts is not None:
                row["ts"] = ts
            ts_src = obj.get("ts_src")
            if isinstance(ts_src, str):
                row["ts_src"] = ts_src
            rows.append(row)
    return rows


# ----------------------------------------------------------------------
# Saleae-I2C-Export
# ----------------------------------------------------------------------

def _norm(name: str) -> str:
    return name.strip().lower()


def _col(header: list[str], *names: str) -> int | None:
    """Index der ersten passenden Spalte (Gross/Klein egal), sonst None."""
    lowered = [_norm(h) for h in header]
    for name in names:
        if name in lowered:
            return lowered.index(name)
    return None


def _cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx].strip()


def _parse_int(text: str) -> int | None:
    """'0x50', '50' (zwei Hex-Ziffern), dezimal sonst. Ungueltig: None."""
    text = text.strip()
    if not text:
        return None
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        if len(text) == 2 and all(c in "0123456789abcdefABCDEF" for c in text):
            return int(text, 16)
        return int(text, 10)
    except ValueError:
        return None


def _parse_float(text: str) -> float | None:
    try:
        return float(text.strip().replace(",", "."))
    except ValueError:
        return None


def _parse_ack(text: str) -> str | None:
    """ACK-Token -> '+' (ACK) oder '-' (NACK). Unbekannt: None."""
    token = text.strip().lower()
    if token in ("ack", "+", "0"):
        return "+"
    if token in ("nack", "nak", "-", "1"):
        return "-"
    return None


def _parse_rw(text: str) -> int | None:
    """Read/Write -> 1 (read), 0 (write). Unbekannt: None."""
    token = text.strip().lower()
    if token in ("read", "r", "1", "true"):
        return 1
    if token in ("write", "w", "0", "false"):
        return 0
    return None


def _delimiter(first_line: str) -> str:
    return ";" if first_line.count(";") > first_line.count(",") else ","


def _frame_row(start_s: float, data: list[int], acks: list[str]) -> dict:
    return {"t": round(start_s * 1000), "u": 0, "data": bytes(data), "ack": "".join(acks),
            "flags": 0, "ts_src": SALEAE_SOURCE}


def _load_saleae_logic1(rows: list[list[str]], header: list[str]) -> list[dict]:
    """Logic 1 (Legacy): Time [s], Packet ID, Address, Data, Read/Write, ACK/NACK. Eine Zeile je Byte."""
    i_time = _col(header, "time [s]", "time")
    i_pid = _col(header, "packet id")
    i_addr = _col(header, "address")
    i_data = _col(header, "data")
    i_rw = _col(header, "read/write")
    i_ack = _col(header, "ack/nack", "ack/nak", "ack")  # Logic 1 schreibt "ACK/NAK"
    if i_time is None or i_pid is None or i_ack is None:
        return []
    packets: dict[str, dict] = {}
    order: list[str] = []
    # Logic 2 "Legacy"-Export: Packet ID immer gleich (0 oder leer), Adresse in jeder Zeile.
    # Dann trennt nur die Zeitluecke die Frames (Bytes ~0,7 ms, Pakete >= 50 ms auseinander).
    pids = {_cell(row, i_pid) for row in rows if _cell(row, i_time)}
    split_by_gap = len(pids - {""}) <= 1
    seq = 0
    last_t = None
    for row in rows:
        pid = _cell(row, i_pid)
        t = _parse_float(_cell(row, i_time))
        ack = _parse_ack(_cell(row, i_ack))
        if t is None or ack is None:
            continue
        if split_by_gap:
            if last_t is None or t - last_t > SALEAE_GAP_S or not _cell(row, i_data):
                seq += 1
            last_t = t
            pid = f"gap{seq}"
        elif not pid:
            continue
        pkt = packets.get(pid)
        if pkt is None:
            pkt = {"t": t, "data": [], "acks": [], "first": True, "bad": False}
            packets[pid] = pkt
            order.append(pid)
        addr_text = _cell(row, i_addr)
        data_text = _cell(row, i_data)
        if pkt["first"] and addr_text:
            addr = _parse_int(addr_text)
            rw = _parse_rw(_cell(row, i_rw)) or 0
            if addr is None:
                pkt["bad"] = True
                continue
            pkt["data"].append(((addr << 1) | rw) & 0xFF)
            if split_by_gap and data_text:
                # Legacy-Export: Adress-ACK fehlt, die Zeile traegt schon das erste Datenbyte.
                pkt["acks"].append("+")
                value = _parse_int(data_text)
                if value is not None:
                    pkt["data"].append(value & 0xFF)
                    pkt["acks"].append(ack)
            else:
                pkt["acks"].append(ack)
        elif data_text:
            value = _parse_int(data_text)
            if value is None:
                continue  # fehlerhafte Zeile
            pkt["data"].append(value & 0xFF)
            pkt["acks"].append(ack)
        pkt["first"] = False
    out = []
    for pid in order:
        pkt = packets[pid]
        if pkt["bad"] or not pkt["data"]:
            continue
        out.append(_frame_row(pkt["t"], pkt["data"], pkt["acks"]))
    return out


def _load_saleae_logic2(rows: list[list[str]], header: list[str]) -> list[dict]:
    """Logic 2: name, type, start_time, duration, ... Typen start, address, data, stop."""
    i_type = _col(header, "type")
    i_time = _col(header, "start_time", "start time", "time")
    i_addr = _col(header, "address")
    i_data = _col(header, "data")
    i_rw = _col(header, "read", "read/write", "r/w", "rw")
    i_ack = _col(header, "ack", "ack/nack")
    if i_type is None or i_time is None or i_ack is None:
        return []
    out: list[dict] = []
    frame: dict | None = None

    def close() -> None:
        nonlocal frame
        if frame is not None and frame["data"]:
            out.append(_frame_row(frame["t"], frame["data"], frame["acks"]))
        frame = None

    for row in rows:
        kind = _cell(row, i_type).lower()
        if not kind:
            continue
        t = _parse_float(_cell(row, i_time))
        if t is None:
            continue
        if "start" in kind:
            close()
            frame = {"t": t, "data": [], "acks": []}
            continue
        if kind == "stop":
            close()
            continue
        if kind not in ("address", "data"):
            continue
        ack = _parse_ack(_cell(row, i_ack))
        if ack is None:
            continue  # fehlerhafte Zeile
        if frame is None:
            frame = {"t": t, "data": [], "acks": []}
        if kind == "address":
            addr = _parse_int(_cell(row, i_addr) or _cell(row, i_data))
            rw = _parse_rw(_cell(row, i_rw)) if i_rw is not None else 0
            if addr is None or rw is None:
                continue
            value = ((addr << 1) | rw) & 0xFF
        else:
            value = _parse_int(_cell(row, i_data))
            if value is None:
                continue
            value &= 0xFF
        frame["data"].append(value)
        frame["acks"].append(ack)
    close()
    return out


def load_saleae_i2c_csv(path) -> list[dict]:
    """Frame-Zeilen aus einem Saleae-I2C-CSV-Export (Logic 2 oder Logic 1, am Header erkannt).

    Frame = ab start/Adresse bis stop (Logic 2) bzw. gleiche Packet ID (Logic 1).
    data[0] = (Adresse << 1) | rw (rw 1 = read). ack je Byte: '+' ACK, '-' NACK.
    t = round(start_time * 1000). Keine ts-Angabe (relative Zeit), u = 0, flags = 0, ts_src = 'saleae'.
    Fehlerhafte Zeilen werden uebersprungen.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    if not lines:
        return []
    delim = _delimiter(lines[0])
    table = list(csv.reader(lines, delimiter=delim))
    header = [h.strip() for h in table[0]]
    rows = table[1:]
    names = {_norm(h) for h in header}
    if "packet id" in names:
        return _load_saleae_logic1(rows, header)
    if "type" in names:
        return _load_saleae_logic2(rows, header)
    return []


def compare_frames(captured: list[dict], reference: list[dict]) -> dict:
    """Vergleich Mitschnitt (captured) gegen Referenz (reference) ueber die Reihenfolge der Bytes.

    Ergebnis: matched (Anzahl gleicher Frames), missing (nur in reference), extra (nur in captured),
    different (Paare (captured_row, reference_row) an gleich langen ersetzten Bloecken), match_rate.
    Zeitstempel und ACK werden nicht verglichen.
    """
    cap_keys = [bytes(r["data"]).hex() for r in captured]
    ref_keys = [bytes(r["data"]).hex() for r in reference]
    matcher = difflib.SequenceMatcher(None, ref_keys, cap_keys, autojunk=False)
    matched = 0
    missing: list[dict] = []
    extra: list[dict] = []
    different: list[tuple[dict, dict]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            matched += i2 - i1
        elif tag == "delete":
            missing.extend(reference[i1:i2])
        elif tag == "insert":
            extra.extend(captured[j1:j2])
        else:  # replace
            if (i2 - i1) == (j2 - j1):
                for k in range(i2 - i1):
                    different.append((captured[j1 + k], reference[i1 + k]))
            else:
                missing.extend(reference[i1:i2])
                extra.extend(captured[j1:j2])
    return {
        "matched": matched,
        "missing": missing,
        "extra": extra,
        "different": different,
        "match_rate": matched / max(len(reference), 1),
    }
