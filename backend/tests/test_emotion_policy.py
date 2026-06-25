import unittest
from backend.services.emotion.models import EmotionLabel, EmotionSource, FusedEmotion
from backend.services.emotion.policy import (
    style_for, speech_rate_for, PolicyContext, authorize_actions, ResponseStyle
)

class EmotionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.fused = FusedEmotion(EmotionLabel.SADNESS, -0.5, 0.4, 0.6, 0.85, False, (EmotionSource.ACOUSTIC, EmotionSource.SEMANTIC))
        self.context = PolicyContext(
            fused=self.fused,
            is_demo_mode=True,
            action_threshold=0.80,
            has_cooldown=False,
            is_late=False
        )
        
    def test_style_and_rate_mapping(self):
        self.assertEqual(style_for(EmotionLabel.JOY), ResponseStyle.CHEERFUL)
        self.assertAlmostEqual(speech_rate_for(EmotionLabel.JOY), 1.06)
        self.assertEqual(style_for(EmotionLabel.SADNESS), ResponseStyle.EMPATHIC)
        self.assertAlmostEqual(speech_rate_for(EmotionLabel.SADNESS), 0.92)

    def test_explicit_command_is_preserved(self):
        llm_json = {"action": {"source": "explicit", "name": "stop"}}
        decision = authorize_actions(llm_json, self.context)
        self.assertEqual(decision.authorized_json["action"]["name"], "stop")

    def test_one_expert_cannot_trigger_hardware(self):
        fused = FusedEmotion(EmotionLabel.JOY, 0.8, 0.8, 0.8, 0.9, False, (EmotionSource.SEMANTIC,))
        ctx = PolicyContext(fused, True, 0.80, False, False)
        llm_json = {"action": {"source": "emotion", "name": "play_music", "target": "happy"}}
        decision = authorize_actions(llm_json, ctx)
        self.assertEqual(decision.authorized_json["action"]["name"], "keep")

    def test_demo_mode_dual_agreement_can_act(self):
        llm_json = {"action": {"source": "emotion", "name": "play_music", "target": "sad"}}
        decision = authorize_actions(llm_json, self.context)
        self.assertEqual(decision.authorized_json["action"]["name"], "play_music")
        self.assertEqual(decision.authorized_json["action"]["loop"], False)
        self.assertEqual(decision.authorized_json["action"]["volume"], 60)

    def test_mixed_blocks_actions(self):
        fused = FusedEmotion(EmotionLabel.SADNESS, -0.5, 0.4, 0.6, 0.85, True, (EmotionSource.ACOUSTIC, EmotionSource.SEMANTIC))
        ctx = PolicyContext(fused, True, 0.80, False, False)
        llm_json = {"action": {"source": "emotion", "name": "play_music"}}
        decision = authorize_actions(llm_json, ctx)
        self.assertEqual(decision.authorized_json["action"]["name"], "keep")

    def test_late_blocks_actions(self):
        ctx = PolicyContext(self.fused, True, 0.80, False, True)
        llm_json = {"action": {"source": "emotion", "name": "play_music"}}
        decision = authorize_actions(llm_json, ctx)
        self.assertEqual(decision.authorized_json["action"]["name"], "keep")

    def test_cooldown_blocks_actions(self):
        ctx = PolicyContext(self.fused, True, 0.80, True, False)
        llm_json = {"action": {"source": "emotion", "name": "play_music"}}
        decision = authorize_actions(llm_json, ctx)
        self.assertEqual(decision.authorized_json["action"]["name"], "keep")

    def test_anxiety_never_auto_switches_photos(self):
        fused = FusedEmotion(EmotionLabel.ANXIETY, -0.8, 0.9, 0.8, 0.9, False, (EmotionSource.ACOUSTIC, EmotionSource.SEMANTIC))
        ctx = PolicyContext(fused, True, 0.80, False, False)
        llm_json = {"action": {"source": "emotion", "name": "show_photo"}}
        decision = authorize_actions(llm_json, ctx)
        self.assertEqual(decision.authorized_json["action"]["name"], "keep")

    def test_unknown_legacy_source_passes_unchanged(self):
        llm_json = {"action": {"name": "play_music", "loop": True}}
        decision = authorize_actions(llm_json, self.context)
        self.assertEqual(decision.authorized_json["action"]["name"], "play_music")
        self.assertEqual(decision.authorized_json["action"]["loop"], True)
