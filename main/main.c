#include <stdio.h>
#include "app_event.h"
#include "audio_player.h"
#include "aroma_ctrl.h"
#include "esp_log.h"
#include "peripherals.h"

static const char *TAG = "MAIN";

void app_main(void)
{
	esp_err_t ret = audio_player_init();
	if (ret != ESP_OK) {
		ESP_LOGE(TAG, "audio_player_init failed: %s", esp_err_to_name(ret));
	}

	ret = aroma_init();
	if (ret != ESP_OK) {
		ESP_LOGW(TAG, "aroma_init failed, continue without PCF8574 hardware: %s", esp_err_to_name(ret));
	}

	ret = app_event_init();
	if (ret != ESP_OK) {
		ESP_LOGE(TAG, "app_event_init failed: %s", esp_err_to_name(ret));
		return;
	}

	ret = app_event_send(APP_EVENT_PLAY_STARTUP, 0);
	if (ret != ESP_OK) {
		ESP_LOGE(TAG, "Failed to send startup event: %s", esp_err_to_name(ret));
	}

	// app_event_send(APP_EVENT_AROMA_CH1_ON, 0);
	// app_event_send_text(APP_EVENT_PLAY_TTS_FILE, "/spiffs/tts_reply.wav");
	// TODO: app_peripherals_init() currently initializes PCF8574/HE30 again.
	// Keep this for now, but later unify app startup around one peripheral entry point.
	app_peripherals_init();
}
