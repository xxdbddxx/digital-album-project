#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "i2c_bus.h"

static const char *TAG = "TCA9548A_AHT20_TEST";

#define TEST_I2C_PORT       I2C_NUM_0
#define TEST_I2C_SDA_GPIO   GPIO_NUM_8
#define TEST_I2C_SCL_GPIO   GPIO_NUM_9
#define TEST_I2C_CLOCK_HZ   50000
#define TCA9548A_ADDR       0x70
#define TCA9548A_ALL_OFF    0x00
#define TCA9548A_CHANNEL_2  0x04
#define AHT20_ADDR          0x38
#define READ_INTERVAL_MS    1000

typedef struct {
    uint8_t raw[7];
    uint32_t humidity_raw;
    uint32_t temperature_raw;
    float humidity;
    float temperature;
} aht20_raw_sample_t;

static esp_err_t probe_address(i2c_bus_handle_t bus, uint8_t address, const char *label)
{
    i2c_bus_device_handle_t dev = i2c_bus_device_create(bus, address, 0);
    if (dev == NULL) {
        ESP_LOGE(TAG, "%s 0x%02x create handle failed", label, address);
        return ESP_FAIL;
    }

    uint8_t value = 0;
    esp_err_t ret = i2c_bus_read_byte(dev, NULL_I2C_MEM_ADDR, &value);
    ESP_LOGI(TAG, "%s: %s", label, esp_err_to_name(ret));

    esp_err_t del_ret = i2c_bus_device_delete(&dev);
    return (ret != ESP_OK) ? ret : del_ret;
}

static esp_err_t write_tca9548a(i2c_bus_handle_t bus, uint8_t value)
{
    i2c_bus_device_handle_t tca = i2c_bus_device_create(bus, TCA9548A_ADDR, 0);
    if (tca == NULL) {
        ESP_LOGE(TAG, "Failed to create TCA9548A device handle");
        return ESP_FAIL;
    }

    esp_err_t ret = i2c_bus_write_byte(tca, NULL_I2C_MEM_ADDR, value);
    ESP_LOGI(TAG, "TCA9548A write 0x%02x: %s", value, esp_err_to_name(ret));

    esp_err_t del_ret = i2c_bus_device_delete(&tca);
    if (ret == ESP_OK) {
        ret = del_ret;
    }

    return ret;
}

static esp_err_t select_tca9548a_channel2(i2c_bus_handle_t bus, uint32_t delay_ms)
{
    esp_err_t ret = write_tca9548a(bus, TCA9548A_CHANNEL_2);
    if (ret == ESP_OK) {
        vTaskDelay(pdMS_TO_TICKS(delay_ms));
    }

    return ret;
}

static esp_err_t read_aht20_raw_no_crc(i2c_bus_device_handle_t aht20, aht20_raw_sample_t *sample)
{
    const uint8_t measure_cmd[] = {0xAC, 0x33, 0x00};
    uint8_t buf[7] = {0};

    esp_err_t ret = i2c_bus_write_bytes(aht20, NULL_I2C_MEM_ADDR, sizeof(measure_cmd), measure_cmd);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "AHT20 measure command failed: %s", esp_err_to_name(ret));
        return ret;
    }

    vTaskDelay(pdMS_TO_TICKS(80));

    ret = i2c_bus_read_bytes(aht20, NULL_I2C_MEM_ADDR, sizeof(buf), buf);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "AHT20 raw read failed: %s", esp_err_to_name(ret));
        return ret;
    }

    for (size_t i = 0; i < sizeof(buf); ++i) {
        sample->raw[i] = buf[i];
    }
    sample->humidity_raw = ((uint32_t)buf[1] << 12) | ((uint32_t)buf[2] << 4) | (buf[3] >> 4);
    sample->temperature_raw = (((uint32_t)buf[3] & 0x0F) << 16) | ((uint32_t)buf[4] << 8) | buf[5];
    sample->humidity = (float)sample->humidity_raw * 100.0f / 1048576.0f;
    sample->temperature = (float)sample->temperature_raw * 200.0f / 1048576.0f - 50.0f;

    return ESP_OK;
}

void app_main(void)
{
    const i2c_config_t i2c_bus_conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = TEST_I2C_SDA_GPIO,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_io_num = TEST_I2C_SCL_GPIO,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = TEST_I2C_CLOCK_HZ,
    };

    i2c_bus_handle_t bus = i2c_bus_create(TEST_I2C_PORT, &i2c_bus_conf);
    if (bus == NULL) {
        ESP_LOGE(TAG, "Failed to create I2C bus on SDA=GPIO8 SCL=GPIO9");
        return;
    }

    ESP_LOGI(TAG, "I2C bus ready SDA=GPIO8 SCL=GPIO9");
    probe_address(bus, TCA9548A_ADDR, "probe 0x70 on main I2C bus");

    esp_err_t ret = write_tca9548a(bus, TCA9548A_ALL_OFF);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to disable all TCA9548A channels: %s", esp_err_to_name(ret));
        return;
    }
    vTaskDelay(pdMS_TO_TICKS(50));

    ret = probe_address(bus, AHT20_ADDR, "probe 0x38 with TCA all channels disabled");
    if (ret == ESP_OK) {
        ESP_LOGW(TAG, "board-level 0x38 still present on main I2C bus");
    }

    ret = select_tca9548a_channel2(bus, 50);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to select TCA9548A channel 2: %s", esp_err_to_name(ret));
        return;
    }

    probe_address(bus, AHT20_ADDR, "probe 0x38 with TCA channel 2 enabled");

    i2c_bus_device_handle_t aht20 = i2c_bus_device_create(bus, AHT20_ADDR, 0);
    if (aht20 == NULL) {
        ESP_LOGE(TAG, "Failed to create AHT20 raw device handle");
        return;
    }

    ESP_LOGI(TAG, "breath on sensor / touch sensor to check response");

    bool has_previous_sample = false;
    aht20_raw_sample_t previous_sample = {0};

    while (true) {
        ret = select_tca9548a_channel2(bus, 20);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "Failed to select TCA9548A channel 2: %s", esp_err_to_name(ret));
            vTaskDelay(pdMS_TO_TICKS(READ_INTERVAL_MS));
            continue;
        }

        aht20_raw_sample_t sample = {0};
        ret = read_aht20_raw_no_crc(aht20, &sample);
        if (ret == ESP_OK) {
            float delta_humidity = 0.0f;
            float delta_temperature = 0.0f;

            if (has_previous_sample) {
                delta_humidity = sample.humidity - previous_sample.humidity;
                delta_temperature = sample.temperature - previous_sample.temperature;
            }

            ESP_LOGI(TAG,
                     "raw=%02x %02x %02x %02x %02x %02x %02x humidity_raw=%lu temperature_raw=%lu humidity=%.2f %% temperature=%.2f C delta_humidity=%.2f delta_temperature=%.2f",
                     sample.raw[0],
                     sample.raw[1],
                     sample.raw[2],
                     sample.raw[3],
                     sample.raw[4],
                     sample.raw[5],
                     sample.raw[6],
                     (unsigned long)sample.humidity_raw,
                     (unsigned long)sample.temperature_raw,
                     sample.humidity,
                     sample.temperature,
                     delta_humidity,
                     delta_temperature);

            previous_sample = sample;
            has_previous_sample = true;
        }

        vTaskDelay(pdMS_TO_TICKS(READ_INTERVAL_MS));
    }
}
