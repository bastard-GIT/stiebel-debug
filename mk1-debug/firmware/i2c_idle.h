// I2C-Pins der Traegerplatine (TI ISO1540, bidirektional, Open-Drain):
// Diese Firmware nutzt I2C nicht, hoert nur mit / stoert nicht. Ein floatender
// oder auf Low gezogener ESP-Pin wuerde den I2C-Bus der LWZ 180 auf Low ziehen.
// Darum frueh beim Boot: nur Eingang + interner Pull-up, nie Output.
#pragma once
#include <driver/gpio.h>
#include <esp_log.h>

inline void i2c_pins_idle(int sda, int scl) {
  gpio_config_t cfg = {};
  cfg.pin_bit_mask = (1ULL << sda) | (1ULL << scl);
  cfg.mode = GPIO_MODE_INPUT;
  cfg.pull_up_en = GPIO_PULLUP_ENABLE;
  cfg.pull_down_en = GPIO_PULLDOWN_DISABLE;
  cfg.intr_type = GPIO_INTR_DISABLE;
  esp_err_t err = gpio_config(&cfg);
  ESP_LOGI("i2c_idle", "SDA=GPIO%d SCL=GPIO%d input+pullup, gpio_config=%d", sda, scl, (int)err);
}
