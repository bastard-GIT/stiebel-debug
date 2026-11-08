# mk1-i2c: I2C-Slave fuer die LWZ 180 (Test-Firmware und PC-Auswertung)

Diese Anleitung beschreibt die Test-Firmware fuer den Mk1 (Waveshare ESP32-S3-ETH, `<ip-mk1>`) und die PC-Auswerte-GUI. Sie richtet sich an den Projektbesitzer, der das Geraet selbst flasht.

Diese Datei enthaelt keine Zugangsdaten. Schluessel stehen nur in `secrets.yaml` (lokal, nie committen). Die Datei wird nie ausgegeben.

## Zweck und Grenzen

- **Nur I2C.** Die Firmware hoert den I2C-Bus zwischen der Stiebel Eltron LWZ 180 (Lueftungsanlage) und ihrem Display mit. Es gibt keinen CAN-Betrieb (kein `canbus:`, kein TWAI).
- **Waehrend des Tests liefert der Mk1 keine WP-Werte an Home Assistant.** Die Produktiv-Firmware ist dann nicht aktiv. Test so kurz wie moeglich halten und danach zurueckflashen.
- **Hardware-I2C-Slave (seit 2026-10-29)**, siehe Abschnitt "Slave-Modus". Der I2C-Controller arbeitet als normaler Slave und quittiert (ACK). Er treibt SDA/SCL open-drain nur im Rahmen des I2C-Protokolls. **Harte Regel: SDA und SCL nie ausserhalb des I2C-Protokolls treiben** (kein GPIO-Output, kein `light:` auf GPIO21). Der ISO1540 ist bidirektional, jede Leitungsaenderung sieht auch die LWZ 180.
- **Der passive Software-Sniffer ist entfallen (2026-10-30).** Test am Geraet: Slave 159 Frames in 30 s, CRC 158/158 ok, 0 Flags, `slave_overflow` 0, `slave_errors` 0, LWZ-Display laeuft normal. Sniffer im selben Test: rund 96 % CRC ok, `err_bits`/`err_skew` durch ISR-Latenz. Deshalb wurde die Idee verworfen (kein Rueckfall, kein Plan B mit LCD_CAM/I2S).
- **Der ESP liefert nur Rohframes** (Bytes, ACK, Flags). Die LWZ-180-Dekodierung passiert in der GUI. Einige Details sind laut Referenz ungeklaert, siehe Abschnitt "Offene Punkte".
- Die Firmware ist so geschrieben, dass sich der Code spaeter in die Produktiv-Firmware uebernehmen laesst. Details siehe `INTEGRATION.md`.

Hardware-Grundlagen (Pinbelegung, ISO1540, WS2812 auf GPIO21): `..\mk1_hw\README.md`, Abschnitt 4.

## Slave-Modus

Der Nutzer hat am 2026-10-29 entschieden: passiv ist nicht mehr Pflicht. Der Weg entspricht kdarnok/lwz180 (Arduino-TWI-Slave mit General Call).

- Substitutionen in `mk1-i2c.yaml`: `i2c_slave_addr: "42"`, `i2c_sda_gpio: "21"` (alter Aufbau, Traegerplatine: 15), `i2c_scl_gpio`, `i2c_internal_pullup`.
- Treiber: IDF-5.5.6-Slave-Treiber V2 (`CONFIG_I2C_ENABLE_SLAVE_DRIVER_VERSION_2=y`, gesetzt in `firmware\board_esp32s3_espidf.yaml`). Code: `firmware\i2c_slave_rx.h`.
- Adresse 42 (7 Bit), General Call (`broadcast_en`) an, ACK auf alles. Interrupt-Prioritaet 3, interne Pull-ups je Substitution.
- **Kein Clock-Stretching.** V2 hat kein `stretch_en`, schaltet es immer ein. Der Code schaltet es nach dem Anlegen per `i2c_ll_slave_enable_scl_stretch(false)` ab. Nicht wieder einschalten.
- Der Treiber liefert pro Paket die Nutzdaten ohne Adressbyte. Die Firmware setzt `bytes[0]=0x00` (General Call) davor, damit die GUI Frames mit Adressbyte `00` sieht. Pakete direkt an Adresse 42 sind davon nicht unterscheidbar. `ack_bits` sind alle gesetzt, `flags=0` (bei mehr als 63 Nutzbytes `F_OVERLEN`).
- Uebergabe aus dem Callback (ISR) per lock-freiem SPSC-Ring (32 Pakete) an den 20-ms-Loop, danach Filter (`changed`|`all`), Ausgabering und MQTT `stiebel-i2c/rx`.
- Zusaetzliche Felder in `stiebel-i2c/status`: `slave_addr`, `slave_frames`, `slave_bytes`, `slave_overflow` (Ring voll), `slave_errors` (Callback ohne Daten). `frames` zaehlt die Slave-Pakete. Das Feld `mode` ist der Filtermodus (`changed`|`all`).

## Bus-Scan (on demand, noch nicht am Geraet getestet)

Auf Anforderung (`cmd/scan`) sucht der ESP Adressen am LWZ-Bus. Der Scan startet nie von selbst. Dabei ist der ESP kurz I2C-**Master**. Die LWZ 180 ist selbst Master (rund 5 General-Call-Frames/s bei etwa 13,5 kHz).

- Ablauf (eigener FreeRTOS-Task `i2cscan`, Haupt-Loop und MQTT-Keepalive laufen weiter): Slave-Device loeschen, `i2c_master`-Bus auf denselben Pins und Port 0 anlegen (nur fuer Pins, Takt-Quelle und Port; interne Pull-ups je Substitution), Takt 10 kHz, Arbitration im Controller eingeschaltet, Interrupts aus. Je Adresse warten bis SDA und SCL mindestens 5 ms High sind (nur Lesen von `GPIO_IN_REG`), dann eigener Probe ueber die LL-Register (START, Adresse+W, STOP, Abfrage von `int_raw`, Timeout 20 ms). Bei Arbitrationsverlust, Hardware-Timeout oder belegtem Bus nach STOP: Master-Bus loeschen und neu anlegen (Peripheral-Reset, kein Bus-Clear, keine SCL-Pulse), 10 ms warten, bis zu 3 Versuche je Adresse. Danach Master-Bus loeschen und den Slave exakt wie beim Boot neu anlegen (auch nach Fehlern, bis zu 3 Versuche). Slave-Zaehler bleiben erhalten.
- Waehrend des Scans fehlen Frames. Es ist immer nur ein Treiber (Slave V2 oder Master) auf Port 0 aktiv, kein Legacy-Treiber. Laut IDF-5.5.6-Quellen geht Master und Slave nacheinander am selben Port: Beim Loeschen gibt `i2c_release_bus_handle` den Port frei.
- **Takt 10 kHz, kein `i2c_master_probe`.** `i2c_master_probe` (IDF 5.5.6) hat festen Takt 100 kHz und fuehrt nach Arbitrationsverlust oder Timeout einen Bus-Clear mit 9 SCL-Pulsen aus (`i2c_master.c` Zeilen 553, 602, 801), also mitten in einem LWZ-Frame. Deshalb eigener Probe ueber die LL-Register (`scan_probe` in `firmware/i2c_scan.h`).
- **Risiko:** Der ESP treibt kurz START, Adressbyte und STOP auf den Bus der LWZ. Verlorene Arbitration oder ein haengender Controller koennen ein LWZ-Frame stoeren. Nicht am Geraet getestet. Erstes Mal bei laufender Beobachtung (LWZ-Display, Logic Analyzer) ausloesen.
- Ergebnis: `scan/result`, Felder siehe MQTT-Topics. `err` ist nur bei Fehlern gesetzt. `ok` heisst: Scan lief durch und der Slave ist wieder aktiv. `failed` listet Adressen ohne Ergebnis nach 3 Versuchen. `arb_lost` zaehlt Timeouts (der Treiber unterscheidet Arbitrationsverlust und Bus belegt nicht).
- Status: `scan_running` (bool) im `status`.

## Ordnerstruktur

```
mk1-i2c/
  mk1-i2c.yaml                  ESPHome-Startdatei (Board, Ethernet, MQTT, API, OTA, SNTP, Paket lwz)
  secrets.yaml                  lokale Schluessel (nie committen, nie ausgeben)
  README.md                     diese Datei
  INTERFACES.md                 verbindliche Schnittstellen (Firmware, MQTT, Dateiformate, Python-Module)
  INTEGRATION.md                Schritte zur Uebernahme in esp32-s3-technik-mk1
  firmware/
    i2c_frame.h                 Frame-Struktur, Flag F_OVERLEN, ChangeFilter (Modus "changed")
    i2c_slave_rx.h              Hardware-I2C-Slave (Treiber V2), SPSC-Ring ISR zu Loop
    i2c_scan.h                  On-Demand-Bus-Scan (eigener Task, Master-Bus nur waehrenddessen)
    i2c_lwz.h                   Glue: Setup, Loop, MQTT-Ausgabe, Status, cmd/sniff, cmd/scan
    i2c_lwz.yaml                ESPHome-Paket: includes, on_boot, interval
    board_esp32s3_espidf.yaml, network_ethernet_w5500.yaml   Kopien aus mk1-debug
    debug_json.h, ring_buffer.h, unix_time.h                 Kopien aus mk1-debug
  gui/
    i2c_gui.py                  Hauptfenster (Einstieg)
    mqtt_bridge.py              paho-mqtt 1.6.x im eigenen Thread, Qt-Signale
    mqtt_secrets.py             Zugangsdaten-Abfrage (nicht gespeichert)
    lwz180.py                   Paketdekoder LWZ 180 (Header, CRC-8, Typtabelle)
    i2c_ref_decoder.py          Python-Referenzdecoder nur fuer Logic-Analyzer-Importe (Saleae)
    data_model.py, export_io.py Datenmodell ohne Qt, CSV/JSONL-Export und Import, Saleae-Vergleich
    tab_live.py, tab_werte.py, tab_display.py, tab_status.py
    requirements.txt            PySide6, paho-mqtt<2
  referenz/
    lwz180_protocol.md          Protokollbeschreibung (aus kdarnok/lwz180, mit Unsicherheiten)
    lwz180_types.json           Typtabelle, CRC-Parameter, Paketformate
    beispiel_log.jsonl          synthetisches Offline-Log (26 Zeilen, gueltige CRC, eine falsche CRC)
  tests/                        pytest-Tests fuer Python
```

## Bauen

Build in PowerShell oder cmd, nicht in Git Bash / MSys. Unter Git Bash scheitert esphome mit "Component discovery found no built-in ESP-IDF components".

```
cd C:\temp\stiebel-Remote\mk1-i2c
C:\temp\stiebel-Remote\.venv\Scripts\esphome.exe compile mk1-i2c.yaml
```

Die C++-Header werden auf dem PC nicht separat uebersetzt (kein C++-Compiler). Die Pruefung erfolgt nur durch `esphome compile`.

## Flashen

**Nur nach ausdruecklicher Freigabe durch den Nutzer.** Vorher muessen alle Punkte erfuellt sein:

1. Produktiv-Backup vorhanden unter `C:\temp\stiebel-Remote\backup\`. Achtung: Das neueste Backup ist `mk1-produktiv-unixtime-router-2026-10-21` (nur `firmware.ota.bin`, `firmware.factory.bin` und `common.yaml`). Welche Version gerade auf dem Geraet laeuft, ist nicht dokumentiert. Vor dem Flashen klaeren und eine Sicherung des aktuellen Stands anlegen.
2. Geraet unter `<ip-mk1>` erreichbar, `secrets.yaml` im Ordner `mk1-i2c` vorhanden.
3. Der Build war erfolgreich (`.esphome\build\mk1-i2c\build\firmware.ota.bin` existiert). Im Stand dieser Datei fehlt diese Datei noch.

OTA-Upload:

```
cd C:\temp\stiebel-Remote\mk1-i2c
C:\temp\stiebel-Remote\.venv\Scripts\esphome.exe upload mk1-i2c.yaml --device <ip-mk1>
```

Danach:

1. Status beobachten: `stiebel-i2c/status` (alle 5 s). Erwartet: `fw` = `"i2c"`, `slave_frames` steigt, `slave_overflow` = 0, `slave_errors` = 0.
2. In der GUI den Status-Tab pruefen (Zaehler, CRC-Fehlerquote).
3. Mitschnitt mit dem Logic Analyzer (Saleae) parallel aufnehmen. Den Export in der GUI importieren (Menue Datei, Saleae-CSV importieren und vergleichen). Abweichungen (fehlende oder unterschiedliche Frames) werden angezeigt.

### Zurueckflashen der Produktiv-Firmware

Die Produktiv-Firmware wird aus dem Mk1-Ordner hochgeladen:

```
cd C:\temp\stiebel-Remote\esp32-s3-technik-mk1
C:\temp\stiebel-Remote\.venv\Scripts\esphome.exe upload esp32-s3-technik-mk1.yaml --device <ip-mk1>
```

Danach pruefen, ob die Werte wieder in Home Assistant erscheinen.

Hinweise:
- `esphome upload` baut aus dem aktuellen Quellstand von `esp32-s3-technik-mk1`. Ein Upload des Backup-Binaers ist im Projekt nicht als Befehl dokumentiert. Vor dem Zurueckflashen den Quellstand gegen das Backup pruefen.
- `mk1-debug\run_full.py` ist **nicht** fuer mk1-i2c geeignet. Es baut `mk1-debug`, startet den CAN-Auto-Scan und spielt danach die Produktiv-Firmware zurueck. Der Zurueckflash-Schritt dort prueft das Binary per SHA256 gegen `backup\mk1-produktiv-rxstream-fw-2026-10-21\firmware.ota.bin`.

## Auswerte-GUI (PC)

Installation (einmalig, im venv):

```
C:\temp\stiebel-Remote\.venv\Scripts\python.exe -m pip install -r C:\temp\stiebel-Remote\mk1-i2c\gui\requirements.txt
```

Start (aus dem Ordner `mk1-i2c`):

```
C:\temp\stiebel-Remote\.venv\Scripts\python.exe gui\i2c_gui.py
C:\temp\stiebel-Remote\.venv\Scripts\python.exe gui\i2c_gui.py --load referenz\beispiel_log.jsonl
```

- **Verbindung:** Broker, Port, Benutzer. Das Passwort wird zur Laufzeit abgefragt und nicht gespeichert (`mqtt_secrets.py`).
- **Tab Live:** Rohframes mit Zeit, Bytes, ACK-Zeichen und Flags. Filter nach Header oder Text, Pause, Autoscroll. Schaltflaeche zum Senden von `cmd/sniff`.
- **Tab Werte:** Letzter Istwert je Typ, dekodiert nach `lwz180_types.json`. Unbekannte Typen als `UNK_0x..`. Verlauf als QtChart.
- **Tab Display:** 28-Byte-Segmentpaket (`f7 20`) als Bitraster, geaenderte Bits orange. Darunter das Tasten-Log (`e3 30 20`). Die Bitbelegung ist nicht gesichert (siehe Offene Punkte).
- **Tab Status:** Zaehler aus `status`, CRC-Fehlerquote, Knopf "Bus-Scan" mit Ergebnisanzeige (`cmd/scan`, `scan/result`). Warnung bei `slave_overflow > 0` oder `slave_errors > 0`.
- **Menue Datei:** CSV und JSONL exportieren, Offline-Log laden, Saleae-CSV importieren und mit dem Mitschnitt vergleichen.

## MQTT-Topics (Praefix `stiebel-i2c/`)

QoS 0, ausser `availability` (QoS 1, retained). Payloads sind UTF-8 JSON.

| Topic | Richtung | Payload |
|---|---|---|
| `cmd/sniff` | GUI zu ESP | `{"on":true,"mode":"all"}`. `mode`: `all` oder `changed`. Fehlende Felder behalten den Wert. Start: on=true, mode=changed. |
| `cmd/scan` | GUI zu ESP | `{}` oder `{"from":"0x08","to":"0x77"}` (Zahl oder Hex-String, Standard 0x08..0x77). Startet den Bus-Scan, siehe Abschnitt "Bus-Scan". |
| `rx` | ESP zu GUI | Array aus Frames, z. B. `[{"t":123456,"u":1761400000000,"d":"00e3201001","a":"+++-","f":0}]` |
| `status` | ESP zu GUI | alle 5 s, Felder siehe unten |
| `scan/result` | ESP zu GUI | `{"found":[80],"failed":[],"from":8,"to":119,"nack":111,"arb_lost":0,"retries":0,"ms":1500,"ok":true}` (Adressen dezimal). Bei Fehler zusaetzlich `"err":"..."`, auch bei abgelehntem Kommando (`ok:false`). |
| `availability` | ESP zu GUI | `online` / `offline`, retained, Last Will, alle 5 s neu |

Felder je Frame in `rx`: `t` (ms seit Start), `u` (Unix-ms, 0 wenn NTP nicht synchron), `d` (Hex, `bytes[0]` ist das Adressbyte), `a` (ein Zeichen je Byte: `+` ACK, `-` NACK), `f` (Flags: 4 OVERLEN, sonst 0).

Felder in `status`: `fw`, `uptime_s`, `frames`, `frames_dropped`, `ring_fill`, `ring_cap` (Slave-Ring), `heap`, `pullup`, `sniff` (Streaming an/aus), `mode` (Filter), `ntp`, `scan_running`, `lvl_sda`, `lvl_scl` (Pegel, Hardware-Diagnose), `slave_addr`, `slave_frames`, `slave_bytes`, `slave_overflow`, `slave_errors`.

## Log- und Exportformate

**JSONL** (eine Zeile je Frame, Schluesselreihenfolge `t`, `ts`, `d`, `a`, `f`, `ts_src`):

```
{"t":123456,"ts":1761400000.123,"d":"00e3201001","a":"++++","f":0,"ts_src":"esp"}
```

Der Lader akzeptiert auch rohe `rx`-Zeilen mit `u`. Fehlt `ts`, gilt `ts = u/1000`. Fehlerhafte Zeilen werden uebersprungen.

**CSV:** Semikolon-getrennt, UTF-8 mit BOM, CRLF. Kopfzeile:

```
t;zeit_unix;zeit_lokal;adresse;header;typ;data_hex;ack;flags;crc_ok;name;wert
```

Die Dekodierspalten (`header`, `typ`, `crc_ok`, `name`, `wert`) sind leer, wenn keine Dekodierfunktion aktiv ist.

**Saleae-Import:** Logic 2 (`name,type,start_time,duration,...`) und Logic 1 (`Time [s],Packet ID,Address,Data,Read/Write,ACK/NACK`). Frames aus Saleae haben `ts_src="saleae"` und keine absolute Zeit.

## Tests

Aus `C:\temp\stiebel-Remote`:

```
C:\temp\stiebel-Remote\.venv\Scripts\python.exe -m pytest mk1-i2c\tests
```

Die Python-Tests decken den Referenz-Decoder (LA-Importe), Datenmodell, Export/Import, Saleae-Vergleich, LWZ-180-Dekodierung, MQTT-Bridge (ohne Netzwerk) und einen GUI-Smoke-Test (offscreen) ab.

## Hardware-Hinweise

- **GPIO21 ist der Datenpin der WS2812B-LED.** Jeder I2C-Verkehr auf SDA laesst die LED flackern. Das ist harmlos. Es darf kein `light:` auf GPIO21 stehen (Ausgangstreiber auf SDA).
- **Interne Pull-ups:** Auf der ESP-Seite gibt es keine externen Pull-ups. Der Code nutzt die internen Pull-ups des ESP32-S3 (ca. 45 kOhm, laut `..\mk1_hw\README.md`). Gemessener Bustakt 13,5 kHz (2026-10-25), dafuer sollten sie reichen. Die Flankenform am Logic Analyzer pruefen (Anstiegszeit). Bei Problemen externe Pull-ups (z. B. 4,7 kOhm nach 3,3 V) nur nach Rueckfrage an den Nutzer.
- **CAN-Transceiver ruhig (`firmware\can_idle.h`, nur Test-Build):** GPIO18 (ISO1042 TXD) wird beim Booten als Erstes (on_boot priority 1000) als Ausgang fest auf High gesetzt. High ist rezessiv, der ISO1042 kann den CAN-Bus so nicht dominant ziehen. `can_idle_check()` prueft das jede Sekunde und setzt den Pin bei Abweichung neu (Log `can_idle`). GPIO16 (RXD) ist nur Eingang. Zwischen Reset und on_boot haelt der interne Pull-up des ISO1042-TXD-Eingangs den Bus rezessiv. Im Test-Build gibt es keinen CAN-Treiber.

## Offene Punkte und Risiken

- **CRC:** Mit Saleae-Mitschnitt vom 2026-10-25 fuer Istwert (167/167) und Display (12/12) bestaetigt (`crc.verified=true`). Tastenpakete waren im Mitschnitt nicht enthalten.
- **Display-Bitbelegung unklar.** Die Indexbasis (mit oder ohne Header) und einige Bitangaben (Power Vent, Zeitschaltung) widersprechen sich zwischen README und Sketch. Details in `referenz\lwz180_protocol.md`, Abschnitt 6 und 8.
- **Referenz nicht byte-genau geprueft.** Laut `lwz180_protocol.md` stammt die Protokollbeschreibung teils aus Fetch-Zusammenfassungen. Vor einer Umsetzung das Original (README und `lwz180.ino`) lesen.
- **Konstante `i2c_internal_pullup`:** Standard `true`. Ohne Pull-up fehlt das Signal ganz. Den Logic Analyzer vor dem ersten Flash nutzen, falls moeglich.
- **Bus-Scan nicht am Geraet getestet**, siehe Abschnitt "Bus-Scan".
- **Pull-ups am Logic Analyzer pruefen**, siehe Hardware-Hinweise.
- **GPIO18 im Produktiv-Betrieb:** Dort ist GPIO18 der TWAI-TX-Pin. Die Test-Firmware darf dort nicht laufen, solange CAN aktiv ist. Siehe `INTEGRATION.md`.

## Sicherheitshinweise

- Die Firmware schreibt im Normalbetrieb nichts aktiv auf den Bus (der Slave quittiert nur). Einzige Ausnahme ist der Bus-Scan auf `cmd/scan` (ESP kurz Master, nur START, Adresse, STOP). SDA/SCL werden nie ausserhalb des I2C-Protokolls getrieben.
- `secrets.yaml` nie committen oder ausgeben. Das Passwort in der GUI wird nicht gespeichert und nicht geloggt.
