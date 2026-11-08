// debug_json.h - Minimales JSON fuer mk1-debug (Parser und Formatter).
// Header-only, C++11, ohne JSON-Bibliothek. Nur snprintf/strstr/strtoul.
#ifndef MKDBG_DEBUG_JSON_H
#define MKDBG_DEBUG_JSON_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ring_buffer.h"

namespace mkdbg {

// format: 0 = auto (Default), 1 = short, 2 = long. sw: Switch-Byte 0xFA (Default), 0xF8, 0xF9.
struct SniffCmd { bool on; uint16_t ids[16]; uint8_t id_count; uint8_t types[8]; uint8_t type_count; };
struct ReadCmd  { uint16_t member; uint16_t index; uint8_t format; uint8_t sw; uint16_t src; uint32_t timeout_ms; };  // src: Absender-CAN-ID (Standard 0x680), timeout_ms 100..10000 (Standard 1000)
struct ScanCmd  { uint16_t member; uint16_t from; uint16_t to; uint32_t delay_ms; uint8_t format; uint8_t sw; };
struct ProbeCmd { uint16_t index; uint16_t from_id; uint16_t to_id; uint32_t delay_ms; };
struct WriteCmd { uint16_t member; uint16_t index; uint16_t value; uint8_t format; };

// ---------- Parser-Hilfen ----------

static inline bool dj_is_digit(char c) { return c >= '0' && c <= '9'; }
static inline bool dj_is_hex(char c) {
  return dj_is_digit(c) || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
}
static inline bool dj_is_alnum(char c) {
  return dj_is_digit(c) || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
}

static inline const char *dj_skip_ws(const char *p) {
  while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') ++p;
  return p;
}

// Liefert Zeiger auf den Wert nach "key": oder nullptr, wenn der Schluessel fehlt.
static inline const char *dj_find_value(const char *json, const char *key) {
  char pat[40];
  int n = snprintf(pat, sizeof pat, "\"%s\"", key);
  if (n < 0 || (size_t)n >= sizeof pat) return nullptr;
  const char *p = strstr(json, pat);
  if (p == nullptr) return nullptr;
  p = dj_skip_ws(p + n);
  if (*p != ':') return nullptr;
  return dj_skip_ws(p + 1);
}

// Liest ein Token: "Inhalt" (Anführungszeichen entfernt) oder unquotiert (alnum).
// p wird hinter das Token bewegt. false bei leerem oder zu langem Token.
static inline bool dj_read_token(const char *&p, char *buf, size_t cap) {
  size_t n = 0;
  if (*p == '"') {
    ++p;
    while (*p != '"') {
      if (*p == '\0' || n + 1 >= cap) return false;
      buf[n++] = *p++;
    }
    ++p;  // schliessendes Anfuehrungszeichen
  } else {
    while (dj_is_alnum(*p)) {
      if (n + 1 >= cap) return false;
      buf[n++] = *p++;
    }
  }
  if (n == 0) return false;
  buf[n] = '\0';
  return true;
}

// "0x..."/"0X..." (max. 8 Hex-Ziffern) oder Dezimal (max. 9 Ziffern); Wert <= max.
static inline bool dj_to_uint(const char *s, uint32_t max, uint32_t &out) {
  int base = 10;
  size_t max_digits = 9;
  const char *d = s;
  if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
    base = 16;
    max_digits = 8;
    d = s + 2;
  }
  size_t n = strlen(d);
  if (n == 0 || n > max_digits) return false;
  for (size_t i = 0; i < n; ++i) {
    bool good = (base == 16) ? dj_is_hex(d[i]) : dj_is_digit(d[i]);
    if (!good) return false;
  }
  char *end = nullptr;
  unsigned long v = strtoul(d, &end, base);
  if (end == nullptr || *end != '\0' || v > (unsigned long)max) return false;
  out = (uint32_t)v;
  return true;
}

// Pflichtfeld: Zahl <= max. false wenn fehlend oder ungueltig.
static inline bool dj_get_uint(const char *json, const char *key, uint32_t max, uint32_t &out) {
  const char *p = dj_find_value(json, key);
  if (p == nullptr) return false;
  char buf[16];
  if (!dj_read_token(p, buf, sizeof buf)) return false;
  return dj_to_uint(buf, max, out);
}

// Pflichtfeld: true oder false.
static inline bool dj_get_bool(const char *json, const char *key, bool &out) {
  const char *p = dj_find_value(json, key);
  if (p == nullptr) return false;
  if (strncmp(p, "true", 4) == 0 && !dj_is_alnum(p[4])) { out = true; return true; }
  if (strncmp(p, "false", 5) == 0 && !dj_is_alnum(p[5])) { out = false; return true; }
  return false;
}

// Optionales Feld: fehlt der Schluessel, ist count = 0 und das Ergebnis true.
// Vorhanden: muss ein Array [..] mit hoechstens cap Zahlen sein.
static inline bool dj_get_uint_array(const char *json, const char *key, uint32_t max,
                                     uint32_t *vals, uint8_t cap, uint8_t &count) {
  count = 0;
  const char *p = dj_find_value(json, key);
  if (p == nullptr) return true;
  if (*p != '[') return false;
  ++p;
  p = dj_skip_ws(p);
  if (*p == ']') return true;
  for (;;) {
    p = dj_skip_ws(p);
    if (count >= cap) return false;
    char buf[16];
    if (!dj_read_token(p, buf, sizeof buf)) return false;
    uint32_t v = 0;
    if (!dj_to_uint(buf, max, v)) return false;
    vals[count++] = v;
    p = dj_skip_ws(p);
    if (*p == ',') { ++p; continue; }
    return *p == ']';
  }
}

// Optionales Feld "format": "auto" (0) | "short" (1) | "long" (2). Fehlt: 0.
// Unbekannter Wert: false.
static inline bool dj_get_format(const char *json, uint8_t &out) {
  out = 0;
  const char *p = dj_find_value(json, "format");
  if (p == nullptr) return true;
  char buf[16];
  if (!dj_read_token(p, buf, sizeof buf)) return false;
  if (strcmp(buf, "auto") == 0) { out = 0; return true; }
  if (strcmp(buf, "short") == 0) { out = 1; return true; }
  if (strcmp(buf, "long") == 0) { out = 2; return true; }
  return false;
}

// Optionales Feld "switch": "fa" | "f8" | "f9" (auch "0xfa" usw., Gross/Klein egal). Fehlt: 0xFA.
// Unbekannter Wert: false.
static inline bool dj_get_switch(const char *json, uint8_t &out) {
  out = 0xFA;
  const char *p = dj_find_value(json, "switch");
  if (p == nullptr) return true;
  char buf[16];
  if (!dj_read_token(p, buf, sizeof buf)) return false;
  for (char *c = buf; *c != '\0'; ++c) {
    if (*c >= 'A' && *c <= 'Z') *c = (char)(*c - 'A' + 'a');
  }
  const char *s = buf;
  if (s[0] == '0' && s[1] == 'x') s += 2;
  if (strcmp(s, "fa") == 0) { out = 0xFA; return true; }
  if (strcmp(s, "f8") == 0) { out = 0xF8; return true; }
  if (strcmp(s, "f9") == 0) { out = 0xF9; return true; }
  return false;
}

// ---------- Parser ----------

// {"on":true,"ids":["0x180"],"types":[2]}  leere Listen = alles. "on" ist Pflicht.
inline bool parse_sniff(const char *json, SniffCmd &out) {
  if (json == nullptr) return false;
  SniffCmd c;
  memset(&c, 0, sizeof c);
  if (!dj_get_bool(json, "on", c.on)) return false;
  uint32_t tmp_ids[16];
  uint32_t tmp_types[8];
  if (!dj_get_uint_array(json, "ids", 0xFFFFu, tmp_ids, 16, c.id_count)) return false;
  if (!dj_get_uint_array(json, "types", 0xFFu, tmp_types, 8, c.type_count)) return false;
  for (uint8_t i = 0; i < c.id_count; ++i) c.ids[i] = (uint16_t)tmp_ids[i];
  for (uint8_t i = 0; i < c.type_count; ++i) c.types[i] = (uint8_t)tmp_types[i];
  out = c;
  return true;
}

// {"member":"0x180","index":"0x4ef0","format":"auto","switch":"fa"}  format/switch optional
inline bool parse_read(const char *json, ReadCmd &out) {
  if (json == nullptr) return false;
  uint32_t member = 0, index = 0;
  uint8_t format = 0, sw = 0xFA;
  if (!dj_get_uint(json, "member", 0xFFFFu, member)) return false;
  if (!dj_get_uint(json, "index", 0xFFFFu, index)) return false;
  if (!dj_get_format(json, format)) return false;
  if (!dj_get_switch(json, sw)) return false;
  uint32_t src = 0x680;
  if (dj_find_value(json, "src") != nullptr) {
    if (!dj_get_uint(json, "src", 0x7FFu, src)) return false;
  }
  out.member = (uint16_t)member;
  out.index = (uint16_t)index;
  out.format = format;
  out.sw = sw;
  uint32_t tmo = 1000;
  if (dj_find_value(json, "timeout_ms") != nullptr) {
    if (!dj_get_uint(json, "timeout_ms", 10000u, tmo)) return false;
    if (tmo < 100) tmo = 100;
  }
  out.src = (uint16_t)src;
  out.timeout_ms = tmo;
  return true;
}

// {"member":"0x500","from":"0x4e00","to":"0x4fff","delay_ms":200,"format":"auto","switch":"fa"}
// delay_ms fehlt: 200. delay_ms < 100: 100. format fehlt: auto. switch fehlt: fa. false bei from > to.
inline bool parse_scan(const char *json, ScanCmd &out) {
  if (json == nullptr) return false;
  uint32_t member = 0, from = 0, to = 0, delay = 200;
  uint8_t format = 0, sw = 0xFA;
  if (!dj_get_uint(json, "member", 0xFFFFu, member)) return false;
  if (!dj_get_uint(json, "from", 0xFFFFu, from)) return false;
  if (!dj_get_uint(json, "to", 0xFFFFu, to)) return false;
  if (from > to) return false;
  if (dj_find_value(json, "delay_ms") != nullptr) {
    if (!dj_get_uint(json, "delay_ms", 0xFFFFFFFFu, delay)) return false;
  }
  if (!dj_get_format(json, format)) return false;
  if (!dj_get_switch(json, sw)) return false;
  if (delay < 100) delay = 100;
  out.member = (uint16_t)member;
  out.from = (uint16_t)from;
  out.to = (uint16_t)to;
  out.delay_ms = delay;
  out.format = format;
  out.sw = sw;
  return true;
}

// {"index":"0x000b","from_id":"0x000","to_id":"0x7ff","delay_ms":100}
// delay_ms fehlt: 100. delay_ms < 100: 100. false bei from_id > to_id oder to_id > 0x7ff.
inline bool parse_probe(const char *json, ProbeCmd &out) {
  if (json == nullptr) return false;
  uint32_t index = 0, from = 0, to = 0, delay = 100;
  if (!dj_get_uint(json, "index", 0xFFFFu, index)) return false;
  if (!dj_get_uint(json, "from_id", 0x7FFu, from)) return false;
  if (!dj_get_uint(json, "to_id", 0x7FFu, to)) return false;
  if (from > to) return false;
  if (dj_find_value(json, "delay_ms") != nullptr) {
    if (!dj_get_uint(json, "delay_ms", 0xFFFFFFFFu, delay)) return false;
  }
  if (delay < 100) delay = 100;
  out.index = (uint16_t)index;
  out.from_id = (uint16_t)from;
  out.to_id = (uint16_t)to;
  out.delay_ms = delay;
  return true;
}

// {"member":"0x201","index":"0x0013","value":"0x0208","format":"auto"}  format optional
// false bei fehlendem Pflichtfeld, Wert > 0xFFFF oder unbekanntem format.
inline bool parse_write(const char *json, WriteCmd &out) {
  if (json == nullptr) return false;
  uint32_t member = 0, index = 0, value = 0;
  uint8_t format = 0;
  if (!dj_get_uint(json, "member", 0xFFFFu, member)) return false;
  if (!dj_get_uint(json, "index", 0xFFFFu, index)) return false;
  if (!dj_get_uint(json, "value", 0xFFFFu, value)) return false;
  if (!dj_get_format(json, format)) return false;
  out.member = (uint16_t)member;
  out.index = (uint16_t)index;
  out.value = (uint16_t)value;
  out.format = format;
  return true;
}

// ---------- Formatter-Hilfen ----------
// pos ist immer <= cap-1; out bleibt stets NUL-terminiert.

static inline size_t dj_fail(char *out) {
  out[0] = '\0';
  return 0;
}

static inline bool dj_put(char *out, size_t cap, size_t &pos, const char *s) {
  size_t n = strlen(s);
  if (n > cap - 1 - pos) return false;
  memcpy(out + pos, s, n);
  pos += n;
  out[pos] = '\0';
  return true;
}

// fmt mit genau einem %lu oder %lx (Wert wird nach unsigned long gewandelt).
static inline bool dj_putf(char *out, size_t cap, size_t &pos, const char *fmt, unsigned long v) {
  char tmp[32];
  int n = snprintf(tmp, sizeof tmp, fmt, v);
  if (n < 0 || (size_t)n >= sizeof tmp) return false;
  return dj_put(out, cap, pos, tmp);
}

// Wie dj_putf, aber fuer unsigned long long (%llu).
static inline bool dj_putull(char *out, size_t cap, size_t &pos, unsigned long long v) {
  char tmp[32];
  int n = snprintf(tmp, sizeof tmp, "%llu", v);
  if (n < 0 || (size_t)n >= sizeof tmp) return false;
  return dj_put(out, cap, pos, tmp);
}

// Sicheres Wort fuer Status-Felder: nur [a-z_], mindestens ein Zeichen.
static inline bool dj_is_safe_word(const char *s) {
  if (s == nullptr || s[0] == '\0') return false;
  for (const char *p = s; *p != '\0'; ++p) {
    bool good = (*p >= 'a' && *p <= 'z') || *p == '_';
    if (!good) return false;
  }
  return true;
}

// ---------- Formatter ----------

// [{"t":123456,"id":"0x180","d":"d200fa01120200","tx":false}, ...]
// Mit Zeit: {"t":123456,"u":1700000000123,"id":...}  ("u" nur wenn f.u != 0).
inline size_t format_rx_batch(const Frame *frames, size_t count, char *out, size_t cap) {
  if (out == nullptr || cap == 0) return 0;
  if (frames == nullptr && count > 0) return dj_fail(out);
  size_t pos = 0;
  if (!dj_put(out, cap, pos, "[")) return dj_fail(out);
  for (size_t i = 0; i < count; ++i) {
    const Frame &f = frames[i];
    if (i > 0 && !dj_put(out, cap, pos, ",")) return dj_fail(out);
    if (!dj_put(out, cap, pos, "{\"t\":")) return dj_fail(out);
    if (!dj_putf(out, cap, pos, "%lu", f.t)) return dj_fail(out);
    if (f.u != 0) {
      if (!dj_put(out, cap, pos, ",\"u\":")) return dj_fail(out);
      if (!dj_putull(out, cap, pos, (unsigned long long)f.u)) return dj_fail(out);
    }
    if (!dj_put(out, cap, pos, ",\"id\":\"")) return dj_fail(out);
    if (!dj_putf(out, cap, pos, "0x%03lx", f.id)) return dj_fail(out);
    if (!dj_put(out, cap, pos, "\",\"d\":\"")) return dj_fail(out);
    size_t n = f.dlc > 8 ? 8 : f.dlc;
    for (size_t b = 0; b < n; ++b) {
      if (!dj_putf(out, cap, pos, "%02lx", f.data[b])) return dj_fail(out);
    }
    if (!dj_put(out, cap, pos, f.tx ? "\",\"tx\":true}" : "\",\"tx\":false}")) return dj_fail(out);
  }
  if (!dj_put(out, cap, pos, "]")) return dj_fail(out);
  return pos;
}

// {"member":"0x180","index":"0x4ef0","switch":"0xfa","raw":"0x03e8","status":"ok"}
// src != 0x680: zusaetzlich ,"src":"0x%03x" (Absender-Test, sonst Ausgabe unveraendert).
inline size_t format_read_result(uint16_t member, uint16_t index, uint8_t sw, uint16_t raw, const char *status,
                                 char *out, size_t cap, uint16_t src = 0x680) {
  if (out == nullptr || cap == 0) return 0;
  if (status == nullptr) return dj_fail(out);
  size_t pos = 0;
  bool ok = dj_put(out, cap, pos, "{\"member\":\"")
    && dj_putf(out, cap, pos, "0x%03lx", member)
    && dj_put(out, cap, pos, "\",\"index\":\"")
    && dj_putf(out, cap, pos, "0x%04lx", index)
    && dj_put(out, cap, pos, "\",\"switch\":\"")
    && dj_putf(out, cap, pos, "0x%02lx", sw)
    && dj_put(out, cap, pos, "\",\"raw\":\"")
    && dj_putf(out, cap, pos, "0x%04lx", raw)
    && dj_put(out, cap, pos, "\",\"status\":\"")
    && dj_put(out, cap, pos, status)
    && (src == 0x680 || (dj_put(out, cap, pos, "\",\"src\":\"") && dj_putf(out, cap, pos, "0x%03lx", src)))
    && dj_put(out, cap, pos, "\"}");
  return ok ? pos : dj_fail(out);
}

// {"member":"0x180","format":"auto","index":"0x4e40","switch":"0xfa","done":64,"total":512,"state":"running"}
// state: "running" | "done" | "stopped"
inline size_t format_scan_progress(uint16_t member, const char *format, uint16_t index, uint8_t sw,
                                   uint32_t done, uint32_t total, const char *state,
                                   char *out, size_t cap) {
  if (out == nullptr || cap == 0) return 0;
  if (format == nullptr || state == nullptr) return dj_fail(out);
  size_t pos = 0;
  bool ok = dj_put(out, cap, pos, "{\"member\":\"")
    && dj_putf(out, cap, pos, "0x%03lx", member)
    && dj_put(out, cap, pos, "\",\"format\":\"")
    && dj_put(out, cap, pos, format)
    && dj_put(out, cap, pos, "\",\"index\":\"")
    && dj_putf(out, cap, pos, "0x%04lx", index)
    && dj_put(out, cap, pos, "\",\"switch\":\"")
    && dj_putf(out, cap, pos, "0x%02lx", sw)
    && dj_put(out, cap, pos, "\",\"done\":")
    && dj_putf(out, cap, pos, "%lu", done)
    && dj_put(out, cap, pos, ",\"total\":")
    && dj_putf(out, cap, pos, "%lu", total)
    && dj_put(out, cap, pos, ",\"state\":\"")
    && dj_put(out, cap, pos, state)
    && dj_put(out, cap, pos, "\"}");
  return ok ? pos : dj_fail(out);
}

// {"index":"0x000b","id":"0x1a0","done":416,"total":2047,"state":"running"}
// state: "running" | "done" | "stopped"
inline size_t format_probe_progress(uint16_t index, uint16_t id, uint32_t done, uint32_t total,
                                    const char *state, char *out, size_t cap) {
  if (out == nullptr || cap == 0) return 0;
  if (state == nullptr) return dj_fail(out);
  size_t pos = 0;
  bool ok = dj_put(out, cap, pos, "{\"index\":\"")
    && dj_putf(out, cap, pos, "0x%04lx", index)
    && dj_put(out, cap, pos, "\",\"id\":\"")
    && dj_putf(out, cap, pos, "0x%03lx", id)
    && dj_put(out, cap, pos, "\",\"done\":")
    && dj_putf(out, cap, pos, "%lu", done)
    && dj_put(out, cap, pos, ",\"total\":")
    && dj_putf(out, cap, pos, "%lu", total)
    && dj_put(out, cap, pos, ",\"state\":\"")
    && dj_put(out, cap, pos, state)
    && dj_put(out, cap, pos, "\"}");
  return ok ? pos : dj_fail(out);
}

// {"fw":"debug","rx":0,"tx":0,"rx_fill":0,"rx_cap":4096,"overflow":0,"twai_err":0,"bus_off":false,"heap":0,"ntp":false}
inline size_t format_status(uint32_t rx, uint32_t tx, size_t rx_fill, size_t rx_cap, uint32_t overflow,
                            uint32_t twai_err, bool bus_off, uint32_t heap, bool ntp, char *out, size_t cap) {
  if (out == nullptr || cap == 0) return 0;
  size_t pos = 0;
  bool ok = dj_put(out, cap, pos, "{\"fw\":\"debug\",\"rx\":")
    && dj_putf(out, cap, pos, "%lu", rx)
    && dj_put(out, cap, pos, ",\"tx\":")
    && dj_putf(out, cap, pos, "%lu", tx)
    && dj_put(out, cap, pos, ",\"rx_fill\":")
    && dj_putf(out, cap, pos, "%lu", (unsigned long)rx_fill)
    && dj_put(out, cap, pos, ",\"rx_cap\":")
    && dj_putf(out, cap, pos, "%lu", (unsigned long)rx_cap)
    && dj_put(out, cap, pos, ",\"overflow\":")
    && dj_putf(out, cap, pos, "%lu", overflow)
    && dj_put(out, cap, pos, ",\"twai_err\":")
    && dj_putf(out, cap, pos, "%lu", twai_err)
    && dj_put(out, cap, pos, ",\"bus_off\":")
    && dj_put(out, cap, pos, bus_off ? "true" : "false")
    && dj_put(out, cap, pos, ",\"heap\":")
    && dj_putf(out, cap, pos, "%lu", heap)
    && dj_put(out, cap, pos, ",\"ntp\":")
    && dj_put(out, cap, pos, ntp ? "true" : "false")
    && dj_put(out, cap, pos, "}");
  return ok ? pos : dj_fail(out);
}

// {"member":"0x201","index":"0x0013","value":"0x0208","status":"verified","ack":4,"readback":"0x0208"}
// ack_type < 0: "ack" fehlt. readback < 0: "readback" fehlt. status: nur [a-z_], sonst Fehler.
inline size_t format_write_result(uint16_t member, uint16_t index, uint16_t value, const char *status,
                                  int ack_type, int32_t readback, char *out, size_t cap) {
  if (out == nullptr || cap == 0) return 0;
  if (!dj_is_safe_word(status)) return dj_fail(out);
  size_t pos = 0;
  bool ok = dj_put(out, cap, pos, "{\"member\":\"")
    && dj_putf(out, cap, pos, "0x%03lx", member)
    && dj_put(out, cap, pos, "\",\"index\":\"")
    && dj_putf(out, cap, pos, "0x%04lx", index)
    && dj_put(out, cap, pos, "\",\"value\":\"")
    && dj_putf(out, cap, pos, "0x%04lx", value)
    && dj_put(out, cap, pos, "\",\"status\":\"")
    && dj_put(out, cap, pos, status)
    && dj_put(out, cap, pos, "\"");
  if (ok && ack_type >= 0) {
    ok = dj_put(out, cap, pos, ",\"ack\":")
      && dj_putf(out, cap, pos, "%lu", (unsigned long)ack_type);
  }
  if (ok && readback >= 0) {
    ok = dj_put(out, cap, pos, ",\"readback\":\"")
      && dj_putf(out, cap, pos, "0x%04lx", (unsigned long)readback)
      && dj_put(out, cap, pos, "\"");
  }
  ok = ok && dj_put(out, cap, pos, "}");
  return ok ? pos : dj_fail(out);
}

}  // namespace mkdbg

#endif  // MKDBG_DEBUG_JSON_H
