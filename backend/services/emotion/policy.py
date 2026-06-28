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
    fused: FusedEmotion | None
    is_demo_mode: bool
    action_threshold: float
    has_cooldown: bool
    is_late: bool

@dataclass
class PolicyDecision:
    authorized_json: dict[str, Any]
    reasons: list[str]

def authorize_actions(llm_json: dict[str, Any], context: PolicyContext) -> PolicyDecision:
    result = copy.deepcopy(llm_json)
    action = result.get("action", {})
    
    source = action.get("source")
    
    if source == "explicit":
        return PolicyDecision(authorized_json=result, reasons=["Explicit command passed unchanged"])
        
    if source == "emotion":
        reasons = []

        if context.fused is None:
            reasons.append("Missing fused emotion")
        elif context.fused.confidence < context.action_threshold:
            reasons.append(f"Confidence {context.fused.confidence:.2f} below threshold {context.action_threshold}")
        if context.fused is not None and (
            EmotionSource.ACOUSTIC not in context.fused.modalities
            or EmotionSource.SEMANTIC not in context.fused.modalities
        ):
            reasons.append(f"Missing modalities: {context.fused.modalities}")
        if context.fused is not None and context.fused.mixed:
            reasons.append("Emotion signals are mixed/conflicting")
        if context.is_late:
            reasons.append("Acoustic result was late")
        if context.has_cooldown:
            reasons.append("Intervention is on cooldown")

        screen_cmd = action.get("screen", {}).get("command")
        if (
            screen_cmd == "show_specific"
            and context.fused is not None
            and context.fused.label == EmotionLabel.ANXIETY
        ):
            reasons.append("Blocked auto photo switch during anxiety")
            
        if not reasons and not context.is_demo_mode:
            reasons.append("Blocked emotion action outside demo mode")
            
        if not reasons:
            if "audio" in action and isinstance(action["audio"], dict):
                action["audio"]["loop"] = False
                action["audio"]["volume"] = 60
            result["action"] = action
        else:
            action["source"] = "none"
            for key in ["mist", "audio", "screen"]:
                if key in action and isinstance(action[key], dict):
                    action[key]["command"] = "keep"
                else:
                    action[key] = {"command": "keep"}
            result["action"] = action
            
        return PolicyDecision(authorized_json=result, reasons=reasons)
        
    logger.info("Legacy action source passed unchanged")
    return PolicyDecision(authorized_json=result, reasons=["Legacy source passed unchanged"])
