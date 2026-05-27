# 后端接口说明

本文档说明当前 UI/API 层应如何调用后端事件接口，并记录当前硬件接线与引脚分配。

## 调用边界

UI 和 API 代码应该通过下面两个接口发送应用层事件：

```c
app_event_send(app_event_id_t id, int param);
app_event_send_text(app_event_id_t id, const char *text_arg);
```

UI/API 层不要直接调用底层驱动接口：

- `i2s_output_*`
- `pcf8574_*`
- 原始 I2S driver API
- 原始 PCF8574 driver API

事件层会统一把事件分发到音频播放服务和香薰控制服务。

## 硬件接线与引脚分配

本项目后端当前涉及三类硬件连接：I2S 音频输出、I2C 扩展 IO、HE30 三路香薰/雾化控制。UI/API 层只需要调用事件接口，但硬件调试和接线时需要参考本节。

### 1. MAX98357A I2S 功放接线

| MAX98357A 引脚 | ESP32-S3 引脚 | 说明 |
|---|---|---|
| VIN | 5V | MAX98357A 供电 |
| GND | GND | 与 ESP32 共地 |
| BCLK | GPIO4 | I2S Bit Clock |
| LRC / WS | GPIO5 | I2S Word Select / LRCLK |
| DIN | GPIO6 | I2S 数据输入，接 ESP32 I2S DOUT |
| SPK+ | 扬声器 + | 接喇叭正极 |
| SPK- | 扬声器 - | 接喇叭负极 |

注意：GPIO4、GPIO5、GPIO6 是当前代码中的默认测试引脚。后续如果和板载接口或其他外设冲突，需要在 `i2s_output` 模块中统一修改 GPIO 宏定义。

### 2. PCF8574 I2C 扩展 IO 接线

| PCF8574 引脚 | ESP32-S3 引脚 | 说明 |
|---|---|---|
| VCC | 3.3V | 推荐使用 3.3V，保证 I2C 电平兼容 ESP32 |
| GND | GND | 与 ESP32 共地 |
| SDA | GPIO8 | I2C 数据线 |
| SCL | GPIO9 | I2C 时钟线 |
| A0 / A1 / A2 | 按模块地址配置 | 当前软件默认地址为 `0x27`，实际地址需与模块焊盘/拨码一致 |

注意：PCF8574 只用于低速 IO 控制，不直接驱动雾化器、功放或其他大电流负载。

### 3. HE30 三路香薰 / 雾化控制接线

当前三路香薰控制通过 PCF8574 扩展 IO 输出控制信号，再由 HE30 控制对应雾化通道。PCF8574 不直接驱动雾化负载，只输出控制信号。

| 香薰通道 | PCF8574 引脚 | HE30 控制端 | 软件通道 |
|---|---|---|---|
| 香薰 1 | P0 | HE30 CH1 控制输入 | `AROMA_CH_1` |
| 香薰 2 | P1 | HE30 CH2 控制输入 | `AROMA_CH_2` |
| 香薰 3 | P2 | HE30 CH3 控制输入 | `AROMA_CH_3` |

UI/API 层不需要关心 HE30 控制输入是高电平触发还是低电平触发。上层永远只表达“打开”或“关闭”：

```c
aroma_set(AROMA_CH_1, true);   // 打开香薰 1
aroma_set(AROMA_CH_1, false);  // 关闭香薰 1
```

实际输出高电平还是低电平，由 `aroma_ctrl` 内部的 `AROMA_ACTIVE_LEVEL` 统一处理。当前默认 `AROMA_ACTIVE_LEVEL = true`，即高电平触发。如果实测 HE30 是低电平触发，只需要修改该宏，不需要改 UI/API 层代码。

### 4. 供电与共地要求

所有相关模块必须共地：

```text
ESP32 GND
PCF8574 GND
MAX98357A GND
HE30 GND
雾化/香薰模块电源 GND
```

HE30 和雾化模块的供电应按 HE30 模块规格连接。ESP32 和 PCF8574 只负责提供控制信号，不直接给雾化负载供电。

### 5. 当前代码默认引脚汇总

| 功能 | 默认引脚 / 地址 | 代码模块 |
|---|---|---|
| I2S BCLK | GPIO4 | `i2s_output` |
| I2S LRCLK / WS | GPIO5 | `i2s_output` |
| I2S DOUT | GPIO6 | `i2s_output` |
| I2C SDA | GPIO8 | `pcf8574_io` |
| I2C SCL | GPIO9 | `pcf8574_io` |
| PCF8574 地址 | `0x27` | `pcf8574_io` |
| 香薰 CH1 | PCF8574 P0 -> HE30 CH1 | `aroma_ctrl` |
| 香薰 CH2 | PCF8574 P1 -> HE30 CH2 | `aroma_ctrl` |
| 香薰 CH3 | PCF8574 P2 -> HE30 CH3 | `aroma_ctrl` |

## 音频提示音事件

播放内置提示音时使用下面这些事件：

```c
app_event_send(APP_EVENT_PLAY_STARTUP, 0);
app_event_send(APP_EVENT_PLAY_CLICK, 0);
app_event_send(APP_EVENT_PLAY_AROMA_ON, 0);
app_event_send(APP_EVENT_PLAY_AROMA_OFF, 0);
app_event_send(APP_EVENT_PLAY_ERROR, 0);
app_event_send(APP_EVENT_STOP_AUDIO, 0);
```

## TTS WAV 文件播放

播放 TTS 生成的 WAV 文件时，使用 `app_event_send_text()` 发送文件路径：

```c
app_event_send_text(APP_EVENT_PLAY_TTS_FILE, "/spiffs/tts_reply.wav");
```

路径格式说明：

- 传入一个已经挂载的文件系统路径字符串。
- 当前示例路径是 `/spiffs/tts_reply.wav`。
- 后续如果使用 SD 卡，也可以继续用同一个事件，例如 `/sdcard/tts_reply.wav`，前提是对应文件系统已经完成挂载。

当前音频文件支持范围：

- PCM WAV
- 16-bit
- 单声道
- 16000 Hz 或 24000 Hz
- little-endian

## 香薰三路事件

三路香薰控制使用下面这些事件：

```c
app_event_send(APP_EVENT_AROMA_CH1_ON, 0);
app_event_send(APP_EVENT_AROMA_CH1_OFF, 0);

app_event_send(APP_EVENT_AROMA_CH2_ON, 0);
app_event_send(APP_EVENT_AROMA_CH2_OFF, 0);

app_event_send(APP_EVENT_AROMA_CH3_ON, 0);
app_event_send(APP_EVENT_AROMA_CH3_OFF, 0);

app_event_send(APP_EVENT_AROMA_ALL_OFF, 0);
```

通道映射：

- CH1 对应 PCF8574 P0，再接 HE30 CH1 控制输入。
- CH2 对应 PCF8574 P1，再接 HE30 CH2 控制输入。
- CH3 对应 PCF8574 P2，再接 HE30 CH3 控制输入。

UI/API 层不需要关心 HE30 控制输入是高电平触发还是低电平触发，这部分由 `aroma_ctrl` 内部统一处理。

## 场景事件

当 UI/API 层需要触发预设场景时，使用下面这些事件：

```c
app_event_send(APP_EVENT_SCENE_RELAX, 0);
app_event_send(APP_EVENT_SCENE_SLEEP, 0);
app_event_send(APP_EVENT_SCENE_FOCUS, 0);
```

当前场景行为：

- `APP_EVENT_SCENE_RELAX`：播放 aroma-on 提示音，打开 CH1，关闭 CH2/CH3。
- `APP_EVENT_SCENE_SLEEP`：播放 aroma-on 提示音，打开 CH2，关闭 CH1/CH3。
- `APP_EVENT_SCENE_FOCUS`：播放 click 提示音，打开 CH3，关闭 CH1/CH2。

## 当前限制

- 当前事件层不负责文件系统挂载。
- 当前还没有实现网络 TTS 请求。
- TTS 播放要求指定路径下已经存在 WAV 文件。
- 目前还没有完成硬件实测。
