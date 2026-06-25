from collections import deque
from backend.services.emotion.models import FusedEmotion

class EmotionSessionStore:
    def __init__(self, window_turns: int = 5, cooldown_sec: int = 300):
        self.window_turns = window_turns
        self.cooldown_sec = cooldown_sec
        self._history: dict[str, deque[FusedEmotion]] = {}
        self._interventions: dict[str, dict[str, float]] = {}

    def update(self, client_id: str, fused: FusedEmotion) -> FusedEmotion:
        if client_id not in self._history:
            self._history[client_id] = deque(maxlen=self.window_turns)
        
        history = self._history[client_id]
        if not history:
            history.append(fused)
            return fused

        prev = history[-1]
        
        # EMA Smoothing
        alpha = 0.85 if (fused.label != prev.label and fused.confidence > 0.7) else 0.65
        
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
        history = self._history.get(client_id)
        if history:
            return history[-1]
        return None

    def can_intervene(self, client_id: str, category: str, now: float) -> bool:
        interventions = self._interventions.get(client_id, {})
        last_run = interventions.get(category, 0.0)
        return (now - last_run) >= self.cooldown_sec

    def mark_intervention(self, client_id: str, category: str, now: float) -> None:
        if client_id not in self._interventions:
            self._interventions[client_id] = {}
        self._interventions[client_id][category] = now

    def clear(self, client_id: str) -> None:
        if client_id in self._history:
            del self._history[client_id]
        if client_id in self._interventions:
            del self._interventions[client_id]
