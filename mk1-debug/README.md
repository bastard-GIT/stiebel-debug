# mk1-debug – CAN/Elster-Debug-Firmware

Diese Anleitung beschreibt das Debug-Werkzeug für die Stiebel Eltron WPL-A am Mk1-Gerät (ESP32-S3, `<ip-mk1>`). Sie richtet sich an den Projektbesitzer, der das Gerät selbst flasht.

Diese Datei enthält keine Zugangsdaten. Passwörter und Schlüssel stehen ausschließlich in `secrets.yaml` (lokal, nie committen) bzw. werden zur Laufzeit abgefragt.

## Zweck

Das Werkzeug besteht aus zwei Teilen:

1. **Debug-Firmware** (ESPHome, esp-idf) auf dem Mk1-ESP32-S3. Sie liest den CAN-Bus mit, sendet gezielte Lese-Requests, führt Index-Scans und Member-Probes durch. Sie dekodiert nicht. Schreiben ist nur über `cmd/write` möglich, mit Rücklesen und Sicherheitsprüfungen. Die Ergebnisse gehen per MQTT (Präfix `stiebel-debug/`).
2. **GUI** auf dem PC (eigenes Repo [ha-stiebel-control-gui](https://github.com/bastard-GIT/ha-stiebel-control-gui)). Sie steuert die Firmware per MQTT, dekodiert Frames anhand von `ElsterTable.json` und exportiert Mitschnitte.

Funktionen:

- CAN-Bus mitlesen (Rohframes, dekodiert in der GUI)
- Einzelne Elster-Signale gezielt abfragen
- Index-Scanner über einen Indexbereich eines CAN-Members
- Member-Probe: einen Index bei allen CAN-IDs abfragen
- Schreiben mit Rücklesen (`cmd/write`, nur Debug-Firmware)

Die Produktiv-Firmware (Topic-Wurzel `heatingpump/`) bietet eine eigene Bus-Analyse: Bus-Scan eines Members, Teilnehmer-Scan, Einzel-Read, Schreiben (nur mit Build-Schalter `can_raw_write`) und einen CAN-Frame-Stream. Die GUI spricht im Produktiv-Modus diese Topics an: die Tabs Abfrage und Scanner sind dort aktiv (mit eingeschränkten Optionen), der Scanner enthält zusätzlich die Teilnehmer-Suche. Details, Payloads und Einschränkungen: `INTERFACES.md`, Abschnitt 13.

## Ordnerstruktur

```
mk1-debug/
  mk1-debug.yaml              ESPHome-Startdatei
  secrets.yaml                MQTT-Zugang, API-Schlüssel (nur lokal, nie committen)
  README.md                   diese Datei
  firmware/
    board_esp32s3_espidf.yaml   Kopie aus dem Mk1-Ordner
    network_ethernet_w5500.yaml Kopie aus dem Mk1-Ordner
    can_debug.yaml              canbus-Konfiguration (TWAI, große Queues)
    ring_buffer.h               Ringpuffer (Single-Producer/Single-Consumer)
    elster_frame.h              Frame-Aufbau und Parser
    debug_json.h                Minimal-JSON für MQTT
    can_debug.h                 Sniffer, Abfrage, Scanner, MQTT-Ausgabe
  tests/                        Header-Testquellen (C++)
```

Die GUI (früher `gui/`) und ihre Python-Tests liegen im Repo ha-stiebel-control-gui. `tools/` und `run_full.py` importieren Module der GUI (`elster.py`, `auto_runner.py`, `type_guess.py`) und laufen nur, wenn die GUI-Module dort wieder unter `gui/` liegen (Kopie oder Verzeichnis-Link aus ha-stiebel-control-gui).

Der Ordner `esp32-s3-technik-mk1` (Produktiv-Firmware) bleibt unverändert.

## Voraussetzungen

- **Python-venv:** `C:\temp\stiebel-Remote\.venv\`
- **ESPHome 2026.9.1** im venv: `C:\temp\stiebel-Remote\.venv\Scripts\esphome.exe`
- **GUI:** Repo [ha-stiebel-control-gui](https://github.com/bastard-GIT/ha-stiebel-control-gui) (PySide6, paho-mqtt), eigene Installation dort.

- MQTT-Broker `<ip-broker>`, Port <port>, Benutzer `<user>`. Das Passwort wird nicht in Dateien geschrieben.

## Kompilieren

Im Ordner `mk1-debug`:

```
cd C:\temp\stiebel-Remote\mk1-debug
C:\temp\stiebel-Remote\.venv\Scripts\esphome.exe compile mk1-debug.yaml
```

Der Build prüft unter anderem die Grenzwerte der TWAI-Queues (`rx_queue_len: 512`, `tx_queue_len: 64`). Die C++-Header werden auf diesem PC nicht separat übersetzt, weil kein C++-Compiler installiert ist. Die Prüfung erfolgt ausschließlich durch `esphome compile`.

## Vor dem Flashen: Mk1-Binary sichern

Bevor die Debug-Firmware auf das Gerät kommt, wird die aktuell laufende Mk1-Firmware als Binary gesichert. Damit ist ein Zurückflashen ohne Neubau möglich.

1. Im Mk1-Ordner kompilieren:

   ```
   cd C:\temp\stiebel-Remote\esp32-s3-technik-mk1
   C:\temp\stiebel-Remote\.venv\Scripts\esphome.exe compile esp32-s3-technik-mk1.yaml
   ```

2. Die `.bin`-Datei aus dem Build-Verzeichnis `.esphome\build\` kopieren (Unterordner des Gerätenamens, Datei mit Endung `.bin`) und an einem sicheren Ort ablegen, z. B. mit Datum im Dateinamen.

Hinweis: Der Build-Unterordner hängt vom Gerätenamen und Framework ab. Die `.bin`-Datei dort suchen, nicht blind den Pfad übernehmen.

## Flashen per OTA

> **Warnung:** Solange die Debug-Firmware auf dem Mk1-Gerät läuft, liefert das Gerät **keine Wärmepumpen-Werte an Home Assistant**. Das Flashen erfolgt nur bewusst. Die Dauer des Debug-Betriebs so kurz wie möglich halten und danach zurückflashen.

Voraussetzungen: Das Mk1-Binary ist gesichert (siehe oben), das Gerät ist unter `<ip-mk1>` erreichbar, `secrets.yaml` ist im Ordner `mk1-debug` vorhanden.

```
cd C:\temp\stiebel-Remote\mk1-debug
C:\temp\stiebel-Remote\.venv\Scripts\esphome.exe upload mk1-debug.yaml --device <ip-mk1>
```

Der Upload läuft über ESPHome-OTA (`ota: platform: esphome` in `mk1-debug.yaml`).

## Zurückflashen der Produktiv-Firmware

Aus dem Mk1-Ordner, mit der Produktiv-YAML:

```
cd C:\temp\stiebel-Remote\esp32-s3-technik-mk1
C:\temp\stiebel-Remote\.venv\Scripts\esphome.exe upload esp32-s3-technik-mk1.yaml --device <ip-mk1>
```

Danach prüfen, ob die Werte wieder in Home Assistant erscheinen.

## Bedienung der GUI

Die GUI ist ausgezogen in das eigene Repo [ha-stiebel-control-gui](https://github.com/bastard-GIT/ha-stiebel-control-gui) (Installation, Start, Tabs: dessen README, deutsch: README.de.md). Sie spricht sowohl diese Debug-Firmware (Präfix `stiebel-debug/`) als auch die Produktiv-Firmware (`heatingpump/`) an.

## MQTT-Topics

Präfix: `stiebel-debug/`

| Topic | Richtung | Inhalt |
|---|---|---|
| `cmd/sniff` | GUI → ESP | `{"on":true,"ids":["0x180"],"types":[2]}` – leere Listen = alles |
| `cmd/read` | GUI → ESP | `{"member":"0x180","index":"0x4ef0"}`, optional `format`, `switch`, `src`, `timeout_ms` |
| `cmd/scan` | GUI → ESP | `{"member":"0x500","from":"0x4e00","to":"0x4fff","delay_ms":200}` |
| `cmd/probe` | GUI → ESP | `{"index":"0x000b","from_id":"0x000","to_id":"0x7ff","delay_ms":100}` – Index bei allen CAN-IDs |
| `cmd/write` | GUI → ESP | `{"member":"0x201","index":"0x0013","value":"0x0208","format":"auto"}` – schreibt, nur Debug-Firmware |
| `cmd/stop` | GUI → ESP | `{}` – bricht Scan, Probe, Read und Write ab |
| `cmd/sniff` | GUI → ESP | `{"on":true,"ids":["0x180"],"types":[2]}` – leere Listen = alles |
| `rx` | ESP → GUI | Batch aus Frames: `[{"t":123456,"id":"0x180","d":"d200fa01120200","tx":false}]` |
| `read/result` | ESP → GUI | `{"member":"0x180","index":"0x4ef0","switch":"0xfa","raw":"0x03e8","status":"ok"}` |
| `write/result` | ESP → GUI | `{"member":"0x201","index":"0x0013","value":"0x0208","status":"verified","ack":4,"readback":"0x0208"}` |
| `scan/progress` | ESP → GUI | `{"member":"0x500","format":"auto","index":"0x4e40","switch":"0xfa","done":64,"total":512,"state":"running"}` |
| `scan/result` | ESP → GUI | `{"member":"0x500","format":"auto","switch":"0xfa","items":[{"index":"0x4e40","raw":"0x0123","status":"ok"}]}` |
| `probe/progress` | ESP → GUI | `{"index":"0x000b","id":"0x1a0","done":416,"total":2047,"state":"running"}` |
| `probe/result` | ESP → GUI | `{"index":"0x000b","items":[{"id":"0x180","raw":"0x8000","status":"unsupported"}]}` |
| `status` | ESP → GUI | `{"fw":"debug","rx":0,"tx":0,"rx_fill":0,"rx_cap":4096,"overflow":0,"twai_err":0,"bus_off":false,"heap":0,"ntp":false}` |
| `availability` | ESP → GUI | `online` / `offline` (retained, Last Will) |

Details zu Feldern, Status-Werten und Regeln: `INTERFACES.md`, Abschnitte 1, 7.2, 8.3, 10 und 12.

### Produktiv-Firmware

Topic-Wurzel `heatingpump/` (Konstante `PROD_ROOT` in `gui/prod_api.py`). Die GUI erkennt die Firmware-Art am Feld `fw` in `stiebel-debug/status` (fehlt es, gilt `prod`).

| Funktion | Topics |
|---|---|
| Verfügbarkeit | `heatingpump/status` (`online` / `offline`) |
| CAN-Frame-Stream | `heatingpump/can_debug/set`, `state` (retained), `frames` |
| Bus-Scan eines Members oder einer CAN-ID | `heatingpump/scan/start`, `scan/stop`, `scan/status` (retained), `scan/<name>/0x<idx>` (retained) |
| Teilnehmer-Scan | `heatingpump/scan/members`, `scan/members/0x<id>` (retained) |
| Einzel-Read, Schreiben | `heatingpump/raw/read`, `raw/write`, `raw/result` (nicht retained) |

Welche Topics mit welchem Build-Schalter gelten, Payloads, Status-Werte und Einschränkungen im Produktiv-Modus (Tabelle je GUI-Funktion, Auto-Runner `--fw prod`): `INTERFACES.md`, Abschnitt 13. Die Sniffer-Details stehen in Abschnitt 8.7.

Im Produktiv-Modus filtert die GUI den CAN-Frame-Stream selbst (CAN-ID, Art). Der Sniffer ist nur aktiv, wenn die Firmware mit `can_debug: "true"` gebaut wurde (Mk1: ja). Schreiben über `raw/write` braucht zusätzlich `can_raw_write: "true"` (Mk1: aus).

## Sicherheitshinweise

- **Schreiben nur gezielt.** `cmd/write` gibt es nur in der Debug-Firmware. Die Firmware schreibt nur im Leerlauf, nur an Ziele 0x000–0x7FF außer 0x680, und Index 0xF7–0xFA mit Wert 0x8000 wird nicht gesendet. Das Ergebnis steht in `write/result` (`verified`, `mismatch`, `no_ack`, `timeout`, `rejected`). Ablauf und Regeln: `INTERFACES.md`, Abschnitt 10.
- **Produktiv-Schreiben (`raw/write`) nur mit Build-Schalter `can_raw_write: "true"`.** Dann kann jeder MQTT-Client mit Broker-Zugang beliebige Werte an beliebige CAN-IDs schreiben, ohne Whitelist. Auf Mk1 ist der Schalter aus. Ohne Schalter antwortet die Firmware mit `rejected`. Details: `INTERFACES.md`, Abschnitt 13.4.
- **Mindestabstand 100 ms** zwischen zwei gesendeten Requests. Ein `delay_ms` unter 100 wird auf 100 angehoben.
- **`secrets.yaml` nie committen.** Die Datei enthält MQTT-Zugang und API-Schlüssel.
- **Keine Zugangsdaten im README** oder in anderen Dokumenten dieses Ordners.
- Das Passwort in der GUI wird nicht gespeichert und nicht geloggt.

## Bekannte Grenzen

- **Referenztabelle teils unsicher:** `ElsterTable.json` ist laut Quelle unvollständig. Unbekannte Indizes erscheinen als `UNK_0x…`, unbekannte Member als `UNBEKANNT_0x…`.
- **Nicht unterstützte Signale:** Rohwert `0x8000` bedeutet „nicht unterstützt“ (Wert `-255`). Beispiel laut Projektübersicht: `WW_ECO` antwortet auf der WPL-A nicht.
- **Buslast:** Bei 50 kbps sind bis etwa 400 Frames/s möglich. Der Ringpuffer fängt Spitzen ab. Verluste zeigt der Überlaufzähler im Tab Status.
- **Scanner:** Viele Requests können den Bus belasten. Der Scanner soll bei TWAI-Fehlern oder Bus-Off stoppen (Planstand; vor dem Einsatz am Gerät prüfen).
- **Offene Punkte:** Ob das Board PSRAM hat (größerer Ringpuffer möglich) und der maximale `rx_queue_len` im ESPHome-`esp32_can`-Treiber sind beim ersten Build zu klären.
- **MQTT-Ausfall:** Bei getrenntem Broker füllt sich der Ringpuffer. Frames werden nach dem Wiederverbinden nachgeliefert, solange der Puffer nicht überläuft.
- **Keine Wärmepumpen-Werte in Home Assistant** während der Debug-Firmware läuft (siehe Warnung oben).
