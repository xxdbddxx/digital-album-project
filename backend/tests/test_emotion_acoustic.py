import unittest
from unittest.mock import patch, MagicMock
from backend.services.emotion.acoustic import pcm_to_wav_bytes, DashScopeAcousticEmotionClient
from backend.services.emotion.models import EmotionLabel, EmotionSource

class EmotionAcousticTests(unittest.TestCase):
    def test_wav_conversion_adds_riff_header(self):
        pcm = b'\x00\x00' * 16000  # 1 second of silence
        wav_bytes = pcm_to_wav_bytes(pcm)
        self.assertTrue(wav_bytes.startswith(b'RIFF'))
        self.assertIn(b'WAVE', wav_bytes[:12])

    @patch('backend.services.emotion.acoustic.requests.post')
    def test_analyze_fatigue_response(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        # Mocking the DashScope compatible mode response
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"label": "fatigue", "confidence": 0.85, "valence": -0.3}'
                    }
                }
            ]
        }
        mock_post.return_value = mock_resp

        client = DashScopeAcousticEmotionClient("fake_key")
        signal = client.analyze(b'\x00\x00' * 1600)
        
        self.assertEqual(signal.label, EmotionLabel.FATIGUE)
        self.assertEqual(signal.confidence, 0.85)
        self.assertEqual(signal.source, EmotionSource.ACOUSTIC)
        
        # Verify the payload audio URL format
        call_args = mock_post.call_args
        self.assertIsNotNone(call_args)
        payload = call_args[1].get('json', {})
        messages = payload.get('messages', [])
        self.assertGreater(len(messages), 0)
        content = messages[0].get('content', [])
        audio_url = None
        for item in content:
            if isinstance(item, dict) and item.get('type') == 'audio_url':
                audio_url = item.get('audio_url', {}).get('url', '')
                break
        self.assertIsNotNone(audio_url)
        self.assertTrue(audio_url.startswith("data:audio/wav;base64,"))

    @patch('backend.services.emotion.acoustic.requests.post')
    def test_http_error_returns_neutral(self, mock_post):
        mock_post.side_effect = Exception("HTTP Error")
        client = DashScopeAcousticEmotionClient("fake_key")
        signal = client.analyze(b'\x00\x00' * 1600)
        self.assertEqual(signal.label, EmotionLabel.NEUTRAL)
        self.assertEqual(signal.confidence, 0.0)

    @patch('backend.services.emotion.acoustic.requests.post')
    def test_malformed_json_returns_neutral(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("Invalid JSON")
        mock_post.return_value = mock_resp
        
        client = DashScopeAcousticEmotionClient("fake_key")
        signal = client.analyze(b'\x00\x00' * 1600)
        self.assertEqual(signal.label, EmotionLabel.NEUTRAL)
        self.assertEqual(signal.confidence, 0.0)
