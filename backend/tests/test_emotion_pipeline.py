import asyncio
import threading
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
from backend.services.emotion.models import EmotionConfig, EmotionSignal, EmotionSource, EmotionLabel, FusedEmotion
from backend.services.voice_server import VoiceServer
from backend.services import voice_server
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

    def test_streaming_tts_decoder_accepts_real_control_characters(self):
        raw_value = "第一句。\n第二句\r第三句\t结束"

        decoded = voice_server._decode_streaming_json_string(raw_value)

        self.assertEqual(decoded, raw_value)

    def test_streaming_tts_decoder_preserves_valid_json_escapes(self):
        raw_value = r"第一句。\n第二句\u3002"

        decoded = voice_server._decode_streaming_json_string(raw_value)

        self.assertEqual(decoded, "第一句。\n第二句。")

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

    @patch(
        "backend.services.voice_server._post_asr_text_to_flask",
        return_value=None,
    )
    @patch(
        "backend.services.voice_server._post_llm_reply_to_flask",
        return_value=None,
    )
    async def test_slow_acoustic_result_does_not_delay_llm_start(
        self, _post_reply, _post_asr
    ):
        acoustic_release = asyncio.Event()
        llm_started = threading.Event()

        async def slow_acoustic(_pcm_data):
            await acoustic_release.wait()
            return EmotionSignal.neutral(EmotionSource.ACOUSTIC)

        def call_llm(*_args, **_kwargs):
            llm_started.set()
            return (
                '{"dialogue":{"tts_text":"","emotion":"neutral"},'
                '"action":{"source":"none"}}'
            )

        self.server._wait_for_acoustic_result = slow_acoustic
        self.server._transcribe = lambda _pcm_data: "今天不太顺利"
        self.server._call_llm_stream = call_llm
        self.server._append_history = MagicMock()
        self.server._generate_tts_audio_only = MagicMock(return_value=None)
        self.server._parse_llm_json = MagicMock(return_value=None)
        self.server._apply_keyword_view_fallback = MagicMock()
        self.server._parse_intent = MagicMock(return_value=None)
        websocket = AsyncMock()
        state = {
            "cancel_requested": False,
            "is_web_simulated": False,
        }

        pipeline = asyncio.create_task(
            self.server._streaming_pipeline(
                b"pcm", websocket, state, self.client_ip
            )
        )
        try:
            await asyncio.sleep(0.05)
            self.assertTrue(
                llm_started.is_set(),
                "LLM waited for the acoustic task to finish",
            )
        finally:
            acoustic_release.set()
            await pipeline

    async def test_late_results_never_call_action_resolution(self):
        # mock policy context where is_late=True causes authorize_actions to keep
        from backend.services.emotion.policy import PolicyContext, authorize_actions
        fused = FusedEmotion(EmotionLabel.JOY, 0.9, 0.9, 0.9, 0.9, False, (EmotionSource.ACOUSTIC, EmotionSource.SEMANTIC))
        ctx = PolicyContext(fused, True, 0.80, False, True)
        
        llm_json = {"action": {"source": "emotion", "audio": {"command": "play"}}}
        decision = authorize_actions(llm_json, ctx)
        self.assertEqual(decision.authorized_json["action"]["audio"]["command"], "keep")

    def test_parse_llm_json_blocks_late_emotion_action(self):
        fused = FusedEmotion(
            EmotionLabel.JOY,
            0.9,
            0.9,
            0.9,
            0.9,
            False,
            (EmotionSource.ACOUSTIC, EmotionSource.SEMANTIC),
        )
        self.server._resolve_cjson = lambda raw, _client_ip: (raw, False)
        payload = (
            '{"dialogue":{"tts_text":"好的","emotion":"cheerful"},'
            '"action":{"source":"emotion","audio":{"command":"play"}}}'
        )

        result, _ = self.server._parse_llm_json(
            payload,
            self.client_ip,
            "我今天心情很好",
            fused,
            is_late=True,
        )

        self.assertEqual(result["action"]["source"], "none")
        self.assertEqual(result["action"]["audio"]["command"], "keep")

    def test_parse_llm_json_rejects_emotion_action_without_fusion(self):
        self.server._resolve_cjson = lambda raw, _client_ip: (raw, False)
        payload = (
            '{"dialogue":{"tts_text":"好的","emotion":"neutral"},'
            '"action":{"source":"emotion","audio":{"command":"play"}}}'
        )

        result, _ = self.server._parse_llm_json(
            payload,
            self.client_ip,
            "今天不太顺利",
            fused_emotion=None,
        )

        self.assertEqual(result["action"]["source"], "none")
        self.assertEqual(result["action"]["audio"]["command"], "keep")

    def test_parse_llm_json_rejects_emotion_action_when_feature_disabled(self):
        self.server.emotion_config = EmotionConfig.from_mapping(
            {"EMOTION_ENABLED": "false"}
        )
        self.server._resolve_cjson = lambda raw, _client_ip: (raw, False)
        payload = (
            '{"dialogue":{"tts_text":"好的","emotion":"neutral"},'
            '"action":{"source":"emotion","audio":{"command":"play"}}}'
        )

        result, _ = self.server._parse_llm_json(
            payload,
            self.client_ip,
            "今天不太顺利",
            fused_emotion=None,
        )

        self.assertEqual(result["action"]["source"], "none")
        self.assertEqual(result["action"]["audio"]["command"], "keep")

    def test_parse_llm_json_records_emotion_turn_metric(self):
        fused = FusedEmotion(
            EmotionLabel.JOY,
            0.8,
            0.8,
            0.8,
            0.9,
            False,
            (EmotionSource.ACOUSTIC, EmotionSource.SEMANTIC),
        )
        self.server._resolve_cjson = lambda raw, _client_ip: (raw, False)
        self.server.emotion_metrics = MagicMock()
        payload = (
            '{"dialogue":{"tts_text":"好的","emotion":"cheerful"},'
            '"action":{"source":"emotion","audio":{"command":"play"}}}'
        )

        self.server._parse_llm_json(
            payload,
            self.client_ip,
            "今天不太顺利",
            fused,
        )

        self.server.emotion_metrics.record.assert_called_once()
        metric = self.server.emotion_metrics.record.call_args.args[0]
        self.assertEqual(metric["fused"]["label"], "joy")
        self.assertNotIn("pcm_data", metric)

    @patch("backend.services.voice_server.ALIYUN_TTS_SPEECH_RATE", 1.0)
    @patch("dashscope.audio.tts_v2.SpeechSynthesizer")
    def test_emotional_tts_rate_is_passed_to_synthesizer(
        self, synthesizer_class
    ):
        synthesizer = synthesizer_class.return_value
        synthesizer.call.return_value = b"pcm"
        synthesizer.get_first_package_delay.return_value = 10
        self.server.aliyun_tts_ok = True
        fused = FusedEmotion(
            EmotionLabel.JOY,
            0.8,
            0.8,
            0.8,
            0.9,
            False,
            (EmotionSource.ACOUSTIC, EmotionSource.SEMANTIC),
        )

        result = self.server._generate_tts_audio_only(
            "测试语音", self.client_ip, fused
        )

        self.assertEqual(result, ("aliyun-cosyvoice", b"pcm"))
        self.assertAlmostEqual(
            synthesizer_class.call_args.kwargs["speech_rate"], 1.06
        )

    @patch('backend.services.voice_server.WhisperModel', create=True)
    def test_server_init_loads_emotion_pipeline(self, mock_whisper):
        import os
        from backend.services.voice_server import VoiceServer
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test_key", "EMOTION_ENABLED": "true"}), \
             patch.object(VoiceServer, '_check_aliyun_tts', return_value=False), \
             patch.object(VoiceServer, '_check_edge_tts', return_value=False), \
             patch.object(VoiceServer, '_check_piper', return_value=False):
            server = VoiceServer()
            self.assertIsNotNone(server.emotion_config)
            self.assertIsNotNone(server.acoustic_emotion)
            self.assertIsNotNone(server.emotion_sessions)
            self.assertTrue(server.emotion_config.enabled)
