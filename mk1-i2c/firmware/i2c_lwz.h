// i2c_lwz.h - Glue fuer mk1-i2c: Hardware-I2C-Slave an der LWZ 180 (Adresse 42, General Call, kein Stretching).
// Nur ESP-Build (esp-idf, ESPHome). Header-only, C++11, keine dynamische Allokation im Hot-Path.
// Der fruehere passive Software-Sniffer entfaellt seit 2026-10-30 (Slave-Test: 159 Frames in 30 s,
// CRC 158/158 ok; Sniffer ca. 96 % CRC ok, Fehler durch ISR-Latenz).
// Include-Reihenfolge: firmware/i2c_lwz.yaml (ring_buffer.h, debug_json.h, unix_time.h,
// i2c_frame.h, i2c_slave_rx.h, i2c_scan.h, i2c_lwz.h). Die Includes unten sind mit Include-Guards abgesichert.
// Oeffentlich fuer YAML-Lambdas (globaler Namensraum): i2cs_setup, i2cs_loop, i2cs_status,
// i2cs_ota_begin, i2cs_ota_error, i2cs_lwz_ha_setup.
//
// Die Pins SDA/SCL treibt der I2C-Controller open-drain, nie als GPIO-Ausgang (harte Regel: SDA/SCL
// nie ausserhalb des I2C-Protokolls treiben). GPIO18 (ISO1042 TXD) fasst der Glue nicht an.
// Im Test-Build haelt can_idle.h ihn High (rezessiv).
#ifndef I2CS_LWZ_H
#define I2CS_LWZ_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <string>

#include "sdkconfig.h"
#include "esp_attr.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "soc/gpio_reg.h"
#include "soc/gpio_periph.h"
#include "soc/soc.h"

#include "debug_json.h"
#include "unix_time.h"
#include "i2c_frame.h"
#include "i2c_slave_rx.h"
#include "i2c_scan.h"
#include "i2c_key.h"
#include "lwz_ha.h"
#include "lwz_ctl.h"
#include "crash_trace.h"

namespace i2cs {

// availability retained? mk1-i2c: ja (Last Will setzt offline). Produktiv-Paket: -DI2CS_AVAIL_RETAIN=false,
// weil dort kein Last Will auf stiebel-i2c/availability liegt (sonst bliebe nach Ausfall "online" stehen).
#ifndef I2CS_AVAIL_RETAIN
#define I2CS_AVAIL_RETAIN true
#endif

static const uint32_t kPubIntervalMs = 50;   // hoechstens ein rx-Batch pro Intervall
static const uint32_t kStuckMs = 100;        // SDA Low + SCL High so lange: Slave haengt, neu anlegen
static const size_t kPubMaxFrames = 16;      // Frames je rx-Batch
static const size_t kPubMaxBytes = 3500;     // Payload-Grenze je rx-Batch
static const size_t kOutRingSize = 256;      // Ausgabering zwischen Slave-Ring und MQTT
static const size_t kTxBufLen = 4096;        // Puffer fuer einen rx-Batch
static const size_t kOneFrameLen = 320;      // ein Frame als JSON (max. ca. 250 Zeichen)
static const char kHex[] = "0123456789abcdef";

// ---------- Zustand (nur Haupt-Loop und MQTT-Callbacks, kein Mutex) ----------
static bool g_setup_done = false;
static bool g_pullup = true;
static int g_slave_addr = 42;
static int g_sda = 21;
static int g_scl = 17;
static int g_txd = 18;
static volatile bool g_sniff_on = true;        // Start: on=true
static volatile bool g_mode_changed = true;    // Start: mode "changed" (true), "all" (false)
static ChangeFilter g_filter;
static uint32_t g_out_dropped = 0;
static uint32_t g_stuck_since = 0;     // ms seit SDA Low / SCL High beobachtet (0 = nicht)
static uint32_t g_slave_recover = 0;   // Anzahl Waechter-Neustarts des Slaves
static bool g_pins_set = false;        // Pins geprueft, Waechter darf GPIO_IN_REG lesen
static bool g_ota_active = false;      // OTA laeuft: Slave abgebaut, kein Waechter, kein Neuanlegen
static uint32_t g_last_pub_ms = 0;
static char g_tx_buf[kTxBufLen];

// Ausgabering: fertige Frames bis zur Veroeffentlichung. Nur Haupt-Loop.
struct OutRing {
  Frame buf[kOutRingSize];
  size_t head;
  size_t n;

  bool push(const Frame &f) {
    if (n >= kOutRingSize) return false;
    buf[(head + n) % kOutRingSize] = f;
    ++n;
    return true;
  }
  const Frame &at(size_t i) const { return buf[(head + i) % kOutRingSize]; }
  void drop(size_t k) {
    if (k > n) k = n;
    head = (head + k) % kOutRingSize;
    n -= k;
  }
};
static OutRing g_out;

// ---------- Hilfsfunktionen ----------

// Haengt einen String an b an. false wenn er nicht mehr passt (inkl. NUL).
static bool app_s(char *b, size_t cap, size_t &pos, const char *s) {
  const size_t n = strlen(s);
  if (pos + n >= cap) return false;
  memcpy(b + pos, s, n);
  pos += n;
  b[pos] = '\0';
  return true;
}

// Haengt eine Dezimalzahl an b an.
static bool app_u(char *b, size_t cap, size_t &pos, unsigned long long v) {
  char t[24];
  const int n = snprintf(t, sizeof t, "%llu", v);
  if (n < 0 || (size_t)n >= sizeof t) return false;
  return app_s(b, cap, pos, t);
}

static bool mqtt_ready() {
  return ::esphome::mqtt::global_mqtt_client != nullptr && ::esphome::mqtt::global_mqtt_client->is_connected();
}

static void mqtt_pub(const char *topic, const char *payload, size_t len, uint8_t qos, bool retain) {
  if (!mqtt_ready()) return;
  const uint16_t prev = ct::g_tr.phase;
  ct::set(ct::P_MQTT_PUB, prev);
  ct::g_tr.pubs = ct::g_tr.pubs + 1;
  ::esphome::mqtt::global_mqtt_client->publish(topic, payload, len, qos, retain);
  ct::set(prev);
}

static bool pins_valid(int sda, int scl, int txd) {
  if (sda < 0 || sda > 31 || scl < 0 || scl > 31 || txd > 31) return false;  // txd < 0 = GPIO nicht anfassen (Produktiv: TWAI)
  return sda != scl && (txd < 0 || (txd != sda && txd != scl));
}

// ---------- MQTT-Kommandos (nur Flags und Zustand, keine Arbeit) ----------

// {"on":true,"mode":"all"}  fehlende Felder behalten den Wert. Ungueltiges Feld: ganzes Kommando verworfen.
static void on_cmd_sniff(const std::string &topic, const std::string &payload) {
  (void)topic;
  const char *json = payload.c_str();
  bool on = g_sniff_on;
  bool changed = g_mode_changed;
  if (mkdbg::dj_find_value(json, "on") != nullptr && !mkdbg::dj_get_bool(json, "on", on)) {
    ESP_LOGW("i2cs", "cmd/sniff: Feld on ungueltig, ignoriert");
    return;
  }
  if (mkdbg::dj_find_value(json, "mode") != nullptr) {
    char buf[16];
    const char *p = mkdbg::dj_find_value(json, "mode");
    if (!mkdbg::dj_read_token(p, buf, sizeof buf)) {
      ESP_LOGW("i2cs", "cmd/sniff: Feld mode ungueltig, ignoriert");
      return;
    }
    if (strcmp(buf, "all") == 0) {
      changed = false;
    } else if (strcmp(buf, "changed") == 0) {
      changed = true;
    } else {
      ESP_LOGW("i2cs", "cmd/sniff: mode unbekannt, ignoriert");
      return;
    }
  }
  g_sniff_on = on;
  g_mode_changed = changed;
  g_filter.clear();
  ESP_LOGI("i2cs", "cmd/sniff: on=%d mode=%s", (int)on, changed ? "changed" : "all");
}

// {"from":"0x08","to":"0x77"}  beide optional (Zahl oder "0x.." String), Standard 0x08..0x77.
// Startet den Bus-Scan (i2c_scan.h) als eigenen Task. Laeuft schon einer: abgelehnt, mit Fehlerergebnis.
static void on_cmd_scan(const std::string &topic, const std::string &payload) {
  (void)topic;
  const char *json = payload.c_str();
  uint32_t from = kScanMinAddr, to = kScanMaxAddr;
  const char *why = nullptr;
  if (mkdbg::dj_find_value(json, "from") != nullptr && !mkdbg::dj_get_uint(json, "from", 0x7F, from)) why = "from ungueltig";
  if (!why && mkdbg::dj_find_value(json, "to") != nullptr && !mkdbg::dj_get_uint(json, "to", 0x7F, to)) why = "to ungueltig";
  if (!why && (from < kScanMinAddr || to > kScanMaxAddr || from > to)) why = "Bereich ausserhalb 0x08..0x77";
  if (!why && !g_slave_up) why = "Slave nicht aktiv";
  if (!why && g_scan_running) why = "Scan laeuft schon";
  if (!why && g_ota_active) why = "OTA laeuft";
  if (!why && !scan_start(g_sda, g_scl, g_slave_addr, g_pullup, (uint8_t)from, (uint8_t)to)) why = "Task nicht startbar";
  if (why != nullptr) {
    ESP_LOGW("i2cs", "cmd/scan abgelehnt: %s", why);
    char b[160];
    const int n = snprintf(b, sizeof b, "{\"found\":[],\"nack\":0,\"arb_lost\":0,\"retries\":0,\"ms\":0,\"ok\":false,\"err\":\"%s\"}", why);
    if (n > 0 && (size_t)n < sizeof b) mqtt_pub("stiebel-i2c/scan/result", b, (size_t)n, 0, false);
    return;
  }
  ESP_LOGI("i2cs", "cmd/scan: 0x%02x..0x%02x gestartet", (unsigned)from, (unsigned)to);
}

// {"code":2,"count":1,"gap_ms":200}  code 1..13 (Tastencodes kdarnok), count 1..5, gap_ms 30..2000.
// Sendet den Tastendruck wie das Bedienteil (i2c_key.h). Ergebnis auf stiebel-i2c/key/result.
static void on_cmd_key(const std::string &topic, const std::string &payload) {
  (void)topic;
  const char *json = payload.c_str();
  uint32_t code = 0, count = 1, gap = 200;
  const char *why = nullptr;
  if (!mkdbg::dj_get_uint(json, "code", 0xFF, code) || code < 1 || code > 13) why = "code ungueltig (1..13)";
  if (!why && mkdbg::dj_find_value(json, "count") != nullptr &&
      (!mkdbg::dj_get_uint(json, "count", 0xFF, count) || count < 1 || count > (uint32_t)kKeyMaxCount)) why = "count ungueltig (1..5)";
  if (!why && mkdbg::dj_find_value(json, "gap_ms") != nullptr &&
      (!mkdbg::dj_get_uint(json, "gap_ms", 0xFFFF, gap) || gap < 30 || gap > 2000)) why = "gap_ms ungueltig (30..2000)";
  if (!why && !g_slave_up) why = "Slave nicht aktiv";
  if (!why && g_scan_running) why = "Bus belegt (Scan oder Taste laeuft)";
  if (!why && g_ota_active) why = "OTA laeuft";
  if (!why && !key_start(g_sda, g_scl, g_slave_addr, g_pullup, (uint8_t)code, (uint8_t)count, gap)) why = "Task nicht startbar";
  if (why != nullptr) {
    ESP_LOGW("i2cs", "cmd/key abgelehnt: %s", why);
    char b[160];
    const int n = snprintf(b, sizeof b, "{\"code\":%lu,\"sent\":0,\"nack\":0,\"arb_lost\":0,\"ms\":0,\"ok\":false,\"err\":\"%s\"}",
                           (unsigned long)code, why);
    if (n > 0 && (size_t)n < sizeof b) mqtt_pub("stiebel-i2c/key/result", b, (size_t)n, 0, false);
    return;
  }
  ESP_LOGI("i2cs", "cmd/key: code=%lu count=%lu gap=%lu ms gestartet", (unsigned long)code, (unsigned long)count,
           (unsigned long)gap);
}

static void key_publish() {
  char b[200];
  const int n = snprintf(b, sizeof b,
                         "{\"code\":%u,\"count\":%u,\"sent\":%u,\"nack\":%u,\"arb_lost\":%lu,\"ms\":%lu,\"ok\":%s,\"err\":\"%s\"}",
                         (unsigned)g_key.code, (unsigned)g_key.count, (unsigned)g_key.sent, (unsigned)g_key.nack,
                         (unsigned long)g_key.arb_lost, (unsigned long)g_key.ms, g_key.ok ? "true" : "false", g_key.err);
  if (n > 0 && (size_t)n < sizeof b) mqtt_pub("stiebel-i2c/key/result", b, (size_t)n, 0, false);
  ESP_LOGI("i2cs", "Taste fertig: %s", b);
}

// Veroeffentlicht das Scan-Ergebnis (Haupt-Loop). Danach darf ein neuer Scan starten.
static void scan_publish() {
  static char b[1536];
  size_t pos = 0;
  b[0] = '\0';
  bool ok = app_s(b, sizeof b, pos, "{\"found\":[");
  for (uint8_t i = 0; ok && i < g_scan.n_found; ++i) {
    if (i > 0) ok = app_s(b, sizeof b, pos, ",");
    ok = ok && app_u(b, sizeof b, pos, g_scan.found[i]);
  }
  ok = ok && app_s(b, sizeof b, pos, "],\"failed\":[");
  for (uint8_t i = 0; ok && i < g_scan.n_failed; ++i) {
    if (i > 0) ok = app_s(b, sizeof b, pos, ",");
    ok = ok && app_u(b, sizeof b, pos, g_scan.failed[i]);
  }
  ok = ok && app_s(b, sizeof b, pos, "],\"from\":") && app_u(b, sizeof b, pos, g_scan.from) &&
       app_s(b, sizeof b, pos, ",\"to\":") && app_u(b, sizeof b, pos, g_scan.to) &&
       app_s(b, sizeof b, pos, ",\"nack\":") && app_u(b, sizeof b, pos, g_scan.nack) &&
       app_s(b, sizeof b, pos, ",\"arb_lost\":") && app_u(b, sizeof b, pos, g_scan.arb_lost) &&
       app_s(b, sizeof b, pos, ",\"retries\":") && app_u(b, sizeof b, pos, g_scan.retries) &&
       app_s(b, sizeof b, pos, ",\"ms\":") && app_u(b, sizeof b, pos, g_scan.ms) &&
       app_s(b, sizeof b, pos, g_scan.ok ? ",\"ok\":true" : ",\"ok\":false");
  if (ok && g_scan.err[0] != '\0') {  // err enthaelt nur eigene ASCII-Texte ohne Anfuehrungszeichen
    ok = app_s(b, sizeof b, pos, ",\"err\":\"") && app_s(b, sizeof b, pos, g_scan.err) && app_s(b, sizeof b, pos, "\"");
  }
  ok = ok && app_s(b, sizeof b, pos, "}");
  if (ok) mqtt_pub("stiebel-i2c/scan/result", b, pos, 0, false);
  ESP_LOGI("i2cs", "Scan fertig: %u gefunden, nack=%lu arb_lost=%lu retries=%lu ms=%lu ok=%d %s", (unsigned)g_scan.n_found,
           (unsigned long)g_scan.nack, (unsigned long)g_scan.arb_lost, (unsigned long)g_scan.retries,
           (unsigned long)g_scan.ms, (int)g_scan.ok, g_scan.err);
  for (uint8_t i = 0; i < g_scan.n_found; ++i) ESP_LOGI("i2cs", "Scan: Adresse 0x%02x antwortet", (unsigned)g_scan.found[i]);
}

// ---------- rx-Batch (JSON, feste Puffer) ----------

// Ein Frame als JSON: {"t":..,"u":..,"d":"hex","a":"+-..","f":n}
static size_t format_one(const Frame &f, char *out, size_t cap) {
  size_t pos = 0;
  out[0] = '\0';
  bool ok = app_s(out, cap, pos, "{\"t\":") && app_u(out, cap, pos, f.t_ms) && app_s(out, cap, pos, ",\"u\":") &&
            app_u(out, cap, pos, f.unix_ms) && app_s(out, cap, pos, ",\"d\":\"");
  for (uint8_t i = 0; ok && i < f.len; ++i) {
    char h[3];
    h[0] = kHex[f.bytes[i] >> 4];
    h[1] = kHex[f.bytes[i] & 15u];
    h[2] = '\0';
    ok = app_s(out, cap, pos, h);
  }
  ok = ok && app_s(out, cap, pos, "\",\"a\":\"");
  for (uint8_t i = 0; ok && i < f.len; ++i) {
    ok = app_s(out, cap, pos, ((f.ack_bits >> i) & 1u) ? "+" : "-");
  }
  ok = ok && app_s(out, cap, pos, "\",\"f\":") && app_u(out, cap, pos, f.flags) && app_s(out, cap, pos, "}");
  return ok ? pos : 0;
}

// Baut ein JSON-Array aus den vordersten Frames der Ausgabering. count_out = Anzahl eingepackter Frames.
// Limits: kPubMaxFrames Frames und kPubMaxBytes Payload. Frames bleiben in der Ring bis drop().
static size_t format_batch(size_t &count_out) {
  size_t pos = 0;
  size_t cnt = 0;
  static char one[kOneFrameLen];
  bool ok = app_s(g_tx_buf, kTxBufLen, pos, "[");
  while (ok && cnt < kPubMaxFrames && cnt < g_out.n) {
    const size_t m = format_one(g_out.at(cnt), one, sizeof one);
    if (m == 0) {  // nicht formatierbar (sollte nicht vorkommen): verwerfen
      g_out_dropped++;
      g_out.drop(1);
      continue;
    }
    const size_t sep = (cnt > 0) ? 1 : 0;
    if (pos + sep + m + 2 > kPubMaxBytes) break;
    if (sep && !app_s(g_tx_buf, kTxBufLen, pos, ",")) break;
    if (!app_s(g_tx_buf, kTxBufLen, pos, one)) break;
    ++cnt;
  }
  ok = ok && app_s(g_tx_buf, kTxBufLen, pos, "]");
  count_out = cnt;
  return ok ? pos : 0;
}

// ---------- Oeffentliche Funktionen (YAML) ----------

// Einmal in on_boot (priority -100). Abos cmd/sniff und cmd/scan, Pruefung der Pins, Slave-Setup.
static void setup_impl(int sda_gpio, int scl_gpio, int txd_gpio, bool internal_pullup, int slave_addr) {
  if (g_setup_done) return;
  ct::set(ct::P_SETUP);
  if (ct::g_have_last) {
    const ct::Trace &l = ct::g_last;
    ESP_LOGW("i2cs", "Absturz vorher (reset_reason %d): phase=%u sub=%u isr_in=%lu isr_cnt=%lu up_ms=%lu loops=%lu pubs=%lu",
             ct::g_reason, (unsigned)l.phase, (unsigned)l.sub, (unsigned long)l.isr_in, (unsigned long)l.isr_cnt,
             (unsigned long)l.up_ms, (unsigned long)l.loops, (unsigned long)l.pubs);
  }
  g_setup_done = true;

  // MQTT-Abo zuerst: Status antwortet auch bei Pin-Fehler.
  if (::esphome::mqtt::global_mqtt_client != nullptr) {
    ::esphome::mqtt::global_mqtt_client->subscribe("stiebel-i2c/cmd/sniff", on_cmd_sniff, 0);
    ::esphome::mqtt::global_mqtt_client->subscribe("stiebel-i2c/cmd/scan", on_cmd_scan, 0);
    ::esphome::mqtt::global_mqtt_client->subscribe("stiebel-i2c/cmd/key", on_cmd_key, 0);
  } else {
    ESP_LOGE("i2cs", "MQTT nicht konfiguriert, keine Kommandos");
  }

  g_pullup = internal_pullup;
  g_slave_addr = slave_addr;
  g_sda = sda_gpio;
  g_scl = scl_gpio;
  g_txd = txd_gpio;
  g_slave_up = false;
  if (!pins_valid(sda_gpio, scl_gpio, txd_gpio)) {
    ESP_LOGE("i2cs", "Ungueltige Pins (SDA/SCL/TXD 0..31, paarweise verschieden)");
    return;
  }
  if (slave_addr < 8 || slave_addr > 119) {
    ESP_LOGE("i2cs", "Ungueltige Slave-Adresse %d", slave_addr);
    return;
  }
  g_pins_set = true;
  if (!slave_setup(sda_gpio, scl_gpio, slave_addr, internal_pullup)) return;
  g_slave_up = true;
  ESP_LOGI("i2cs", "I2C-Slave aktiv: Adresse=%d General Call an, SDA=%d SCL=%d pullup=%d", slave_addr, sda_gpio,
           scl_gpio, (int)internal_pullup);
}

// rx-Batch, hoechstens alle 50 ms. Ohne MQTT-Verbindung werden Frames verworfen (gezaehlt).
static void publish_batch(uint32_t ms) {
  ct::set(ct::P_BATCH);
  if (g_out.n > 0 && (ms - g_last_pub_ms) >= kPubIntervalMs) {
    g_last_pub_ms = ms;
    if (!mqtt_ready()) {
      g_out_dropped += (uint32_t)g_out.n;
      g_out.drop(g_out.n);
    } else {
      size_t cnt = 0;
      const size_t len = format_batch(cnt);
      if (cnt > 0) {
        mqtt_pub("stiebel-i2c/rx", g_tx_buf, len, 0, false);
        g_out.drop(cnt);
      } else {
        g_out_dropped++;
        g_out.drop(1);
      }
    }
  }
}

// Waechter (30.10.2026 beobachtet): Startet der Slave mitten in einem LWZ-Frame, kann er im ACK-Bit haengen
// und SDA dauerhaft Low halten (SCL High). Dann kann die LWZ keinen START mehr senden, der Bus steht.
// Nachpruefung: 2 ms lang ohne jede Flanke in genau diesem Zustand (bei 13,5 kHz sind das ~27 Takte).
static bool bus_stuck_confirmed() {
  const uint32_t m = (1u << g_sda) | (1u << g_scl);
  const uint32_t want = (1u << g_scl);
  const int64_t t0 = esp_timer_get_time();
  while (esp_timer_get_time() - t0 < 2000) {
    if ((REG_READ(GPIO_IN_REG) & m) != want) return false;
  }
  return true;
}

// Slave per Loeschen/Neuanlegen zuruecksetzen (Peripheral gibt SDA frei, kein Treiben). Zaehler bleiben.
static void slave_recover() {
  ct::set(ct::P_RECOVER);
  const uint32_t f = g_slv.frames, b = g_slv.bytes, o = g_slv.overflow, e = g_slv.errors;
  g_slave_up = false;
  if (!slave_teardown()) return;
  if (slave_setup(g_sda, g_scl, g_slave_addr, g_pullup)) {
    g_slv.frames = f;
    g_slv.bytes = b;
    g_slv.overflow = o;
    g_slv.errors = e;
    g_slave_up = true;
  }
  ++g_slave_recover;
  ESP_LOGW("i2cs", "SDA haengt Low (SCL High): Slave neu angelegt (%lu), aktiv=%d", (unsigned long)g_slave_recover,
           (int)g_slave_up);
}

static void stuck_check(uint32_t ms) {
  ct::set(ct::P_STUCK);
  if (g_scan_running || g_ota_active) {
    g_stuck_since = 0;
    return;
  }
  const uint32_t m = (1u << g_sda) | (1u << g_scl);
  if ((REG_READ(GPIO_IN_REG) & m) != (1u << g_scl)) {
    g_stuck_since = 0;
    return;
  }
  if (g_stuck_since == 0) {
    g_stuck_since = ms ? ms : 1u;
  } else if (ms - g_stuck_since >= kStuckMs && bus_stuck_confirmed()) {
    g_stuck_since = 0;
    slave_recover();
  }
}

// OTA-Beginn: Slave abbauen, damit waehrend des Flash-Schreibens kein I2C-Interrupt laeuft und der Bus beim
// Neustart frei ist (der ESP haelt dann kein ACK). Die LWZ sendet weiter, ihre Frames gehen nur verloren.
static void ota_begin_impl() {
  ct::set(ct::P_OTA);
  if (g_ota_active) return;
  g_ota_active = true;
  if (g_scan_running) return;  // Scan-Task baut selbst ab und wieder auf
  g_slave_up = false;
  if (slave_teardown()) ESP_LOGI("i2cs", "OTA: Slave abgebaut");
}

// OTA-Fehler: Slave wie beim Boot wieder anlegen (Zaehler bleiben).
static void ota_error_impl() {
  if (!g_ota_active) return;
  g_ota_active = false;
  if (g_scan_running || g_slave_up || !g_pins_set) return;
  const uint32_t f = g_slv.frames, b = g_slv.bytes, o = g_slv.overflow, e = g_slv.errors;
  if (slave_setup(g_sda, g_scl, g_slave_addr, g_pullup)) {
    g_slv.frames = f;
    g_slv.bytes = b;
    g_slv.overflow = o;
    g_slv.errors = e;
    g_slave_up = true;
    ESP_LOGI("i2cs", "OTA-Fehler: Slave wieder aktiv");
  }
}

// Tastenfolge fuer lwz_ctl.h (Pins und Slave wie beim Setup).
static bool ctl_send(const uint8_t *codes, uint8_t n, uint32_t gap_ms) {
  return key_start_seq(g_sda, g_scl, g_slave_addr, g_pullup, codes, n, gap_ms);
}

// Haupt-Loop, interval 20 ms. Entleert den Slave-Ring, filtert, publiziert rx-Batches.
static void loop_impl() {
  if (!g_setup_done) return;
  ct::set(ct::P_LOOP);
  ct::g_tr.up_ms = (uint32_t)::esphome::millis();
  ct::g_tr.loops = ct::g_tr.loops + 1;
  if (g_scan_running && __atomic_load_n(&g_scan_done, __ATOMIC_ACQUIRE)) {
    ct::set(ct::P_SCAN_PUB);
    scan_publish();
    g_scan_done = false;
    g_scan_running = false;
  }
  if (g_scan_running && __atomic_load_n(&g_key_done, __ATOMIC_ACQUIRE)) {
    key_publish();
    g_key_done = false;
    g_scan_running = false;
  }
  const uint32_t ms = (uint32_t)::esphome::millis();
  if (g_pins_set) stuck_check(ms);
  if (!g_slave_up) return;

  const uint64_t unix_now = mkdbg::unix_ms();
  Frame f;
  size_t k = 0;
  while (k < kSlvRingSlots && slave_pop(f)) {
    ++k;
    f.unix_ms = 0;
    if (unix_now != 0) {
      // Vorzeichenbehaftet: Frames, die nach dem Lesen von ms ankommen, haben t_ms > ms (sonst Ueberlauf um 2^32 ms).
      int32_t age = (int32_t)(ms - f.t_ms);
      if (age < 0) age = 0;
      if (unix_now > (uint64_t)age) f.unix_ms = unix_now - (uint64_t)age;
    }
    lwzha::on_frame(f, ms);     // LWZ-Werte fuer HA (nur aktiv nach i2cs_lwz_ha_setup)
    if (!g_sniff_on) continue;  // Frames verworfen, Slave-Zaehler laufen weiter
    if (!g_mode_changed || g_filter.pass(f)) {
      if (!g_out.push(f)) ++g_out_dropped;
    }
  }
  publish_batch(ms);
  lwzha::poll(ms);
  lwzctl::poll(ms, g_scan_running || g_ota_active || !g_slave_up, ctl_send);
  ct::set(ct::P_NONE);
}

// Status alle 5 s: status-JSON, availability (retained).
static void status_impl() {
  if (!g_setup_done) return;
  ct::set(ct::P_STATUS);

  const uint32_t ms = (uint32_t)::esphome::millis();

  // Aktueller Pegel von SDA/SCL (nur lesen, Hardware-Diagnose).
  const uint32_t in = REG_READ(GPIO_IN_REG);
  const int lvl_sda = (int)((in >> g_sda) & 1u);
  const int lvl_scl = (int)((in >> g_scl) & 1u);

  const uint32_t fill = g_slv.head - g_slv.tail;
  char buf[640];
  const int n = snprintf(
      buf, sizeof buf,
      "{\"fw\":\"i2c\",\"uptime_s\":%lu,\"frames\":%lu,\"frames_dropped\":%lu,"
      "\"ring_fill\":%lu,\"ring_cap\":%lu,\"heap\":%lu,\"pullup\":%s,\"sniff\":%s,"
      "\"mode\":\"%s\",\"ntp\":%s,\"scan_running\":%s,\"lvl_sda\":%d,\"lvl_scl\":%d,"
      "\"slave_addr\":%d,\"slave_frames\":%lu,\"slave_bytes\":%lu,"
      "\"slave_overflow\":%lu,\"slave_errors\":%lu,\"slave_recover\":%lu,\"reset_reason\":%d,\"lwz_values\":%lu}",
      (unsigned long)(ms / 1000u), (unsigned long)g_slv.frames, (unsigned long)g_out_dropped,
      (unsigned long)fill, (unsigned long)kSlvRingSlots, (unsigned long)esp_get_free_heap_size(),
      g_pullup ? "true" : "false", g_sniff_on ? "true" : "false", g_mode_changed ? "changed" : "all",
      mkdbg::ntp_synced() ? "true" : "false", g_scan_running ? "true" : "false", lvl_sda, lvl_scl, g_slave_addr, (unsigned long)g_slv.frames,
      (unsigned long)g_slv.bytes, (unsigned long)g_slv.overflow, (unsigned long)g_slv.errors,
      (unsigned long)g_slave_recover, (int)esp_reset_reason(), (unsigned long)lwzha::g_values);
  if (n > 0 && (size_t)n < sizeof buf) mqtt_pub("stiebel-i2c/status", buf, (size_t)n, 0, false);
  // Absturz-Spur des vorherigen Starts (crash_trace.h), solange bis zum naechsten sauberen Start
  if (ct::g_have_last) {
    const ct::Trace &l = ct::g_last;
    char cb[256];
    const int m = snprintf(cb, sizeof cb,
        "{\"reset_reason\":%d,\"boot\":%lu,\"phase\":%u,\"sub\":%u,\"isr_in\":%lu,\"isr_cnt\":%lu,"
        "\"up_ms\":%lu,\"loops\":%lu,\"pubs\":%lu}",
        ct::g_reason, (unsigned long)l.boot, (unsigned)l.phase, (unsigned)l.sub, (unsigned long)l.isr_in,
        (unsigned long)l.isr_cnt, (unsigned long)l.up_ms, (unsigned long)l.loops, (unsigned long)l.pubs);
    if (m > 0 && (size_t)m < sizeof cb) mqtt_pub("stiebel-i2c/crash", cb, (size_t)m, 0, true);
  }
  ct::set(ct::P_NONE);
  mqtt_pub("stiebel-i2c/availability", "online", 6, 1, I2CS_AVAIL_RETAIN);
}

}  // namespace i2cs

// Globale Namen fuer YAML-Lambdas. slave_addr: 7-Bit-Adresse (42).
void i2cs_setup(int sda_gpio, int scl_gpio, int txd_gpio, bool internal_pullup, int slave_addr) {
  i2cs::setup_impl(sda_gpio, scl_gpio, txd_gpio, internal_pullup, slave_addr);
}

void i2cs_loop() { i2cs::loop_impl(); }

void i2cs_status() { i2cs::status_impl(); }

// LWZ-Werte als HA-Geraet (Produktiv-Paket lwz_i2c). prefix z. B. "lwz180", avail = availability_topic.
void i2cs_lwz_ha_setup(const char *prefix, const char *dev_name, const char *model, const char *avail) {
  i2cs::lwzha::setup(prefix, dev_name, model, avail);
  i2cs::lwzctl::setup();  // HA-Steuerung Luefterstufe / Intensivlueftung
}

void i2cs_ota_begin() { i2cs::ota_begin_impl(); }

void i2cs_ota_error() { i2cs::ota_error_impl(); }

#endif  // I2CS_LWZ_H
