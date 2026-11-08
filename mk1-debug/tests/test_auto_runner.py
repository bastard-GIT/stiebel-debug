"""Tests fuer gui/auto_runner.py. Kein Broker: der Transport ist ein Fake.

Ausfuehren: C:\\temp\\stiebel-Remote\\.venv\\Scripts\\python.exe -I -B -m pytest C:\\temp\\stiebel-Remote\\mk1-debug\\tests\\test_auto_runner.py -q
"""

import copy
import io
import json
import logging
import sys
import time
from collections import deque
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gui"))

import auto_runner as ar  # noqa: E402

FAST = ar.Timing(passive_s=0, gap_s=0, online_wait_s=0.5, stall_s=0.5,
                 read_timeout_s=0.5, reread_gap_s=0)
SECRET_PW = "geheim-pw-4711"


def make_log(buf: io.StringIO) -> logging.Logger:
    logger = logging.getLogger(f"test-auto-runner-{id(buf)}")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler(buf))
    return logger


class FakeTransport:
    """Simuliert ein Geraet mit Membern 0x180 und 0x201 (plus stummer Absender 0x100)."""

    def __init__(self, members=(0x180, 0x201), silent=(0x100,), statuses=(),
                 online=True, offline_on_first_scan=False, scan_answers=None,
                 minmax=None, omit_switch=False):
        self.members = list(members)
        self.silent = list(silent)
        self.statuses = list(statuses)
        self.offline_on_first_scan = offline_on_first_scan
        # (member, index, switch) -> raw fuer cmd/read; sonst 0x0001
        self.minmax = dict(minmax or {})
        # True: read/result und scan/result ohne switch-Feld (aeltere Firmware)
        self.omit_switch = omit_switch
        # IDs, die auf cmd/scan antworten (Standard: nur die Member). 0x100 antwortet nicht.
        self.scan_answers = set(self.members if scan_answers is None else scan_answers)
        self.q: deque = deque()
        if online:
            self.q.append(("availability", "online"))
        self.sent: list[tuple[str, dict]] = []
        self.scan_calls = 0

    def publish(self, suffix, payload):
        self.sent.append((suffix, payload))
        if suffix == "cmd/sniff" and payload.get("on"):
            frames = [{"t": 1, "id": i, "data": bytes(7), "tx": False}
                      for i in self.members + self.silent]
            frames.append({"t": 2, "id": 0x680, "data": bytes(7), "tx": True})
            self.q.append(("rx", frames))
            for status in self.statuses:
                self.q.append(("status", status))
        elif suffix == "cmd/probe":
            self._probe(payload)
        elif suffix == "cmd/scan":
            self._scan(payload)
        elif suffix == "cmd/read":
            sw = ar.parse_switch(payload.get("switch"))
            key = (ar.parse_hex(payload["member"]), ar.parse_hex(payload["index"]), sw)
            res = {"member": payload["member"], "index": payload["index"],
                   "raw": self.minmax.get(key, "0x0001"), "status": "ok"}
            if not self.omit_switch:
                res["switch"] = ar.switch_state(sw)
            self.q.append(("read/result", res))

    def _probe(self, p):
        lo = ar.parse_hex(p["from_id"])
        hi = ar.parse_hex(p["to_id"])
        found = [m for m in self.members if lo <= m <= hi]
        items = [{"id": ar.hex_member(m), "raw": "0x0001", "status": "ok"} for m in found]
        total = hi - lo + 1
        self.q.append(("probe/progress", {"index": p["index"], "id": p["to_id"],
                                          "done": total, "total": total, "state": "running"}))
        for k in range(0, len(items), 32):
            self.q.append(("probe/result", {"index": p["index"], "items": items[k:k + 32]}))
        self.q.append(("probe/progress", {"index": p["index"], "id": p["to_id"],
                                          "done": total, "total": total, "state": "done"}))

    def _scan(self, p):
        self.scan_calls += 1
        if self.offline_on_first_scan and self.scan_calls == 1:
            self.q.append(("availability", "offline"))
            self.q.append(("availability", "online"))
            return
        lo = ar.parse_hex(p["from"])
        hi = ar.parse_hex(p["to"])
        n = hi - lo + 1
        items = []
        if ar.parse_hex(p["member"]) in self.scan_answers:
            for i in range(lo, hi + 1):
                if i % 2 == 0:
                    items.append({"index": ar.hex_index(i), "raw": "0x0001", "status": "ok"})
                else:
                    items.append({"index": ar.hex_index(i), "raw": "0x8000", "status": "unsupported"})
        # Wie die Firmware: Chunks zu 32 Items, dann der done-Progress
        sw_field = {} if self.omit_switch else {"switch": ar.switch_state(
            ar.parse_switch(p.get("switch")))}
        for k in range(0, len(items), 32):
            self.q.append(("scan/result", {"member": p["member"], "format": p["format"],
                                           "items": items[k:k + 32], **sw_field}))
        self.q.append(("scan/progress", {"member": p["member"], "format": p["format"],
                                         "index": p["to"], "done": n, "total": n,
                                         "state": "done"}))

    def poll(self, timeout):
        if not self.q:
            time.sleep(min(max(timeout, 0.0), 0.001))
            return []
        out = list(self.q)
        self.q.clear()
        return out

    def close(self):
        pass

    def sent_of(self, suffix):
        return [p for s, p in self.sent if s == suffix]


def run_fake(tmp_path, transport, *, state=None, smoke=True, timing=FAST, name="auto_t",
             minmax=False):
    buf = io.StringIO()
    state = state if state is not None else ar.new_state(100, smoke)
    state_path = tmp_path / f"{name}_state.json"
    status_fp = open(tmp_path / f"{name}_status.jsonl", "w", encoding="utf-8")
    try:
        code = ar.execute(transport, state, state_path=str(state_path), timing=timing,
                          log=make_log(buf), smoke=smoke, delay_ms=100, status_fp=status_fp,
                          minmax=minmax)
    finally:
        status_fp.close()
    return code, state_path, buf


# ----------------------------------------------------------------------
# Task-Planung
# ----------------------------------------------------------------------
def test_plan_counts_and_ranges_full():
    tasks = ar.plan_tasks([0x180, 0x201])
    # pro Member: A1 1, A2 1, B 1, C 3, D 15 (Teile zu je max. 0x1000)
    assert len(tasks) == 2 * (1 + 1 + 1 + 3 + 15)
    for t in tasks:
        size = ar.parse_hex(t["to"]) - ar.parse_hex(t["from"]) + 1
        assert 1 <= size <= ar.PAGE


def test_plan_order_stage_then_member():
    tasks = ar.plan_tasks([0x180, 0x201])
    stages = [t["stage"] for t in tasks]
    assert stages == sorted(stages, key=["A1", "A2", "B", "C", "D"].index)
    assert [(t["stage"], t["member"]) for t in tasks[:2]] == [("A1", "0x180"), ("A1", "0x201")]
    assert tasks[2]["stage"] == "A2" and tasks[2]["member"] == "0x180"
    assert tasks[0]["format"] == "short" and tasks[2]["format"] == "long"
    assert tasks[4]["format"] == "auto"


def test_plan_covers_full_range_once_per_member():
    members = [0x180]
    tasks = ar.plan_tasks(members)
    seen = []
    for t in tasks:
        if t["stage"] == "A2":  # A2 deckt 0x0000-0x00FF erneut ab (Langformat)
            continue
        seen.extend(range(ar.parse_hex(t["from"]), ar.parse_hex(t["to"]) + 1))
    assert sorted(seen) == list(range(0x10000))


def test_split_range():
    assert ar.split_range(0x0000, 0x00FF) == [(0x0000, 0x00FF)]
    assert ar.split_range(0x1000, 0x4DFF) == [(0x1000, 0x1FFF), (0x2000, 0x2FFF),
                                              (0x3000, 0x3FFF), (0x4000, 0x4DFF)]


def test_smoke_plan():
    tasks = ar.plan_tasks([0x180, 0x201], ar.SMOKE_STAGES)
    assert [(t["stage"], t["member"], t["from"], t["to"]) for t in tasks] == [
        ("S1", "0x180", "0x0000", "0x001f"), ("S1", "0x201", "0x0000", "0x001f"),
        ("S2", "0x180", "0x00f8", "0x0102"), ("S2", "0x201", "0x00f8", "0x0102"),
    ]


# ----------------------------------------------------------------------
# Secrets
# ----------------------------------------------------------------------
def test_secrets_parser_quoted_plain_comments():
    text = (
        "# Kommentar\n"
        "\n"
        'mqtt_server: "192.0.2.10"\n'
        "mqtt_port: 1883\n"
        "mqtt_username: 'bob'\n"
        f"mqtt_password: \"{SECRET_PW}\"\n"
        "ohne_doppelpunkt\n"
    )
    values = ar.parse_secrets(text)
    assert values == {"mqtt_server": "192.0.2.10", "mqtt_port": "1883",
                      "mqtt_username": "bob", "mqtt_password": SECRET_PW}


def test_broker_config_hides_password_and_errors_leak_nothing(tmp_path):
    good = tmp_path / "secrets.yaml"
    good.write_text(f'mqtt_server: "h"\nmqtt_port: 1883\nmqtt_username: "u"\n'
                    f'mqtt_password: "{SECRET_PW}"\n', encoding="utf-8")
    cfg = ar.load_broker_config(str(good))
    assert cfg.port == 1883 and cfg.password == SECRET_PW
    assert SECRET_PW not in repr(cfg) and SECRET_PW not in str(cfg)

    bad = tmp_path / "bad.yaml"
    bad.write_text(f'mqtt_server: "h"\nmqtt_port: abc\nmqtt_username: "u"\n'
                   f'mqtt_password: "{SECRET_PW}"\n', encoding="utf-8")
    with pytest.raises(ar.ConfigError) as info:
        ar.load_broker_config(str(bad))
    assert SECRET_PW not in str(info.value)

    missing = tmp_path / "missing.yaml"
    missing.write_text(f'mqtt_password: "{SECRET_PW}"\n', encoding="utf-8")
    with pytest.raises(ar.ConfigError) as info:
        ar.load_broker_config(str(missing))
    assert "mqtt_server" in str(info.value) and SECRET_PW not in str(info.value)


# ----------------------------------------------------------------------
# State: Roundtrip, atomar, Fortsetzen
# ----------------------------------------------------------------------
def test_state_roundtrip_atomic(tmp_path):
    state = ar.new_state(100, smoke=True)
    state["members"] = ["0x180"]
    state["tasks"] = ar.plan_tasks([0x180], ar.SMOKE_STAGES)
    path = tmp_path / "auto_x_state.json"
    ar.save_state(str(path), state)
    assert not (tmp_path / "auto_x_state.json.tmp").exists()
    assert ar.load_state(str(path)) == state


def test_load_state_rejects_unknown_version(tmp_path):
    path = tmp_path / "s.json"
    path.write_text('{"version": 9}', encoding="utf-8")
    with pytest.raises(ValueError):
        ar.load_state(str(path))


def test_resume_skips_done_tasks(tmp_path):
    state = ar.new_state(100, smoke=True)
    state["passive_ids"] = ["0x180", "0x201"]
    state["probe"] = {"index": "0x000b", "items": []}
    state["members"] = ["0x180", "0x201"]
    state["silent"] = []
    state["tasks"] = ar.plan_tasks([0x180, 0x201], ar.SMOKE_STAGES)
    state["tasks"][0]["done"] = True
    fake = FakeTransport()
    code, state_path, _ = run_fake(tmp_path, fake, state=state)
    assert code == 0
    scans = [(p["member"], p["from"]) for p in fake.sent_of("cmd/scan")]
    assert ("0x180", "0x0000") not in scans
    assert len(scans) == 3
    assert fake.sent_of("cmd/probe") == []
    saved = ar.load_state(str(state_path))
    assert all(t["done"] for t in saved["tasks"])


# ----------------------------------------------------------------------
# Auswertung und Watchdog (rein)
# ----------------------------------------------------------------------
def test_members_from_probe_and_silent():
    items = [{"id": "0x201", "raw": "0x0001", "status": "ok"},
             {"id": "0x180", "raw": "0x8000", "status": "unsupported"},
             {"id": "0x680", "raw": "0x0001", "status": "ok"},
             {"id": "0x333", "raw": "0x0000", "status": "timeout"}]
    members = ar.members_from_probe(items)
    assert members == [0x180, 0x201]
    assert ar.silent_ids({0x100, 0x180}, members) == ["0x100"]


def test_apply_scan_result_stores_only_ok_and_unsupported():
    task = {"items": [], "timeouts": 0}
    payload = {"member": "0x180", "items": [
        {"index": "0x0001", "raw": "0x0002", "status": "ok"},
        {"index": "0x0002", "raw": "0x8000", "status": "unsupported"},
        {"index": "0x0003", "raw": "0x0000", "status": "timeout"},
    ]}
    assert ar.apply_scan_result(task, payload) == 2
    assert [i["index"] for i in task["items"]] == ["0x0001", "0x0002"]
    assert task["timeouts"] == 1


def test_collect_hits_only_ok():
    tasks = [{"member": "0x180", "items": [
        {"index": "0x000c", "raw": "0x0058", "status": "ok"},
        {"index": "0x000d", "raw": "0x8000", "status": "unsupported"}]}]
    assert ar.collect_hits(tasks) == [("0x180", "0x000c")]


def test_watchdog_decisions():
    assert ar.stalled(0.0, 60.5, 60.0)
    assert not ar.stalled(0.0, 60.0, 60.0)


def test_bus_safety_decisions():
    assert ar.bus_problem(None, {"bus_off": True, "twai_err": 0}) == "bus_off"
    assert ar.bus_problem(10, {"bus_off": False, "twai_err": 111}) == "twai_err"
    assert ar.bus_problem(10, {"bus_off": False, "twai_err": 110}) is None
    assert ar.bus_problem(None, {"bus_off": False, "twai_err": 5000}) is None


# ----------------------------------------------------------------------
# Kompletter Durchlauf mit Fake-Transport (--smoke)
# ----------------------------------------------------------------------
def test_smoke_full_run(tmp_path):
    fake = FakeTransport(statuses=[{"rx": 0, "tx": 0, "twai_err": 0, "bus_off": False}])
    code, state_path, buf = run_fake(tmp_path, fake, smoke=True)
    assert code == 0

    saved = ar.load_state(str(state_path))
    assert saved["passive_ids"] == ["0x100", "0x180", "0x201"]
    assert saved["probe"] is not None
    assert saved["members"] == ["0x180", "0x201"]
    assert saved["silent"] == ["0x100"]
    assert [t["stage"] for t in saved["tasks"]] == ["S1", "S1", "S2", "S2"]
    assert all(t["done"] for t in saved["tasks"])
    assert saved["finished"]

    probe = fake.sent_of("cmd/probe")[0]
    assert probe["from_id"] == "0x170" and probe["to_id"] == "0x210"
    assert probe["index"] == "0x000b"
    assert len(fake.sent_of("cmd/scan")) == 4
    assert all(p["member"] != "0x680" for p in fake.sent_of("cmd/scan"))

    hits = ar.collect_hits(saved["tasks"])
    assert len(hits) > 0
    assert len(saved["rereads"]) == len(hits)
    assert all(r["pass"] == 1 for r in saved["rereads"])
    assert fake.sent[-1] == ("cmd/sniff", {"on": False, "ids": [], "types": []})
    assert not any(s == "cmd/stop" for s, _ in fake.sent)
    assert not (tmp_path / "auto_t_state.json.tmp").exists()


def test_bus_off_stops_with_exit_3(tmp_path):
    fake = FakeTransport(statuses=[{"bus_off": True, "twai_err": 0}])
    code, state_path, _ = run_fake(tmp_path, fake, smoke=True)
    assert code == 3
    assert ("cmd/stop", {}) in fake.sent
    assert ar.load_state(str(state_path))["finished"] is None


def test_twai_error_jump_stops_with_exit_3(tmp_path):
    fake = FakeTransport(statuses=[{"bus_off": False, "twai_err": 0},
                                   {"bus_off": False, "twai_err": 150}])
    code, _, _ = run_fake(tmp_path, fake, smoke=True)
    assert code == 3
    assert ("cmd/stop", {}) in fake.sent


def test_offline_repeats_scan_task(tmp_path):
    fake = FakeTransport(offline_on_first_scan=True)
    code, _, _ = run_fake(tmp_path, fake, smoke=True)
    assert code == 0
    scans = fake.sent_of("cmd/scan")
    # erster Versuch abgebrochen, danach wird derselbe Teil-Scan wiederholt
    assert len(scans) == 5
    assert scans[0] == scans[1]


def test_never_online_exit_2(tmp_path):
    fake = FakeTransport(online=False)
    timing = ar.Timing(passive_s=0, gap_s=0, online_wait_s=0.2, stall_s=0.2,
                       read_timeout_s=0.2, reread_gap_s=0)
    code, _, _ = run_fake(tmp_path, fake, smoke=True, timing=timing)
    assert code == 2
    assert ("cmd/stop", {}) in fake.sent


def test_log_and_status_contain_no_secret(tmp_path):
    fake = FakeTransport(statuses=[{"bus_off": False, "twai_err": 0}])
    code, _, buf = run_fake(tmp_path, fake, smoke=True)
    assert code == 0
    assert SECRET_PW not in buf.getvalue()
    for path in tmp_path.iterdir():
        assert SECRET_PW not in path.read_text(encoding="utf-8")


def test_scan_result_chunks_are_accumulated():
    # Regression: mehrere scan/result-Chunks pro Teil-Scan duerfen nicht verloren gehen
    task = ar.plan_tasks([0x180], (("X", "auto", ((0x0000, 0x00FF),)),))[0]
    runner = ar.Runner(None, ar.new_state(100, True), state_path="unused",
                       timing=ar.Timing(), log=__import__("logging").getLogger("t"),
                       smoke=True, delay_ms=100)
    for k in range(0, 256, 32):
        items = [{"index": ar.hex_index(i), "raw": "0x0001", "status": "ok"} for i in range(k, k + 32)]
        runner._dispatch("scan/result", {"member": "0x180", "items": items})
    for payload in runner._scan_results.pop((0x180, 0xFA)):
        ar.apply_scan_result(task, payload)
    assert len(task["items"]) == 256


# ----------------------------------------------------------------------
# D-Member-Auswahl, Extra-Member, refine_tasks
# ----------------------------------------------------------------------
def test_plan_d_members_limits_stage_d_only():
    tasks = ar.plan_tasks([0x180, 0x201, 0x480], d_members=[0x180, 0x480])
    d = [t for t in tasks if t["stage"] == "D"]
    assert {t["member"] for t in d} == {"0x180", "0x480"}
    assert len(d) == 2 * 15
    assert len([t for t in tasks if t["stage"] == "A1"]) == 3
    assert ar.plan_tasks([0x180, 0x201]) == ar.plan_tasks([0x180, 0x201], d_members=None)


def test_refine_is_idempotent():
    tasks = ar.plan_tasks([0x180, 0x201])
    once = ar.refine_tasks(copy.deepcopy(tasks), ar.D_MEMBERS, ar.EXTRA_MEMBERS, ar.FULL_STAGES)
    twice = ar.refine_tasks(copy.deepcopy(once), ar.D_MEMBERS, ar.EXTRA_MEMBERS, ar.FULL_STAGES)
    assert once == twice
    assert any(t["member"] == "0x100" for t in once)


def test_refine_keeps_done_and_removes_unfinished_foreign_d():
    tasks = ar.plan_tasks([0x180, 0x201])
    d201 = [t for t in tasks if t["stage"] == "D" and t["member"] == "0x201"]
    d201[0]["done"] = True
    refined = ar.refine_tasks(tasks, ar.D_MEMBERS, (), ar.FULL_STAGES)
    left = [t for t in refined if t["stage"] == "D" and t["member"] == "0x201"]
    assert left == [d201[0]]
    assert sum(1 for t in refined if t["stage"] == "D" and t["member"] == "0x180") == 15


def test_refine_places_extra_tasks_behind_last_task_of_stage():
    orig = ar.plan_tasks([0x180])
    refined = ar.refine_tasks(copy.deepcopy(orig), ar.D_MEMBERS, (0x100,), ar.FULL_STAGES)
    assert [t for t in refined if t["member"] != "0x100"] == orig
    assert not any(t["member"] == "0x100" and t["stage"] == "D" for t in refined)
    for stage in ("A1", "A2", "B", "C"):
        idx = [i for i, t in enumerate(refined) if t["stage"] == stage]
        assert refined[idx[-1]]["member"] == "0x100"
        assert idx == list(range(idx[0], idx[-1] + 1))
        assert idx[-1] + 1 == len(refined) or refined[idx[-1] + 1]["stage"] != stage


def test_refine_empty_list_stays_empty():
    assert ar.refine_tasks([], ar.D_MEMBERS, ar.EXTRA_MEMBERS, ar.FULL_STAGES) == []


def test_cli_minmax_default_off():
    assert ar.build_parser().parse_args(["--out", "x"]).minmax is False
    assert ar.build_parser().parse_args(["--out", "x", "--minmax"]).minmax is True


def test_cli_member_options():
    ns = ar.build_parser().parse_args(["--out", "x"])
    assert ns.d_members == ar.D_MEMBERS and ns.extra_members == ar.EXTRA_MEMBERS
    ns = ar.build_parser().parse_args(["--out", "x", "--d-members", "all", "--extra-members", "none"])
    assert ns.d_members is None and ns.extra_members == ()
    ns = ar.build_parser().parse_args(["--out", "x", "--d-members", "0x180, 480",
                                       "--extra-members", "0x100,0x101"])
    assert ns.d_members == (0x180, 0x480) and ns.extra_members == (0x100, 0x101)


CUSTOM_STAGES = (
    ("A1", "short", ((0x0000, 0x000F),)),
    ("D", "auto", ((0x1000, 0x100F),)),
)


def run_custom(tmp_path, fake, *, members, name="auto_c"):
    """Runner mit kleinen Stufen (A1, D); Probe bereits vorhanden, Member gesetzt."""
    buf = io.StringIO()
    state = ar.new_state(100, True)
    state["probe"] = {"index": "0x000b", "items": []}
    state["members"] = [ar.hex_member(m) for m in members]
    state_path = tmp_path / f"{name}_state.json"
    runner = ar.Runner(fake, state, state_path=str(state_path), timing=FAST,
                       log=make_log(buf), smoke=True, delay_ms=100,
                       d_members=ar.D_MEMBERS, extra_members=ar.EXTRA_MEMBERS)
    runner.stages = CUSTOM_STAGES
    code = runner.execute()
    return code, ar.load_state(str(state_path)), buf


def test_extra_member_d_inserted_before_first_d_with_hits(tmp_path):
    fake = FakeTransport(scan_answers=(0x180, 0x100))
    code, saved, buf = run_custom(tmp_path, fake, members=[0x180])
    assert code == 0
    tasks = saved["tasks"]
    assert all(t["done"] for t in tasks)
    d = [t for t in tasks if t["stage"] == "D"]
    assert [t["member"] for t in d] == ["0x100", "0x180"]
    assert "ohne Antwort" not in buf.getvalue()
    assert "Stufe D fuer Extra-Member ergaenzt: 1 Teil-Scans" in buf.getvalue()


def test_extra_member_without_answer_skips_d(tmp_path):
    fake = FakeTransport()  # 0x100 antwortet nicht auf cmd/scan
    code, saved, buf = run_custom(tmp_path, fake, members=[0x180])
    assert code == 0
    assert [t["member"] for t in saved["tasks"] if t["stage"] == "A1"] == ["0x100", "0x180"]
    assert [t["member"] for t in saved["tasks"] if t["stage"] == "D"] == ["0x180"]
    assert "Extra-Member 0x100 ohne Antwort, Stufe D uebersprungen" in buf.getvalue()


# ----------------------------------------------------------------------
# Switch (f8/f9) und Min/Max (--minmax)
# ----------------------------------------------------------------------
def make_runner(tmp_path, fake):
    return ar.Runner(fake, ar.new_state(100, True), state_path=str(tmp_path / "s.json"),
                     timing=FAST, log=make_log(io.StringIO()), smoke=True, delay_ms=100)


def test_switch_parse_and_helpers():
    assert ar.parse_switch(None) == 0xFA
    assert ar.parse_switch("fa") == 0xFA
    assert ar.parse_switch("0xF8") == 0xF8
    with pytest.raises(ValueError):
        ar.parse_switch("0xfb")
    assert ar.entry_switch({}) == 0xFA
    assert ar.switch_cmd(0xF8) == "f8"
    assert ar.switch_state(0xF9) == "0xf9"


def test_collect_hits_ignores_switch_tasks():
    tasks = [
        {"member": "0x180", "items": [{"index": "0x0001", "raw": "0x0002", "status": "ok"}]},
        {"member": "0x180", "switch": "0xf8",
         "items": [{"index": "0x0002", "raw": "0x0010", "status": "ok"}]},
    ]
    assert ar.collect_hits(tasks) == [("0x180", "0x0001")]


def test_read_payload_switch_only_if_not_default(tmp_path):
    fake = FakeTransport()
    runner = make_runner(tmp_path, fake)
    runner._read_one("0x180", "0x000c")
    runner._read_one("0x180", "0x000c", 0xF8)
    payloads = fake.sent_of("cmd/read")
    assert payloads[0] == {"member": "0x180", "index": "0x000c"}
    assert payloads[1] == {"member": "0x180", "index": "0x000c", "switch": "f8"}


def test_scan_payload_switch_only_if_not_default(tmp_path):
    fake = FakeTransport()
    runner = make_runner(tmp_path, fake)
    task = ar.plan_tasks([0x180], ar.SMOKE_STAGES)[0]
    runner._run_scan_task(task)
    task_sw = dict(task, items=[], done=False, switch="0xf8")
    runner._run_scan_task(task_sw)
    scans = fake.sent_of("cmd/scan")
    assert "switch" not in scans[0]
    assert scans[1]["switch"] == "f8"
    assert task_sw["done"] and task_sw["items"]


def test_minmax_off_by_default(tmp_path):
    fake = FakeTransport()
    code, state_path, _ = run_fake(tmp_path, fake, smoke=True)
    assert code == 0
    assert "minmax" not in ar.load_state(str(state_path))
    assert not any(p.get("switch") for p in fake.sent_of("cmd/read"))


def test_minmax_reads_f8_and_f9_per_hit(tmp_path):
    fake = FakeTransport(minmax={(0x180, 0x0002, 0xF8): "0x0010",
                                 (0x180, 0x0002, 0xF9): "0x0200"})
    code, state_path, _ = run_fake(tmp_path, fake, smoke=True, minmax=True)
    assert code == 0
    saved = ar.load_state(str(state_path))
    hits = ar.collect_hits(saved["tasks"])
    assert len(saved["minmax"]) == 2 * len(hits)
    entries = {(e["member"], e["index"], e["switch"]): e for e in saved["minmax"]}
    assert entries[("0x180", "0x0002", "0xf8")]["raw"] == "0x0010"
    assert entries[("0x180", "0x0002", "0xf9")]["raw"] == "0x0200"
    assert all(e["status"] == "ok" for e in saved["minmax"])
    minmax_reads = [p for p in fake.sent_of("cmd/read") if p.get("switch")]
    assert len(minmax_reads) == 2 * len(hits)
    assert {p["switch"] for p in minmax_reads} == {"f8", "f9"}
    # Rereads bleiben Standard-Switch und ohne switch-Feld
    assert len(saved["rereads"]) == len(hits)
    assert all("switch" not in r for r in saved["rereads"])
    assert fake.sent[-1] == ("cmd/sniff", {"on": False, "ids": [], "types": []})


def test_minmax_resume_skips_existing(tmp_path):
    code, state_path, _ = run_fake(tmp_path, FakeTransport(), smoke=True, minmax=True,
                                   name="auto_r1")
    assert code == 0
    saved = ar.load_state(str(state_path))
    total = len(saved["minmax"])
    saved["minmax"] = saved["minmax"][:4]
    fake2 = FakeTransport()
    code2, state_path2, _ = run_fake(tmp_path, fake2, state=saved, smoke=True, minmax=True,
                                     name="auto_r2")
    assert code2 == 0
    reads2 = fake2.sent_of("cmd/read")
    assert len([p for p in reads2 if p.get("switch")]) == total - 4
    assert all(p.get("switch") for p in reads2)  # Rereads waren schon fertig
    assert len(ar.load_state(str(state_path2))["minmax"]) == total


def test_minmax_saves_in_steps_of_reread_save_every(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "REREAD_SAVE_EVERY", 5)
    saves = []
    real_save = ar.save_state

    def spy(path, state):
        saves.append(len(state.get("minmax", [])))
        real_save(path, state)

    monkeypatch.setattr(ar, "save_state", spy)
    code, state_path, _ = run_fake(tmp_path, FakeTransport(), smoke=True, minmax=True)
    assert code == 0
    total = len(ar.load_state(str(state_path))["minmax"])
    assert total > 10
    assert {5, 10, 15} <= set(saves)
    assert all(n % 5 == 0 or n == total for n in saves if n)


def test_read_result_without_switch_field_counts_as_default(tmp_path):
    fake = FakeTransport(omit_switch=True)
    code, state_path, _ = run_fake(tmp_path, fake, smoke=True)
    assert code == 0
    saved = ar.load_state(str(state_path))
    assert len(saved["rereads"]) == len(ar.collect_hits(saved["tasks"]))
    assert all(r["status"] == "ok" for r in saved["rereads"])


def test_rx_item_ts_from_u_only_when_positive():
    import queue as _q
    from types import SimpleNamespace

    transport = object.__new__(ar.PahoTransport)
    transport._rx = io.StringIO()
    transport._log = logging.getLogger("test-rx-ts")
    transport._queue = _q.Queue()
    items = [
        {"t": 1000, "u": 1700000000123, "id": "180", "d": "3180100000", "tx": False},
        {"t": 1100, "u": 0, "id": "180", "d": "3180100000", "tx": False},
        {"t": 1200, "id": "180", "d": "3180100000", "tx": False},
    ]
    msg = SimpleNamespace(topic=ar.PREFIX + "rx", payload=json.dumps(items).encode("utf-8"))
    transport._on_message(None, None, msg)
    kind, frames = transport._queue.get_nowait()
    assert kind == "rx"
    assert frames[0]["ts"] == 1700000000.123
    assert "ts" not in frames[1]
    assert "ts" not in frames[2]
    assert transport._rx.getvalue().count("\n") == 3
    assert '"u":1700000000123' in transport._rx.getvalue()
