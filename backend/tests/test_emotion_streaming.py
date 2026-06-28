import unittest
from backend.services.emotion.models import EmotionLabel, EmotionSource
from backend.services.emotion.streaming import extract_user_emotion

class EmotionStreamingTests(unittest.TestCase):
    def test_complete_user_emotion_parsed_early(self):
        # A buffer where user_emotion is complete, but dialogue is still streaming
        buffer = '''{
  "user_emotion": {
    "label": "joy",
    "confidence": 0.85,
    "valence": 0.9,
    "arousal": 0.7,
    "intensity": 0.8,
    "evidence": ["speaker is laughing"]
  },
  "dialogue": {
    "tts_text": "I am so glad to hear th'''
        
        signal = extract_user_emotion(buffer)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.label, EmotionLabel.JOY)
        self.assertEqual(signal.confidence, 0.85)
        self.assertEqual(signal.source, EmotionSource.SEMANTIC)
        
    def test_incomplete_braces_returns_none(self):
        buffer = '''{
  "user_emotion": {
    "label": "joy",
    "confidence": 0'''
        
        signal = extract_user_emotion(buffer)
        self.assertIsNone(signal)
        
    def test_braces_and_escapes_inside_evidence_handled_correctly(self):
        # Even with tricky quotes and braces in strings, it should parse exactly the object.
        buffer = '''{
  "user_emotion": {
    "label": "sadness",
    "confidence": 0.8,
    "valence": -0.6,
    "evidence": ["user said \\"this is so {sad}\\""]
  },
  "dialogue": {
    "tt'''
        
        signal = extract_user_emotion(buffer)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.label, EmotionLabel.SADNESS)
        self.assertEqual(signal.evidence[0], 'user said "this is so {sad}"')
