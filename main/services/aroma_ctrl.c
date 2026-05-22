#include "aroma_ctrl.h"

#include <stdint.h>
#include "esp_log.h"
#include "pcf8574_io.h"

static const char *TAG = "AROMA_CTRL";

#ifndef AROMA_ACTIVE_LEVEL
#define AROMA_ACTIVE_LEVEL true
#endif

static const uint8_t s_aroma_pins[AROMA_CH_MAX] = {
    [AROMA_CH_1] = 0,
    [AROMA_CH_2] = 1,
    [AROMA_CH_3] = 2,
};

static bool s_initialized;
static bool s_channel_state[AROMA_CH_MAX];

static bool aroma_channel_is_valid(aroma_channel_t channel)
{
    return channel >= AROMA_CH_1 && channel < AROMA_CH_MAX;
}

static bool aroma_output_level(bool on)
{
    return on ? AROMA_ACTIVE_LEVEL : !AROMA_ACTIVE_LEVEL;
}

esp_err_t aroma_init(void)
{
    if (s_initialized) {
        return ESP_OK;
    }

    esp_err_t ret = pcf8574_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "PCF8574 init failed: %s", esp_err_to_name(ret));
        return ret;
    }

    for (int i = 0; i < AROMA_CH_MAX; ++i) {
        s_channel_state[i] = false;
    }

    s_initialized = true;
    ESP_LOGI(TAG, "Initialized %d channels, active_level=%d", AROMA_CH_MAX, AROMA_ACTIVE_LEVEL);
    return ESP_OK;
}

esp_err_t aroma_set(aroma_channel_t channel, bool on)
{
    if (!aroma_channel_is_valid(channel)) {
        ESP_LOGE(TAG, "Invalid channel: %d", channel);
        return ESP_ERR_INVALID_ARG;
    }

    esp_err_t ret = aroma_init();
    if (ret != ESP_OK) {
        return ret;
    }

    ret = pcf8574_write_pin(s_aroma_pins[channel], aroma_output_level(on));
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Set channel %d to %d failed: %s", channel, on, esp_err_to_name(ret));
        return ret;
    }

    s_channel_state[channel] = on;
    ESP_LOGI(TAG, "Channel %d set to %d", channel, on);
    return ESP_OK;
}

esp_err_t aroma_set_all(bool on)
{
    esp_err_t ret = aroma_init();
    if (ret != ESP_OK) {
        return ret;
    }

    for (aroma_channel_t channel = AROMA_CH_1; channel < AROMA_CH_MAX; ++channel) {
        ret = aroma_set(channel, on);
        if (ret != ESP_OK) {
            return ret;
        }
    }

    return ESP_OK;
}

esp_err_t aroma_toggle(aroma_channel_t channel)
{
    if (!aroma_channel_is_valid(channel)) {
        ESP_LOGE(TAG, "Invalid channel: %d", channel);
        return ESP_ERR_INVALID_ARG;
    }

    return aroma_set(channel, !s_channel_state[channel]);
}

bool aroma_get_state(aroma_channel_t channel)
{
    if (!aroma_channel_is_valid(channel)) {
        ESP_LOGE(TAG, "Invalid channel: %d", channel);
        return false;
    }

    return s_channel_state[channel];
}
