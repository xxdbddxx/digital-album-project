import unittest
import json
from backend.services.emotion.metrics import EmotionMetricStore, build_turn_metric
from backend.services.emotion.models import EmotionSignal, EmotionLabel, EmotionSource

class EmotionMetricsTests(unittest.TestCase):
    def test_metric_store_is_bounded(self):
        store = EmotionMetricStore(max_turns=2)

        store.record({"turn": 1})
        store.record({"turn": 2})
        store.record({"turn": 3})

        self.assertEqual(
            store.snapshot(),
            [{"turn": 2}, {"turn": 3}],
        )

    def test_metric_redaction(self):
        signal = EmotionSignal(EmotionLabel.JOY, 0.9, 0.9, 0.9, 0.9, ("evidence",), EmotionSource.SEMANTIC)
        metric = build_turn_metric(
            client_id="127.0.0.1",
            acoustic_signal=signal,
            semantic_signal=signal,
            fused_emotion=None,
            action_decision=None,
            timing={"start": 0},
            pcm_data=b"secret_audio_bytes_that_should_not_be_logged"
        )
        metric_str = json.dumps(metric)
        self.assertNotIn("secret_audio_bytes", metric_str)
        self.assertIn("timing", metric)
        self.assertEqual(metric["client_id"], "127.0.0.1")
