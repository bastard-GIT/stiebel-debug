# LWZ 180 I2C-Protokoll (Referenz aus kdarnok/lwz180)

Stand: Abrufdatum 2026-10-25 (WP1, mk1-i2c).

## 0. Quellen und Status

| Quelle | URL | Lizenz |
|---|---|---|
| README.md | https://raw.githubusercontent.com/kdarnok/lwz180/master/README.md | GPL-2.0 (laut Repo-Seite, About-Bereich, LICENSE-Datei vorhanden) |
| lwz180.ino | https://raw.githubusercontent.com/kdarnok/lwz180/master/lwz180.ino | GPL-2.0 |
| Repo-Seite | https://github.com/kdarnok/lwz180 | GPL-2.0 |

**Wichtiger Hinweis zur Herkunft:** Die Inhalte wurden über WebFetch abgerufen. Das Werkzeug lieferte jeweils eine Zusammenfassung durch ein Modell, keinen Rohtext. Wörtliche Zitate sind deshalb nur Ausschnitte aus diesen Zusammenfassungen. Alles unten ist als "Quelle (Fetch)" markiert und ist **nicht byte-genau gegen den Rohtext geprüft**. Vor der Implementierung in `lwz180.py` sollte die README und der Sketch einmal im Original (Browser oder `git clone` in einen leeren Ordner) gelesen werden.

Legende:
- **[Quelle]** = steht laut Fetch-Ergebnis in README oder Sketch.
- **[Quelle, Code]** = steht im Sketch (lwz180.ino), Code-Ausschnitt aus dem Fetch-Ergebnis.
- **[unklar]** = Quellen widersprechen sich oder schweigen. Nicht raten.
- **nicht in der Quelle** = Quelle enthält dazu keine Angabe.

## 1. Busparameter

| Parameter | Wert | Status |
|---|---|---|
| Takt | **13,5 kHz gemessen** (SCL-Periode 74 us, Byteabstand 0,67 ms) | Messung 2026-10-25 (Saleae, 4 MS/s, 41 s, Salae-logic-data/): Die Quelle (README) nennt 31,25 kHz, das stimmt fuer dieses Geraet nicht. |
| Rolle LWZ 180 | I2C-Bus zwischen Gerät und externem Display. Pakete werden als **General-Call-Writes** an Adresse `0x00` gesendet. Mithörer sehen `00` als erstes Frame-Byte. | [Quelle] README, [Quelle, Code] Sketch: `Wire.beginTransmission(0)` |
| Adresse Sketch-Slave | `I2C_SLAVE_ADDR 42` (dezimal, 0x2A), `Wire.begin(42)`, `TWAR = (42 << 1) \| 1` (General Call aktiv) | [Quelle, Code] Sketch. Gehört zur Emulation der Bedieneinheit, nicht zum LWZ-Protokoll. |
| Clock-Stretching, ACK-Verhalten | nicht in der Quelle | - |
| Pull-ups, Pegel | README-Abschnitt "Wiring" vorhanden, Inhalt nicht ausgewertet | nicht in der Quelle (in dieser Recherche) |

## 2. Paketaufbau

Die Quelle beschreibt: 3 Byte Header, Payload, 1 Byte Prüfsumme (CRC-8).

| Typ | Header | Länge gesamt | Aufbau laut Sketch-Auswertung | Quelle |
|---|---|---|---|---|
| Istwerte | `e3 20 10` | 7 Byte | `e3 20 10` · Typ (Index 3) · Wert hi (Index 4) · Wert lo (Index 5) · CRC (Index 6) | [Quelle, Code] Empfang: `data[0]==0xe3`, `data[1]==0x20`, `reg = data[3]`, Wert `(data[4] << 8) + data[5]` |
| Tasten | `e3 30 20` | 7 Byte | `e3 30 20` · `00` · `00` · Tastencode (Index 5) · CRC (Index 6) | [Quelle, Code] Vorlage `{0xe3,0x30,0x20,0x00,0x00,0x00,0x00}` |
| Display | `f7 20` | 28 Byte | siehe Abschnitt 6 | [Quelle] README |

Zum Header-Format: Der Header ist im Sketch nur an den Bytes 0 und 1 geprüft (Istwerte). Die Bedeutung von Byte 2 (`0x10` bzw. `0x20`) und der Bytes 3 und 4 der Tasten-Vorlage ist in der Quelle nicht erläutert. Die Bedeutung der Indizes 0 bis 4 im Tastenpaket ist im Sketch nicht erläutert.

Wertformat Istwerte: vorzeichenbehaftet (Zweierkomplement), Big-Endian, 16 Bit. [Quelle] README: "stored in a big-endian signed (using two's complement) word". Sketch: `int16_t`.

## 3. CRC-8

### 3.1 Parameter

| Parameter | Wert | Status |
|---|---|---|
| Polynom | `0x49` | [Quelle] README: "8-bit CRC using the polynomial 0x49". Sketch: `CRC_POLY 0x49`. |
| Startwert (init) | `0x00` | [Quelle, Code] Sketch: `button_packet[6] = crc8_push_byte(0, code);` |
| Bitreihenfolge (refin) | MSB first, keine Reflexion | [Quelle, Code] Sketch: Schleife prüft `crc & 0x80`, Linksshift. Refin/Refout im Code nicht vorhanden. |
| refout | false (kein Reflex am Ausgang) | [Quelle, Code] Sketch: kein Reflex, Rückgabe direkt |
| xorout | `0x00` (kein Final-XOR) | [Quelle, Code] Sketch: kein Final-XOR im Code. |
| Abdeckung | siehe 3.2 | [Quelle, Code] |

Im Fetch-Ergebnis wird die Bitfolge aus dem Code so beschrieben. Die README selbst nennt init, refin und refout nicht.

### 3.2 Abdeckung

- **Tastenpaket:** Die CRC wird über das **Tastencode-Byte allein** gebildet, Index 5. **Adressbyte: nein. Header: nein.** Der Sketch ruft `crc8_push_byte(0, code)` auf. [Quelle, Code]
- **Istwerte:** Abdeckung **nicht in der Quelle**. Der Sketch prüft die CRC beim Empfang nicht.
- **Displaypaket:** Abdeckung **nicht in der Quelle**.
- Die Fetch-Zusammenfassung der README nennt "Die Prüfsumme des Headers eines Tastenpakets ist `0x00`." Das lässt sich nicht eindeutig deuten (kann sich auf Index 6 der Vorlage beziehen, die dort mit 0 vorbelegt ist). **[unklar]**
- Der Sketch enthält eine Funktion `crc8(byte crc, byte *data, int len)`, die nie aufgerufen wird.

### 3.3 Code-Ausschnitte (Quelle, Code, aus Fetch-Zusammenfassung)

```c
// crc8_push_byte(byte crc, byte data)
crc ^= data;
for (int i = 0; i < 8; i++) {
  if ((crc & 0x80) != 0)
    crc = (byte)((crc << 1) ^ CRC_POLY);   // CRC_POLY = 0x49
  else
    crc <<= 1;
}
return crc;

// send_button(code, count)
button_packet[5] = code;
button_packet[6] = crc8_push_byte(0, code);
```

### 3.4 Referenzfunktion Python (eigene Umsetzung des Sketch-Algorithmus)

```python
def crc8(data: bytes, init: int = 0x00, poly: int = 0x49) -> int:
    """CRC-8 MSB-first, ohne Reflexion, ohne Final-XOR (wie crc8_push_byte im Sketch)."""
    crc = init
    for d in data:
        crc ^= d
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc
```

Diese Funktion ist nur für das Tastenpaket aus dem Sketch belegt. Für Istwerte und Display ist die Abdeckung in der Quelle nicht festgelegt.

### 3.5 Kontrollwerte (eigene Berechnung mit der Funktion oben, **keine Quellenbeispiele**)

CRC über das Code-Byte allein (init 0x00):

| Code | Taste | CRC (eigene Berechnung) |
|---|---|---|
| 0x01 (1) | down (wheel) | 0x49 |
| 0x02 (2) | up (wheel) | 0x92 |
| 0x05 (5) | menu | 0x24 |
| 0x06 (6) | ok | 0xff |
| 0x07 (7) | home | 0xb6 |
| 0x08 (8) | power vent | 0xda |
| 0x09 (9) | menu (long) | 0x93 |
| 0x0a (10) | ok (long) | 0x48 |
| 0x0b (11) | home (long) | 0x01 |
| 0x0c (12) | power vent (long) | 0xb7 |
| 0x0d (13) | cleaning lock | 0xfe |

Vollständiges Paket `e3 30 20 00 00 0c b7` ist eine eigene Konstruktion aus dem Sketch-Algorithmus, nicht aus der Quelle.

## 4. Typtabelle Istwerte

Quelle: README-Tabelle "Actual values" (Fetch-Zusammenfassung). Skalierung: Wert = Rohwert (int16) × Skalierung. Die README gibt Skalierung als `0.1` oder `1` an. Der Sketch rechnet äquivalent `Rohwert / 10` bei Skalierung 10. Vorzeichen: **signed** für alle Typen (README: global big-endian signed).

"bestätigt" bedeutet hier: Name und Skalierung stimmen in README **und** Sketch überein. Im Sketch sind nur vier Typen mit Skalierung genannt (Sketch-Skalierung `10` = Division durch 10).

| Typ | Name (I-/P-Nr.) | Einheit | Skalierung | Vorzeichen | Status |
|---|---|---|---|---|---|
| 0x00 | I13, Taupunkt Abluft | °C | 0.1 | ja | unbestätigt |
| 0x01 | I14, Taupunkt Außenluft | °C | 0.1 | ja | unbestätigt |
| 0x06 | I9, Temperatur Außenluft | °C | 0.1 | ja | **bestätigt** (Sketch: Außentemperatur, Skal. 10) |
| 0x07 | I10, Temperatur Zuluft | °C | 0.1 | ja | unbestätigt |
| 0x08 | I2, Temperatur Abluft | °C | 0.1 | ja | unbestätigt |
| 0x09 | I11, Temperatur Fortluft | °C | 0.1 | ja | unbestätigt |
| 0x0e | I12, rel. Feuchte Außenluft | % | 0.1 | ja | unbestätigt |
| 0x0f | I3, rel. Feuchte Abluft | % | 0.1 | ja | **bestätigt** (Sketch: Abluftfeuchte, Skal. 10) |
| 0x10 | I16, Zuluftvolumenstrom (berechnet) | m³/h | 1 | ja | **bestätigt** (Sketch: berechneter Zuluftvolumenstrom, Skal. 1) |
| 0x11 | I18, Abluftvolumenstrom (berechnet) | m³/h | 1 | ja | unbestätigt |
| 0x14 | I15, Antrieb Zuluftventilator | % | 0.1 | ja | **bestätigt** (Sketch: Leistung Zuluftventilator, Skal. 10) |
| 0x15 | I17, Antrieb Abluftventilator | % | 0.1 | ja | unbestätigt |
| 0x16 | I19, Leistung interner Vorheizer | % | 1 | ja | unbestätigt |
| 0x1e | P6 | m³/h | 1 | ja | unbestätigt |
| 0x1f | P7 | m³/h | 1 | ja | unbestätigt |
| 0x20 | P8 | m³/h | 1 | ja | unbestätigt |
| 0x21 | P9 | m³/h | 1 | ja | unbestätigt |
| 0x4d | P10 | Pa | 1 | ja | unbestätigt |
| 0x50 | I21, Betriebszeit Lüfter | d | 1 | ja | unbestätigt |
| 0x51 | I20, Betriebszeit Lüftungsgerät | d | 1 | ja | unbestätigt |
| 0x53 | P11 | Pa | 1 | ja | unbestätigt |
| 0x54 | P12 | Pa | 1 | ja | unbestätigt |
| 0x55 | P13 | Pa | 1 | ja | unbestätigt |
| 0x5a | I4, Filterstandzeit | h | 1 | ja | unbestätigt |

Anzahl Typen: **24**. Bestätigt: **4**. Unbestätigt: **20**.

Anmerkungen:
- Die README schreibt "suuply air flow" (Tippfehler), gemeint ist Zuluft.
- Die Fetch-Zusammenfassung nennt weitere beobachtete Typ-Bytes ohne Zuordnung (z. B. `0x12`, `0x13`, evtl. Druckwerte). **[unklar]**
- Die Namenszuordnung I-/P-Nummern stammt aus der README. Ob die Zuordnung Typ zu Name zuverlässig ist, ist **nicht am Gerät verifiziert** (laut README/Projektbeschreibung).
- Der Sketch nennt die Register als Hex-Werte, die Gruppenadressen (KNX, `5/7/x`) sind für das I2C-Protokoll ohne Bedeutung.

## 5. Tastencodes

| Code | Taste | Quelle |
|---|---|---|
| 1 | down (wheel), Drehrad ab | README |
| 2 | up (wheel), Drehrad auf | README |
| 5 | menu | README |
| 6 | ok | README |
| 7 | home | README |
| 8 | power vent (kurz) | README |
| 9 | menu (lang) | README |
| 10 | ok (lang) | README |
| 11 | home (lang) | README |
| 12 | power vent (lang) | README |
| 13 | Reinigungssperre: home + ok (lang) | README |

**[unklar] Power Vent:** Der Sketch definiert `BUTTON_POWER_VENT = 0x0c` (= 12). Die README listet 12 als "power vent (long)" und 8 als "power vent" (kurz). Der Sketch sendet also den Langdruck-Code. Ob 8 im Protokoll den kurzen Druck bedeutet, ist in der Quelle nicht geklärt.

Sketch: `BUTTON_DOWN = 0x01`, `BUTTON_UP = 0x02`, `BUTTON_POWER_VENT = 0x0c`. Der Sketch sendet pro Tastendruck das Paket zweimal (`send_button(code, 2)`), mit 50 ms Pause (`WHEEL_DELAY`) zwischen den Wiederholungen.

Die Fetch-Zusammenfassung nennt Codes 1 und 2 als "nur paarweise akzeptiert". Die Quelle belegt dies nicht eindeutig. **[unklar]**

## 6. Display-Paket `f7 20` (28 Byte)

Quelle: README-Tabelle "Display updates" (Fetch-Zusammenfassung) und Sketch-Auswertung. **Die Indexbasis ist nicht eindeutig.** Die Tabelle nennt Byte-Indizes 3 bis 23, der Sketch liest `data[8]`, `data[15]`, `data[19]`, `data[20]`, `data[26]`. Ob die Tabelle den Index innerhalb des 28-Byte-Pakets (inkl. Header `f7 20`) oder ohne Header meint, ist in der Quelle nicht festgelegt. **[unklar]**

### 6.1 Segmentbelegung laut README (Byte, Bit, Element)

| Byte | Bit | Anzeigeelement |
|---|---|---|
| 3 | – | Ziffer Zeile 1, Stelle 1 (`d`) |
| 4 | – | Ziffer Zeile 1, Stelle 2 (`e`) |
| 5 | – | Ziffer Zeile 1, Stelle 3 (`f`) |
| 6 | – | Ziffer Zeile 2, Stelle 1 (`p`) |
| 7 | – | Ziffer Zeile 2, Stelle 2 (`q`) |
| 8 | – | Ziffer Zeile 2, Stelle 3 (`s`) |
| 9 | – | Ziffer Zeile 2, Stelle 4 (`t`) |
| 12 | 0 | Uhr (`b`) |
| 12 | 2 | Leistung (`a`) |
| 12 | 4 | Ziffer 7 (`o`) |
| 12 | 6 | Ziffer 6 (`n`) |
| 13 | 0 | Ziffer 5 (`m`) |
| 13 | 2 | Ziffer 4 (`l`) |
| 13 | 4 | Ziffer 3 (`k`) |
| 13 | 6 | Ziffer 2 (`i`) |
| 15 | 2 | Schloss (`c`, Sperre) |
| 16 | 4 | Lüfter (`v`) |
| 17 | 2 | Dezimalpunkt erste Ziffer (`g`) |
| 18 | 2 | Einheit m³/h (`y`) |
| 18 | 6 | Einheit % (`x`) |
| 20 | 0 | Filter (`ab`) |
| 20 | 6 | Frost (`aa`) |
| 21 | 2 | Bypass (`z`) |
| 23 | 0 | LED Power Vent |

### 6.2 Sketch-Auswertung (Fetch-Ausschnitt)

- Power Vent: `data[26] & 0x01`. **Widerspruch zur Tabelle** (dort Byte 23, Bit 0). **[unklar]**
- Zeitschaltung: `data[15] & 0x01`. Tabelle nennt Byte 15 Bit 2 (Schloss). **[unklar]**
- Stufe: aus `data[8]`, nur auswertbar, wenn `(data[20] & 0x04)` oder `(data[19] & 0x10)` gesetzt ist. Sieben-Segment-Codes: `0x06` = 1, `0x5b` = 2, `0x4f` = 3, `0x3f` = 0. Sonst Stufe 0.

### 6.3 Zeichentabelle Ziffern (Fetch-Ergebnis, README)

| Byte | Zeichen | | Byte | Zeichen |
|---|---|---|---|---|
| 0x00 | (leer) | | 0x5c | o |
| 0x06 | 1 | | 0x5e | d |
| 0x07 | 7 | | 0x66 | 4 |
| 0x1c | u | | 0x6d | 5 |
| 0x30 | I | | 0x6f | 9 |
| 0x39 | C | | 0x73 | P |
| 0x3f | 0 | | 0x77 | A |
| 0x40 | - | | 0x78 | t |
| 0x4f | 3 | | 0x7d | 6 |
| 0x50 | r | | 0x7f | 8 |
| 0x5b | 2 | | | |

Die Zeichentabelle enthält die Codes für die Ziffern 0 bis 9 vollständig. Die Quelle begründet die Codes nicht über eine Segment-Logik.

### 6.4 CRC und Beispiel

- CRC des Displaypakets: **nicht in der Quelle**.
- Beispielpakete mit vollständigen Hex-Bytes: **nicht in der Quelle** (nur die Header-Präfixe `e3 20 10`, `e3 30 20`, `f7 20`).

## 7. Beispielpakete

Die Quelle enthält **keine** vollständigen Beispielpakete mit Hex-Bytes und CRC. Die Liste `examples` in `lwz180_types.json` ist daher leer. Die Kontrollwerte in 3.5 sind eigene Berechnungen und keine Quellenbeispiele.

## 8. Offene und unklare Punkte

1. **Quelltext nur per Fetch-Zusammenfassung.** Vor Implementierung Rohtext prüfen.
2. **CRC-Abdeckung Istwerte und Display:** nicht in der Quelle. Im Sketch nur Tastenpaket belegt.
3. **Header-Bedeutung** Byte 2 (`0x10`, `0x20`) und Indizes 0 bis 4 im Tastenpaket: nicht erläutert.
4. **Power Vent:** Sketch `0x0c` (lang), README kurz = 8. Zuordnung unklar.
5. **Codes 1 und 2:** "nur paarweise akzeptiert" (Fetch) ist nicht belegt.
6. **Display-Indexbasis** und Widerspruch Power-Vent-Bit (Tabelle Byte 23, Sketch `data[26]`), Zeitschaltung (Tabelle Byte 15 Bit 2, Sketch `data[15]` Bit 0).
7. **Takt:** gemessen 13,5 kHz (2026-10-25), nicht 31,25 kHz wie in der Quelle. CRC (Polynom 0x49, init 0, ueber Byte 1 bis vorletztes) fuer Istwert (167/167) und Display (12/12) am Geraet bestaetigt.
8. **Typ 0x12, 0x13:** unbekannt.
9. **Keine Verifikation am Gerät** laut Projektdokumentation.
10. **Header-Prüfsumme Tasten "0x00"** (Fetch-Aussage): unklar, siehe 3.2.

## 9. Verwendung in mk1-i2c

- `lwz180_types.json` übernimmt die Werte aus diesem Dokument mit Statuskennzeichnung.
- `lwz180.py` implementiert `crc8` nach 3.4. Für Istwerte und Display ist die Prüfung nicht durch die Quelle gedeckt, deshalb dort `crc_ok` nur als Information führen (kein Verwerfen), bis Mitschnitte gegen Logic Analyzer geprüft sind.

## Nachtrag Ueberwacher (2026-10-25, Rohtext per curl geprueft)

- Der Sketch empfaengt ueber Wire: das Adressbyte 0x00 fehlt in `data[]`. Pakete sind 7 bzw. 28 Byte ohne Adressbyte. Mitgeschnittene Frames des Sniffers beginnen mit 0x00.
- CRC: init 0, poly 0x49, MSB-first. Tastenpaket e3 30 20 00 00 code: crc8(e3 30 20) = 0x00, daher crc8 ueber alle sechs Bytes == crc8([code]). Rechnerisch bestaetigt (crc8([5]) = 0x24). CRC deckt Header+Payload (Index 0-5). Ein fuehrendes 0x00 aendert die CRC bei init 0 nicht.
- Istwertpaket: Index 3 = Typ (`reg = data[3]`), Index 4-5 = Wert BE. Der Sketch prueft die CRC beim Empfang nicht.
- Display: README-Byteindizes sind 0-basiert im Paket ohne Adressbyte (Index 8 = Ziffer s = Lueftungsstufe, passt zu `data[8]` im Sketch). Widerspruch Power-Vent-LED (README 23 Bit 0, Sketch data[26] Bit 0) bleibt offen.
- Abschnitte oben, die "Abdeckung nicht in der Quelle" sagen, sind durch diesen Nachtrag praezisiert.

## Nachtrag Messung 2026-10-25, Offset +3 (langer Mitschnitt digital.csv, 1168 s, 6056 Frames, 97 Display-Zustaende, Menue P1..P85 und I1..I79)

Indizes 0-basiert im Display-Paket ohne Adressbyte (`seg[0]` = f7, `seg[1]` = 20). **Die README-Tabelle 6.1 ist um +3 Byte verschoben**; massgeblich ist die folgende Belegung.

- Ziffern: grosse Ziffern (Zeile 1, 3 Stellen) = `seg[6]`, `seg[7]`, `seg[8]`. Kleine Ziffern (Zeile 2, 4 Stellen) = `seg[9..12]`. Standard-7-Segment, Bit 0 = a ... Bit 6 = g. Beispiele: "P 1" / " 260" (26.0 Grad C), "P81" / "1628" (16:28), Standby "  1" (Stufe 1) mit Luefter. `seg[8]` = Lueftungsstufe, passt zum Sketch `data[8]`. Die fruehere Annahme (Zeile 1 = 3..5, Zeile 2 = 6..9) ist falsch.
- `seg[2..5]` sind in allen Zustaenden konstant `0b 20 6d 00`: keine Ziffern.
- Stufenregel des Sketches (Stufe aus `data[8]`, wenn `data[20] & 0x04` oder `data[19] & 0x10`) passt: Standby Stufe 1 hat `seg[19]` Bit 4 (Luefter).

| Byte/Bit | Element | Status |
|---|---|---|
| 15/0 | Uhr | bestaetigt (P81 Uhrzeit, Pro) |
| 19/4 | Luefter | bestaetigt (Standby, P28) |
| 21/6 | Prozent | bestaetigt (I3 61.7) |
| 22/0 | Grad C | bestaetigt (P1 26.0, I2 20.6) |
| 22/2 | Dezimalpunkt Zeile 2 (Stelle 3/4) | bestaetigt |
| 22/4 | Doppelpunkt Zeile 2 (Stelle 2/3) | bestaetigt (P81 16:28) |
| 23/0 | Filter | bestaetigt (P4, I4 1753), nicht Power Vent |
| 24/2 | Bypass | bestaetigt (P3) |
| 15/2 | Leistung | nur README+3 |
| 15/4, 15/6, 16/0, 16/2, 16/4, 16/6 | Ziffernleiste 7, 6, 5, 4, 3, 2 | nur README+3 (P80 zeigt 15/4) |
| 18/2 | Schloss | nur README+3 |
| 20/2 | Dezimalpunkt erste grosse Ziffer | nur README+3 |
| 21/2 | m3/h | nur README+3 |
| 23/6 | Frost | nur README+3 |
| 26/0 | LED Power Vent | Sketch |

Nachtrag 2026-11-11: `esphome-lwz-180` (Commit 018ef9d, Mk1-Firmware 2026.11.10) wertet 23/0, 23/6, 24/2, 15/0, 18/2 und 26/0 als binary_sensor aus, nur im Grundzustand, aus erst nach 60 s ohne Bit. 23/0 im Grundzustand = Filterwechsel-Meldung am Geraet bestaetigt (Display zeigte die Meldung, Sensor `on`, Filterlaufzeit 5a = 2162 h; Schwelle vermutlich 2160 h = 90 Tage). 23/6 Frost und 18/2 Schloss weiter unbestaetigt.

- Bekannt unklar: 13/0, 13/2, 13/4 fast immer an (beim Zeit-Editieren in P81 statt dessen 13/3, 13/5), 14/0, 14/2, 14/4, 14/6 in allen Menueanzeigen an, im Standby aus. Auf den Fotos nichts sichtbar, vermutlich nicht LCD (Tasten-LED/Blinksteuerung?).
- Zusaetzliche Zeichen (Tabelle 6.3): `0x54` n, `0x38` L, `0x79` E, `0x71` F, `0x76` H, `0x3e` U, `0x74` h, `0x58` c, `0x1e` J (`0x5e` d, `0x77` A, `0x1c` u bereits enthalten). Gesehene Anzeigen: "Cod", "Pro", "Auto", "0FF", "----", "0n".
- Mitschnitt: 6056 Frames, `err_bits` 0, 15 abgeschnittene Pakete (`err_nostop`), alle vollstaendigen Istwert- (5328) und Display-Pakete (455) mit CRC ok. Kanalzuordnung SCL = "Channel 4", SDA = Spalte "VDD". `i2c.txt` gehoert zum frueheren kurzen Mitschnitt.
