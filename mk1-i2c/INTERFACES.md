# mk1-i2c: verbindliche Schnittstellen

Stand: 2026-10-30 (Sniffer entfernt, Bus-Scan ergaenzt; urspruenglich 2026-10-25). Geschrieben vom Ueberwacher vor Start der Arbeitspakete. Alle WPs richten sich danach. Abweichungen nur nach Rueckmeldung an den Ueberwacher.

Quellen: Plan `C:\Users\bonze\.claude\plans\declarative-weaving-otter.md`, Hardware `C:\temp\stiebel-Remote\mk1_hw\README.md`, Konzept `C:\temp\stiebel-Remote\i2c_sniffer\DOKUMENTATION.md`. Vorlagen (nur lesen): `mk1-debug\`.

## 0. Grundregeln

- Stand 2026-10-30: Es gibt nur noch den Hardware-I2C-Slave (Adresse `i2c_slave_addr: "42"`, General Call, ACK, kein Clock-Stretching, Treiber V2, ISR IRAM-sicher, `firmware/i2c_slave_rx.h`). Der passive Software-Sniffer (GPIO-Interrupts, Software-Decoder, Pin-Schutz, Selbsttest) wurde verworfen: Slave-Test am 2026-10-30 mit 159 Frames in 30 s, CRC 158/158 ok, 0 Flags, `slave_overflow` 0, `slave_errors` 0; Sniffer im selben Test rund 96 % CRC ok (`err_bits`/`err_skew` durch ISR-Latenz). **Harte Regel: Pins (SDA/SCL) nie ausserhalb des I2C-Protokolls treiben.**
- Kein YAML-Block `i2c:`, `light:` oder `canbus:` irgendwo in `mk1-i2c/` (auch nicht in Kommentaren als Beispiel, damit `grep` sauber bleibt). Der Slave nutzt den IDF-Treiber direkt, nicht den ESPHome-`i2c:`-Block.
- GPIO17 (SCL) und GPIO15 bzw. GPIO21 (SDA): Der I2C-Controller treibt sie open-drain, interner Pull-up per Substitution `i2c_internal_pullup` (Standard `true`). Nie `gpio_set_level`, nie GPIO-Ausgang, nie `pinMode(..., OUTPUT)`. Ausnahme ist nur der Bus-Scan (1.3), bei dem der Controller kurz als I2C-Master arbeitet.
- CAN-Pins (nur Test-Build, `firmware/can_idle.h`, on_boot priority 1000): GPIO18 (ISO1042 TXD) `GPIO_MODE_OUTPUT` fest High = rezessiv, zusaetzlich Pull-up, jede Sekunde geprueft (`can_idle_check`). GPIO16 (RXD) nur Eingang ohne Pull. Kein TWAI. Der Glue (`i2c_lwz.h`) fasst GPIO18 nie an.
- Slave-Callback (ISR): `IRAM_ATTR`, kopiert das Paket in einen SPSC-Ring fester Groesse. Kein Logging, kein Malloc, keine Float-Rechnung, keine virtuellen Aufrufe, kein `ESP_LOG*`.
- Code-Stil: C++11, header-only, deutsche Kommentare ohne Umlaute (ae/oe/ue/ss), wie `mk1-debug\firmware\*.h`. Python: Docstrings deutsch ohne Umlaute, wie `mk1-debug\gui\*.py`. paho-mqtt 1.6.x API (`mqtt.Client(client_id="", clean_session=True)`, Callback `(client, userdata, flags, rc)`).
- Keine Zugangsdaten in Code oder Dokumenten. `secrets.yaml` nie ausgeben.
- `mk1-debug\` und `esp32-s3-technik-mk1\` nie aendern.

## 1. Firmware (C++)

Namespace `i2cs`. Dateien in `mk1-i2c\firmware\`.

### 1.1 `i2c_frame.h`, reines C++11, kein ESP-Include

```cpp
namespace i2cs {
static const size_t kMaxFrameBytes = 64;
static const size_t kChangeTableSize = 64;
enum FrameFlags : uint8_t { F_OVERLEN = 0x04 };   // mehr Bytes als in den Frame passen, Rest verworfen

struct Frame {
  uint32_t t_ms;       // Empfangszeit (Firmware-Uhr, millis-Basis)
  uint64_t unix_ms;    // setzt der Glue (0 = Uhr nicht synchron)
  uint64_t ack_bits;   // Bit i = 1: Byte i quittiert (Slave: alle gesetzt)
  uint8_t len;         // 0..64
  uint8_t flags;       // FrameFlags
  uint8_t bytes[kMaxFrameBytes];   // bytes[0] = 0x00 (General Call, rekonstruiert)
};

class ChangeFilter { bool pass(const Frame&); void clear(); };   // Modus "changed"
}
```

`ChangeFilter`: Schluessel = 3 Bytes ab `bytes[1]` (wenn `bytes[0]==0x00`), bei Header `e3 20 10` das 4. Byte dazu. Vergleich ueber ganzen Inhalt (len + bytes). Tabelle 64 Eintraege, bei voll Round-Robin. Frames mit `flags != 0` laufen immer durch. (Frueher Teil des Software-Decoders `i2c_decoder.h`, der entfallen ist.)

### 1.2 `i2c_slave_rx.h`, Hardware-Slave

Treiber IDF 5.5.6 `i2c_slave` V2 (`CONFIG_I2C_ENABLE_SLAVE_DRIVER_VERSION_2=y` in `board_esp32s3_espidf.yaml`), Port 0, Adresse 42 (7 Bit), `broadcast_en=1`, interne Pull-ups je Substitution, Interrupt-Prioritaet 3, Stretch danach per `i2c_ll_slave_enable_scl_stretch(false)` aus.

```cpp
bool slave_setup(int sda, int scl, int addr, bool pullup);   // legt das Device an, setzt g_slv zurueck
bool slave_teardown();                                         // i2c_del_slave_device (fuer den Bus-Scan)
bool slave_pop(Frame &f);                                      // Haupt-Loop: naechstes Paket, false wenn leer
```

`on_receive` (ISR) legt `length` Nutzbytes (ohne Adressbyte) in einen SPSC-Ring mit 32 Slots (`head` nur ISR, `tail` nur Loop). `slave_pop` setzt `bytes[0]=0x00` davor, `ack_bits` alle gesetzt, `flags=F_OVERLEN` bei gekuerztem Paket. Zaehler: `frames`, `bytes`, `overflow` (Ring voll), `errors` (Callback ohne Daten).

### 1.3 `i2c_scan.h`, On-Demand-Bus-Scan (nicht am Geraet getestet)

Ausloeser nur per MQTT `cmd/scan` (nie von selbst). Eigener FreeRTOS-Task `i2cscan` (Stack 5120, Prioritaet 2), der Haupt-Loop veroeffentlicht das Ergebnis (`scan/result`). Ablauf:

1. `g_slave_up=false`, `slave_teardown()` (Slave-Device loeschen, Port 0 frei).
2. `i2c_new_master_bus` auf denselben Pins, Port 0, interne Pull-ups je Substitution. Arbitration im Controller einschalten (`i2c_ll_enable_arbitration(&I2C0, true)`), der IDF-Master laesst sie aus.
3. Je Adresse (Standard `0x08..0x77`): warten bis SDA und SCL zusammen mindestens 5 ms High sind (nur Lesen von `GPIO_IN_REG`, bis 1 s je Versuch), dann eigener Probe `scan_probe` ueber die LL-Register mit 10 kHz (START, Adresse+W, STOP; Interrupts aus, Abfrage von `int_raw`, Timeout 20 ms). ACK = gefunden, NACK = `nack++`. Bei Arbitrationsverlust, Hardware-Timeout oder belegtem Bus nach STOP: `arb_lost++`, Master-Bus loeschen und neu anlegen (Peripheral-Reset, kein Bus-Clear), 10 ms warten, `retries++`, max. 3 Versuche je Adresse; danach Adresse in `failed`. Nach 3 Adressen in Folge ohne Ergebnis Abbruch mit `err`.
4. `i2c_del_master_bus`, danach Slave exakt wie beim Boot neu anlegen (`slave_setup`, bis zu 3 Versuche, auch nach Fehlern), Slave-Zaehler bleiben erhalten.

Es ist zu jedem Zeitpunkt nur ein Treiber (Slave V2 oder Master) aktiv, kein Legacy-Treiber. `i2c_master_probe` wird nicht benutzt: fester Takt 100 kHz und Bus-Clear mit 9 SCL-Pulsen nach Arbitrationsverlust oder Timeout (IDF 5.5.6, `i2c_master.c` Zeilen 553, 602, 801). Risiko: Der ESP ist kurz Master auf dem Bus der LWZ, ein LWZ-Frame kann dabei gestoert werden.

### 1.4 `i2c_lwz.h`, Glue

Nur diese Funktionen werden von YAML-Lambdas aufgerufen (Namespace-frei, damit `lambda: i2cs_setup(...)` geht):

```cpp
void i2cs_setup(int sda_gpio, int scl_gpio, int txd_gpio, bool internal_pullup,
                int slave_addr);  // einmal in on_boot
void i2cs_loop();      // interval 20 ms
void i2cs_status();    // interval 5 s: Status + availability (retained "online") publizieren
```

Aufgaben von `i2cs_setup`:
1. MQTT-Abos per `esphome::mqtt::global_mqtt_client->subscribe(topic, callback, qos)` (kein `on_message` in YAML): `stiebel-i2c/cmd/sniff`, `stiebel-i2c/cmd/scan`. Callbacks setzen nur Flags/Zustand bzw. starten den Scan-Task.
2. Pins pruefen (`pins_valid`: 0..31, paarweise verschieden, `txd_gpio` nur Kollisionspruefung, GPIO18 wird nicht angefasst), Adresse 8..119 pruefen, `slave_setup`.

`i2cs_loop`:
- Fertigen Scan veroeffentlichen (`scan/result`), danach `scan_running=false`.
- Frames aus dem Slave-Ring holen (`slave_pop`), `unix_ms` setzen: `mkdbg::unix_ms()` minus `(millis() - t_ms)` wenn synchron, sonst 0. Danach in einen Ausgabering (256 Frames) schieben, abhaengig von `cmd/sniff` (`on=false`: verwerfen; `all`: alle; `changed`: nur wenn `ChangeFilter::pass`). Ausgabering voll: `frames_dropped++`.
- Batch publizieren auf `stiebel-i2c/rx`: hoechstens alle 50 ms, hoechstens 16 Frames und 3500 Byte pro Nachricht, QoS 0, nicht retained. Ohne MQTT-Verbindung werden Frames verworfen (gezaehlt).

Status (`stiebel-i2c/status`, alle 5 s): siehe 2.

Datei `firmware\i2c_lwz.yaml`, ESPHome-Paket:

```yaml
esphome:
  includes:            # Reihenfolge: ring_buffer.h, debug_json.h, unix_time.h, i2c_frame.h, i2c_slave_rx.h, i2c_scan.h, i2c_lwz.h
    - firmware/...
  on_boot:
    - priority: -100   # nach MQTT-Setup (Listenform, damit sie mit anderen on_boot-Eintraegen mergt)
      then:
        - lambda: |-
            i2cs_setup(${i2c_sda_gpio}, ${i2c_scl_gpio}, ${can_txd_gpio}, ${i2c_internal_pullup}, ${i2c_slave_addr});
interval:
  - interval: 20ms
    then: [ lambda: 'i2cs_loop();' ]
  - interval: 5s
    then: [ lambda: 'i2cs_status();' ]
```

Substitutionen (in `mk1-i2c.yaml`): `i2c_slave_addr: "42"`, `i2c_sda_gpio: "15"` (Traegerplatine, alter Aufbau 21), `i2c_scl_gpio: "17"`, `can_txd_gpio: "18"`, `can_rxd_gpio: "16"` (nur Zahlen, da in Lambda verwendet), `i2c_internal_pullup: "true"`.

Hinweis: `debug_json.h` aus mk1-debug includet `ring_buffer.h`. Darum wird `ring_buffer.h` ebenfalls nach `firmware\` kopiert (unveraendert) und vor `debug_json.h` in `includes` gefuehrt. `debug_json.h` liefert die `dj_get_*`-Parser (`dj_get_bool`, `dj_find_value`) fuer `cmd/sniff`.

Dateien `mk1-i2c.yaml`: wie `mk1-debug.yaml`, aber `device_name: mk1-i2c`, Packages `board`, `network`, `lwz` (= `firmware/i2c_lwz.yaml`), MQTT `topic_prefix: stiebel-i2c`, birth/will auf `stiebel-i2c/availability` (online/offline, qos 1, retain), logger INFO, SNTP (Substitution `ntp_server`), api/ota wie mk1-debug (Schluessel aus `secrets.yaml`: `debug_api_key`, `debug_ota_password`, `mqtt_server`, `mqtt_port`, `mqtt_username`, `mqtt_password`). Kein `on_message`, kein `i2c:`, `light:`, `canbus:`.

## 2. MQTT (Praefix `stiebel-i2c/`)

QoS 0 ausser availability (QoS 1, retained). Alle Payloads UTF-8 JSON ohne Leerzeichen-Zwang.

| Topic | Richtung | Payload |
|---|---|---|
| `cmd/sniff` | GUI zu ESP | `{"on":true,"mode":"all"}`. `mode`: `"all"` oder `"changed"`. Fehlende Felder behalten den aktuellen Wert. Start: on=true, mode=changed. |
| `cmd/scan` | GUI zu ESP | `{}` oder `{"from":"0x08","to":"0x77"}` (Zahl oder Hex-String, Standard `0x08..0x77`, Grenzen 0x08..0x77). Startet den Bus-Scan (1.3). Laeuft schon einer oder ist der Slave nicht aktiv: Antwort `ok:false` mit `err`. |
| `rx` | ESP zu GUI | Array: `[{"t":123456,"u":1761400000000,"d":"00e3201001...","a":"+++-","f":0}]` |
| `status` | ESP zu GUI | alle 5 s, siehe unten |
| `scan/result` | ESP zu GUI | `{"found":[80],"failed":[],"from":8,"to":119,"nack":111,"arb_lost":0,"retries":0,"ms":1500,"ok":true}` (Adressen dezimal). `err` (Text) nur bei Fehler. `ok`: Scan lief durch und der Slave ist wieder aktiv. `failed`: Adressen ohne Ergebnis nach 3 Versuchen. |
| `availability` | ESP zu GUI | `online`/`offline`, retained, Last Will, alle 5 s neu |

`rx`-Felder je Frame:
- `t`: `t_ms` (ms seit ESP-Start, Zeitbasis `millis()`), Ganzzahl.
- `u`: Unix-Zeit in ms, `0` wenn NTP nicht synchron (Feld immer vorhanden).
- `d`: Bytes als Hex-Kleinbuchstaben, ohne Trenner, `bytes[0]` ist das Adressbyte (LWZ 180: `00`).
- `a`: String, ein Zeichen je Byte in `d`: `+` ACK, `-` NACK. Laenge == Byteanzahl.
- `f`: Flags (Ganzzahl, siehe FrameFlags: 4 OVERLEN, sonst 0).

`status`:

```json
{"fw":"i2c","uptime_s":123,"frames":50,"frames_dropped":0,"ring_fill":0,"ring_cap":32,
 "heap":123456,"pullup":true,"sniff":true,"mode":"changed","ntp":true,"scan_running":false,
 "lvl_sda":1,"lvl_scl":1,"slave_addr":42,"slave_frames":50,"slave_bytes":900,
 "slave_overflow":0,"slave_errors":0}
```
`frames` = Slave-Pakete. `ring_fill`/`ring_cap` gehoeren zum Slave-Ring (32 Slots). `frames_dropped` = verworfene Frames im Ausgabering (voll oder MQTT getrennt). `sniff` und `mode` sind der Streaming-Schalter und der Filter aus `cmd/sniff` (der Topic-Name bleibt). `lvl_sda`/`lvl_scl` sind die aktuellen Pegel (nur Lesen, Hardware-Diagnose). `scan_running`: Bus-Scan aktiv (der Slave ist dann pausiert). `fw` ist immer `"i2c"` (die GUI erkennt daran die Firmware).

## 3. JSONL-Log, CSV (GUI)

JSONL, eine Zeile je Frame, Schluesselreihenfolge: `t`, `ts` (optional, Unix-Sekunden float, round 3), `d`, `a`, `f`, `ts_src` (optional `"esp"`/`"pc"`):

```
{"t":123456,"ts":1761400000.123,"d":"00e3201001","a":"++++","f":0,"ts_src":"esp"}
```
Der Lader akzeptiert auch rohe rx-Zeilen mit `u` (ms): fehlt `ts` und ist `u > 0`, gilt `ts = u/1000`. Fehlerhafte Zeilen werden uebersprungen.

CSV: Semikolon, UTF-8 mit BOM, CRLF. Spalten (`FRAME_CSV_HEADER`): `t;zeit_unix;zeit_lokal;adresse;header;typ;data_hex;ack;flags;crc_ok;name;wert`. Die Dekodierspalten (`header`, `typ`, `crc_ok`, `name`, `wert`) sind leer ohne `decode`-Funktion.

## 4. Python-Module (`mk1-i2c\gui\`)

Frame-Zeile (`row`, ueberall gleich): `{"t":int, "u":int (ms, 0 = keine), "data":bytes, "ack":str, "flags":int, optional "ts":float, optional "ts_src":str}`.

### 4.1 `i2c_ref_decoder.py` Python-Referenzdecoder nur fuer Logic-Analyzer-Importe (Saleae, SDA/SCL-Flanken). Nachbau des frueheren Firmware-Decoders (2026-10-30 aus der Firmware entfernt), keine Firmware-Schnittstelle mehr

```python
class I2cRefDecoder:
    def __init__(self, cycles_per_us: int = 240, origin_cyc: int = 0, origin_ms: int = 0)
    def feed(self, cyc: int, lvl: int) -> None
    def advance(self, now_cyc: int) -> None
    def mark_overflow(self) -> None
    def pop(self) -> dict | None            # {"t_ms","data":bytes,"ack":str,"flags":int}
    def pending(self) -> int
    counters: dict   # Schluessel: events, starts, stops, frames, frames_dropped, err_nostop, err_bits, err_overlen, err_ring, err_skew
    def reset(self, keep_counters: bool = False) -> None

def decode_events(events, cycles_per_us=240) -> tuple[list[dict], dict]   # events: Iterable (cyc, lvl); liefert (frames, counters)
def synth_events(data: bytes, ack=None, stop=True, t0=0, half_bit_cyc=3840, repstart=False) -> list[tuple[int,int]]  # synthetische Event-Folge fuer Tests (Start, 9 Bit je Byte, optional Stop). Standard: alle ACK.
FLAG_NOSTOP, FLAG_BITERR, FLAG_OVERLEN, FLAG_RING_OVF, FLAG_REPSTART   # Konstanten
class ChangeFilter: def pass_(self, row) -> bool   # gleiche Regeln wie C++ ChangeFilter (i2c_frame.h)
```
`frame_to_row(frame: dict, unix_ms: int = 0) -> dict` wandelt ref-Frame in Frame-Zeile (`t`, `u`, `data`, `ack`, `flags`).

### 4.2 `mqtt_bridge.py` (WP4), Muster `mk1-debug\gui\mqtt_client.py`

```python
TOPIC_PREFIX = "stiebel-i2c/"
RX_TOPICS = ("rx", "status", "scan/result", "availability")

class MqttBridge(QObject):
    connected = Signal(bool)
    rx_frames = Signal(list)          # Liste von Frame-Zeilen (mit ts, ts_src)
    status = Signal(dict)
    scan_result = Signal(dict)         # Ergebnis des Bus-Scans
    availability = Signal(str)        # "online" | "offline"
    def connect_to(self, host, port, user, password) -> None   # paho 1.6, loop_start
    def disconnect_from(self) -> None
    def send_sniff(self, on: bool, mode: str = "changed") -> None   # mode "all"|"changed", sonst ValueError
    def send_scan(self, from_addr: int | None = None, to_addr: int | None = None) -> None   # cmd/scan
    def _on_message(self, client, userdata, msg) -> None            # ohne Netzwerk testbar (Fake-msg mit .topic, .payload)
    _now = time.time                                                # ersetzbar
```
`rx`-Verarbeitung: pro Item `t`,`d` (hex), `a`, `f` Pflicht, `u` optional. `u > 0`: `ts = round(u/1000, 3)`, `ts_src="esp"`. Sonst `ClockSync` (Kopie aus mk1-debug, Fallback `pc`). Fehlerhafte Items/Nachrichten ignorieren, nie Exception nach aussen. `a` mit falscher Laenge: Item verwerfen. Das Passwort wird nicht gespeichert/geloggt.

### 4.3 `mqtt_secrets.py` (WP4)

Kopie aus `mk1-debug\gui\mqtt_secrets.py`, `SECRETS_PATH` zeigt auf `mk1-i2c\secrets.yaml` (`Path(__file__).resolve().parents[1] / "secrets.yaml"`).

### 4.4 `lwz180.py` (WP5), Parameter aus `referenz\lwz180_types.json`

```python
def crc8(data: bytes, poly=0x49, init=..., refin=False, refout=False, xorout=0) -> int   # Werte laut WP1 (CRC_PARAMS)
CRC_PARAMS: dict      # aus lwz180_types.json["crc"], inkl. "covers" und "verified"
def build_packet(header: bytes, payload: bytes, addr: int = 0x00) -> bytes   # addr + header + payload + crc (fuer Tests)
def decode(frame_bytes: bytes) -> dict
```
`decode` bekommt den gesamten mitgelesenen Frame (inkl. Adressbyte, bei LWZ 180 `0x00`). Rueckgabe-Dict (alle Schluessel immer vorhanden, `None` wenn nicht zutreffend):

```python
{"ok": bool,            # Adresse/Header bekannt, Laenge passt, CRC ok
 "addr": int|None, "header": "e3 20 10"|None, "kind": "istwert"|"tasten"|"display"|"unknown",
 "payload": bytes, "crc_rx": int|None, "crc_calc": int|None, "crc_ok": bool|None,
 "type": int|None, "name": str|None, "raw": int|None, "value": float|None, "unit": str|None,
 "keys": list[int]|None, "segments": bytes|None, "error": str|None}
```
Unbekannter Istwert-Typ: `name="UNK_0x..", value=raw` (ohne Skalierung, `unit=None`). Zu kurze/falsche Frames: `ok=False`, `error` gesetzt, nie Exception.

`referenz\lwz180_types.json` (WP1) Schema:

```json
{"source": "...", "retrieved": "2026-10-25", "unverified": true,
 "crc": {"poly": "0x49", "init": "0x00", "refin": false, "refout": false, "xorout": "0x00",
         "covers": "header+payload", "verified": false, "note": "..."},
 "packets": {"istwert": {"header": "e3 20 10", "length": 7, "layout": "..."},
             "tasten": {"header": "e3 30 20", "length": 7, "layout": "..."},
             "display": {"header": "f7 20", "length": 28, "layout": "..."}},
 "types": {"0x01": {"name": "...", "unit": "C", "scale": 0.1, "signed": true, "confirmed": true}},
 "keys": {"1": "..."}}
```
Werte sind das, was WP1 aus kdarnok/lwz180 tatsaechlich findet. Ist etwas dort nicht eindeutig, steht `null` plus Hinweis in `note`/`lwz180_protocol.md`. Nichts raten.

### 4.5 `data_model.py` (WP4), kein Qt

```python
class DataModel:
    def __init__(self, decode=None, max_frames=200000)       # decode: Callable(bytes)->dict (lwz180.decode), None = nur Rohdaten
    def add_frames(self, rows: list[dict]) -> list[dict]      # speichert, dekodiert, gibt Dekodierergebnisse zurueck
    def clear(self) -> None
    frames: list[dict]                       # Frame-Zeilen (aeltere werden bei max_frames verworfen)
    decoded: list[dict|None]                 # parallel zu frames
    latest: dict[int, dict]                  # Typ -> {"name","value","unit","raw","ts"}
    def history(self, type_id: int) -> list[tuple[float, float]]   # (ts, value), ts Unix-s, Fallback t/1000
    display_current: bytes|None; display_previous: bytes|None       # 28-Byte-Segmentpaket
    def display_changed_bits(self) -> list[int]                     # Bitindizes (MSB zuerst je Byte), die sich gegenueber previous aendern
    key_log: list[tuple[float, list[int]]]
    counts_by_header: dict[str, int]
    crc_total: int; crc_bad: int
    def crc_error_rate(self) -> float                               # 0.0 wenn crc_total == 0
    def filter_rows(self, header: str|None = None, text: str|None = None) -> list[dict]
```

### 4.6 `export_io.py` (WP4), kein Qt

```python
FRAME_CSV_HEADER   # siehe Abschnitt 3
def save_frames_csv(path, rows, decode=None) -> None
def save_frames_jsonl(path, rows) -> None
def load_frames_jsonl(path) -> list[dict]
def load_saleae_i2c_csv(path) -> list[dict]       # Frame-Zeilen aus Saleae-I2C-Export, siehe unten
def compare_frames(captured: list[dict], reference: list[dict]) -> dict
    # {"matched": int, "missing": [row,...], "extra": [row,...], "different": [(captured_row, reference_row), ...], "match_rate": float}
```
`load_saleae_i2c_csv`: header-getrieben, zwei Formate.
- Logic 2: Spalten `name,type,start_time,duration,...` mit Typen `start`, `address`, `data`, `stop` (Spalten fuer `address`, `data`, `read`, `ack` je nach Export; Namen defensiv per Header suchen, Gross-/Kleinschreibung ignorieren).
- Logic 1 (Legacy): `Time [s],Packet ID,Address,Data,Read/Write,ACK/NACK`, eine Zeile je Byte, Frame = gleiche `Packet ID`.
Frame = ab `start`/Adresse bis `stop`. `data[0] = (adresse << 1) | rw` (rw 1 = Read), danach Datenbytes. `ack`: `+`/`-` je Byte. `t` = `round(start_time*1000)`, `ts` = nicht gesetzt (relative Zeit), `u=0`, `flags=0`, `ts_src="saleae"`. Werte "0x00" oder "00" oder dezimal toleriert. Fehlerhafte Zeilen ueberspringen.
`compare_frames`: Vergleich nur ueber die Reihenfolge der Bytes (`data.hex()`), per `difflib.SequenceMatcher` ueber die Listen. `missing` = nur in `reference`, `extra` = nur in `captured`, `different` = ersetzte Bloecke gleicher Laenge paarweise. Keine Zeit-Toleranz noetig. `match_rate = matched / max(len(reference), 1)`.

### 4.7 GUI (WP5)

Dateien: `lwz180.py`, `tab_live.py`, `tab_werte.py`, `tab_display.py`, `tab_status.py`, `i2c_gui.py`, `requirements.txt` (`PySide6`, `paho-mqtt<2`).

```python
class I2cMainWindow(QMainWindow):
    def __init__(self, bridge: MqttBridge | None = None, model: DataModel | None = None)
    def load_offline(self, path: str) -> int          # JSONL laden, in Model einspeisen, Tabs aktualisieren, Anzahl Frames
    def import_saleae(self, path: str) -> dict        # Vergleich mit aktuellem Mitschnitt, Ergebnis von compare_frames, zeigt Dialog nur bei interaktivem Aufruf
    model: DataModel; bridge: MqttBridge
def main(argv=None) -> int                            # Optionen: --load DATEI
```
Tabs bekommen `(model, bridge, parent=None)` und haben `refresh()` (Daten neu zeichnen) sowie `on_frames(rows)`. Verbindung: Broker, Port, Benutzer eingeben, Passwort nur zur Laufzeit ueber `mqtt_secrets.load_mqtt_settings()` oder Eingabefeld, nicht speichern. Menue Datei: CSV/JSONL exportieren, Offline-Log laden, Saleae-CSV importieren und vergleichen.
- Live: Tabelle (Zeit, Bytes, ACK, Flags, Header/Typ), Filter nach Header/Text, Pause, Autoscroll.
- Werte: Tabelle letzter Wert je Typ (`UNK_0x..` fuer unbekannt), Verlauf als `QChart` (PySide6.QtCharts).
- Display: Bitraster 28 Byte x 8, geaenderte Bits hervorgehoben; darunter Tasten-Log.
- Status: Zaehler aus `status`, CRC-Fehlerquote, Knopf "Bus-Scan" (`send_scan`) mit Ergebnisanzeige (`scan_result`), Warnung bei `slave_overflow` > 0 oder `slave_errors` > 0.

Beispiel-Offline-Log: `mk1-i2c\referenz\beispiel_log.jsonl` (WP5, synthetisch mit `lwz180.build_packet` erzeugt: Istwerte-Frames e3 20 10 mit mehreren Typen, Tasten e3 30 20, Display f7 20 mit 28 Byte, je mit gueltiger CRC, ein Frame mit falscher CRC).

## 5. Dateiliste je WP (alles unter `C:\temp\stiebel-Remote\mk1-i2c\`)

| WP | Dateien |
|---|---|
| 1 | `referenz\lwz180_protocol.md`, `referenz\lwz180_types.json` |
| 2 | `firmware\i2c_frame.h`, `firmware\i2c_slave_rx.h`, `firmware\i2c_scan.h` (frueher i2c_events.h, i2c_decoder.h, test_i2c_decoder.cpp, 2026-10-30 entfernt) |
| 3 | `firmware\i2c_lwz.h`, `firmware\i2c_lwz.yaml`, `mk1-i2c.yaml`, `secrets.yaml` (Kopie), Kopien `firmware\board_esp32s3_espidf.yaml`, `firmware\network_ethernet_w5500.yaml`, `firmware\debug_json.h`, `firmware\ring_buffer.h`, `firmware\unix_time.h` |
| 4 | `gui\mqtt_bridge.py`, `gui\mqtt_secrets.py`, `gui\data_model.py`, `gui\export_io.py`, `gui\i2c_ref_decoder.py`, `tests\test_i2c_ref_decoder.py`, `tests\test_data_model.py`, `tests\test_export_io.py`, `tests\test_saleae.py`, `tests\test_mqtt_bridge.py`, `tests\test_mqtt_secrets.py` |
| 5 | `gui\lwz180.py`, `gui\tab_live.py`, `gui\tab_werte.py`, `gui\tab_display.py`, `gui\tab_status.py`, `gui\i2c_gui.py`, `gui\requirements.txt`, `referenz\beispiel_log.jsonl`, `tests\test_lwz180.py`, `tests\test_gui_smoke.py` |
| 6 | `README.md`, `INTEGRATION.md`, Verweis in `..\mk1_hw\README.md` (nur ein Abschnitt/Link ergaenzen) |

Python-Tests setzen am Dateikopf `GUI_DIR` in `sys.path` (wie `mk1-debug\tests\test_export_io.py`) und Qt-Tests `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` vor dem Qt-Import. Python-Interpreter: `C:\temp\stiebel-Remote\.venv\Scripts\python.exe`.

## 6. Display-Nachbau (Bedienteil-Tab)

Plan: `C:\Users\bonze\.claude\plans\declarative-weaving-otter.md`. Neue Dateien: `referenz\lwz180_display.jpg`, `referenz\lwz180_display_layout.json`, `gui\display_model.py`, `gui\display_widget.py`, `gui\tab_bedienteil.py`, `tests\test_display_model.py`, `tests\test_tab_bedienteil.py`. Geaendert: `gui\data_model.py`, `gui\i2c_gui.py`. Der Tab "Display" (Bitraster) bleibt unveraendert.

Indexbasis: `seg` ist das 28-Byte-Paket ohne Adressbyte (`DataModel.display_current`, `lwz180.decode(...)["segments"]`). Index 0 = `f7`, 1 = `20`, Index 27 = CRC. Alle `byte`-Angaben im Layout sind solche Indizes.

### 6.1 `referenz\lwz180_display_layout.json`

```json
{"source": "kdarnok/lwz180 README + images/full_display.jpg", "photo": "lwz180_display.jpg",
 "photo_size": [1234, 567],
 "note": "Positionen normiert 0..1 bezogen auf das Foto (x,y = linke obere Ecke).",
 "elements": [
  {"id": "e", "name": "Zeile 1 Stelle 2 (gross)", "kind": "digit7", "byte": 7, "row": 1, "pos": 2,
   "x": 0.454, "y": 0.144, "w": 0.072, "h": 0.37, "confirmed": true},
  {"id": "f", "name": "Zeile 1 Stelle 3 (Lueftungsstufe)", "kind": "digit7", "byte": 8, "row": 1, "pos": 3,
   "role": "level", "x": 0.552, "y": 0.144, "w": 0.072, "h": 0.37, "confirmed": true},
  {"id": "b", "name": "Uhr", "kind": "icon", "byte": 15, "bit": 0, "x": 0.12, "y": 0.156, "w": 0.056, "h": 0.121, "confirmed": true},
  {"id": "pv2", "name": "LED Power Vent (Sketch)", "kind": "led", "byte": 26, "bit": 0, "x": 0.011, "y": 0.064, "w": 0.04, "h": 0.08, "confirmed": false},
  {"id": "btn_ok", "name": "OK", "kind": "button", "code": 6, "codes": [6, 10], "x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0, "confirmed": false}
 ]}
```

- `kind`: `digit7` (nur `byte`, plus `row` 1|2 und `pos` ab 1; Standard-7-Segment Bit0 = a ... Bit6 = g), `icon`, `led` (`byte` + `bit`), `button` (`code` Hauptcode, optional `codes` = alle Codes die diese Taste aufleuchten lassen, z. B. kurz + lang).
- `id`: Buchstabe aus der README-Tabelle (a..ab), neu `degc`, `dp2`, `colon`. Die Ziffernleiste "2-7" (Elemente o, n, m, l, k, i) hat `kind: "icon"`. Tasten: `id` = `btn_<name>`.
- Rechteck `x,y,w,h` normiert aufs Foto (`photo` = `display_fotos/p1_26-0_grad.jpg`, 1878 x 866).
- Korrektur nach Messung 2026-10-25 (Offset +3 gegenueber der README-Tabelle): grosse Ziffern (Zeile 1, 3 Stellen) = `seg[6..8]` (ids `d`, `e`, `f`; `f` = `role: "level"`), kleine Ziffern (Zeile 2, 4 Stellen) = `seg[9..12]` (ids `p`, `q`, `s`, `t`). `seg[2..5]` sind konstant `0b 20 6d 00`: keine Ziffern, nicht anzeigen.
- Icons (Byte/Bit), `confirmed: true` = am Mitschnitt mit Foto/Anzeige gesehen: Uhr `b` 15/0, Luefter `v` 19/4, Prozent `x` 21/6, Grad C `degc` 22/0, Dezimalpunkt Zeile 2 `dp2` (zwischen Stelle 3 und 4) 22/2, Doppelpunkt Zeile 2 `colon` (zwischen Stelle 2 und 3) 22/4, Filter `ab` 23/0, Bypass `z` 24/2. Nur README+3 (`confirmed: false`): Leistung `a` 15/2, Leiste 7..2 (`o`,`n`,`m`,`l`,`k`,`i`) 15/4, 15/6, 16/0, 16/2, 16/4, 16/6, Schloss `c` 18/2, Dezimalpunkt erste grosse Ziffer `g` 20/2, m3/h `y` 21/2, Frost `aa` 23/6. Power-Vent-LED nur `pv2` 26/0 (Sketch); der alte Eintrag `pv` 23/0 entfaellt (23/0 ist Filter).
- Bytes 13 und 14 (13/0, 13/2, 13/3, 13/4, 13/5, 14/0, 14/2, 14/4, 14/6): bekannt unklar, vermutlich nicht LCD (Tasten-LED/Blinksteuerung?). Auf den Fotos nichts sichtbar, nicht gezeichnet, nicht im Layout.
- Optionales Feld `note` (String).

### 6.2 `gui\display_model.py` (kein Qt)

```python
LAYOUT_PATH      # Path auf referenz/lwz180_display_layout.json
def load_layout(path=LAYOUT_PATH) -> dict          # JSON laden
def decode_display(seg: bytes, layout: dict) -> dict
```

Rueckgabe (alle Schluessel immer vorhanden, nie Exception; bei `seg` None oder Laenge != 28: `ok=False`, `error` gesetzt, leere Strukturen):

```python
{"ok": bool, "error": str|None,
 "digits": {id: {"byte": int, "raw": int, "char": str, "segments": "abdeg", "confirmed": bool}},
 "rows": {1: "...", 2: "..."},       # Zeichen je Zeile (1: 3 Stellen gross, 2: 4 Stellen klein), nach pos sortiert, unbekannter Code = "?", 0x00 = " "
 "icons": {id: bool},                # kind icon UND led, nach id
 "level": int,                       # Lueftungsstufe nach Sketch-Regel, sonst 0
 "unmapped_bits": [(byte, bit), ...],      # gesetzte Bits ohne Zuordnung, sortiert (echte Unbekannte)
 "known_unclear_bits": [(byte, bit), ...]  # gesetzte Bits der Rubrik "bekannt unklar", sortiert
}
```
- `char` aus der Zeichentabelle (`referenz\lwz180_protocol.md` 6.3), `segments` = Zeichenkette der gesetzten Segmente a..g in Reihenfolge a-g.
- `level`: nur auswertbar wenn `seg[20] & 0x04` oder `seg[19] & 0x10`; dann `seg[8]`: `0x06` = 1, `0x5b` = 2, `0x4f` = 3, `0x3f` = 0, sonst 0. Ohne Bedingung 0.
- Zeichentabelle: zusaetzlich zur README `0x54` n, `0x38` L, `0x79` E, `0x71` F, `0x76` H, `0x3e` U, `0x74` h, `0x58` c, `0x1e` J (gesehen: "Cod", "Pro", "Auto", "0FF", "----", "0n").
- `known_unclear_bits` (Rubrik bekannt unklar, kein Fehler): gesetzte Bits der konstanten Bytes 2..5 sowie 13/0, 13/2, 13/3, 13/4, 13/5, 14/0, 14/2, 14/4, 14/6 (`display_model.KNOWN_UNCLEAR_BYTES`, `KNOWN_UNCLEAR_BITS`). Sie erscheinen nicht in `unmapped_bits`.
- `unmapped_bits`: Bits der Bytes 2..26, die weder zu einem `digit7`-Byte noch zu einem Icon/LED-Element noch zu `known_unclear_bits` gehoeren. Byte 0, 1 (Header) und 27 (CRC) zaehlen nicht. Digit-Bytes zaehlen als zugeordnet (alle 8 Bits).

### 6.3 `gui\display_widget.py` (PySide6)

```python
class DisplayWidget(QWidget):
    def __init__(self, layout: dict | None = None, parent=None)   # layout None = load_layout()
    def set_state(self, state: dict | None) -> None   # Rueckgabe von decode_display, None = alles aus; ruft update()
    def set_keys(self, codes) -> None                 # Iterable[int] aktuell leuchtender Tastencodes (match gegen button.code/codes)
    def set_photo_background(self, on: bool) -> None  # Foto statt gezeichnetem Hintergrund
    def sizeHint(self) / heightForWidth                # Seitenverhaeltnis = photo_size
```
`paintEvent` zeichnet alles aus `layout["elements"]` im Seitenverhaeltnis des Fotos (Koordinaten x,y,w,h mal Widgetgroesse). Ziffern an = dunkel, aus = schwach sichtbar. Fehlt das Foto, faellt der Foto-Hintergrund auf den gezeichneten zurueck (kein Fehler). Offscreen-Rendering ohne Absturz Pflicht (`widget.grab()`).

### 6.4 `DataModel.display_history`

`display_history: list[tuple[float, bytes]]` = `(ts, segments)` je Display-Paket (28 Byte, jedes Paket, auch identische), `ts` = `_row_ts(row)`. Gedeckelt per `self._cap(...)` wie `key_log`. Wird in `clear()` neu angelegt und in `_account` beim Display-Zweig angehaengt. Bestehendes Verhalten (`display_current`, `display_previous`) bleibt.

### 6.5 `gui\tab_bedienteil.py`

```python
class BedienteilTab(QWidget):
    def __init__(self, model, bridge=None, parent=None)
    def on_frames(self, rows: list[dict]) -> None    # wie die anderen Tabs
    def refresh(self) -> None
    # fuer Tests: Attribute widget (DisplayWidget), slider (QSlider), play_btn, speed (QComboBox 1x/5x/20x), live (QCheckBox), time_label, unknown_list
    def set_position(self, index: int) -> None        # Zeitleiste auf Eintrag index (0-basiert in display_history)
    def current_ts(self) -> float | None              # Zeitstempel des angezeigten Pakets
    def step(self) -> None                            # ein Abspielschritt (vom QTimer aufgerufen, auch direkt testbar)
```
- Slider hat `len(display_history)` Positionen (Maximum = n-1). Live-Haken: folgt dem neuesten Paket bei `on_frames`/`refresh`.
- Wiedergabe: Play/Pause, Tempo 1x/5x/20x (Echtzeit aus den ts-Differenzen, Timer 50 ms, in einem Schritt hoechstens bis ein Paket weiter oder bis die Zeit reicht; Ende: Pause).
- Tasten-Overlay: aus `model.key_log` leuchten Tasten, deren ts im Fenster [ts - 0.5, ts] liegt (ts = Zeit des angezeigten Pakets; Live: Zeit des neuesten Pakets, ts des neuesten Eintrags in `display_history`/`key_log`, nicht Systemuhr, damit Offline-Logs gehen). Weitergabe an `widget.set_keys`.
- Liste `unknown_list`: `unmapped_bits` des aktuellen Pakets als Text `Byte/Bit`.
- Zeitanzeige: `time_label` mit Position `i/n`, Zeitstempel und Abstand zum ersten Paket.
- `i2c_gui.py`: `self.bedienteil = BedienteilTab(...)`, Tab "Bedienteil" hinter "Display", in `_tab_list` aufnehmen.

Zusatz 6.1 (Ueberwacher, nach WP A): Tasten (`kind: button`) liegen unterhalb des Fotos, `y` > 1.0 (ca. 1.03..1.13). Das Widget-Seitenverhaeltnis ist daher Foto plus Tastenstreifen: Hoehe = photo_h * max(1.0, max(y+h ueber alle Elemente) + 0.02). Normierung x,y,w,h bleibt bezogen auf die Fotogroesse. Ein Foto als Hintergrund deckt nur den Bereich 0..1 ab, darunter wird der Tastenstreifen gezeichnet. Das Foto zeigt keine Beschriftungen a..ab, Zuordnung stammt aus Tabelle 6.1 (korrigiert um +3 Byte, siehe oben). Zeilenlayout nach den echten Fotos: Zeile 1 gross oben mittig (3 Stellen), Zeile 2 klein unten mittig (4 Stellen), Grad C rechts oben neben Zeile 2, Doppelpunkt zwischen Stelle 2 und 3, Dezimalpunkt unten zwischen Stelle 3 und 4, Bypass = zwei Pfeile nach oben (zweiter mit Phi) rechts neben Zeile 1, Filter = kariertes Quadrat unten rechts, Luefter rechts neben der 3. grossen Ziffer, Uhr oben links. `decode_display` liefert im Fehlerfall `rows == {}`, Nutzer verwenden `rows.get(1, "")`.
