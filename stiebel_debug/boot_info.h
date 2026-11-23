// boot_info.h - Neustart-Infos des Mk1 als HA-Entitaeten (Geraet Waermepumpe, Diagnose).
//
// Einmal pro Start, sobald MQTT verbunden und die Uhrzeit (SNTP) gueltig ist:
//   - Letzter Neustart (timestamp): aendert sich bei jedem Start, im Verlauf ist jeder Neustart sichtbar.
//   - Neustart Grund (Text): esp_reset_reason() im Klartext. Attribute: Code, Startzaehler,
//     Laufzeit davor und bei Absturz die Spur aus crash_trace.h (Phase, ISR, Zaehler).
//   - Laufzeit vor Neustart (s): letzter Stand von crash_trace up_ms vor dem Reset.
//   - Neustarts (Zaehler seit Stromausfall, aus dem RTC-Speicher; kein Flash-Schreiben).
//   - Firmware Version (substitution fw_version, JJJJ.MM.N) und Firmware Build (Datum/Uhrzeit des Builds,
//     Ortszeit des Build-PCs aus __DATE__/__TIME__). Die Version steht auch als sw_version am Geraet.
// Alles retained: HA hat die Werte auch nach einem HA-Neustart.
//
// Herkunft: bis 2026-11-23 im Fork ha-stiebel-control (wpla07-modular), jetzt Teil von stiebel-debug.
// Voraussetzungen aus der Waermepumpen-Firmware: HA_TOPIC_ROOT, HA_DEVICE_MODEL_STR (config.h),
// MQTT-Komponente mit id mqtt_client, gueltige Systemzeit (SNTP in common.yaml).
// Abschaltbar ueber die Substitution debug_boot_info: "false" (dann ist loop() leer).
#ifndef BOOT_INFO_H
#define BOOT_INFO_H

#ifndef STIEBEL_DEBUG_BOOTINFO_true
namespace bootinfo {
static inline void loop() {}
}  // namespace bootinfo
#else

#include <time.h>
#include "crash_trace.h"
#include "unix_time.h"

namespace bootinfo {

namespace ct = ::i2cs::ct;

static bool g_sent = false;

#ifndef ESPHOME_PROJECT_VERSION
#define ESPHOME_PROJECT_VERSION "?"
#endif

// __DATE__ "Nov  6 2026" + __TIME__ "22:54:07" -> "2026-11-06 22:54:07"
static void build_time(char *out, size_t len) {
  static const char kMon[] = "JanFebMarAprMayJunJulAugSepOctNovDec";
  const char *d = __DATE__;
  int mon = 0;
  for (int i = 0; i < 12; i++)
    if (strncmp(d, kMon + 3 * i, 3) == 0) mon = i + 1;
  snprintf(out, len, "%.4s-%02d-%02d %s", d + 7, mon, atoi(d + 4), __TIME__);
}

static const char *reason_text(int r) {
  switch (r) {
    case ESP_RST_POWERON: return "Einschalten (Strom)";
    case ESP_RST_EXT: return "Externer Reset";
    case ESP_RST_SW: return "Software (OTA/Neustart)";
    case ESP_RST_PANIC: return "Absturz (Panic)";
    case ESP_RST_INT_WDT: return "Interrupt-Watchdog";
    case ESP_RST_TASK_WDT: return "Task-Watchdog";
    case ESP_RST_WDT: return "Watchdog";
    case ESP_RST_DEEPSLEEP: return "Deep-Sleep";
    case ESP_RST_BROWNOUT: return "Unterspannung (Brownout)";
    case ESP_RST_SDIO: return "SDIO";
    case ESP_RST_USB: return "USB";
    case ESP_RST_JTAG: return "JTAG";
    case ESP_RST_EFUSE: return "eFuse-Fehler";
    case ESP_RST_PWR_GLITCH: return "Spannungseinbruch";
    case ESP_RST_CPU_LOCKUP: return "CPU-Lockup";
    default: return "Unbekannt";
  }
}

static const char *phase_text(uint16_t p) {
  static const char *const kNames[] = {"ausserhalb (ESPHome/MQTT/CAN)", "i2c loop", "lwz on_frame", "lwz discovery",
                                       "lwz state", "lwz poll", "i2c publish_batch", "i2c status", "i2c stuck_check",
                                       "i2c slave_recover", "ota", "i2c scan_publish", "mqtt publish", "i2c setup"};
  return p < sizeof(kNames) / sizeof(kNames[0]) ? kNames[p] : "?";
}

static void pub(const char *topic, const char *payload) {
  id(mqtt_client).publish(topic, payload, strlen(payload), 0, true);
}

static void discovery(const char *obj, const char *name, const char *extra) {
  char topic[96];
  snprintf(topic, sizeof topic, "homeassistant/sensor/" HA_TOPIC_ROOT "/%s/config", obj);
  char buf[640];
  snprintf(buf, sizeof buf,
           "{\"name\":\"%s\",\"unique_id\":\"%s\",\"state_topic\":\"" HA_TOPIC_ROOT "/calculated/%s/state\","
           "\"entity_category\":\"diagnostic\",\"availability_topic\":\"" HA_TOPIC_ROOT "/status\"%s,"
           "\"device\":{\"identifiers\":[\"stiebel_eltron_" HA_DEVICE_MODEL_STR "\"],"
           "\"name\":\"Stiebel Eltron Wärmepumpe\",\"manufacturer\":\"Stiebel Eltron\","
           "\"sw_version\":\"" ESPHOME_PROJECT_VERSION "\"}}",
           name, obj, obj, extra);
  pub(topic, buf);
}

// Aufruf jede Sekunde. Haelt nebenbei crash_trace up_ms aktuell (fuer "Laufzeit vor Neustart").
static void loop() {
  ct::g_tr.up_ms = (uint32_t)millis();
  if (g_sent || !id(mqtt_client).is_connected()) return;
  const uint64_t now_ms = mkdbg::unix_ms();
  if (now_ms == 0) return;  // ohne gueltige Uhrzeit kein Boot-Zeitstempel
  g_sent = true;

  discovery("stiebel_mk1_letzter_neustart", "Letzter Neustart", ",\"device_class\":\"timestamp\",\"icon\":\"mdi:restart\"");
  discovery("stiebel_mk1_neustart_grund", "Neustart Grund",
            ",\"icon\":\"mdi:restart-alert\",\"json_attributes_topic\":\"" HA_TOPIC_ROOT "/calculated/stiebel_mk1_neustart_grund/attr\"");
  discovery("stiebel_mk1_laufzeit_vor_neustart", "Laufzeit vor Neustart",
            ",\"device_class\":\"duration\",\"unit_of_measurement\":\"s\",\"state_class\":\"measurement\",\"icon\":\"mdi:timer-outline\"");
  discovery("stiebel_mk1_neustarts", "Neustarts", ",\"state_class\":\"total_increasing\",\"icon\":\"mdi:counter\"");
  discovery("stiebel_mk1_firmware_version", "Firmware Version", ",\"icon\":\"mdi:tag-outline\"");
  discovery("stiebel_mk1_firmware_build", "Firmware Build", ",\"icon\":\"mdi:calendar-clock\"");
  pub(HA_TOPIC_ROOT "/calculated/stiebel_mk1_firmware_version/state", ESPHOME_PROJECT_VERSION);
  char bt[24];
  build_time(bt, sizeof bt);
  pub(HA_TOPIC_ROOT "/calculated/stiebel_mk1_firmware_build/state", bt);

  // Startzeit = jetzt - Laufzeit
  const time_t boot_s = (time_t)((now_ms - (uint64_t)millis()) / 1000ULL);
  struct tm tm;
  gmtime_r(&boot_s, &tm);
  char ts[40];
  strftime(ts, sizeof ts, "%Y-%m-%dT%H:%M:%S+00:00", &tm);

  const int r = ct::g_reason;
  char buf[384];
  int n;
  if (ct::g_have_last) {
    const ct::Trace &l = ct::g_last;
    n = snprintf(buf, sizeof buf,
                 "{\"code\":%d,\"grund\":\"%s\",\"start\":%lu,\"laufzeit_davor_s\":%lu,\"absturz_phase\":%u,"
                 "\"absturz_phase_text\":\"%s\",\"absturz_sub\":%u,\"isr_aktiv\":%lu,\"isr_cnt\":%lu,\"i2c_loops\":%lu,"
                 "\"mqtt_pubs\":%lu}",
                 r, reason_text(r), (unsigned long)ct::g_tr.boot, (unsigned long)(ct::g_prev_up_ms / 1000), (unsigned)l.phase,
                 phase_text(l.phase), (unsigned)l.sub, (unsigned long)l.isr_in, (unsigned long)l.isr_cnt,
                 (unsigned long)l.loops, (unsigned long)l.pubs);
  } else {
    n = snprintf(buf, sizeof buf, "{\"code\":%d,\"grund\":\"%s\",\"start\":%lu,\"laufzeit_davor_s\":%lu}", r,
                 reason_text(r), (unsigned long)ct::g_tr.boot, (unsigned long)(ct::g_prev_up_ms / 1000));
  }
  if (n > 0 && (size_t)n < sizeof buf) pub(HA_TOPIC_ROOT "/calculated/stiebel_mk1_neustart_grund/attr", buf);

  pub(HA_TOPIC_ROOT "/calculated/stiebel_mk1_letzter_neustart/state", ts);
  pub(HA_TOPIC_ROOT "/calculated/stiebel_mk1_neustart_grund/state", reason_text(r));
  char v[16];
  if (ct::g_prev_valid) {
    snprintf(v, sizeof v, "%lu", (unsigned long)(ct::g_prev_up_ms / 1000));
    pub(HA_TOPIC_ROOT "/calculated/stiebel_mk1_laufzeit_vor_neustart/state", v);
  }
  snprintf(v, sizeof v, "%lu", (unsigned long)ct::g_tr.boot);
  pub(HA_TOPIC_ROOT "/calculated/stiebel_mk1_neustarts/state", v);
  ESP_LOGI("BOOT", "Firmware %s, Build %s", ESPHOME_PROJECT_VERSION, bt);
  ESP_LOGI("BOOT", "Neustart: %s (code %d), Start %lu, Laufzeit davor %lu s, Absturzspur %s", reason_text(r), r,
           (unsigned long)ct::g_tr.boot, (unsigned long)(ct::g_prev_up_ms / 1000), ct::g_have_last ? "ja" : "nein");
}

}  // namespace bootinfo

#endif  // STIEBEL_DEBUG_BOOTINFO_true
#endif  // BOOT_INFO_H
