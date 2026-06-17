#include "pcf8574_io.h"

#include "aht20_sensor.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "tca9548a.h"

static const char *TAG = "PCF8574_IO";

#define PCF8574_PIN_COUNT 8

#ifndef PCF8574_I2C_PORT
#define PCF8574_I2C_PORT I2C_NUM_0
#endif

#ifndef PCF8574_I2C_ADDR
#define PCF8574_I2C_ADDR 0x20
#endif

static bool s_initialized;
static uint8_t s_port_state = 0xFF;
static i2c_bus_handle_t s_pcf_i2c_bus = NULL;

static bool pcf8574_pin_is_valid(uint8_t pin) {
  return pin < PCF8574_PIN_COUNT;
}

static esp_err_t pcf8574_lock(void) {
  i2c_bus_lock();
  return ESP_OK;
}

static void pcf8574_unlock(void) { i2c_bus_unlock(); }

static i2c_bus_handle_t pcf8574_get_bus(void) {
  if (s_pcf_i2c_bus != NULL) {
    return s_pcf_i2c_bus;
  }

  const i2c_config_t i2c_bus_conf = {
      .mode = I2C_MODE_MASTER,
      .sda_io_num = I2C_MASTER_SDA_IO,
      .sda_pullup_en = GPIO_PULLUP_ENABLE,
      .scl_io_num = I2C_MASTER_SCL_IO,
      .scl_pullup_en = GPIO_PULLUP_ENABLE,
      .master.clk_speed = I2C_MASTER_FREQ_HZ,
  };
  s_pcf_i2c_bus = i2c_bus_create(PCF8574_I2C_PORT, &i2c_bus_conf);
  return s_pcf_i2c_bus;
}

static esp_err_t pcf8574_with_device(esp_err_t (*fn)(i2c_bus_device_handle_t,
                                                     void *),
                                     void *ctx) {
  i2c_bus_handle_t bus = pcf8574_get_bus();
  if (bus == NULL) {
    return ESP_ERR_INVALID_STATE;
  }

  i2c_bus_device_handle_t dev =
      i2c_bus_device_create(bus, PCF8574_I2C_ADDR, I2C_MASTER_FREQ_HZ);
  if (dev == NULL) {
    return ESP_ERR_INVALID_STATE;
  }

  esp_err_t ret = fn(dev, ctx);
  esp_err_t del_ret = i2c_bus_device_delete(&dev);
  return (ret == ESP_OK) ? del_ret : ret;
}

static esp_err_t pcf8574_write_device(i2c_bus_device_handle_t dev, void *ctx) {
  const uint8_t value = *(const uint8_t *)ctx;
  return i2c_bus_write_byte(dev, NULL_I2C_MEM_ADDR, value);
}

static esp_err_t pcf8574_read_device(i2c_bus_device_handle_t dev, void *ctx) {
  return i2c_bus_read_byte(dev, NULL_I2C_MEM_ADDR, (uint8_t *)ctx);
}

esp_err_t pcf8574_init(void) {
  esp_err_t ret = pcf8574_lock();
  if (ret != ESP_OK) {
    return ret;
  }

  if (s_initialized) {
    pcf8574_unlock();
    return ESP_OK;
  }

  ret = pcf8574_with_device(pcf8574_read_device, &s_port_state);
  if (ret != ESP_OK) {
    ESP_LOGE(TAG, "Failed to probe PCF8574 at 0x%02x: %s", PCF8574_I2C_ADDR,
             esp_err_to_name(ret));
    pcf8574_unlock();
    return ret;
  }

  s_port_state = 0x00;
  ret = pcf8574_with_device(pcf8574_write_device, &s_port_state);
  if (ret != ESP_OK) {
    ESP_LOGW(TAG, "Failed to force pins LOW: %s", esp_err_to_name(ret));
    pcf8574_unlock();
    return ret;
  }

  s_initialized = true;
  ESP_LOGI(TAG, "Initialized addr=0x%02x state=0x%02x", PCF8574_I2C_ADDR,
           s_port_state);
  pcf8574_unlock();
  return ESP_OK;
}

esp_err_t pcf8574_io_init(void) { return pcf8574_init(); }

esp_err_t pcf8574_write_pin(ext_io_pin_t pin, uint32_t level) {
  if (!pcf8574_pin_is_valid((uint8_t)pin)) {
    ESP_LOGE(TAG, "Invalid pin: %u", pin);
    return ESP_ERR_INVALID_ARG;
  }

  esp_err_t ret = pcf8574_init();
  if (ret != ESP_OK) {
    return ret;
  }

  ret = pcf8574_lock();
  if (ret != ESP_OK) {
    return ret;
  }

  uint8_t next_state =
      level ? (s_port_state | BIT(pin)) : (s_port_state & (uint8_t)~BIT(pin));
  ret = pcf8574_with_device(pcf8574_write_device, &next_state);
  if (ret == ESP_OK) {
    s_port_state = next_state;
  } else {
    ESP_LOGE(TAG, "Write pin %u failed: %s", pin, esp_err_to_name(ret));
  }

  pcf8574_unlock();
  return ret;
}

esp_err_t pcf8574_read_pin(ext_io_pin_t pin, uint32_t *level) {
  if (!pcf8574_pin_is_valid((uint8_t)pin) || level == NULL) {
    return ESP_ERR_INVALID_ARG;
  }

  uint8_t value = 0;
  esp_err_t ret = pcf8574_read_port(&value);
  if (ret == ESP_OK) {
    *level = (value & BIT(pin)) ? 1 : 0;
  }

  return ret;
}

esp_err_t pcf8574_write_port(uint8_t value) {
  esp_err_t ret = pcf8574_init();
  if (ret != ESP_OK) {
    return ret;
  }

  ret = pcf8574_lock();
  if (ret != ESP_OK) {
    return ret;
  }

  ret = pcf8574_with_device(pcf8574_write_device, &value);
  if (ret == ESP_OK) {
    s_port_state = value;
  } else {
    ESP_LOGE(TAG, "Port write failed: %s", esp_err_to_name(ret));
  }

  pcf8574_unlock();
  return ret;
}

esp_err_t pcf8574_read_port(uint8_t *value) {
  if (value == NULL) {
    return ESP_ERR_INVALID_ARG;
  }

  esp_err_t ret = pcf8574_init();
  if (ret != ESP_OK) {
    return ret;
  }

  ret = pcf8574_lock();
  if (ret != ESP_OK) {
    return ret;
  }

  ret = pcf8574_with_device(pcf8574_read_device, value);
  if (ret == ESP_OK) {
    s_port_state = *value;
  } else {
    ESP_LOGE(TAG, "Port read failed: %s", esp_err_to_name(ret));
  }

  pcf8574_unlock();
  return ret;
}

uint8_t pcf8574_get_cached_port(void) { return s_port_state; }
