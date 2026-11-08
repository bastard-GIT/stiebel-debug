"""Export/Import fuer mk1-debug (kein Qt).

- CSV: Semikolon-Trennzeichen, UTF-8 mit BOM (Excel-kompatibel).
- JSONL: gleiches Zeilenformat wie die MQTT-rx-Payload (Abschnitt 1 INTERFACES.md).
"""

import csv
import json
import math
from datetime import datetime

FRAME_CSV_HEADER = [
    "t",
    "zeit_unix",
    "zeit_lokal",
    "richtung",
    "can_id",
    "data_hex",
    "absender",
    "empfaenger",
    "msg_type",
    "index",
    "switch",
    "name",
]

SCAN_CSV_HEADER = ["index", "switch", "name", "raw", "status", "wert"]

_CSV_ENCODING = "utf-8-sig"  # UTF-8 mit BOM
_JSONL_ENCODING = "utf-8"


def _hex_id(value, width):
    """int -> "0x%0<width>x" (Kleinbuchstaben)."""
    return "0x%0*x" % (width, int(value))


def _to_int(value):
    """int oder String ("0x180", "12") -> int."""
    if isinstance(value, str):
        return int(value, 0)
    return int(value)


def _ts_or_none(value):
    """Optionaler Zeitstempel (Unix-Sekunden) -> float oder None bei fehlend/ungueltig."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _decode_frame(can_id, data, db):
    """Liefert (absender, empfaenger, msg_type, index, switch, name) oder 6x ""."""
    if db is None:
        return "", "", "", "", "", ""
    from elster import parse_frame, switch_label  # lazy: elster.py liegt parallel in gui/

    parsed = parse_frame(can_id, data)
    if parsed is None:
        return "", "", "", "", "", ""
    index = parsed["index"]
    return (
        _hex_id(parsed["sender"], 3),
        _hex_id(parsed["receiver"], 3),
        parsed["msg_type"],
        _hex_id(index, 4),
        switch_label(parsed.get("switch")),
        # Systemnachrichten (Art 6/7) sind keine Indizes: wie im Live-Tab als SYSTEM_0x.. benennen
        f"SYSTEM_0x{index:02X}" if parsed.get("system") else db.signal_name(index),
    )


def save_frames_csv(path, rows, db=None):
    """Schreibt Frames als CSV.

    rows: Iterable von {"t":int,"id":int,"data":bytes,"tx":bool}, optional
          "ts" (float, Unix-Sekunden). Ohne ts bleiben zeit_unix/zeit_lokal leer.
    db: optional ElsterDb. Ohne db bleiben die Dekodier-Spalten leer.
    """
    with open(path, "w", encoding=_CSV_ENCODING, newline="") as fh:
        writer = csv.writer(fh, delimiter=";", lineterminator="\r\n")
        writer.writerow(FRAME_CSV_HEADER)
        for row in rows:
            can_id = int(row["id"])
            data = bytes(row["data"])
            decoded = _decode_frame(can_id, data, db)
            ts = _ts_or_none(row.get("ts"))
            if ts is None:
                zeit_unix = zeit_lokal = ""
            else:
                ts = round(ts, 3)
                zeit_unix = f"{ts:.3f}"
                zeit_lokal = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            writer.writerow(
                [
                    int(row["t"]),
                    zeit_unix,
                    zeit_lokal,
                    "TX" if row["tx"] else "RX",
                    _hex_id(can_id, 3),
                    data.hex(),
                    *decoded,
                ]
            )


def save_frames_jsonl(path, rows):
    """Schreibt Frames als JSONL, eine Zeile je Frame.

    Zeile: {"t":123,"id":"0x180","d":"hex","tx":false}
    Optional: "ts" (direkt nach "t", round(ts,3)) und "ts_src" (am Ende).
    """
    with open(path, "w", encoding=_JSONL_ENCODING, newline="\n") as fh:
        for row in rows:
            obj = {"t": int(row["t"])}
            ts = _ts_or_none(row.get("ts"))
            if ts is not None:
                obj["ts"] = round(ts, 3)
            obj["id"] = _hex_id(row["id"], 3)
            obj["d"] = bytes(row["data"]).hex()
            obj["tx"] = bool(row["tx"])
            if row.get("ts_src") is not None:
                obj["ts_src"] = str(row["ts_src"])
            fh.write(json.dumps(obj, separators=(",", ":")) + "\n")


def load_frames_jsonl(path):
    """Liest JSONL und liefert Liste von row-dicts.

    Fehlerhafte Zeilen (kein JSON, fehlende Felder, falsche Typen,
    ungueltiger Hex) werden ubersprungen. Leere Zeilen ebenfalls.
    Optionales "ts"/"ts_src": ungueltige oder fehlende Werte werden weggelassen,
    die Zeile bleibt gueltig (alte Dateien ohne ts).
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
                tx = obj["tx"]
                if isinstance(t, bool) or not isinstance(t, int):
                    continue
                if not isinstance(tx, bool):
                    continue
                can_id = _to_int(obj["id"])
                data = bytes.fromhex(obj["d"])
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            row = {"t": t, "id": can_id, "data": data, "tx": tx}
            ts = _ts_or_none(obj.get("ts"))
            if ts is not None:
                row["ts"] = ts
            ts_src = obj.get("ts_src")
            if isinstance(ts_src, str):
                row["ts_src"] = ts_src
            rows.append(row)
    return rows


def save_scan_csv(path, items, db):
    """Schreibt Scan-Ergebnisse als CSV.

    items: Liste von {"index": int|str, "raw": int|str, "status": str,
                      "switch": int|str (optional, Standard 0xFA)}
           (wie scan/result, Werte als int oder "0x..."-String).
    db: ElsterDb fuer name und Typ-Dekodierung (wert).
    Spalten: index;switch;name;raw;status;wert. switch: "" bei 0xFA, sonst min/max/f7.
    wert nur bei status "ok".
    """
    from elster import decode_value, switch_label  # lazy

    with open(path, "w", encoding=_CSV_ENCODING, newline="") as fh:
        writer = csv.writer(fh, delimiter=";", lineterminator="\r\n")
        writer.writerow(SCAN_CSV_HEADER)
        for item in items:
            index = _to_int(item["index"])
            raw = _to_int(item["raw"])
            switch = _to_int(item.get("switch", 0xFA))
            status = str(item.get("status", ""))
            wert = ""
            if status == "ok":
                sig = db.signal_by_index(index)
                type_name = sig.get("type") if sig else None
                has_bits = hasattr(db, "bits_for") and db.bits_for(index)
                if has_bits:
                    wert = db.decode(index, raw, type_name)
                elif type_name:
                    wert = decode_value(type_name, raw)
            writer.writerow(
                [
                    _hex_id(index, 4),
                    switch_label(switch),
                    db.signal_name(index),
                    _hex_id(raw, 4),
                    status,
                    wert,
                ]
            )
