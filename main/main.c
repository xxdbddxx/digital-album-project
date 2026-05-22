#include <stdio.h>
#include "app_event.h"
#include "audio_player.h"
#include "aroma_ctrl.h"
#include "peripherals.h"


void app_main(void)
{
	audio_player_init();
	aroma_init();
	app_event_init();
	app_event_send(APP_EVENT_PLAY_STARTUP, 0);
	// app_event_send(APP_EVENT_AROMA_CH1_ON, 0);
	// app_event_send_text(APP_EVENT_PLAY_TTS_FILE, "/spiffs/tts_reply.wav");
	app_peripherals_init();
}
