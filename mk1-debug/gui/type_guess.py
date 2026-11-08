"""Typ-Heuristik und Auswertung der Automatik-Scans (kein Qt, kein paho).

Spezifikation: INTERFACES.md 7.4 und 7.5, plan/mk1-debug-auto-plan.md Schritt 3.
CLI: python -I type_guess.py <state.json>
Ausgabe: <prefix>_analyse.csv und <prefix>_analyse.md (prefix = state-Datei ohne "_state.json").

Hinweise zur Heuristik:
- Die Scores sind Plausibilitaets-Werte 0..1, keine Wahrscheinlichkeiten.
- Rohwert 0x8000 (nicht unterstuetzt) wird vor der Bewertung entfernt.
- Bei nur 0-Rohwerten werden alle Scores halbiert (0 ist fast ueberall moeglich).
- Abgleich mit der Uhr (et_zeit/et_datum) ist nicht umgesetzt: guess_types bekommt keine Uhrzeit.
- et_double_val und et_triple_val werden nicht aus Einzelwerten geschaetzt, sondern als
  Paar-Hinweis fuer benachbarte Indizes (_H/_L, _WH/_KWH/_MWH) ausgegeben.
- Min/Max (switch 0xF8/0xF9 aus state["minmax"]) werden nie in die Rohwerte gemischt. Liegen
  Min und Max im Bereich des Typs, bekommt der Kandidat +0.1 (max 1.0).
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

GUI_DIR = Path(__file__).resolve().parent
if str(GUI_DIR) not in sys.path:  # python -I startet ohne Skriptverzeichnis im Pfad
    sys.path.insert(0, str(GUI_DIR))

from elster import ElsterDb, NOT_SUPPORTED_RAW, decode_value  # noqa: E402

MKGIT_DEFAULT = Path(os.environ.get("ELSTER_TABLE_H", "ElsterTable.h"))  # optional: ElsterTable.h aus ha-stiebel-control
_MKGIT_RE = re.compile(
    r'^\s*\{\s*"([^"]+)"\s*,\s*(0[xX][0-9a-fA-F]+)\s*,\s*(et_\w+|0)\s*[,}]'
)
_PAIR_RULES = [("_H", "_L"), ("_WH", "_KWH"), ("_WH", "_MWH")]
SWITCH_DEFAULT = 0xFA
SWITCH_MIN = 0xF8
SWITCH_MAX = 0xF9
MINMAX_BONUS = 0.1


def _num(value) -> int:
    """'0x0112' / '0x180' / int -> int."""
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.lower().startswith("0x"):
        return int(text, 16)
    return int(text, 10)


def _s16(raw: int) -> int:
    return raw - 0x10000 if raw & 0x8000 else raw


def _sw(value) -> int:
    """Switch aus state-Feld ("0xf8", int, fehlt = 0xFA)."""
    return SWITCH_DEFAULT if value is None else _num(value)


def _minmax_plausible(name: str, lo: int, hi: int) -> bool | None:
    """Liegen Min (lo) und Max (hi) im Bereich des Typs? None, wenn der Typ keine Regel hat.
    Bereiche in Dezimal-Einheiten: et_dec_val /10 in -50..150, et_cent_val /100 in -10..20,
    et_mil_val /1000 in -5..5."""
    slo, shi = _s16(lo), _s16(hi)
    if name == "et_dec_val":
        return -500 <= slo and shi <= 1500 and slo <= shi
    if name == "et_cent_val":
        return -1000 <= slo and shi <= 2000 and slo <= shi
    if name == "et_mil_val":
        return -5000 <= slo and shi <= 5000 and slo <= shi
    if name == "et_bool":
        return lo in (0, 1) and hi in (0, 1) and lo <= hi
    if name == "et_little_bool":
        return lo in (0x0000, 0x0100) and hi in (0x0000, 0x0100) and lo <= hi
    if name == "et_byte":
        return lo >> 8 == 0 and hi >> 8 == 0 and lo <= hi
    if name == "zaehler":
        return lo <= hi
    return None


@lru_cache(maxsize=1)
def _betriebsart_count() -> int:
    return len(ElsterDb().betriebsart_list)


@lru_cache(maxsize=1)
def _reference_db() -> ElsterDb:
    return ElsterDb()


def load_mkgit_table(path: str | Path = MKGIT_DEFAULT) -> dict[int, tuple[str, str]]:
    """mk-git ElsterTable.h lesen (nur lesen). Index -> (name, type).

    Typ 0 wird als et_default gefuehrt. Bei doppelten Indizes gewinnt der erste Eintrag.
    """
    table: dict[int, tuple[str, str]] = {}
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        m = _MKGIT_RE.match(line)
        if not m:
            continue
        name, index_hex, type_name = m.group(1), m.group(2), m.group(3)
        if type_name == "0":
            type_name = "et_default"
        table.setdefault(int(index_hex, 16), (name, type_name))
    return table


def guess_types(raws: list[int],
                minmax: tuple[int, int] | None = None) -> list[tuple[str, float, str]]:
    """Typ-Kandidaten fuer eine Rohwert-Folge (chronologisch).

    minmax: optional (min_raw, max_raw) aus switch 0xF8/0xF9. Passt der Typ zu Min und Max,
    steigt sein Score um MINMAX_BONUS (max 1.0).
    Rueckgabe: [(typ, score 0..1, dekodiertes Beispiel)], absteigend nach Score.
    Leere Liste, wenn nach Entfernen von 0x8000 nichts uebrig bleibt.
    """
    values = [int(r) & 0xFFFF for r in raws if int(r) & 0xFFFF != NOT_SUPPORTED_RAW]
    if not values:
        return []

    distinct = set(values)
    all_zero = distinct == {0}
    betriebsart_n = _betriebsart_count()
    candidates: list[tuple[str, float]] = []

    def add(name: str, score: float) -> None:
        if score > 0:
            candidates.append((name, score))

    # et_bool / et_little_bool: nur 0/1 bzw. 0/0x0100
    if distinct <= {0x0000, 0x0001}:
        add("et_bool", 0.9 if 0x0001 in distinct else 0.3)
    if distinct <= {0x0000, 0x0100}:
        add("et_little_bool", 0.9 if 0x0100 in distinct else 0.3)

    # et_dec_val: int16 / 10 im Bereich -50 ... 150 (Temperaturen)
    if all(-500 <= _s16(v) <= 1500 for v in values):
        add("et_dec_val", 0.8)
    # et_cent_val / et_mil_val: int16 / 100 bzw. / 1000, Plausibilitaet -10 ... 20 bzw. -5 ... 5
    if all(-1000 <= _s16(v) <= 2000 for v in values):
        add("et_cent_val", 0.4)
    if all(-5000 <= _s16(v) <= 5000 for v in values):
        add("et_mil_val", 0.3)

    # et_byte: High-Byte 0, Wert <= 255
    if all(v >> 8 == 0 for v in values):
        add("et_byte", 0.5)

    # et_little_endian: Byte-getauscht kleiner als direkt (0x0100 = 1)
    swapped = [((v & 0xFF) << 8) | (v >> 8) for v in values]
    if all(sw < v for sw, v in zip(swapped, values)):
        add("et_little_endian", 0.6)

    # et_zeit (Stunde:Minute) und et_datum (Tag.Monat) mit Byte-Plausibilitaet
    if all((v >> 8) < 24 and (v & 0xFF) < 60 for v in values):
        add("et_zeit", 0.4)
    if all(1 <= (v >> 8) <= 31 and 1 <= (v & 0xFF) <= 12 for v in values):
        add("et_datum", 0.3)

    # et_betriebsart: Low-Byte 0, High-Byte in betriebsart_list
    if all((v & 0xFF) == 0 and (v >> 8) < betriebsart_n for v in values):
        add("et_betriebsart", 0.7)

    # Zaehler: nur steigend ueber mehrere Rohwerte (Reihenfolge wie uebergeben)
    if len(values) >= 2:
        non_decreasing = all(b >= a for a, b in zip(values, values[1:]))
        if non_decreasing and values[-1] > values[0]:
            add("zaehler", 0.9 if len(values) >= 3 else 0.6)

    factor = 0.5 if all_zero else 1.0
    result: list[tuple[str, float, str]] = []
    for name, score in candidates:
        if name == "zaehler":
            sample = str(values[-1])
        else:
            sample = decode_value(name, values[-1])
        final = min(score * factor, 1.0)
        if minmax is not None and _minmax_plausible(name, minmax[0] & 0xFFFF,
                                                    minmax[1] & 0xFFFF):
            final = min(final + MINMAX_BONUS, 1.0)
        result.append((name, round(final, 2), sample))
    result.sort(key=lambda item: (-item[1], item[0]))
    return result


def _parse_state(state: dict) -> tuple[dict, dict, dict, dict]:
    """Liefert:
    raws[(member, index, switch)] -> Liste aus Scan- und Reread-Rohwerten (ok); switch 0xFA = Standard,
    stage_items[(member, stage, format, switch)] -> {index: (status, raw)},
    stage_counts[(member, stage, format, switch)] -> {"tasks", "done", "ok", "unsupported", "ohne_antwort"},
    minmax[(member, index)] -> {"min": raw, "max": raw} aus state["minmax"] (nur status ok).
    """
    raws: dict[tuple[int, int, int], list[int]] = {}
    stage_items: dict[tuple[int, str, str, int], dict[int, tuple[str, int]]] = {}
    stage_counts: dict[tuple[int, str, str, int], dict[str, int]] = {}
    minmax: dict[tuple[int, int], dict[str, int]] = {}

    for task in state.get("tasks", []):
        member = _num(task["member"])
        sw = _sw(task.get("switch"))
        key = (member, task.get("stage", "?"), task.get("format", "auto"), sw)
        counts = stage_counts.setdefault(
            key, {"tasks": 0, "done": 0, "ok": 0, "unsupported": 0, "ohne_antwort": 0}
        )
        counts["tasks"] += 1
        if task.get("done"):
            counts["done"] += 1
        span = _num(task["to"]) - _num(task["from"]) + 1
        items_map = stage_items.setdefault(key, {})
        answered = 0
        for item in task.get("items", []):
            status = item.get("status", "")
            index = _num(item["index"])
            raw = _num(item["raw"])
            items_map[index] = (status, raw)
            if status == "ok":
                counts["ok"] += 1
                raws.setdefault((member, index, sw), []).append(raw)
                answered += 1
            elif status == "unsupported":
                counts["unsupported"] += 1
                answered += 1
        if task.get("done"):
            counts["ohne_antwort"] += max(0, span - answered)

    for rr in state.get("rereads", []):
        if rr.get("status") != "ok":
            continue
        key = (_num(rr["member"]), _num(rr["index"]), _sw(rr.get("switch")))
        raws.setdefault(key, []).append(_num(rr["raw"]))

    for entry in state.get("minmax", []):
        if entry.get("status") != "ok" or entry.get("raw") is None:
            continue
        sw = _sw(entry.get("switch"))
        if sw == SWITCH_MIN:
            side = "min"
        elif sw == SWITCH_MAX:
            side = "max"
        else:
            continue
        pair = minmax.setdefault((_num(entry["member"]), _num(entry["index"])), {})
        pair[side] = _num(entry["raw"])

    return raws, stage_items, stage_counts, minmax


def _pair_hints(names: dict[tuple[int, int], str], present: set[tuple[int, int]]) -> list[dict]:
    hints = []
    for (member, index), name in sorted(names.items()):
        nxt = (member, index + 1)
        if nxt not in names or nxt not in present or (member, index) not in present:
            continue
        name_b = names[nxt]
        for suffix_a, suffix_b in _PAIR_RULES:
            if name.endswith(suffix_a) and name_b.endswith(suffix_b):
                base_a = name[: -len(suffix_a)]
                base_b = name_b[: -len(suffix_b)]
                if base_a == base_b:
                    hints.append({
                        "member": member, "index": index, "index_b": index + 1,
                        "name_a": name, "name_b": name_b,
                        "regel": f"{suffix_a}/{suffix_b}",
                    })
                    break
    return hints


def _hex4(value: int) -> str:
    return f"0x{value:04x}"


def _md_table(headers: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return lines


def analyse(state_json_path: str, out_prefix: str) -> tuple[str, str]:
    """state.json auswerten. Schreibt <prefix>_analyse.csv und <prefix>_analyse.md."""
    state_path = Path(state_json_path)
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)

    db = _reference_db()
    mkgit = load_mkgit_table()
    mkgit_found = bool(mkgit)
    raws, stage_items, stage_counts, minmax = _parse_state(state)
    # Tabellenzeilen nur fuer den Standard-Switch; Min/Max stehen in den Spalten min/max
    base_keys = sorted({(m, i) for (m, i, sw) in raws if sw == SWITCH_DEFAULT})

    # Namen und Typen aus Referenz und mk-git
    ref_name: dict[int, str] = {}
    ref_type: dict[int, str] = {}
    for index in {idx for (_m, idx) in base_keys}:
        sig = db.signal_by_index(index)
        if sig:
            ref_name[index] = sig["name"]
            ref_type[index] = sig["type"]

    rows = []
    names_for_pairs: dict[tuple[int, int], str] = {}
    for (member, index) in base_keys:
        values = raws[(member, index, SWITCH_DEFAULT)]
        name_mk, type_mk = mkgit.get(index, ("", ""))
        name_ref = ref_name.get(index, "")
        type_ref = ref_type.get(index, "")
        if name_ref:
            names_for_pairs[(member, index)] = name_ref
        elif name_mk:
            names_for_pairs[(member, index)] = name_mk
        if len(values) >= 2:
            konstant = "ja" if len(set(values)) == 1 else "nein"
        else:
            konstant = "unbekannt"
        mm = minmax.get((member, index), {})
        pair = (mm["min"], mm["max"]) if "min" in mm and "max" in mm else None
        cands = guess_types(values, pair)
        conflict = ""
        if type_ref and type_mk and type_ref != type_mk and type_mk != "et_default" \
                and type_ref != "et_default":
            conflict = f"ja: ref={type_ref} mkgit={type_mk}"
        rows.append({
            "member": f"0x{member:03x}",
            "member_name": db.member_name(member),
            "index": _hex4(index),
            "name_ref": name_ref,
            "type_ref": type_ref,
            "name_mkgit": name_mk,
            "type_mkgit": type_mk,
            "raws": ",".join(_hex4(v) for v in values),
            "min": _hex4(mm["min"]) if "min" in mm else "",
            "max": _hex4(mm["max"]) if "max" in mm else "",
            "konstant": konstant,
            "kandidaten": " | ".join(f"{n}:{s:.2f} ({smp})" for n, s, smp in cands),
            "konflikt": conflict,
            "_cands": cands,
            "_values": values,
            "_minmax": pair,
            "_member_int": member,
            "_index_int": index,
        })

    csv_path = f"{out_prefix}_analyse.csv"
    csv_cols = ["member", "member_name", "index", "name_ref", "type_ref", "name_mkgit",
                "type_mkgit", "raws", "min", "max", "konstant", "kandidaten", "konflikt"]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(csv_cols)
        for r in rows:
            writer.writerow([r[c] for c in csv_cols])

    present = {(r["_member_int"], r["_index_int"]) for r in rows}
    md = _build_markdown(state_path, state, rows, stage_items, stage_counts,
                         names_for_pairs, present, mkgit_found)
    md_path = f"{out_prefix}_analyse.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    return csv_path, md_path


def _build_markdown(state_path, state, rows, stage_items, stage_counts,
                    names_for_pairs, present, mkgit_found) -> list[str]:
    db = _reference_db()
    md: list[str] = []
    md.append(f"# Automatik-Analyse: {state_path.name}")
    md.append("")
    md.append(f"Stand: gestartet {state.get('started')}, beendet {state.get('finished')}, "
              f"Pause {state.get('delay_ms')} ms")
    md.append("")

    members = sorted({_num(m) for m in state.get("members", [])}
                     | {r["_member_int"] for r in rows}
                     | {k[0] for k in stage_counts})
    md.append("## Zusammenfassung")
    md.append("")
    md.append(f"- Member: {', '.join(f'0x{m:03x} {db.member_name(m)}' for m in members) or '-'}")
    silent = state.get("silent", [])
    md.append(f"- Passiv gesehen, ohne Probe-Antwort (silent): {', '.join(silent) or '-'}")
    konst = sum(1 for r in rows if r["konstant"] == "ja")
    veraend = sum(1 for r in rows if r["konstant"] == "nein")
    konflikte = sum(1 for r in rows if r["konflikt"])
    unbekannt = sum(1 for r in rows if not r["name_ref"] and not r["name_mkgit"])
    md.append(f"- Indizes mit Antwort ungleich 0x8000: {len(rows)} "
              f"(konstant {konst}, veraenderlich {veraend}, ohne Mehrfachlesung "
              f"{len(rows) - konst - veraend})")
    md.append(f"- Typkonflikte Referenz vs. mk-git: {konflikte}")
    md.append(f"- Ohne Namen (weder Referenz noch mk-git): {unbekannt}")
    md.append("")

    md.append("## Treffer je Stufe und Member")
    md.append("")
    stage_rows = []
    for key in sorted(stage_counts):
        member, stage, fmt, sw = key
        c = stage_counts[key]
        stage_rows.append([f"0x{member:03x}", stage, fmt, f"0x{sw:02x}", c["tasks"], c["done"],
                           c["ok"], c["unsupported"], c["ohne_antwort"]])
    if stage_rows:
        md += _md_table(["Member", "Stufe", "Format", "Switch", "Teil-Scans", "fertig", "ok",
                         "unsupported (0x8000)", "ohne Antwort"], stage_rows)
    else:
        md.append("Keine Scan-Tasks im State.")
    md.append("")

    md.append("## Vergleich short/long fuer 0x00-0xFF")
    md.append("")
    any_cmp = False
    for member in members:
        short = stage_items.get((member, "A1", "short", SWITCH_DEFAULT))
        long_ = stage_items.get((member, "A2", "long", SWITCH_DEFAULT))
        if short is None and long_ is None:
            continue
        any_cmp = True
        if short is None or long_ is None:
            fehlt = "A1 (short)" if short is None else "A2 (long)"
            md.append(f"**Member 0x{member:03x} {db.member_name(member)}**: Stufe {fehlt} "
                      f"fehlt, kein Vergleich moeglich")
            md.append("")
            continue
        same = diff = only_one = 0
        diff_list = []
        for index in range(0x100):
            s = short.get(index) if short else None
            l_ = long_.get(index) if long_ else None
            s_ok = s is not None and s[0] in ("ok", "unsupported")
            l_ok = l_ is not None and l_[0] in ("ok", "unsupported")
            if s_ok and l_ok:
                if s[1] == l_[1]:
                    same += 1
                else:
                    diff += 1
                    diff_list.append(f"{_hex4(index)} short={_hex4(s[1])} long={_hex4(l_[1])}")
            elif s_ok or l_ok:
                only_one += 1
        md.append(f"**Member 0x{member:03x} {db.member_name(member)}**: gleich {same}, "
                  f"abweichend {diff}, nur in einem Format {only_one}")
        md.append("")
        if diff_list:
            md.append("Abweichend: " + "; ".join(diff_list))
            md.append("")
    if not any_cmp:
        md.append("Keine Stufen A1/A2 im State, kein Vergleich moeglich.")
        md.append("")

    md.append("## Min/Max (switch 0xF8/0xF9)")
    md.append("")
    mm_rows = []
    for r in rows:
        if not r["min"] and not r["max"]:
            continue
        pair = r["_minmax"]
        plaus = "-"
        if pair is not None:
            names = [n for n, _s, _x in r["_cands"] if _minmax_plausible(n, *pair)]
            plaus = ", ".join(names) or "-"
        mm_rows.append([r["member"], r["index"], r["name_ref"] or r["name_mkgit"],
                        r["min"] or "-", r["max"] or "-", plaus])
    if mm_rows:
        md += _md_table(["Member", "Index", "Name", "min", "max", "Typ passt (Bonus)"], mm_rows)
    else:
        md.append("Keine Min/Max-Werte im State.")
    md.append("")

    md.append("## Typkonflikte Referenz vs. mk-git")
    md.append("")
    conf_rows = [[r["member"], r["index"], r["name_ref"] or r["name_mkgit"], r["type_ref"],
                  r["type_mkgit"], r["raws"]] for r in rows if r["konflikt"]]
    if conf_rows:
        md += _md_table(["Member", "Index", "Name", "Typ Referenz", "Typ mk-git", "Rohwerte"],
                        conf_rows)
    else:
        md.append("Keine.")
    if not mkgit_found:
        md.append("")
        md.append("Hinweis: mk-git ElsterTable.h nicht gefunden, kein Abgleich.")
    md.append("")

    md.append("## Unbekannte Indizes mit Kandidaten")
    md.append("")
    unk_rows = []
    for r in rows:
        if r["name_ref"] or r["name_mkgit"]:
            continue
        top = " | ".join(f"{n}:{s:.2f} ({smp})" for n, s, smp in r["_cands"][:3])
        unk_rows.append([r["member"], r["index"], r["raws"], r["konstant"], top or "-"])
    if unk_rows:
        md += _md_table(["Member", "Index", "Rohwerte", "konstant", "Top-Kandidaten"], unk_rows)
    else:
        md.append("Keine.")
    md.append("")

    md.append("## Paar-Hinweise (benachbarte Indizes)")
    md.append("")
    hints = _pair_hints(names_for_pairs, present)
    if hints:
        md += _md_table(
            ["Member", "Index", "Index+1", "Name", "Name+1", "Regel"],
            [[f"0x{h['member']:03x}", _hex4(h["index"]), _hex4(h["index_b"]),
              h["name_a"], h["name_b"], h["regel"]] for h in hints],
        )
    else:
        md.append("Keine.")
    md.append("")
    return md


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Aufruf: python -I type_guess.py <state.json>", file=sys.stderr)
        return 2
    state_path = Path(argv[1])
    base = state_path.name
    if base.endswith("_state.json"):
        base = base[: -len("_state.json")]
    else:
        base = state_path.stem
    out_prefix = str(state_path.with_name(base))
    csv_path, md_path = analyse(str(state_path), out_prefix)
    print(f"CSV: {csv_path}")
    print(f"MD:  {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
