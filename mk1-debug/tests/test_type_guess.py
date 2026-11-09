"""Tests fuer gui/type_guess.py (ohne Qt, ohne paho)."""
import csv
import json
import os
import sys
from pathlib import Path

GUI_DIR = Path(__file__).resolve().parents[1] / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

import pytest  # noqa: E402

import type_guess  # noqa: E402
from type_guess import analyse, guess_types, load_mkgit_table  # noqa: E402

NEEDS_TABLE_H = pytest.mark.skipif(not type_guess.MKGIT_DEFAULT.exists(), reason="ElsterTable.h fehlt (ELSTER_TABLE_H setzen, Quelle: ha-stiebel-control)")
LOG_DIR = Path(os.environ.get("STIEBEL_LOG_DIR") or Path(__file__).resolve().parents[1] / "logs")
MITSCHNITT = LOG_DIR / "mitschnitt.jsonl"


def _read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def _top(raws):
    result = guess_types(raws)
    assert result, f"keine Kandidaten fuer {raws}"
    return result[0]


def _build_state_from_mitschnitt(path: Path) -> dict:
    """Scan KESSEL 0x00-0xFF aus dem Mitschnitt rekonstruieren.

    Requests: rx/tx-Frames mit id 0x680 (tx true). Member-ID aus d0/d1:
    ((d0 & 0xF0) << 3) | d1 & 0x7F. Index: d2 (Kurz) oder d3<<8|d4 (Lang, wenn d2 == 0xFA).
    Antwort: erster Frame mit Absender = Member, msg_type 2, gleicher dekodierter Index,
    Zeitpunkt nach dem Request, max. 500 ms. Ohne Antwort: kein Item (Timeout).
    """
    import elster

    frames = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                frames.append(json.loads(line))

    responses = []
    for fr in frames:
        if fr["tx"] or fr["id"] == "0x680":
            continue
        data = bytes.fromhex(fr["d"])
        parsed = elster.parse_frame(int(fr["id"], 16), data)
        if parsed and parsed["msg_type"] == 2:
            # Antwort ohne switch-Feld (Kurzlayout oder 0xFA) gilt als 0xFA
            r_sw = parsed.get("switch") or 0xFA
            responses.append((fr["t"], parsed["sender"], parsed["index"], parsed["value"], r_sw))

    # Requests nach Switch trennen: Langlayout bei d[2] in {F7,F8,F9,FA}, sonst Standard 0xFA
    items_by_sw: dict[int, list[dict]] = {}
    for fr in frames:
        if not (fr["tx"] and fr["id"] == "0x680"):
            continue
        d = bytes.fromhex(fr["d"])
        member = ((d[0] & 0xF0) << 3) | (d[1] & 0x7F)
        if d[2] in (0xF7, 0xF8, 0xF9, 0xFA):
            sw, index = d[2], (d[3] << 8) | d[4]
        else:
            sw, index = 0xFA, d[2]
        raw = None
        for t, sender, r_index, value, r_sw in responses:
            if sender == member and r_index == index and r_sw == sw and 0 <= t - fr["t"] <= 500:
                raw = value
                break
        if raw is None:
            continue  # Timeout, zaehlt im Scan als ohne Antwort
        status = "unsupported" if raw == 0x8000 else "ok"
        items_by_sw.setdefault(sw, []).append(
            {"index": f"0x{index:04x}", "raw": f"0x{raw:04x}", "status": status})

    tasks = [{
        "stage": "A1", "member": "0x180", "format": "short",
        "from": "0x0000", "to": "0x00ff", "done": True,
        "items": items_by_sw.get(0xFA, []),
    }]
    for sw in sorted(items_by_sw):
        if sw == 0xFA:
            continue
        tasks.append({
            "stage": "MM", "member": "0x180", "format": "long", "switch": f"0x{sw:02x}",
            "from": "0x0000", "to": "0x00ff", "done": True, "items": items_by_sw[sw],
        })

    return {
        "version": 1,
        "started": "2026-10-20T19:00:00",
        "finished": "2026-10-20T19:05:00",
        "delay_ms": 100,
        "passive_ids": ["0x100", "0x180", "0x201"],
        "probe": {"index": "0x000b", "items": []},
        "members": ["0x180"],
        "silent": [],
        "tasks": tasks,
        "rereads": [],
    }


# --- load_mkgit_table -------------------------------------------------------

@NEEDS_TABLE_H
def test_load_mkgit_table_liefert_viele_eintraege():
    table = load_mkgit_table()
    assert len(table) > 3000
    assert table[0x0112] == ("PROGRAMMSCHALTER", "et_betriebsart")


@NEEDS_TABLE_H
def test_load_mkgit_table_typ_null_ist_et_default():
    table = load_mkgit_table()
    assert all(typ for _name, typ in table.values())
    assert "0" not in {typ for _name, typ in table.values()}


# --- guess_types ------------------------------------------------------------

def test_guess_25_1_ist_dec_val_hoch():
    name, score, sample = _top([0x00FB])
    assert name == "et_dec_val"
    assert score >= 0.7
    assert sample == "25.1"


def test_guess_0001_ist_bool_hoch():
    name, score, sample = _top([0x0001])
    assert name == "et_bool"
    assert score >= 0.85
    assert sample == "on"


def test_guess_0100_ist_little_bool():
    name, score, sample = _top([0x0100])
    assert name == "et_little_bool"
    assert score >= 0.85
    assert sample == "on"


def test_guess_steigende_werte_ergeben_zaehler():
    name, score, _sample = _top([10, 11, 12, 13])
    assert name == "zaehler"
    assert score >= 0.8


def test_guess_nur_fallende_werte_kein_zaehler():
    names = [n for n, _s, _x in guess_types([13, 12, 11, 10])]
    assert "zaehler" not in names


def test_guess_0x8000_wird_ausgeschlossen():
    assert guess_types([0x8000]) == []
    assert guess_types([0x8000, 0x00FB]) == guess_types([0x00FB])


def test_guess_nur_nullen_werden_gedaempft():
    nur_null = guess_types([0x0000])
    assert nur_null, "Nullen sollen trotzdem Kandidaten haben"
    assert all(score <= 0.5 for _n, score, _x in nur_null)


# --- analyse --------------------------------------------------------------

@NEEDS_TABLE_H
def test_analyse_synthetisch_nach_7_5(tmp_path):
    state = {
        "version": 1,
        "started": "2026-10-20T20:00:00",
        "finished": None,
        "delay_ms": 100,
        "passive_ids": ["0x100", "0x180"],
        "probe": {"index": "0x000b", "items": [{"id": "0x180", "raw": "0x8000",
                                                "status": "unsupported"}]},
        "members": ["0x180", "0x201"],
        "silent": ["0x100"],
        "tasks": [
            {"stage": "A1", "member": "0x180", "format": "short", "from": "0x0000",
             "to": "0x00ff", "done": True,
             "items": [{"index": "0x000c", "raw": "0x0058", "status": "ok"},
                       {"index": "0x0001", "raw": "0x8000", "status": "unsupported"}]},
            {"stage": "A2", "member": "0x180", "format": "long", "from": "0x0000",
             "to": "0x00ff", "done": True,
             "items": [{"index": "0x000c", "raw": "0x0059", "status": "ok"}]},
            {"stage": "B", "member": "0x180", "format": "auto", "from": "0x0100",
             "to": "0x0fff", "done": False,
             "items": [{"index": "0x0112", "raw": "0x0200", "status": "ok"}]},
        ],
        "rereads": [{"member": "0x180", "index": "0x000c", "pass": 1,
                     "t": "2026-10-20T23:00:00", "raw": "0x0059", "status": "ok"}],
    }
    state_path = tmp_path / "auto_test_state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    prefix = str(tmp_path / "auto_test")

    csv_path, md_path = analyse(str(state_path), prefix)

    rows = {r["index"]: r for r in _read_csv(Path(csv_path))}
    assert set(rows) == {"0x000c", "0x0112"}  # 0x0001 ist 0x8000 -> keine Zeile
    assert rows["0x000c"]["konstant"] == "nein"  # Scan A1 0x58, Scan A2 0x59, Reread 0x59
    assert rows["0x000c"]["type_ref"] == "et_dec_val"
    assert rows["0x000c"]["raws"] == "0x0058,0x0059,0x0059"
    assert rows["0x0112"]["type_ref"] == "et_betriebsart"
    assert rows["0x0112"]["konflikt"] == ""

    md = Path(md_path).read_text(encoding="utf-8")
    assert "## Vergleich short/long" in md
    assert "abweichend 1" in md  # 0x000c: short 0x0058 vs long 0x0059
    assert "## Typkonflikte" in md
    assert "## Paar-Hinweise" in md


@NEEDS_TABLE_H
def test_analyse_paar_hinweis_fuer_wh_kwh(tmp_path):
    state = {
        "version": 1, "started": None, "finished": None, "delay_ms": 100,
        "members": ["0x180"], "silent": [], "passive_ids": [],
        "probe": {"index": "0x000b", "items": []},
        "tasks": [{
            "stage": "C", "member": "0x180", "format": "auto",
            "from": "0x4e00", "to": "0x4fff", "done": True,
            "items": [{"index": "0x4e40", "raw": "0x0123", "status": "ok"},
                      {"index": "0x4e41", "raw": "0x0004", "status": "ok"}],
        }],
        "rereads": [],
    }
    state_path = tmp_path / "auto_pair_state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _csv_path, md_path = analyse(str(state_path), str(tmp_path / "auto_pair"))
    md = Path(md_path).read_text(encoding="utf-8")
    assert "Paar-Hinweise" in md


@pytest.mark.skipif(not MITSCHNITT.exists(), reason="Mitschnitt fehlt (logs/ nicht im Repo)")
def test_analyse_mitschnitt_aussentemp_et_dec_val(tmp_path):
    state = _build_state_from_mitschnitt(MITSCHNITT)
    ok_items = [i for i in state["tasks"][0]["items"] if i["status"] == "ok"]
    assert len(ok_items) > 10, "Mitschnitt ergab zu wenige Antworten"
    state_path = tmp_path / "auto_mitschnitt_state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    csv_path, _md_path = analyse(str(state_path), str(tmp_path / "auto_mitschnitt"))

    rows = {r["index"]: r for r in _read_csv(Path(csv_path))}
    row = rows["0x000c"]
    assert row["name_ref"] == "AUSSENTEMP"
    assert row["raws"].startswith("0x0058")
    assert row["kandidaten"].startswith("et_dec_val:")


# --- Min/Max (switch 0xF8/0xF9) ----------------------------------------------

def test_guess_minmax_plausibel_gibt_bonus():
    ohne = dict((n, s) for n, s, _x in guess_types([0x00FB]))
    mit = dict((n, s) for n, s, _x in guess_types([0x00FB], (0x00E6, 0x0100)))
    assert ohne["et_dec_val"] == 0.8
    assert mit["et_dec_val"] == pytest.approx(0.9)


def test_guess_minmax_unplausibel_kein_bonus():
    # Max 0x7000 = 2867.2 Grad, ausserhalb -50..150 fuer et_dec_val
    mit = dict((n, s) for n, s, _x in guess_types([0x00FB], (0x00E6, 0x7000)))
    assert mit["et_dec_val"] == 0.8


def test_guess_minmax_bonus_max_1():
    top = guess_types([0x0001], (0x0000, 0x0001))
    assert top[0][0] == "et_bool" and top[0][1] == 1.0


@NEEDS_TABLE_H
def test_analyse_minmax_getrennt_und_csv_spalten(tmp_path):
    state = {
        "version": 1, "started": None, "finished": None, "delay_ms": 100,
        "members": ["0x180"], "silent": [], "passive_ids": [],
        "probe": {"index": "0x000b", "items": []},
        "tasks": [{
            "stage": "A1", "member": "0x180", "format": "short", "from": "0x0000",
            "to": "0x00ff", "done": True,
            "items": [{"index": "0x000c", "raw": "0x00fb", "status": "ok"}],
        }],
        # Reread mit switch f8 darf nicht in die Standard-Rohwerte rutschen
        "rereads": [{"member": "0x180", "index": "0x000c", "pass": 1, "t": None,
                     "raw": "0x0050", "status": "ok", "switch": "0xf8"}],
        "minmax": [
            {"member": "0x180", "index": "0x000c", "switch": "0xf8", "raw": "0x00e6",
             "status": "ok"},
            {"member": "0x180", "index": "0x000c", "switch": "0xf9", "raw": "0x0100",
             "status": "ok"},
        ],
    }
    state_path = tmp_path / "auto_mm_state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    csv_path, md_path = analyse(str(state_path), str(tmp_path / "auto_mm"))

    rows = {r["index"]: r for r in _read_csv(Path(csv_path))}
    row = rows["0x000c"]
    assert row["raws"] == "0x00fb"
    assert row["min"] == "0x00e6"
    assert row["max"] == "0x0100"
    assert row["kandidaten"].startswith("et_dec_val:0.90")

    md = Path(md_path).read_text(encoding="utf-8")
    assert "## Min/Max (switch 0xF8/0xF9)" in md
    assert "Switch" in md  # Spalte in der Stufen-Tabelle


@NEEDS_TABLE_H
def test_analyse_minmax_nur_status_ok_zaehlt(tmp_path):
    state = {
        "version": 1, "started": None, "finished": None, "delay_ms": 100,
        "members": ["0x180"], "silent": [], "passive_ids": [],
        "probe": {"index": "0x000b", "items": []},
        "tasks": [{
            "stage": "A1", "member": "0x180", "format": "short", "from": "0x0000",
            "to": "0x00ff", "done": True,
            "items": [{"index": "0x000c", "raw": "0x00fb", "status": "ok"}],
        }],
        "rereads": [],
        "minmax": [
            {"member": "0x180", "index": "0x000c", "switch": "0xf8", "raw": None,
             "status": "timeout"},
            {"member": "0x180", "index": "0x000c", "switch": "0xf9", "raw": "0x0100",
             "status": "ok"},
        ],
    }
    state_path = tmp_path / "auto_mm2_state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    csv_path, _md = analyse(str(state_path), str(tmp_path / "auto_mm2"))
    row = _read_csv(Path(csv_path))[0]
    assert row["min"] == ""
    assert row["max"] == "0x0100"
