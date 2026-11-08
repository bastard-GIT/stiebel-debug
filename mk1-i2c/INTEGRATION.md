# INTEGRATION: I2C-Slave in die Produktiv-Firmware uebernehmen

**Stand 2026-10-30: umgesetzt als ESPHome-Paket** `esp32-s3-technik-mk1/ha-stiebel-control/lwz_i2c.yaml` (eingebunden in `esp32-s3-technik-mk1.yaml` unter `packages: lwz_i2c`, Pins per Substitution `i2c_sda_gpio`, `i2c_scl_gpio`, `i2c_slave_addr`, `i2c_internal_pullup`). Abweichend von den Schritten unten: keine Aenderung der Includes/on_boot/interval in `common.yaml` (stehen im Paket), `ota` in `common.yaml` hat `id: ota_esphome` und wird vom Paket per `!extend` erweitert, `stiebel-i2c/availability` nicht retained (`-DI2CS_AVAIL_RETAIN=false`). Header nach Aenderungen in `mk1-i2c/firmware` dorthin kopieren. Binary: `backup/mk1-produktiv-lwzi2c-2026-10-30/`.

Dieses Dokument beschreibt, wie der I2C-Slave (Glue `i2c_lwz.h`) aus `mk1-i2c` in `C:\temp\stiebel-Remote\esp32-s3-technik-mk1` uebernommen wird. Es ist eine Anleitung. Es wurde **nichts** in `esp32-s3-technik-mk1` geaendert. Die Uebernahme erfolgt erst nach Freigabe durch den Nutzer.

Kernpunkt: Die Produktiv-Firmware nutzt **GPIO18 als CAN-TX (TWAI)**. Die Test-Firmware haelt GPIO18 ueber `firmware\can_idle.h` fest High. `can_idle.h` und seine Eintraege in `mk1-i2c.yaml` (on_boot priority 1000, interval 1s) **nie** in die Produktiv-Firmware uebernehmen (Abschnitt 4). Der Sniffer selbst (`i2c_sniffer.h`) fasst GPIO18 nicht an.

## 0. Vorbedingungen

1. Produktiv-Stand sichern, bevor etwas kopiert wird. Die neueste Sicherung ist `C:\temp\stiebel-Remote\backup\mk1-produktiv-unixtime-router-2026-10-21\` (nur Binary und `common.yaml`). Den Quellstand von `esp32-s3-technik-mk1` vollstaendig kopieren (z. B. nach `backup\mk1-produktiv-<datum>-vor-i2c\`). Die Dateien `*.bak_before_*` in `ha-stiebel-control` sind alte Staende, keine Sicherung.
2. Abschnitt 4 lesen: `can_idle.h` bleibt im Test-Build.
3. Hinweis (2026-10-30): Der passive Software-Sniffer ist entfallen (nur noch Hardware-Slave, Port 0, Treiber V2). Der Slave belegt den I2C-Port 0 und braucht `CONFIG_I2C_ENABLE_SLAVE_DRIVER_VERSION_2=y` (siehe `firmware\board_esp32s3_espidf.yaml`). Das in der Produktiv-Firmware pruefen.

## 1. Dateien kopieren

Ziel ist `C:\temp\stiebel-Remote\esp32-s3-technik-mk1\ha-stiebel-control\`.

| Datei | Aktion | Begruendung |
|---|---|---|
| `i2c_frame.h` | kopieren (neu) | Frame, ChangeFilter, Namespace `i2cs` |
| `i2c_slave_rx.h` | kopieren (neu) | Hardware-Slave (Treiber V2) |
| `i2c_scan.h` | kopieren (neu, optional) | On-Demand-Bus-Scan (`cmd/scan`), nicht am Geraet getestet |
| `i2c_lwz.h` | kopieren (neu) | Glue, globale Funktionen `i2cs_setup`, `i2cs_loop`, `i2cs_status` |
| `ring_buffer.h` | nicht kopieren | in Produktiv vorhanden, identisch mit `mk1-i2c\firmware\ring_buffer.h` |
| `unix_time.h` | nicht kopieren | in Produktiv vorhanden, identisch mit `mk1-i2c\firmware\unix_time.h` |
| `debug_json.h` | **nicht kopieren** | Produktiv hat eine **neuere** Version (u. a. `parse_write`, `switch` in `format_read_result`). Die Kopie in `mk1-i2c` ist aelter und wuerde Produktiv-Code brechen. |
| `board_esp32s3_espidf.yaml`, `network_ethernet_w5500.yaml` | nicht kopieren | in Produktiv vorhanden, identisch |
| `i2c_lwz.yaml` | nicht kopieren, Inhalt uebernehmen | Paket aus `mk1-i2c`, siehe Abschnitte 2 bis 5 (Pfade anpassen) |

Pruefung der Abhaengigkeiten: `i2c_lwz.h` nutzt `dj_find_value`, `dj_get_bool`, `dj_read_token` (aus `debug_json.h`) und `mkdbg::unix_ms()`, `mkdbg::ntp_synced()` (aus `unix_time.h`). Diese Funktionen gibt es in der Produktiv-Version. Die Includes in `i2c_lwz.h` sind relativ (`"debug_json.h"`), sie finden die Dateien im selben Ordner.

Namenskollisionen: In `ha-stiebel-control` (ohne `.bak`) gibt es keine Namen `i2cs`, `ChangeFilter`, `Frame`, `i2c_`. Der Namespace `mkdbg` der Produktiv-Dateien wird nicht neu definiert. Vor dem Kopieren trotzdem erneut mit `grep` pruefen.

## 2. Includes in `common.yaml` ergaenzen

Datei: `C:\temp\stiebel-Remote\esp32-s3-technik-mk1\ha-stiebel-control\common.yaml`, Block `esphome: includes:` (ab Zeile 35). Nach der Zeile `- ha-stiebel-control/unix_time.h` einfuegen:

```yaml
    - ha-stiebel-control/i2c_frame.h
    - ha-stiebel-control/i2c_slave_rx.h
    - ha-stiebel-control/i2c_scan.h
    - ha-stiebel-control/i2c_lwz.h
```

Reihenfolge: `ring_buffer.h`, `debug_json.h`, `unix_time.h` stehen schon vor diesen Zeilen. `i2c_lwz.h` braucht sie und die drei Header davor.

## 3. on_boot-Lambda

Die Produktiv-Firmware hat bereits einen `esphome: on_boot:` (priority 600, `initBoostStatePrefs`). Den I2C-Slave **als zweiten Eintrag** anhaengen, nicht den bestehenden Block ersetzen.

```yaml
esphome:
  on_boot:
    - priority: 600        # bestehender Eintrag, unveraendert
      then: ...
    - priority: -100       # nach MQTT-Setup
      then:
        - lambda: |-
            i2cs_setup(${i2c_sda_gpio}, ${i2c_scl_gpio}, -1, ${i2c_internal_pullup}, ${i2c_slave_addr});
```

Hinweis zum Parameter `txd_gpio`: Siehe Abschnitt 4. `-1` ist zulaessig: dann wird GPIO18 nicht angefasst (Glue seit 2026-10-25 angepasst).

Achtung: In `mk1-i2c\firmware\i2c_lwz.yaml` steht `${can_txd_gpio}` statt `-1`. Das ist nur fuer den Test-Build gedacht. In der Produktiv-Uebernahme nicht `18` uebergeben.

## 4. GPIO18 (CAN-TX)

- `i2c_lwz.h` konfiguriert GPIO18 nicht (seit 2026-10-25). `txd_gpio` dient nur der Pruefung, dass SDA/SCL nicht auf dem CAN-TX-Pin liegen. `-1` ist zulaessig und schaltet die Pruefung ab.
- `firmware\can_idle.h` (`can_idle_setup`, `can_idle_check`) setzt GPIO18 als Ausgang High. Das ist nur fuer den Test-Build ohne CAN-Treiber gedacht. In der Produktiv-Firmware wuerde es den TWAI-TX-Pin uebersteuern. **Nicht kopieren, nicht einbinden.**
- Fuer die Produktiv-Uebernahme `i2cs_setup(..., -1, ...)` oder `18` uebergeben. Beides laesst GPIO18 unveraendert. Danach mit `esphome compile` pruefen.

## 5. Substitutionen

Datei: `C:\temp\stiebel-Remote\esp32-s3-technik-mk1\esp32-s3-technik-mk1.yaml`, Block `substitutions:` (Zeilen 17-21 mit `device_model`, `can_tx_pin`, `can_rx_pin`). Ergaenzen:

```yaml
  i2c_sda_gpio: "21"
  i2c_scl_gpio: "17"
  i2c_internal_pullup: "true"
  i2c_slave_addr: "42"
```

Die Werte muessen **Zahlen** sein, weil sie in der Lambda-Zeile von `i2cs_setup` stehen. `GPIO21` (mit Praefix) geht nicht.

`can_tx_pin` und `can_rx_pin` bleiben unveraendert (`GPIO18`, `GPIO16`).

## 6. Interval-Eintraege

Datei: `common.yaml`, Block `interval:` (ab Zeile 331). Zwei Eintraege anhaengen:

```yaml
  - interval: 20ms
    then:
      - lambda: 'i2cs_loop();'

  - interval: 5s
    then:
      - lambda: 'i2cs_status();'
```

Die Produktiv-Firmware hat bereits `50ms` (`dbgrx_flush`) und `5s` (`dbgrx_publish_status`). Die neuen Eintraege stehen daneben, sie ersetzen nichts.

## 7. MQTT: Praefix und Availability

- **Keine Uebernahme** der MQTT-Zeilen aus `mk1-i2c.yaml`: `birth_message` und `will_message` auf `stiebel-i2c/availability`, `log_topic:`, `shutdown_message:` werden **nicht** in die Produktiv-Firmware uebernommen. Produktiv nutzt `heatingpump/status` fuer Birth und Will. `log_topic:` und `shutdown_message:` leer zu setzen aendert das Verhalten der Produktiv-Firmware.
- `stiebel-i2c/availability` bleibt als eigenes, retained Topic bestehen. Es kollidiert nicht mit `heatingpump/status` und nicht mit dem Praefix `stiebel-debug/` (CAN-Debug in Produktiv).
- Die Abos `stiebel-i2c/cmd/sniff` und `stiebel-i2c/cmd/scan` laufen ueber `global_mqtt_client->subscribe()` in `i2cs_setup`. Produktiv hat `on_message` fuer `stiebel-debug/cmd/sniff` und `heatingpump/reconnect` (`common.yaml`, Zeile 158). Beide Mechanismen koexistieren. Nicht verifiziert: ob ESPHome Abos, die im on_boot-Lambda gesetzt werden, nach einem Reconnect erneut angemeldet werden. Am Geraet pruefen.
- Topics: `stiebel-i2c/rx`, `stiebel-i2c/status`, `stiebel-i2c/scan/result`, `stiebel-i2c/availability`, `stiebel-i2c/cmd/sniff`, `stiebel-i2c/cmd/scan`.

## 8. GPIO21 und WS2812

- In der Produktiv-Firmware gibt es kein `light:` und kein `i2c:` (geprueft mit `grep`). Das muss so bleiben.
- GPIO21 ist der Datenpin der WS2812-LED. Auf GPIO21 darf nie ein `light:`-Eintrag stehen. Sonst treibt die LED den SDA-Pin.
- Vor und nach der Uebernahme pruefen: `grep -rnE '^\s*(i2c|light):' C:\temp\stiebel-Remote\esp32-s3-technik-mk1\ha-stiebel-control` darf nichts ausser Kommentaren liefern.

## 9. Abnahme

Nach dem Flashen der integrierten Firmware beobachten:

- `stiebel-i2c/status`: `slave_frames` steigt, `slave_overflow` und `slave_errors` bleiben 0.
- `stiebel-debug/status` (CAN-Debug in Produktiv): `overflow`, `twai_err`, `bus_off`. Diese Werte duerfen sich gegenueber dem Stand ohne I2C-Slave nicht verschlechtern.
- Werte in Home Assistant (WP-Werte) muessen wie vorher erscheinen.
- Der Slave ist mit dem CAN-Verkehr gleichzeitig aktiv. Ob I2C-Interrupt und TWAI-Interrupt sich gegenseitig stoeren, ist nicht geprueft.
- Der Bus-Scan (`cmd/scan`) macht den ESP kurz zum I2C-Master und pausiert den Slave. In der Produktiv-Firmware nur nach Freigabe aufrufen, nicht am Geraet getestet.

## 10. Rueckfall (Produktiv-Stand)

Wenn die Produktiv-Firmware nach der Uebernahme Probleme macht (CAN-Fehler, fehlende WP-Werte, Abstuerze):

1. Die Includes (Abschnitt 2), den zweiten `on_boot`-Eintrag (Abschnitt 3), die Substitutionen (Abschnitt 5) und die zwei Intervals (Abschnitt 6) aus dem Quellstand entfernen. Einfacher: den vor der Uebernahme gesicherten Quellstand zurueckkopieren.
2. Produktiv neu bauen und hochladen:

```
cd C:\temp\stiebel-Remote\esp32-s3-technik-mk1
C:\temp\stiebel-Remote\.venv\Scripts\esphome.exe compile esp32-s3-technik-mk1.yaml
C:\temp\stiebel-Remote\.venv\Scripts\esphome.exe upload esp32-s3-technik-mk1.yaml --device <ip-mk1>
```

3. Pruefen, dass die WP-Werte in Home Assistant wieder erscheinen.

Ein Upload des Binaers aus `backup\` ist im Projekt nicht als Befehl dokumentiert. Der Weg ueber den Quellstand ist der dokumentierte.

## Offene Punkte vor der Uebernahme

- Glue-Aenderung fuer `txd_gpio = -1` (Abschnitt 4): umgesetzt, nicht auf Hardware getestet.
- Ob ESPHome die im on_boot gesetzten MQTT-Abos nach einem Reconnect erneut anmeldet (Abschnitt 7).
- Zusammenspiel von I2C-Slave-Interrupt und TWAI im Produktiv-Betrieb (Abschnitt 9).
- Das Produktiv-Quellstand-Backup (Abschnitt 0) muss vor dem ersten Schritt vorhanden sein.
