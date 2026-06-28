from backend.services.emotion.models import EmotionLabel, EmotionSignal, EmotionSource, FusedEmotion

def fuse_emotions(acoustic: EmotionSignal | None, semantic: EmotionSignal | None) -> FusedEmotion:
    if not acoustic and not semantic:
        return FusedEmotion(EmotionLabel.NEUTRAL, 0.0, 0.0, 0.0, 0.0, False, ())

    if acoustic and not semantic:
        return FusedEmotion(
            label=acoustic.label, valence=acoustic.valence, arousal=acoustic.arousal,
            intensity=acoustic.intensity, confidence=acoustic.confidence,
            mixed=False, modalities=(EmotionSource.ACOUSTIC,), evidence=acoustic.evidence
        )

    if semantic and not acoustic:
        return FusedEmotion(
            label=semantic.label, valence=semantic.valence, arousal=semantic.arousal,
            intensity=semantic.intensity, confidence=semantic.confidence,
            mixed=False, modalities=(EmotionSource.SEMANTIC,), evidence=semantic.evidence
        )

    # Both present
    weights = {
        EmotionLabel.SADNESS: (0.30, 0.70),
        EmotionLabel.LONELINESS: (0.30, 0.70),
        EmotionLabel.ANXIETY: (0.55, 0.45),
        EmotionLabel.ANGER: (0.55, 0.45),
        EmotionLabel.FATIGUE: (0.55, 0.45),
        EmotionLabel.JOY: (0.50, 0.50),
        EmotionLabel.CALM: (0.50, 0.50),
        EmotionLabel.NEUTRAL: (0.50, 0.50),
    }

    mixed = (acoustic.valence * semantic.valence) < 0
    compatible_polarity = (acoustic.valence * semantic.valence) > 0

    if acoustic.label == semantic.label:
        best_signal = acoustic if acoustic.confidence >= semantic.confidence else semantic
        confidence = max(acoustic.confidence, semantic.confidence)
        if best_signal.label != EmotionLabel.NEUTRAL:
            confidence = min(1.0, confidence + 0.10)
        valence = best_signal.valence
        arousal = best_signal.arousal
        intensity = best_signal.intensity
    else:
        a_score = acoustic.confidence * weights.get(acoustic.label, (0.5, 0.5))[0]
        s_score = semantic.confidence * weights.get(semantic.label, (0.5, 0.5))[1]
        
        if a_score >= s_score:
            best_signal = acoustic
            confidence = a_score
        else:
            best_signal = semantic
            confidence = s_score

        if compatible_polarity:
            stronger = max(acoustic.confidence, semantic.confidence)
            weaker = min(acoustic.confidence, semantic.confidence)
            confidence = min(1.0, 0.70 * stronger + 0.30 * weaker + 0.05)
            total_confidence = acoustic.confidence + semantic.confidence
            acoustic_ratio = (
                acoustic.confidence / total_confidence
                if total_confidence
                else 0.5
            )
            semantic_ratio = 1.0 - acoustic_ratio
            valence = (
                acoustic_ratio * acoustic.valence
                + semantic_ratio * semantic.valence
            )
            arousal = (
                acoustic_ratio * acoustic.arousal
                + semantic_ratio * semantic.arousal
            )
            intensity = (
                acoustic_ratio * acoustic.intensity
                + semantic_ratio * semantic.intensity
            )
        else:
            valence = best_signal.valence
            arousal = best_signal.arousal
            intensity = best_signal.intensity

    if mixed:
        confidence = min(0.64, confidence)

    return FusedEmotion(
        label=best_signal.label,
        valence=valence,
        arousal=arousal,
        intensity=intensity,
        confidence=confidence,
        mixed=mixed,
        modalities=(EmotionSource.ACOUSTIC, EmotionSource.SEMANTIC),
        evidence=acoustic.evidence + semantic.evidence
    )
