#include "mic_service.h"

#include <math.h>

#include "esp_log.h"
#include "i2s_mic_input.h"

static const char *TAG = "MIC_SERVICE";

static bool s_initialized;
static float s_threshold = MIC_SERVICE_DEFAULT_THRESHOLD;

esp_err_t mic_service_init(void)
{
    if (s_initialized) {
        ESP_LOGI(TAG, "mic service already initialized");
        return ESP_OK;
    }

    esp_err_t ret = i2s_mic_input_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2s_mic_input_init failed: %s", esp_err_to_name(ret));
        return ret;
    }

    s_initialized = true;
    ESP_LOGI(TAG, "mic service initialized, threshold=%.2f", s_threshold);
    return ESP_OK;
}

esp_err_t mic_service_deinit(void)
{
    if (!s_initialized) {
        ESP_LOGW(TAG, "mic service is not initialized");
        return ESP_OK;
    }

    esp_err_t ret = i2s_mic_input_deinit();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2s_mic_input_deinit failed: %s", esp_err_to_name(ret));
        return ret;
    }

    s_initialized = false;
    ESP_LOGI(TAG, "mic service deinitialized");
    return ESP_OK;
}

esp_err_t mic_service_get_rms(float *rms)
{
    if (rms == NULL) {
        ESP_LOGE(TAG, "rms must not be NULL");
        return ESP_ERR_INVALID_ARG;
    }

    if (!s_initialized) {
        ESP_LOGW(TAG, "mic service is not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    esp_err_t ret = i2s_mic_input_read_rms(rms, MIC_SERVICE_RMS_DURATION_MS);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "read rms failed: %s", esp_err_to_name(ret));
        return ret;
    }

    ESP_LOGI(TAG, "rms=%.2f", *rms);
    return ESP_OK;
}

esp_err_t mic_service_is_voice_active(bool *active)
{
    if (active == NULL) {
        ESP_LOGE(TAG, "active must not be NULL");
        return ESP_ERR_INVALID_ARG;
    }

    float rms = 0.0f;
    esp_err_t ret = mic_service_get_rms(&rms);
    if (ret != ESP_OK) {
        return ret;
    }

    *active = rms >= s_threshold;
    ESP_LOGI(TAG, "voice_active=%d rms=%.2f threshold=%.2f", *active, rms, s_threshold);
    return ESP_OK;
}

esp_err_t mic_service_set_threshold(float threshold)
{
    if (!isfinite(threshold) || threshold < 0.0f) {
        ESP_LOGE(TAG, "invalid threshold: %.2f", threshold);
        return ESP_ERR_INVALID_ARG;
    }

    s_threshold = threshold;
    ESP_LOGI(TAG, "threshold set to %.2f", s_threshold);
    return ESP_OK;
}

float mic_service_get_threshold(void)
{
    return s_threshold;
}

bool mic_service_is_initialized(void)
{
    return s_initialized;
}
