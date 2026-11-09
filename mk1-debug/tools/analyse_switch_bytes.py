"""Auswertung aller Frames mit data[2] (3. Byte) zwischen 0xF0 und 0xFF.

Liest alle auto_*_rx.jsonl aus mk1-debug/logs (oder die als Argument uebergebenen Dateien)
und schreibt logs/switch_bytes_analyse.md. Aufruf:
    python -I analyse_switch_bytes.py [datei.jsonl ...]
"""

import collections
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.environ.get("STIEBEL_LOG_DIR") or os.path.join(os.path.dirname(HERE), "logs")
OWN = 0x680
PAIR_WINDOW_MS = 1500
LO, HI = 0xF0, 0xFF
MSG = {0: "write", 1: "read", 2: "response", 3: "ack", 4: "write-ack", 5: "write-resp", 6: "system", 7: "system-resp"}


def load(files):
    frames = []
    for path in files:
        n = 0
        start = len(frames)
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    d = bytes.fromhex(r["d"])
                    frames.append({"t": int(r["t"]), "u": int(r.get("u") or 0), "id": int(r["id"], 16), "d": d,
                                   "tx": bool(r["tx"]), "src": os.path.basename(path)})
                    n += 1
                except (ValueError, KeyError, TypeError):
                    continue
        # Zeitquelle pro Datei einheitlich (Paare liegen innerhalb einer Datei): u (Unix ms) nur wenn alle Frames u > 0 haben, sonst t
        file_frames = frames[start:]
        use_u = bool(file_frames) and all(f["u"] > 0 for f in file_frames)
        for f in file_frames:
            if use_u:
                f["t"] = f["u"]
            del f["u"]
        print(f"{os.path.basename(path)}: {n} Frames" + (" (Zeit: u)" if use_u else " (Zeit: t)"))
    # pro Datei ist t monoton (ms seit ESP-Boot); Dateien einzeln halten
    return frames


def receiver(d):
    return ((d[0] & 0xF0) << 3) | d[1]


def hx(b):
    return " ".join(f"{x:02x}" for x in b)


def top(counter, n=8):
    return ", ".join(f"`{k}` x{v}" for k, v in counter.most_common(n))


def main():
    files = sys.argv[1:] or sorted(glob.glob(os.path.join(LOGS, "auto_*_rx.jsonl")))
    frames = load(files)
    sel = [i for i, f in enumerate(frames) if len(f["d"]) >= 7 and LO <= f["d"][2] <= HI]
    out = []
    w = out.append
    w("# Auswertung Frames mit 3. Byte (data[2]) 0xF0-0xFF")
    w("")
    w(f"Quelle: {len(files)} Datei(en), {len(frames)} Frames, davon {len(sel)} mit data[2] in 0x{LO:02x}-0x{HI:02x}.")
    w("")

    by_sw = collections.defaultdict(list)
    for i in sel:
        by_sw[frames[i]["d"][2]].append(i)

    w("## Uebersicht")
    w("")
    w("| Switch | Frames | TX (0x680) | RX | davon Antworten (Art 2) | Absender (RX) |")
    w("|---|---|---|---|---|---|")
    for sw in sorted(by_sw):
        idx = by_sw[sw]
        tx = sum(1 for i in idx if frames[i]["tx"])
        rx = len(idx) - tx
        resp = sum(1 for i in idx if not frames[i]["tx"] and (frames[i]["d"][0] & 0x0F) == 2)
        senders = collections.Counter(hex(frames[i]["id"]) for i in idx if not frames[i]["tx"])
        w(f"| 0x{sw:02x} | {len(idx)} | {tx} | {rx} | {resp} | {top(senders, 5)} |")
    w("")

    for sw in sorted(by_sw):
        idx = by_sw[sw]
        w(f"## Switch 0x{sw:02x}")
        w("")
        # Art-Verteilung je Richtung
        for direction, label in ((True, "TX (unsere Requests)"), (False, "RX (alles andere)")):
            sub = [i for i in idx if frames[i]["tx"] == direction]
            if not sub:
                continue
            arts = collections.Counter(MSG.get(frames[i]["d"][0] & 0x0F, "?") for i in sub)
            w(f"**{label}**: {len(sub)} Frames, Nachrichtenart: {top(arts)}")
            # Layout der Folgebytes d3..d6
            pat = collections.Counter(hx(frames[i]["d"][3:7]) for i in sub)
            w(f"- Folgebytes d3..d6, haeufigste Muster ({len(pat)} verschiedene): {top(pat, 6)}")
            for pos, name in ((3, "d3"), (4, "d4"), (5, "d5"), (6, "d6")):
                vals = collections.Counter(frames[i]["d"][pos] for i in sub)
                w(f"- {name}: {len(vals)} verschiedene Werte" +
                  (f", immer 0x{next(iter(vals)):02x}" if len(vals) == 1 else f", Top: " + ", ".join(f"0x{k:02x} x{v}" for k, v in vals.most_common(4))))
            w("")

        # Request/Antwort-Paare: TX-Request -> erste Antwort (Art 2) des Zielmembers mit gleichem Switch-Byte
        pairs = []
        for i in idx:
            f = frames[i]
            if not f["tx"]:
                continue
            tgt = receiver(f["d"])
            for j in range(i + 1, min(i + 400, len(frames))):
                g = frames[j]
                if g["src"] != f["src"] or g["t"] - f["t"] > PAIR_WINDOW_MS:
                    break
                if g["tx"] or g["id"] != tgt or len(g["d"]) < 7:
                    continue
                if (g["d"][0] & 0x0F) == 2 and g["d"][2] == sw:
                    pairs.append((f, g, g["t"] - f["t"]))
                    break
        if pairs:
            echo = sum(1 for f, g, _ in pairs if f["d"][3:5] == g["d"][3:5])
            w(f"**Request/Antwort-Paare**: {len(pairs)} von {sum(1 for i in idx if frames[i]['tx'])} Requests mit Antwort gleichen Switch-Bytes")
            w(f"- d3/d4 der Antwort == d3/d4 des Requests (Index-Echo): {echo} von {len(pairs)}")
            val_long = collections.Counter(f"{g['d'][5]:02x}{g['d'][6]:02x}" for _, g, _ in pairs)
            val_short = collections.Counter(f"{g['d'][3]:02x}{g['d'][4]:02x}" for _, g, _ in pairs)
            w(f"- Wert falls Langlayout (d5/d6): {top(val_long, 6)}")
            w(f"- Wert falls Kurzlayout (d3/d4): {top(val_short, 6)}")
            lat = sorted(x for _, _, x in pairs)
            w(f"- Latenz ms: Median {lat[len(lat) // 2]}, p90 {lat[int(len(lat) * 0.9)]}, max {lat[-1]}")
            w("")
        # Beispiele
        ex = collections.OrderedDict()
        for i in idx:
            key = (frames[i]["tx"], frames[i]["d"][0] & 0x0F)
            ex.setdefault(key, i)
        w("Beispiele:")
        for (tx, art), i in ex.items():
            f = frames[i]
            w(f"- {'TX' if tx else 'RX'} 0x{f['id']:03x} {MSG.get(art, '?')}: `{hx(f['d'])}`")
        w("")

    # Frames von fremden Absendern (nicht 0x680 und keine Antworten auf uns)
    foreign = [i for i in sel if not frames[i]["tx"] and (frames[i]["d"][0] & 0x0F) != 2]
    w("## Fremder Verkehr mit Switch-Byte (keine Antworten)")
    w("")
    if foreign:
        c = collections.Counter((hex(frames[i]["id"]), f"0x{frames[i]['d'][2]:02x}", MSG.get(frames[i]["d"][0] & 0x0F, "?")) for i in foreign)
        for (src, sw, art), n in c.most_common(30):
            w(f"- {src} sendet {art} mit Switch {sw}: {n} Frames")
    else:
        w("Keine.")
    w("")

    # FA: Verhalten ueber die Zeit je Index (nur Antworten, wie viele Indizes aendern sich?)
    w("## Switch 0xFA: Werteverhalten (Antworten, alle Quellen)")
    w("")
    series = collections.defaultdict(list)
    for i in by_sw.get(0xFA, []):
        f = frames[i]
        if f["tx"] or (f["d"][0] & 0x0F) != 2:
            continue
        series[(f["id"], (f["d"][3] << 8) | f["d"][4])].append((f["src"], f["t"], (f["d"][5] << 8) | f["d"][6]))
    changing = [(k, v) for k, v in series.items() if len({x[2] for x in v}) > 1]
    w(f"Serien (Member, Index): {len(series)}, davon mit wechselnden Werten: {len(changing)}")
    w("")
    w("| Member | Index | Antworten | verschiedene Werte | min | max | erster | letzter |")
    w("|---|---|---|---|---|---|---|---|")
    for (mid, ix), v in sorted(changing, key=lambda kv: -len({x[2] for x in kv[1]}))[:40]:
        vals = [x[2] for x in v]
        w(f"| 0x{mid:03x} | 0x{ix:04x} | {len(v)} | {len(set(vals))} | 0x{min(vals):04x} | 0x{max(vals):04x} | 0x{vals[0]:04x} | 0x{vals[-1]:04x} |")
    w("")

    path = os.path.join(LOGS, "switch_bytes_analyse.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    print("geschrieben:", path)


if __name__ == "__main__":
    main()
