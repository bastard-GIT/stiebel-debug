// lwz_ha.h - LWZ 180: Istwerte aus den I2C-Paketen dekodieren und als eigenes Home-Assistant-Geraet
// per MQTT veroeffentlichen (Discovery wie die Waermepumpe in ha-stiebel-control.h).
// Header-only, C++11. Aktiv nur nach lwzha::setup() (Produktiv-Paket lwz_i2c); in mk1-i2c inaktiv.
//
// Quellen der Zuordnung: referenzen/dokumentation/erkenntnisse_2026-10-30_lwz-i2c-slave-scan.md, Abschnitt 6.
//  - Istwert-Paket: 00 e3 20 10 <typ> <hi> <lo> <crc>, CRC-8 Poly 0x49, Init 0 ueber Byte 1..6.
//  - Am Display bestaetigt (30.10.2026): 00 01 06 07 08 09 0e 0f 10 11 14 15; 0c/0d sehr wahrscheinlich.
//  - 16 50 51 5a nur nach kdarnok/lwz180, auf unserem Bus bisher nicht gesehen.
//  - Luefterstufe aus dem Display-Paket 00 f7 20 ...: Byte 8 (ohne Adressbyte) nur gueltig, wenn
//    Byte 20 Bit 2 oder Byte 19 Bit 4 gesetzt ist und der Code bekannt ist (06=1, 5b=2, 4f=3, 3f=0).
// Discovery: retained, erst beim ersten Empfang eines Typs (keine toten Entitaeten), nach jedem
// MQTT-Reconnect erneut. State: bei Aenderung hoechstens alle 10 s, sonst spaetestens alle 5 min
// (auch ohne neues Paket, solange der Bus lebt: die LWZ sendet manche Typen nur bei Aenderung).
#ifndef I2CS_LWZ_HA_H
#define I2CS_LWZ_HA_H

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "i2c_frame.h"
#include "crash_trace.h"

namespace i2cs {
namespace lwzha {

struct Def {
  uint8_t typ;          // Istwert-Typ (Byte nach e3 20 10); 0xFF = Luefterstufe aus dem Display
  const char *key;      // Topic- und ID-Teil
  const char *name;     // Anzeigename in HA
  const char *unit;     // nullptr = ohne Einheit
  const char *dev_class;
  uint8_t div;          // Rohwert / div (1 oder 10)
  bool sign;            // Rohwert als int16
  const char *icon;
};

static const uint8_t kTypLevel = 0xFF;

static const Def kDefs[] = {
    {0x06, "aussenluft_temperatur", "Außenlufttemperatur", "°C", "temperature", 10, true, nullptr},
    {0x07, "zuluft_temperatur", "Zulufttemperatur", "°C", "temperature", 10, true, nullptr},
    {0x08, "abluft_temperatur", "Ablufttemperatur", "°C", "temperature", 10, true, nullptr},
    {0x09, "fortluft_temperatur", "Fortlufttemperatur", "°C", "temperature", 10, true, nullptr},
    {0x00, "abluft_taupunkt", "Taupunkt Abluft", "°C", "temperature", 10, true, nullptr},
    {0x01, "aussenluft_taupunkt", "Taupunkt Außenluft", "°C", "temperature", 10, true, nullptr},
    {0x0e, "aussenluft_feuchte", "Feuchte Außenluft", "%", "humidity", 10, false, nullptr},
    {0x0f, "abluft_feuchte", "Feuchte Abluft", "%", "humidity", 10, false, nullptr},
    {0x10, "zuluft_volumenstrom", "Volumenstrom Zuluft", "m³/h", "volume_flow_rate", 1, false, nullptr},
    {0x11, "fortluft_volumenstrom", "Volumenstrom Fortluft", "m³/h", "volume_flow_rate", 1, false, nullptr},
    {0x14, "zuluftluefter_ansteuerung", "Zuluftlüfter Ansteuerung", "%", nullptr, 10, false, "mdi:fan"},
    {0x15, "fortluftluefter_ansteuerung", "Fortluftlüfter Ansteuerung", "%", nullptr, 10, false, "mdi:fan"},
    {0x0c, "zuluftluefter_drehzahl", "Zuluftlüfter Drehzahl", "rpm", nullptr, 1, false, "mdi:fan"},
    {0x0d, "fortluftluefter_drehzahl", "Fortluftlüfter Drehzahl", "rpm", nullptr, 1, false, "mdi:fan"},
    // nur nach kdarnok, auf unserem Bus bisher nicht gesehen (Entitaet entsteht erst beim ersten Empfang)
    {0x16, "vorheizung_leistung", "Vorheizung Leistung", "%", nullptr, 1, false, "mdi:radiator"},
    {0x5a, "filter_laufzeit", "Filterlaufzeit", "h", "duration", 1, false, nullptr},
    {0x51, "betriebsdauer_geraet", "Betriebsdauer Gerät", "d", "duration", 1, false, nullptr},
    {0x50, "betriebsdauer_luefter", "Betriebsdauer Lüfter", "d", "duration", 1, false, nullptr},
    {kTypLevel, "luefterstufe", "Lüfterstufe", nullptr, nullptr, 1, false, "mdi:fan-speed-1"},
};
static const size_t kNumDefs = sizeof(kDefs) / sizeof(kDefs[0]);

static const uint32_t kMinPubMs = 10000;   // Aenderung hoechstens alle 10 s
static const uint32_t kMaxPubMs = 300000;  // ohne Aenderung spaetestens alle 5 min
static const uint32_t kExpireS = 900;      // HA expire_after: 3x kMaxPubMs, danach unavailable
static const uint32_t kBusAliveMs = 60000; // Bus gilt als lebendig, wenn so kurz zuvor ein gueltiges Paket kam

struct State {
  int32_t raw;
  int32_t pub_raw;
  uint32_t pub_ms;
  bool seen;
  bool published;
  bool disc_sent;
};

static State g_st[kNumDefs];
static bool g_on = false;
static bool g_was_connected = false;
static char g_prefix[24] = "lwz180";
static char g_dev_name[64] = "Stiebel Eltron LWZ 180 Enthalpie";
static char g_model[40] = "LWZ 180 Enthalpie";
static char g_avail[48] = "heatingpump/status";
static uint32_t g_values = 0;  // Anzahl dekodierter Werte (Status)
static uint32_t g_last_valid_ms = 0;  // Zeit des letzten gueltigen LWZ-Pakets (Istwert oder Display)
static bool g_any_valid = false;

// Display-Zustand fuer die Steuerung (lwz_ctl.h). Nur gesetzt, wenn das Display den Grundzustand zeigt:
// Zeile 1 Stellen 1-2 und Zeile 2 leer, Stelle 3 = Luefterstufe. In Menues bleibt g_disp_ms alt.
static int g_disp_level = -1;      // 0..3, -1 = unbekannt
static bool g_disp_pv = false;     // Intensivlueftung (Power Vent) aktiv: seg[26] Bit 0 (Icon pv2)
static uint32_t g_disp_ms = 0;     // Zeit des letzten Grundzustand-Pakets
static bool g_disp_seen = false;

static void copy_str(char *dst, size_t cap, const char *src) {
  if (src == nullptr) return;
  strncpy(dst, src, cap - 1);
  dst[cap - 1] = '\0';
}

// prefix: Topic-Praefix und ID-Praefix (z. B. "lwz180"). avail: availability_topic der Firmware.
static void setup(const char *prefix, const char *dev_name, const char *model, const char *avail) {
  copy_str(g_prefix, sizeof g_prefix, prefix);
  copy_str(g_dev_name, sizeof g_dev_name, dev_name);
  copy_str(g_model, sizeof g_model, model);
  copy_str(g_avail, sizeof g_avail, avail);
  memset(g_st, 0, sizeof g_st);
  g_on = true;
  ESP_LOGI("lwzha", "LWZ-Werte an: Praefix %s, Geraet \"%s\"", g_prefix, g_dev_name);
}

static uint8_t crc8(const uint8_t *p, size_t n) {
  uint8_t c = 0;
  for (size_t i = 0; i < n; ++i) {
    c ^= p[i];
    for (int b = 0; b < 8; ++b) c = (c & 0x80) ? (uint8_t)((c << 1) ^ 0x49) : (uint8_t)(c << 1);
  }
  return c;
}

static bool connected() {
  return ::esphome::mqtt::global_mqtt_client != nullptr && ::esphome::mqtt::global_mqtt_client->is_connected();
}

static void pub(const char *topic, const char *payload, size_t len, bool retain) {
  if (!connected()) return;
  const uint16_t prev = ct::g_tr.phase;
  ct::set(ct::P_MQTT_PUB, prev);
  ct::g_tr.pubs = ct::g_tr.pubs + 1;
  ::esphome::mqtt::global_mqtt_client->publish(topic, payload, len, 0, retain);
  ct::set(prev);
}

static void send_discovery(size_t i) {
  ct::set(ct::P_DISCOVERY, (uint16_t)i);
  const Def &d = kDefs[i];
  char topic[128];
  snprintf(topic, sizeof topic, "homeassistant/sensor/%s/%s/config", g_prefix, d.key);
  char buf[768];
  int n = snprintf(buf, sizeof buf,
                   "{\"name\":\"%s\",\"unique_id\":\"%s_%s\",\"object_id\":\"%s_%s\","
                   "\"state_topic\":\"%s/%s/state\",\"availability_topic\":\"%s\",\"state_class\":\"measurement\","
                   "\"expire_after\":%lu",
                   d.name, g_prefix, d.key, g_prefix, d.key, g_prefix, d.key, g_avail,
                   (unsigned long)(kExpireS));
  if (d.dev_class != nullptr && n > 0 && (size_t)n < sizeof buf)
    n += snprintf(buf + n, sizeof buf - n, ",\"device_class\":\"%s\"", d.dev_class);
  if (d.unit != nullptr && n > 0 && (size_t)n < sizeof buf)
    n += snprintf(buf + n, sizeof buf - n, ",\"unit_of_measurement\":\"%s\"", d.unit);
  if (d.div == 10 && n > 0 && (size_t)n < sizeof buf)
    n += snprintf(buf + n, sizeof buf - n, ",\"suggested_display_precision\":1");
  if (d.icon != nullptr && n > 0 && (size_t)n < sizeof buf)
    n += snprintf(buf + n, sizeof buf - n, ",\"icon\":\"%s\"", d.icon);
  if (n > 0 && (size_t)n < sizeof buf)
    n += snprintf(buf + n, sizeof buf - n,
                  ",\"device\":{\"identifiers\":[\"stiebel_eltron_%s\"],\"name\":\"%s\","
                  "\"manufacturer\":\"Stiebel Eltron\",\"model\":\"%s\"}}",
                  g_prefix, g_dev_name, g_model);
  if (n <= 0 || (size_t)n >= sizeof buf) {
    ESP_LOGW("lwzha", "Discovery zu lang: %s", d.key);
    return;
  }
  pub(topic, buf, (size_t)n, true);
  g_st[i].disc_sent = true;
}

static void send_state(size_t i, uint32_t ms) {
  ct::set(ct::P_STATE, (uint16_t)i);
  const Def &d = kDefs[i];
  State &s = g_st[i];
  char topic[96];
  snprintf(topic, sizeof topic, "%s/%s/state", g_prefix, d.key);
  char val[16];
  int n;
  if (d.div == 10) {
    const int32_t a = s.raw < 0 ? -s.raw : s.raw;
    n = snprintf(val, sizeof val, "%s%ld.%ld", s.raw < 0 ? "-" : "", (long)(a / 10), (long)(a % 10));
  } else {
    n = snprintf(val, sizeof val, "%ld", (long)s.raw);
  }
  if (n <= 0 || (size_t)n >= sizeof val) return;
  pub(topic, val, (size_t)n, true);
  s.pub_raw = s.raw;
  s.pub_ms = ms;
  s.published = true;
}

static void update(size_t i, int32_t raw, uint32_t ms) {
  State &s = g_st[i];
  s.raw = raw;
  s.seen = true;
  ++g_values;
  if (!connected()) return;
  if (!s.disc_sent) send_discovery(i);
  const uint32_t age = ms - s.pub_ms;
  if (!s.published || (raw != s.pub_raw && age >= kMinPubMs) || age >= kMaxPubMs) send_state(i, ms);
}

static int find(uint8_t typ) {
  for (size_t i = 0; i < kNumDefs; ++i)
    if (kDefs[i].typ == typ) return (int)i;
  return -1;
}

// Pro empfangenem Frame (bytes[0] = Adressbyte 0x00). Nur vollstaendige Pakete mit gueltiger CRC.
static void on_frame(const Frame &f, uint32_t ms) {
  if (!g_on || f.flags != 0) return;
  ct::set(ct::P_ON_FRAME);
  const uint8_t *b = f.bytes;
  // Istwert: 00 e3 20 10 typ hi lo crc
  if (f.len == 8 && b[0] == 0x00 && b[1] == 0xe3 && b[2] == 0x20 && b[3] == 0x10) {
    if (crc8(b + 1, 6) != b[7]) return;
    g_last_valid_ms = ms;
    g_any_valid = true;
    const int i = find(b[4]);
    if (i < 0 || kDefs[i].typ == kTypLevel) return;
    const uint16_t u = (uint16_t)((b[5] << 8) | b[6]);
    if (u == 0x8000) return;  // Sentinel "kein Wert" (wie Elster), sicherheitshalber verwerfen
    update((size_t)i, kDefs[i].sign ? (int32_t)(int16_t)u : (int32_t)u, ms);
    return;
  }
  // Display: 00 f7 20 <25 Byte> crc (29 Byte); seg = b + 1 (28 Byte, Index wie display_model.py)
  if (f.len == 29 && b[0] == 0x00 && b[1] == 0xf7 && b[2] == 0x20) {
    if (crc8(b + 1, 27) != b[28]) return;
    g_last_valid_ms = ms;
    g_any_valid = true;
    const uint8_t *seg = b + 1;
    if (!((seg[20] & 0x04) || (seg[19] & 0x10))) return;
    int level = -1;
    switch (seg[8]) {
      case 0x3f: level = 0; break;
      case 0x06: level = 1; break;
      case 0x5b: level = 2; break;
      case 0x4f: level = 3; break;
      default: return;  // Menue oder unbekannter Code
    }
    const bool home = seg[6] == 0 && seg[7] == 0 && seg[9] == 0 && seg[10] == 0 && seg[11] == 0 && seg[12] == 0;
    if (home) {
      g_disp_level = level;
      g_disp_pv = (seg[26] & 0x01) != 0;
      g_disp_ms = ms;
      g_disp_seen = true;
    }
    const int i = find(kTypLevel);
    if (i < 0) return;
    // Stufe ohne 10-s-Sperre veroeffentlichen: Rueckmeldung fuer das HA-Select (lwz_ctl.h)
    if (connected() && g_st[i].published && g_st[i].pub_raw != level) g_st[i].pub_ms = ms - kMinPubMs;
    update((size_t)i, level, ms);
  }
}

// Aus dem Haupt-Loop: nach MQTT-Reconnect Discovery und States neu senden.
static void poll(uint32_t ms) {
  if (!g_on) return;
  ct::set(ct::P_POLL);
  const bool c = connected();
  if (c && !g_was_connected) {
    for (size_t i = 0; i < kNumDefs; ++i) {
      g_st[i].disc_sent = false;
      g_st[i].published = false;
    }
  }
  g_was_connected = c;
  if (!c) return;
  for (size_t i = 0; i < kNumDefs; ++i) {
    State &s = g_st[i];
    if (!s.seen) continue;
    if (!s.disc_sent) send_discovery(i);
    if (!s.published) {
      send_state(i, ms);
      continue;
    }
    // Die LWZ sendet manche Typen nur bei Aenderung (z. B. 08 Ablufttemperatur, Luecken > 15 min).
    // Letzten Wert alle kMaxPubMs wiederholen, solange der Bus lebt; sonst greift expire_after in HA.
    const bool bus_alive = g_any_valid && (uint32_t)(ms - g_last_valid_ms) < kBusAliveMs;
    if (bus_alive && (uint32_t)(ms - s.pub_ms) >= kMaxPubMs) send_state(i, ms);
  }
}

}  // namespace lwzha
}  // namespace i2cs

#endif  // I2CS_LWZ_HA_H
