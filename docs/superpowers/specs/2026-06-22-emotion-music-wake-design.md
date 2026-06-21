# Emotion Music And Music Wake Design

## Goal

Fix wake-word interruption during vocal music, make mood-based music selection
deterministic, and propagate the LLM's real emotion to the device UI.

## Design

The backend keeps DeepSeek as the semantic emotion classifier. The classifier
uses ASR text and recent dialogue history; it does not infer emotion from pitch,
speaking rate, or microphone acoustics.

Music selection uses two layers:

1. Explicit title and artist matching from MP3 metadata.
2. Category and tag matching from `backend/music_tags.json`.

Existing valid macros such as `<search: sad>` are preserved. User wording is
only used when the model did not provide a usable search macro. Mood aliases
such as `悲伤`, `难过`, and `孤独` resolve to the `sad` category.

The backend sends the final emotion as a separate `assistant_emotion` event.
The ESP32 updates the existing dialogue panel accent without restarting the
typewriter animation.

During speaker playback, ESP-SR AEC uses full-duplex low-cost mode with normal
NLP. This retains echo cancellation while reducing suppression of a near-end
wake word that overlaps vocal music.

## Verification

- Backend unit tests cover mood aliases, tag matching, and preservation of
  model-provided search macros.
- Python syntax checks cover the modified backend.
- ESP-IDF v6.0.1 single-threaded build covers the firmware changes.
- Final hardware confirmation requires playing a vocal section of `晴天` and
  saying `你好小智`.
