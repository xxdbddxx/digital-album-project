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

    def test_extracts_final_dialogue_emotion(self):
        response = (
            '{"dialogue":{"tts_text":"我会陪着你。","emotion":"empathic"},'
            '"action":{"audio":{"command":"keep"}}}'
        )

        emotion = voice_server._extract_dialogue_emotion(response)

        self.assertEqual(emotion, "empathic")


if __name__ == "__main__":
    unittest.main()
