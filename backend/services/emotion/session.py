from collections import deque
from threading import RLock
from backend.services.emotion.models import FusedEmotion

class EmotionSessionStore:
    def __init__(self, window_turns: int = 5, cooldown_sec: int = 300):
        self.window_turns = window_turns
        self.cooldown_sec = cooldown_sec
        self._history: dict[str, deque[FusedEmotion]] = {}
        self._interventions: dict[str, dict[str, float]] = {}
        self._lock = RLock()

    def update(self, client_id: str, fused: FusedEmotion) -> FusedEmotion:
        with self._lock:
            if client_id not in self._history:
                self._history[client_id] = deque(maxlen=self.window_turns)

            history = self._history[client_id]
            if not history:
                history.append(fused)
                return fused

            prev = history[-1]

            if fused.label != prev.label:
                history.append(fused)
                return fused

            # EMA smoothing is valid only within the same discrete label.
            alpha = 0.65

            smoothed_confidence = (alpha * fused.confidence) + ((1 - alpha) * prev.confidence)
            smoothed_valence = (alpha * fused.valence) + ((1 - alpha) * prev.valence)
            smoothed_arousal = (alpha * fused.arousal) + ((1 - alpha) * prev.arousal)
            smoothed_intensity = (alpha * fused.intensity) + ((1 - alpha) * prev.intensity)

            smoothed = FusedEmotion(
                label=fused.label,
                valence=smoothed_valence,
                arousal=smoothed_arousal,
                intensity=smoothed_intensity,
                confidence=smoothed_confidence,
                mixed=fused.mixed,
                modalities=fused.modalities,
                evidence=fused.evidence
            )

            history.append(smoothed)
            return smoothed

    def current(self, client_id: str) -> FusedEmotion | None:
        with self._lock:
            history = self._history.get(client_id)
            if history:
                return history[-1]
            return None

    def can_intervene(self, client_id: str, category: str, now: float) -> bool:
        with self._lock:
            interventions = self._interventions.get(client_id, {})
            last_run = interventions.get(category, 0.0)
            return (now - last_run) >= self.cooldown_sec

    def mark_intervention(self, client_id: str, category: str, now: float) -> None:
        with self._lock:
            if client_id not in self._interventions:
                self._interventions[client_id] = {}
            self._interventions[client_id][category] = now

    def clear(self, client_id: str) -> None:
        with self._lock:
            self._history.pop(client_id, None)
            self._interventions.pop(client_id, None)
