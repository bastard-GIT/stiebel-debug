# stiebel-debug

Debug-Paket und Debug-Werkzeuge fuer die Stiebel-Eltron-Waermepumpe (Elster-CAN-Bus) mit ESPHome.

Lizenz: GPL-3.0, siehe [LICENSE](LICENSE). Die Elster-Tabellen und -Logik gehen auf ha-stiebel-control (GPLv3) und Jürg Müller (CAN-Progs, GPL) zurück.

## 1. Paket fuer die Produktiv-Firmware

Dateien: `stiebel_debug.yaml` und Ordner `stiebel_debug/` (`can_rx_stream.h`, `ring_buffer.h`, `debug_json.h`, `unix_time.h`).

Einbindung in die Firmware-YAML:

```yaml
packages:
  stiebel_debug: github://bastard-GIT/stiebel-debug/stiebel_debug.yaml@main
```

Voraussetzung: Waermepumpen-Firmware mit den Hook-Makros `STIEBEL_DEBUG_RX(id, x)` und `STIEBEL_DEBUG_TX(id, x)`
(Fork `bastard-GIT/ha-stiebel-control`, Branch `wpla07`). Das Paket setzt `-DSTIEBEL_DEBUG`, bindet `stiebel_debug/`
ein und nutzt `::esphome::mqtt::global_mqtt_client` (keine feste MQTT-id noetig). Ohne das Paket sind die Makros leer.

Das Paket bringt selbst mit: `mqtt.on_connect` (availability online, retained), `mqtt.on_message` fuer
`stiebel-debug/cmd/sniff`, `interval 50ms` (`dbgrx_flush()`), `interval 5s` (`dbgrx_publish_status()`).

MQTT-Topics (Format: `mk1-debug/INTERFACES.md`):

| Topic | Inhalt |
|---|---|
| `stiebel-debug/rx` | Batch empfangener CAN-Frames (hoechstens alle 50 ms) |
| `stiebel-debug/status` | Zaehler und Status alle 5 s. TWAI-Felder: `twai_err` (Busfehler gesamt), `bus_off`, `rx_missed` (TWAI-RX-Queue voll, Frame verworfen), `rx_overrun` (Hardware-FIFO uebergelaufen), `rx_queue` (wartende Frames), `tec`/`rec` (Fehlerzaehler) |
| `stiebel-debug/availability` | `online`, retained |
| `stiebel-debug/cmd/sniff` | Kommando, z.B. `{"on":true,"ids":["0x180"],"types":[2]}`. Sniffer ist standardmaessig AUS |

Das Paket ist nicht allein baubar: es braucht die Waermepumpen-Firmware (Hooks, `mqtt`, TWAI).

## 2. Werkzeuge

| Ordner | Inhalt |
|---|---|
| `mk1-debug/` | Eigene Debug-Firmware `mk1-debug.yaml` (Scanner, Lesen/Schreiben per MQTT; **keine Paket-Variante**, wird anstelle der Produktiv-Firmware geflasht), `firmware/`, PySide6-GUI `gui/`, `tools/`, `tests/`, `referenz/`, `INTERFACES.md`, `run_full.py` |
| `mk1-i2c/` | Test-Firmware `mk1-i2c.yaml` und GUI fuer die LWZ-180-I2C-Schnittstelle (Display, Luefter), `firmware/`, `gui/`, `tests/`, `referenz/`, `INTEGRATION.md`, `INTERFACES.md` |
| `i2c_sniffer/` | Dokumentation zum I2C-Sniffer (`DOKUMENTATION.md`) |

Die Unterordner-READMEs stammen aus der Arbeitsumgebung des Autors und nennen teils Windows-Pfade (`C:\temp\stiebel-Remote`).
IP-Adressen sind durch `<ip-mk1>` und `<ip-broker>` ersetzt.

### Abhaengigkeiten und Start

```
pip install PySide6 "paho-mqtt<2"      # mk1-debug/gui/requirements.txt
python mk1-debug/gui/elster_gui.py     # Debug-GUI
python mk1-i2c/gui/i2c_gui.py          # I2C-GUI
```

Tests (Python 3, pytest):

```
cd mk1-debug && QT_QPA_PLATFORM=offscreen python -m pytest -q tests
cd mk1-i2c   && QT_QPA_PLATFORM=offscreen python -m pytest -q tests
```

### Referenztabelle (ElsterTable.json)

Die GUI braucht `ElsterTable.json` aus https://github.com/bastard-GIT/elster_reference. Klone das Repo neben dieses Repo:

```
git clone https://github.com/bastard-GIT/elster_reference ../elster_reference
```

Suchreihenfolge (`mk1-debug/gui/elster.py`, `default_reference_path()`): Umgebungsvariable `ELSTER_REFERENCE_JSON`,
dann `../elster_reference/ElsterTable.json`, dann der alte Pfad `../referenzen/elster_reference/ElsterTable.json`.
Die Tests nutzen dieselbe Funktion.

### Zugangsdaten und Umgebungsvariablen

Zugangsdaten stehen nicht im Repo. `secrets.yaml` (`mqtt_server`, `mqtt_port`, `mqtt_username`, `mqtt_password`) liegt im jeweiligen Werkzeugordner
(`mk1-debug/secrets.yaml`, `mk1-i2c/secrets.yaml`), alternativ `STIEBEL_SECRETS`. Die YAMLs nutzen `!secret` und die Substitution `ntp_server`.

| Variable | Wirkung |
|---|---|
| `ELSTER_REFERENCE_JSON` | Pfad zur `ElsterTable.json` |
| `STIEBEL_SECRETS` | Pfad zur `secrets.yaml` |
| `STIEBEL_MQTT_HOST`, `STIEBEL_MQTT_USER` | Vorbelegung des Verbindungsdialogs |
| `STIEBEL_LOG_DIR` | Log-Verzeichnis für Mitschnitte und Auswertungen (Default `mk1-debug/logs`), siehe unten |
| `MK1_IP`, `STIEBEL_ROOT` | nur `mk1-debug/run_full.py` (Maintainer-Skript) |

### Log-Verzeichnis

Die Werkzeuge schreiben Mitschnitte und Auswertungen nach `mk1-debug/logs/`. Mit Umgebungsvariable `STIEBEL_LOG_DIR` wird dieser Ordner überschrieben (gilt für `run_full.py`, `tools/fes_src_test.py`, `tools/latency.py`, `tools/analyse_switch_bytes.py` und den Mitschnitt-Test in `tests/test_type_guess.py`). Ohne Variable bleibt der Default `mk1-debug/logs/`. `STIEBEL_ROOT` setzt nur die Wurzel für venv, Firmware und Backup. Ein eigener Ausgabeordner für `gui/auto_runner.py` geht weiterhin über `--out <ordner>`; `run_full.py` übergibt dafür seinen Log-Ordner (also auch den aus `STIEBEL_LOG_DIR`) als `--out`.

Beispiel für ein Arbeitslayout mit ausgelagerten Logs: `<STIEBEL_ROOT>/daten/can-logs/` (Platzhalter, kein fester Pfad). Dorthin zeigt `--out`, die Werkzeuge selbst lesen dann nicht automatisch von dort.

## KI-Unterstützung

Debug-Paket und Werkzeuge (mk1-debug, mk1-i2c, i2c_sniffer) wurden mit Unterstützung von KI erstellt (Claude von Anthropic, über Claude Code), einschließlich Code, Dokumentation und Auswerte-Werkzeugen. Die Protokoll-Analyse beruht auf eigenen Messungen am Gerät (Logic-Analyzer- und Bus-Logs) und den genannten Quellen. Die Firmware läuft beim Autor auf echter Hardware (ESP32-S3, Stiebel Eltron WPL-A 07 / LWZ 180) und ist dort getestet. Einsatz auf eigene Verantwortung. Bei Tasten- und Schreibbefehlen auf den Bus ist Vorsicht geboten.
