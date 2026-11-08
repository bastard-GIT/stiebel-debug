// crash_trace.h - Absturz-Spur fuer mk1-i2c / Paket lwz_i2c (Diagnose INT_WDT 30.10.2026).
// Header-only, C++11. Die Spur liegt im RTC-Speicher (RTC_NOINIT_ATTR): sie uebersteht Watchdog-Resets,
// Panics und Software-Resets, nur nicht Stromausfall. Der Code schreibt laufend, in welcher Phase er ist;
// nach einem Absturz-Reset wird die alte Spur gesichert und im Status als "crash" veroeffentlicht.
// Schreiben kostet wenige Takte (kein Lock); der I2C-Callback (ISR) markiert Ein- und Austritt.
#ifndef I2CS_CRASH_TRACE_H
#define I2CS_CRASH_TRACE_H

#include <stdint.h>
#include "esp_attr.h"
#include "esp_system.h"

namespace i2cs {
namespace ct {

// Phasen (0 = ausserhalb unseres Codes, also ESPHome/MQTT/Ethernet/CAN)
enum Phase : uint16_t {
  P_NONE = 0,
  P_LOOP = 1,         // i2cs loop_impl
  P_ON_FRAME = 2,     // lwzha::on_frame
  P_DISCOVERY = 3,    // lwzha::send_discovery (sub = Def-Index)
  P_STATE = 4,        // lwzha::send_state (sub = Def-Index)
  P_POLL = 5,         // lwzha::poll
  P_BATCH = 6,        // publish_batch (stiebel-i2c/rx)
  P_STATUS = 7,       // status_impl
  P_STUCK = 8,        // stuck_check / bus_stuck_confirmed
  P_RECOVER = 9,      // slave_recover
  P_OTA = 10,         // ota_begin/ota_error
  P_SCAN_PUB = 11,    // scan_publish
  P_MQTT_PUB = 12,    // im MQTT-publish-Aufruf (sub = vorherige Phase)
  P_SETUP = 13,       // setup_impl
};

struct Trace {
  uint32_t magic;
  uint32_t boot;              // Startzaehler seit Stromausfall
  volatile uint16_t phase;
  volatile uint16_t sub;
  volatile uint32_t isr_in;   // 1 = I2C-Callback lief gerade
  volatile uint32_t isr_cnt;  // Anzahl I2C-Callbacks seit Start
  volatile uint32_t up_ms;    // Laufzeit beim letzten Loop-Eintritt
  volatile uint32_t loops;    // Anzahl i2cs-Loops seit Start
  volatile uint32_t pubs;     // Anzahl MQTT-Publishes aus unserem Code seit Start
};

static const uint32_t kMagic = 0x4C575A31;  // "LWZ1"

static RTC_NOINIT_ATTR Trace g_tr;
static Trace g_last;            // Spur des letzten Absturzes (Kopie beim Start)
static bool g_have_last = false;
static int g_reason = 0;        // esp_reset_reason() dieses Starts
static uint32_t g_prev_up_ms = 0;  // Laufzeit des vorherigen Starts (letzter Stand von up_ms), bei jedem Reset
static bool g_prev_valid = false;  // false nach Stromausfall (RTC-Speicher leer)

static inline void set(uint16_t phase, uint16_t sub = 0) {
  g_tr.phase = phase;
  g_tr.sub = sub;
}

static void boot() {
  g_reason = (int)esp_reset_reason();
  const bool valid = (g_tr.magic == kMagic);
  const bool crash = (g_reason == ESP_RST_PANIC || g_reason == ESP_RST_INT_WDT || g_reason == ESP_RST_TASK_WDT ||
                      g_reason == ESP_RST_WDT || g_reason == ESP_RST_BROWNOUT);
  if (valid && crash) {
    g_last = g_tr;
    g_have_last = true;
  }
  g_prev_valid = valid;
  g_prev_up_ms = valid ? g_tr.up_ms : 0;
  const uint32_t boots = valid ? g_tr.boot + 1 : 1;
  g_tr.magic = kMagic;
  g_tr.boot = boots;
  g_tr.phase = P_NONE;
  g_tr.sub = 0;
  g_tr.isr_in = 0;
  g_tr.isr_cnt = 0;
  g_tr.up_ms = 0;
  g_tr.loops = 0;
  g_tr.pubs = 0;
}

// Laeuft vor app_main (statischer Konstruktor), also auch wenn der Absturz frueh kommt.
struct BootHook {
  BootHook() { boot(); }
};
static BootHook g_boot_hook;

}  // namespace ct
}  // namespace i2cs

#endif  // I2CS_CRASH_TRACE_H
