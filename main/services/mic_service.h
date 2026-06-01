#ifndef MIC_SERVICE_H
#define MIC_SERVICE_H

#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define MIC_SERVICE_RMS_DURATION_MS 200

/*
 * Initial RMS threshold for voice activity detection.
 * This value must be tuned with the real INMP441 hardware, enclosure, and
 * ambient noise level.
 */
#define MIC_SERVICE_DEFAULT_THRESHOLD 500.0f

esp_err_t mic_service_init(void);
esp_err_t mic_service_deinit(void);

esp_err_t mic_service_get_rms(float *rms);
esp_err_t mic_service_is_voice_active(bool *active);

esp_err_t mic_service_set_threshold(float threshold);
float mic_service_get_threshold(void);

bool mic_service_is_initialized(void);

#ifdef __cplusplus
}
#endif

#endif
