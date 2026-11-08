# I2C-Sniffer mit ESPHome: Übergabedokumentation

> **Hinweis 2026-10-30:** Der passive Software-Sniffer wurde verworfen und aus `mk1-i2c` entfernt. Es gilt nur noch der Hardware-I2C-Slave (Adresse 42, General Call, ACK, kein Clock-Stretching). Grund: Slave-Test 159 Frames in 30 s, CRC 158/158 ok; Sniffer im selben Test rund 96 % CRC ok (ISR-Latenz). Dieses Dokument ist nur noch historisch. Harte Regel bleibt: SDA/SCL nie ausserhalb des I2C-Protokolls treiben.

Stand: 2026-10-24

## 1. Ziel und Kontext

Ein ESP32-S3 soll die zyklisch gesendeten I2C-Daten zwischen einem Gerät und seinem externen Display passiv mitlesen und in Home Assistant anzeigen.

- Das Gerät sendet alle Daten zyklisch an das Display. Es gibt keine Abfragen.
- Master und Slave dürfen nicht gestört werden: kein ACK, kein Treiben von SDA oder SCL.
- Die Firmware läuft mit ESPHome, damit Updates und Anpassungen per OTA möglich sind.
- Schritt 1: passiver I2C-Empfang. Schritt 2: Dekodierung der Werte. Schritt 3: Anbindung an Home Assistant.
- Das Gerät ist vermutlich eine Stiebel Eltron LWZ 180. Das ist noch nicht bestätigt (Frage an den Nutzer offen).

## 2. Hardware

Die Hardware steht. Die Kommunikation ist mit einem Logic Analyzer auf beiden Seiten des Pegelwandlers sichtbar.

| Baustein | Zweck | Stand |
| --- | --- | --- |
| ESP32-S3 | Empfänger, ESPHome-Firmware | vorhanden |
| Isolierter I2C-Pegelwandler | galvanische Trennung, Pegelanpassung | vorhanden, Signale auf beiden Seiten geprüft |
| Logic Analyzer | Vergleich, Referenzmitschnitt | vorhanden |

Pin-Nummern stehen nicht fest. Die Beispiele verwenden GPIO8 (SDA) und GPIO9 (SCL).

## 3. Technischer Ansatz

- Der Hardware-I2C-Controller des ESP32 scheidet aus. Im Slave-Modus würde er Adressen quittieren und SDA treiben. Das stört Master und Slave.
- Stattdessen: Software-Decoder. SDA und SCL sind reine Eingänge ohne Pullup, ausgewertet per GPIO-Interrupt.
- START: SDA fällt bei SCL high. STOP: SDA steigt bei SCL high.
- Datenbit: SDA bei steigender SCL-Flanke abtasten. Das 9. Bit jedes Bytes ist ACK/NACK (SDA low = ACK).
- Die ISR im IRAM schreibt nur Events in einen Ringpuffer. `loop()` setzt daraus Frames zusammen.
- Grenzen: Die ISR-Latenz begrenzte den Software-Weg (siehe Hinweis oben). Der LWZ-180-Bus laeuft mit rund 13,5 kHz.
- Pegelwandler prüfen: Er muss Signale unverändert durchreichen (Clock-Stretching).

## 4. Vorhandener Code (`/home/claude/i2c_sniffer/`)

**Status: nicht kompiliert, nicht getestet.** Erster Build kann kleine API-Fehler zeigen, je nach ESPHome-Version.

| Datei | Inhalt |
| --- | --- |
| `components/i2c_sniffer/__init__.py` | Config-Schema (`sda_pin`, `scl_pin`, `frame`, `on_frame`), Code-Generierung |
| `components/i2c_sniffer/i2c_sniffer.h` | `SnifferStore` (ISR-Zustand, Ringpuffer mit 1024 Events), `I2CSniffer`, `FrameTrigger` |
| `components/i2c_sniffer/i2c_sniffer.cpp` | ISRs `scl_isr` und `sda_isr`, `loop()` baut Frames, Überlauf-Warnung |
| `sniffer.yaml` | Beispiel: ESP32-S3, ESP-IDF, API, OTA, `external_components` lokal |

Verhalten:

- Bei STOP wird ein Frame als Text-Sensor veröffentlicht, z. B. `S 50+ 12+ 34- P` (`+` = ACK, `-` = NACK).
- `on_frame` löst zusätzlich mit `bytes` (`std::vector<uint8_t>`) aus. `bytes[0]` ist `Adresse<<1 | R/W`.
- Ringpuffer-Überlauf wird als Warnung geloggt (`Ring overflow`).

Wichtig: In der YAML keinen `i2c:`-Block anlegen. Der Hardware-Controller würde die Leitungen treiben.

## 5. Referenzprojekte

| Projekt | Nutzen |
| --- | --- |
| [TheAndi/esphome_i2c_sniffer](https://github.com/TheAndi/esphome_i2c_sniffer) | Fertiger passiver ESPHome-Sniffer auf beliebigen GPIOs. Text-Sensor je Transaktion mit Zeitstempel (`[40563ms] [0x54]W+(10)+ \| [0x54]R+(00)+(00)-`), Repeated Start zusammengefasst, numerische Sensoren für letzte Adresse und letztes Datenbyte, `on_address`-Callback. Einbindung als Git-`external_components`. |
| [kdarnok/lwz180](https://github.com/kdarnok/lwz180) | Reverse Engineering des internen I2C-Busses einer Stiebel Eltron LWZ 180 (GPL-2.0). `README.md` beschreibt das Protokoll, `lwz180.ino` ist ein Arduino-Sketch. |

Hinweise:

- Der Quellcode von TheAndi wurde nicht gelesen. GitHub blockierte den Abruf der Verzeichnisseite. Unklar sind Implementierung (Interrupt oder Polling), maximale Busgeschwindigkeit und Pufferung. Prüfen, bevor man darauf aufbaut.
- Empfehlung: TheAndis Projekt als Basis testen und die Ausgabe mit dem Logic Analyzer vergleichen. Die eigene Komponente bleibt als zusätzliches Beispiel.
- Von kdarnok wurde nur die Projektbeschreibung gelesen. `README.md` und `lwz180.ino` sind noch nicht im Detail ausgewertet.

## 6. Protokoll LWZ 180 (laut kdarnok/lwz180)

Alles aus dem Referenzprojekt, nicht am eigenen Gerät verifiziert.

- Takt: 31,25 kHz.
- Alle Pakete sind General-Call-Writes an Adresse `0x00`. In einem mitgelesenen Frame steht also zuerst das Byte `00`.
- Paketaufbau: 3 Byte Header, Payload, 1 Byte Prüfsumme (8-Bit-CRC, Polynom `0x49`). Startwert und Bitreihenfolge der CRC sind nicht erfasst, sie stehen in der README oder im Sketch.

| Typ | Header | Länge | Inhalt |
| --- | --- | --- | --- |
| Istwerte | `e3 20 10` | 7 Byte | 1 Byte Typ, 2 Byte Wert (Big-Endian, vorzeichenbehaftet), Skalierung 0,1 oder 1 je Typ |
| Tasten | `e3 30 20` | 7 Byte | Tastencodes 1 bis 13 (Drehrad, Menü, OK, Home, Powerlüftung, Reinigungssperre) |
| Display | `f7 20` | 28 Byte | Segmentbits der Anzeige |

Bekannte Istwerte: Außen-, Zu-, Ab- und Fortlufttemperatur, Taupunkte, relative Feuchte (außen und Abluft), berechnete Zu- und Abluftvolumenströme (m³/h), Lüfterleistung, Vorheizerleistung, P6 bis P13 (Durchfluss und Druck), Betriebszeiten, Filterstandzeit. Mehrere Typcodes sind unbestätigt (`0x12`, `0x13` evtl. Druckwerte).

Sendezeitpunkt: Einige Pakettypen erscheinen beim Start und bei Wertänderungen an der Bedieneinheit, ein fester Zyklus ist nicht dokumentiert.

## 7. Offene Punkte und nächste Schritte

1. Beim Nutzer klären, ob das Gerät eine LWZ 180 (oder baugleich) ist.
2. Sniffer auf dem ESP32-S3 bauen und starten (`esphome run sniffer.yaml`), Pins anpassen. Ausgabe mit dem Logic Analyzer vergleichen, Verluste bei der echten Busfrequenz prüfen.
3. Alternativ TheAndis Komponente einbinden und gegen die eigene vergleichen.
4. README und `lwz180.ino` von kdarnok lesen: CRC-Startwert, Typ-Tabelle, Skalierungen.
5. In `on_frame` Header und CRC prüfen, Typ-Byte einem Wert zuordnen und per `template`-Sensor an Home Assistant liefern.
6. Mitschnitt des Gerätes sichern (Text-Sensor-Zeilen oder Logic-Analyzer-Export) und gegen die Typ-Tabelle prüfen.
