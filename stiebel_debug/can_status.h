// can_status.h - CAN-/TWAI-Status der Produktiv-Firmware auf stiebel-debug/status (alle 5 s).
// Nur Lesepfad, keine Requests, keine Recovery (die bleibt bei publishCanDiagnostics()).
// Den Roh-Frame-Mitschnitt liefert seit 2026-11-23 die Firmware selbst (can_debug.h,
// <root>/can_debug/set on|off, Frames auf <root>/can_debug/frames). Header-only, C++11.
//
// Status-JSON (Feldnamen wie bisher, Stream-Felder entfallen):
//   {"fw":"prod","rx":<Frames seit Start>,"twai_err":..,"bus_off":..,"heap":..,"ntp":..,
//    "rx_missed":..,"rx_overrun":..,"rx_queue":..,"tec":..,"rec":..}
// TWAI-Felder nur mit ESP32-TWAI (can_esp32.yaml). Beim MCP2515 (SPI) fehlen sie, weil der
// TWAI-Treiber dann nicht installiert ist (twai_get_status_info liefert einen Fehler).
#ifndef STIEBEL_DEBUG_CAN_STATUS_H
#define STIEBEL_DEBUG_CAN_STATUS_H

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <vector>

#include <esp_system.h>
#if __has_include(<driver/twai.h>)
#include <driver/twai.h>
#define STIEBEL_DEBUG_HAVE_TWAI 1
#endif

#include "unix_time.h"

namespace mkdbg {
static uint32_t rx_count = 0;   // alle empfangenen Frames seit Start (on_frame in stiebel_debug.yaml)
}  // namespace mkdbg

// Aus on_frame (canbus my_can, !extend): nur zaehlen.
inline void dbgstat_on_frame() { ++mkdbg::rx_count; }

// Status alle 5 s, dazu availability (retained) erneuern.
inline void dbgstat_publish() {
  ::esphome::mqtt::MQTTClientComponent *c = ::esphome::mqtt::global_mqtt_client;
  if (c == nullptr || !c->is_connected()) return;
  char buf[320];
  int n = snprintf(buf, sizeof buf, "{\"fw\":\"prod\",\"rx\":%lu,\"heap\":%lu,\"ntp\":%s",
                   (unsigned long)mkdbg::rx_count, (unsigned long)esp_get_free_heap_size(),
                   mkdbg::ntp_synced() ? "true" : "false");
#ifdef STIEBEL_DEBUG_HAVE_TWAI
  twai_status_info_t ts;
  if (n > 0 && (size_t)n < sizeof buf && twai_get_status_info(&ts) == ESP_OK) {
    n += snprintf(buf + n, sizeof buf - n,
                  ",\"twai_err\":%lu,\"bus_off\":%s,\"rx_missed\":%lu,\"rx_overrun\":%lu,\"rx_queue\":%lu,"
                  "\"tec\":%lu,\"rec\":%lu",
                  (unsigned long)ts.bus_error_count, ts.state == TWAI_STATE_BUS_OFF ? "true" : "false",
                  (unsigned long)ts.rx_missed_count, (unsigned long)ts.rx_overrun_count,
                  (unsigned long)ts.msgs_to_rx, (unsigned long)ts.tx_error_counter,
                  (unsigned long)ts.rx_error_counter);
  }
#endif
  if (n <= 0 || (size_t)n + 2 > sizeof buf) return;
  buf[n++] = '}';
  buf[n] = 0;
  c->publish("stiebel-debug/status", buf, n, 0, false);
  // Availability alle 5 s erneuern (retained): ein spaeter ausgeloester Last Will einer anderen
  // Firmware (z.B. mk1-debug nach dem Umflashen) wuerde sonst "offline" stehen lassen.
  c->publish("stiebel-debug/availability", "online", 6, 1, true);
}

#endif  // STIEBEL_DEBUG_CAN_STATUS_H
