# Emotion Pipeline Targeted Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the confirmed emotion-pipeline integration defects without replacing or restructuring the existing voice pipeline.

**Architecture:** Preserve the existing acoustic → semantic fusion → policy flow. Add explicit late-state propagation into JSON policy evaluation, make deterministic user intent override model-provided action source, align the policy command vocabulary with the existing screen protocol, and calculate emotional TTS rate before constructing the DashScope request.

**Tech Stack:** Python 3, `unittest`, `asyncio`, DashScope CosyVoice, existing emotion service modules.

## Global Constraints

- Treat this as an optimization and repair of an already working module.
- Do not refactor unrelated voice, ASR, WebSocket, photo, or music behavior.
- Explicit user commands must take priority over inferred emotional actions.
- Default audio volume remains `60%` and music remains non-looping.
- Timed-out or late acoustic evidence must not authorize hardware actions.
- Preserve all pre-existing uncommitted work in the feature worktree.

---

### Task 1: Late-State Policy Integration

**Files:**
- Modify: `backend/services/voice_server.py`
- Test: `backend/tests/test_emotion_pipeline.py`

**Interfaces:**
- Consumes: `_parse_llm_json(text, client_ip, user_text, fused_emotion)`
- Produces: `_parse_llm_json(..., is_late=False)` and `tts_state["acoustic_late"]`

- [ ] Add a regression test that invokes `_parse_llm_json` with a fused dual-modality emotion and `is_late=True`.
- [ ] Run the test and verify the current implementation fails because `st` is undefined or the argument is unsupported.
- [ ] Add an explicit `is_late` parameter, pass it into `PolicyContext`, and propagate whether the acoustic task completed after ASR.
- [ ] Run the regression test and verify emotional hardware commands are changed to `keep`.

### Task 2: Screen Command and Explicit Intent Priority

**Files:**
- Modify: `backend/services/emotion/policy.py`
- Modify: `backend/services/voice_server.py`
- Test: `backend/tests/test_emotion_policy.py`
- Test: `backend/tests/test_music_routing.py`

**Interfaces:**
- Consumes: screen command `show_specific`; deterministic intent completion.
- Produces: anxiety-safe screen policy and `action.source == "explicit"` for recognized user commands.

- [ ] Change the anxiety regression test to use the protocol command `show_specific` and verify it fails.
- [ ] Add tests proving explicit music and photo requests overwrite an incorrect model source of `emotion`.
- [ ] Update the policy command comparison to `show_specific`.
- [ ] Mark the action source as `explicit` only when deterministic completion recognizes a concrete music, photo, orientation, random-photo, or brightness request.
- [ ] Run the focused policy and routing tests.

### Task 3: Effective Emotional TTS Rate

**Files:**
- Modify: `backend/services/voice_server.py`
- Test: `backend/tests/test_emotion_pipeline.py`

**Interfaces:**
- Consumes: `speech_rate_for(fused_emotion.label)`.
- Produces: final `speech_rate` passed to `SpeechSynthesizer(...)`.

- [ ] Add a mocked DashScope regression test that captures constructor arguments.
- [ ] Verify the test fails because the constructor currently receives the unmodified base rate.
- [ ] Compute and clamp the final rate before constructing `SpeechSynthesizer`.
- [ ] Run the focused test and confirm the constructor receives the emotional rate.

### Task 4: Prompt and Verification

**Files:**
- Modify: `backend/services/voice_system_prompt.md`
- Modify only touched lines with trailing whitespace in current emotion changes.

- [ ] Replace the remaining behavioral `loop: true` instruction with `loop: false`.
- [ ] Run all emotion, policy, and routing tests.
- [ ] Run the complete backend unit-test suite.
- [ ] Run `git diff --check` and inspect `git diff` to ensure no unrelated changes were introduced.
