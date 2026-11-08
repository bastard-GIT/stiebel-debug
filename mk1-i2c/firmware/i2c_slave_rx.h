// i2c_slave_rx.h - Hardware-I2C-Slave (ESP32-S3) fuer die LWZ 180, mk1-i2c.
// Seit 2026-10-29 erlaubt: aktiver Betrieb als normaler I2C-Slave (Adresse 42, General Call an,
// Clock-Stretching aus). Treiber: IDF 5.5.6 i2c_slave V2 (CONFIG_I2C_ENABLE_SLAVE_DRIVER_VERSION_2=y,
// gesetzt in board_esp32s3_espidf.yaml). V2 liefert on_receive(buffer, length) im ISR-Kontext, V1 liefert
// keine Laenge. Das Adressbyte steht nicht im RX-FIFO: bytes[0] wird als 0x00 (General Call) rekonstruiert.
// Pakete an Adresse 42 sind vom General Call nicht unterscheidbar (auch dann bytes[0]=0x00).
// Uebergabe ISR -> loop ueber SPSC-Ring fester Groesse, ohne malloc und ohne Logging im ISR.
#ifndef I2CS_SLAVE_RX_H
#define I2CS_SLAVE_RX_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "sdkconfig.h"
#include "driver/gpio.h"
#include "driver/i2c_slave.h"
#include "esp_attr.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "crash_trace.h"
#include "hal/i2c_ll.h"
#include "soc/i2c_struct.h"

#include "i2c_frame.h"

#if !CONFIG_I2C_ENABLE_SLAVE_DRIVER_VERSION_2
#error "i2c_slave_rx.h braucht CONFIG_I2C_ENABLE_SLAVE_DRIVER_VERSION_2=y (board_esp32s3_espidf.yaml)"
#endif

namespace i2cs {

static const size_t kSlvRingSlots = 32;     // Zweierpotenz
static const uint32_t kSlvRxDepth = 64;     // receive_buf_depth, = kMaxFrameBytes
static const uint32_t kSlvTxDepth = 32;     // send_buf_depth (ungenutzt, Treiber verlangt Ringbuffer)

struct SlvSlot {
  uint32_t t_ms;
  uint8_t len;               // Nutzdaten ohne Adressbyte
  uint8_t trunc;             // 1 = gekuerzt
  uint8_t data[kMaxFrameBytes];
};

struct SlaveRx {
  SlvSlot slot[kSlvRingSlots];
  volatile uint32_t head;    // Schreibindex (nur ISR)
  volatile uint32_t tail;    // Leseindex (nur loop)
  volatile uint32_t frames;
  volatile uint32_t bytes;
  volatile uint32_t overflow;
  volatile uint32_t errors;
  i2c_slave_dev_handle_t dev;
  bool ok;
};
static SlaveRx g_slv;

// ISR-Kontext (Prioritaet 3). Kein Logging, kein malloc.
static IRAM_ATTR bool slv_on_receive_impl(const i2c_slave_rx_done_event_data_t *ev);

static IRAM_ATTR bool slv_on_receive(i2c_slave_dev_handle_t, const i2c_slave_rx_done_event_data_t *ev, void *) {
  ct::g_tr.isr_in = 1;
  ct::g_tr.isr_cnt = ct::g_tr.isr_cnt + 1;
  const bool r = slv_on_receive_impl(ev);
  ct::g_tr.isr_in = 0;
  return r;
}

static IRAM_ATTR bool slv_on_receive_impl(const i2c_slave_rx_done_event_data_t *ev) {
  SlaveRx &s = g_slv;
  const uint32_t h = s.head;
  const uint32_t t = __atomic_load_n(&s.tail, __ATOMIC_ACQUIRE);
  uint32_t n = ev->length;
  if (ev->buffer == nullptr || n == 0) {
    s.errors++;
    return false;
  }
  if (h - t >= kSlvRingSlots) {
    s.overflow++;
    return false;
  }
  SlvSlot &sl = s.slot[h & (kSlvRingSlots - 1)];
  sl.trunc = 0;
  if (n > sizeof sl.data - 1) {
    n = sizeof sl.data - 1;  // Platz fuer Adressbyte in Frame
    sl.trunc = 1;
  }
  sl.t_ms = (uint32_t)(esp_timer_get_time() / 1000);
  sl.len = (uint8_t)n;
  memcpy(sl.data, ev->buffer, n);
  s.frames++;
  s.bytes += n;
  __atomic_store_n(&s.head, h + 1, __ATOMIC_RELEASE);
  return false;
}

// Setup: sda/scl, 7-Bit-Adresse, General Call an, Stretch aus, interne Pull-ups je Substitution.
static bool slave_setup(int sda, int scl, int addr, bool pullup) {
  memset((void *)&g_slv, 0, sizeof g_slv);
  i2c_slave_config_t cfg;
  memset(&cfg, 0, sizeof cfg);
  cfg.i2c_port = 0;
  cfg.sda_io_num = (gpio_num_t)sda;
  cfg.scl_io_num = (gpio_num_t)scl;
  cfg.clk_source = I2C_CLK_SRC_DEFAULT;
  cfg.send_buf_depth = kSlvTxDepth;
  cfg.receive_buf_depth = kSlvRxDepth;
  cfg.slave_addr = (uint16_t)addr;
  cfg.addr_bit_len = I2C_ADDR_BIT_LEN_7;
  cfg.intr_priority = 3;
  cfg.flags.enable_internal_pullup = pullup ? 1 : 0;
  cfg.flags.broadcast_en = 1;
  esp_err_t r = i2c_new_slave_device(&cfg, &g_slv.dev);
  if (r != ESP_OK) {
    ESP_LOGE("i2cs", "i2c_new_slave_device fehlgeschlagen: %d", (int)r);
    return false;
  }
  i2c_slave_event_callbacks_t cbs;
  memset(&cbs, 0, sizeof cbs);
  cbs.on_receive = slv_on_receive;
  r = i2c_slave_register_event_callbacks(g_slv.dev, &cbs, nullptr);
  if (r != ESP_OK) {
    ESP_LOGE("i2cs", "i2c_slave_register_event_callbacks fehlgeschlagen: %d", (int)r);
    return false;
  }
  // V2 setzt Stretch immer an (kein stretch_en in der Config). Direkt abschalten: SCL wird nie gehalten.
  i2c_ll_slave_enable_scl_stretch(&I2C0, false);
  i2c_ll_update(&I2C0);
  g_slv.ok = true;
  return true;
}

// Slave-Device loeschen (fuer den Bus-Scan, i2c_scan.h). true wenn kein Device mehr existiert.
static bool slave_teardown() {
  if (g_slv.dev == nullptr) return true;
  const esp_err_t r = i2c_del_slave_device(g_slv.dev);
  if (r != ESP_OK) {
    ESP_LOGE("i2cs", "i2c_del_slave_device fehlgeschlagen: %d", (int)r);
    return false;
  }
  g_slv.dev = nullptr;
  g_slv.ok = false;
  return true;
}

// Haupt-Loop: naechstes Paket als Frame holen. false wenn leer.
static bool slave_pop(Frame &f) {
  SlaveRx &s = g_slv;
  const uint32_t t = s.tail;
  if (__atomic_load_n(&s.head, __ATOMIC_ACQUIRE) == t) return false;
  const SlvSlot &sl = s.slot[t & (kSlvRingSlots - 1)];
  memset(&f, 0, sizeof f);
  f.t_ms = sl.t_ms;
  f.bytes[0] = 0x00;  // General Call, Write
  memcpy(f.bytes + 1, sl.data, sl.len);
  f.len = (uint8_t)(sl.len + 1);
  f.ack_bits = (f.len >= 64) ? ~0ULL : ((1ULL << f.len) - 1ULL);
  f.flags = sl.trunc ? F_OVERLEN : 0;
  __atomic_store_n(&s.tail, t + 1, __ATOMIC_RELEASE);
  return true;
}

}  // namespace i2cs

#endif  // I2CS_SLAVE_RX_H
