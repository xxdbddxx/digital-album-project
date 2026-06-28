import unittest
from unittest.mock import patch

from backend.services import voice_server


class MusicRoutingTests(unittest.TestCase):
    def setUp(self):
        self.catalog = [
            {
                "filename": "ambient_piano_1.mp3",
                "stem": "ambient_piano_1",
                "title": "",
                "artist": "",
                "album": "",
            },
            {
                "filename": "test.mp3",
                "stem": "test",
                "title": "晴天",
                "artist": "周杰伦",
                "album": "",
            },
        ]

    @patch.object(voice_server, "_music_catalog")
    def test_chinese_sad_request_selects_sad_category(self, catalog):
        catalog.return_value = self.catalog

        selected = voice_server._select_music_file("播放悲伤的音乐")

        self.assertEqual(selected, "ambient_piano_1.mp3")

    @patch.object(voice_server, "_music_catalog")
    def test_manifest_tags_participate_in_music_search(self, catalog):
        catalog.return_value = self.catalog

        with patch.object(
            voice_server,
            "_music_tag_manifest",
            return_value={"ambient_piano_1.mp3": ["忧郁", "安静"]},
        ):
            selected = voice_server._select_music_file("来一首忧郁的音乐")

        self.assertEqual(selected, "ambient_piano_1.mp3")

    @patch.object(voice_server, "_available_music_files", return_value=[])
    def test_model_music_macro_is_not_overwritten(self, _available):
        server = voice_server.VoiceServer.__new__(voice_server.VoiceServer)
        raw = {
            "dialogue": {"tts_text": "为你播放舒缓的钢琴曲。", "emotion": "sad"},
            "action": {
                "audio": {
                    "command": "play",
                    "url": "<search: sad>",
                    "loop": False,
                    "volume": 60,
                },
                "screen": {"command": "keep"},
            },
        }

        server._complete_llm_view_intent(raw, "播放悲伤的音乐")

        self.assertEqual(raw["action"]["audio"]["url"], "<search: sad>")

    @patch.object(voice_server, "_available_music_files", return_value=[])
    def test_explicit_music_request_overrides_model_emotion_source(
        self, _available
    ):
        server = voice_server.VoiceServer.__new__(voice_server.VoiceServer)
        raw = {
            "dialogue": {"tts_text": "好的", "emotion": "neutral"},
            "action": {
                "source": "emotion",
                "audio": {"command": "keep"},
                "screen": {"command": "keep"},
            },
        }

        server._complete_llm_view_intent(raw, "播放悲伤的音乐")

        self.assertEqual(raw["action"]["source"], "explicit")
        self.assertEqual(raw["action"]["audio"]["command"], "play")

    @patch.object(
        voice_server, "_extract_photo_search_query", return_value="猫"
    )
    def test_explicit_photo_request_overrides_model_emotion_source(
        self, _extract_query
    ):
        server = voice_server.VoiceServer.__new__(voice_server.VoiceServer)
        raw = {
            "dialogue": {"tts_text": "好的", "emotion": "neutral"},
            "action": {
                "source": "emotion",
                "audio": {"command": "keep"},
                "screen": {"command": "keep"},
            },
        }

        server._complete_llm_view_intent(raw, "显示一张猫的照片")

        self.assertEqual(raw["action"]["source"], "explicit")
        self.assertEqual(raw["action"]["screen"]["command"], "show_specific")

    def test_extracts_final_dialogue_emotion(self):
        response = (
            '{"dialogue":{"tts_text":"我会陪着你。","emotion":"empathic"},'
            '"action":{"audio":{"command":"keep"}}}'
        )

        emotion = voice_server._extract_dialogue_emotion(response)

        self.assertEqual(emotion, "empathic")

    def test_preserves_old_dialogue_emotion_compatibility(self):
        from backend.services.emotion.streaming import extract_user_emotion
        response = (
            '{"dialogue":{"tts_text":"我会陪着你。","emotion":"empathic"},'
            '"action":{"audio":{"command":"keep"}}}'
        )
        self.assertIsNone(extract_user_emotion(response))
        self.assertEqual(voice_server._extract_dialogue_emotion(response), "empathic")


if __name__ == "__main__":
    unittest.main()
