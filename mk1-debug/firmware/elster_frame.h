// elster_frame.h - Elster-Frame-Format fuer mk1-debug (Header-only).
// Namespace mkdbg, C++11, nur <stddef.h>/<stdint.h>, kein ESPHome-Include.
// Alle Funktionen static inline, MEMBERS static const: jede Translation Unit
// erhaelt eigene Kopien, daher keine ODR-Verletzung bei mehrfachem Include.
// MEMBERS-Block 1:1 aus can_members von ElsterTable.json erzeugt (Generator).
#ifndef MKDBG_ELSTER_FRAME_H
#define MKDBG_ELSTER_FRAME_H

#include <stddef.h>
#include <stdint.h>

namespace mkdbg {

struct Member {
  const char *name;
  uint16_t can_id;
  uint8_t read_id[2];
  uint8_t write_id[2];
};

static const Member MEMBERS[] = {
  {"KESSEL", 0x180, {0x31, 0x00}, {0x30, 0x00}},
  {"SPEICHER", 0x201, {0x41, 0x01}, {0x40, 0x01}},
  {"ATEZ", 0x280, {0x51, 0x00}, {0x50, 0x00}},
  {"BEDIENMODUL_1", 0x300, {0x61, 0x00}, {0x60, 0x00}},
  {"BEDIENMODUL_2", 0x301, {0x61, 0x01}, {0x60, 0x01}},
  {"BEDIENMODUL_3", 0x302, {0x61, 0x02}, {0x60, 0x02}},
  {"BEDIENMODUL_4", 0x303, {0x61, 0x03}, {0x60, 0x03}},
  {"RAUMFERNFUEHLER", 0x400, {0x00, 0x00}, {0x00, 0x00}},
  {"MANAGER", 0x480, {0x91, 0x00}, {0x90, 0x00}},
  {"WPUMPE", 0x514, {0xa1, 0x14}, {0xa0, 0x14}},
  {"HEIZMODUL", 0x500, {0xa1, 0x00}, {0xa0, 0x00}},
  {"BUSKOPPLER", 0x580, {0xb1, 0x00}, {0xb0, 0x00}},
  {"MISCHERMODUL_1", 0x600, {0xc1, 0x00}, {0xc0, 0x00}},
  {"MISCHERMODUL_2", 0x601, {0xc1, 0x01}, {0xc0, 0x01}},
  {"MISCHERMODUL_3", 0x602, {0xc1, 0x02}, {0xc0, 0x02}},
  {"MISCHERMODUL_4", 0x603, {0xc1, 0x03}, {0xc0, 0x03}},
  {"ESPCLIENT", 0x680, {0xd1, 0x00}, {0xd0, 0x00}},
  {"FREMDGERAET", 0x700, {0xe1, 0x00}, {0xe0, 0x00}},
  {"DCF_MODUL", 0x780, {0xf1, 0x00}, {0xf0, 0x00}},
  {"FES", 0x100, {0x21, 0x00}, {0x20, 0x00}},
  {"OTHER", 0x000, {0x01, 0x00}, {0x00, 0x00}},
};
static const size_t MEMBER_COUNT = sizeof(MEMBERS) / sizeof(MEMBERS[0]);

// Gibt nullptr zurueck, wenn can_id unbekannt ist.
static inline const Member *find_member(uint16_t can_id) {
  for (size_t i = 0; i < MEMBER_COUNT; ++i) {
    if (MEMBERS[i].can_id == can_id) {
      return &MEMBERS[i];
    }
  }
  return nullptr;
}

// Baut die 7 Datenbytes eines Lese-Requests fuer eine beliebige CAN-ID (INTERFACES 7.1, 8.1).
// d0 = ((can_id >> 3) & 0xF0) | 0x01, d1 = can_id & 0x7F.
// fmt: 0 = auto, 1 = short, 2 = long. sw: Switch-Byte im Langformat (0xFA, 0xF8, 0xF9).
// Langformat Pflicht fuer sw != 0xFA und fuer Index 0xF7..0xFA (d2 waere sonst Switch/Kennung).
// Sonst: auto/short = kurz fuer index <= 0xFF, long = immer lang.
// Gibt immer 7 zurueck. Pruefung der Ziel-ID (0x000..0x7FF, != 0x680) und sw macht der Aufrufer.
static inline size_t build_read_request_id(uint16_t can_id, uint16_t index, uint8_t fmt, uint8_t sw,
                                           uint8_t out[7]) {
  out[0] = (uint8_t)(((can_id >> 3) & 0xF0) | 0x01);
  out[1] = (uint8_t)(can_id & 0x7F);
  const bool force_long = (sw != 0xFA) || (index >= 0x00F7 && index <= 0x00FA);
  const bool use_short = !force_long && (fmt != 2) && (index <= 0x00FF);
  if (use_short) {
    out[2] = (uint8_t)(index & 0xFF);
    out[3] = 0;
    out[4] = 0;
  } else {
    out[2] = sw;
    out[3] = (uint8_t)((index >> 8) & 0xFF);
    out[4] = (uint8_t)(index & 0xFF);
  }
  out[5] = 0;
  out[6] = 0;
  return 7;
}

// Baut die 7 Datenbytes eines Schreib-Requests fuer eine beliebige CAN-ID (Art 0 = write).
// d0 = ((can_id >> 3) & 0xF0) | 0x00, d1 = can_id & 0x7F.
// fmt: 0 = auto, 1 = short, 2 = long. Langformat Pflicht fuer index > 0xFF und fuer Index 0xF7..0xFA.
// Kurz: [d0, d1, index, vh, vl, 0, 0]. Lang: [d0, d1, 0xFA, ih, il, vh, vl].
// Gibt immer 7 zurueck. Pruefung der Ziel-ID (0x000..0x7FF, != 0x680) und des Werts macht der Aufrufer.
static inline size_t build_write_request_id(uint16_t can_id, uint16_t index, uint16_t value, uint8_t fmt,
                                            uint8_t out[7]) {
  out[0] = (uint8_t)(((can_id >> 3) & 0xF0) | 0x00);
  out[1] = (uint8_t)(can_id & 0x7F);
  const bool force_long = (index > 0x00FF) || (index >= 0x00F7 && index <= 0x00FA);
  const bool use_long = force_long || (fmt == 2);
  if (use_long) {
    out[2] = 0xFA;
    out[3] = (uint8_t)((index >> 8) & 0xFF);
    out[4] = (uint8_t)(index & 0xFF);
    out[5] = (uint8_t)((value >> 8) & 0xFF);
    out[6] = (uint8_t)(value & 0xFF);
  } else {
    out[2] = (uint8_t)(index & 0xFF);
    out[3] = (uint8_t)((value >> 8) & 0xFF);
    out[4] = (uint8_t)(value & 0xFF);
    out[5] = 0;
    out[6] = 0;
  }
  return 7;
}

// Lese-Request fuer ein bekanntes Member (Langform mit Format auto, Switch 0xFA).
static inline size_t build_read_request(const Member &m, uint16_t index, uint8_t out[7]) {
  return build_read_request_id(m.can_id, index, 0, 0xFA, out);
}

struct Parsed {
  uint8_t msg_type;
  uint16_t receiver;
  uint16_t index;
  uint16_t value;
  uint8_t sw;  // Switch-Byte bei Langlayout (0xF7..0xFA), 0 bei Kurzlayout
};

// Dekodiert einen Frame (Abschnitt 2, 8.1). false wenn len < 7.
static inline bool parse_frame(const uint8_t *data, size_t len, Parsed &out) {
  if (data == nullptr || len < 7) {
    return false;
  }
  out.msg_type = (uint8_t)(data[0] & 0x0F);
  out.receiver = (uint16_t)(((data[0] & 0xF0) << 3) | data[1]);
  const uint8_t s = data[2];
  if (s == 0xF7 || s == 0xF8 || s == 0xF9 || s == 0xFA) {
    out.sw = s;
    out.index = (uint16_t)((data[3] << 8) | data[4]);
    out.value = (uint16_t)((data[5] << 8) | data[6]);
  } else {
    out.sw = 0;
    out.index = data[2];
    out.value = (uint16_t)((data[3] << 8) | data[4]);
  }
  return true;
}

// Antwort (msg_type 2) vom Member sender_id/member_id auf index mit Switch sw.
// sw == 0xFA akzeptiert auch Kurzlayout (p.sw == 0).
static inline bool is_response_for(const Parsed &p, uint16_t sender_id, uint16_t member_id, uint16_t index,
                                   uint8_t sw) {
  if (!(p.msg_type == 2 && sender_id == member_id && p.index == index)) {
    return false;
  }
  return (sw == 0xFA) ? (p.sw == 0 || p.sw == 0xFA) : (p.sw == sw);
}

}  // namespace mkdbg

#endif  // MKDBG_ELSTER_FRAME_H
