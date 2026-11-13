"""Zwei Tabellen aus dem Scan-State: bekannte (Referenz) und unbekannte Elster-IDs, Werte je CAN-Member.
Aufruf: python -I analyse_tables.py <state.json>
Ausgabe: <prefix>_bekannt.csv/.md, <prefix>_unbekannt.csv/.md und <prefix>_verfuegbarkeit.csv/.md
"""
import csv
import json
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "gui"))
sys.path.insert(0, HERE)
from elster import ElsterDb, decode_value  # noqa: E402
from type_guess import guess_types, load_mkgit_table  # noqa: E402
from latency import latencies, stats  # noqa: E402

ALL_TYPES = ["et_dec_val", "et_cent_val", "et_mil_val", "et_byte", "et_bool", "et_little_bool",
             "et_little_endian", "et_betriebsart", "et_zeit", "et_datum", "et_time_domain",
             "et_dev_nr", "et_dev_id", "et_err_nr", "et_default"]
SHORT = {t: t[3:] for t in ALL_TYPES}
UNSUP = 0x8000
# Kategorie "Verfuegbarkeit": Indizes, die der Manager beim Boot als 0x0001 (vorhanden) / 0x8000 (nicht vorhanden)
# an das Bedienteil meldet. Grenzen = kleinster und groesster beobachteter Index je Block, Quelle
# daten/can-logs/boot_20261113_verfuegbarkeit.md, keine geschaetzten Bereiche.
VERFUEGBARKEIT_BLOECKE = [(40002, 40127), (40901, 40923), (41001, 41064), (44001, 44033), (46001, 46019), (48001, 48035)]


def ist_verfuegbarkeit(idx):
    return any(lo <= idx <= hi for lo, hi in VERFUEGBARKEIT_BLOECKE)


def num(v):
    return v if isinstance(v, int) else int(str(v), 16)


def s16(v):
    return v - 0x10000 if v >= 0x8000 else v


def load(path):
    st = json.load(open(path, encoding="utf-8"))
    raws = defaultdict(list)       # (member, index) -> [raw ok in Reihenfolge]
    unsup = set()
    for t in st["tasks"]:
        sw = num(t.get("switch", 0xFA)) if t.get("switch") is not None else 0xFA
        if sw != 0xFA:
            continue
        m = num(t["member"])
        for it in t.get("items", []):
            k = (m, num(it["index"]))
            if it["status"] == "ok":
                raws[k].append(num(it["raw"]))
            elif it["status"] == "unsupported":
                unsup.add(k)
    for r in st.get("rereads", []):
        if r.get("status") == "ok" and num(r.get("switch", 0xFA) or 0xFA) == 0xFA:
            raws[(num(r["member"]), num(r["index"]))].append(num(r["raw"]))
    mm = defaultdict(dict)
    for e in st.get("minmax", []):
        if e["status"] == "ok":
            mm[(num(e["member"]), num(e["index"]))]["min" if e["switch"].lower() == "0xf8" else "max"] = num(e["raw"])
    members = sorted({num(x) for x in st["members"]} if not isinstance(st["members"], dict)
                     else {num(x) for x in st["members"]})
    return raws, unsup, mm, [m for m in members]


def fmt_val(tname, raw, mmv=None):
    txt = f"{decode_value(tname, raw)} [0x{raw:04x}]"
    if mmv and ("min" in mmv or "max" in mmv):
        lo = decode_value(tname, mmv["min"]) if "min" in mmv else "-"
        hi = decode_value(tname, mmv["max"]) if "max" in mmv else "-"
        txt += f" (min {lo} / max {hi})"
    return txt


def lat_rows(cols_m):
    lat, sent = latencies()
    st = {m: stats(lat.get(m, []), sent.get(m, 0)) for m in cols_m}
    def g(m, k):
        return "" if not st[m] else st[m][k]
    return [["", "ANTWORTZEIT Median ms", "", "", "", ""] + [g(m, "med") for m in cols_m],
            ["", "ANTWORTZEIT p90 ms", "", "", "", ""] + [g(m, "p90") for m in cols_m],
            ["", "ANTWORTZEIT max ms", "", "", "", ""] + [g(m, "max") for m in cols_m],
            ["", "Antwortquote % (beantwortet/gesendet)", "", "", "", ""] + [("" if not st[m] else f"{st[m]['quote']} ({st[m]['n']}/{st[m]['sent']})") for m in cols_m]], st


def write_md(path, title, headers, rows, note=""):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n{note}\n\n| " + " | ".join(headers) + " |\n|" + "---|" * len(headers) + "\n")
        for r in rows:
            f.write("| " + " | ".join(str(c).replace("|", "/") for c in r) + " |\n")


def main(path):
    raws, unsup, mm, members = load(path)
    db = ElsterDb()
    mk = load_mkgit_table()
    answered_members = sorted({m for m, _ in raws})
    cols = [f"0x{m:03x}" for m in answered_members]
    base = os.path.splitext(path)[0].replace("_state", "")

    # ---- Tabelle 1: Referenz-IDs ----
    ref_idx = {}
    for s in db.signals:
        if s.get("sentinel"):
            continue
        ref_idx.setdefault(num(s["index"]), s)
    lrows, lst = lat_rows(answered_members)
    rows1 = []
    for idx, s in sorted(ref_idx.items()):
        typ = s["type"]
        vals = []
        for m in answered_members:
            r = raws.get((m, idx))
            vals.append(fmt_val(typ, r[-1], mm.get((m, idx))) if r else ("nicht unterst." if (m, idx) in unsup else ""))
        mkn = mk.get(idx, ("", ""))
        rows1.append([f"0x{idx:04x}", s["name"], typ, s.get("owner_name", ""), mkn[0],
                      mkn[1]] + vals)
    h1 = ["index", "name_ref", "typ_ref", "owner_ref", "name_mkgit", "typ_mkgit"] + cols
    with open(base + "_bekannt.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\r\n")
        w.writerow(h1)
        w.writerows(lrows)
        w.writerows(rows1)
    write_md(base + "_bekannt.md", f"Referenz-IDs ({len(rows1)}) mit Wert je CAN-Member",
             h1, lrows + rows1, "Wert = dekodiert nach Referenz-Typ [Rohwert], letzter ok-Wert, dahinter Min/Max (Switch F8/F9) im selben Typ dekodiert. Leer = keine Antwort, "
             "'nicht unterst.' = 0x8000. 0x500 ist wegen 100-ms-Timeout unvollstaendig.")

    # ---- Tabelle 2: unbekannte IDs ----
    unk = sorted({i for (_, i) in raws if i not in ref_idx})
    rows2 = []
    for idx in unk:
        mkn = mk.get(idx)
        name, typ = mkn if mkn else ("", "")
        per = []
        allraw = []
        for m in answered_members:
            r = raws.get((m, idx))
            if r:
                allraw += r
                if typ and typ != "et_default":
                    per.append(f"0x{r[-1]:04x} " + f"({fmt_val(typ, r[-1], mm.get((m, idx)))})".replace(f" [0x{r[-1]:04x}]", ""))
                else:
                    mv = mm.get((m, idx))
                    per.append(f"0x{r[-1]:04x}" + (f" (min 0x{mv['min']:04x} / max 0x{mv['max']:04x})" if mv and len(mv) == 2 else ""))
            else:
                per.append("")
        first = next((raws[(m, idx)][-1] for m in answered_members if raws.get((m, idx))), None)
        # Min/Max vom ersten Member mit Antwort
        mmv = next((mm[(m, idx)] for m in answered_members if (m, idx) in mm), None)
        gt = typ if typ and typ != "et_default" else None
        if mmv and not gt:
            gg = guess_types(allraw, (mmv["min"], mmv["max"]) if len(mmv) == 2 else None)
            gt = gg[0][0] if gg else None
        if mmv:
            lo = decode_value(gt, mmv["min"]) if gt and "min" in mmv else (f"0x{mmv['min']:04x}" if "min" in mmv else "-")
            hi = decode_value(gt, mmv["max"]) if gt and "max" in mmv else (f"0x{mmv['max']:04x}" if "max" in mmv else "-")
            mmtxt = f"{lo}..{hi}" + (f" ({SHORT.get(gt, gt)})" if gt else "")
        else:
            mmtxt = ""
        if mkn and typ != "et_default":
            alle = ""
            guess = ""
        else:
            alle = " | ".join(f"{SHORT[t]}={decode_value(t, first)}" for t in ALL_TYPES)
            g = guess_types(allraw, (mmv["min"], mmv["max"]) if mmv and len(mmv) == 2 else None)
            guess = ", ".join(f"{n}:{s}" for n, s, _ in g[:3])
        rows2.append([f"0x{idx:04x}", name or "(unbekannt)", typ, sum(1 for c in per if c), mmtxt, guess, alle] + per)
    h2 = ["index", "name_mkgit", "typ_mkgit", "n_member", "min..max", "typ_vermutung", "alle_typen(erster Wert)"] + cols
    rowsV = [r for r in rows2 if ist_verfuegbarkeit(int(r[0], 16))]
    rows2 = [r for r in rows2 if not ist_verfuegbarkeit(int(r[0], 16))]
    with open(base + "_unbekannt.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\r\n")
        w.writerow(h2)
        w.writerows([[r[0], r[1], r[2], r[3], "", "", ""] + r[6:] for r in lrows])
        w.writerows(rows2)
    write_md(base + "_unbekannt.md", f"Unbekannte IDs ({len(rows2)}), Name aus mk-git (wpl10ac)", h2, [[r[0], r[1], r[2], r[3], "", "", ""] + r[6:] for r in lrows] + rows2,
             "Mit mk-git-Typ (nicht et_default): Wert in Klammern dekodiert. Sonst alle Typen probiert (Spalte "
             "alle_typen, Wert des ersten antwortenden Members) und Typ-Vermutung aus allen Rohwerten + Min/Max.")
    with open(base + "_verfuegbarkeit.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\r\n")
        w.writerow(h2)
        w.writerows([[r[0], r[1], r[2], r[3], "", "", ""] + r[6:] for r in lrows])
        w.writerows(rowsV)
    write_md(base + "_verfuegbarkeit.md", f"Verfuegbarkeits-Indizes ({len(rowsV)}) aus dem Boot-Burst, Name aus mk-git", h2, [[r[0], r[1], r[2], r[3], "", "", ""] + r[6:] for r in lrows] + rowsV,
             "Quelle: daten/can-logs/boot_20261113_verfuegbarkeit.md (Boot-Burst boot_20261113). 0x0001 = vorhanden, 0x8000 = nicht vorhanden. "
             "Gleiche Zeilenbildung wie unbekannt.")
    named = sum(1 for r in rows2 if r[1] != "(unbekannt)")
    print("bekannt:", len(rows1), "mit Wert:", sum(1 for r in rows1 if any(c and c != 'nicht unterst.' for c in r[6:])))
    print("unbekannt:", len(rows2), "davon Name in mk-git:", named, "ohne Namen:", len(rows2) - named)
    print("verfuegbarkeit:", len(rowsV))
    print("Member-Spalten:", cols)
    print(base + "_bekannt.csv", base + "_unbekannt.csv", base + "_verfuegbarkeit.csv")


if __name__ == "__main__":
    main(sys.argv[1])
