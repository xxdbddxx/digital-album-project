import copy
import logging
from dataclasses import dataclass
from typing import Any
from backend.services.emotion.models import EmotionLabel, FusedEmotion, ResponseStyle, EmotionSource

logger = logging.getLogger(__name__)

def style_for(label: EmotionLabel) -> ResponseStyle:
    mapping = {
        EmotionLabel.JOY: ResponseStyle.CHEERFUL,
        EmotionLabel.SADNESS: ResponseStyle.EMPATHIC,
        EmotionLabel.LONELINESS: ResponseStyle.EMPATHIC,
        EmotionLabel.ANXIETY: ResponseStyle.SOOTHING,
        EmotionLabel.ANGER: ResponseStyle.CALM,
        EmotionLabel.FATIGUE: ResponseStyle.GENTLE,
        EmotionLabel.CALM: ResponseStyle.WARM,
        EmotionLabel.NEUTRAL: ResponseStyle.NEUTRAL,
    }
    return mapping.get(label, ResponseStyle.NEUTRAL)

def speech_rate_for(label: EmotionLabel) -> float:
    mapping = {
        EmotionLabel.JOY: 1.06,
        EmotionLabel.SADNESS: 0.92,
        EmotionLabel.LONELINESS: 0.92,
        EmotionLabel.ANXIETY: 0.90,
        EmotionLabel.ANGER: 0.92,
        EmotionLabel.FATIGUE: 0.90,
        EmotionLabel.CALM: 0.97,
        EmotionLabel.NEUTRAL: 1.0,
    }
    return mapping.get(label, 1.0)

@dataclass
class PolicyContext:
    fused: FusedEmotion
    is_demo_mode: bool
    action_threshold: float
    has_cooldown: bool
    is_late: bool

@dataclass
class PolicyDecision:
    authorized_json: dict[str, Any]

def authorize_actions(llm_json: dict[str, Any], context: PolicyContext) -> PolicyDecision:
    result = copy.deepcopy(llm_json)
    action = result.get("action", {})
    
    source = action.get("source")
    
    if source == "explicit":
        return PolicyDecision(authorized_json=result)
        
    if source == "emotion":
        authorized = True
        
        if context.fused.confidence < context.action_threshold:
            authorized = False
        if EmotionSource.ACOUSTIC not in context.fused.modalities or EmotionSource.SEMANTIC not in context.fused.modalities:
            authorized = False
        if context.fused.mixed:
            authorized = False
        if context.is_late:
            authorized = False
        if context.has_cooldown:
            authorized = False
        if not context.is_demo_mode:
            authorized = False
            
        if action.get("name") == "show_photo" and context.fused.label == EmotionLabel.ANXIETY:
            authorized = False
            
        if authorized:
            action["loop"] = False
            action["volume"] = 60
            result["action"] = action
        else:
            result["action"] = {
                "source": "emotion",
                "name": "keep",
                "target": "",
                "loop": False,
                "volume": 60
            }
            
        return PolicyDecision(authorized_json=result)
        
    logger.info("Legacy action source passed unchanged")
    return PolicyDecision(authorized_json=result)
