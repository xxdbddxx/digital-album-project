# Multimodal User Emotion Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cloud acoustic emotion analysis, semantic emotion output, deterministic fusion, session smoothing, expressive TTS, and safely gated ambient actions without destabilizing the existing voice pipeline.

**Architecture:** Add focused modules under `backend/services/emotion/`. Start DashScope acoustic analysis in parallel with the existing Whisper task, require DeepSeek to emit semantic emotion before `dialogue`, fuse both locally, and pass only authorized actions into the existing CJSON resolver. All enhancement paths are feature-flagged and fail back to current behavior.

**Tech Stack:** Python 3.10, standard-library `dataclasses`/`enum`/`wave`/`base64`, existing `requests`, DashScope OpenAI-compatible Qwen3-Omni Captioner API, `unittest`, ESP-IDF v6.0.1 for final regression build.

## Global Constraints

- Treat this as a sidecar optimization of a working module; do not refactor unrelated voice code.
- Preserve WakeNet, VAD, endpoint detection, AEC, persistent WebSocket, 8192-byte buffers, Whisper CUDA `large-v3-turbo` with beam size 5, DeepSeek streaming, and CosyVoice streaming.
- Preserve the 24x WakeNet gain, 12x recording gain, approximately 1.2 seconds of post-speech silence, and stable endpoint energy reference.
- Send reply text before generating each corresponding TTS sentence.
- Keep speech and music volume at 60%; emotional music is always non-looping.
- Explicit commands always override emotion policy. Named unavailable songs and photos never fall back to random choices.
- Store credentials only in `backend/.env.local`; never print or commit keys.
- Use `qwen3-omni-30b-a3b-captioner` through `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` as the first acoustic provider. Official API reference: <https://help.aliyun.com/zh/model-studio/qwen3-omni-captioner>.
- Every task starts with a failing test and ends with a focused commit.
- Run firmware commands only through `.\scripts\idf.ps1`; do not use `fullclean` or PlatformIO.

## File Map

- Create `backend/services/emotion/__init__.py`: public exports only.
- Create `backend/services/emotion/models.py`: configuration, enums, immutable signal/result types, validation.
- Create `backend/services/emotion/fusion.py`: deterministic weighting and conflict detection.
- Create `backend/services/emotion/session.py`: per-client 3–5 turn smoothing and intervention state.
- Create `backend/services/emotion/acoustic.py`: PCM→WAV and DashScope provider adapter.
- Create `backend/services/emotion/policy.py`: response-style mapping and hardware authorization.
- Create `backend/services/emotion/streaming.py`: parse semantic emotion before streamed `tts_text`.
- Create `backend/services/emotion/metrics.py`: structured per-turn diagnostic record construction.
- Create `backend/tests/test_emotion_models.py`.
- Create `backend/tests/test_emotion_fusion.py`.
- Create `backend/tests/test_emotion_acoustic.py`.
- Create `backend/tests/test_emotion_policy.py`.
- Create `backend/tests/test_emotion_streaming.py`.
- Create `backend/tests/test_emotion_pipeline.py`.
- Create `backend/tests/test_emotion_metrics.py`.
- Create `backend/tools/replay_emotion.py`.
- Create `backend/tests/fixtures/emotion/README.md`.
- Modify `.gitignore`: ignore local WAV fixtures and reports.
- Modify `backend/services/voice_server.py`: narrow orchestration hooks only.
- Modify `backend/services/voice_system_prompt.md`: semantic emotion schema and action-source contract.
- Modify `backend/.env.example`: feature flags and provider settings.

---

### Task 1: Typed Emotion Contracts And Configuration

**Files:**
- Create: `backend/services/emotion/__init__.py`
- Create: `backend/services/emotion/models.py`
- Create: `backend/tests/test_emotion_models.py`
- Modify: `backend/.env.example`

**Interfaces:**
- Produces: `EmotionLabel`, `EmotionSource`, `ResponseStyle`, `EmotionSignal`, `FusedEmotion`, `EmotionConfig`.
- Consumers: all later emotion modules and `voice_server.py`.

- [ ] **Step 1: Write failing contract tests**

```python
# backend/tests/test_emotion_models.py
import unittest

from backend.services.emotion.models import EmotionConfig, EmotionLabel, EmotionSignal, EmotionSource


class EmotionModelTests(unittest.TestCase):
    def test_invalid_payload_becomes_low_confidence_neutral(self):
        signal = EmotionSignal.from_mapping(
            {"label": "panic", "confidence": 4, "valence": -8},
            source=EmotionSource.ACOUSTIC,
        )
        self.assertEqual(signal.label, EmotionLabel.NEUTRAL)
        self.assertEqual(signal.confidence, 0.0)
        self.assertEqual(signal.valence, -1.0)

    def test_config_reads_competition_defaults(self):
        config = EmotionConfig.from_mapping({})
        self.assertTrue(config.enabled)
        self.assertTrue(config.demo_mode)
        self.assertEqual(config.acoustic_timeout_ms, 400)
        self.assertEqual(config.session_window_turns, 5)
        self.assertEqual(config.action_threshold, 0.80)
```

- [ ] **Step 2: Run the test and verify the missing-module failure**

```powershell
backend\.venv\Scripts\python.exe -m unittest backend.tests.test_emotion_models -v
```

Expected: `ModuleNotFoundError: No module named 'backend.services.emotion'`.

- [ ] **Step 3: Implement immutable validated contracts**

```python
# backend/services/emotion/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Sequence


class EmotionLabel(str, Enum):
    JOY = "joy"
    SADNESS = "sadness"
    ANXIETY = "anxiety"
    ANGER = "anger"
    FATIGUE = "fatigue"
    LONELINESS = "loneliness"
    CALM = "calm"
    NEUTRAL = "neutral"


class EmotionSource(str, Enum):
    ACOUSTIC = "acoustic"
    SEMANTIC = "semantic"
    FUSED = "fused"


class ResponseStyle(str, Enum):
    CHEERFUL = "cheerful"
    EMPATHIC = "empathic"
    SOOTHING = "soothing"
    CALM = "calm"
    GENTLE = "gentle"
    WARM = "warm"
    NEUTRAL = "neutral"


def _clamp(value: object, minimum: float, maximum: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


@dataclass(frozen=True)
class EmotionSignal:
    label: EmotionLabel
    valence: float
    arousal: float
    intensity: float
    confidence: float
    evidence: tuple[str, ...]
    source: EmotionSource

    @classmethod
    def neutral(cls, source: EmotionSource) -> "EmotionSignal":
        return cls(EmotionLabel.NEUTRAL, 0.0, 0.0, 0.0, 0.0, (), source)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object], *, source: EmotionSource) -> "EmotionSignal":
        try:
            label = EmotionLabel(str(payload.get("label", "neutral")).lower())
            confidence = _clamp(payload.get("confidence"), 0.0, 1.0, 0.0)
        except ValueError:
            label = EmotionLabel.NEUTRAL
            confidence = 0.0
        raw_evidence = payload.get("evidence", ())
        evidence: Sequence[object] = raw_evidence if isinstance(raw_evidence, (list, tuple)) else ()
        return cls(
            label=label,
            valence=_clamp(payload.get("valence"), -1.0, 1.0, 0.0),
            arousal=_clamp(payload.get("arousal"), 0.0, 1.0, 0.0),
            intensity=_clamp(payload.get("intensity"), 0.0, 1.0, 0.0),
            confidence=confidence,
            evidence=tuple(str(item)[:80] for item in evidence[:5]),
            source=source,
        )


@dataclass(frozen=True)
class FusedEmotion:
    label: EmotionLabel
    valence: float
    arousal: float
    intensity: float
    confidence: float
    mixed: bool
    modalities: tuple[EmotionSource, ...]
    evidence: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class EmotionConfig:
    enabled: bool = True
    acoustic_provider: str = "dashscope"
    acoustic_model: str = "qwen3-omni-30b-a3b-captioner"
    acoustic_timeout_ms: int = 400
    demo_mode: bool = True
    session_window_turns: int = 5
    ui_threshold: float = 0.45
    response_threshold: float = 0.65
    action_threshold: float = 0.80
    intervention_cooldown_sec: int = 300
    tts_style_enabled: bool = True

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "EmotionConfig":
        def flag(name: str, default: bool) -> bool:
            value = str(values.get(name, str(default))).strip().lower()
            return value in {"1", "true", "yes", "on"}
        return cls(
            enabled=flag("EMOTION_ENABLED", True),
            acoustic_provider=str(values.get("EMOTION_ACOUSTIC_PROVIDER", "dashscope")),
            acoustic_model=str(values.get("EMOTION_ACOUSTIC_MODEL", "qwen3-omni-30b-a3b-captioner")),
            acoustic_timeout_ms=int(values.get("EMOTION_ACOUSTIC_TIMEOUT_MS", 400)),
            demo_mode=flag("EMOTION_DEMO_MODE", True),
            session_window_turns=max(3, min(5, int(values.get("EMOTION_SESSION_WINDOW_TURNS", 5)))),
            ui_threshold=float(values.get("EMOTION_UI_THRESHOLD", 0.45)),
            response_threshold=float(values.get("EMOTION_RESPONSE_THRESHOLD", 0.65)),
            action_threshold=float(values.get("EMOTION_ACTION_THRESHOLD", 0.80)),
            intervention_cooldown_sec=int(values.get("EMOTION_INTERVENTION_COOLDOWN_SEC", 300)),
            tts_style_enabled=flag("EMOTION_TTS_STYLE_ENABLED", True),
        )
```

```python
# backend/services/emotion/__init__.py
from .models import EmotionConfig, EmotionLabel, EmotionSignal, EmotionSource, FusedEmotion, ResponseStyle

__all__ = ["EmotionConfig", "EmotionLabel", "EmotionSignal", "EmotionSource", "FusedEmotion", "ResponseStyle"]
```

Append this exact block to `backend/.env.example`:

```dotenv
EMOTION_ENABLED=true
EMOTION_ACOUSTIC_PROVIDER=dashscope
EMOTION_ACOUSTIC_MODEL=qwen3-omni-30b-a3b-captioner
EMOTION_ACOUSTIC_TIMEOUT_MS=400
EMOTION_DEMO_MODE=true
EMOTION_SESSION_WINDOW_TURNS=5
EMOTION_UI_THRESHOLD=0.45
EMOTION_RESPONSE_THRESHOLD=0.65
EMOTION_ACTION_THRESHOLD=0.80
EMOTION_INTERVENTION_COOLDOWN_SEC=300
EMOTION_TTS_STYLE_ENABLED=true
```

- [ ] **Step 4: Run the contract tests**

Run Step 2. Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/services/emotion backend/tests/test_emotion_models.py backend/.env.example
git commit -m "feat: add emotion contracts and configuration"
```

---

### Task 2: Deterministic Fusion And Session Smoothing

**Files:**
- Create: `backend/services/emotion/fusion.py`
- Create: `backend/services/emotion/session.py`
- Create: `backend/tests/test_emotion_fusion.py`

**Interfaces:**
- Produces: `fuse_emotions(acoustic, semantic) -> FusedEmotion`; `EmotionSessionStore.update(client_id, fused) -> FusedEmotion`; `can_intervene(client_id, category, now) -> bool`; `mark_intervention(client_id, category, now) -> None`; `clear(client_id) -> None`.

- [ ] **Step 1: Write failing fusion/session tests**

```python
import unittest
from backend.services.emotion.fusion import fuse_emotions
from backend.services.emotion.models import EmotionLabel, EmotionSignal, EmotionSource
from backend.services.emotion.session import EmotionSessionStore


def signal(label, confidence, source, valence=-0.5, arousal=0.6):
    return EmotionSignal(label, valence, arousal, confidence, confidence, (), source)


class EmotionFusionTests(unittest.TestCase):
    def test_matching_modalities_raise_confidence(self):
        fused = fuse_emotions(
            signal(EmotionLabel.ANXIETY, 0.8, EmotionSource.ACOUSTIC),
            signal(EmotionLabel.ANXIETY, 0.7, EmotionSource.SEMANTIC),
        )
        self.assertEqual(fused.label, EmotionLabel.ANXIETY)
        self.assertGreaterEqual(fused.confidence, 0.8)
        self.assertFalse(fused.mixed)

    def test_positive_negative_conflict_is_mixed(self):
        fused = fuse_emotions(
            signal(EmotionLabel.ANXIETY, 0.9, EmotionSource.ACOUSTIC),
            signal(EmotionLabel.JOY, 0.9, EmotionSource.SEMANTIC, 0.8, 0.8),
        )
        self.assertTrue(fused.mixed)
        self.assertLess(fused.confidence, 0.8)

    def test_session_clear_removes_history(self):
        store = EmotionSessionStore(window_turns=3)
        store.update("client", fuse_emotions(None, signal(EmotionLabel.SADNESS, 0.8, EmotionSource.SEMANTIC)))
        store.clear("client")
        self.assertIsNone(store.current("client"))

    def test_intervention_state_is_scoped_to_session(self):
        store = EmotionSessionStore(window_turns=3, cooldown_sec=300)
        self.assertTrue(store.can_intervene("client", "audio:sad", now=1000.0))
        store.mark_intervention("client", "audio:sad", now=1000.0)
        self.assertFalse(store.can_intervene("client", "audio:sad", now=1100.0))
        store.clear("client")
        self.assertTrue(store.can_intervene("client", "audio:sad", now=1100.0))
```

- [ ] **Step 2: Run and verify missing-module failures**

```powershell
backend\.venv\Scripts\python.exe -m unittest backend.tests.test_emotion_fusion -v
```

- [ ] **Step 3: Implement fusion and bounded smoothing**

Use the exact weight table from the design: sadness/loneliness acoustic 0.30 semantic 0.70; anxiety/anger/fatigue acoustic 0.55 semantic 0.45; joy/calm/neutral 0.50 each. Boost matching non-neutral confidence by 0.10 and cap positive/negative conflicts at 0.64. Preserve a single expert without inventing agreement. `EmotionSessionStore(window_turns, cooldown_sec)` uses `deque(maxlen=3..5)`, alpha 0.65, alpha 0.85 for high-confidence reversal, a per-client `{category: last_run_monotonic}` map, and the exact methods named in Interfaces. `clear(client_id)` removes both emotion history and intervention timestamps.

- [ ] **Step 4: Run tests and commit**

Expected: all fusion tests pass.

```powershell
git add backend/services/emotion/fusion.py backend/services/emotion/session.py backend/tests/test_emotion_fusion.py
git commit -m "feat: add deterministic emotion fusion"
```

---

### Task 3: DashScope Acoustic Expert Adapter

**Files:**
- Create: `backend/services/emotion/acoustic.py`
- Create: `backend/tests/test_emotion_acoustic.py`

**Interfaces:**
- Produces: `pcm_to_wav_bytes(pcm_data) -> bytes` and `DashScopeAcousticEmotionClient.analyze(pcm_data) -> EmotionSignal`.

- [ ] **Step 1: Write failing WAV/payload/response tests**

Mock `requests.post`; assert WAV starts with `RIFF...WAVE`, payload audio starts `data:audio/wav;base64,`, a valid fatigue JSON maps to `EmotionLabel.FATIGUE`, and HTTP/malformed/empty/keyless cases return acoustic neutral confidence 0.

- [ ] **Step 2: Run and verify failure**

```powershell
backend\.venv\Scripts\python.exe -m unittest backend.tests.test_emotion_acoustic -v
```

- [ ] **Step 3: Implement provider**

Use standard `wave` over `io.BytesIO`, Base64 encode the WAV, and POST to the official endpoint with model `qwen3-omni-30b-a3b-captioner`. Prompt the model to analyze delivery rather than factual content and return only the approved 8-label JSON. Use `temperature=0`, `max_tokens=180`, and network timeout `max(2.0, soft_deadline_seconds + 1.5)`. Parse list or string content and fail closed to neutral without logging response bodies or keys.

- [ ] **Step 4: Run tests and commit**

```powershell
git add backend/services/emotion/acoustic.py backend/tests/test_emotion_acoustic.py
git commit -m "feat: add dashscope acoustic emotion adapter"
```

---

### Task 4: Response Style And Hardware Authorization

**Files:**
- Create: `backend/services/emotion/policy.py`
- Create: `backend/tests/test_emotion_policy.py`

**Interfaces:**
- Produces: `style_for`, `speech_rate_for`, `PolicyContext`, `PolicyDecision`, `authorize_actions`.

- [ ] **Step 1: Write failing policy tests**

Cover: explicit stop is preserved; one expert cannot trigger hardware; demo-mode dual agreement can act once; mixed/late/cooldown blocks actions; anxiety never auto-switches photos; authorized audio is forced to loop false and volume 60; unavailable target is not replaced with random.

- [ ] **Step 2: Run and verify failure**

```powershell
backend\.venv\Scripts\python.exe -m unittest backend.tests.test_emotion_policy -v
```

- [ ] **Step 3: Implement policy**

Map joy→cheerful, sadness/loneliness→empathic, anxiety→soothing, anger→calm, fatigue→gentle, calm→warm, neutral→neutral. Rate multipliers: 1.06, 0.92, 0.90, 0.92, 0.90, 0.97, 1.0 respectively.

Deep-copy the LLM JSON. Preserve every `source="explicit"` action. Emotional actions require confidence ≥0.80, both modalities, no mixed result, no late-only result, no cooldown, and no prior same-category intervention. Normal mode also requires confirmation; demo mode does not. Unauthorized emotional actions become keep with empty URL and loop false. Unknown legacy source passes unchanged for feature-disabled/backward-compatible operation and is logged as legacy.

- [ ] **Step 4: Run tests and commit**

```powershell
git add backend/services/emotion/policy.py backend/tests/test_emotion_policy.py
git commit -m "feat: gate emotion-driven ambient actions"
```

---

### Task 5: Semantic Emotion Contract And Streaming Extraction

**Files:**
- Create: `backend/services/emotion/streaming.py`
- Create: `backend/tests/test_emotion_streaming.py`
- Modify: `backend/services/voice_system_prompt.md`
- Modify: `backend/tests/test_music_routing.py`

**Interfaces:**
- Produces: `extract_user_emotion(json_buffer) -> EmotionSignal | None`.
- LLM root order becomes `user_emotion`, `dialogue`, `action`.

- [ ] **Step 1: Write failing partial-buffer tests**

Assert a complete `user_emotion` object is parsed while `dialogue.tts_text` remains incomplete; incomplete braces return `None`; braces and escaped quotes inside evidence do not terminate parsing early.

- [ ] **Step 2: Run and verify failure**

```powershell
backend\.venv\Scripts\python.exe -m unittest backend.tests.test_emotion_streaming -v
```

- [ ] **Step 3: Implement quote-aware brace-balanced extraction**

Find the `"user_emotion"` marker, walk from its opening brace while tracking depth, quote state, and escapes, parse only the completed object, then call `EmotionSignal.from_mapping(..., source=SEMANTIC)`.

- [ ] **Step 4: Update prompt schema and examples**

Require `user_emotion` first and action `source=explicit|emotion|none`. Replace “immediately execute emotional combination” instructions with “generate candidates; backend authorizes them.” Keep all explicit command semantics. Change every example to loop false and volume 60. Explicit control uses source explicit, emotional candidate uses emotion, keep uses none. Replies must not claim a candidate was executed.

- [ ] **Step 5: Preserve old dialogue emotion compatibility**

Extend `test_music_routing.py` to prove old JSON still parses and existing metadata/category routing remains non-looping.

- [ ] **Step 6: Run tests and commit**

```powershell
backend\.venv\Scripts\python.exe -m unittest backend.tests.test_emotion_streaming backend.tests.test_music_routing -v
git add backend/services/emotion/streaming.py backend/tests/test_emotion_streaming.py backend/services/voice_system_prompt.md backend/tests/test_music_routing.py
git commit -m "feat: add semantic emotion streaming contract"
```

---

### Task 6: Parallel Pipeline Integration

**Files:**
- Create: `backend/tests/test_emotion_pipeline.py`
- Modify: `backend/services/voice_server.py`
- Modify: `backend/services/emotion/__init__.py`

**Interfaces:**
- `VoiceServer` owns `emotion_config`, `acoustic_emotion`, and `emotion_sessions`.
- `_call_llm_stream(..., acoustic_signal=None, emotion_turn=None)` updates semantic/fused state before queueing TTS.
- `_generate_tts_audio_only(text, client_ip, response_style=ResponseStyle.NEUTRAL)` applies only speech-rate style.

- [ ] **Step 1: Write failing orchestration tests**

Use `IsolatedAsyncioTestCase` and fake providers to assert: acoustic timeout returns `None` within the soft deadline; provider exception is swallowed; session clear delegates to the store; feature-disabled mode never invokes the acoustic client; late results never call action resolution.

- [ ] **Step 2: Run and verify helper failures**

```powershell
backend\.venv\Scripts\python.exe -m unittest backend.tests.test_emotion_pipeline -v
```

- [ ] **Step 3: Initialize feature safely**

After existing provider checks in `VoiceServer.__init__`, load `EmotionConfig.from_mapping(os.environ)`, create `EmotionSessionStore`, and create the DashScope client only when enabled/provider matches/key exists. Missing key logs provider unavailable but does not fail startup.

- [ ] **Step 4: Fork acoustic work before Whisper**

For physical PCM turns, start `asyncio.to_thread(client.analyze, pcm_data)` before awaiting `_transcribe`. After successful ASR, wait at most `acoustic_timeout_ms` using `asyncio.wait_for(asyncio.shield(task), ...)`. Construct per-turn state with acoustic, semantic, fused, style, late flag, timing, and intervention fields.

- [ ] **Step 5: Supply early acoustic context to DeepSeek**

Append a compact non-authoritative acoustic evidence block to the system prompt when available. State explicitly that DeepSeek must judge semantic emotion from words/context and may disagree.

- [ ] **Step 6: Fuse semantic emotion during streaming**

After each token enters `json_buffer`, call `extract_user_emotion` until semantic data exists. Fuse with early acoustic, smooth through the session store, map response style, and store results in turn state. Do not add another LLM call.

- [ ] **Step 7: Preserve text-before-TTS while adding style**

Queue `(sentence, response_style)` instead of only text. `tts_consumer` still sends `assistant_reply` before calling TTS. Pass style into `_generate_tts_audio_only`. Apply narrow speech-rate mapping only to CosyVoice, preserving volume 60 and unchanged edge/piper fallbacks.

- [ ] **Step 8: Gate actions before existing resolver**

After `_complete_llm_view_intent` and before `_resolve_cjson`, parse final semantic emotion, perform final fusion/smoothing, build `PolicyContext`, call `authorize_actions`, update intervention/cooldown state, then pass approved raw JSON to the unchanged resolver. Skip this entire hook when disabled.

- [ ] **Step 9: Handle late completion UI-only**

After a soft timeout, await the existing task in a background coroutine. It may update session state and send optional UI telemetry, but must mark late and must never parse/resolve actions.

- [ ] **Step 10: Clear state at existing session boundaries**

Next to the existing history clear calls for `wake_word_detected` and `session_end`, clear the emotion store. Do not change cancellation flags or WebSocket behavior.

- [ ] **Step 11: Run focused regression tests**

```powershell
backend\.venv\Scripts\python.exe -m unittest backend.tests.test_emotion_pipeline backend.tests.test_emotion_models backend.tests.test_emotion_fusion backend.tests.test_emotion_acoustic backend.tests.test_emotion_policy backend.tests.test_emotion_streaming backend.tests.test_music_routing -v
backend\.venv\Scripts\python.exe -m py_compile backend\services\voice_server.py
```

- [ ] **Step 12: Commit**

```powershell
git add backend/services/voice_server.py backend/services/emotion backend/tests/test_emotion_pipeline.py
git commit -m "feat: integrate multimodal emotion pipeline"
```

---

### Task 7: Diagnostics And Replay Evaluation

**Files:**
- Create: `backend/services/emotion/metrics.py`
- Create: `backend/tests/test_emotion_metrics.py`
- Create: `backend/tools/replay_emotion.py`
- Create: `backend/tests/fixtures/emotion/README.md`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `build_turn_metric(...) -> dict` and a read-only replay CLI.

- [ ] **Step 1: Write failing metric-redaction tests**

Assert the metric contains turn/timing/results/policy fields but never API keys or PCM/audio bytes.

- [ ] **Step 2: Run and verify failure**

```powershell
backend\.venv\Scripts\python.exe -m unittest backend.tests.test_emotion_metrics -v
```

- [ ] **Step 3: Implement pure JSON metric construction**

Serialize only label, dimensions, confidence, source, evidence, timing, deadline status, style, and authorization reason. Emit one concise JSON log line after each completed turn.

- [ ] **Step 4: Implement replay CLI**

Accept `--manifest` and `--output`. Manifest entries contain WAV path, expected label, and text. Validate WAV format, call configured acoustic provider, optionally load recorded semantic JSON, fuse, and report per-class precision/recall/F1, macro F1, high-confidence errors, and latency percentiles. Never touch photo/upload databases or device APIs.

- [ ] **Step 5: Track instructions but ignore local audio**

Add `.gitignore` rules for fixture WAV, local manifest, and reports. Track a README requiring 8 classes × 20 samples plus natural, acted, conflict, explicit-command, noise, and weak-signal subsets.

- [ ] **Step 6: Run tests and commit**

```powershell
backend\.venv\Scripts\python.exe -m unittest backend.tests.test_emotion_metrics -v
backend\.venv\Scripts\python.exe backend\tools\replay_emotion.py --help
git add backend/services/emotion/metrics.py backend/tests/test_emotion_metrics.py backend/tools/replay_emotion.py backend/tests/fixtures/emotion/README.md .gitignore
git commit -m "test: add emotion diagnostics and replay harness"
```

---

### Task 8: Full Regression And Firmware Build Gate

**Files:**
- Modify only when verification proves a minimal fix is necessary.

- [ ] **Step 1: Run complete backend tests**

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v
```

- [ ] **Step 2: Verify disabled mode**

With `EMOTION_ENABLED=false`, assert no acoustic request, no policy gate, old assistant emotion compatibility, and unchanged music/photo outputs.

- [ ] **Step 3: Verify ordering and outage cases**

Test acoustic before ASR, within deadline, after deadline, HTTP error, malformed JSON, modality conflict, and wake interruption while work is in flight. Expected: no uncaught exception, late action, or TTS interruption.

- [ ] **Step 4: Run syntax and whitespace checks**

```powershell
backend\.venv\Scripts\python.exe -m compileall -q backend\services\emotion backend\services\voice_server.py backend\tools\replay_emotion.py
git diff --check
```

- [ ] **Step 5: Build through the repository wrapper**

```powershell
.\scripts\idf.ps1 build
```

Expected: single-threaded ESP-IDF v6.0.1 build exits 0. Do not run `fullclean`, flash, or monitor.

- [ ] **Step 6: Run deterministic competition scenarios**

Verify joy one-time demo intervention, anxiety without auto photo, fatigue gentle style, conflict without action, timeout fallback, explicit stop priority, unavailable named media without random fallback, loop false, and volume 60.

- [ ] **Step 7: Commit only required regression fixes**

Do not create an empty commit. If fixes were needed, stage only those files and commit `fix: preserve voice regressions with emotion enhancement`.

## Final Manual Hardware Checklist

After review and explicit authorization to flash:

1. Start both backends with `.\restart_backend.bat`.
2. Flash/monitor with `.\scripts\idf.ps1 -p COM11 flash monitor`.
3. Confirm wake interruption during speech/music.
4. Confirm UI text starts before its TTS sentence.
5. Confirm continuous-dialogue polling remains non-blocking.
6. Confirm at most one same-category intervention per session.
7. Disconnect DashScope networking and confirm normal ASR→DeepSeek→TTS fallback.



