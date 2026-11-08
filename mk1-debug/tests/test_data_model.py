"""Tests fuer gui/data_model.py (ohne Qt). Alle Dateien liegen in tmp_path."""
import json
import sys
from pathlib import Path

GUI_DIR = Path(__file__).resolve().parents[1] / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

from data_model import PRIO_CSV, PRIO_STATE, Measurements  # noqa: E402


# ---- Hilfsfunktionen ----

def _write_json(tmp_path, name, obj):
    path = tmp_path / name
    path.write_text(json.dumps(obj), encoding="utf-8")
    return str(path)


def _write_csv(tmp_path, name, lines):
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig", newline="")
    return str(path)


CSV_HEADER = "index;name_ref;typ_ref;owner_ref;name_mkgit;typ_mkgit;0x180;0x200;0x201;0x202;0x480"


def _csv_lines():
    return [
        CSV_HEADER,
        # Index 0x0208: 0x180 mit Min/Max-Text, 0x200 mit Rohwert + Text,
        # 0x201 nicht unterstuetzt, 0x202 mit Min/Max-Rohwerten, 0x480 leer
        "0x0208;NAME_A;;;;;52.0 [0x0208] (min 10.0 / max 60.0);"
        "0x00e6 (23.0 (min 5.0 / max 30.0));nicht unterst.;0x0125 (min 0x0032 / max 0x00c8);",
        # Index 0x0301: nur 0x480 hat einen Wert
        "0x0301;NAME_B;;;;;;;;;0x0064",
        # Latenz-Zeilen (erste Spalte leer, Label in Spalte 2)
        ";ANTWORTZEIT Median ms;;;;;20;20;20;20;20",
        ";ANTWORTZEIT p90 ms;;;;;25;26;25;26;25",
        ";ANTWORTZEIT max ms;;;;;1041;278;287;263;244",
    ]


def _state_task(member, items, switch=None):
    task = {"member": member, "items": items}
    if switch is not None:
        task["switch"] = switch
    return task


# ---- load_state ----

def test_state_nur_switch_fa_fuer_werte(tmp_path):
    state = {
        "tasks": [
            _state_task("0x180", [{"index": "0x0001", "raw": "0x0010", "status": "ok"}], switch="0xf8"),
            _state_task("0x180", [{"index": "0x0002", "raw": "0x0011", "status": "ok"}], switch="0xf9"),
            _state_task("0x180", [{"index": "0x0003", "raw": "0x0012", "status": "ok"}], switch="0xFA"),
            _state_task("0x180", [{"index": "0x0004", "raw": "0x0013", "status": "ok"}]),
        ],
        "rereads": [
            {"member": "0x180", "index": "0x0005", "switch": "0xf8", "raw": "0x0014", "status": "ok"},
        ],
    }
    meas = Measurements()
    meas.load_state(_write_json(tmp_path, "s.json", state))
    assert meas.cells(1) == {}
    assert meas.cells(2) == {}
    assert meas.cells(5) == {}
    assert meas.cells(3)[0x180].raw == 0x12
    assert meas.cells(4)[0x180].raw == 0x13
    assert meas.indices == [3, 4]


def test_state_rereads_werden_angehaengt_raw_ist_letzter(tmp_path):
    state = {
        "tasks": [_state_task("0x180", [{"index": "0x0001", "raw": "0x00fa", "status": "ok"}])],
        "rereads": [
            {"member": "0x180", "index": "0x0001", "raw": "0x00fb", "status": "ok"},
            {"member": "0x180", "index": "0x0001", "raw": "0x00fc", "status": "ok"},
            {"member": "0x180", "index": "0x0001", "raw": "0x0000", "status": "timeout"},
        ],
    }
    meas = Measurements()
    meas.load_state(_write_json(tmp_path, "s.json", state))
    cell = meas.cells(1)[0x180]
    assert cell.raws == [0xFA, 0xFB, 0xFC]
    assert cell.raw == 0xFC
    assert cell.status == "ok"
    assert cell.prio == PRIO_STATE


def test_state_minmax_setzt_min_und_max_raw(tmp_path):
    state = {
        "tasks": [_state_task("0x180", [{"index": "0x0001", "raw": "0x0020", "status": "ok"}])],
        "minmax": [
            {"member": "0x180", "index": "0x0001", "switch": "0xf8", "raw": "0x000a", "status": "ok"},
            {"member": "0x180", "index": "0x0001", "switch": "0xf9", "raw": "0x003c", "status": "ok"},
            # ungueltige Eintraege werden ignoriert
            {"member": "0x180", "index": "0x0001", "switch": "0xf9", "raw": "0x0001", "status": "timeout"},
            {"member": "0x180", "index": "0x0001", "switch": "0xf8", "raw": None, "status": "ok"},
        ],
    }
    meas = Measurements()
    meas.load_state(_write_json(tmp_path, "s.json", state))
    cell = meas.cells(1)[0x180]
    assert cell.min_raw == 0x0A
    assert cell.max_raw == 0x3C
    assert cell.raw == 0x20


def test_state_unsupported_und_ok_mischung(tmp_path):
    state = {
        "tasks": [
            _state_task("0x180", [
                {"index": "0x0005", "raw": "0x8000", "status": "unsupported"},
                {"index": "0x0006", "raw": "0x0010", "status": "ok"},
                {"index": "0x0006", "raw": "0x8000", "status": "unsupported"},
            ]),
        ],
    }
    meas = Measurements()
    meas.load_state(_write_json(tmp_path, "s.json", state))
    assert meas.cells(5)[0x180].status == "unsupported"
    assert meas.cells(5)[0x180].raws == []
    # ok bleibt ok, auch wenn spaeter unsupported kommt
    assert meas.cells(6)[0x180].status == "ok"
    assert meas.members == [0x180]
    assert meas.indices == [6]


def test_state_indices_und_members_nur_mit_ok(tmp_path):
    state = {
        "tasks": [
            _state_task("0x180", [{"index": "0x0001", "raw": "0x0001", "status": "ok"}]),
            _state_task("0x200", [{"index": "0x0002", "raw": "0x8000", "status": "unsupported"}]),
            _state_task("0x201", [{"index": "0x0003", "raw": "0x0000", "status": "timeout"}]),
            _state_task("0x202", [{"index": "0x0004", "raw": "0x0000", "status": "timeout"}]),
        ],
    }
    meas = Measurements()
    meas.load_state(_write_json(tmp_path, "s.json", state))
    assert meas.members == [0x180]
    assert meas.indices == [1]
    # Zellen ohne ok bleiben im Modell, zaehlen aber nicht
    assert 0x201 not in meas.members
    assert meas.cells(3)[0x201].status == "none"
    assert 3 not in meas.indices


def test_state_sources_und_rueckgabe(tmp_path):
    state = {
        "tasks": [_state_task("0x180", [
            {"index": "0x0001", "raw": "0x0001", "status": "ok"},
            {"index": "0x0002", "raw": "0x8000", "status": "unsupported"},
        ])],
    }
    path = _write_json(tmp_path, "auto_test_state.json", state)
    meas = Measurements()
    assert meas.load_state(path) == 2
    assert meas.sources == ["auto_test_state.json"]


# ---- load_csv ----

def test_csv_zellen_werden_geparst(tmp_path):
    meas = Measurements()
    n = meas.load_csv(_write_csv(tmp_path, "bekannt.csv", _csv_lines()))
    # ok-Zellen: 0x0208/0x180, 0x0208/0x200, 0x0208/0x202, 0x0301/0x480
    assert n == 4

    a = meas.cells(0x0208)
    assert a[0x180].status == "ok"
    assert a[0x180].raws == [0x0208]
    assert a[0x180].mm_text == "(min 10.0 / max 60.0)"
    assert a[0x180].min_raw is None and a[0x180].max_raw is None
    assert a[0x180].prio == PRIO_CSV

    assert a[0x200].raws == [0x00E6]
    assert a[0x200].mm_text == "(min 5.0 / max 30.0))"

    assert a[0x201].status == "unsupported"
    assert a[0x201].raws == []

    assert a[0x202].raws == [0x0125]
    assert a[0x202].min_raw == 0x0032
    assert a[0x202].max_raw == 0x00C8
    assert a[0x202].mm_text == ""

    assert 0x480 not in a  # leere Zelle wird uebersprungen
    assert meas.cells(0x0301)[0x480].raws == [0x0064]


def test_csv_members_und_indices(tmp_path):
    meas = Measurements()
    meas.load_csv(_write_csv(tmp_path, "bekannt.csv", _csv_lines()))
    assert meas.members == [0x180, 0x200, 0x202, 0x480]  # 0x201 nur unsupported
    assert meas.indices == [0x0208, 0x0301]


def test_csv_latenz_zeilen_landen_in_latency(tmp_path):
    meas = Measurements()
    meas.load_csv(_write_csv(tmp_path, "bekannt.csv", _csv_lines()))
    assert meas.latency[0x180] == {"med": 20.0, "p90": 25.0, "max": 1041.0}
    assert meas.latency[0x200] == {"med": 20.0, "p90": 26.0, "max": 278.0}
    # Latenzzeilen sind keine Messindizes
    assert 0 not in meas.by_index
    assert "" not in meas.by_index
    assert meas.indices == [0x0208, 0x0301]


def test_csv_utf8_sig_bom_und_semikolon(tmp_path):
    # BOM am Dateianfang darf die Member-Spalten nicht kaputt machen
    meas = Measurements()
    n = meas.load_csv(_write_csv(tmp_path, "bekannt.csv", _csv_lines()))
    assert n > 0
    assert meas.cells(0x0208)[0x180].raws == [0x0208]
    assert meas.sources == ["bekannt.csv"]


# ---- Merge: State vs. CSV ----

def _merge_state(tmp_path):
    state = {"tasks": [_state_task("0x180", [{"index": "0x0208", "raw": "0x0209", "status": "ok"}])]}
    return _write_json(tmp_path, "auto_state.json", state)


def _merge_csv(tmp_path):
    return _write_csv(tmp_path, "bekannt.csv", [
        CSV_HEADER,
        "0x0208;NAME_A;;;;;52.0 [0x0208] (min 10.0 / max 60.0);;;;",
        "0x0301;NAME_B;;;;;;;;;0x0064",
    ])


def test_merge_state_vor_csv_state_zuerst(tmp_path):
    meas = Measurements()
    meas.load_state(_merge_state(tmp_path))
    meas.load_csv(_merge_csv(tmp_path))
    cell = meas.cells(0x0208)[0x180]
    assert cell.raws == [0x0209]
    assert cell.raw == 0x0209
    assert cell.prio == PRIO_STATE
    # CSV-only Zelle bleibt erhalten
    assert meas.cells(0x0301)[0x480].raws == [0x0064]


def test_merge_state_vor_csv_csv_zuerst(tmp_path):
    meas = Measurements()
    meas.load_csv(_merge_csv(tmp_path))
    meas.load_state(_merge_state(tmp_path))
    cell = meas.cells(0x0208)[0x180]
    assert cell.raws == [0x0209]
    assert cell.raw == 0x0209
    assert cell.prio == PRIO_STATE
    assert meas.cells(0x0301)[0x480].raws == [0x0064]


def test_merge_members_und_indices_ladereihenfolge_egal(tmp_path):
    a = Measurements()
    a.load_state(_merge_state(tmp_path))
    a.load_csv(_merge_csv(tmp_path))
    b = Measurements()
    b.load_csv(_merge_csv(tmp_path))
    b.load_state(_merge_state(tmp_path))
    assert a.members == b.members == [0x180, 0x480]
    assert a.indices == b.indices == [0x0208, 0x0301]


# ---- load_any ----

def test_load_any_waehlt_nach_endung(tmp_path, monkeypatch):
    rx_calls = []

    def fake_rx(self, path):
        rx_calls.append(path)
        return 7

    monkeypatch.setattr(Measurements, "load_rx", fake_rx)

    csv_path = _write_csv(tmp_path, "bekannt.csv", _csv_lines())
    state_path = _write_json(tmp_path, "auto_state.json", {"tasks": [
        _state_task("0x180", [{"index": "0x0001", "raw": "0x0001", "status": "ok"}])]})
    rx_path = str(tmp_path / "auto_rx.jsonl")
    Path(rx_path).write_text("", encoding="utf-8")

    meas = Measurements()
    assert meas.load_any(csv_path) == 4
    assert meas.sources == ["bekannt.csv"]
    assert meas.latency  # CSV-Latenz wurde gelesen

    meas2 = Measurements()
    assert meas2.load_any(state_path) == 1
    assert meas2.sources == ["auto_state.json"]
    assert meas2.cells(1)[0x180].raw == 1

    assert meas.load_any(rx_path) == 7
    assert rx_calls == [rx_path]


def test_load_any_endung_ist_nicht_case_sensitiv(tmp_path, monkeypatch):
    monkeypatch.setattr(Measurements, "load_rx", lambda self, path: 0)
    csv_path = tmp_path / "BEKANNT.CSV"
    csv_path.write_text("\n".join(_csv_lines()) + "\n", encoding="utf-8-sig")
    meas = Measurements()
    assert meas.load_any(str(csv_path)) == 4
    assert meas.sources == ["BEKANNT.CSV"]
