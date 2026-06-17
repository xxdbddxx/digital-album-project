#include "tca9548a.h"

#include "aht20_sensor.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "TCA9548A";

static SemaphoreHandle_t s_i2c_bus_mutex = NULL;
static i2c_bus_handle_t s_tca_i2c_bus = NULL;

void i2c_bus_mutex_init(void) { s_i2c_bus_mutex = xSemaphoreCreateMutex(); }

void i2c_bus_lock(void) {
  if (s_i2c_bus_mutex != NULL) {
    if (xSemaphoreTake(s_i2c_bus_mutex, pdMS_TO_TICKS(100)) != pdTRUE) {
      ESP_LOGW("I2C_BUS", "I2C mutex timeout (100ms)");
    }
  }
}

void i2c_bus_unlock(void) {
  if (s_i2c_bus_mutex != NULL) {
    xSemaphoreGive(s_i2c_bus_mutex);
  }
}

static i2c_bus_handle_t tca9548a_get_bus(void) {
  if (s_tca_i2c_bus != NULL) {
    return s_tca_i2c_bus;
  }

  const i2c_config_t i2c_bus_conf = {
      .mode = I2C_MODE_MASTER,
      .sda_io_num = I2C_MASTER_SDA_IO,
      .sda_pullup_en = GPIO_PULLUP_ENABLE,
      .scl_io_num = I2C_MASTER_SCL_IO,
      .scl_pullup_en = GPIO_PULLUP_ENABLE,
      .master.clk_speed = I2C_MASTER_FREQ_HZ,
  };
  s_tca_i2c_bus = i2c_bus_create(I2C_MASTER_NUM, &i2c_bus_conf);
  return s_tca_i2c_bus;
}

static esp_err_t tca9548a_write_control(uint8_t value) {
  i2c_bus_handle_t bus = tca9548a_get_bus();
  if (bus == NULL) {
    ESP_LOGE(TAG, "Failed to create I2C bus");
    return ESP_ERR_INVALID_STATE;
  }

  i2c_bus_device_handle_t dev =
      i2c_bus_device_create(bus, TCA9548A_ADDR, I2C_MASTER_FREQ_HZ);
  if (dev == NULL) {
    ESP_LOGE(TAG, "Failed to create device handle");
    return ESP_ERR_INVALID_STATE;
  }

  esp_err_t ret = i2c_bus_write_byte(dev, NULL_I2C_MEM_ADDR, value);
  esp_err_t del_ret = i2c_bus_device_delete(&dev);
  return (ret == ESP_OK) ? del_ret : ret;
}

esp_err_t tca9548a_select_channel(uint8_t channel) {
  if (channel > 7) {
    ESP_LOGE(TAG, "Invalid channel number: %d", channel);
    return ESP_ERR_INVALID_ARG;
  }

  return tca9548a_write_control(1U << channel);
}

esp_err_t tca9548a_disable_all_channels(void) {
  return tca9548a_write_control(0x00);
}
