import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
from backend.services.emotion.models import EmotionConfig, EmotionSignal, EmotionSource, EmotionLabel, FusedEmotion
from backend.services.voice_server import VoiceServer
from backend.services.emotion.session import EmotionSessionStore

class FakeAcousticClient:
    def __init__(self, fail=False, slow=False):
        self.fail = fail
        self.slow = slow

    def analyze(self, pcm_data, soft_deadline_seconds=0.4):
        if self.fail:
            raise Exception("Mock provider error")
        if self.slow:
            import time
            time.sleep(soft_deadline_seconds + 0.5)
        return EmotionSignal.neutral(EmotionSource.ACOUSTIC)

class EmotionPipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server = VoiceServer.__new__(VoiceServer)
        self.server.output_dir = "mock_dir"
        self.server.response_dir = "mock_dir"
        self.server.sessions = {}
        self.server.emotion_config = EmotionConfig.from_mapping({"EMOTION_ENABLED": "true"})
        self.server.acoustic_emotion = FakeAcousticClient()
        self.server.emotion_sessions = EmotionSessionStore()
        self.server.emotion_sessions.clear = MagicMock()
        self.client_ip = "127.0.0.1"

    async def test_acoustic_timeout_returns_none(self):
        self.server.acoustic_emotion = FakeAcousticClient(slow=True)
        result = await self.server._wait_for_acoustic_result(b"fake_pcm")
        self.assertIsNone(result)

    async def test_provider_exception_is_swallowed(self):
        self.server.acoustic_emotion = FakeAcousticClient(fail=True)
        result = await self.server._wait_for_acoustic_result(b"fake_pcm")
        self.assertIsNone(result)
        
    async def test_session_clear_delegates_to_store(self):
        self.server._clear_session(self.client_ip)
        self.server.emotion_sessions.clear.assert_called_with(self.client_ip)

    async def test_feature_disabled_mode_never_invokes_acoustic_client(self):
        self.server.emotion_config = EmotionConfig.from_mapping({"EMOTION_ENABLED": "false"})
        self.server.acoustic_emotion.analyze = MagicMock()
        result = await self.server._wait_for_acoustic_result(b"fake_pcm")
        self.assertIsNone(result)
        self.server.acoustic_emotion.analyze.assert_not_called()

    async def test_late_results_never_call_action_resolution(self):
        # mock policy context where is_late=True causes authorize_actions to keep
        from backend.services.emotion.policy import PolicyContext, authorize_actions
        fused = FusedEmotion(EmotionLabel.JOY, 0.9, 0.9, 0.9, 0.9, False, (EmotionSource.ACOUSTIC, EmotionSource.SEMANTIC))
        ctx = PolicyContext(fused, True, 0.80, False, True)
        
        llm_json = {"action": {"source": "emotion", "name": "play_music", "target": "happy"}}
        decision = authorize_actions(llm_json, ctx)
        self.assertEqual(decision.authorized_json["action"]["name"], "keep")
