# Emotion Pipeline Six Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the six confirmed emotion-pipeline defects in priority order without restructuring the working voice pipeline.

**Architecture:** Keep the existing ASR, LLM, TTS, fusion, policy, and device-control boundaries. Make policy fail closed, publish acoustic results into shared per-turn state without delaying LLM startup, harden streaming string decoding, correct cross-label session smoothing and compatible fusion, then add locking and lightweight in-memory metrics.

**Tech Stack:** Python 3.10, asyncio, unittest, DashScope, existing VoiceServer and emotion service modules.

## Global Constraints

- Preserve existing working behavior outside the emotion optimization module.
- Explicit user commands always override inferred emotional actions.
- Late acoustic results never trigger hardware actions.
- Do not change PCM streaming throttling.
- Do not add a database, frontend panel, or external dependency.
- Keep default volume at 60 and music non-looping.

---

### Task 1: Fail-Closed Emotion Policy

**Files:** `backend/services/emotion/policy.py`, `backend/services/voice_server.py`, `backend/tests/test_emotion_pipeline.py`

- [ ] Add a test where `source=emotion` and `fused_emotion=None`; expect source `none` and all commands `keep`.
- [ ] Run it and verify the unsafe action currently passes.
- [ ] Allow PolicyContext to represent missing fusion and reject it with a reason.
- [ ] Invoke policy for every emotion-sourced action when emotion handling is enabled.
- [ ] Run focused tests.

### Task 2: Non-Blocking Acoustic Collection

**Files:** `backend/services/voice_server.py`, `backend/tests/test_emotion_pipeline.py`

- [ ] Add a pipeline test proving LLM starts before a slow acoustic task completes.
- [ ] Run it and verify the current pipeline waits.
- [ ] Add a collector coroutine that publishes an on-time result into `tts_state`; mark results arriving after semantic extraction as late.
- [ ] Read the current acoustic result from `tts_state` when semantic emotion is extracted.
- [ ] Run focused tests.

### Task 3: Streaming TTS Control Characters

**Files:** `backend/services/voice_server.py`, `backend/tests/test_emotion_pipeline.py`

- [ ] Add tests for real newline, carriage return, and tab characters in a partial `tts_text`.
- [ ] Run them and verify decoding fails.
- [ ] Add a small decoder that escapes illegal JSON control characters while preserving valid JSON escapes.
- [ ] Use it in streaming and fallback TTS extraction.
- [ ] Run focused tests.

### Task 4: Cross-Label Session Smoothing

**Files:** `backend/services/emotion/session.py`, `backend/tests/test_emotion_fusion.py`

- [ ] Add a test for JOY followed by low-confidence SADNESS.
- [ ] Verify the current result inherits positive valence and inflated confidence.
- [ ] Reset smoothing when labels differ; retain EMA only for the same label.
- [ ] Run fusion/session tests.

### Task 5: Compatible Emotion Fusion

**Files:** `backend/services/emotion/fusion.py`, `backend/tests/test_emotion_fusion.py`

- [ ] Add a test for strong ANXIETY plus SADNESS with matching negative valence.
- [ ] Verify confidence is currently below the action threshold.
- [ ] Blend compatible same-polarity evidence while keeping opposite polarity mixed and capped.
- [ ] Run fusion and policy tests.

### Task 6: Session Locking and Lightweight Metrics

**Files:** `backend/services/emotion/session.py`, `backend/services/emotion/metrics.py`, `backend/services/voice_server.py`, `backend/tests/test_emotion_fusion.py`, `backend/tests/test_emotion_metrics.py`

- [ ] Add concurrency tests for update/clear and a bounded metric store test.
- [ ] Run tests and verify missing interfaces or race-sensitive behavior.
- [ ] Guard session state with `threading.RLock`.
- [ ] Add a bounded in-memory metric recorder and record one redacted metric per parsed emotion turn.
- [ ] Run focused and complete backend tests, `py_compile`, and `git diff --check`.
