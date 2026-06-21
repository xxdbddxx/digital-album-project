# Emotion Music And Music Wake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make vocal music interruptible and make mood-based local music selection reliable.

**Architecture:** Preserve semantic decisions produced by DeepSeek, then resolve
music against explicit metadata and a local tag manifest. Use ESP-SR
full-duplex AEC settings during playback and deliver final emotion to the active
LVGL dialogue panel through a dedicated event.

**Tech Stack:** Python 3.10, `unittest`, ESP-IDF v6.0.1, ESP-SR AEC, LVGL, cJSON.

## Global Constraints

- Keep music and speech volume at 60%.
- Do not add keyword replacement to ASR text.
- Do not lower playback volume to improve wake-word recognition.
- Build only through `.\scripts\idf.ps1 build`.
- Do not flash during this task.

---

### Task 1: Mood-Aware Music Resolution

**Files:**
- Create: `backend/music_tags.json`
- Create: `backend/tests/test_music_routing.py`
- Modify: `backend/services/voice_server.py`

**Interfaces:**
- Consumes: LLM `action.audio.url` search macros and ASR user text.
- Produces: `_select_music_file(query, user_text="") -> str`.

- [ ] Write failing tests for `悲伤音乐`, manifest tags, and preserving
  `<search: sad>`.
- [ ] Run `python -m unittest backend.tests.test_music_routing -v` and confirm
  the new tests fail for the expected routing reasons.
- [ ] Load `music_tags.json`, add mood aliases, and preserve valid model macros.
- [ ] Run the unit tests and confirm they pass.

### Task 2: Real Emotion UI Propagation

**Files:**
- Modify: `backend/services/voice_server.py`
- Modify: `main/voice_assistant/src/voice_assistant.c`
- Modify: `main/lv_ui/src/lv_voice_assistant.h`
- Modify: `main/lv_ui/src/lv_voice_assistant.c`

**Interfaces:**
- Consumes: final `dialogue.emotion` from parsed LLM JSON.
- Produces: `{"event":"assistant_emotion","emotion":"..."}` and
  `lv_va_set_emotion(const char *emotion)`.

- [ ] Send the parsed final emotion without repeating reply text.
- [ ] Handle the event on ESP32 and update only the current panel accent.
- [ ] Verify backend syntax and firmware compilation.

### Task 3: Vocal-Music Wake Interruption

**Files:**
- Modify: `main/voice_assistant/src/voice_io.c`

**Interfaces:**
- Consumes: microphone and volume-scaled playback reference PCM.
- Produces: AEC output suitable for WakeNet during simultaneous vocal music.

- [ ] Change AEC mode from `AEC_MODE_SR_LOW_COST` to
  `AEC_MODE_FD_LOW_COST`.
- [ ] Change NLP from `AEC_NLP_LEVEL_AGGR` to `AEC_NLP_LEVEL_NORMAL`.
- [ ] Log the selected AEC mode and NLP level at initialization.
- [ ] Run `.\scripts\idf.ps1 build` and confirm exit code 0.

### Task 4: Final Verification

**Files:**
- No production files.

- [ ] Run all new backend unit tests.
- [ ] Run Python bytecode compilation for `voice_server.py`.
- [ ] Run `git diff --check`.
- [ ] Run the ESP-IDF wrapper build.
- [ ] Report that hardware wake-over-vocal-music still requires user testing
  after flashing.
