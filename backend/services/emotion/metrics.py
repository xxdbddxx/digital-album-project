import time
from typing import Dict, Any, Optional

def build_turn_metric(
    client_id: str,
    acoustic_signal: Optional[Any],
    semantic_signal: Optional[Any],
    fused_emotion: Optional[Any],
    action_decision: Optional[Any],
    timing: Dict[str, float],
    pcm_data: Optional[bytes] = None
) -> Dict[str, Any]:
    metric = {
        "timestamp": time.time(),
        "client_id": client_id,
        "timing": timing,
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
            "arousal": acoustic_signal.arousal
        }
        
    if semantic_signal:
        metric["semantic"] = {
            "label": semantic_signal.label.value,
            "confidence": semantic_signal.confidence,
            "valence": semantic_signal.valence,
            "arousal": semantic_signal.arousal
        }
        
    if fused_emotion:
        metric["fused"] = {
            "label": fused_emotion.label.value,
            "confidence": fused_emotion.confidence,
            "valence": fused_emotion.valence,
            "arousal": fused_emotion.arousal,
            "mixed": fused_emotion.mixed
        }
        
    if action_decision:
        metric["action"] = {
            "authorized": action_decision.authorized_json,
            "reasons": action_decision.reasons
        }
        
    return metric
