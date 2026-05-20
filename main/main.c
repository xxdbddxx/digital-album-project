#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "i2s_output.h"
#include "peripherals.h"


void app_main(void)
{
	i2s_output_init(NULL);
	vTaskDelay(pdMS_TO_TICKS(500));
	i2s_output_play_test_tone();
	app_peripherals_init();
}
