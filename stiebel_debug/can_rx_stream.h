// can_rx_stream.h - CAN-RX-Stream fuer die Produktiv-Firmware (Sniffer, Standard aus).
// Nur Lesepfad: Frames mitschneiden und ueber MQTT ausgeben. Keine Requests, keine Recovery.
// Kommando: stiebel-debug/cmd/sniff (einziges Kommando). Header-only, C++11.
// Kopien von ring_buffer.h und debug_json.h liegen im selben Ordner (Quelle: mk1-debug/firmware).
#ifndef MKDBG_CAN_RX_STREAM_H
#define MKDBG_CAN_RX_STREAM_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <string>
#include <vector>

#include <esp_system.h>
#include <driver/twai.h>

#include "ring_buffer.h"
#include "debug_json.h"
#include "unix_time.h"

namespace mkdbg {

static const uint32_t kRxIntervalMs = 50;         // hoechstens ein rx-Batch pro Intervall
static const size_t kRxBatch = 64;                // max. Frames je rx-Batch
static const size_t kRxBatchBufLen = 64 * 100;     // Ausgabepuffer fuer format_rx_batch

// ---------- Zustand (nur aus Haupt-Loop / CAN-Callback, kein Mutex) ----------
static RingBuffer<1024> rx_ring;                  // 16 KB (Debug-Firmware: 4096)
static uint32_t rx_count = 0;                     // alle empfangenen Frames
static uint32_t tx_count = 0;                     // alle gesendeten Requests
static SniffCmd sniff_cmd = {false, {0}, 0, {0}, 0};  // Standard AUS, leere Listen = alles
static uint32_t last_rx_pub_ms = 0;
static Frame rx_batch[kRxBatch];
static char rx_buf[kRxBatchBufLen];

// Sniffer-Filter: ids und types (type = data[0] & 0x0F, nur bei dlc >= 1). Leere Liste = alles.
static bool sniff_match(const Frame &f) {
  if (!sniff_cmd.on) return false;
  if (sniff_cmd.id_count > 0) {
    bool found = false;
    for (uint8_t i = 0; i < sniff_cmd.id_count; ++i) {
      if (sniff_cmd.ids[i] == f.id) { found = true; break; }
    }
    if (!found) return false;
  }
  if (sniff_cmd.type_count > 0) {
    if (f.dlc < 1) return false;
    const uint8_t t = (uint8_t)(f.data[0] & 0x0F);
    bool found = false;
    for (uint8_t i = 0; i < sniff_cmd.type_count; ++i) {
      if (sniff_cmd.types[i] == t) { found = true; break; }
    }
    if (!found) return false;
  }
  return true;
}

static ::esphome::mqtt::MQTTClientComponent *mqtt_client() { return ::esphome::mqtt::global_mqtt_client; }

static bool mqtt_ready() {
  ::esphome::mqtt::MQTTClientComponent *c = mqtt_client();
  return c != nullptr && c->is_connected();
}

static void mqtt_publish(const char *topic, const char *payload, size_t len) {
  if (!mqtt_ready()) return;
  mqtt_client()->publish(topic, payload, len, 0, false);
}

}  // namespace mkdbg

// ---------- Globale Schnittstelle (aufgerufen aus YAML und ha-stiebel-control.h) ----------

// Empfangener Frame aus on_frame. rx_count immer, Ring nur bei aktivem Sniffer und passendem Filter.
void dbgrx_on_frame(uint32_t can_id, const std::vector<uint8_t> &x) {
  ++mkdbg::rx_count;
  if (!mkdbg::sniff_cmd.on) return;

  mkdbg::Frame f;
  memset(&f, 0, sizeof f);
  f.t = (uint32_t)esphome::millis();
  f.u = mkdbg::unix_ms();
  f.id = (uint16_t)(can_id & 0x7FFu);
  const size_t n = x.size() > 8 ? 8 : x.size();
  f.dlc = (uint8_t)n;
  for (size_t i = 0; i < n; ++i) {
    f.data[i] = x[i];
  }
  f.tx = false;
  if (mkdbg::sniff_match(f)) {
    mkdbg::rx_ring.push(f);
  }
}

// Vom ESP gesendeter Frame (readSignal/writeSignal). tx_count immer, Ring nur bei aktivem Sniffer.
void dbgrx_note_tx(uint32_t can_id, const std::vector<uint8_t> &data) {
  ++mkdbg::tx_count;
  if (!mkdbg::sniff_cmd.on) return;

  mkdbg::Frame f;
  memset(&f, 0, sizeof f);
  f.t = (uint32_t)esphome::millis();
  f.u = mkdbg::unix_ms();
  f.id = (uint16_t)(can_id & 0x7FFu);
  const size_t n = data.size() > 8 ? 8 : data.size();
  f.dlc = (uint8_t)n;
  for (size_t i = 0; i < n; ++i) {
    f.data[i] = data[i];
  }
  f.tx = true;
  mkdbg::rx_ring.push(f);
}

// Ring leeren in hoechstens einem Batch pro kRxIntervalMs. Ohne MQTT-Verbindung bleibt der Ring unangetastet.
void dbgrx_flush() {
  if (mkdbg::rx_ring.size() == 0) return;
  const uint32_t now = (uint32_t)esphome::millis();
  if ((now - mkdbg::last_rx_pub_ms) < mkdbg::kRxIntervalMs) return;
  if (!mkdbg::mqtt_ready()) return;
  size_t n = 0;
  while (n < mkdbg::kRxBatch && mkdbg::rx_ring.pop(mkdbg::rx_batch[n])) ++n;
  if (n == 0) return;
  mkdbg::last_rx_pub_ms = now;
  size_t len = mkdbg::format_rx_batch(mkdbg::rx_batch, n, mkdbg::rx_buf, sizeof mkdbg::rx_buf);
  if (len == 0) {
    ESP_LOGW("mkdbg", "rx-Batch zu gross fuer Puffer, verworfen");
    return;
  }
  mkdbg::mqtt_publish("stiebel-debug/rx", mkdbg::rx_buf, len);
}

// TWAI-Treiberzaehler fuer den Status. Bewusst hier und nicht in debug_json.h: im Produktiv-Build
// gewinnt die gleichnamige Kopie aus lwz_180/ (gleicher Include-Guard), Aenderungen dort kaemen nicht an.
namespace mkdbg {
struct TwaiDiag {
  uint32_t rx_missed;   // Frames verworfen, weil die TWAI-RX-Queue voll war
  uint32_t rx_overrun;  // Frames verloren durch Ueberlauf des Hardware-RX-FIFO
  uint32_t rx_queue;    // Frames, die gerade in der TWAI-RX-Queue warten
  uint32_t tec;         // Sende-Fehlerzaehler
  uint32_t rec;         // Empfangs-Fehlerzaehler
};

// Haengt die TWAI-Felder an ein fertiges Status-JSON an (ersetzt die schliessende Klammer).
// Ergebnis: {...,"ntp":true,"rx_missed":0,"rx_overrun":0,"rx_queue":0,"tec":0,"rec":0}
inline size_t append_twai_diag(char *out, size_t cap, size_t len, const TwaiDiag &td) {
  if (len == 0 || out[len - 1] != '}') return len;
  size_t pos = len - 1;
  bool ok = dj_put(out, cap, pos, ",\"rx_missed\":")
    && dj_putf(out, cap, pos, "%lu", (unsigned long)td.rx_missed)
    && dj_put(out, cap, pos, ",\"rx_overrun\":")
    && dj_putf(out, cap, pos, "%lu", (unsigned long)td.rx_overrun)
    && dj_put(out, cap, pos, ",\"rx_queue\":")
    && dj_putf(out, cap, pos, "%lu", (unsigned long)td.rx_queue)
    && dj_put(out, cap, pos, ",\"tec\":")
    && dj_putf(out, cap, pos, "%lu", (unsigned long)td.tec)
    && dj_put(out, cap, pos, ",\"rec\":")
    && dj_putf(out, cap, pos, "%lu", (unsigned long)td.rec)
    && dj_put(out, cap, pos, "}");
  if (!ok) {            // passt nicht: altes Status-JSON unveraendert lassen
    out[len - 1] = '}';
    out[len] = 0;
    return len;
  }
  return pos;
}
}  // namespace mkdbg

// Status alle 5 s. TWAI-Werte nur lesend; Recovery bleibt bei publishCanDiagnostics().
void dbgrx_publish_status() {
  uint32_t twai_err = 0;
  bool bus_off = false;
  mkdbg::TwaiDiag td = {0, 0, 0, 0, 0};
  twai_status_info_t ts;
  if (twai_get_status_info(&ts) == ESP_OK) {
    twai_err = ts.bus_error_count;
    bus_off = (ts.state == TWAI_STATE_BUS_OFF);
    td.rx_missed = ts.rx_missed_count;
    td.rx_overrun = ts.rx_overrun_count;
    td.rx_queue = ts.msgs_to_rx;
    td.tec = ts.tx_error_counter;
    td.rec = ts.rx_error_counter;
  }
  char buf[384];
  size_t n = mkdbg::format_status(mkdbg::rx_count, mkdbg::tx_count,
                                  mkdbg::rx_ring.size(), mkdbg::rx_ring.capacity(),
                                  mkdbg::rx_ring.overflow(), twai_err, bus_off,
                                  (uint32_t)esp_get_free_heap_size(), mkdbg::ntp_synced(), buf, sizeof buf);
  n = mkdbg::append_twai_diag(buf, sizeof buf, n, td);
  if (n > 0) mkdbg::mqtt_publish("stiebel-debug/status", buf, n);
  // Availability alle 5 s erneuern (retained): ein spaeter ausgeloester Last Will einer anderen
  // Firmware (z.B. mk1-debug nach dem Umflashen) wuerde sonst "offline" stehen lassen.
  if (mkdbg::mqtt_ready()) mkdbg::mqtt_client()->publish("stiebel-debug/availability", "online", 6, 1, true);
}

// Kommando stiebel-debug/cmd/sniff. Alle anderen Suffixe werden ignoriert.
void dbgrx_on_command(const std::string &topic_suffix, const std::string &payload) {
  if (topic_suffix != "sniff") {
    ESP_LOGW("mkdbg", "Kommando '%s' ignoriert (Produktiv-Firmware kennt nur sniff)", topic_suffix.c_str());
    return;
  }
  mkdbg::SniffCmd c;
  if (!mkdbg::parse_sniff(payload.c_str(), c)) {
    ESP_LOGW("mkdbg", "sniff: Payload ungueltig, ignoriert");
    return;
  }
  mkdbg::sniff_cmd = c;
  ESP_LOGI("mkdbg", "sniff: on=%d ids=%u types=%u", (int)c.on, (unsigned)c.id_count, (unsigned)c.type_count);
}

#endif  // MKDBG_CAN_RX_STREAM_H
