// can_idle.h - CAN-Transceiver (ISO1042) im Test-Build ruhig halten (mk1-i2c).
// Nur fuer Firmware OHNE CAN-Treiber. Nie in die Produktiv-Firmware uebernehmen (dort ist GPIO18 TWAI-TX).
// TXD (GPIO18): Ausgang, fest High = rezessiv. Der Transceiver kann den Bus so nie dominant ziehen.
// RXD (GPIO16): nur Eingang ohne Pull, der ISO1042 treibt RXD selbst.
// Header-only, C++11.
#ifndef CAN_IDLE_H
#define CAN_IDLE_H

#include <stdint.h>
#include <string.h>

#include <driver/gpio.h>
#include <soc/gpio_reg.h>
#include "esphome/core/log.h"

namespace can_idle {

static int g_txd = -1;
static int g_rxd = -1;
static uint32_t g_fix_count = 0;  // wie oft TXD neu gesetzt werden musste

static bool txd_ok() {
  if (g_txd < 0 || g_txd > 31) return true;
  const uint32_t en = REG_READ(GPIO_ENABLE_REG);
  const uint32_t out = REG_READ(GPIO_OUT_REG);
  return ((en >> g_txd) & 1u) != 0 && ((out >> g_txd) & 1u) != 0;
}

// TXD High setzen, dann als Ausgang schalten: kein kurzer Low-Puls beim Umschalten.
static void drive_txd_high() {
  gpio_set_level((gpio_num_t)g_txd, 1);
  gpio_config_t cfg;
  memset(&cfg, 0, sizeof cfg);
  cfg.pin_bit_mask = 1ULL << g_txd;
  cfg.mode = GPIO_MODE_OUTPUT;
  cfg.pull_up_en = GPIO_PULLUP_ENABLE;  // zusaetzlich, falls der Ausgang je abgeschaltet wird
  cfg.pull_down_en = GPIO_PULLDOWN_DISABLE;
  cfg.intr_type = GPIO_INTR_DISABLE;
  gpio_config(&cfg);
  gpio_set_level((gpio_num_t)g_txd, 1);
}

}  // namespace can_idle

// Einmal ganz frueh in on_boot (priority 1000). txd/rxd < 0 = Pin nicht anfassen.
void can_idle_setup(int txd_gpio, int rxd_gpio) {
  can_idle::g_txd = txd_gpio;
  can_idle::g_rxd = rxd_gpio;
  if (txd_gpio >= 0 && txd_gpio <= 31) can_idle::drive_txd_high();
  if (rxd_gpio >= 0 && rxd_gpio <= 31) {
    gpio_config_t cfg;
    memset(&cfg, 0, sizeof cfg);
    cfg.pin_bit_mask = 1ULL << rxd_gpio;
    cfg.mode = GPIO_MODE_INPUT;
    cfg.pull_up_en = GPIO_PULLUP_DISABLE;
    cfg.pull_down_en = GPIO_PULLDOWN_DISABLE;
    cfg.intr_type = GPIO_INTR_DISABLE;
    gpio_config(&cfg);
  }
  ESP_LOGI("can_idle", "CAN ruhig: TXD=%d High (rezessiv), RXD=%d Eingang", txd_gpio, rxd_gpio);
}

// Periodisch: TXD muss Ausgang High sein, sonst neu setzen.
void can_idle_check() {
  if (can_idle::txd_ok()) return;
  can_idle::g_fix_count++;
  ESP_LOGE("can_idle", "TXD=%d nicht High/Ausgang, neu gesetzt (%u)", can_idle::g_txd,
           (unsigned)can_idle::g_fix_count);
  can_idle::drive_txd_high();
}

#endif  // CAN_IDLE_H
