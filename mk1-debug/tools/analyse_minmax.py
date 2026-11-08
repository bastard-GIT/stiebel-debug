"""Auswertung state["minmax"] (Switch 0xF8 min / 0xF9 max) -> logs/<prefix>_minmax.csv und .md.
Aufruf: python -I analyse_minmax.py <state.json>
"""
import collections
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "gui"))
from elster import ElsterDb  # noqa: E402

UNLIM = (-32767, 32767)


def s16(v):
    return v - 0x10000 if v >= 0x8000 else v


def main(path):
    st = json.load(open(path, encoding="utf-8"))
    db = ElsterDb()
    stat = collections.Counter()
    pairs = {}
    for e in st["minmax"]:
        key = (int(e["member"], 16), int(e["index"], 16))
        side = {"0xf8": "min", "0xf9": "max"}[e["switch"].lower()]
        stat[(key[0], e["status"])] += 1
        pairs.setdefault(key, {})[side] = (e["status"], int(e["raw"], 16) if e.get("raw") is not None else None)
    rows = []
    for (m, i), p in sorted(pairs.items()):
        smin, rmin = p.get("min", ("fehlt", None))
        smax, rmax = p.get("max", ("fehlt", None))
        if smin == "ok" and smax == "ok":
            lo, hi = s16(rmin), s16(rmax)
            art = "unbegrenzt" if (lo, hi) == UNLIM else ("min>max" if lo > hi else "Grenzen")
        elif smin == "ok" or smax == "ok":
            lo = s16(rmin) if smin == "ok" else ""
            hi = s16(rmax) if smax == "ok" else ""
            art = "nur eine Seite"
        elif "timeout" in (smin, smax):
            lo = hi = ""
            art = "timeout"
        else:
            lo = hi = ""
            art = "keine Grenzen"
        rows.append((m, i, db.signal_name(i), art, lo, hi, rmin, rmax))
    base = os.path.splitext(path)[0].replace("_state", "")
    with open(base + "_minmax.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\r\n")
        w.writerow(["member", "index", "name", "art", "min", "max", "raw_min", "raw_max"])
        for m, i, n, a, lo, hi, rl, rh in rows:
            w.writerow([f"0x{m:03x}", f"0x{i:04x}", n, a, lo, hi,
                        "" if rl is None else f"0x{rl:04x}", "" if rh is None else f"0x{rh:04x}"])
    cnt = collections.Counter(r[3] for r in rows)
    bym = collections.defaultdict(collections.Counter)
    for r in rows:
        bym[r[0]][r[3]] += 1
    out = ["# Min/Max-Auswertung (Switch 0xF8 / 0xF9)", "",
           f"Register: {len(rows)}, Reads: {len(st['minmax'])}", "", "| Art | Anzahl |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in cnt.most_common()]
    out += ["", "## je Teilnehmer", "", "| Member | Name | Register | Grenzen | unbegrenzt | min>max | eine Seite | keine | timeout |", "|---|---|---|---|---|---|---|---|---|"]
    for m, c in sorted(bym.items()):
        out.append(f"| 0x{m:03x} | {db.member_name(m)} | {sum(c.values())} | {c['Grenzen']} | {c['unbegrenzt']} | {c['min>max']} | {c['nur eine Seite']} | {c['keine Grenzen']} | {c['timeout']} |")
    for art in ("Grenzen", "min>max", "nur eine Seite", "timeout"):
        sel = [r for r in rows if r[3] == art]
        if not sel:
            continue
        out += ["", f"## {art} ({len(sel)})", "", "| Member | Index | Name | min | max |", "|---|---|---|---|---|"]
        for m, i, n, a, lo, hi, *_ in sel[:400]:
            out.append(f"| 0x{m:03x} | 0x{i:04x} | {n} | {lo} | {hi} |")
    open(base + "_minmax.md", "w", encoding="utf-8").write("\n".join(out) + "\n")
    print(base + "_minmax.md")
    for k, v in cnt.most_common():
        print(k, v)
    for m, c in sorted(bym.items()):
        print(hex(m), dict(c))


if __name__ == "__main__":
    main(sys.argv[1])
