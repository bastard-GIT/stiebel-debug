// i2c_key.h - Tastendruck wie das Bedienteil senden (Ausloeser: MQTT stiebel-i2c/cmd/key). Test 2026-10-31.
// Paket (General-Call-Write, wie im Mitschnitt vom 30.10.): 00 e3 30 20 00 00 <code> <crc>,
// crc = CRC-8 (init 0, poly 0x49, MSB-first) ueber e3 30 20 00 00 code (gleich crc8([code])).
// Beispiele aus dem Mitschnitt: 01 -> 49 (Rad ab), 02 -> 92 (Rad auf).
// Ablauf (eigener Task, wie i2c_scan.h): Slave loeschen, Master-Bus auf Port 0 anlegen (scan_bus_create,
// 10 kHz, Arbitration an, Interrupts aus), je Paket auf Bus-idle warten und ueber die LL-Register senden,
// danach Master loeschen und Slave wie beim Boot wieder anlegen. Kein i2c_master_transmit (Bus-Clear mit
// 9 SCL-Pulsen nach Arbitrationsverlust, siehe i2c_scan.h).
// Busy-Flag: g_scan_running (Waechter, OTA und Scan sehen den Bus damit als belegt).
#ifndef I2CS_KEY_H
#define I2CS_KEY_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal/i2c_ll.h"
#include "soc/i2c_struct.h"

#include "i2c_slave_rx.h"
#include "i2c_scan.h"

namespace i2cs {

static const uint32_t kKeyTxUs = 30000;   // Timeout je Paket (8 Byte bei 10 kHz ca. 7,5 ms)
static const int kKeyMaxCount = 5;

struct KeyResult {
  uint8_t code;
  uint8_t count;
  uint8_t sent;      // Pakete mit ACK auf allen Bytes
  uint8_t nack;      // Pakete mit NACK
  uint32_t arb_lost;
  uint32_t ms;
  bool ok;
  char err[80];
};

static const int kKeySeqMax = 8;

struct KeyCfg {
  uint8_t codes[kKeySeqMax];  // Tastenfolge, je Eintrag ein Paket
  uint8_t count;
  uint32_t gap_ms;
};

static KeyResult g_key;
static KeyCfg g_key_cfg;
static volatile bool g_key_done = false;  // Task fertig, g_key gueltig (Haupt-Loop veroeffentlicht)

static uint8_t key_crc8(const uint8_t *p, size_t n) {
  uint8_t c = 0;
  for (size_t i = 0; i < n; ++i) {
    c ^= p[i];
    for (int b = 0; b < 8; ++b) c = (c & 0x80) ? (uint8_t)((c << 1) ^ 0x49) : (uint8_t)(c << 1);
  }
  return c;
}

static void key_set_err(const char *msg) {
  if (g_key.err[0] == '\0') {
    strncpy(g_key.err, msg, sizeof g_key.err - 1);
    g_key.err[sizeof g_key.err - 1] = '\0';
  }
}

// START + 8 Byte (Adresse 0x00 = General Call, Write) + STOP. Rueckgabe wie scan_probe.
static ScanProbe key_tx(const uint8_t *pkt, uint8_t n) {
  i2c_dev_t *hw = &I2C0;
  i2c_ll_txfifo_rst(hw);
  i2c_ll_rxfifo_rst(hw);
  i2c_ll_clear_intr_mask(hw, 0xFFFFFFFF);
  i2c_ll_write_txfifo(hw, pkt, n);
  i2c_ll_hw_cmd_t c0, c1, c2;
  c0.val = 0;
  c0.op_code = I2C_LL_CMD_RESTART;
  c1.val = 0;
  c1.op_code = I2C_LL_CMD_WRITE;
  c1.byte_num = n;
  c1.ack_en = 1;
  c1.ack_exp = 0;
  c2.val = 0;
  c2.op_code = I2C_LL_CMD_STOP;
  i2c_ll_master_write_cmd_reg(hw, c0, 0);
  i2c_ll_master_write_cmd_reg(hw, c1, 1);
  i2c_ll_master_write_cmd_reg(hw, c2, 2);
  i2c_ll_update(hw);
  i2c_ll_start_trans(hw);

  const int64_t t0 = esp_timer_get_time();
  bool nack = false;
  while (true) {
    i2c_int_raw_reg_t r;
    r.val = hw->int_raw.val;
    if (r.arbitration_lost_int_raw || r.time_out_int_raw) return PROBE_LOST;
    if (r.nack_int_raw) nack = true;
    if (r.trans_complete_int_raw) break;
    if (esp_timer_get_time() - t0 >= (int64_t)kKeyTxUs) return PROBE_LOST;
    esp_rom_delay_us(50);
  }
  while (i2c_ll_is_bus_busy(hw)) {
    if (esp_timer_get_time() - t0 >= (int64_t)kKeyTxUs) return PROBE_LOST;
    esp_rom_delay_us(50);
  }
  return nack ? PROBE_NACK : PROBE_ACK;
}

static void key_task(void *) {
  const int64_t t_start = esp_timer_get_time();

  g_slave_up = false;
  bool slave_deleted = false;
  if (slave_teardown()) {
    slave_deleted = true;
    i2c_master_bus_handle_t bus = scan_bus_create();
    if (bus == nullptr) key_set_err("i2c_new_master_bus fehlgeschlagen");
    for (uint8_t i = 0; bus != nullptr && i < g_key_cfg.count && g_key.err[0] == '\0'; ++i) {
      if (i > 0) vTaskDelay(pdMS_TO_TICKS(g_key_cfg.gap_ms));
      uint8_t pkt[8] = {0x00, 0xe3, 0x30, 0x20, 0x00, 0x00, g_key_cfg.codes[i], 0};
      pkt[7] = key_crc8(pkt + 1, 6);
      bool done = false;
      for (int tr = 0; tr < kScanTries && !done && bus != nullptr; ++tr) {
        if (!scan_wait_idle(kScanIdleWaitMs)) continue;
        const ScanProbe r = key_tx(pkt, sizeof pkt);
        if (r == PROBE_ACK) {
          ++g_key.sent;
          done = true;
        } else if (r == PROBE_NACK) {
          ++g_key.nack;
          done = true;
        } else {
          ++g_key.arb_lost;
          scan_bus_delete(bus);
          bus = scan_bus_create();
          if (bus == nullptr) key_set_err("Master-Bus nach Arbitrationsverlust nicht neu angelegt");
          vTaskDelay(pdMS_TO_TICKS(10));
        }
      }
      if (!done) key_set_err("Paket nicht gesendet (Bus belegt oder Arbitration verloren)");
    }
    scan_bus_delete(bus);
  } else {
    key_set_err("Slave konnte nicht geloescht werden");
  }

  if (slave_deleted) {
    if (scan_restore_slave()) {
      g_slave_up = true;
    } else {
      key_set_err("Slave nicht wiederhergestellt");
    }
  } else if (g_slv.dev != nullptr) {
    g_slave_up = true;
  }
  g_key.ok = (g_key.err[0] == '\0' && g_key.nack == 0);
  g_key.ms = (uint32_t)((esp_timer_get_time() - t_start) / 1000);
  __atomic_store_n(&g_key_done, true, __ATOMIC_RELEASE);
  vTaskDelete(nullptr);
}

// Startet den Sende-Task fuer eine Tastenfolge (n Pakete, gap_ms Abstand). Nutzt g_scan_running als
// Busy-Flag und g_scan_cfg fuer Pins und Slave. In g_key.code steht der erste Code.
static bool key_start_seq(int sda, int scl, int slave_addr, bool pullup, const uint8_t *codes, uint8_t n,
                          uint32_t gap_ms) {
  if (g_scan_running || n == 0 || n > kKeySeqMax) return false;
  g_scan_cfg.sda = sda;
  g_scan_cfg.scl = scl;
  g_scan_cfg.slave_addr = slave_addr;
  g_scan_cfg.pullup = pullup;
  memset(&g_key, 0, sizeof g_key);
  g_key.code = codes[0];
  g_key.count = n;
  memcpy(g_key_cfg.codes, codes, n);
  g_key_cfg.count = n;
  g_key_cfg.gap_ms = gap_ms;
  g_key_done = false;
  g_scan_done = false;
  g_scan_running = true;
  if (xTaskCreate(key_task, "i2ckey", 4096, nullptr, 2, nullptr) != pdPASS) {
    g_scan_running = false;
    return false;
  }
  return true;
}

// Gleicher Code count-mal (cmd/key, Drehrad-Burst).
static bool key_start(int sda, int scl, int slave_addr, bool pullup, uint8_t code, uint8_t count, uint32_t gap_ms) {
  uint8_t codes[kKeySeqMax];
  if (count == 0 || count > kKeySeqMax) return false;
  memset(codes, code, count);
  return key_start_seq(sda, scl, slave_addr, pullup, codes, count, gap_ms);
}

}  // namespace i2cs

#endif  // I2CS_KEY_H
