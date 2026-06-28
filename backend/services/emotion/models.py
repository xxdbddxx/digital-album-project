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
        def safe_int(name: str, default: int) -> int:
            try: return int(values.get(name, default))
            except (ValueError, TypeError): return default
        def safe_float(name: str, default: float) -> float:
            try: return float(values.get(name, default))
            except (ValueError, TypeError): return default

        return cls(
            enabled=flag("EMOTION_ENABLED", True),
            acoustic_provider=str(values.get("EMOTION_ACOUSTIC_PROVIDER", "dashscope")),
            acoustic_model=str(values.get("EMOTION_ACOUSTIC_MODEL", "qwen3-omni-30b-a3b-captioner")),
            acoustic_timeout_ms=safe_int("EMOTION_ACOUSTIC_TIMEOUT_MS", 400),
            demo_mode=flag("EMOTION_DEMO_MODE", True),
            session_window_turns=max(3, min(5, safe_int("EMOTION_SESSION_WINDOW_TURNS", 5))),
            ui_threshold=safe_float("EMOTION_UI_THRESHOLD", 0.45),
            response_threshold=safe_float("EMOTION_RESPONSE_THRESHOLD", 0.65),
            action_threshold=safe_float("EMOTION_ACTION_THRESHOLD", 0.80),
            intervention_cooldown_sec=safe_int("EMOTION_INTERVENTION_COOLDOWN_SEC", 300),
            tts_style_enabled=flag("EMOTION_TTS_STYLE_ENABLED", True),
        )
