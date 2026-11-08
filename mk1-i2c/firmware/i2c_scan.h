// i2c_scan.h - On-Demand-Bus-Scan fuer mk1-i2c (Ausloeser: MQTT stiebel-i2c/cmd/scan). NICHT AM GERAET GETESTET.
// Ablauf (eigener FreeRTOS-Task, der Haupt-Loop bleibt frei):
//   1. Slave-Device loeschen (i2c_del_slave_device), danach nur noch EIN aktiver Treiber auf Port 0.
//   2. i2c_master-Bus auf denselben Pins/Port anlegen (nur fuer Pins, Takt-Quelle und Port-Belegung),
//      Takt 10 kHz (unter dem LWZ-Takt von ca. 13,5 kHz), Arbitration im Controller einschalten.
//   3. Je Adresse: warten bis SDA und SCL mindestens 5 ms lang High sind (LWZ ist Master und sendet
//      ca. 5 Frames/s), dann eigener Probe ueber die LL-Register (START + Adresse/W + STOP, Abfrage der
//      int_raw-Bits, Interrupts bleiben aus). Bei Arbitrationsverlust oder Timeout: Master-Bus loeschen und
//      neu anlegen (Peripheral-Reset ueber i2c_common, KEINE SCL-Pulse), max. 3 Versuche je Adresse.
//   4. Master-Bus loeschen, Slave exakt wie beim Boot neu anlegen (Zaehler bleiben erhalten).
//      Die Wiederherstellung laeuft auch nach Fehlern (bis zu 3 Versuche).
// Das Ergebnis liegt in g_scan; der Haupt-Loop veroeffentlicht es (kein MQTT-Aufruf aus dem Task).
// Warum nicht i2c_master_probe (IDF 5.5.6): fester Takt 100 kHz, und bei Arbitrationsverlust oder Timeout
// setzt der Treiber den Status TIMEOUT (i2c_master.c:801) und ruft danach s_i2c_hw_fsm_reset(..., true)
// mit Bus-Clear (9 SCL-Pulse) auf (i2c_master.c:553/602), also mitten in einem LWZ-Frame.
// Harte Regel: SDA/SCL nie ausserhalb des I2C-Protokolls treiben.
#ifndef I2CS_SCAN_H
#define I2CS_SCAN_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "sdkconfig.h"
#include "driver/i2c_master.h"
#include "esp_clk_tree.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal/i2c_ll.h"
#include "soc/gpio_reg.h"
#include "soc/i2c_struct.h"
#include "soc/soc.h"

#include "i2c_slave_rx.h"

namespace i2cs {

static const uint8_t kScanMinAddr = 0x08;
static const uint8_t kScanMaxAddr = 0x77;
static const uint32_t kScanIdleUs = 5000;       // SDA und SCL mindestens so lange High
static const uint32_t kScanIdleWaitMs = 1000;   // Wartezeit auf Bus-idle je Versuch
static const uint32_t kScanHz = 10000;          // Scan-Takt, unter dem LWZ-Takt (ca. 13,5 kHz)
static const uint32_t kScanProbeUs = 20000;     // Timeout je Probe (ein Probe dauert bei 10 kHz ca. 1,2 ms)
static const int kScanTries = 3;                // Versuche je Adresse
static const int kScanMaxConsecFail = 3;        // so viele Adressen in Folge ohne Ergebnis: Abbruch

struct ScanResult {
  uint8_t found[128];
  uint8_t failed[128];
  uint8_t n_found;
  uint8_t n_failed;
  uint8_t from;
  uint8_t to;
  uint32_t nack;
  uint32_t arb_lost;
  uint32_t retries;
  uint32_t ms;
  bool ok;
  char err[80];
};

struct ScanCfg {
  int sda;
  int scl;
  int slave_addr;
  bool pullup;
  uint8_t from;
  uint8_t to;
};

static ScanResult g_scan;
static ScanCfg g_scan_cfg;
static volatile bool g_scan_running = false;  // gesetzt von scan_start, geloescht vom Haupt-Loop nach Veroeffentlichung
static volatile bool g_scan_done = false;     // Task fertig, g_scan gueltig
static volatile bool g_slave_up = false;      // Slave-Treiber laeuft (vom Scan-Task und Setup geschrieben)

static void scan_set_err(const char *msg) {
  if (g_scan.err[0] == '\0') {
    strncpy(g_scan.err, msg, sizeof g_scan.err - 1);
    g_scan.err[sizeof g_scan.err - 1] = '\0';
  }
}

// true wenn SDA und SCL fuer kScanIdleUs ohne Unterbrechung High waren (nur Lesen von GPIO_IN_REG).
static bool scan_wait_idle(uint32_t max_ms) {
  const uint32_t mask = (1u << g_scan_cfg.sda) | (1u << g_scan_cfg.scl);
  const int64_t t0 = esp_timer_get_time();
  int64_t hi_since = 0;
  while (true) {
    const int64_t now = esp_timer_get_time();
    if ((REG_READ(GPIO_IN_REG) & mask) == mask) {
      if (hi_since == 0) {
        hi_since = now;
      } else if (now - hi_since >= (int64_t)kScanIdleUs) {
        return true;
      }
      esp_rom_delay_us(40);
    } else {
      hi_since = 0;
      if ((now - t0) >= (int64_t)max_ms * 1000) return false;
      vTaskDelay(1);  // Bus belegt: Task abgeben
    }
    if ((now - t0) >= (int64_t)max_ms * 1000 && hi_since == 0) return false;
  }
}

// Slave exakt wie beim Boot anlegen, Zaehler bleiben erhalten.
static bool scan_restore_slave() {
  for (int i = 0; i < 3; ++i) {
    const uint32_t f = g_slv.frames, b = g_slv.bytes, o = g_slv.overflow, e = g_slv.errors;
    if (slave_setup(g_scan_cfg.sda, g_scan_cfg.scl, g_scan_cfg.slave_addr, g_scan_cfg.pullup)) {
      g_slv.frames = f;
      g_slv.bytes = b;
      g_slv.overflow = o;
      g_slv.errors = e;
      return true;
    }
    vTaskDelay(pdMS_TO_TICKS(50));
  }
  return false;
}

enum ScanProbe { PROBE_ACK, PROBE_NACK, PROBE_LOST };

// Master-Bus anlegen und fuer den eigenen Probe einstellen. i2c_new_master_bus belegt Port 0, setzt Pins
// (open-drain) und Takt-Quelle und macht dabei einen Peripheral-Reset (i2c_common.c), keinen Bus-Clear.
static i2c_master_bus_handle_t scan_bus_create() {
  i2c_master_bus_config_t bc;
  memset(&bc, 0, sizeof bc);
  bc.i2c_port = 0;
  bc.sda_io_num = (gpio_num_t)g_scan_cfg.sda;
  bc.scl_io_num = (gpio_num_t)g_scan_cfg.scl;
  bc.clk_source = I2C_CLK_SRC_DEFAULT;
  bc.glitch_ignore_cnt = 7;
  bc.flags.enable_internal_pullup = g_scan_cfg.pullup ? 1 : 0;
  i2c_master_bus_handle_t bus = nullptr;
  const esp_err_t rb = i2c_new_master_bus(&bc, &bus);
  if (rb != ESP_OK || bus == nullptr) {
    ESP_LOGE("i2cs", "i2c_new_master_bus fehlgeschlagen: %d", (int)rb);
    return nullptr;
  }
  uint32_t src_hz = 0;
  esp_clk_tree_src_get_freq_hz((soc_module_clk_t)I2C_CLK_SRC_DEFAULT, ESP_CLK_TREE_SRC_FREQ_PRECISION_CACHED, &src_hz);
  if (src_hz == 0) src_hz = 40000000;  // XTAL des ESP32-S3
  i2c_hal_clk_config_t cc;
  i2c_ll_master_cal_bus_clk(src_hz, kScanHz, &cc);
  i2c_ll_master_set_bus_timing(&I2C0, &cc);
  i2c_ll_master_set_fractional_divider(&I2C0, 0, 0);
  // Interrupts bleiben aus (die Treiber-ISR greift dann nie ein); scan_probe fragt int_raw ab.
  i2c_ll_disable_intr_mask(&I2C0, 0xFFFFFFFF);
  // Der IDF-Master schaltet Arbitration aus: einschalten, damit ein gleichzeitiger START der LWZ erkannt wird.
  i2c_ll_enable_arbitration(&I2C0, true);
  i2c_ll_update(&I2C0);
  return bus;
}

static void scan_bus_delete(i2c_master_bus_handle_t bus) {
  if (bus == nullptr) return;
  const esp_err_t rd = i2c_del_master_bus(bus);
  if (rd != ESP_OK) {
    ESP_LOGE("i2cs", "i2c_del_master_bus fehlgeschlagen: %d", (int)rd);
    scan_set_err("i2c_del_master_bus fehlgeschlagen");
  }
}

// START + Adresse/W + STOP ueber die Kommando-Register. Nach Arbitrationsverlust treibt der Controller
// selbst nichts mehr; der Aufrufer setzt ihn dann ueber Loeschen/Neuanlegen des Busses zurueck.
static ScanProbe scan_probe(uint8_t addr) {
  i2c_dev_t *hw = &I2C0;
  i2c_ll_txfifo_rst(hw);
  i2c_ll_rxfifo_rst(hw);
  i2c_ll_clear_intr_mask(hw, 0xFFFFFFFF);
  const uint8_t b = (uint8_t)(addr << 1);  // Schreibzugriff
  i2c_ll_write_txfifo(hw, &b, 1);
  i2c_ll_hw_cmd_t c0, c1, c2;
  c0.val = 0;
  c0.op_code = I2C_LL_CMD_RESTART;
  c1.val = 0;
  c1.op_code = I2C_LL_CMD_WRITE;
  c1.byte_num = 1;
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
    const uint32_t raw = hw->int_raw.val;
    i2c_int_raw_reg_t r;
    r.val = raw;
    if (r.arbitration_lost_int_raw || r.time_out_int_raw) return PROBE_LOST;
    if (r.nack_int_raw) nack = true;
    if (r.trans_complete_int_raw) break;  // STOP gesendet
    if (esp_timer_get_time() - t0 >= (int64_t)kScanProbeUs) return PROBE_LOST;
    esp_rom_delay_us(50);
  }
  // Nach STOP muss der Bus frei sein, sonst als Stoerung werten.
  while (i2c_ll_is_bus_busy(hw)) {
    if (esp_timer_get_time() - t0 >= (int64_t)kScanProbeUs) return PROBE_LOST;
    esp_rom_delay_us(50);
  }
  return nack ? PROBE_NACK : PROBE_ACK;
}

static void scan_task(void *) {
  const int64_t t_start = esp_timer_get_time();
  memset(&g_scan, 0, sizeof g_scan);
  g_scan.from = g_scan_cfg.from;
  g_scan.to = g_scan_cfg.to;

  // 1. Slave weg
  g_slave_up = false;
  bool slave_deleted = false;
  if (slave_teardown()) {
    slave_deleted = true;
    // 2. Master-Bus auf denselben Pins und demselben Port
    i2c_master_bus_handle_t bus = scan_bus_create();
    if (bus == nullptr) {
      scan_set_err("i2c_new_master_bus fehlgeschlagen");
    } else {
      int consec_fail = 0;
      for (int a = g_scan.from; a <= g_scan.to && g_scan.err[0] == '\0'; ++a) {
        bool done = false;
        for (int tr = 0; tr < kScanTries && !done && bus != nullptr; ++tr) {
          bool failed_try = false;
          if (!scan_wait_idle(kScanIdleWaitMs)) {
            failed_try = true;  // Bus nicht idle, kein Probe gesendet
          } else {
            const ScanProbe r = scan_probe((uint8_t)a);
            if (r == PROBE_ACK) {
              g_scan.found[g_scan.n_found++] = (uint8_t)a;
              done = true;
            } else if (r == PROBE_NACK) {
              ++g_scan.nack;
              done = true;
            } else {
              // Arbitrationsverlust, Hardware-Timeout oder Bus nach STOP belegt:
              // Controller per Loeschen/Neuanlegen zuruecksetzen (kein Bus-Clear).
              ++g_scan.arb_lost;
              scan_bus_delete(bus);
              bus = scan_bus_create();
              if (bus == nullptr) scan_set_err("Master-Bus nach Arbitrationsverlust nicht neu angelegt");
              failed_try = true;
            }
          }
          if (failed_try && tr + 1 < kScanTries) {
            ++g_scan.retries;
            vTaskDelay(pdMS_TO_TICKS(10));
          }
        }
        if (done) {
          consec_fail = 0;
        } else {
          g_scan.failed[g_scan.n_failed++] = (uint8_t)a;
          if (++consec_fail >= kScanMaxConsecFail) scan_set_err("Bus nicht frei oder Probe-Fehler (Abbruch)");
        }
        vTaskDelay(1);  // Haupt-Loop und MQTT-Keepalive nicht aushungern
      }
      scan_bus_delete(bus);
    }
  } else {
    scan_set_err("Slave konnte nicht geloescht werden");
  }

  // 4. Slave wiederherstellen, auch nach Fehlern
  if (slave_deleted) {
    if (scan_restore_slave()) {
      g_slave_up = true;
    } else {
      scan_set_err("Slave nicht wiederhergestellt");
    }
  } else if (g_slv.dev != nullptr) {
    g_slave_up = true;  // Teardown fehlgeschlagen, alter Slave laeuft weiter
  }
  g_scan.ok = (g_scan.err[0] == '\0');
  g_scan.ms = (uint32_t)((esp_timer_get_time() - t_start) / 1000);
  __atomic_store_n(&g_scan_done, true, __ATOMIC_RELEASE);
  vTaskDelete(nullptr);
}

// Startet den Scan-Task. false wenn schon einer laeuft oder der Task nicht erzeugt werden kann.
static bool scan_start(int sda, int scl, int slave_addr, bool pullup, uint8_t from, uint8_t to) {
  if (g_scan_running) return false;
  g_scan_cfg.sda = sda;
  g_scan_cfg.scl = scl;
  g_scan_cfg.slave_addr = slave_addr;
  g_scan_cfg.pullup = pullup;
  g_scan_cfg.from = from;
  g_scan_cfg.to = to;
  g_scan_done = false;
  g_scan_running = true;
  if (xTaskCreate(scan_task, "i2cscan", 5120, nullptr, 2, nullptr) != pdPASS) {
    g_scan_running = false;
    return false;
  }
  return true;
}

}  // namespace i2cs

#endif  // I2CS_SCAN_H
