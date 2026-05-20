#include "audio_player.h"

#include "esp_log.h"
#include "i2s_output.h"

static const char *TAG = "AUDIO_PLAYER";

static bool s_initialized;
static bool s_is_playing;

typedef struct {
    uint32_t frequency_hz;
    uint32_t duration_ms;
} audio_effect_tone_t;

static esp_err_t audio_player_effect_tone(audio_effect_t effect, audio_effect_tone_t *tone)
{
    if (tone == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    switch (effect) {
    case AUDIO_EFFECT_CLICK:
        *tone = (audio_effect_tone_t) {
            .frequency_hz = 1200,
            .duration_ms = 60,
        };
        return ESP_OK;
    case AUDIO_EFFECT_STARTUP:
        *tone = (audio_effect_tone_t) {
            .frequency_hz = 660,
            .duration_ms = 220,
        };
        return ESP_OK;
    case AUDIO_EFFECT_AROMA_ON:
        *tone = (audio_effect_tone_t) {
            .frequency_hz = 880,
            .duration_ms = 120,
        };
        return ESP_OK;
    case AUDIO_EFFECT_AROMA_OFF:
        *tone = (audio_effect_tone_t) {
            .frequency_hz = 440,
            .duration_ms = 120,
        };
        return ESP_OK;
    case AUDIO_EFFECT_ERROR:
        *tone = (audio_effect_tone_t) {
            .frequency_hz = 220,
            .duration_ms = 300,
        };
        return ESP_OK;
    default:
        return ESP_ERR_INVALID_ARG;
    }
}

esp_err_t audio_player_init(void)
{
    if (s_initialized) {
        return ESP_OK;
    }

    esp_err_t ret = i2s_output_init(NULL);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize audio output: %s", esp_err_to_name(ret));
        return ret;
    }

    s_initialized = true;
    s_is_playing = false;
    return ESP_OK;
}

esp_err_t audio_player_play_effect(audio_effect_t effect)
{
    esp_err_t ret = audio_player_init();
    if (ret != ESP_OK) {
        return ret;
    }

    audio_effect_tone_t tone;
    ret = audio_player_effect_tone(effect, &tone);
    if (ret != ESP_OK) {
        return ret;
    }

    s_is_playing = true;
    ret = i2s_output_play_sine(tone.frequency_hz, tone.duration_ms);
    s_is_playing = false;

    return ret;
}

esp_err_t audio_player_play_pcm(const int16_t *pcm_data,
                                size_t sample_count,
                                uint32_t sample_rate)
{
    if (pcm_data == NULL || sample_count == 0 || sample_rate == 0) {
        return ESP_ERR_INVALID_ARG;
    }

    esp_err_t ret = audio_player_init();
    if (ret != ESP_OK) {
        return ret;
    }

    // TODO: Reconfigure I2S when the output layer supports dynamic sample-rate switching.
    // For now PCM is written using the current I2S initialization sample rate.
    (void)sample_rate;

    s_is_playing = true;
    ret = i2s_output_write(pcm_data, sample_count);
    s_is_playing = false;

    return ret;
}

esp_err_t audio_player_stop(void)
{
    s_is_playing = false;
    return ESP_OK;
}

bool audio_player_is_playing(void)
{
    return s_is_playing;
}
