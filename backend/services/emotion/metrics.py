import time
import copy
from collections import deque
from threading import RLock
from typing import Dict, Any, Optional


class EmotionMetricStore:
    def __init__(self, max_turns: int = 100):
        self._metrics = deque(maxlen=max(1, max_turns))
        self._lock = RLock()

    def record(self, metric: Dict[str, Any]) -> None:
        with self._lock:
            self._metrics.append(copy.deepcopy(metric))

    def snapshot(self) -> list[Dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(list(self._metrics))


def build_turn_metric(
    client_id: str,
    acoustic_signal: Optional[Any],
    semantic_signal: Optional[Any],
    fused_emotion: Optional[Any],
    action_decision: Optional[Any],
    timing: Dict[str, float],
    pcm_data: Optional[bytes] = None,
    deadline_status: str = "ok",
    response_style: Optional[str] = None
) -> Dict[str, Any]:
    metric = {
        "timestamp": time.time(),
        "client_id": client_id,
        "timing": timing,
        "deadline_status": deadline_status,
        "response_style": response_style,
        "acoustic": None,
        "semantic": None,
        "fused": None,
        "action": None
    }
    
    if acoustic_signal:
        metric["acoustic"] = {
            "label": acoustic_signal.label.value,
            "confidence": acoustic_signal.confidence,
            "valence": acoustic_signal.valence,
            "arousal": acoustic_signal.arousal,
            "source": acoustic_signal.source.value,
            "evidence": list(acoustic_signal.evidence)
        }
        
    if semantic_signal:
        metric["semantic"] = {
            "label": semantic_signal.label.value,
            "confidence": semantic_signal.confidence,
            "valence": semantic_signal.valence,
            "arousal": semantic_signal.arousal,
            "source": semantic_signal.source.value,
            "evidence": list(semantic_signal.evidence)
        }
        
    if fused_emotion:
        metric["fused"] = {
            "label": fused_emotion.label.value,
            "confidence": fused_emotion.confidence,
            "valence": fused_emotion.valence,
            "arousal": fused_emotion.arousal,
            "mixed": fused_emotion.mixed,
            "modalities": [m.value for m in fused_emotion.modalities],
            "evidence": list(fused_emotion.evidence)
        }
        
    if action_decision:
        metric["action"] = {
            "authorized": action_decision.authorized_json,
            "reasons": action_decision.reasons
        }
        
    return metric
