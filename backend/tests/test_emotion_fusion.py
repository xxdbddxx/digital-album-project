import unittest
import threading
from backend.services.emotion.fusion import fuse_emotions
from backend.services.emotion.models import EmotionLabel, EmotionSignal, EmotionSource
from backend.services.emotion.session import EmotionSessionStore


def signal(label, confidence, source, valence=-0.5, arousal=0.6):
    return EmotionSignal(label, valence, arousal, confidence, confidence, (), source)


class EmotionFusionTests(unittest.TestCase):
    def test_session_store_has_reentrant_lock(self):
        store = EmotionSessionStore()

        self.assertIsInstance(store._lock, type(threading.RLock()))

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

    def test_compatible_negative_emotions_keep_strong_confidence(self):
        fused = fuse_emotions(
            signal(
                EmotionLabel.ANXIETY,
                0.8,
                EmotionSource.ACOUSTIC,
                valence=-0.8,
                arousal=0.9,
            ),
            signal(
                EmotionLabel.SADNESS,
                0.7,
                EmotionSource.SEMANTIC,
                valence=-0.6,
                arousal=0.4,
            ),
        )

        self.assertFalse(fused.mixed)
        self.assertGreaterEqual(fused.confidence, 0.8)
        self.assertGreater(fused.valence, -0.8)
        self.assertLess(fused.valence, -0.6)

    def test_session_clear_removes_history(self):
        store = EmotionSessionStore(window_turns=3)
        store.update("client", fuse_emotions(None, signal(EmotionLabel.SADNESS, 0.8, EmotionSource.SEMANTIC)))
        store.clear("client")
        self.assertIsNone(store.current("client"))

    def test_label_change_resets_session_smoothing(self):
        store = EmotionSessionStore(window_turns=3)
        previous = signal(
            EmotionLabel.JOY,
            0.9,
            EmotionSource.SEMANTIC,
            valence=0.9,
            arousal=0.8,
        )
        current = signal(
            EmotionLabel.SADNESS,
            0.2,
            EmotionSource.SEMANTIC,
            valence=-0.2,
            arousal=0.2,
        )
        store.update("client", fuse_emotions(None, previous))

        smoothed = store.update("client", fuse_emotions(None, current))

        self.assertEqual(smoothed.label, EmotionLabel.SADNESS)
        self.assertEqual(smoothed.confidence, current.confidence)
        self.assertEqual(smoothed.valence, current.valence)
        self.assertEqual(smoothed.arousal, current.arousal)

    def test_intervention_state_is_scoped_to_session(self):
        store = EmotionSessionStore(window_turns=3, cooldown_sec=300)
        self.assertTrue(store.can_intervene("client", "audio:sad", now=1000.0))
        store.mark_intervention("client", "audio:sad", now=1000.0)
        self.assertFalse(store.can_intervene("client", "audio:sad", now=1100.0))
        store.clear("client")
        self.assertTrue(store.can_intervene("client", "audio:sad", now=1100.0))
