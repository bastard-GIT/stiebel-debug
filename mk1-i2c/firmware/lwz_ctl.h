// lwz_ctl.h - LWZ 180 aus Home Assistant steuern: Luefterstufe (select) und Intensivlueftung (switch).
// Header-only, C++11. Aktiv nur nach lwzctl::setup() (Produktiv-Paket lwz_i2c ueber i2cs_lwz_ha_setup).
// Die Steuerung drueckt Tasten wie das Bedienteil (i2c_key.h) und prueft das Ergebnis im Display-Paket
// (lwz_ha.h: g_disp_level, g_disp_pv). Gesendet wird nur, wenn das Display den Grundzustand zeigt
// (kein Menue offen), sonst wuerde das Drehrad im Menue blaettern oder Werte aendern.
// Erkenntnisse (Test 2026-10-31, referenzen/dokumentation/erkenntnisse_2026-10-31_lwz-luefterstufe-i2c.md):
//  - Stufe: Drehrad-Burst 5x Code 02 (auf) bzw. 01 (ab), 100 ms Abstand = 1 Stufe. Etwa jeder vierte Burst
//    wirkt nicht (meist nach Richtungswechsel), deshalb Regelkreis mit Wiederholung.
//  - Stufe 3 ist per Drehrad nicht erreichbar (Test 31.10.: 8 Bursts ohne Wirkung), nur als Intensivlueftung.
//  - Intensivlueftung: Power-Vent lang = 08, nach 500 ms 0c (wie das Bedienteil, Mitschnitt 30.10.), am
//    31.10. per ESP getestet. Startet nur (laeuft bis Timerende), Anzeige Icon pv2 = seg[26] Bit 0, Stufe 3.
//    Vorzeitig beenden nur durch Verstellen der Stufe (Nutzer 31.10.): hier Drehrad-Burst ab.
// MQTT: <prefix>/luefterstufe/set ("0".."2", "3" = Intensivlueftung an), <prefix>/intensivlueftung/set ("ON"/"OFF"),
//       <prefix>/intensivlueftung/state (retained), <prefix>/steuerung/result (Text, nicht retained).
#ifndef I2CS_LWZ_CTL_H
#define I2CS_LWZ_CTL_H

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <string>

#include "esp_log.h"
#include "lwz_ha.h"

namespace i2cs {
namespace lwzctl {

typedef bool (*SendFn)(const uint8_t *codes, uint8_t n, uint32_t gap_ms);

static const uint32_t kDeadlineMs = 60000;   // Auftrag spaetestens nach so langer Zeit aufgeben
static const uint32_t kFreshMs = 10000;      // Display-Zustand gilt so lange als aktuell
static const uint32_t kLevelWaitMs = 1500;   // nach einem Burst mindestens so lange auf das Display warten
static const uint32_t kPvWaitMs = 2500;      // nach Power Vent mindestens so lange warten
static const uint8_t kLevelMaxTries = 8;     // Bursts je Auftrag
static const uint8_t kPvMaxTries = 3;
static const uint32_t kPvRepubMs = 300000;   // pv-State spaetestens alle 5 min wiederholen

static bool g_on = false;
static bool g_was_connected = false;
static int g_tgt_level = -1;     // -1 = kein Auftrag
static int g_tgt_pv = -1;        // -1 = kein Auftrag, 0 = aus, 1 = an
static uint8_t g_tries = 0;
static uint32_t g_deadline_ms = 0;
static uint32_t g_next_ms = 0;
static uint32_t g_sent_ms = 0;   // Zeit des letzten Sendens (Display-Paket muss danach kommen)
static bool g_sent = false;
static uint32_t g_wait = 0;      // Wartezeit nach Ende der laufenden Tastenfolge (0 = keine laeuft)
static bool g_pv_pub_valid = false;
static bool g_pv_pub = false;
static uint32_t g_pv_pub_ms = 0;
static volatile bool g_cmd_level = false;  // neuer Auftrag aus dem MQTT-Callback
static volatile bool g_cmd_pv = false;
static volatile int g_cmd_level_val = -1;
static volatile int g_cmd_pv_val = -1;

static uint32_t now_ms() { return (uint32_t)::esphome::millis(); }

static void result(const char *msg) {
  ESP_LOGI("lwzctl", "%s", msg);
  char topic[64];
  snprintf(topic, sizeof topic, "%s/steuerung/result", lwzha::g_prefix);
  lwzha::pub(topic, msg, strlen(msg), false);
}

static void on_set_level(const std::string &topic, const std::string &payload) {
  (void)topic;
  if (payload.size() != 1 || payload[0] < '0' || payload[0] > '3') {
    ESP_LOGW("lwzctl", "luefterstufe/set ungueltig: %s", payload.c_str());
    return;
  }
  const int v = payload[0] - '0';
  if (v == 3) {  // Stufe 3 nur als Intensivlueftung
    g_cmd_pv_val = 1;
    g_cmd_pv = true;
    return;
  }
  g_cmd_level_val = v;
  g_cmd_level = true;
}

static void on_set_pv(const std::string &topic, const std::string &payload) {
  (void)topic;
  int v = -1;
  if (payload == "ON") v = 1;
  if (payload == "OFF") v = 0;
  if (v < 0) {
    ESP_LOGW("lwzctl", "intensivlueftung/set ungueltig: %s", payload.c_str());
    return;
  }
  g_cmd_pv_val = v;
  g_cmd_pv = true;
}

static void setup() {
  if (::esphome::mqtt::global_mqtt_client == nullptr) return;
  char t[64];
  snprintf(t, sizeof t, "%s/luefterstufe/set", lwzha::g_prefix);
  ::esphome::mqtt::global_mqtt_client->subscribe(t, on_set_level, 0);
  snprintf(t, sizeof t, "%s/intensivlueftung/set", lwzha::g_prefix);
  ::esphome::mqtt::global_mqtt_client->subscribe(t, on_set_pv, 0);
  g_on = true;
}

static void send_discovery() {
  const char *p = lwzha::g_prefix;
  char dev[256];
  snprintf(dev, sizeof dev,
           "\"device\":{\"identifiers\":[\"stiebel_eltron_%s\"],\"name\":\"%s\","
           "\"manufacturer\":\"Stiebel Eltron\",\"model\":\"%s\"}",
           p, lwzha::g_dev_name, lwzha::g_model);
  char topic[128];
  char buf[768];
  snprintf(topic, sizeof topic, "homeassistant/select/%s/luefterstufe_einstellen/config", p);
  int n = snprintf(buf, sizeof buf,
                   "{\"name\":\"Lüfterstufe einstellen\",\"unique_id\":\"%s_luefterstufe_einstellen\","
                   "\"object_id\":\"%s_luefterstufe_einstellen\",\"command_topic\":\"%s/luefterstufe/set\","
                   "\"state_topic\":\"%s/luefterstufe/state\",\"options\":[\"0\",\"1\",\"2\",\"3\"],"
                   "\"availability_topic\":\"%s\",\"icon\":\"mdi:fan\",%s}",
                   p, p, p, p, lwzha::g_avail, dev);
  if (n > 0 && (size_t)n < sizeof buf) lwzha::pub(topic, buf, (size_t)n, true);
  snprintf(topic, sizeof topic, "homeassistant/switch/%s/intensivlueftung/config", p);
  n = snprintf(buf, sizeof buf,
               "{\"name\":\"Intensivlüftung\",\"unique_id\":\"%s_intensivlueftung\","
               "\"object_id\":\"%s_intensivlueftung\",\"command_topic\":\"%s/intensivlueftung/set\","
               "\"state_topic\":\"%s/intensivlueftung/state\",\"payload_on\":\"ON\",\"payload_off\":\"OFF\","
               "\"availability_topic\":\"%s\",\"icon\":\"mdi:fan-plus\",%s}",
               p, p, p, p, lwzha::g_avail, dev);
  if (n > 0 && (size_t)n < sizeof buf) lwzha::pub(topic, buf, (size_t)n, true);
}

static void pub_pv(uint32_t ms) {
  char topic[64];
  snprintf(topic, sizeof topic, "%s/intensivlueftung/state", lwzha::g_prefix);
  const char *v = lwzha::g_disp_pv ? "ON" : "OFF";
  lwzha::pub(topic, v, strlen(v), true);
  g_pv_pub = lwzha::g_disp_pv;
  g_pv_pub_valid = true;
  g_pv_pub_ms = ms;
}

static void finish(const char *msg) {
  g_tgt_level = -1;
  g_tgt_pv = -1;
  g_tries = 0;
  g_sent = false;
  g_wait = 0;
  result(msg);
}

// Haupt-Loop. busy: Bus belegt (Scan, Taste, OTA) oder Slave nicht aktiv. send startet eine Tastenfolge.
static void poll(uint32_t ms, bool busy, SendFn send) {
  if (!g_on) return;
  const bool c = lwzha::connected();
  if (c && !g_was_connected) {
    send_discovery();
    g_pv_pub_valid = false;
  }
  g_was_connected = c;

  // Display-Zustand der Intensivlueftung veroeffentlichen
  if (c && lwzha::g_disp_seen &&
      (!g_pv_pub_valid || g_pv_pub != lwzha::g_disp_pv || (uint32_t)(ms - g_pv_pub_ms) >= kPvRepubMs))
    pub_pv(ms);

  // Neue Auftraege uebernehmen (ein neuer Auftrag ersetzt den alten gleicher Art)
  if (g_cmd_level) {
    g_cmd_level = false;
    g_tgt_level = g_cmd_level_val;
    g_tries = 0;
    g_deadline_ms = ms + kDeadlineMs;
    ESP_LOGI("lwzctl", "Auftrag: Luefterstufe %d", g_tgt_level);
  }
  if (g_cmd_pv) {
    g_cmd_pv = false;
    g_tgt_pv = g_cmd_pv_val;
    g_tries = 0;
    g_deadline_ms = ms + kDeadlineMs;
    ESP_LOGI("lwzctl", "Auftrag: Intensivlueftung %s", g_tgt_pv ? "an" : "aus");
  }
  if (g_tgt_level < 0 && g_tgt_pv < 0) return;
  if (busy) return;
  if (g_wait != 0) {  // Tastenfolge gerade fertig: ab jetzt warten, nur spaetere Display-Pakete zaehlen
    g_sent_ms = ms;
    g_next_ms = ms + g_wait;
    g_wait = 0;
    return;
  }
  if ((int32_t)(ms - g_next_ms) < 0) return;
  if ((int32_t)(ms - g_deadline_ms) >= 0) {
    finish("Abbruch: Zeitlimit (Display nicht im Grundzustand oder keine Wirkung)");
    return;
  }
  // Aktueller Display-Zustand noetig, nach dem Senden ein Paket, das danach kam
  const bool fresh = lwzha::g_disp_seen && (uint32_t)(ms - lwzha::g_disp_ms) < kFreshMs &&
                     (!g_sent || (int32_t)(lwzha::g_disp_ms - g_sent_ms) > 0);
  if (!fresh) {
    g_next_ms = ms + 200;
    return;
  }

  if (g_tgt_pv >= 0) {
    if ((int)lwzha::g_disp_pv == g_tgt_pv) {
      finish(g_tgt_pv ? "Intensivlueftung an" : "Intensivlueftung aus");
      return;
    }
    if (g_tries >= kPvMaxTries) {
      finish("Abbruch: Intensivlueftung schaltet nicht");
      return;
    }
    // An: Power Vent lang. Aus: geht nur ueber Verstellen der Stufe, also Drehrad-Burst ab.
    static const uint8_t kPv[2] = {0x08, 0x0c};
    static const uint8_t kDown[5] = {0x01, 0x01, 0x01, 0x01, 0x01};
    const bool ok = g_tgt_pv ? send(kPv, 2, 500) : send(kDown, 5, 100);
    if (!ok) return;  // Bus belegt: naechster Durchlauf
    ++g_tries;
    g_sent = true;
    g_wait = kPvWaitMs;
    return;
  }

  // Waehrend Intensivlueftung zeigt das Display 3: Burst ab beendet sie, danach normal weiterregeln.
  const int lvl = lwzha::g_disp_level;
  if (lvl == g_tgt_level && !lwzha::g_disp_pv) {
    char b[48];
    snprintf(b, sizeof b, "Luefterstufe %d erreicht (%u Bursts)", lvl, (unsigned)g_tries);
    finish(b);
    return;
  }
  if (g_tries >= kLevelMaxTries) {
    finish("Abbruch: Luefterstufe aendert sich nicht");
    return;
  }
  const uint8_t code = (g_tgt_level > lvl && !lwzha::g_disp_pv) ? 0x02 : 0x01;
  const uint8_t burst[5] = {code, code, code, code, code};
  if (!send(burst, 5, 100)) return;
  ++g_tries;
  g_sent = true;
  g_wait = kLevelWaitMs;
}

}  // namespace lwzctl
}  // namespace i2cs

#endif  // I2CS_LWZ_CTL_H
