"""Headless Automatik-Runner fuer mk1-debug (kein Qt, paho-mqtt 1.6.x).

Ablauf und Dateien laut INTERFACES.md Abschnitt 7.3 und 7.5 bzw. Plan
plan/mk1-debug-auto-plan.md. Aufbau:
- reine Funktionen (Planung, Secrets, State, Auswertung, Watchdog, Bus-Sicherheit),
- Klasse Runner mit der Ablaufsteuerung, die nur ueber ein Transport-Objekt
  mit publish()/poll() mit dem MQTT-Teil spricht,
- PahoTransport als echte Transport-Schicht (rx-Mitschnitt im paho-Thread).

Zugangsdaten aus secrets.yaml werden nie ausgegeben, geloggt oder in Dateien geschrieben.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

# Mit -I ist das Skriptverzeichnis nicht im sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mqtt_secrets import SECRETS_PATH, SECRET_KEYS, parse_secrets  # noqa: E402,F401

PREFIX = "stiebel-debug/"
PC_CAN_ID = 0x680
PROBE_INDEX = 0x000B
PROBE_FULL = (0x000, 0x7FF)
PROBE_SMOKE = (0x170, 0x210)
PAGE = 0x1000
RX_TOPICS = (
    "rx",
    "read/result",
    "scan/progress",
    "scan/result",
    "status",
    "availability",
    "probe/progress",
    "probe/result",
)

EXIT_OK = 0
EXIT_OFFLINE = 2
EXIT_BUS = 3
EXIT_ERROR = 4
EXIT_INTERRUPTED = 130  # Ctrl+C, zusaetzlich zu 0/2/3/4 aus Plan 7.3

ONLINE_WAIT_S = 300.0
STALL_S = 60.0
READ_TIMEOUT_S = 2.0
REREAD_GAP_S = 60.0
REREAD_PASSES = 3
REREAD_SAVE_EVERY = 100  # State-Datei nur alle N Reads schreiben
SMOKE_REREAD_PASSES = 1
TWAI_ERR_LIMIT = 100
MIN_DELAY_MS = 100
DEFAULT_PASSIVE_S = 60.0
DEFAULT_SECRETS = str(SECRETS_PATH)   # mk1-debug/secrets.yaml

# (Stufe, Format fuer cmd/scan, Bereiche inklusive). Reihenfolge laut Plan Schritt 2.
FULL_STAGES = (
    ("A1", "short", ((0x0000, 0x00FF),)),
    ("A2", "long", ((0x0000, 0x00FF),)),
    ("B", "auto", ((0x0100, 0x0FFF),)),
    ("C", "auto", ((0x4E00, 0x4FFF), (0xC000, 0xC3FF), (0xFFF0, 0xFFFF))),
    ("D", "auto", ((0x1000, 0x4DFF), (0x5000, 0xBFFF), (0xC400, 0xFFEF))),
)
SMOKE_STAGES = (
    ("S1", "auto", ((0x0000, 0x001F),)),
    ("S2", "auto", ((0x00F8, 0x0102),)),
)
# Stufe D nur fuer diese Member (Standard, CLI --d-members).
D_MEMBERS = (0x180, 0x480, 0x500, 0x514)
# Zusaetzliche Member (FES, antwortet laut Probe nicht), CLI --extra-members.
EXTRA_MEMBERS = (0x100,)
# Stufen, in denen Extra-Member statisch geplant werden. Stufe D nur dynamisch.
EXTRA_STAGES = ("A1", "A2", "B", "C")
# Switch-Bytes (INTERFACES.md 8.1/8.6): 0xFA = aktueller Wert (Standard), 0xF8 = Min, 0xF9 = Max.
SWITCH_DEFAULT = 0xFA
SWITCH_VALUES = (0xF7, 0xF8, 0xF9, 0xFA)
MINMAX_SWITCHES = (0xF8, 0xF9)


class ConfigError(Exception):
    """Fehler in secrets.yaml. Meldungen enthalten nie Werte."""


class RunAbort(Exception):
    """Lauf abbrechen mit Exit-Code."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class _Retry(Exception):
    """Teil-Scan/Probe/Read nach Offline oder Stall komplett wiederholen."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class Timing:
    """Wartezeiten. Im Test verkuerzbar."""

    passive_s: float = DEFAULT_PASSIVE_S
    gap_s: float = 0.1
    online_wait_s: float = ONLINE_WAIT_S
    stall_s: float = STALL_S
    read_timeout_s: float = READ_TIMEOUT_S
    reread_gap_s: float = REREAD_GAP_S


@dataclass(frozen=True)
class BrokerConfig:
    host: str
    port: int
    user: str
    password: str = field(repr=False)


# ----------------------------------------------------------------------
# Hilfsfunktionen
# ----------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def hex_member(value: int) -> str:
    return f"0x{value:03x}"


def hex_index(value: int) -> str:
    return f"0x{value:04x}"


def parse_hex(value) -> int:
    """"0x180", "0X180", "180" oder int in int wandeln."""
    if isinstance(value, bool):
        raise TypeError("bool ist kein Hex-Wert")
    if isinstance(value, int):
        return value
    return int(str(value).strip(), 16)


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_switch(value) -> int:
    """Switch aus einem Feld ("fa", "0xf8", int; fehlt/None = 0xFA). Sonst ValueError."""
    if value is None:
        return SWITCH_DEFAULT
    sw = parse_hex(value)
    if sw not in SWITCH_VALUES:
        raise ValueError(f"ungueltiger switch: {value}")
    return sw


def switch_cmd(sw: int) -> str:
    """Switch fuer cmd/scan und cmd/read: "f8"."""
    return f"{sw:02x}"


def switch_state(sw: int) -> str:
    """Switch fuer _state.json: "0xf8"."""
    return f"0x{sw:02x}"


def entry_switch(entry: dict) -> int:
    """Switch eines Task-, Reread- oder Min/Max-Eintrags; fehlt = 0xFA."""
    return parse_switch(entry.get("switch"))


# ----------------------------------------------------------------------
# Secrets
# ----------------------------------------------------------------------
def load_broker_config(path: str) -> BrokerConfig:
    with open(path, encoding="utf-8") as fh:
        values = parse_secrets(fh.read())
    missing = [k for k in SECRET_KEYS if k not in values]
    if missing:
        raise ConfigError(f"secrets.yaml: fehlende Schluessel: {', '.join(missing)}")
    try:
        port = int(values["mqtt_port"])
    except ValueError:
        raise ConfigError("secrets.yaml: mqtt_port ist keine Zahl") from None
    return BrokerConfig(
        host=values["mqtt_server"],
        port=port,
        user=values["mqtt_username"],
        password=values["mqtt_password"],
    )


# ----------------------------------------------------------------------
# Planung
# ----------------------------------------------------------------------
def split_range(lo: int, hi: int, size: int = PAGE) -> list[tuple[int, int]]:
    """Bereich [lo, hi] (inklusive) in Teil-Bereiche mit hoechstens `size` Indizes teilen."""
    out = []
    cur = lo
    while cur <= hi:
        end = min(cur + size - 1, hi)
        out.append((cur, end))
        cur = end + 1
    return out


def build_stage_tasks(stage: str, fmt: str, ranges, member: int) -> list[dict]:
    """Teil-Scans eines Members fuer eine Stufe (Bereiche in Teile zu hoechstens PAGE Indizes)."""
    out = []
    for lo, hi in ranges:
        for clo, chi in split_range(lo, hi):
            out.append({
                "stage": stage,
                "member": hex_member(member),
                "format": fmt,
                "from": hex_index(clo),
                "to": hex_index(chi),
                "done": False,
                "items": [],
                "timeouts": 0,
            })
    return out


def plan_tasks(members: list[int], stages=FULL_STAGES, d_members=None) -> list[dict]:
    """Task-Liste: Stufe -> Member -> Bereich -> Teil-Scan. Reihenfolge wie Plan Schritt 2.

    d_members: Stufe D nur fuer diese Member (None = alle).
    """
    tasks = []
    for stage, fmt, ranges in stages:
        for member in members:
            if stage == "D" and d_members is not None and member not in d_members:
                continue
            tasks.extend(build_stage_tasks(stage, fmt, ranges, member))
    return tasks


def refine_tasks(tasks: list[dict], d_members, extra_members, stages=FULL_STAGES) -> list[dict]:
    """Idempotent. Entfernt nicht erledigte D-Tasks fremder Member (d_members None = nichts entfernen)
    und ergaenzt fehlende A1/A2/B/C-Tasks der Extra-Member direkt hinter der letzten Task ihrer Stufe.
    Erledigte Tasks bleiben unberuehrt. Leere Liste bleibt leer (Planung erfolgt vorher).
    """
    if not tasks:
        return tasks
    if d_members is not None:
        allowed = {hex_member(m) for m in d_members}
        tasks[:] = [t for t in tasks
                    if t["done"] or t["stage"] != "D" or t["member"] in allowed]
    order = [s[0] for s in stages]
    for stage, fmt, ranges in stages:
        if stage not in EXTRA_STAGES:
            continue
        for member in extra_members:
            if member == PC_CAN_ID:
                continue
            mhex = hex_member(member)
            if any(t["stage"] == stage and t["member"] == mhex for t in tasks):
                continue
            idx = [i for i, t in enumerate(tasks) if t["stage"] == stage]
            if idx:
                pos = idx[-1] + 1
            else:
                pos = next((i for i, t in enumerate(tasks)
                            if t["stage"] in order and order.index(t["stage"]) > order.index(stage)),
                           len(tasks))
            tasks[pos:pos] = build_stage_tasks(stage, fmt, ranges, member)
    return tasks


def members_from_probe(items: list[dict]) -> list[int]:
    """Member = IDs mit Antwort (ok/unsupported), sortiert, ohne eigene ID."""
    ids = set()
    for item in items:
        if item.get("status") in ("ok", "unsupported"):
            ids.add(parse_hex(item["id"]))
    ids.discard(PC_CAN_ID)
    return sorted(ids)


def passive_senders(frames: list[dict]) -> set[int]:
    """Absender-IDs aus rx-Frames (tx == false), ohne eigene ID."""
    return {
        int(f["id"])
        for f in frames
        if not f.get("tx") and int(f["id"]) != PC_CAN_ID
    }


def silent_ids(passive: set[int], members: list[int]) -> list[str]:
    """Passiv gesehene Absender ohne Probe-Antwort."""
    return [hex_member(i) for i in sorted(passive - set(members))]


def apply_scan_result(task: dict, payload: dict) -> int:
    """Items mit ok/unsupported in den Task uebernehmen, Timeouts nur zaehlen. Gibt Anzahl uebernommener Items zurueck."""
    stored = 0
    for item in payload.get("items", []):
        status = item.get("status")
        if status in ("ok", "unsupported"):
            task["items"].append({
                "index": item["index"],
                "raw": item["raw"],
                "status": status,
            })
            stored += 1
        elif status == "timeout":
            task["timeouts"] = task.get("timeouts", 0) + 1
    return stored


def collect_hits(tasks: list[dict]) -> list[tuple[str, str]]:
    """Alle Treffer (status ok) als eindeutige (member, index)-Paare, sortiert.
    Nur Tasks mit Standard-Switch (0xFA); Min/Max-Werte (0xF8/0xF9) sind keine Treffer fuer Rereads."""
    seen = set()
    for task in tasks:
        if entry_switch(task) != SWITCH_DEFAULT:
            continue
        member = parse_hex(task["member"])
        for item in task["items"]:
            if item.get("status") == "ok":
                seen.add((member, parse_hex(item["index"])))
    return [(hex_member(m), hex_index(i)) for m, i in sorted(seen)]


# ----------------------------------------------------------------------
# Watchdog und Bus-Sicherheit
# ----------------------------------------------------------------------
def stalled(last_progress: float, now: float, stall_s: float) -> bool:
    return now - last_progress > stall_s


def bus_problem(prev_twai_err: int | None, status: dict) -> str | None:
    """`bus_off` true oder twai_err-Anstieg > TWAI_ERR_LIMIT zur letzten Status-Meldung."""
    if bool(status.get("bus_off")):
        return "bus_off"
    err = _as_int(status.get("twai_err"), None)
    if err is not None and prev_twai_err is not None and err - prev_twai_err > TWAI_ERR_LIMIT:
        return "twai_err"
    return None


# ----------------------------------------------------------------------
# State (_state.json)
# ----------------------------------------------------------------------
def new_state(delay_ms: int, smoke: bool) -> dict:
    return {
        "version": 1,
        "started": now_iso(),
        "finished": None,
        "delay_ms": delay_ms,
        "smoke": smoke,
        "passive_ids": [],
        "probe": None,
        "members": None,
        "silent": None,
        "tasks": [],
        "rereads": [],
    }


def save_state(path: str, state: dict) -> None:
    """Atomar schreiben: tmp-Datei, dann os.replace."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_state(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        state = json.load(fh)
    if not isinstance(state, dict) or state.get("version") != 1:
        raise ValueError("unbekannte State-Version")
    return state


# ----------------------------------------------------------------------
# Ablaufsteuerung
# ----------------------------------------------------------------------
class Runner:
    """Ablauf Schritt 1 bis 3. Events kommen aus transport.poll(), Befehle gehen per transport.publish()."""

    def __init__(self, transport, state: dict, *, state_path: str, timing: Timing,
                 log: logging.Logger, smoke: bool, delay_ms: int, status_fp=None,
                 clock=time.monotonic, d_members=D_MEMBERS, extra_members=EXTRA_MEMBERS,
                 minmax: bool = False):
        self.transport = transport
        self.state = state
        self.state_path = state_path
        self.timing = timing
        self.log = log
        self.smoke = smoke
        self.delay_ms = delay_ms
        self.status_fp = status_fp
        self.clock = clock
        self.minmax = minmax
        self.probe_range = PROBE_SMOKE if smoke else PROBE_FULL
        self.stages = SMOKE_STAGES if smoke else FULL_STAGES
        self.d_members = None if d_members is None else tuple(d_members)
        self.extra_members = tuple(m for m in extra_members if m != PC_CAN_ID)

        self.online = False
        self.went_offline = False
        self._collect_passive = False
        self._passive: set[int] = set()
        self._last_twai: int | None = None
        self._last_progress = clock()
        self._scan_done = False
        self._scan_results: dict[tuple[int, int], list[dict]] = {}  # (member, switch)
        self._probe_done = False
        self._probe_items: list[dict] = []
        self._read_results: dict[tuple[int, int, int], dict] = {}  # (member, index, switch)

    # -- Einstieg ------------------------------------------------------
    def execute(self) -> int:
        """Lauf mit Abbruchbehandlung. Gibt Exit-Code zurueck."""
        try:
            return self.run()
        except RunAbort as exc:
            self.log.error("%s", exc)
            code = exc.code
        except KeyboardInterrupt:
            self.log.warning("Ctrl+C: Lauf gestoppt, Zustand gesichert")
            code = EXIT_INTERRUPTED
        except Exception:
            self.log.exception("Unerwarteter Fehler")
            code = EXIT_ERROR
        self._stop()
        return code

    def run(self) -> int:
        st = self.state
        self._wait_online()
        self._publish("cmd/sniff", {"on": True, "ids": [], "types": []})
        if st["probe"] is None:
            self._step1()
        if not st["tasks"] and st["members"] is not None:
            self._plan()
        st["tasks"] = refine_tasks(st["tasks"], self.d_members, self.extra_members, self.stages)
        if st["tasks"]:
            self._log_stage_counts("Schritt 2 Tasks je Stufe")
        self._save()
        self._run_tasks()
        self._rereads()
        if self.minmax:
            self._minmax()
        st["finished"] = now_iso()
        self._save()
        self._publish("cmd/sniff", {"on": False, "ids": [], "types": []})
        self.log.info("Automatik fertig")
        return EXIT_OK

    def _stop(self) -> None:
        try:
            self.transport.publish("cmd/stop", {})
        except Exception:
            pass
        try:
            self._save()
        except OSError as exc:
            self.log.error("Zustand konnte nicht gesichert werden: %s", exc)

    # -- Schritt 1 -----------------------------------------------------
    def _step1(self) -> None:
        self._collect_passive = True
        self._passive = set()
        self.log.info("Schritt 1: passiv sammeln (%s s)", self.timing.passive_s)
        self._wait_until(lambda: False, self.timing.passive_s, guard=False)
        self._collect_passive = False
        self.state["passive_ids"] = [hex_member(i) for i in sorted(self._passive)]
        self._save()
        while True:
            try:
                self._probe()
                break
            except _Retry as exc:
                self.log.warning("Probe wird wiederholt (%s)", exc.reason)
                self._wait_online()

    def _probe(self) -> None:
        lo, hi = self.probe_range
        self._probe_items = []
        self._probe_done = False
        self._last_progress = self.clock()
        self._publish("cmd/probe", {
            "index": hex_index(PROBE_INDEX),
            "from_id": hex_member(lo),
            "to_id": hex_member(hi),
            "delay_ms": self.delay_ms,
        })
        self._wait_until(lambda: self._probe_done, stall=True)
        # Nachzuegler der letzten probe/result-Chunks abwarten
        self._wait_until(lambda: False, self.timing.read_timeout_s)
        items = list(self._probe_items)
        members = members_from_probe(items)
        self.state["probe"] = {"index": hex_index(PROBE_INDEX), "items": items}
        self.state["members"] = [hex_member(m) for m in members]
        self.state["silent"] = silent_ids(self._passive, members)
        self._save()
        self.log.info("Member gefunden: %d (%s)", len(members),
                      ", ".join(hex_member(m) for m in members))
        if self.state["silent"]:
            self.log.info("Passiv, ohne Probe-Antwort: %s", ", ".join(self.state["silent"]))
        if not members:
            self.log.warning("Keine Member gefunden")

    # -- Schritt 2 -----------------------------------------------------
    def _plan(self) -> None:
        """Schritt 2 planen. Member = Probe-Member + Extra-Member (ohne 0x680).
        Stufe D nur fuer Probe-Member in d_members; Extra-Member bekommen D dynamisch."""
        st = self.state
        base = [parse_hex(m) for m in st["members"]]
        d_static = [m for m in base if self.d_members is None or m in self.d_members]
        extra_only = [m for m in self.extra_members if m not in base]
        extra_hex = {hex_member(m) for m in extra_only}
        planned = plan_tasks(sorted(set(base) | set(extra_only)), self.stages, d_members=d_static)
        st["tasks"] = [t for t in planned
                       if t["member"] not in extra_hex or t["stage"] in EXTRA_STAGES]
        self.log.info("Schritt 2: %d Teil-Scans geplant", len(st["tasks"]))
        self._save()

    def _log_stage_counts(self, prefix: str) -> None:
        names = [s[0] for s in self.stages]
        counts = {n: 0 for n in names}
        for task in self.state["tasks"]:
            counts[task["stage"]] = counts.get(task["stage"], 0) + 1
        self.log.info("%s: %s", prefix, ", ".join(f"{n}={counts.get(n, 0)}" for n in names))

    def _expand_extra_d(self, tasks: list, pos: int) -> int:
        """Stufe D fuer Extra-Member mit mindestens einem ok-Treffer (A/B/C) vor tasks[pos] einfuegen.
        Gibt Anzahl eingefuegter Tasks zurueck. Doppelte Bereiche werden nicht eingefuegt."""
        d_spec = next((s for s in self.stages if s[0] == "D"), None)
        if d_spec is None:
            return 0
        _, fmt, ranges = d_spec
        base_hex = set(self.state.get("members") or [])
        extra = [m for m in self.extra_members if hex_member(m) not in base_hex]
        hit_members = {m for m, _ in collect_hits([t for t in tasks if t["stage"] in EXTRA_STAGES])}
        present = {(t["member"], t["from"], t["to"]) for t in tasks if t["stage"] == "D"}
        new: list[dict] = []
        for member in extra:
            mhex = hex_member(member)
            if mhex not in hit_members:
                self.log.info("Extra-Member %s ohne Antwort, Stufe D uebersprungen", mhex)
                continue
            for task in build_stage_tasks("D", fmt, ranges, member):
                if (task["member"], task["from"], task["to"]) not in present:
                    new.append(task)
        if new:
            tasks[pos:pos] = new
            self.log.info("Stufe D fuer Extra-Member ergaenzt: %d Teil-Scans", len(new))
        return len(new)

    def _run_tasks(self) -> None:
        tasks = self.state["tasks"]
        expanded = False
        i = 0
        while i < len(tasks):
            task = tasks[i]
            if task["done"]:
                i += 1
                continue
            if task["stage"] == "D" and not expanded:
                expanded = True  # vor dem ersten D-Task einmal pruefen
                if self._expand_extra_d(tasks, i):
                    self._save()
                    task = tasks[i]
            number = i + 1
            label = (f"{task['stage']} {task['member']} "
                     f"{task['from']}-{task['to']} {task['format']}")
            while True:
                try:
                    self._run_scan_task(task)
                    break
                except _Retry as exc:
                    self.log.warning("Teil-Scan %s wird wiederholt (%s)", label, exc.reason)
                    task["items"] = []
                    task["timeouts"] = 0
                    self._wait_online()
            self.log.info("Teil-Scan %d/%d fertig: %s, %d Treffer",
                          number, len(tasks), label, len(task["items"]))
            self._save()
            i += 1

    def _run_scan_task(self, task: dict) -> None:
        member = parse_hex(task["member"])
        sw = entry_switch(task)
        key = (member, sw)
        self._scan_done = False
        self._scan_results.pop(key, None)
        self._last_progress = self.clock()
        payload = {
            "member": task["member"],
            "from": task["from"],
            "to": task["to"],
            "format": task["format"],
            "delay_ms": self.delay_ms,
        }
        if sw != SWITCH_DEFAULT:
            payload["switch"] = switch_cmd(sw)
        self._publish("cmd/scan", payload)
        self._wait_until(lambda: self._scan_done, stall=True)
        # Die Firmware schickt scan/result in Chunks zu 32 Items, der letzte Chunk vor dem
        # done-Progress. Kurz auf Nachzuegler warten, dann alle Chunks uebernehmen.
        self._wait_until(lambda: False, self.timing.read_timeout_s / 2, guard=False)
        chunks = self._scan_results.pop(key, [])
        if not chunks:
            self.log.info("Kein scan/result fuer %s (keine Antworten)", task["member"])
        for payload in chunks:
            apply_scan_result(task, payload)
        task["done"] = True

    # -- Schritt 3 -----------------------------------------------------
    def _rereads(self) -> None:
        st = self.state
        passes = SMOKE_REREAD_PASSES if self.smoke else REREAD_PASSES
        hits = collect_hits(st["tasks"])
        self.log.info("Schritt 3: %d Treffer, %d Durchgang/Durchgaenge", len(hits), passes)
        last_end = None
        since_save = 0
        for number in range(1, passes + 1):
            done = {(r["member"], r["index"], r["pass"], entry_switch(r)) for r in st["rereads"]}
            todo = [h for h in hits if (h[0], h[1], number, SWITCH_DEFAULT) not in done]
            if not todo:
                continue
            if last_end is not None:
                self.log.info("Pause bis Durchgang %d (%s s)", number, self.timing.reread_gap_s)
                self._wait_until(lambda: False, self.timing.reread_gap_s, guard=False)
            for member, index in todo:
                while True:
                    try:
                        result = self._read_one(member, index)
                        break
                    except _Retry as exc:
                        self.log.warning("Read wird wiederholt (%s)", exc.reason)
                        self._wait_online()
                st["rereads"].append({
                    "member": member,
                    "index": index,
                    "pass": number,
                    "t": now_iso(),
                    "raw": result["raw"],
                    "status": result["status"],
                })
                # _state.json ist nach den Scans zig MB gross: nicht nach jedem Read schreiben
                since_save += 1
                if since_save >= REREAD_SAVE_EVERY:
                    self._save()
                    since_save = 0
            self._save()
            since_save = 0
            last_end = self.clock()
            self.log.info("Durchgang %d fertig", number)

    def _read_one(self, member: str, index: str, switch: int = SWITCH_DEFAULT) -> dict:
        key = (parse_hex(member), parse_hex(index), switch)
        self._read_results.pop(key, None)
        payload = {"member": member, "index": index}
        if switch != SWITCH_DEFAULT:
            payload["switch"] = switch_cmd(switch)
        self._publish("cmd/read", payload)
        self._wait_until(lambda: key in self._read_results, self.timing.read_timeout_s)
        res = self._read_results.pop(key, None)
        self._wait_until(lambda: False, self.timing.gap_s, guard=False)
        if res is None:
            return {"raw": None, "status": "timeout"}
        return {"raw": res.get("raw"), "status": res.get("status")}

    def _minmax(self) -> None:
        """Schritt 4 (nur mit --minmax): je Treffer cmd/read mit switch f8 (Min) und f9 (Max).
        Ergebnisse in state["minmax"]; bereits vorhandene Eintraege werden uebersprungen."""
        st = self.state
        entries = st.setdefault("minmax", [])
        hits = collect_hits(st["tasks"])
        done = {(e["member"], e["index"], entry_switch(e)) for e in entries}
        todo = [(m, i, sw) for m, i in hits for sw in MINMAX_SWITCHES
                if (m, i, sw) not in done]
        self.log.info("Min/Max: %d Treffer, %d Reads", len(hits), len(todo))
        since_save = 0
        for member, index, sw in todo:
            while True:
                try:
                    result = self._read_one(member, index, sw)
                    break
                except _Retry as exc:
                    self.log.warning("Min/Max-Read wird wiederholt (%s)", exc.reason)
                    self._wait_online()
            entries.append({
                "member": member,
                "index": index,
                "switch": switch_state(sw),
                "raw": result["raw"],
                "status": result["status"],
            })
            since_save += 1
            if since_save >= REREAD_SAVE_EVERY:
                self._save()
                since_save = 0
        self._save()
        self.log.info("Min/Max fertig")

    # -- Verbindung / Warten -------------------------------------------
    def _wait_online(self) -> None:
        self.went_offline = False
        self.log.info("Warte auf availability online")
        if not self._wait_until(lambda: self.online, self.timing.online_wait_s, guard=False):
            raise RunAbort(EXIT_OFFLINE, "ESP nicht online")
        self._last_progress = self.clock()
        self.log.info("ESP online")

    def _wait_until(self, cond, timeout: float | None = None, *,
                    stall: bool = False, guard: bool = True) -> bool:
        """Events verarbeiten, bis cond() wahr ist oder timeout abläuft.

        guard: bei offline-Meldung _Retry werfen. stall: bei 60 s ohne Fortschritt _Retry werfen.
        """
        start = self.clock()
        while True:
            if cond():
                return True
            if guard and self.went_offline:
                raise _Retry("offline")
            now = self.clock()
            if stall and stalled(self._last_progress, now, self.timing.stall_s):
                raise _Retry("stall")
            wait = 0.2
            if timeout is not None:
                remaining = timeout - (now - start)
                if remaining <= 0:
                    self._pump(0.0)  # bereits eingegangene Events noch verarbeiten
                    return cond()
                wait = min(wait, remaining)
            self._pump(wait)

    def _pump(self, wait: float) -> None:
        for suffix, data in self.transport.poll(wait):
            self._dispatch(suffix, data)

    def _publish(self, suffix: str, payload: dict) -> None:
        self.transport.publish(suffix, payload)

    def _save(self) -> None:
        save_state(self.state_path, self.state)

    # -- Eingehende Nachrichten ----------------------------------------
    def _dispatch(self, suffix: str, data) -> None:
        if suffix == "availability":
            if data == "online":
                self.online = True
            elif data == "offline":
                self.online = False
                self.went_offline = True
            self.log.info("availability: %s", data)
            return

        if suffix == "status":
            self._write_status(data)
            reason = bus_problem(self._last_twai, data)
            err = _as_int(data.get("twai_err"), None)
            if err is not None:
                self._last_twai = err
            if reason is not None:
                raise RunAbort(EXIT_BUS, f"Bus-Problem ({reason}), Automatik gestoppt")
            return

        if suffix == "rx":
            if self._collect_passive:
                self._passive |= passive_senders(data)
            return

        if suffix == "scan/progress":
            self._last_progress = self.clock()
            if data.get("state") == "done":
                self._scan_done = True
            return

        if suffix == "scan/result":
            try:
                key = (parse_hex(data["member"]), parse_switch(data.get("switch")))
                self._scan_results.setdefault(key, []).append(data)
            except (KeyError, TypeError, ValueError):
                pass
            return

        if suffix == "probe/progress":
            self._last_progress = self.clock()
            if data.get("state") == "done":
                self._probe_done = True
            return

        if suffix == "probe/result":
            self._probe_items.extend(data.get("items", []))
            return

        if suffix == "read/result":
            try:
                key = (parse_hex(data["member"]), parse_hex(data["index"]),
                       parse_switch(data.get("switch")))
                self._read_results[key] = data
            except (KeyError, TypeError, ValueError):
                pass

    def _write_status(self, data: dict) -> None:
        if self.status_fp is None:
            return
        line = {"pc": now_iso(), "status": data}
        self.status_fp.write(json.dumps(line, ensure_ascii=False) + "\n")
        self.status_fp.flush()


def execute(transport, state: dict, *, state_path: str, timing: Timing,
            log: logging.Logger, smoke: bool, delay_ms: int, status_fp=None,
            clock=time.monotonic, d_members=D_MEMBERS, extra_members=EXTRA_MEMBERS,
            minmax: bool = False) -> int:
    runner = Runner(transport, state, state_path=state_path, timing=timing, log=log,
                    smoke=smoke, delay_ms=delay_ms, status_fp=status_fp, clock=clock,
                    d_members=d_members, extra_members=extra_members, minmax=minmax)
    return runner.execute()


# ----------------------------------------------------------------------
# Echte MQTT-Transport-Schicht (paho-mqtt 1.6.x)
# ----------------------------------------------------------------------
class PahoTransport:
    """publish() sendet; poll() liefert Events (suffix, data). rx-Frames werden im paho-Thread in rx_fp geschrieben."""

    def __init__(self, cfg: BrokerConfig, rx_fp, log: logging.Logger):
        self._cfg = cfg
        self._rx = rx_fp
        self._log = log
        self._queue: queue.Queue = queue.Queue()
        self._client = mqtt.Client(client_id="", clean_session=True)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        if cfg.user:
            self._client.username_pw_set(cfg.user, cfg.password)

    def connect(self) -> None:
        self._client.connect(self._cfg.host, self._cfg.port, keepalive=30)
        self._client.loop_start()

    def close(self) -> None:
        try:
            self._client.disconnect()
        except Exception:
            pass
        try:
            self._client.loop_stop()
        except Exception:
            pass

    def publish(self, suffix: str, payload: dict) -> None:
        self._client.publish(PREFIX + suffix, json.dumps(payload), qos=0)

    def poll(self, timeout: float) -> list[tuple[str, object]]:
        events = []
        try:
            events.append(self._queue.get(timeout=max(0.0, timeout)))
        except queue.Empty:
            return events
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                return events

    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            self._log.error("Broker lehnt Verbindung ab (rc=%s)", rc)
            return
        for topic in RX_TOPICS:
            client.subscribe(PREFIX + topic, qos=0)

    def _on_message(self, client, userdata, msg):
        try:
            if not msg.topic.startswith(PREFIX):
                return
            suffix = msg.topic[len(PREFIX):]
            if suffix not in RX_TOPICS:
                return
            text = msg.payload.decode("utf-8")
            if suffix == "availability":
                self._queue.put(("availability", text.strip()))
                return
            data = json.loads(text)
            if suffix == "rx":
                frames = []
                for item in data:
                    self._rx.write(json.dumps(item, separators=(",", ":")) + "\n")
                    frame = {
                        "t": int(item["t"]),
                        "id": parse_hex(item["id"]),
                        "data": bytes.fromhex(item["d"]),
                        "tx": bool(item["tx"]),
                    }
                    u = int(item.get("u") or 0)
                    if u > 0:
                        frame["ts"] = round(u / 1000, 3)
                    frames.append(frame)
                self._rx.flush()
                if frames:
                    self._queue.put(("rx", frames))
                return
            self._queue.put((suffix, data))
        except Exception:
            self._log.debug("Ungueltige Nachricht ignoriert")


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def _parse_member_ids(text: str) -> tuple[int, ...]:
    out = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = parse_hex(part)
        except ValueError:
            raise argparse.ArgumentTypeError(f"keine Hex-ID: {part}") from None
        if not 0 <= value <= 0x7FF or value == PC_CAN_ID:
            raise argparse.ArgumentTypeError(f"ungueltige Member-ID: {part}")
        out.add(value)
    return tuple(sorted(out))


def parse_d_members(text: str):
    """Hex-IDs kommagetrennt; 'all' = None (Stufe D fuer alle)."""
    if text.strip().lower() == "all":
        return None
    return _parse_member_ids(text)


def parse_extra_members(text: str) -> tuple[int, ...]:
    """Hex-IDs kommagetrennt; 'none' = keine Extra-Member."""
    if text.strip().lower() == "none":
        return ()
    return _parse_member_ids(text)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="mk1-debug Automatik (headless)")
    ap.add_argument("--out", required=True, help="Ausgabeordner")
    ap.add_argument("--secrets", default=DEFAULT_SECRETS, help="secrets.yaml")
    ap.add_argument("--delay", type=int, default=MIN_DELAY_MS, help="Pause zwischen Requests in ms (min. 100)")
    ap.add_argument("--passive", type=float, default=DEFAULT_PASSIVE_S, help="Sekunden passiv sammeln")
    ap.add_argument("--resume", metavar="STATE_JSON", help="Lauf an _state.json fortsetzen")
    ap.add_argument("--smoke", action="store_true", help="Kurztest mit kleinen Bereichen")
    ap.add_argument("--d-members", type=parse_d_members, default=D_MEMBERS,
                    help="Stufe D nur fuer diese Member (Hex, kommagetrennt; 'all' = alle)")
    ap.add_argument("--extra-members", type=parse_extra_members, default=EXTRA_MEMBERS,
                    help="Extra-Member fuer A1/A2/B/C (Hex, kommagetrennt; 'none' = keine)")
    ap.add_argument("--minmax", action="store_true",
                    help="nach den Rereads Min (switch f8) und Max (switch f9) je Treffer lesen")
    return ap


def make_logger(log_path: str) -> logging.Logger:
    logger = logging.getLogger("auto_runner")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = out_dir / f"auto_{stamp}"
    log = make_logger(f"{prefix}_runner.log")
    delay_ms = max(MIN_DELAY_MS, args.delay)

    try:
        cfg = load_broker_config(args.secrets)
    except (OSError, ConfigError) as exc:
        log.error("Konfiguration: %s", exc)
        return EXIT_ERROR

    if args.resume:
        try:
            state = load_state(args.resume)
        except (OSError, ValueError) as exc:
            log.error("State nicht lesbar: %s", exc)
            return EXIT_ERROR
        state_path = args.resume
        smoke = bool(state.get("smoke", args.smoke))
        log.info("Fortsetzen aus %s", state_path)
    else:
        state = new_state(delay_ms, args.smoke)
        state_path = f"{prefix}_state.json"
        smoke = args.smoke

    timing = Timing(passive_s=max(0.0, args.passive), gap_s=delay_ms / 1000.0)
    rx_fp = open(f"{prefix}_rx.jsonl", "a", encoding="utf-8", buffering=1)
    status_fp = open(f"{prefix}_status.jsonl", "a", encoding="utf-8", buffering=1)
    transport = PahoTransport(cfg, rx_fp, log)
    try:
        try:
            transport.connect()
        except Exception as exc:
            log.error("Broker nicht erreichbar (%s)", type(exc).__name__)
            return EXIT_ERROR
        return execute(transport, state, state_path=state_path, timing=timing, log=log,
                       smoke=smoke, delay_ms=delay_ms, status_fp=status_fp,
                       d_members=args.d_members, extra_members=args.extra_members,
                       minmax=args.minmax)
    finally:
        transport.close()
        rx_fp.close()
        status_fp.close()


if __name__ == "__main__":
    sys.exit(main())
