"""Absender-Test FES (0x100): antwortet FES je nach Absender-CAN-ID anders?

Voraussetzung: Debug-Firmware mit cmd/read-Feld "src" laeuft auf dem Mk1 (stiebel-debug/availability = online).
Ablauf:
  1. Passiv --passive s mitlesen: jede CAN-ID, die selbst sendet, gilt als aktiv.
  2. Fuer jede Absender-ID und jeden Index ein cmd/read an 0x100 mit "src" (Timeout --timeout s).
     Aktive IDs werden uebersprungen, ausser sie stehen in --spoof (Kollisionsgefahr bei gleichzeitigem Senden).
  3. Zusaetzlich jede Antwort von 0x100 (Art 2) aus dem Mitschnitt mit Empfaenger notieren.
Ausgabe: logs/fes_src_<stamp>.csv (Semikolon) und .md, Mitschnitt logs/fes_src_<stamp>_rx.jsonl.

Aufruf:
  C:\\temp\\stiebel-Remote\\.venv\\Scripts\\python.exe -I C:\\temp\\stiebel-Remote\\mk1-debug\\tools\\fes_src_test.py
  ... --src 0x680,0x700 --index 0x4f4d,0x0199 --spoof 0x480
"""
import argparse
import datetime as dt
import json
import os
import sys
import threading
import time

import paho.mqtt.client as mqtt

HERE = os.path.dirname(os.path.abspath(__file__))
DEBUG_DIR = os.path.dirname(HERE)
LOG_DIR = os.environ.get("STIEBEL_LOG_DIR") or os.path.join(DEBUG_DIR, "logs")
sys.path.insert(0, os.path.join(DEBUG_DIR, "gui"))
from mqtt_secrets import load_mqtt_settings  # noqa: E402

FES = 0x100
# Unbenutzte IDs (laut Probe 2026-10-20 ohne eigene Sendungen). 0x680 = Standard (Vergleich).
DEFAULT_SRC = [0x680, 0x700, 0x780, 0x300, 0x400, 0x580, 0x600, 0x6a0]
# Kontrolle (FES liefert Werte an 0x680) und die Produktiv-Indizes mit 0x8000 an 0x680.
DEFAULT_INDEX = [0x0199, 0xfff0, 0x4e8b, 0x000b,
                 0x06b4, 0x4f4d, 0x4f4e, 0x4f98, 0x4f99, 0x4f91, 0x4e8a]


def hexlist(s):
    return [int(x, 0) for x in s.split(",") if x.strip()]


class Runner:
    def __init__(self, args):
        self.args = args
        self.online = threading.Event()
        self.result = None
        self.result_ev = threading.Event()
        self.lock = threading.Lock()
        self.active = set()
        self.fes_answers = []      # (pc_time, receiver, index, value, layout)
        self.rx_file = None

    def on_message(self, c, u, m):
        # Eine Ausnahme hier beendet den paho-Thread (Test lief 2026-11-02 dadurch ins Leere).
        try:
            self._handle(m)
        except Exception as e:  # noqa: BLE001
            print(f"on_message {m.topic}: {e!r}", flush=True)

    def _handle(self, m):
        p = m.payload.decode(errors="replace")
        if m.topic == "stiebel-debug/availability":
            if p == "online":
                self.online.set()
            return
        if m.topic == "stiebel-debug/read/result":
            try:
                self.result = json.loads(p)
            except ValueError:
                return
            self.result_ev.set()
            return
        if m.topic == "stiebel-debug/rx":
            try:
                frames = json.loads(p)
            except ValueError:
                return
            now = time.time()
            with self.lock:
                for fr in frames:
                    self.rx_file.write(json.dumps(fr) + "\n")
                    if fr.get("tx"):
                        continue
                    cid = int(fr["id"], 16)
                    self.active.add(cid)
                    d = bytes.fromhex(fr.get("d") or "")
                    if cid != FES or len(d) < 7 or (d[0] & 0x0F) != 2:
                        continue
                    recv = ((d[0] & 0xF0) << 3) | d[1]
                    long_ = d[2] in (0xF7, 0xF8, 0xF9, 0xFA)
                    idx = (d[3] << 8 | d[4]) if long_ else d[2]
                    val = (d[5] << 8 | d[6]) if long_ else (d[3] << 8 | d[4])
                    self.fes_answers.append((now, recv, idx, val, "L" if long_ else "S"))

    def read(self, c, src, index):
        self.result = None
        self.result_ev.clear()
        t0 = time.time()
        c.publish("stiebel-debug/cmd/read", json.dumps(
            {"member": "0x%03x" % FES, "index": "0x%04x" % index, "src": "0x%03x" % src,
             "timeout_ms": int(self.args.timeout * 1000)}))
        if not self.result_ev.wait(self.args.timeout + 1.0):
            return "no_result", None, t0
        r = self.result
        if r.get("index") != "0x%04x" % index or r.get("src", "0x680") != "0x%03x" % src:
            return "mismatch", r, t0
        return r.get("status"), r.get("raw"), t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=None, help="Absender-IDs, Komma-getrennt (Standard: unbenutzte IDs)")
    ap.add_argument("--index", default=None, help="Indizes, Komma-getrennt")
    ap.add_argument("--spoof", default="", help="aktive IDs, die trotzdem als Absender erlaubt sind (Risiko!)")
    ap.add_argument("--passive", type=int, default=30, help="Sekunden passiv mitlesen vor dem Test")
    ap.add_argument("--timeout", type=float, default=3.0, help="Antwort-Timeout in s (FES antwortet nach 0,5..1,5 s)")
    ap.add_argument("--gap", type=float, default=0.3, help="Pause zwischen Requests in s")
    args = ap.parse_args()
    srcs = hexlist(args.src) if args.src else list(DEFAULT_SRC)
    idxs = hexlist(args.index) if args.index else list(DEFAULT_INDEX)
    spoof = set(hexlist(args.spoof))
    for s in srcs:
        if s > 0x7FF or s == FES:
            sys.exit(f"Absender 0x{s:03x} ungueltig")

    settings = load_mqtt_settings()
    if not settings:
        sys.exit("MQTT-Zugang nicht gefunden (secrets.yaml)")
    host, port, user, pw = settings

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(LOG_DIR, f"fes_src_{stamp}")
    r = Runner(args)
    r.rx_file = open(out + "_rx.jsonl", "w", encoding="utf-8")

    c = mqtt.Client()
    c.username_pw_set(user, pw)
    c.on_message = r.on_message
    c.connect(host, port, 30)
    c.subscribe([("stiebel-debug/availability", 0), ("stiebel-debug/read/result", 0), ("stiebel-debug/rx", 0)])
    c.loop_start()
    rows = []
    try:
        if not r.online.wait(30):
            print("Debug-Firmware nicht online")
            return 2
        c.publish("stiebel-debug/cmd/sniff", json.dumps({"on": True, "ids": [], "types": []}))
        print(f"Passiv {args.passive} s mitlesen ...", flush=True)
        time.sleep(args.passive)
        with r.lock:
            active = set(r.active)
        print("Aktive IDs:", " ".join(f"0x{x:03x}" for x in sorted(active)))
        for src in srcs:
            if src in active and src not in spoof:
                print(f"0x{src:03x} ist aktiv auf dem Bus, uebersprungen (--spoof zum Erzwingen)")
                rows.append((src, None, "skipped_active", None, ""))
                continue
            for idx in idxs:
                st, raw, t0 = r.read(c, src, idx)
                time.sleep(0.3)  # spaete Antworten noch einsammeln
                with r.lock:
                    late = [a for a in r.fes_answers if a[0] >= t0 and a[2] == idx]
                recv = ",".join(sorted({f"0x{a[1]:03x}" for a in late}))
                rows.append((src, idx, st, raw, recv))
                print(f"src 0x{src:03x} idx 0x{idx:04x}: {st} {raw or ''} (FES-Antwort an: {recv or '-'})", flush=True)
                time.sleep(args.gap)
    finally:
        c.publish("stiebel-debug/cmd/sniff", json.dumps({"on": False, "ids": [], "types": []}))
        time.sleep(0.5)
        c.loop_stop()
        r.rx_file.close()

    with open(out + ".csv", "w", encoding="utf-8-sig") as f:
        f.write("src;index;status;raw;fes_antwort_an\n")
        for src, idx, st, raw, recv in rows:
            f.write(f"0x{src:03x};{'' if idx is None else '0x%04x' % idx};{st};{raw or ''};{recv}\n")
    # Matrix: Zeilen Index, Spalten Absender
    tested = [s for s in srcs if any(rw[0] == s and rw[1] is not None for rw in rows)]
    with open(out + ".md", "w", encoding="utf-8") as f:
        f.write(f"# FES-Absendertest {stamp}\n\nZiel 0x100, Timeout {args.timeout} s. "
                f"Aktive IDs (passiv {args.passive} s): {' '.join(f'0x{x:03x}' for x in sorted(active))}\n\n")
        f.write("| Index | " + " | ".join(f"0x{s:03x}" for s in tested) + " |\n")
        f.write("|---|" + "---|" * len(tested) + "\n")
        for idx in idxs:
            cells = []
            for s in tested:
                hit = [rw for rw in rows if rw[0] == s and rw[1] == idx]
                if not hit:
                    cells.append("")
                    continue
                _, _, st, raw, _ = hit[0]
                cells.append(raw if st == "ok" else ("8000" if st == "unsupported" else st))
            f.write(f"| 0x{idx:04x} | " + " | ".join(cells) + " |\n")
        skipped = [f"0x{rw[0]:03x}" for rw in rows if rw[2] == "skipped_active"]
        if skipped:
            f.write(f"\nUebersprungen (aktiv): {', '.join(skipped)}\n")
    print("Ergebnis:", out + ".md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
