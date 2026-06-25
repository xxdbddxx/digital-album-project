import unittest
from backend.services.emotion.fusion import fuse_emotions
from backend.services.emotion.models import EmotionLabel, EmotionSignal, EmotionSource
from backend.services.emotion.session import EmotionSessionStore


def signal(label, confidence, source, valence=-0.5, arousal=0.6):
    return EmotionSignal(label, valence, arousal, confidence, confidence, (), source)


class EmotionFusionTests(unittest.TestCase):
    def test_matching_modalities_raise_confidence(self):
        fused = fuse_emotions(
            signal(EmotionLabel.ANXIETY, 0.8, EmotionSource.ACOUSTIC),
            signal(EmotionLabel.ANXIETY, 0.7, EmotionSource.SEMANTIC),
        )
        self.assertEqual(fused.label, EmotionLabel.ANXIETY)
        self.assertGreaterEqual(fused.confidence, 0.8)
        self.assertFalse(fused.mixed)

    def test_positive_negative_conflict_is_mixed(self):
        fused = fuse_emotions(
            signal(EmotionLabel.ANXIETY, 0.9, EmotionSource.ACOUSTIC),
            signal(EmotionLabel.JOY, 0.9, EmotionSource.SEMANTIC, 0.8, 0.8),
        )
        self.assertTrue(fused.mixed)
        self.assertLess(fused.confidence, 0.8)

    def test_session_clear_removes_history(self):
        store = EmotionSessionStore(window_turns=3)
        store.update("client", fuse_emotions(None, signal(EmotionLabel.SADNESS, 0.8, EmotionSource.SEMANTIC)))
        store.clear("client")
        self.assertIsNone(store.current("client"))

    def test_intervention_state_is_scoped_to_session(self):
        store = EmotionSessionStore(window_turns=3, cooldown_sec=300)
        self.assertTrue(store.can_intervene("client", "audio:sad", now=1000.0))
        store.mark_intervention("client", "audio:sad", now=1000.0)
        self.assertFalse(store.can_intervene("client", "audio:sad", now=1100.0))
        store.clear("client")
        self.assertTrue(store.can_intervene("client", "audio:sad", now=1100.0))
