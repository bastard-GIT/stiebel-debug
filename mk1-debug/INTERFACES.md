# mk1-debug – Schnittstellen (verbindlich für alle Teilaufgaben)

Jede Teilaufgabe schreibt NUR die ihr zugewiesenen Dateien. Namen, Signaturen und JSON-Formate unten exakt einhalten. Keine Zugangsdaten in Dateien schreiben. Plan: `../plan/mk1-debug-plan.md`.

Python: `C:\temp\stiebel-Remote\.venv\Scripts\python.exe` (immer mit `-I` starten, wenn Daten fremder Herkunft gelesen werden). Referenztabelle: `C:\temp\stiebel-Remote\referenzen\elster_reference\ElsterTable.json` (Lesehilfe: `ElsterTable.README.md` im selben Ordner).

## 1. MQTT-Topics (Präfix `stiebel-debug/`)

Diese Tabelle gilt für die Debug-Firmware (Präfix `stiebel-debug/`). Die Produktiv-Firmware hat andere Topics, siehe Abschnitt 13.

| Topic | Richtung | Payload (JSON) |
|---|---|---|
| `cmd/sniff` | GUI→ESP | `{"on":true,"ids":["0x180"],"types":[2]}` – leere Listen = alles |
| `cmd/read` | GUI→ESP | `{"member":"0x180","index":"0x4ef0"}`, optional `format`, `switch`, `src`, `timeout_ms` (Abschnitte 7.2, 8.3, 12) |
| `cmd/write` | GUI→ESP | `{"member":"0x201","index":"0x0013","value":"0x0208","format":"auto"}` – nur Debug-Firmware (Abschnitt 10) |
| `cmd/scan` | GUI→ESP | `{"member":"0x500","from":"0x4e00","to":"0x4fff","delay_ms":200}`, optional `format`, `switch` |
| `cmd/probe` | GUI→ESP | `{"index":"0x000b","from_id":"0x000","to_id":"0x7ff","delay_ms":100}` (Abschnitt 7.2) |
| `cmd/stop` | GUI→ESP | `{}` – bricht Scan, Probe, Read und Write ab |
| `rx` | ESP→GUI | `[{"t":123456,"id":"0x180","d":"d200fa01120200","tx":false}]`, optional `"u"` (Unix-ms, Abschnitt 11) |
| `read/result` | ESP→GUI | `{"member":"0x180","index":"0x4ef0","switch":"0xfa","raw":"0x03e8","status":"ok"}`, bei `src` ≠ 0x680 zusätzlich `"src"` |
| `write/result` | ESP→GUI | `{"member":"0x201","index":"0x0013","value":"0x0208","status":"verified","ack":4,"readback":"0x0208"}` (Abschnitt 10.5) |
| `scan/progress` | ESP→GUI | `{"member":"0x500","format":"auto","index":"0x4e40","switch":"0xfa","done":64,"total":512,"state":"running"}` |
| `scan/result` | ESP→GUI | `{"member":"0x500","format":"auto","switch":"0xfa","items":[{"index":"0x4e40","raw":"0x0123","status":"ok"}]}` |
| `probe/progress` | ESP→GUI | `{"index":"0x000b","id":"0x1a0","done":416,"total":2047,"state":"running"}` |
| `probe/result` | ESP→GUI | `{"index":"0x000b","items":[{"id":"0x180","raw":"0x8000","status":"unsupported"}]}` |
| `status` | ESP→GUI | `{"fw":"debug","rx":0,"tx":0,"rx_fill":0,"rx_cap":4096,"overflow":0,"twai_err":0,"bus_off":false,"heap":0,"ntp":false}` |
| `availability` | ESP→GUI | `online` / `offline` (retained, LWT) |

Regeln:
- `t` = Millisekunden seit Boot (uint32). `id` = CAN-ID als `"0x%03x"` (Kleinbuchstaben). `d` = 7 oder 8 Datenbytes als Hex-String ohne Leerzeichen, Kleinbuchstaben (2 Zeichen je Byte). `tx` = true bei vom ESP gesendeten Frames.
- `status` Werte: `ok` | `unsupported` (Rohwert 0x8000) | `timeout`.
- `raw` und `index` sind Strings `"0x%04x"`, `member` ist `"0x%03x"` (CAN-ID des Members).
- Mindestabstand zwischen zwei gesendeten Requests: 100 ms. `delay_ms` unter 100 wird auf 100 angehoben.
- Lese-Requests und (nur Debug-Firmware) Schreib-Requests über cmd/write, siehe Abschnitt 10.

## 2. Elster-Frame-Format (aus `ElsterTable.json`, `frame_format`)

- Eigene CAN-ID (Absender bei Requests): `0x680`. Standard-ID (11 Bit). DLC 7 beim Senden.
- Lese-Request, Index ≤ 0xFF: `[read_id0, read_id1, idx_lo, 0, 0, 0, 0]`
- Lese-Request, Index ≥ 0x100: `[read_id0, read_id1, 0xFA, idx_hi, idx_lo, 0, 0]`
- Antwort: CAN-ID = Member-ID, `data[0] & 0x0F == 2`. `data[2]==0xFA` → `index=(d3<<8)|d4`, `value=(d5<<8)|d6`; sonst `index=d2`, `value=(d3<<8)|d4`.
- Nachrichtenart = `data[0] & 0x0F`. Empfänger = `((data[0]&0xF0)<<3)|data[1]`.
- Wert `0x8000` = nicht unterstützt.

## 3. C++-Header der Firmware (alle in `firmware/`, Namespace `mkdbg`, C++11, nur `<stdint.h>`, `<stddef.h>`, `<string.h>`, `<stdio.h>`, `<stdlib.h>`; kein ESPHome-Include in 3.1–3.3, damit sie mit `g++ -std=c++11` auf dem PC testbar sind)

### 3.1 `ring_buffer.h`
```cpp
namespace mkdbg {
struct Frame { uint32_t t; uint16_t id; uint8_t dlc; uint8_t data[8]; bool tx; };
template <size_t N> class RingBuffer {      // N Zweierpotenz (static_assert)
 public:
  bool push(const Frame &f);                 // false und overflow()++ wenn voll (neuer Frame verworfen)
  bool pop(Frame &out);                      // false wenn leer
  size_t size() const;  size_t capacity() const;  // capacity() == N
  uint32_t overflow() const;  void clear();
};
}
```
Speicher: `Frame buf_[N]` als Member; für große N optional Konstruktor-Parameter `Frame *external` (PSRAM). Single-Producer/Single-Consumer, `volatile`/`std::atomic` für head/tail, ohne Mutex.

### 3.2 `elster_frame.h`
```cpp
namespace mkdbg {
struct Member { const char *name; uint16_t can_id; uint8_t read_id[2]; uint8_t write_id[2]; };
extern const Member MEMBERS[];  extern const size_t MEMBER_COUNT;   // Werte 1:1 aus can_members der ElsterTable.json
const Member *find_member(uint16_t can_id);                         // nullptr wenn unbekannt
size_t build_read_request(const Member &m, uint16_t index, uint8_t out[7]);  // Format Abschnitt 2, gibt 7 zurück
struct Parsed { uint8_t msg_type; uint16_t receiver; uint16_t index; uint16_t value; };
bool parse_frame(const uint8_t *data, size_t len, Parsed &out);     // false wenn len < 7
bool is_response_for(const Parsed &p, uint16_t sender_id, uint16_t member_id, uint16_t index); // msg_type==2 && sender==member && index gleich
}
```
Als `inline`/`static` in der Header-Datei definieren (Header-only; `MEMBERS` als `static const`).

### 3.3 `debug_json.h`
Handgeschriebener Minimal-JSON-Code ohne Bibliothek (Strings in `char`-Puffer, `snprintf`, `strstr`).
```cpp
namespace mkdbg {
struct SniffCmd { bool on; uint16_t ids[16]; uint8_t id_count; uint8_t types[8]; uint8_t type_count; };
struct ReadCmd  { uint16_t member; uint16_t index; };
struct ScanCmd  { uint16_t member; uint16_t from; uint16_t to; uint32_t delay_ms; };   // delay_ms bereits auf >=100 geklemmt
bool parse_sniff(const char *json, SniffCmd &out);
bool parse_read(const char *json, ReadCmd &out);
bool parse_scan(const char *json, ScanCmd &out);       // false bei from>to oder fehlenden Feldern
size_t format_rx_batch(const Frame *frames, size_t count, char *out, size_t cap);   // JSON-Array nach Abschnitt 1; schreibt nie über cap; gibt Länge zurück (0 bei cap zu klein)
size_t format_read_result(uint16_t member, uint16_t index, uint16_t raw, const char *status, char *out, size_t cap);
size_t format_scan_progress(uint16_t index, uint32_t done, uint32_t total, char *out, size_t cap);
size_t format_status(uint32_t rx, uint32_t tx, size_t rx_fill, size_t rx_cap, uint32_t overflow, uint32_t twai_err, bool bus_off, uint32_t heap, char *out, size_t cap);
}
```
`debug_json.h` bindet `ring_buffer.h` ein (für `Frame`). Hex-Zahlen in Eingaben sind Strings `"0x…"` oder `"0X…"` oder Dezimal.

### 3.4 `can_debug.h` (Glue, spätere Welle)
Bindet 3.1–3.3 und ESPHome ein. Stellt bereit (global, aufrufbar aus YAML-Lambdas):
```cpp
void dbg_on_frame(uint32_t can_id, const std::vector<uint8_t> &x);   // aus canbus on_frame
void dbg_on_command(const std::string &topic_suffix, const std::string &payload);  // sniff|read|write|scan|probe|stop
void dbg_loop();                                                      // aus interval: 20ms – RX-Ringpuffer leeren, Batch publizieren, Scanner/Read-State-Machine, TX-Takt
void dbg_publish_status();                                            // aus interval: 5s
```
MQTT-Veröffentlichung über `id(mqtt_client).publish(topic, payload, len, qos, retain)`.

## 4. YAML (Welle 1: Gerüst)
- `mk1-debug.yaml`: Substitutions wie Mk1 (`can_tx_pin: GPIO18`, `can_rx_pin: GPIO16`, `can_id_pc: "0x680"`, `can_bitrate: 50kbps`, Ethernet-Pins GPIO13/11/12/14/10/9), Gerätename `mk1-debug`, Pakete `firmware/board_esp32s3_espidf.yaml`, `firmware/network_ethernet_w5500.yaml`, `firmware/can_debug.yaml`. `api:` mit `encryption: key: !secret debug_api_key`, `ota: platform: esphome`, `mqtt:` mit `broker/port/username/password` aus `!secret mqtt_server|mqtt_port|mqtt_username|mqtt_password`, `id: mqtt_client`, `discovery: false`, `topic_prefix: stiebel-debug`, Birth/Will auf `stiebel-debug/availability` (`online`/`offline`, retained), `on_message` für `stiebel-debug/cmd/sniff|read|write|scan|probe|stop` (mk1-debug.yaml), die `dbg_on_command(<Suffix>, x)` aufrufen. Unbekannte Suffixe loggen nur eine Warnung. `includes:` die Header aus Abschnitt 3.
- `firmware/can_debug.yaml`: `canbus: esp32_can`, `id: my_can`, Pins/ID/Bitrate aus Substitutions, `use_extended_id: false`, `rx_queue_len: 512`, `tx_queue_len: 64`, `on_frame` mit `can_id: 0`, `can_id_mask: 0` → `dbg_on_frame(can_id, x);`; `interval:` 20 ms → `dbg_loop();` und 5 s → `dbg_publish_status();`.

## 5. Python-Module der GUI (alle in `gui/`)

### 5.1 `elster.py` (kein Qt, kein paho)
```python
class ElsterDb:
    def __init__(self, json_path: str | None = None): ...      # Standard: ../referenzen/elster_reference/ElsterTable.json relativ zu elster.py
    members: list[dict]                                         # Einträge aus can_members (name, can_id, read_id, write_id, ...)
    def member_by_id(self, can_id: int) -> dict | None
    def member_name(self, can_id: int) -> str                   # "UNBEKANNT_0x%03x" falls nicht gefunden
    def signal_by_index(self, index: int) -> dict | None
    def signal_name(self, index: int) -> str                    # "UNK_0x%04x" falls nicht gefunden
    def search_signals(self, text: str) -> list[dict]           # Teilstring, case-insensitiv, auf name und index
def parse_frame(can_id: int, data: bytes) -> dict | None        # None wenn len(data) < 7; sonst {"sender","receiver","msg_type","msg_type_name","index","value"}
def decode_value(type_name: str, raw: int) -> str               # Regeln aus ElsterTable.json types / KElsterTable.cpp SetValueType; raw 0x8000 -> "-255"
def build_read_request(member_can_id: int, index: int, db: ElsterDb) -> tuple[int, bytes]   # (0x680, 7 Bytes) nach Abschnitt 2
```

### 5.2 `mqtt_client.py` (PySide6.QtCore, paho-mqtt 1.6.x API)
```python
class MqttBridge(QObject):
    connected = Signal(bool)
    rx_frames = Signal(list)         # Liste von dict {"t","id"(int),"data"(bytes),"tx"(bool)}
    read_result = Signal(dict)       # Payload von read/result, member/index/raw als int
    scan_progress = Signal(dict)
    scan_result = Signal(dict)       # items mit index/raw als int
    status = Signal(dict)
    availability = Signal(str)
    def connect_to(self, host: str, port: int, user: str, password: str) -> None
    def disconnect_from(self) -> None
    def send_sniff(self, on: bool, ids: list[int], types: list[int]) -> None
    def send_read(self, member: int, index: int) -> None
    def send_scan(self, member: int, from_idx: int, to_idx: int, delay_ms: int) -> None
    def send_stop(self) -> None
```
paho-Loop in eigenem Thread (`loop_start`); Signale nur über Qt-Signale (thread-sicher), keine direkten Widget-Zugriffe. Das Passwort wird nie gespeichert oder geloggt.

### 5.3 Tabs (je ein `QWidget`, jeweils eigene Datei)
- `tab_live.py`: `class LiveTab(QWidget)`; `__init__(self, db: ElsterDb, bridge: MqttBridge)`; Modell `FrameTableModel(QAbstractTableModel)`.
- `tab_query.py`: `class QueryTab(QWidget)`; gleiche Signatur.
- `tab_scanner.py`: `class ScannerTab(QWidget)`; gleiche Signatur.
- `tab_status.py`: `class StatusTab(QWidget)`; `__init__(self, bridge: MqttBridge)`.
- `export_io.py` (kein Qt): `save_frames_csv(path, rows)`, `save_frames_jsonl(path, rows)`, `load_frames_jsonl(path) -> list[dict]`, `save_scan_csv(path, items, db)`. Eine `row` ist `{"t":int,"id":int,"data":bytes,"tx":bool}`.
- `elster_gui.py` (Hauptfenster, spätere Welle): `QMainWindow` mit den vier Tabs, Verbindungsdialog (Passwort wird abgefragt, nicht gespeichert), Menü Datei→Export/Offline-Log laden.

## 6. Tests
- Firmware-Header: Auf diesem PC gibt es KEINEN Host-C++-Compiler (kein g++/clang++/cl). Es werden keine C++-Tests ausgeführt; die Header müssen sorgfältig von Hand geprüft werden. Die Gesamtprüfung macht später `esphome compile`. Wer einen Header schreibt, legt zusätzlich `tests/test_<name>.cpp` (`main()` mit `assert`) als Quelltext an, führt sie aber nicht aus.
- Python: `tests/test_elster.py`, `tests/test_export_io.py` mit `pytest` oder `unittest`.
- Beispiele für Frame-Tests (aus `frame_format.examples`): Lesen `PROGRAMMSCHALTER 0x0112` von KESSEL → CAN `0x680`, Daten `31 00 fa 01 12 00 00`; Lesen `0x0003` von SPEICHER → `41 01 03 00 00 00 00`; Antwort KESSEL→ESP `d2 00 fa 01 12 02 00`.

## 7. Erweiterung Automatik (Plan: `../plan/mk1-debug-auto-plan.md`)

### 7.1 Generische Adressierung (Firmware und `elster.py`)
- Read-ID aus jeder CAN-ID 0x000–0x7FF: `d0 = ((can_id >> 3) & 0xF0) | 0x01`, `d1 = can_id & 0x7F`. Eigene ID `0x680` ist als Ziel verboten.
- Format: `0` = auto, `1` = short, `2` = long. Auto: Kurzformat nur wenn `index <= 0xFF` UND `index != 0xFA`, sonst Langformat. Short: nur für `index <= 0xFF`, und `0xFA` wird trotzdem lang gesendet (Kollision mit der Langformat-Kennung). Long: immer `[d0, d1, 0xFA, hi, lo, 0, 0]`, auch für Indizes ≤ 0xFF.
- Firmware `elster_frame.h`: neue Funktion `static inline size_t build_read_request_id(uint16_t can_id, uint16_t index, uint8_t fmt, uint8_t out[7]);`. Die alte `build_read_request(const Member&, ...)` bleibt und ruft intern `build_read_request_id(m.can_id, index, 0, out)` auf. Die Antwort-Erkennung bleibt `is_response_for` (Format der Antwort egal, nur dekodierter Index zählt).
- `elster.py`: `build_read_request(member_can_id, index, db=None, fmt="auto") -> (0x680, bytes)`, `fmt` in `"auto"|"short"|"long"`, Berechnung nach obiger Formel (nicht mehr über die Member-Tabelle); `ValueError` bei Ziel 0x680 oder ID > 0x7FF.

### 7.2 MQTT-Erweiterungen (alles Präfix `stiebel-debug/`)
| Topic | Richtung | Payload |
|---|---|---|
| `cmd/read` | rein | wie bisher, zusätzlich optional `"format":"auto"|"short"|"long"`; `member` = beliebige CAN-ID |
| `cmd/scan` | rein | wie bisher, zusätzlich optional `"format"` (Default `auto`); `member` = beliebige CAN-ID ≠ 0x680 |
| `cmd/probe` | rein | `{"index":"0x000b","from_id":"0x000","to_id":"0x7ff","delay_ms":100}` – fragt einen Index bei jeder CAN-ID im Bereich ab (0x680 wird übersprungen), Timeout = delay_ms |
| `scan/progress` | raus | `{"member":"0x180","format":"auto","index":"0x4e40","done":64,"total":512,"state":"running"}` – `state` ∈ `running|done|stopped`; bei `done`/`stopped` immer gesendet |
| `scan/result` | raus | `{"member":"0x180","format":"auto","items":[{"index":"0x4e40","raw":"0x0123","status":"ok"}]}` |
| `probe/progress` | raus | `{"index":"0x000b","id":"0x1a0","done":416,"total":2047,"state":"running"}` |
| `probe/result` | raus | `{"index":"0x000b","items":[{"id":"0x180","raw":"0x8000","status":"unsupported"}]}` – nur IDs mit Antwort, Chunks ≤ 32 |
| `cmd/stop` | rein | bricht Scan UND Probe ab |

Scan-Ergebnis-Items: nur `ok` und `unsupported` (Timeouts werden nur gezählt). Neue Felder sind additiv; vorhandene GUI-Tabs ignorieren unbekannte Felder.

### 7.3 `gui/auto_runner.py` (headless, kein Qt, paho-mqtt 1.6.x)
CLI: `python -I auto_runner.py --out <dir> [--secrets ../secrets.yaml] [--delay 100] [--passive 60] [--resume <state.json>] [--smoke]`
- Liest `mqtt_server`, `mqtt_port`, `mqtt_username`, `mqtt_password` aus `secrets.yaml` (einfacher Zeilen-Parser `key: "value"`/`key: value`); gibt Zugangsdaten nie aus.
- Dateien in `--out`, Präfix `auto_<YYYYmmdd_HHMMSS>`: `_rx.jsonl` (jeder rx-Frame, Format wie Mitschnitt), `_status.jsonl` (jede status-Meldung mit PC-Zeit), `_state.json` (Fortschritt + alle Ergebnisse, nach jedem Chunk atomar geschrieben: tmp + replace), `_runner.log`.
- Ablauf: warte auf `availability`=`online` (max 5 min) → `cmd/sniff` on, alle → Schritt 1: passiv `--passive` s Absender sammeln, dann `probe` 0x000–0x7ff mit Index `0x000b` → Member = Probe-Antworten (sortiert); passive Absender ohne Probe-Antwort als `silent` notieren → Schritt 2 stufenweise über ALLE Member: A1 `0x0000–0x00ff` short, A2 `0x0000–0x00ff` long, B `0x0100–0x0fff`, C `0x4e00–0x4fff`, `0xc000–0xc3ff`, `0xfff0–0xffff`, D Rest (`0x1000–0x4dff`, `0x5000–0xbfff`, `0xc400–0xffef`); jeder Bereich in Teil-Scans à max. 0x1000 Indizes (Fortsetzungs-Granularität) → Schritt 3: alle Treffer (status ok) 3 Durchgänge per `cmd/read`, Abstand zwischen Durchgängen ≥ 60 s.
- Ein Teil-Scan ist fertig bei `scan/progress.state` = `done`. Bleibt Fortschritt 60 s aus, oder geht `availability` auf `offline`: warten bis `online`, Teil-Scan komplett wiederholen.
- Sicherheit: `status.bus_off` true oder `twai_err` steigt um > 100 zwischen zwei Status-Meldungen → `cmd/stop`, Zustand sichern, Exit-Code 3.
- `--smoke`: nur Probe `0x170–0x210`, Member-Scan `0x0000–0x001f` auto und `0x00f8–0x0102` auto, 1 Read-Durchgang; zum schnellen Test.
- Exit-Codes: 0 ok, 2 ESP nicht online, 3 Bus-Problem, 4 sonstiger Fehler. Ctrl+C → `cmd/stop`, Zustand sichern.

### 7.4 `gui/type_guess.py` (kein Qt)
- `load_mkgit_table(path) -> dict[int, (name, type)]` aus mk-git `ElsterTable.h` (`C:\temp\stiebel-Remote\ha-stiebel-control\esphome\ha-stiebel-control\elster\ElsterTable.h`, Zeilenformat `{ "NAME", 0x1234, et_xxx|0, ...}`).
- `guess_types(raws: list[int]) -> list[tuple[str, float, str]]` – Typ-Kandidaten mit Score 0..1 und dekodiertem Beispielwert, nach Regeln aus Plan Schritt 3.
- `analyse(state_json_path, out_prefix)` → `<prefix>_analyse.csv` (Semikolon, UTF-8-BOM: member;member_name;index;name_ref;type_ref;name_mkgit;type_mkgit;raws;konstant;kandidaten;konflikt) und `<prefix>_analyse.md` (Zusammenfassung: Member, Trefferzahlen je Stufe, Vergleich short/long für 0x00–0xFF, Typkonflikte, unbekannte Indizes mit Kandidaten).
- CLI: `python -I type_guess.py <state.json>`.

### 7.5 Format `_state.json` (von auto_runner geschrieben, von type_guess gelesen)
```json
{
  "version": 1,
  "started": "2026-10-20T20:00:00",
  "finished": null,
  "delay_ms": 100,
  "passive_ids": ["0x100", "0x180"],
  "probe": {"index": "0x000b", "items": [{"id": "0x180", "raw": "0x8000", "status": "unsupported"}]},
  "members": ["0x180", "0x201"],
  "silent": ["0x100"],
  "tasks": [
    {"stage": "A1", "member": "0x180", "format": "short", "from": "0x0000", "to": "0x00ff", "done": true,
     "items": [{"index": "0x000c", "raw": "0x0058", "status": "ok"}]}
  ],
  "rereads": [{"member": "0x180", "index": "0x000c", "pass": 1, "t": "2026-10-20T23:00:00", "raw": "0x0059", "status": "ok"}]
}
```
`tasks` enthält alle geplanten Teil-Scans in Ausführungsreihenfolge (`done` false bis fertig). Beim Fortsetzen werden Tasks mit `done: true` übersprungen.

## 8. Switch-Bytes und Produktiv-Streaming (Plan: `C:\Users\bonze\.claude\plans\1-kannst-du-bitte-unified-zephyr.md`)

### 8.1 Frame-Regeln (gemessen am 20./21.10.2026)
- `data[2]` ist ein Switch-Byte. Langlayout (`d3/d4` = Index, `d5/d6` = Wert) nur bei `0xF7`, `0xF8` (Minimum), `0xF9` (Maximum), `0xFA` (aktueller Wert, Standard). Alle anderen `data[2]` sind Kurzlayout (`d2` = Index, `d3/d4` = Wert), auch `0xF0`–`0xF6` und `0xFB`–`0xFF`.
- `0xFE` mit Nachrichtenart 6/7 ist eine Systemnachricht (kein Index). Systemnachrichten werden wie Kurzlayout gelesen (`index = d2`, `value = d3/d4`) und mit `system = true` markiert.
- Request-Format: Der Request bestimmt das Layout der Antwort. Langformat ist Pflicht für `switch != 0xFA` und für die Indizes `0xF7`, `0xF8`, `0xF9`, `0xFA` (sonst wird `d2` als Switch gelesen). Sonst gilt: `auto` = kurz für `index <= 0xFF`, `short` = nur für `index <= 0xFF` (Index `0xF7`–`0xFA` wird trotzdem lang gesendet), `long` = immer lang.
- Switch-Werte im Request: `0xFA` (Standard), `0xF8` (Min), `0xF9` (Max). Andere Werte: Fehler.

### 8.2 `gui/elster.py`
- `SWITCH_LONG = (0xF7, 0xF8, 0xF9, 0xFA)`.
- `parse_frame(can_id, data)` liefert zusätzlich `"switch"`: `int` (0xF7–0xFA) bei Langlayout, sonst `None`; und `"system"`: `bool` (msg_type 6 oder 7). Bei `system` immer Kurzlayout. Alle bisherigen Felder bleiben.
- `build_read_request(member_can_id, index, db=None, fmt="auto", switch=0xFA) -> (0x680, bytes)`: Regeln aus 8.1; `ValueError` bei ungültigem `switch`, bei Ziel 0x680, ID > 0x7FF, Index > 0xFFFF, `fmt="short"` mit Index > 0xFF.
- `switch_label(switch: int | None) -> str`: `None` oder `0xFA` → `""`, `0xF8` → `"min"`, `0xF9` → `"max"`, `0xF7` → `"f7"`.
- Zeilen/Rows aus Mitschnitten (`{"t","id","data","tx"}`) bleiben unverändert; der Switch wird aus `data` abgeleitet.

### 8.3 MQTT-Erweiterung (additiv)
- `cmd/read` und `cmd/scan`: optional `"switch":"fa"|"f8"|"f9"` (auch `"0xfa"` usw., Groß-/Kleinschreibung egal), Standard `fa`; ungültig → Kommando wird abgelehnt.
  Seit 2026-11-15 zum Testen auch jedes andere Byte `f0`–`ff` (Langformat `[d0,d1,sw,ih,il,0,0]`). Der Antwort-Abgleich
  kennt nur F7–FA; bei den übrigen Bytes kommt `timeout`, die Antwort steht im Sniffer-Stream. Messung WPL-A 07:
  F7 mit echtem Index liefert `0x8000`; F0–F4, FB–FD, FF antworten im Kurzlayout ohne Index-Echo, F5/F6 gar nicht
  (`daten/can-logs/f7test_20261115.jsonl`, `f0test_20261115.jsonl` im Projektordner).
- `read/result`: zusätzlich `"switch":"0xfa"`. `scan/progress` und `scan/result` (Top-Level, neben `format`): zusätzlich `"switch":"0xfa"`. Fehlt das Feld (ältere Firmware), gilt `0xFA`.
- `gui/mqtt_client.py`: `send_read(member, index, fmt=None, switch=None)`, `send_scan(member, from_idx, to_idx, delay_ms, fmt=None, switch=None)`: `fmt` in `None|"auto"|"short"|"long"` (Payload-Feld `format` nur wenn nicht `None`/`"auto"`), `switch` in `None|0xFA|0xF8|0xF9` (Payload-Feld nur wenn nicht `None`/`0xFA`, als `"0xf8"`). `read_result`/`scan_progress`/`scan_result` Signal-Dicts enthalten immer `"switch"` als `int` (Standard `0xFA`), `scan_result["items"]` ebenfalls je Item.
- Availability: `mqtt_client.py` abonniert zusätzlich `heatingpump/status` (Payload `online`/`offline`, Produktiv-Firmware). Neues Signal `availability_source = Signal(str)` mit `"debug"` (Topic `stiebel-debug/availability`) oder `"produktiv"` (Topic `heatingpump/status`), emittiert unmittelbar vor `availability`. Offline gewinnt, wenn eines der beiden Topics `offline` meldet (zuletzt gemeldeter Zustand je Quelle merken; effektiv `online` nur wenn keine Quelle `offline` meldet und mindestens eine `online`).

### 8.4 Debug-Firmware (C++)
- `elster_frame.h`: `build_read_request_id(uint16_t can_id, uint16_t index, uint8_t fmt, uint8_t sw, uint8_t out[7])` (sw ∈ {0xFA, 0xF8, 0xF9}, Regeln 8.1); `build_read_request(const Member&, index, out)` bleibt (ruft mit fmt 0, sw 0xFA). `struct Parsed` bekommt `uint8_t sw` (0 = Kurzlayout). `parse_frame`: Langlayout bei `data[2]` ∈ {F7,F8,F9,FA}, dann `sw = data[2]`. `is_response_for(const Parsed&, uint16_t sender, uint16_t member, uint16_t index, uint8_t sw)`: `msg_type == 2 && sender == member && p.index == index &&` (`sw == 0xFA ? (p.sw == 0 || p.sw == 0xFA) : p.sw == sw`).
- `debug_json.h`: `ReadCmd` und `ScanCmd` bekommen `uint8_t sw` (Standard 0xFA, `dj_get_switch`); `format_read_result(member, index, sw, raw, status, out, cap)`, `format_scan_progress` und der scan/result-Chunk in `can_debug.h` enthalten `"switch":"0x%02x"`.
- `can_debug.h`: `req_switch` wird beim Senden gesetzt, der Antwort-Abgleich ruft `is_response_for(..., req_switch)`. Probe bleibt `fmt 0`, `sw 0xFA`.

### 8.5 Produktiv-Firmware (`esp32-s3-technik-mk1/ha-stiebel-control/`)
- Neuer Header `can_rx_stream.h` (+ Kopien `ring_buffer.h`, `debug_json.h` aus `mk1-debug/firmware/`), `namespace mkdbg`, Funktionen: `void dbgrx_on_frame(uint32_t can_id, const std::vector<uint8_t>&)`, `void dbgrx_note_tx(uint32_t can_id, const std::vector<uint8_t>&)`, `void dbgrx_on_command(const std::string& suffix, const std::string& payload)` (nur `"sniff"`), `void dbgrx_flush()`, `void dbgrx_publish_status()`.
- Topics: `stiebel-debug/status`, `heatingpump/status` (Availability, `online`/`offline`). Der Sniffer-Stream läuft über `heatingpump/can_debug/*` (siehe 8.7), nicht mehr über `stiebel-debug/rx`/`cmd/sniff`. Keine Kommandos read/scan/probe/stop. Dieser Stand ist älter; aktuell siehe Abschnitt 13.

### 8.7 Produktiv-Sniffer `heatingpump/can_debug/*` (GUI-Seite, 2026-11-23)
Die Produktiv-Firmware hat `stiebel-debug/rx` und `stiebel-debug/cmd/sniff` nicht mehr. Stattdessen (Topic-Wurzel `PROD_ROOT = "heatingpump"` in `mqtt_client.py`, alle Produktiv-Topics daraus abgeleitet):

| Topic | Richtung | Payload |
|---|---|---|
| `heatingpump/can_debug/set` | GUI → ESP | `on` / `off` (Rohtext, kein JSON; kein Filter in der Firmware) |
| `heatingpump/can_debug/state` | ESP → GUI | `on` / `off`, retained |
| `heatingpump/can_debug/frames` | ESP → GUI | JSON-Array `[{"t":<ms seit Boot>,"id":"0x180","d":"<hex>","tx":false},...]`, ohne Feld `u` |

- `mqtt_client.py` abonniert `frames` und `state` beim Connect. Frames werden wie `stiebel-debug/rx` verarbeitet (`_handle_frames`). Fehlt `u`, gilt `ts_src = "pc"` (ClockSync aus `t`, Abschnitt 11.2).
- Signal `can_debug_state = Signal(bool)`: nur `"on"`/`"off"` werden emittiert, andere Werte ignoriert. `tab_live.py` setzt die Checkbox "Sniffer an" danach ohne Rücksendung.
- `send_sniff(on, ids, types)`: fw `prod` → `heatingpump/can_debug/set`; `ids`/`types` entfallen. Fw `debug` oder unbekannt (vor dem ersten `status`) → `stiebel-debug/cmd/sniff` wie bisher.
- Filter im Produktiv-Modus clientseitig: `tab_live.py` verwirft Frames, die nicht zu CAN-ID-Liste und Art passen, bevor sie in den Puffer gehen (`_frame_matches`). Begründung: die Firmware sendet ungefiltert, der Anzeigepuffer ist begrenzt. Frames vor einer Filteränderung fehlen dadurch nachträglich.
- `stiebel-debug/status` (Produktiv) enthält nur `fw`, `rx`, `heap`, `ntp`, bei TWAI zusätzlich `twai_err`, `bus_off`, `rx_missed`, `rx_overrun`, `rx_queue`, `tec`, `rec`. Fehlende Felder (`tx`, `rx_fill`, `rx_cap`, `overflow`, ...) zeigt `tab_status.py` als `-`. Zahlenfelder mit null, Text, Liste oder inf gelten ebenfalls als fehlend; `bus_off` wird nur als Bool gewertet (null = `-`).
- Debug-Firmware (`stiebel-debug/rx`, `cmd/read`, `cmd/scan`, ...) bleibt unverändert unterstützt.

### 8.6 Persistenz (Auto-Runner / Auswertung)
- Tasks in `_state.json` bekommen optional `"switch":"0xfa"` (fehlt = `0xfa`). Rereads: optional `"switch"`. Neue optionale Liste `state["minmax"]`: `[{"member":"0x180","index":"0x0003","switch":"0xf8","raw":"0x0100","status":"ok"}]`.
- `auto_runner.py`: neuer CLI-Schalter `--minmax` (Standard aus): nach den Rereads für jeden `ok`-Treffer je ein `cmd/read` mit `switch f8` und `f9`, Ergebnisse in `state["minmax"]`, fortsetzbar.
- `export_io.py`: `FRAME_CSV_HEADER` = `t;richtung;can_id;data_hex;absender;empfaenger;msg_type;index;switch;name`; `SCAN_CSV_HEADER` = `index;switch;name;raw;status;wert`. Switch-Spalte: `""` bei Kurzlayout oder 0xFA, sonst `min`/`max`/`f7`.

## 9. Bitfelder (Referenz-Erweiterung)

### 9.1 Referenz-JSON
- Neues optionales Signal-Feld `bits` (additiv, Typ bleibt `et_default`):
```json
"bits": [{"bit": 0, "name": "VERDICHTER", "confirmed": true, "comment": "optional"}]
```
- `bit`: 0..15. `name`: eindeutig je Signal. Bits eindeutig je Signal.
- `comment`: optional.

Beispiel `BETRIEBS_STATUS` (Index `0x0176`, Owner `0x180`):
- Bit 0 `VERDICHTER`, Bit 6 `SPEICHERLADEPUMPE`, Bit 12 `ZUSATZHEIZUNG`, Bit 14 `WARMWASSER` (am Display geprüft: Warmwasser 0x4041, Heizen 0x0041, Notlauf 0x1040).
- Wert Big-Endian aus `data[5]`/`data[6]`.
- Gemessen: `0x0000`, `0x0041`.

Beispiel `BETRIEBS_STATUS_WPLA` (Index `0x4eef`, Owner `0x480`, Typ `et_default`, Quelle kr0ner/OneESP32ToRuleThemAll `yaml/wpl_a_base.yaml`, 12 Bit):
- Bit 0 `HEIZKREISPUMPE_1`, 1 `HEIZKREISPUMPE_2`, 2 `AUFHEIZPROGRAMM`, 3 `NHZ_STUFEN_IN_BETRIEB`, 4 `WP_IM_HEIZBETRIEB`, 5 `WP_IM_WW_BETRIEB`, 6 `VERDICHTER_IN_BETRIEB`, 7 `SOMMERBETRIEB_AKTIV`, 8 `KUEHLBETRIEB_AKTIV`, 9 `ABTAUBETRIEB`, 10 `SILENT_MODE_1`, 11 `SILENT_MODE_2`.
- Bestätigt (am Display gemessen): Bits 0, 4, 6. Gemessen Heizen `0x0051` (2026-10-20). Übrige Bits: nur Quelle kr0ner, noch nicht am Display geprüft.
- `0x8000` ergibt `-255` (Bit 15 nicht definiert).

### 9.2 Eingebaute Vorgabe und API
- Eingebaute Vorgabe: `elster.BUILTIN_BITS`.
- `elster.set_bit_names`, `elster.decode_bits`.
- `ElsterDb.bits_for`, `ElsterDb.decode`.
- Bitfeld-Text: `0x0041: VERDICHTER, SPEICHERLADEPUMPE`. `0x8000` ergibt `-255`.
- `ReferenceModel.bits`, `ReferenceModel.set_bits`.

### 9.3 Anzeige
- Nur für aktuelle Werte (Switch `FA`), nicht für Min/Max.

## 10. Schreiben (cmd/write)

Nur Debug-Firmware (`cmd/write`). Die Produktiv-Firmware schreibt nur über `heatingpump/raw/write`, und nur mit Build-Schalter `can_raw_write` (Abschnitt 13.4).

### 10.1 Payload `cmd/write` (GUI→ESP)
`{"member":"0x201","index":"0x0013","value":"0x0208","format":"auto"}`
- `member`: CAN-ID `"0x%03x"`. `index`: `"0x%04x"`. `value`: Rohwert 0..0xFFFF (Hex oder Dezimal).
- `format` optional: `auto` (Standard, Feld fehlt) | `short` | `long`. Wie `cmd/read`.
- Text→Rohwert macht die GUI (`elster.encode_value`); die Firmware bekommt nur Rohwerte.
- Member: beliebige CAN-ID 0x000–0x7FF außer 0x680, nicht nur die Member aus `can_members`. Gilt für Abfrage (read/scan/probe) und Schreiben gleich. Unbekannte Member heißen `UNBEKANNT_0x…`.

### 10.2 Frameaufbau Schreiben
Art 0 (`d0 & 0x0F == 0`), Absender 0x680, DLC 7. `d0 = ((id >> 3) & 0xF0) | 0x00`, `d1 = id & 0x7F`.
- Kurz (Index ≤ 0xFF, nicht 0xF7–0xFA, Format auto/short): `[d0, d1, idx, vh, vl, 0, 0]`
- Lang (Format long oder Index > 0xFF oder 0xF7–0xFA): `[d0, d1, 0xFA, ih, il, vh, vl]`
- Index 0xF7–0xFA ist immer lang (Kollision mit Kennung). Format short bei Index > 0xFF: Fehler.

### 10.3 Sicherheitsregeln
- Ziel 0x000–0x7FF, nicht 0x680. Sonst abgelehnt, keine Antwort.
- Index 0xF7–0xFA mit Wert 0x8000: `status "rejected"`, nichts gesendet.
- Nur im Zustand MODE_IDLE. Sonst ignoriert, keine Antwort.
- Ungültiges Payload: ignoriert, keine Antwort (nur Log).

### 10.4 Ablauf
1. write-Frame senden. Er wird im Sniffer als `tx: true` gespiegelt. Read/Scan/Probe/Write werden bis zum Ende abgelehnt.
2. Ack-Fenster 1 s: erster Frame vom Member mit msg_type 3, 4 oder 5 → `ack` = msg_type. msg_type 2 mit gleichem Index → `ack` = 2.
3. Rücklesen: read-Request mit Switch `0xFA`, Format wie Kommando, Timeout 1 s. Wert `0x8000` zählt als kein Rücklesewert.
4. Ergebnis: Rücklesewert = value → `verified`; ≠ value → `mismatch`; kein Rücklesewert, aber Ack → `timeout`; weder Ack noch Rücklesewert → `no_ack`. Stop oder Bus-Off während des Ablaufs → `timeout`.
- Hinweis: Ack-Art am eigenen Bus noch nicht gemessen.

### 10.5 Topic `write/result` (ESP→GUI)
`{"member":"0x201","index":"0x0013","value":"0x0208","status":"verified","ack":4,"readback":"0x0208"}`
- `member`, `index`, `value`: Echo des Kommandos (`"0x%03x"`, `"0x%04x"`, `"0x%04x"`).
- `status`: `verified` | `mismatch` | `no_ack` | `timeout` | `rejected`.
- `ack`: Integer (msg_type), fehlt ohne Ack. `readback`: `"0x%04x"`, fehlt ohne Rücklesewert.
- `rejected`: nur member/index/value/status.

### 10.6 GUI-Schutz (Soll)
- Schreiben nur nach Freischalten (Schalter, Standard aus).
- Bestätigungsdialog vor jedem Senden: Member, Index, Name, Rohwert und Text.
- Typen mit `types[].writable` false (`elster.is_writable`) und unbekannte Typen werden nicht angeboten. Hinweis: Rohwert `0x…` ist bei jedem Typ erlaubt (`encode_value`), daher im Dialog immer den Rohwert zeigen.

## 11. Zeitstempel

### 11.1 Firmware
- `t`: ms seit Boot (uint32, `esphome::millis`). Pflichtfeld in `rx`-Frames.
- `u`: Unix-ms (uint64) vom ESP, nur wenn SNTP synchron (`tv_sec >= 1700000000`). Fehlt sonst.
- `status.ntp`: `true` | `false`, zeigt den SNTP-Status.

### 11.2 GUI (`mqtt_client.py`)
- `ts`: Unix-Sekunden (float), auf 3 Nachkommastellen gerundet. `ts_src`: `"esp"` (aus `u`) oder `"pc"` (Schätzung).
- Fallback `pc` (`ClockSync`): pro Batch gilt Ankunft (PC) − t/1000 = Offset + Latenz. Der kleinste Offset seit dem letzten Reset wird benutzt. Reset, wenn ein Batch ein kleineres `t` als bisher enthält (ESP-Neustart). Nur Frames ohne `u` speisen die Schätzung.
- Genauigkeit: `pc` ca. 10–50 ms, SNTP (`esp`) ca. 1–5 ms.

### 11.3 Dateien
- CSV (`export_io.py`): Spalten `t;zeit_unix;zeit_lokal;…`. `zeit_unix` = `ts` mit 3 Nachkommastellen. `zeit_lokal` = PC-Lokalzeit `JJJJ-MM-TT hh:mm:ss.mmm`. Ohne `ts` bleiben beide leer.
- JSONL: Feld `ts` direkt nach `t` (nur wenn gültig). `ts_src` am Ende.
- Alte Logs ohne `ts` bleiben lesbar. Die Zeilen sind gültig, es fehlt nur der Zeitstempel.
- Fotos/EXIF: Kamera-Zeit ist meist lokale Zeit ohne Zeitzone. Gegen PC-Zeit prüfen, Zeitzone und Sommerzeit beachten.

## 12. Absender-ID (cmd/read "src", 2026-11-02)

- `cmd/read` optional `"src":"0x700"`: Absender-CAN-ID des Requests (0x000–0x7FF, Standard `0x680`). `src` gleich `member`: abgelehnt.
- Mit `src` ≠ 0x680 zählt nur eine Antwort an Empfänger `src`. Frames mit CAN-ID `src` werden beim Abgleich ignoriert.
- `read/result` enthält dann zusätzlich `"src":"0x700"` (am Ende). Ohne `src` bleibt die Ausgabe unverändert.
- Der Sniffer spiegelt den Request mit `id` = `src` und `tx: true`.
- Scan, Probe und Write senden weiter von 0x680.
- `cmd/read` optional `"timeout_ms"`: 100–10000 (Standard 1000, unter 100 wird 100). FES (0x100) antwortet erst nach 0,5–1,5 s, mit Rückstau bis ~4 s (gemessen 2026-11-02).
- Vorsicht: Eine ID, die selbst auf dem Bus sendet, als `src` zu nutzen, kann Kollisionen (Error-Frames) auslösen. Antworten gehen dann an das echte Gerät.
- Testwerkzeug: `tools/fes_src_test.py` (FES 0x100 mit wechselnden Absendern; aktive IDs nur mit `--spoof`).

## 13. Produktiv-Modus (Topic-Wurzel `heatingpump/`)

Gilt für die Produktiv-Firmware (`esp32-s3-technik-mk1`). Die Topic-Wurzel ist `PROD_ROOT = "heatingpump"` in `gui/prod_api.py` (dort auch Payload-Bau und Antwort-Parser) und in `gui/mqtt_client.py` übernommen.

Quelle der Firmware-Seite: PR #141 im Fork `bastard-GIT/ha-stiebel-control` (Branch `feat/bus-analysis`, Dateien `esphome/ha-stiebel-control/bus_scan.h`, `can_raw.h`, `can_debug.h`, `common.yaml`; Doku `docs/CONFIGURATION.md`, Abschnitt "Bus analysis"). Die Upstream-PR ist ein Draft. Stand 2026-11-24. Welche Firmware auf dem Mk1 läuft und ob sie diesen Stand enthält, steht im Tag, nicht in diesem Abschnitt.

### 13.0 Bau-Optionen

| Funktion | Build-Schalter (`common.yaml`) | Topics |
|---|---|---|
| Bus-Scan | Standard, immer eingebaut | `scan/start`, `scan/stop`, `scan/status`, `scan/<name>/0x<idx>` |
| Teilnehmer-Scan | `can_debug: "true"` | `scan/members`, `scan/members/0x<id>` |
| Einzel-Read | `can_debug: "true"` | `raw/read`, `raw/result` |
| Schreiben | `can_debug: "true"` und `can_raw_write: "true"` | `raw/write`, `raw/result` |
| CAN-Frame-Stream | `can_debug: "true"` | `can_debug/set`, `can_debug/state`, `can_debug/frames` |

Ohne Schalter loggt die Firmware nur, dass die Funktion nicht eingebaut ist. Mk1 (Produktiv-YAML): `can_debug: "true"`, `can_raw_write: "false"`.

### 13.1 Bus-Scan (`scan/*`)

Fragt einen Member (oder eine CAN-ID) nach Indizes ab, entweder über die ganze Signaltabelle oder über einen Hex-Bereich. Veröffentlicht werden nur Indizes, die antworten.

| Topic | Richtung | Payload |
|---|---|---|
| `heatingpump/scan/start` | GUI/HA → ESP | `MEMBER\|0xID [0xLO 0xHI] [fa\|f8\|f9]` |
| `heatingpump/scan/stop` | GUI/HA → ESP | beliebig, bricht sofort ab |
| `heatingpump/scan/status` | ESP → | `{"state":"running\|done\|stopped","mode":"table\|range\|members","member":"…","done":…,"total":…,"hits":…,"seconds":…}`, retained |
| `heatingpump/scan/<name>/0x<idx>` | ESP → | `{"name":"…","raw":…,"value":"…"}`, retained |
| `heatingpump/scan/<name>/0x<idx>/f8`, `/f9` | ESP → | wie oben, für die Schalterbytes Minimum und Maximum |

Regeln:
- `MEMBER`: Name aus der Mitgliederliste (Groß-/Kleinschreibung egal, PC ausgenommen) oder Hex-CAN-ID `0x000`–`0x7FF` (nicht `0x680`). Eine Hex-ID aus der Tabelle scannt den Member unter seinem Namen. Jede andere Hex-ID scannt das Ziel und veröffentlicht unter `0x%03x` (klein).
- Ohne Bereich läuft die ganze Signaltabelle (ca. 3 600 Indizes, ca. 9 min bei 150 ms), auch bei einer Hex-ID.
- Bereich: genau zwei Hex-Zahlen `LO HI`, jede höchstens `0xFFFF`, `HI >= LO`. Er ersetzt die Tabelle.
- Schalterbyte: `fa` (Standard, Wert), `f8` (Minimum), `f9` (Maximum). Höchstens eines, an beliebiger Stelle.
- Ungültige Payload: nur Log, nichts ändert sich. Ein gültiger Start ersetzt einen laufenden Scan.
- Takt fest 150 ms je Anfrage. Das normale Polling pausiert während des Scans.
- Ende: 1 s Nachlauf nach dem letzten Index, dann `done`. `stop` führt sofort zu `stopped`.
- `scan/status` kommt beim Start, danach alle 100 Anfragen und am Ende. `done` = Anfragen bisher, `total` = Anzahl, `hits` = Treffer, `seconds` = Dauer. `member` ist im Modus `members` leer.
- Ergebnisse gehen einzeln und sofort raus, nicht gesammelt. Nur Antworten mit Rohwert ≠ `0x8000` werden veröffentlicht. Ausbleibende Antworten (Timeout) werden nicht gemeldet.
- `raw` ist eine Dezimalzahl, kein Hex-String. `name` kommt aus der Tabelle und ist leer für Indizes ohne Tabelleneintrag. `value` ist der dekodierte Wert als Text.

```
mosquitto_pub -h <broker> -u <user> -P <passwort> -t heatingpump/scan/start -m "MANAGER 0xfd00 0xfdff"
mosquitto_pub -h <broker> -u <user> -P <passwort> -t heatingpump/scan/start -m "0x180 0x0000 0x00ff f8"
mosquitto_pub -h <broker> -u <user> -P <passwort> -t heatingpump/scan/stop -m stop
```

### 13.2 Teilnehmer-Scan (`scan/members`)

Fragt jede CAN-ID eines Bereichs nach einem Index (nur Schalterbyte `fa`) und meldet die Antworten.

| Topic | Richtung | Payload |
|---|---|---|
| `heatingpump/scan/members` | GUI/HA → ESP | `[0xLO 0xHI] [0xIDX]`, Standard `0x000 0x7ff 0x000b` |
| `heatingpump/scan/members/0x<id>` | ESP → | `{"index":"0x<idx>","raw":<dezimal>}`, retained |

- Ohne Angabe: alle IDs `0x000`–`0x7FF`, Index `0x000b`. Ein Wert: Index. Zwei Werte: Bereich. Drei Werte: Bereich und Index.
- Die eigene ID (`0x680`) wird übersprungen. Der volle Bereich dauert ca. 310 s (2 048 × 150 ms).
- Fortschritt und Ende stehen auf `scan/status` mit `mode` `members`.
- Nur mit `can_debug: "true"`.

```
mosquitto_pub -h <broker> -u <user> -P <passwort> -t heatingpump/scan/members -m "0x100 0x1ff 0x0013"
```

### 13.3 Einzel-Read (`raw/read`)

Liest einen Index bei einer CAN-ID mit Schalterbyte `fa` (Standard), `f8` oder `f9`.

| Topic | Richtung | Payload |
|---|---|---|
| `heatingpump/raw/read` | GUI/HA → ESP | `0xID 0xIDX [fa\|f8\|f9]` |
| `heatingpump/raw/result` | ESP → | ein JSON-Objekt je Operation, nicht retained |

Beispiel: `{"op":"read","id":"0x180","index":"0x0013","switch":"fa","raw":"0x0208","status":"ok"}`

| `status` | Bedeutung | Feld `raw` |
|---|---|---|
| `ok` | Antwort mit Wert | ja |
| `unsupported` | Antwort mit Rohwert `0x8000` | ja (`"0x8000"`) |
| `timeout` | keine Antwort innerhalb 1 s | nein |
| `busy` | Bus-Scan, andere Raw-Operation oder passiver Modus läuft | nein |

- Eine Operation gleichzeitig. Die Antwort auf `raw/read` ist nicht retained.
- Ungültige Payload oder Read der eigenen ID (`0x680`): nur Log, keine Antwort.
- Nur mit `can_debug: "true"`.

### 13.4 Schreiben (`raw/write`)

Schreibt einen Index bei einer CAN-ID und prüft das Ergebnis per Rücklesen. Nur mit `can_debug: "true"` und `can_raw_write: "true"`.

| Topic | Richtung | Payload |
|---|---|---|
| `heatingpump/raw/write` | GUI/HA → ESP | `0xID 0xIDX 0xVALUE` |
| `heatingpump/raw/result` | ESP → | ein JSON-Objekt je Operation, nicht retained |

> **Sicherheit:** Mit `can_raw_write: "true"` kann jeder MQTT-Client mit Broker-Zugang beliebige Werte an beliebige CAN-IDs schreiben. Es gibt keine Whitelist. Auf Mk1 ist der Schalter aus (`"false"`). Ein Wechsel ist eine Build-Änderung und braucht einen Flash.

Ablauf: Der Schreibrahmen geht an das Ziel. Danach folgt ein Ack-Fenster von 1 s. Ein Ack-Rahmen vom Typ 3, 4 oder 5 bzw. eine Typ-2-Antwort auf denselben Index wird als `ack` (Nachrichtentyp) vermerkt. Dann wird der Index mit Schalterbyte `fa` zurückgelesen (Timeout 1 s).

Beispiel: `{"op":"write","id":"0x180","index":"0x0013","value":"0x0208","status":"verified","ack":4,"readback":"0x0208"}`

| `status` | Bedeutung |
|---|---|
| `verified` | Rücklesewert entspricht dem Schreibwert |
| `mismatch` | Rücklesewert weicht ab |
| `timeout` | Ack empfangen, kein Rücklesewert (Rücklesewert `0x8000` zählt als kein Wert) |
| `no_ack` | weder Ack noch Rücklesewert |
| `rejected` | nichts gesendet: Firmware ohne `can_raw_write`, Schreiben an die eigene ID (`0x680`), `read_only`-Build, Index `0xF7`–`0xFA` mit Wert `0x8000`, oder ein anderer Vorgang läuft (`busy`-Fall) |

Bei `rejected` ist `ack` und `readback` nicht gesetzt.

### 13.5 CAN-Frame-Stream (`can_debug/*`)

- Schalter `can_debug` (Mk1: `"true"`). `can_debug_queue_size` (Standard 256 Frames, 16 Byte je Frame, nur solange der Stream an ist).
- `set` wird nicht gespeichert: nach einem Neustart ist der Stream aus. `on` startet mit leerer Queue, `off` gibt den Speicher frei.
- Frames enthalten RX und vom ESP gesendete Frames (`tx: true`). Kein Feld `u`. Kein Filter in der Firmware.

| Topic | Richtung | Payload |
|---|---|---|
| `heatingpump/can_debug/set` | GUI/HA → ESP | `on` / `off` (Rohtext, kein JSON) |
| `heatingpump/can_debug/state` | ESP → | `on` / `off`, retained |
| `heatingpump/can_debug/frames` | ESP → | `[{"t":123456,"id":"0x180","d":"d200fa01120200","tx":false}]`, nicht retained, höchstens CAN_DEBUG_BATCH Frames je Nachricht |

Andere Werte bei `set` werden nur geloggt.

### 13.6 GUI-Funktionen im Produktiv-Modus

Die GUI wählt die Topic-Wurzel über die Firmware-Art (Feld `fw` in `stiebel-debug/status`, fehlt es, gilt `prod`).

| GUI-Funktion | Topic | Einschränkung im Produktiv-Modus |
|---|---|---|
| Abfrage Read | `raw/read` | Tab Abfrage aktiv (`tab_query.py`, `_apply_lock`). Absender (`src`), Timeout und Format sind deaktiviert (Tooltip); fest gilt Absender `0x680`, Format `auto`, Timeout 1 s. Status `busy` wird angezeigt, ohne Wert. |
| Abfrage Write | `raw/write` | Tab Abfrage aktiv nach Freischalten und Bestätigungsdialog (Zusatzzeile Produktiv). Bridge (`send_write`) nur mit Format `auto`. Status `rejected` (Firmware ohne `can_raw_write` oder Sperrregel) erscheint als rote Warnung. |
| Scanner (Bereichs-Scan) | `scan/start` (mit Bereich) | Tab Scanner aktiv (`tab_scanner.py`): Member per Auswahl oder Hex-ID, Bereich, Schaltbyte. Unbekannte Hex-ID: Topic-Name `0x%03x`. Format und Pause deaktiviert (Tooltip), Takt fest 150 ms (`delay_ms` entfällt). |
| Teilnehmer-Suche | `scan/members` | Tab Scanner, Gruppe "Teilnehmer-Suche (Produktiv)": ID-Bereich, Index (Standard `0x000b`), Suchen/Stopp, Fortschritt, Tabelle ID/Name/Index/Rohwert, CSV-Export. Doppelklick übernimmt die ID ins Feld "oder Hex-ID". Bereichs-Scan und Teilnehmer-Suche sperren sich gegenseitig. Auch im Auto-Runner (`--fw prod`). |
| Auto-Runner `--fw prod` | `scan/start`, `scan/members`, `raw/read`, `raw/result` | Stufe A1 (Format `short`) wird übersprungen und in `state["skipped"]` vermerkt. `delay_ms` wird ignoriert (ebenfalls in `state["skipped"]`). Die Teilnehmer-Suche nutzt `scan/members`. |
| Live-Sniffer | `can_debug/set`, `state`, `frames` | Filter (CAN-ID, Art) nur in der GUI. Die Firmware sendet ungefiltert. Frames vor einer Filteränderung fehlen. |
| Tab Status | `stiebel-debug/status` | Die Produktiv-Firmware sendet nur `fw`, `rx`, `heap`, `ntp` und bei TWAI die Fehlerzähler. Fehlende Felder zeigt die Anzeige als `-`. |

### 13.7 Nicht verfügbar im Produktiv-Modus

- Read mit fremder Absender-ID `src` (`cmd/read` "src"). Standard-Absender ist `0x680`.
- `delay_ms` im Scan. Der Takt ist fest 150 ms.
- Format `short` und `long`. Die Produktiv-Firmware kennt nur das Format `auto`.
- `timeout_ms` beim Read. Die Antwortzeit ist fest 1 s. Für langsame Teilnehmer (FES 0x100 braucht laut GUI-Hinweis 0,5–1,5 s) reicht das nicht.
- Ein eigener Probe-Befehl (`cmd/probe`). Die Teilnehmer-Suche mit beliebigem Index läuft nur über `scan/members` (Schalterbyte `fa`).
- Die Debug-Befehle `stiebel-debug/cmd/read|scan|probe|stop|write`. Ihre Funktionen stehen über `heatingpump/scan/*` und `heatingpump/raw/*` bereit (Abschnitte 13.1–13.4).
- Status-Felder `rx_fill`, `rx_cap`, `overflow`. Die Produktiv-Firmware sendet sie nicht.

### 13.8 Retained-Altlasten

- `scan/status`, `scan/<name>/0x<idx>` und `scan/members/0x<id>` sind retained. Nach Scan-Ende bleiben sie auf dem Broker liegen, auch ein `scan/status` mit `running` nach einem Neustart.
- Die Bridge (`mqtt_client.py`) wertet Scan-Ergebnisse nur aus, wenn sie selbst einen Scan gestartet hat und `scan/status` `running` gemeldet hat. Alles andere wird verworfen und in `stale_ignored` gezählt.
- Der Auto-Runner wertet `scan/status`, Treffer und Member-Antworten nur nach eigenem Start und nach `running` aus. Alte Einträge werden ignoriert.
- Zum Entfernen alter Einträge kann auf das Topic eine retained Nachricht mit leerem Payload gesendet werden (Broker-Standard). Das ist nicht getestet.
