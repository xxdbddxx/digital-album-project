import base64
import io
import json
import wave
import requests
from backend.services.emotion.models import EmotionLabel, EmotionSignal, EmotionSource

def pcm_to_wav_bytes(pcm_data: bytes, sample_rate: int = 16000, sample_width: int = 2, channels: int = 1) -> bytes:
    with io.BytesIO() as wav_io:
        with wave.open(wav_io, 'wb') as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(sample_width)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_data)
        return wav_io.getvalue()

class DashScopeAcousticEmotionClient:
    def __init__(self, api_key: str, model: str = "qwen3-omni-30b-a3b-captioner", endpoint: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"):
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint

    def analyze(self, pcm_data: bytes, soft_deadline_seconds: float = 0.4) -> EmotionSignal:
        try:
            wav_bytes = pcm_to_wav_bytes(pcm_data)
            wav_base64 = base64.b64encode(wav_bytes).decode('utf-8')
            audio_url = f"data:audio/wav;base64,{wav_base64}"

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            prompt = (
                "You are an acoustic emotion analysis expert. Analyze the speaker's vocal delivery (tone, pitch, rhythm, energy) "
                "rather than the factual content. Choose exactly one label from: "
                "joy, sadness, anxiety, anger, fatigue, loneliness, calm, neutral. "
                "Output MUST be exactly a JSON object with 'label' (string), 'confidence' (float 0.0-1.0), "
                "'valence' (float -1.0 to 1.0), 'arousal' (float 0.0 to 1.0), and 'intensity' (float 0.0 to 1.0). "
                "Do not include any other text or markdown."
            )

            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "audio_url", "audio_url": {"url": audio_url}}
                        ]
                    }
                ],
                "temperature": 0.0,
                "max_tokens": 180
            }

            timeout = max(2.0, soft_deadline_seconds + 1.5)

            response = requests.post(self.endpoint, headers=headers, json=payload, timeout=timeout)
            response.raise_for_status()
            
            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                return EmotionSignal.neutral(EmotionSource.ACOUSTIC)
                
            content = choices[0].get("message", {}).get("content", "")
            if isinstance(content, list):
                # Qwen might return list of contents
                texts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
                content_str = "".join(texts)
            else:
                content_str = str(content)
                
            # clean up markdown if any
            content_str = content_str.strip()
            if content_str.startswith("```json"):
                content_str = content_str[7:]
            if content_str.endswith("```"):
                content_str = content_str[:-3]
                
            result_map = json.loads(content_str)
            return EmotionSignal.from_mapping(result_map, source=EmotionSource.ACOUSTIC)
            
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Acoustic analysis failed: {e}")
            # Fail closed to neutral without logging response bodies or keys
            return EmotionSignal.neutral(EmotionSource.ACOUSTIC)
