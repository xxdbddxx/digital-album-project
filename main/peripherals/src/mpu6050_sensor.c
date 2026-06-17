#include "mpu6050_sensor.h"

#include "driver/gpio.h"
#include "esp_log.h"
#include "i2c_bus.h"
#include "tca9548a.h"
#include "ui_main.h"
#include <stdio.h>
#include <stdlib.h>

#define MPU6050_I2C_NUM I2C_NUM_0
#define MPU6050_ADDR 0x68
#define MPU6050_PWR_MGMT_1 0x6B
#define MPU6050_ACCEL_XOUT_H 0x3B

#define MPU6050_I2C_SDA_IO 8
#define MPU6050_I2C_SCL_IO 9
#define MPU6050_I2C_FREQ_HZ 400000

static const char *TAG = "MPU6050";
static bool mpu6050_initialized = false;
static i2c_bus_handle_t s_mpu_i2c_bus = NULL;

static int16_t s_last_accel_x = 0;
static int16_t s_last_accel_y = 0;
static int16_t s_last_accel_z = 0;

static i2c_bus_handle_t mpu6050_get_bus(void) {
  if (s_mpu_i2c_bus != NULL) {
    return s_mpu_i2c_bus;
  }

  const i2c_config_t i2c_bus_conf = {
      .mode = I2C_MODE_MASTER,
      .sda_io_num = MPU6050_I2C_SDA_IO,
      .sda_pullup_en = GPIO_PULLUP_ENABLE,
      .scl_io_num = MPU6050_I2C_SCL_IO,
      .scl_pullup_en = GPIO_PULLUP_ENABLE,
      .master.clk_speed = MPU6050_I2C_FREQ_HZ,
  };
  s_mpu_i2c_bus = i2c_bus_create(MPU6050_I2C_NUM, &i2c_bus_conf);
  return s_mpu_i2c_bus;
}

static esp_err_t mpu6050_with_device(esp_err_t (*fn)(i2c_bus_device_handle_t,
                                                     void *),
                                     void *ctx) {
  i2c_bus_handle_t bus = mpu6050_get_bus();
  if (bus == NULL) {
    return ESP_ERR_INVALID_STATE;
  }

  i2c_bus_device_handle_t dev =
      i2c_bus_device_create(bus, MPU6050_ADDR, MPU6050_I2C_FREQ_HZ);
  if (dev == NULL) {
    return ESP_ERR_INVALID_STATE;
  }

  esp_err_t ret = fn(dev, ctx);
  esp_err_t del_ret = i2c_bus_device_delete(&dev);
  return (ret == ESP_OK) ? del_ret : ret;
}

static esp_err_t mpu6050_write_pwr(i2c_bus_device_handle_t dev, void *ctx) {
  (void)ctx;
  return i2c_bus_write_byte(dev, MPU6050_PWR_MGMT_1, 0x00);
}

typedef struct {
  uint8_t *data;
  size_t len;
} mpu6050_read_ctx_t;

static esp_err_t mpu6050_read_accel_regs(i2c_bus_device_handle_t dev,
                                         void *ctx) {
  mpu6050_read_ctx_t *read_ctx = (mpu6050_read_ctx_t *)ctx;
  return i2c_bus_read_bytes(dev, MPU6050_ACCEL_XOUT_H, read_ctx->len,
                            read_ctx->data);
}

esp_err_t mpu6050_init(void) {
  if (mpu6050_initialized) {
    return ESP_OK;
  }

  i2c_bus_lock();
  esp_err_t err = tca9548a_select_channel(1);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Init: Failed to select channel 1: %s", esp_err_to_name(err));
    i2c_bus_unlock();
    return err;
  }

  err = mpu6050_with_device(mpu6050_write_pwr, NULL);

  tca9548a_disable_all_channels();
  i2c_bus_unlock();

  if (err == ESP_OK) {
    ESP_LOGI(TAG, "MPU6050 initialized successfully.");
    mpu6050_initialized = true;
  } else {
    ESP_LOGW(TAG, "Failed to wake up MPU6050. Check wiring.");
  }
  return err;
}

esp_err_t mpu6050_read_accel(int16_t *x, int16_t *y, int16_t *z) {
  if (!mpu6050_initialized || !x || !y || !z) {
    return ESP_ERR_INVALID_STATE;
  }

  uint8_t data[6] = {0};
  mpu6050_read_ctx_t read_ctx = {
      .data = data,
      .len = sizeof(data),
  };
  esp_err_t err = mpu6050_with_device(mpu6050_read_accel_regs, &read_ctx);

  if (err == ESP_OK) {
    *x = (int16_t)((data[0] << 8) | data[1]);
    *y = (int16_t)((data[2] << 8) | data[3]);
    *z = (int16_t)((data[4] << 8) | data[5]);
    s_last_accel_x = *x;
    s_last_accel_y = *y;
    s_last_accel_z = *z;
  }
  return err;
}

void peripheral_mpu6050(void) {
  static int portrait_count = 0;
  static int landscape_count = 0;
  static bool current_is_portrait = true;

  int16_t accel_x = 0, accel_y = 0, accel_z = 0;

  i2c_bus_lock();
  esp_err_t ret = tca9548a_select_channel(1);
  if (ret != ESP_OK) {
    ESP_LOGE(TAG, "Failed to select channel: %s", esp_err_to_name(ret));
    goto cleanup;
  }

  if (mpu6050_read_accel(&accel_x, &accel_y, &accel_z) == ESP_OK) {
    bool is_portrait = abs(accel_y) > abs(accel_x);

    if (is_portrait) {
      portrait_count++;
      landscape_count = 0;
    } else {
      landscape_count++;
      portrait_count = 0;
    }

    if (portrait_count >= 3 && !current_is_portrait) {
      current_is_portrait = true;
      ESP_LOGI(TAG, "Orientation changed to Portrait.");
      ui_set_screen_rotation(true);
    } else if (landscape_count >= 2 && current_is_portrait) {
      current_is_portrait = false;
      ESP_LOGI(TAG, "Orientation changed to Landscape.");
      ui_set_screen_rotation(false);
    }
  } else {
    ESP_LOGE(TAG, "Failed to read: %s", esp_err_to_name(ret));
  }

cleanup:
  tca9548a_disable_all_channels();
  i2c_bus_unlock();
}

esp_err_t mpu6050_get_latest_accel(int16_t *x, int16_t *y, int16_t *z) {
  if (!x || !y || !z) {
    return ESP_ERR_INVALID_ARG;
  }
  *x = s_last_accel_x;
  *y = s_last_accel_y;
  *z = s_last_accel_z;
  return ESP_OK;
}
