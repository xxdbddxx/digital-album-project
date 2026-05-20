#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "audio_player.h"
#include "peripherals.h"


void app_main(void)
{
	audio_player_init();
	audio_player_play_effect(AUDIO_EFFECT_STARTUP);
	vTaskDelay(pdMS_TO_TICKS(500));
	audio_player_play_effect(AUDIO_EFFECT_CLICK);
	app_peripherals_init();
}
