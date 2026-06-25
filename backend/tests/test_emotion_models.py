import unittest

from backend.services.emotion.models import EmotionConfig, EmotionLabel, EmotionSignal, EmotionSource


class EmotionModelTests(unittest.TestCase):
    def test_invalid_payload_becomes_low_confidence_neutral(self):
        signal = EmotionSignal.from_mapping(
            {"label": "panic", "confidence": 4, "valence": -8},
            source=EmotionSource.ACOUSTIC,
        )
        self.assertEqual(signal.label, EmotionLabel.NEUTRAL)
        self.assertEqual(signal.confidence, 0.0)
        self.assertEqual(signal.valence, -1.0)

    def test_config_reads_competition_defaults(self):
        config = EmotionConfig.from_mapping({})
        self.assertTrue(config.enabled)
        self.assertTrue(config.demo_mode)
        self.assertEqual(config.acoustic_timeout_ms, 400)
        self.assertEqual(config.session_window_turns, 5)
        self.assertEqual(config.action_threshold, 0.80)
