import json
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from backend.services import voice_server


class _JsonResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


class VoicePhotoRoutingTests(unittest.TestCase):
    def test_search_photo_result_prefers_normalized_local_keyword(self):
        server = voice_server.VoiceServer.__new__(voice_server.VoiceServer)
        requested_queries = []

        def fake_urlopen(url, timeout):
            parsed = urlparse(url)
            requested_queries.append(parse_qs(parsed.query).get("q", [""])[0])
            self.assertEqual(requested_queries[0], "狗")
            return _JsonResponse({"id": "dog-photo"})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = server._search_photo_result("小狗")

        self.assertEqual(result["id"], "dog-photo")
        self.assertEqual(requested_queries, ["狗"])

    def test_unresolved_photo_search_does_not_send_placeholder_to_device(self):
        server = voice_server.VoiceServer.__new__(voice_server.VoiceServer)
        raw = {
            "dialogue": {"tts_text": "好的", "emotion": "neutral"},
            "action": {
                "screen": {
                    "command": "show_specific",
                    "url": "<search: 不存在的照片>",
                    "orientation": "keep",
                },
                "audio": {"command": "keep"},
            },
        }

        with patch.object(server, "_get_best_local_ip", return_value="127.0.0.1"), \
             patch.object(server, "_search_photo_result", return_value={}):
            resolved, view_handled = server._resolve_cjson(raw, "127.0.0.1")

        self.assertFalse(view_handled)
        self.assertEqual(resolved["action"]["screen"]["command"], "keep")
        self.assertEqual(resolved["action"]["screen"]["url"], "")

    def test_orientation_only_request_does_not_search_photo(self):
        server = voice_server.VoiceServer.__new__(voice_server.VoiceServer)
        raw = {
            "dialogue": {"tts_text": "好的，已切换为竖屏模式。", "emotion": "neutral"},
            "action": {
                "screen": {"command": "keep", "orientation": "keep"},
                "audio": {"command": "keep"},
            },
        }

        with patch.object(server, "_get_best_local_ip", return_value="127.0.0.1"), \
             patch.object(server, "_search_photo_result") as search_photo, \
             patch.object(voice_server, "_set_device_view") as set_device_view:
            resolved, view_handled = server._parse_llm_json(
                json.dumps(raw, ensure_ascii=False),
                "127.0.0.1",
                user_text="表知,切换成竖屏模式。",
            )

        search_photo.assert_not_called()
        set_device_view.assert_called_once_with("", "portrait")
        self.assertTrue(view_handled)
        self.assertEqual(resolved["action"]["screen"]["command"], "keep")
        self.assertEqual(resolved["action"]["screen"]["orientation"], "keep")


if __name__ == "__main__":
    unittest.main()
