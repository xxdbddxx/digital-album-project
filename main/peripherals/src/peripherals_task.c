/**
 * @file peripherals_task.c
 * @brief 外围设备任务控制逻辑。
 *
 * 管理温湿度传感器与香薰雾化器的定期轮询与状态同步。
 */
#include "peripherals_task.h"
#include "aht20_sensor.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/task.h"
#include "he30.h"
#define ENABLE_AHT20_FEATURES 0
#define ENABLE_MPU6050_FEATURES 0
#if ENABLE_MPU6050_FEATURES
#include "mpu6050_sensor.h"
#endif
#include "pcf8574_io.h"
#include "stream_player.h"
#include "tca9548a.h"
#include "voice_assistant.h"
#include "voice_io.h"
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static TaskHandle_t audio_task_handle = NULL;
static volatile bool audio_is_playing = false;
static bool s_audio_inited = false;
static bool s_mic_diagnostics_enabled = false;

static int s_aroma_level[3] = {3, 3, 3};
static bool s_aroma_target[3] = {false, false, false};

void peripherals_set_aroma(int channel, bool on, int level) {
  if (channel >= 0 && channel < 3) {
    s_aroma_target[channel] = on;
    if (level >= 1 && level <= 3) {
      s_aroma_level[channel] = level;
    }
    ESP_LOGI("peripherals", "Aroma CH %d set to %s, level: %d", channel,
             on ? "ON" : "OFF", s_aroma_level[channel]);
  }
}

static bool s_humidity_blocked = false;

void peripherals_mist_level_up(void) {
  for (int i = 0; i < 3; i++) {
    if (s_aroma_target[i] && s_aroma_level[i] < 3) {
      s_aroma_level[i]++;
      ESP_LOGI("peripherals", "Mist CH %d level up → %d", i, s_aroma_level[i]);
    }
  }
}

void peripherals_mist_level_down(void) {
  for (int i = 0; i < 3; i++) {
    if (s_aroma_target[i] && s_aroma_level[i] > 1) {
      s_aroma_level[i]--;
      ESP_LOGI("peripherals", "Mist CH %d level down → %d", i, s_aroma_level[i]);
    }
  }
}

bool peripherals_is_humidity_blocked(void) {
  return s_humidity_blocked;
}

int peripherals_mist_channel_from_string(const char *ch_str) {
  if (!ch_str) return 0;
  if (strcmp(ch_str, "mint") == 0)    return 0;
  if (strcmp(ch_str, "jasmine") == 0) return 1;
  if (strcmp(ch_str, "rose") == 0)    return 2;
  return 0; // "none" or unknown → default channel 0
}

static void audio_test_task(void *arg) {
  uint32_t duration_ms = (uint32_t)(uintptr_t)arg;
  TickType_t started_at = xTaskGetTickCount();

  // 播放测试 Beep 音前，强制对功放做冷启动物理复位以防抖和防止锁死
  voice_io_spk_force_reset();

  int16_t *beep = heap_caps_malloc(720, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!beep) {
    ESP_LOGE("audio_test", "Failed to allocate memory for beep");
    audio_task_handle = NULL;
    vTaskDelete(NULL);
    return;
  }
  for (int i = 0; i < 360; i++) {
    // 降低数字振幅，避免满载 30000 造成的极限爆音
    beep[i] = (int16_t)(10000.0f * sinf(2.0f * M_PI * 440.0f * i / 16000.0f));
  }

  ESP_LOGI("audio_test", "Audio task started, volume=%d%%, duration=%s",
           voice_io_get_spk_volume(),
           duration_ms > 0 ? "limited" : "manual");
  uint32_t loops = 0;
  // 360 samples / 16000 Hz = 22.5ms，等待 22ms 匹配消费速率防止 buffer 爆满
  const TickType_t chunk_delay = pdMS_TO_TICKS(22);
  while (audio_is_playing) {
    if (duration_ms > 0 &&
        xTaskGetTickCount() - started_at >= pdMS_TO_TICKS(duration_ms)) {
      ESP_LOGI("audio_test", "Automatic speaker test completed after %lu ms",
               (unsigned long)duration_ms);
      audio_is_playing = false;
      break;
    }

    esp_err_t err = voice_io_spk_play_stream((const uint8_t *)beep, 720);
    if (err == ESP_ERR_TIMEOUT) {
      // Ringbuf 消费不及，延长等待
      vTaskDelay(pdMS_TO_TICKS(50));
      continue;
    } else if (err != ESP_OK) {
      ESP_LOGE("audio_test", "Play fail: %s", esp_err_to_name(err));
      vTaskDelay(pdMS_TO_TICKS(100));
    } else {
      loops++;
      if (loops % 88 == 0) {
        ESP_LOGI("audio_test", "Playing audio... Elapsed: %lu seconds",
                 (unsigned long)(loops / 88));
      }
      // 匹配音频消费速率，防止 CPU 速度狂塞 ringbuf
      vTaskDelay(chunk_delay);
    }
  }

  ESP_LOGI("audio_test", "Audio task stopped, cleaning up...");
  voice_io_spk_stop();
  free(beep);
  audio_task_handle = NULL;
  vTaskDelete(NULL);
}

static void __attribute__((unused)) melody_test_task(void *arg) {
  (void)arg;
  enum { CHUNK_SAMPLES = 320 }; // 16kHz 下 20ms
  static const uint16_t notes_hz[] = {
      262, 262, 392, 392, 440, 440, 392,
      349, 349, 330, 330, 294, 294, 262,
  };
  static const uint16_t durations_ms[] = {
      300, 300, 300, 300, 300, 300, 600,
      300, 300, 300, 300, 300, 300, 600,
  };

  voice_io_spk_force_reset();
  int16_t *pcm = heap_caps_malloc(
      CHUNK_SAMPLES * sizeof(int16_t),
      MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!pcm) {
    ESP_LOGE("audio_test", "Failed to allocate local melody buffer");
    audio_is_playing = false;
    audio_task_handle = NULL;
    vTaskDelete(NULL);
    return;
  }

  ESP_LOGI("audio_test", "Local melody started, volume=%d%%",
           voice_io_get_spk_volume());
  float phase = 0.0f;
  for (size_t note = 0;
       note < sizeof(notes_hz) / sizeof(notes_hz[0]) && audio_is_playing;
       note++) {
    const int chunks = durations_ms[note] / 20;
    const float phase_step =
        2.0f * M_PI * (float)notes_hz[note] / 16000.0f;
    for (int chunk = 0; chunk < chunks && audio_is_playing; chunk++) {
      for (int i = 0; i < CHUNK_SAMPLES; i++) {
        float envelope = 1.0f;
        if (chunk == 0 && i < 80)
          envelope = (float)i / 80.0f;
        if (chunk == chunks - 1 && i >= CHUNK_SAMPLES - 80)
          envelope = (float)(CHUNK_SAMPLES - 1 - i) / 80.0f;
        pcm[i] = (int16_t)(7000.0f * envelope * sinf(phase));
        phase += phase_step;
        if (phase >= 2.0f * M_PI)
          phase -= 2.0f * M_PI;
      }
      esp_err_t err = voice_io_spk_play_stream(
          (const uint8_t *)pcm, sizeof(int16_t) * CHUNK_SAMPLES);
      if (err != ESP_OK) {
        ESP_LOGE("audio_test", "Local melody output failed: %s",
                 esp_err_to_name(err));
        audio_is_playing = false;
        break;
      }
    }

    // 每个音符之间写入真实静音，而不是让 I2S 队列空转。
    memset(pcm, 0, sizeof(int16_t) * CHUNK_SAMPLES);
    voice_io_spk_play_stream(
        (const uint8_t *)pcm, sizeof(int16_t) * CHUNK_SAMPLES);
  }

  // 末尾加入 300ms 全零 PCM，防止最后一个音符突断。
  memset(pcm, 0, sizeof(int16_t) * CHUNK_SAMPLES);
  for (int i = 0; i < 15; i++) {
    voice_io_spk_play_stream(
        (const uint8_t *)pcm, sizeof(int16_t) * CHUNK_SAMPLES);
  }
  audio_is_playing = false;

  ESP_LOGI("audio_test", "Local melody stopped, cleaning up...");
  voice_io_spk_stop();
  free(pcm);
  audio_task_handle = NULL;
  vTaskDelete(NULL);
}

/**
 * @brief 外设任务主循环
 *
 * 负责初始化各种 I2C 传感器与 I/O 扩展器，并在死循环中周期性
 * 读取温湿度及同步雾化器状态。
 *
 * @param param 任务参数
 */
static void peripheral_aroma_process(int *tick_count) {
  *tick_count = (*tick_count + 1) % 10;

  // AHT20 湿度一票否决：湿度 > 80% 强制关闭所有香薰
  float hum = 0.0f;
  aht20_get_latest(NULL, &hum);
  bool was_blocked = s_humidity_blocked;
  s_humidity_blocked = (hum > 80.0f);

  for (int i = 0; i < 3; ++i) {
    bool physical_on = false;
    if (s_aroma_target[i] && !s_humidity_blocked) {
      int lv = s_aroma_level[i];
      if (lv >= 3) {
        physical_on = true; // 100% 开启
      } else if (lv == 2) {
        physical_on = (*tick_count < 5); // 50% 占空比 (1.5s开，1.5s停)
      } else if (lv == 1) {
        physical_on = (*tick_count < 2); // 20% 占空比 (0.6s开，2.4s停)
      }
    }
    he30_set_target(i, physical_on);
  }
  he30_sync();

  if (s_humidity_blocked && !was_blocked) {
    ESP_LOGW("peripherals", "Humidity %.1f%% > 80%% — mist blocked", hum);
  }
}

#if ENABLE_MPU6050_FEATURES
static void peripheral_privacy_check(int16_t ax, int16_t ay, int16_t az) {
  static bool s_privacy_active = false;
  if (az < -10000 && !s_privacy_active) {
    s_privacy_active = true;
    ESP_LOGW("peripherals", "Privacy mode triggered (device face down)!");
    voice_assistant_send_text("{\"event\":\"privacy_mode\"}");
  } else if (az > 10000 && s_privacy_active) {
    s_privacy_active = false;
    ESP_LOGI("peripherals", "Privacy mode exited (device face up).");
    voice_assistant_send_text("{\"event\":\"privacy_mode_exit\"}");
  }
}

static void peripheral_telemetry_report(int16_t ax, int16_t ay, int16_t az) {
  static int telemetry_count = 0;
  telemetry_count++;
  if (telemetry_count >= 15) {
    telemetry_count = 0;
    float temp = 0.0f, hum = 0.0f;
    aht20_get_latest(&temp, &hum);

    char telemetry_json[192];
    snprintf(telemetry_json, sizeof(telemetry_json),
             "{\"event\":\"telemetry\",\"temp\":%.1f,\"hum\":%.1f,\"ax\":%d,"
             "\"ay\":%d,\"az\":%d}",
             temp, hum, ax, ay, az);

    voice_assistant_send_text(telemetry_json);
  }
}
#endif

void app_peripherals(void *param) {
  (void)param;
  i2c_bus_mutex_init();

#if ENABLE_AHT20_FEATURES
  if (i2c_sensor_aht20_init() != ESP_OK) {
    ESP_LOGE("peripherals", "AHT20 init failed!");
  }
#else
  ESP_LOGI("peripherals", "AHT20 disabled for current testing");
#endif
  if (pcf8574_io_init() != ESP_OK) {
    ESP_LOGE("peripherals", "PCF8574 init failed!");
  }

  // 初始化 MPU6050
#if ENABLE_MPU6050_FEATURES
  if (mpu6050_init() != ESP_OK) {
    ESP_LOGE("peripherals", "MPU6050 init failed!");
  }
#else
  ESP_LOGI("peripherals", "MPU6050 features disabled for current testing");
#endif

  // 初始化 3 个雾化器句柄，配置相关引脚
  he30_init_all(EXT_IO_PIN_0, EXT_IO_PIN_1, EXT_IO_PIN_2);

  static int tick_count = 0;

  while (1) {
    // 1. 加湿强度占空比计算与底层引脚控制 (间歇性喷雾)
    peripheral_aroma_process(&tick_count);
    
    // 2. 轮询传感器并更新内部数据缓存
#if ENABLE_AHT20_FEATURES
    static int aht20_tick = 0;
    if (++aht20_tick >= 10) { // 300ms * 10 = 3s
      peripheral_aht20();
      aht20_tick = 0;
    }
#endif
#if ENABLE_MPU6050_FEATURES
    peripheral_mpu6050();

    // 3. 姿态传感器手势隐私模式判定与周期发送遥测数据
    int16_t ax = 0, ay = 0, az = 0;
    if (mpu6050_get_latest_accel(&ax, &ay, &az) == ESP_OK) {
      peripheral_privacy_check(ax, ay, az);
      peripheral_telemetry_report(ax, ay, az);
    }
#endif

    vTaskDelay(pdMS_TO_TICKS(300)); // 轮询周期 300ms
  }

  vTaskDelete(NULL);
}

static void uart_ctrl_task(void *arg) {
  static bool t_state[3] = {false, false, false};

  // 延时 3 秒，等 Wi-Fi 和屏幕初始化完毕
  vTaskDelay(pdMS_TO_TICKS(3000));

#if CONFIG_VA_AUTO_SPK_TEST
  ESP_LOGI("uart_ctrl", "Auto-starting audio test Beep (440Hz Sine)...");
  esp_err_t err = voice_io_spk_init(48000, 1, 16);
  if (err == ESP_OK) {
    voice_io_set_spk_volume(10);
    audio_is_playing = true;
    BaseType_t ret = xTaskCreatePinnedToCoreWithCaps(
        audio_test_task, "audio_test", 6144,
        (void *)(uintptr_t)CONFIG_VA_AUTO_SPK_TEST_DURATION_MS, 5,
        &audio_task_handle, tskNO_AFFINITY,
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (ret == pdPASS) {
      ESP_LOGI("uart_ctrl", "audio_test_task auto-created successfully.");
    } else {
      ESP_LOGE("uart_ctrl", "Failed to auto-create audio_test_task: %d", ret);
      audio_is_playing = false;
    }
  } else {
    ESP_LOGE("uart_ctrl", "Audio init failed on startup");
  }
#endif

  while (1) {
    int c = getchar();
    if (c != EOF) {
      switch (c) {
      case '1':
        t_state[0] = !t_state[0]; // Toggle
        he30_set_target(0, t_state[0]);
        ESP_LOGI("uart_ctrl", "Channel 1 %s", t_state[0] ? "ON" : "OFF");
        break;
      case '2':
        t_state[1] = !t_state[1]; // Toggle
        he30_set_target(1, t_state[1]);
        ESP_LOGI("uart_ctrl", "Channel 2 %s", t_state[1] ? "ON" : "OFF");
        break;
      case '3':
        t_state[2] = !t_state[2]; // Toggle
        he30_set_target(2, t_state[2]);
        ESP_LOGI("uart_ctrl", "Channel 3 %s", t_state[2] ? "ON" : "OFF");
        break;
      case '4': {
        // Toggle ALL
        bool new_state = !(t_state[0] && t_state[1] && t_state[2]);
        t_state[0] = t_state[1] = t_state[2] = new_state;
        he30_set_target(0, new_state);
        he30_set_target(1, new_state);
        he30_set_target(2, new_state);
        ESP_LOGI("uart_ctrl", "All Channels %s", new_state ? "ON" : "OFF");
        break;
      }
      case '5': {
        if (audio_task_handle != NULL) {
          ESP_LOGI("uart_ctrl", "Stopping audio test...");
          audio_is_playing = false;
          // 等待任务自行退出（最多 500ms），否则强制删除
          for (int i = 0; i < 10 && audio_task_handle != NULL; i++) {
            vTaskDelay(pdMS_TO_TICKS(50));
          }
          if (audio_task_handle != NULL) {
            ESP_LOGW("uart_ctrl", "audio_test_task stuck after 500ms, force deleting");
            vTaskDelete(audio_task_handle);
            audio_task_handle = NULL;
            voice_io_spk_stop();
            ESP_LOGI("uart_ctrl", "Audio test force-stopped.");
          } else {
            ESP_LOGI("uart_ctrl", "Audio test stopped cleanly.");
          }
        } else {
          ESP_LOGI("uart_ctrl", "Starting audio test (440Hz Beep)...");
          if (!s_audio_inited) {
            esp_err_t err = voice_io_spk_init(48000, 1, 16);
            if (err == ESP_OK) {
              s_audio_inited = true;
            } else {
              ESP_LOGE("uart_ctrl", "Audio init failed, cannot play");
              break;
            }
          }
          // 强制将测试音量设定为 10%
          voice_io_set_spk_volume(10);
          audio_is_playing = true;
          BaseType_t ret = xTaskCreatePinnedToCoreWithCaps(
              audio_test_task, "audio_test", 6144, NULL, 5, &audio_task_handle,
              tskNO_AFFINITY, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
          if (ret == pdPASS) {
            ESP_LOGI("uart_ctrl", "audio_test_task created successfully.");
          } else {
            ESP_LOGE("uart_ctrl", "Failed to create audio_test_task: %d", ret);
          }
        }
        break;
      }
      case '6': {
        if (stream_player_is_playing()) {
          ESP_LOGI("uart_ctrl", "Stopping real music PCM stream...");
          stream_player_stop();
        } else {
          char url[160];
          snprintf(url, sizeof(url),
                   "%s/music/happy_pop_1_16k_mono.pcm",
                   CONFIG_SERVER_URL);
          ESP_LOGI("uart_ctrl", "Starting real music PCM stream (%s)...",
                   url);
          if (!s_audio_inited) {
            esp_err_t err = voice_io_spk_init(48000, 1, 16);
            if (err == ESP_OK) {
              s_audio_inited = true;
            } else {
              ESP_LOGE("uart_ctrl", "Audio init failed, cannot play");
              break;
            }
          }
          voice_io_set_spk_volume(60);
          stream_player_play_pcm_url(url);
        }
        break;
      }
      case '7': {
        ESP_LOGI("uart_ctrl", "Forcing voice assistant wakeup (simulated by key '7')...");
        va_force_wake();
        break;
      }
      case '8':
        s_mic_diagnostics_enabled = !s_mic_diagnostics_enabled;
        ESP_LOGI("uart_ctrl", "Microphone diagnostics %s",
                 s_mic_diagnostics_enabled ? "ON" : "OFF");
        break;
      case ' ':
        t_state[0] = t_state[1] = t_state[2] = false;
        he30_set_target(0, false);
        he30_set_target(1, false);
        he30_set_target(2, false);
        ESP_LOGI("uart_ctrl", "All Channels OFF");
        break;
      case '+':
      case '=': {
        uint8_t vol = voice_io_get_spk_volume();
        if (vol <= 90)
          vol += 10;
        else
          vol = 100;
        voice_io_set_spk_volume(vol);
        break;
      }
      case '-':
      case '_': {
        uint8_t vol = voice_io_get_spk_volume();
        if (vol >= 10)
          vol -= 10;
        else
          vol = 0;
        voice_io_set_spk_volume(vol);
        break;
      }
      default:
        break;
      }
    }

    if (s_mic_diagnostics_enabled) {
      static TickType_t last_mic_log = 0;
      TickType_t now = xTaskGetTickCount();
      if (now - last_mic_log >= pdMS_TO_TICKS(500)) {
        voice_io_mic_metrics_t metrics;
        esp_err_t ret = voice_io_mic_get_metrics(&metrics);
        if (ret == ESP_OK) {
          ESP_LOGI(
              "mic_test",
              "L=%lu R=%lu raw_peak=%lu processed=%lu peak=%lu frames=%lu",
              (unsigned long)metrics.left_average,
              (unsigned long)metrics.right_average,
              (unsigned long)metrics.input_peak,
              (unsigned long)metrics.output_average,
              (unsigned long)metrics.output_peak,
              (unsigned long)metrics.frame_count);
        } else {
          ESP_LOGW("mic_test", "No microphone frames: %s",
                   esp_err_to_name(ret));
        }
        last_mic_log = now;
      }
    }
    vTaskDelay(pdMS_TO_TICKS(50));
  }
}

/**
 * @brief 初始化外设任务
 *
 * 静态创建并拉起外设轮询任务。
 */
void app_peripherals_init(void) {
  static StaticTask_t task_tcb;
  static StackType_t task_stack[1024 * 4];

  TaskHandle_t task_peripherals =
      xTaskCreateStaticPinnedToCore(app_peripherals, "peripherals_task", 1024 * 4, NULL, 5,
                        task_stack, /* 数组 */
                        &task_tcb,  /*  TCB */
                        0           /* Core 0 */
      );

  if (task_peripherals == NULL) {
    printf("Failed to create peripherals task\n");
  }

  // 创建串口控制任务 (固定在 Core 0，不抢占语音)
  xTaskCreatePinnedToCore(uart_ctrl_task, "uart_ctrl_task", 4096, NULL, 1, NULL, 0);
}

/* ── 雾化器控制接口（线程安全）────────────────────────── */

/**
 * @brief 开启全部香薰/雾化通道
 */
void peripherals_mist_on(void) {
  for (int i = 0; i < 3; i++) {
    peripherals_set_aroma(i, true, 3);
  }
  ESP_LOGI("peripherals", "Voice → Mist ON (all 3 channels)");
}

/**
 * @brief 关闭全部香薰/雾化通道
 */
void peripherals_mist_off(void) {
  for (int i = 0; i < 3; i++) {
    peripherals_set_aroma(i, false, 3);
  }
  ESP_LOGI("peripherals", "Voice → Mist OFF (all 3 channels)");
}

/**
 * @brief 查询是否有任意一路雾化器处于开启状态
 *
 * @return true 至少有一路开启
 * @return false 全部关闭
 */
bool peripherals_mist_is_on(void) {
  for (int i = 0; i < 3; i++) {
    if (s_aroma_target[i])
      return true;
  }
  return false;
}
