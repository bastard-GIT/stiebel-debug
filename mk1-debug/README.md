# mk1-debug – CAN/Elster-Debug-Firmware und GUI

Diese Anleitung beschreibt das Debug-Werkzeug für die Stiebel Eltron WPL-A am Mk1-Gerät (ESP32-S3, `<ip-mk1>`). Sie richtet sich an den Projektbesitzer, der das Gerät selbst flasht.

Diese Datei enthält keine Zugangsdaten. Passwörter und Schlüssel stehen ausschließlich in `secrets.yaml` (lokal, nie committen) bzw. werden zur Laufzeit abgefragt.

## Zweck

Das Werkzeug besteht aus zwei Teilen:

1. **Debug-Firmware** (ESPHome, esp-idf) auf dem Mk1-ESP32-S3. Sie liest den CAN-Bus mit, sendet gezielte Lese-Requests und führt Index-Scans durch. Sie dekodiert nicht und kann nicht schreiben. Die Ergebnisse gehen per MQTT (Präfix `stiebel-debug/`).
2. **PySide6-GUI** auf dem PC. Sie steuert die Firmware per MQTT, dekodiert Frames anhand von `ElsterTable.json` und exportiert Mitschnitte.

Funktionen:

- CAN-Bus mitlesen (Rohframes, dekodiert in der GUI)
- Einzelne Elster-Signale gezielt abfragen
- Index-Scanner über einen Indexbereich eines CAN-Members

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
  gui/
    elster_gui.py               PySide6-Hauptfenster
    elster.py                   Dekodierung/Kodierung auf Basis von ElsterTable.json
    export_io.py                CSV- und JSONL-Export/Import
    mqtt_client.py              paho-mqtt in eigenem Thread, Signale an Qt
    tab_live.py, tab_query.py, tab_scanner.py, tab_status.py
    requirements.txt            PySide6, paho-mqtt
  tests/                        Tests für Python (pytest/unittest) und Header-Testquellen
```

Referenztabelle: `..\referenzen\elster_reference\ElsterTable.json` (Lesehilfe: `ElsterTable.README.md` im selben Ordner).

Der Ordner `esp32-s3-technik-mk1` (Produktiv-Firmware) bleibt unverändert.

## Voraussetzungen

- **Python-venv:** `C:\temp\stiebel-Remote\.venv\`
- **ESPHome 2026.9.1** im venv: `C:\temp\stiebel-Remote\.venv\Scripts\esphome.exe`
- **PySide6** und **paho-mqtt** (1.6.x-API) für die GUI, installiert im selben venv:

  ```
  C:\temp\stiebel-Remote\.venv\Scripts\python.exe -m pip install -r C:\temp\stiebel-Remote\mk1-debug\gui\requirements.txt
  ```

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

Start (aus dem Ordner `mk1-debug`):

```
C:\temp\stiebel-Remote\.venv\Scripts\python.exe gui\elster_gui.py
```

- **Verbinden:** Broker, Port und Benutzer eingeben. Das Passwort wird bei jedem Start abgefragt und nicht gespeichert.
- **Tab Live:** Rohframes und dekodierte Werte. Filter nach CAN-ID, Nachrichtenart und Index/Name. Pause, Löschen, Autoscroll. Ansicht umschaltbar zwischen allen Frames und dem letzten Wert pro Index. Schaltfläche zum Senden der Sniffer-Filter an die Firmware.
- **Tab Abfrage:** Member aus der Liste wählen, Index per Hex oder Namenssuche angeben. Das Ergebnis wird dekodiert angezeigt, der Verlauf als Tabelle.
- **Tab Scanner:** Member, Indexbereich und Pause (mindestens 100 ms, Standard 200 ms) eingeben. Start und Stopp, Fortschrittsanzeige, Trefferliste mit Namen aus der Referenz oder `UNK_0x…`.
- **Tab Status:** Zähler aus dem Status-Topic (RX, TX, Füllstand des Ringpuffers, Überläufe, TWAI-Fehler, Bus-Off).
- **Exporte:** Mitschnitt als CSV oder als Rohlog (JSON-Lines). Trefferliste des Scanners als CSV. Abgleich mit der Referenz: bekannt, unbekannt oder anderer Typ.
- **Offline-Log:** Über Menü Datei → Offline-Log laden kann ein gespeichertes Rohlog ohne Verbindung dekodiert angezeigt werden.

## MQTT-Topics

Präfix: `stiebel-debug/`

| Topic | Richtung | Inhalt |
|---|---|---|
| `cmd/sniff` | GUI → ESP | `{"on":true,"ids":["0x180"],"types":[2]}` – leere Listen = alles |
| `cmd/read` | GUI → ESP | `{"member":"0x180","index":"0x4ef0"}` |
| `cmd/scan` | GUI → ESP | `{"member":"0x500","from":"0x4e00","to":"0x4fff","delay_ms":200}` |
| `cmd/stop` | GUI → ESP | `{}` – Scan abbrechen |
| `rx` | ESP → GUI | Batch aus Frames: `[{"t":123456,"id":"0x180","d":"d200fa01120200","tx":false}]` |
| `read/result` | ESP → GUI | `{"member":"0x180","index":"0x4ef0","raw":"0x03e8","status":"ok"}` |
| `scan/progress` | ESP → GUI | `{"index":"0x4e40","done":64,"total":512}` |
| `scan/result` | ESP → GUI | `{"member":"0x500","items":[{"index":"0x4e40","raw":"0x0123","status":"ok"}]}` |
| `status` | ESP → GUI | `{"rx":0,"tx":0,"rx_fill":0,"rx_cap":4096,"overflow":0,"twai_err":0,"bus_off":false,"heap":0}` |
| `availability` | ESP → GUI | `online` / `offline` (retained, Last Will) |

Status-Werte in `read/result` und `scan/result`: `ok`, `unsupported` (Rohwert `0x8000`), `timeout`.

## Sicherheitshinweise

- **Nur Lese-Requests.** Die Firmware hat keine Schreibfunktion. Es werden keine Werte an die Wärmepumpe geschrieben.
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
