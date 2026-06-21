# Repository Tooling

## ESP-IDF

- ESP-IDF version: `v6.0.1`
- ESP-IDF root: `C:\esp\v6.0.1\esp-idf`
- Tools root: `C:\Espressif`
- Base Python: `C:\Users\sxxy4\AppData\Local\Programs\Python\Python310`
- IDF Python environment: `C:\Espressif\tools\python\v6.0.1\venv`

Always run ESP-IDF commands through the repository wrapper:

```powershell
.\scripts\idf.ps1 build
.\scripts\idf.ps1 -p COM11 flash monitor
```

Do not infer that Python is missing when a sandboxed command cannot enumerate
`AppData\Local\Programs`. An `Access denied` result requires running the wrapper
with the appropriate filesystem permission; it is not evidence that Python was
uninstalled. Do not reinstall Python unless the wrapper's explicit dependency
check fails outside the sandbox.

## Stable Build And Flash Workflow

- Repository: `C:\Users\sxxy4\Documents\esp_projects\digital-album-project`
- Main development branch: `main`
- Target: ESP32-S3
- Serial port: `COM11`
- Builds are forced to single-threaded mode by `scripts\idf.ps1`. Parallel full
  builds have repeatedly triggered a GCC internal compiler error in
  `esp_lcd_panel_rgb.c`.

```powershell
cd C:\Users\sxxy4\Documents\esp_projects\digital-album-project
.\scripts\idf.ps1 build
.\scripts\idf.ps1 -p COM11 flash monitor
```

Do not run `fullclean` unless stale build state has been demonstrated. Do not use
PlatformIO for this repository.

## Backend Services

- Flask/Web UI: port `8765`
- Voice WebSocket service: port `8888`
- Start or restart both services with `.\restart_backend.bat`. The script must
  close the previous dedicated backend PowerShell windows and their child
  processes before opening fresh Web Backend and Voice Backend windows.
- Backend virtual environment: `backend\.venv`
- Expected Whisper runtime: `large-v3-turbo`, CUDA `float16`, beam size `5`.
- Installed CUDA Python runtimes:
  - `nvidia-cublas-cu12==12.4.5.8`
  - `nvidia-cudnn-cu12==9.10.2.21`

## Secrets

- Store API keys only in `backend\.env.local`.
- `backend\.env.local` must remain ignored by Git.
- Never commit or print API keys in logs, documentation, or source files.

## Hardware Constraints

- INMP441:
  - VDD -> 3V3
  - GND -> GND
  - SCK -> GPIO11
  - WS -> GPIO12
  - SD -> GPIO13
  - L/R -> GND
- MAX98357A amplifier SD mode: GPIO19.
- PCF8574 address: `0x20`.
- This board has no MPU6050. Do not enable or add MPU6050-dependent behavior.
- The automatic startup 440 Hz speaker test must remain disabled.

## Audio And Voice Requirements

- Default speech and music volume is `60%`. Do not lower volume as a workaround
  for echo, wake-word, or connection problems.
- Music playback is non-looping unless the user explicitly requests looping.
- Music must remain interruptible with the wake word `你好小智`.
- Voice pipeline:
  `INMP441 -> WakeNet -> VAD -> WebSocket -> faster-whisper CUDA -> DeepSeek V4 Flash -> Aliyun CosyVoice -> MAX98357A`.
- WakeNet microphone gain is `24x`; active recording gain is `12x`.
- Recording must end after about `1.2s` of post-speech silence.
- Endpoint detection must use a stable speech-energy reference that is not
  pulled down by trailing ambient noise.
- Send assistant reply text to the device before generating and streaming the
  corresponding TTS sentence, so UI typing begins before speech playback.
- Waiting for a continuous-dialogue turn must not block device command polling.
  Trigger the next turn only when VAD confirms speech near the previous
  utterance's measured RMS, rather than using ambient noise alone.
- AEC receives linear microphone samples before output gain.
- Queue the volume-scaled playback reference before the I2S DMA write.
- Keep the voice WebSocket persistent; task stack and transport buffer are both
  `8192` bytes.

## Product Behavior Requirements

- Interpret the full user sentence semantically. Photo selection and
  orientation changes may appear in any word order and should execute together.
- Handle plausible ASR homophones semantically, such as `树瓶鸭子` meaning
  `竖屏鸭子`, without relying only on fixed keyword replacement.
- Portrait display preserves the source aspect ratio and uses a blurred
  background for unused space.
- Music search must inspect filename and embedded title/artist metadata.
  `backend\music\test.mp3` is `晴天` by `周杰伦` and has legacy GBK metadata.
- An unavailable explicitly named song must not fall back to a random song.
- Random selection is allowed only for an explicit generic or random-play
  request.
