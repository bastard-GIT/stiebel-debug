// can_debug.h - ESPHome-Glue fuer mk1-debug (CAN-Sniffer, Single-Read, Register-Scan, Probe ueber MQTT).
// Bindet ring_buffer.h, elster_frame.h, debug_json.h ein. C++11, header-only, nur von main.cpp included.
// Lese-Requests (read/scan/probe) und write (Schreiben mit Ack-Erkennung und Ruecklesen).
#ifndef MKDBG_CAN_DEBUG_H
#define MKDBG_CAN_DEBUG_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <string>
#include <vector>

#include <esp_system.h>
#include <driver/twai.h>

#include "ring_buffer.h"
#include "elster_frame.h"
#include "debug_json.h"
#include "unix_time.h"

namespace mkdbg {

static const uint32_t kOwnCanId = 0x680;          // eigene Absender-ID fuer Requests (als Ziel verboten)
static const uint32_t kCanIdMax = 0x7FF;          // hoechste 11-Bit-CAN-ID
static const uint32_t kTimeoutMs = 1000;          // Antwort-Timeout
static const uint32_t kReadGapMs = 100;           // Mindestabstand Single-Read
static const uint32_t kRxIntervalMs = 50;         // hoechstens eine rx-Nachricht pro Intervall
static const uint32_t kProgressIntervalMs = 250;  // Drosselung scan/progress und probe/progress
static const uint32_t kWriteAckWindowMs = 1000;   // write: Sammelfenster fuer Ack-Erkennung
static const size_t kRxBatch = 64;
static const size_t kRxBatchBufLen = 64 * 80;
static const uint8_t kScanChunk = 32;             // max. Items pro scan/result bzw. probe/result

enum Mode { MODE_IDLE, MODE_READ, MODE_SCAN, MODE_PROBE, MODE_WRITE };
enum WritePhase { WP_NONE, WP_COLLECT, WP_READBACK };  // MODE_WRITE: Ack-Sammelfenster, dann Ruecklesen
enum ReqStatus { ST_OK, ST_UNSUPPORTED, ST_TIMEOUT };

struct ScanItem {
  uint16_t index;
  uint16_t raw;
  ReqStatus status;
};

struct ProbeItem {
  uint16_t id;
  uint16_t raw;
  ReqStatus status;
};

// ---------- Zustand (nur aus Haupt-Loop / CAN-Callback, kein Mutex) ----------
static RingBuffer<4096> rx_ring;
static uint32_t rx_count = 0;
static uint32_t tx_count = 0;
static SniffCmd sniff_cmd = {true, {0}, 0, {0}, 0};  // Default: an, leere Listen = alles

static Mode mode = MODE_IDLE;
static bool send_pending = false;     // naechster Request fuer Read/Scan/Probe steht an
static bool req_open = false;         // Antwort fuer aktuellen Request ausstehend
static uint16_t req_member = 0;       // Ziel-CAN-ID des aktuellen Requests
static uint16_t req_index = 0;
static uint8_t req_switch = 0xFA;     // Switch-Byte des aktuellen Requests (fuer Antwort-Abgleich)
static uint16_t req_src = 0x680;      // Absender des aktuellen Requests (cmd/read "src", sonst 0x680)
static uint32_t req_sent_ms = 0;
static uint32_t req_timeout_ms = kTimeoutMs;  // Scan/Probe: delay_ms (Antworten kommen in wenigen ms), Read: kTimeoutMs
static uint32_t next_send_ms = 0;
static uint32_t last_rx_pub_ms = 0;
static uint32_t last_progress_ms = 0;

static ReadCmd read_cmd = {0, 0, 0, 0xFA, (uint16_t)kOwnCanId, kTimeoutMs};
static ScanCmd scan_cmd = {0, 0, 0, 0, 0, 0xFA};
static uint32_t scan_next = 0;        // naechster zu sendender Index (uint32, damit to=0xFFFF sauber endet)
static uint32_t scan_done = 0;
static uint32_t scan_total = 0;
static uint16_t scan_last_index = 0;
static ScanItem scan_items[kScanChunk];
static uint8_t scan_item_count = 0;

static ProbeCmd probe_cmd = {0, 0, 0, 0};
static uint32_t probe_next = 0;       // naechste zu sendende CAN-ID (uint32, damit to_id=0x7FF sauber endet)
static uint32_t probe_done = 0;
static uint32_t probe_total = 0;      // Anzahl CAN-IDs im Bereich ohne 0x680
static ProbeItem probe_items[kScanChunk];
static uint8_t probe_item_count = 0;

static WriteCmd write_cmd = {0, 0, 0, 0};
static WritePhase write_phase = WP_NONE;
static uint32_t write_sent_ms = 0;    // Sendezeitpunkt des write-Frames (Beginn Sammelfenster)
static int write_ack_type = -1;       // msg_type des Acks, -1 = kein Ack
static int32_t write_readback = -1;   // Ruecklesewert, -1 = keiner

static Frame rx_batch[kRxBatch];
static char rx_buf[kRxBatchBufLen];

// ---------- Hilfsfunktionen ----------

static bool mqtt_ready() { return id(mqtt_client).is_connected(); }

static void mqtt_publish(const char *topic, const char *payload, size_t len) {
  if (!mqtt_ready()) return;
  id(mqtt_client).publish(std::string(topic), payload, len, 0, false);
}

// Zulaessiges Ziel fuer Lese-Requests: 0x000..0x7FF, nicht die eigene ID 0x680.
static bool is_valid_target(uint32_t can_id) {
  return can_id <= kCanIdMax && can_id != kOwnCanId;
}

// Format-Name fuer JSON: 0 = auto, 1 = short, 2 = long.
static const char *fmt_name(uint8_t f) {
  if (f == 1) return "short";
  if (f == 2) return "long";
  return "auto";
}

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

static const char *status_str(ReqStatus s) {
  switch (s) {
    case ST_OK: return "ok";
    case ST_UNSUPPORTED: return "unsupported";
    default: return "timeout";
  }
}

// state "running" wird gedrosselt, "done" und "stopped" werden immer gesendet.
static void scan_progress(const char *state) {
  const uint32_t now = (uint32_t)esphome::millis();
  const bool running = (strcmp(state, "running") == 0);
  if (running && (now - last_progress_ms) < kProgressIntervalMs) return;
  last_progress_ms = now;
  char buf[160];
  size_t n = format_scan_progress(scan_cmd.member, fmt_name(scan_cmd.format), scan_last_index, scan_cmd.sw,
                                  scan_done, scan_total, state, buf, sizeof buf);
  if (n > 0) mqtt_publish("stiebel-debug/scan/progress", buf, n);
}

// Veroeffentlicht die gesammelten Scan-Items als scan/result und leert die Sammlung.
static void scan_flush_items() {
  if (scan_item_count == 0) return;
  static char buf[2048];  // 32 Items x ca. 57 Zeichen + Kopf: passt sicher
  int n = snprintf(buf, sizeof buf, "{\"member\":\"0x%03x\",\"format\":\"%s\",\"switch\":\"0x%02x\",\"items\":[",
                   (unsigned)scan_cmd.member, fmt_name(scan_cmd.format), (unsigned)scan_cmd.sw);
  size_t pos = (n > 0 && (size_t)n < sizeof buf) ? (size_t)n : 0;
  bool ok = pos > 0;
  for (uint8_t i = 0; ok && i < scan_item_count; ++i) {
    const ScanItem &it = scan_items[i];
    int m = snprintf(buf + pos, sizeof buf - pos,
                     "%s{\"index\":\"0x%04x\",\"raw\":\"0x%04x\",\"status\":\"%s\"}",
                     i > 0 ? "," : "", (unsigned)it.index, (unsigned)it.raw, status_str(it.status));
    if (m < 0 || (size_t)m >= sizeof buf - pos) {
      ok = false;
    } else {
      pos += (size_t)m;
    }
  }
  if (ok && pos + 2 < sizeof buf) {
    buf[pos++] = ']';
    buf[pos++] = '}';
    buf[pos] = '\0';
    mqtt_publish("stiebel-debug/scan/result", buf, pos);
  } else {
    ESP_LOGW("mkdbg", "scan/result Chunk zu gross, verworfen");
  }
  scan_item_count = 0;
}

static void scan_add_item(uint16_t index, uint16_t raw, ReqStatus st) {
  ScanItem &it = scan_items[scan_item_count];
  it.index = index;
  it.raw = raw;
  it.status = st;
  ++scan_item_count;
  if (scan_item_count >= kScanChunk) scan_flush_items();
}

// Scan beenden: Rest der Items und scan/progress mit state "done" oder "stopped".
static void scan_finish(const char *state) {
  scan_flush_items();
  scan_progress(state);
  req_open = false;
  mode = MODE_IDLE;
  send_pending = false;
}

// probe/progress; state "running" wird gedrosselt.
static void probe_progress(const char *state) {
  const uint32_t now = (uint32_t)esphome::millis();
  const bool running = (strcmp(state, "running") == 0);
  if (running && (now - last_progress_ms) < kProgressIntervalMs) return;
  last_progress_ms = now;
  char buf[160];
  size_t n = format_probe_progress(probe_cmd.index, req_member, probe_done, probe_total, state, buf, sizeof buf);
  if (n > 0) mqtt_publish("stiebel-debug/probe/progress", buf, n);
}

// Veroeffentlicht die gesammelten Probe-Treffer als probe/result (Chunks <= 32).
static void probe_flush_items() {
  if (probe_item_count == 0) return;
  static char buf[2048];  // 32 Items x ca. 50 Zeichen + Kopf: passt sicher
  int n = snprintf(buf, sizeof buf, "{\"index\":\"0x%04x\",\"items\":[", (unsigned)probe_cmd.index);
  size_t pos = (n > 0 && (size_t)n < sizeof buf) ? (size_t)n : 0;
  bool ok = pos > 0;
  for (uint8_t i = 0; ok && i < probe_item_count; ++i) {
    const ProbeItem &it = probe_items[i];
    int m = snprintf(buf + pos, sizeof buf - pos,
                     "%s{\"id\":\"0x%03x\",\"raw\":\"0x%04x\",\"status\":\"%s\"}",
                     i > 0 ? "," : "", (unsigned)it.id, (unsigned)it.raw, status_str(it.status));
    if (m < 0 || (size_t)m >= sizeof buf - pos) {
      ok = false;
    } else {
      pos += (size_t)m;
    }
  }
  if (ok && pos + 2 < sizeof buf) {
    buf[pos++] = ']';
    buf[pos++] = '}';
    buf[pos] = '\0';
    mqtt_publish("stiebel-debug/probe/result", buf, pos);
  } else {
    ESP_LOGW("mkdbg", "probe/result Chunk zu gross, verworfen");
  }
  probe_item_count = 0;
}

static void probe_add_item(uint16_t id, uint16_t raw, ReqStatus st) {
  ProbeItem &it = probe_items[probe_item_count];
  it.id = id;
  it.raw = raw;
  it.status = st;
  ++probe_item_count;
  if (probe_item_count >= kScanChunk) probe_flush_items();
}

// Probe beenden: Rest der Treffer und probe/progress mit state "done" oder "stopped".
static void probe_finish(const char *state) {
  probe_flush_items();
  probe_progress(state);
  req_open = false;
  mode = MODE_IDLE;
  send_pending = false;
}

// Ueberspringt die eigene ID 0x680 im Probe-Bereich.
static void probe_skip_own() {
  while (probe_next <= probe_cmd.to_id && probe_next == kOwnCanId) ++probe_next;
}

// ---------- write ----------

// Sendet einen 7-Byte-Request (Absender src, Standard eigene ID), zaehlt ihn und spiegelt ihn in den Sniffer.
static void can_send_frame(const uint8_t d[7], uint32_t now, uint16_t src = (uint16_t)kOwnCanId) {
  std::vector<uint8_t> data(d, d + 7);
  id(my_can).send_data(src, false, data);
  ++tx_count;
  if (sniff_cmd.on) {
    Frame f;
    memset(&f, 0, sizeof f);
    f.t = now;
    f.u = unix_ms();
    f.id = src;
    f.dlc = 7;
    memcpy(f.data, d, 7);
    f.tx = true;
    rx_ring.push(f);
  }
}

// write/result veroeffentlichen. readback < 0 = Feld fehlt, ack < 0 = Feld fehlt.
static void write_publish(const WriteCmd &c, const char *status, int ack, int32_t readback) {
  char buf[200];
  size_t n = format_write_result(c.member, c.index, c.value, status, ack, readback, buf, sizeof buf);
  if (n > 0) mqtt_publish("stiebel-debug/write/result", buf, n);
}

static void write_reset() {
  req_open = false;
  mode = MODE_IDLE;
  send_pending = false;
  write_phase = WP_NONE;
}

// Ergebnis aus Ack und Ruecklesewert ableiten, veroeffentlichen und beenden.
static void write_done() {
  const char *status;
  if (write_readback >= 0) {
    status = (write_readback == (int32_t)write_cmd.value) ? "verified" : "mismatch";
  } else if (write_ack_type >= 0) {
    status = "timeout";
  } else {
    status = "no_ack";
  }
  write_publish(write_cmd, status, write_ack_type, write_readback);
  write_reset();
}

// Abbruch (stop oder Bus-Off): status "timeout".
static void write_abort() {
  write_publish(write_cmd, "timeout", write_ack_type, -1);
  write_reset();
}

// Ack-Erkennung: erster Frame vom Ziel mit msg_type 3/4/5 oder msg_type 2 mit gleichem Index.
static void write_collect(const Frame &f) {
  if (write_ack_type >= 0 || f.id != write_cmd.member) return;
  Parsed p;
  if (!parse_frame(f.data, f.dlc, p)) return;
  if (p.msg_type == 3 || p.msg_type == 4 || p.msg_type == 5 ||
      (p.msg_type == 2 && p.index == write_cmd.index)) {
    write_ack_type = p.msg_type;
  }
}

// Rueckleseanfrage: Switch 0xFA, Format aus dem Kommando, Timeout wie bei read.
static void write_start_readback(uint32_t now) {
  uint8_t d[7];
  build_read_request_id(write_cmd.member, write_cmd.index, write_cmd.format, 0xFA, d);
  write_phase = WP_READBACK;
  can_send_frame(d, now);
  req_open = true;
  req_member = write_cmd.member;
  req_index = write_cmd.index;
  req_switch = 0xFA;
  req_sent_ms = now;
  req_timeout_ms = kTimeoutMs;
}

// Startet einen Schreibvorgang: write-Frame senden, danach Ack-Sammelfenster.
static void write_start(const WriteCmd &c, uint32_t now) {
  write_cmd = c;
  write_ack_type = -1;
  write_readback = -1;
  uint8_t d[7];
  build_write_request_id(c.member, c.index, c.value, c.format, d);
  ESP_LOGI("mkdbg", "WRITE 0x%03x index 0x%04x value 0x%04x fmt %s", (unsigned)c.member, (unsigned)c.index,
           (unsigned)c.value, fmt_name(c.format));
  mode = MODE_WRITE;
  send_pending = false;
  req_open = false;
  write_phase = WP_COLLECT;
  write_sent_ms = now;
  can_send_frame(d, now);
}

// Schliesst den offenen Request ab (Antwort, unsupported oder Timeout).
static void complete_request(ReqStatus st, uint16_t raw) {
  req_open = false;
  if (mode == MODE_READ) {
    char buf[160];
    size_t n = format_read_result(read_cmd.member, req_index, read_cmd.sw, raw, status_str(st), buf, sizeof buf,
                                  read_cmd.src);
    if (n > 0) mqtt_publish("stiebel-debug/read/result", buf, n);
    mode = MODE_IDLE;
    send_pending = false;
  } else if (mode == MODE_SCAN) {
    scan_last_index = req_index;
    if (st != ST_TIMEOUT) scan_add_item(req_index, raw, st);
    ++scan_done;
    scan_progress("running");
    if (scan_next > (uint32_t)scan_cmd.to) {
      scan_finish("done");
    } else {
      send_pending = true;
    }
  } else if (mode == MODE_PROBE) {
    if (st != ST_TIMEOUT) probe_add_item(req_member, raw, st);
    ++probe_done;
    probe_progress("running");
    probe_skip_own();
    if (probe_next > probe_cmd.to_id) {
      probe_finish("done");
    } else {
      send_pending = true;
    }
  } else if (mode == MODE_WRITE) {
    write_readback = (st == ST_OK) ? (int32_t)raw : -1;
    write_done();
  }
}

// Ein Batch aus dem Ringpuffer publizieren. Ohne MQTT-Verbindung bleibt der Ring unangetastet.
static void rx_flush(uint32_t now) {
  if (rx_ring.size() == 0) return;
  if ((now - last_rx_pub_ms) < kRxIntervalMs) return;
  if (!mqtt_ready()) return;
  size_t n = 0;
  while (n < kRxBatch && rx_ring.pop(rx_batch[n])) ++n;
  if (n == 0) return;
  last_rx_pub_ms = now;
  size_t len = format_rx_batch(rx_batch, n, rx_buf, sizeof rx_buf);
  if (len == 0) {
    ESP_LOGW("mkdbg", "rx-Batch zu gross fuer Puffer, verworfen");
    return;
  }
  mqtt_publish("stiebel-debug/rx", rx_buf, len);
}

// Sendet den naechsten Lese-Request (Read, aktueller Scan-Index oder aktuelle Probe-ID).
static void send_request(uint32_t now) {
  uint16_t target = 0;
  uint16_t index = 0;
  uint8_t fmt = 0;
  uint8_t sw = 0xFA;
  uint16_t src = (uint16_t)kOwnCanId;
  if (mode == MODE_READ) {
    target = read_cmd.member;
    src = read_cmd.src;
    index = read_cmd.index;
    fmt = read_cmd.format;
    sw = read_cmd.sw;
  } else if (mode == MODE_SCAN) {
    target = scan_cmd.member;
    index = (uint16_t)scan_next;
    fmt = scan_cmd.format;
    sw = scan_cmd.sw;
    ++scan_next;
  } else {  // MODE_PROBE: gleicher Index, Format auto, Switch 0xFA
    target = (uint16_t)probe_next;
    index = probe_cmd.index;
    fmt = 0;
    sw = 0xFA;
    ++probe_next;
    probe_skip_own();
  }
  send_pending = false;

  uint8_t d[7];
  build_read_request_id(target, index, fmt, sw, d);
  ESP_LOGD("mkdbg", "READ 0x%03x index 0x%04x fmt %s sw 0x%02x src 0x%03x: %02x %02x %02x %02x %02x %02x %02x",
           (unsigned)target, (unsigned)index, fmt_name(fmt), (unsigned)sw, (unsigned)src,
           d[0], d[1], d[2], d[3], d[4], d[5], d[6]);

  can_send_frame(d, now, src);

  req_open = true;
  req_member = target;
  req_index = index;
  req_switch = sw;
  req_src = src;
  req_sent_ms = now;
  if (mode == MODE_READ) {
    req_timeout_ms = read_cmd.timeout_ms;  // FES (0x100) antwortet erst nach 0,5..1,5 s
    next_send_ms = now + kReadGapMs;
  } else {
    const uint32_t delay = (mode == MODE_SCAN) ? scan_cmd.delay_ms : probe_cmd.delay_ms;
    req_timeout_ms = delay;
    next_send_ms = now + delay;
  }
}

static void stop_all() {
  if (mode == MODE_SCAN) {
    ESP_LOGI("mkdbg", "Scan abgebrochen");
    scan_finish("stopped");
  } else if (mode == MODE_PROBE) {
    ESP_LOGI("mkdbg", "Probe abgebrochen");
    probe_finish("stopped");
  } else if (mode == MODE_READ) {
    req_open = false;
    mode = MODE_IDLE;
    send_pending = false;
    ESP_LOGI("mkdbg", "Read abgebrochen");
  } else if (mode == MODE_WRITE) {
    ESP_LOGI("mkdbg", "Write abgebrochen");
    write_abort();
  } else {
    ESP_LOGI("mkdbg", "stop: nichts aktiv");
  }
}

}  // namespace mkdbg

// ---------- Globale Schnittstelle (INTERFACES 3.4) ----------

void dbg_on_frame(uint32_t can_id, const std::vector<uint8_t> &x) {
  ++mkdbg::rx_count;

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

  // write: Ack-Sammelfenster (vor dem Antwort-Abgleich, req_open ist dort false).
  if (mkdbg::mode == mkdbg::MODE_WRITE && mkdbg::write_phase == mkdbg::WP_COLLECT && can_id != mkdbg::kOwnCanId) {
    mkdbg::write_collect(f);
  }

  // Antwort-Abgleich unabhaengig vom Sniffer; eigene Frames (0x680) ignorieren.
  // Fremder Absender (cmd/read "src"): Antwort muss an src adressiert sein.
  if (mkdbg::req_open && can_id != mkdbg::kOwnCanId && can_id != mkdbg::req_src) {
    mkdbg::Parsed p;
    if (mkdbg::parse_frame(f.data, f.dlc, p) &&
        (mkdbg::req_src == mkdbg::kOwnCanId || p.receiver == mkdbg::req_src) &&
        mkdbg::is_response_for(p, (uint16_t)(can_id & 0x7FFu), mkdbg::req_member, mkdbg::req_index,
                               mkdbg::req_switch)) {
      const mkdbg::ReqStatus st = (p.value == 0x8000) ? mkdbg::ST_UNSUPPORTED : mkdbg::ST_OK;
      mkdbg::complete_request(st, p.value);
    }
  }
}

void dbg_on_command(const std::string &topic_suffix, const std::string &payload) {
  const char *json = payload.c_str();

  if (topic_suffix == "sniff") {
    mkdbg::SniffCmd c;
    if (!mkdbg::parse_sniff(json, c)) {
      ESP_LOGW("mkdbg", "sniff: Payload ungueltig, ignoriert");
      return;
    }
    mkdbg::sniff_cmd = c;
    ESP_LOGI("mkdbg", "sniff: on=%d ids=%u types=%u", (int)c.on, (unsigned)c.id_count, (unsigned)c.type_count);
    return;
  }

  if (topic_suffix == "stop") {
    mkdbg::stop_all();
    return;
  }

  if (topic_suffix == "read") {
    if (mkdbg::mode != mkdbg::MODE_IDLE) {
      ESP_LOGW("mkdbg", "read ignoriert: Scan, Probe oder Read laeuft");
      return;
    }
    mkdbg::ReadCmd rc;
    if (!mkdbg::parse_read(json, rc)) {
      ESP_LOGW("mkdbg", "read: Payload ungueltig, ignoriert");
      return;
    }
    if (!mkdbg::is_valid_target(rc.member)) {
      ESP_LOGW("mkdbg", "read: Ziel 0x%03x nicht zulaessig (0x000..0x7ff, nicht 0x680), abgelehnt",
               (unsigned)rc.member);
      return;
    }
    if (rc.src == rc.member) {
      ESP_LOGW("mkdbg", "read: src 0x%03x gleich Ziel, abgelehnt", (unsigned)rc.src);
      return;
    }
    if (rc.src != mkdbg::kOwnCanId) {
      ESP_LOGI("mkdbg", "read: Ziel 0x%03x index 0x%04x mit Absender 0x%03x", (unsigned)rc.member,
               (unsigned)rc.index, (unsigned)rc.src);
    }
    mkdbg::read_cmd = rc;
    mkdbg::mode = mkdbg::MODE_READ;
    mkdbg::send_pending = true;
    return;
  }

  if (topic_suffix == "scan") {
    if (mkdbg::mode != mkdbg::MODE_IDLE) {
      ESP_LOGW("mkdbg", "scan ignoriert: Scan, Probe oder Read laeuft");
      return;
    }
    mkdbg::ScanCmd sc;
    if (!mkdbg::parse_scan(json, sc)) {
      ESP_LOGW("mkdbg", "scan: Payload ungueltig, ignoriert");
      return;
    }
    if (!mkdbg::is_valid_target(sc.member)) {
      ESP_LOGW("mkdbg", "scan: Ziel 0x%03x nicht zulaessig (0x000..0x7ff, nicht 0x680), abgelehnt",
               (unsigned)sc.member);
      return;
    }
    mkdbg::scan_cmd = sc;
    mkdbg::scan_next = sc.from;
    mkdbg::scan_done = 0;
    mkdbg::scan_total = (uint32_t)sc.to - (uint32_t)sc.from + 1u;
    mkdbg::scan_last_index = sc.from;
    mkdbg::scan_item_count = 0;
    mkdbg::mode = mkdbg::MODE_SCAN;
    mkdbg::send_pending = true;
    return;
  }

  if (topic_suffix == "probe") {
    if (mkdbg::mode != mkdbg::MODE_IDLE) {
      ESP_LOGW("mkdbg", "probe ignoriert: Scan, Probe oder Read laeuft");
      return;
    }
    mkdbg::ProbeCmd pc;
    if (!mkdbg::parse_probe(json, pc)) {
      ESP_LOGW("mkdbg", "probe: Payload ungueltig, ignoriert");
      return;
    }
    mkdbg::probe_cmd = pc;
    mkdbg::probe_next = pc.from_id;
    mkdbg::probe_done = 0;
    uint32_t total = (uint32_t)pc.to_id - (uint32_t)pc.from_id + 1u;
    if (pc.from_id <= mkdbg::kOwnCanId && mkdbg::kOwnCanId <= pc.to_id) --total;  // 0x680 wird uebersprungen
    mkdbg::probe_total = total;
    mkdbg::probe_item_count = 0;
    mkdbg::req_member = pc.from_id;
    mkdbg::mode = mkdbg::MODE_PROBE;
    mkdbg::probe_skip_own();
    if (mkdbg::probe_next > pc.to_id) {
      mkdbg::probe_finish("done");  // Bereich enthaelt nur 0x680
      return;
    }
    ESP_LOGI("mkdbg", "probe: index 0x%04x ids 0x%03x..0x%03x", (unsigned)pc.index,
             (unsigned)pc.from_id, (unsigned)pc.to_id);
    mkdbg::send_pending = true;
    return;
  }

  if (topic_suffix == "write") {
    if (mkdbg::mode != mkdbg::MODE_IDLE) {
      ESP_LOGW("mkdbg", "write ignoriert: Scan, Probe, Read oder Write laeuft");
      return;
    }
    mkdbg::WriteCmd wc;
    if (!mkdbg::parse_write(json, wc)) {
      ESP_LOGW("mkdbg", "write: Payload ungueltig, ignoriert");
      return;
    }
    if (!mkdbg::is_valid_target(wc.member)) {
      ESP_LOGW("mkdbg", "write: Ziel 0x%03x nicht zulaessig (0x000..0x7ff, nicht 0x680), abgelehnt",
               (unsigned)wc.member);
      return;
    }
    if (wc.index >= 0x00F7 && wc.index <= 0x00FA && wc.value == 0x8000) {
      ESP_LOGW("mkdbg", "write: Index 0x%04x mit Wert 0x8000 abgelehnt, nichts gesendet", (unsigned)wc.index);
      mkdbg::write_publish(wc, "rejected", -1, -1);
      return;
    }
    mkdbg::write_start(wc, (uint32_t)esphome::millis());
    return;
  }

  ESP_LOGW("mkdbg", "unbekanntes Kommando: %s", topic_suffix.c_str());
}

void dbg_loop() {
  const uint32_t now = (uint32_t)esphome::millis();

  if (mkdbg::req_open && (now - mkdbg::req_sent_ms) >= mkdbg::req_timeout_ms) {
    mkdbg::complete_request(mkdbg::ST_TIMEOUT, 0);
  }

  if (mkdbg::mode != mkdbg::MODE_IDLE && mkdbg::send_pending && !mkdbg::req_open &&
      now >= mkdbg::next_send_ms) {
    mkdbg::send_request(now);
  }

  if (mkdbg::mode == mkdbg::MODE_WRITE && mkdbg::write_phase == mkdbg::WP_COLLECT &&
      (now - mkdbg::write_sent_ms) >= mkdbg::kWriteAckWindowMs) {
    mkdbg::write_start_readback(now);
  }

  mkdbg::rx_flush(now);
}

void dbg_publish_status() {
  // Treiberstatus: overflow = Ringpuffer-Verluste + TWAI-Hardware-Verluste (missed/overrun).
  uint32_t twai_err = 0;
  uint32_t hw_lost = 0;
  bool bus_off = false;
  twai_status_info_t ts;
  if (twai_get_status_info(&ts) == ESP_OK) {
    twai_err = ts.bus_error_count;
    hw_lost = ts.rx_missed_count + ts.rx_overrun_count;
    bus_off = (ts.state == TWAI_STATE_BUS_OFF);
  }
  if (bus_off && (mkdbg::mode == mkdbg::MODE_SCAN || mkdbg::mode == mkdbg::MODE_PROBE ||
                  mkdbg::mode == mkdbg::MODE_WRITE)) {
    ESP_LOGW("mkdbg", "Bus-Off: Scan/Probe/Write wird abgebrochen");
    mkdbg::stop_all();
  }
  char buf[200];
  size_t n = mkdbg::format_status(mkdbg::rx_count, mkdbg::tx_count,
                                  mkdbg::rx_ring.size(), mkdbg::rx_ring.capacity(),
                                  mkdbg::rx_ring.overflow() + hw_lost, twai_err, bus_off,
                                  (uint32_t)esp_get_free_heap_size(), mkdbg::ntp_synced(), buf, sizeof buf);
  if (n > 0) mkdbg::mqtt_publish("stiebel-debug/status", buf, n);
}

#endif  // MKDBG_CAN_DEBUG_H
