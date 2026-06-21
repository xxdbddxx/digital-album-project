#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file voice_server.py
@brief 零延迟并发多轮对话流式引擎 (ASR -> LLM -> TTS)

@architecture
本模块依托 WebSockets 建立 ESP32 与服务器的全双工低延迟通道：
1. ASR 阶段：引入 WebRTC VAD 侦测与 faster-whisper 实现不等截断直接转录。
2. LLM 阶段：支持 DeepSeek API / Ollama 流式生成，实现 Token 级别逐字下发。
3. TTS 阶段：边缘计算与云端(edge-tts -> piper-tts)双模回退，分块 (Chunked) 发送 PCM 结合自然反压，避免设备 OOM 且支持随时的硬核打断。
"""

import sys
from unittest.mock import MagicMock
# Python 3.13 移除了 audioop 模块，pydub 依赖它，mock 掉避免崩溃
sys.modules['audioop'] = MagicMock()
sys.modules['pyaudioop'] = MagicMock()
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='ignore')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='ignore')

import os
import json
import wave
import asyncio
import re
import shutil
import concurrent.futures
import subprocess
import websockets
import time
import socket
import random
import threading
from functools import lru_cache

def _load_local_env():
    """Load backend/.env.local without overwriting explicit shell variables."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as env_file:
        env_lines = env_file.read().splitlines()
    for raw_line in env_lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)

_load_local_env()

# 调试开关：仅显式设置 VOICE_ECHO_TEST=1 时启用，正常运行默认关闭。
ECHO_TEST_MODE = os.environ.get("VOICE_ECHO_TEST", "0").strip().lower() in {
    "1", "true", "yes", "on"
}
from datetime import datetime
from pathlib import Path

_NVIDIA_DLL_HANDLES = []

def _configure_nvidia_dll_paths():
    """Expose NVIDIA pip-package DLLs to CTranslate2 on Windows."""
    if os.name != "nt":
        return

    site_packages = Path(sys.prefix) / "Lib" / "site-packages"
    dll_dirs = (
        site_packages / "nvidia" / "cublas" / "bin",
        site_packages / "nvidia" / "cudnn" / "bin",
    )
    existing = [str(path) for path in dll_dirs if path.is_dir()]
    if not existing:
        return

    os.environ["PATH"] = os.pathsep.join(existing + [os.environ.get("PATH", "")])
    if hasattr(os, "add_dll_directory"):
        for dll_dir in existing:
            _NVIDIA_DLL_HANDLES.append(os.add_dll_directory(dll_dir))

_configure_nvidia_dll_paths()

SAMPLE_RATE = 16000
CHANNELS = 1
BIT_DEPTH = 16
BYTES_PER_SAMPLE = 2

WS_HOST = "0.0.0.0"
WS_PORT = 8888
ROOT_DIR = os.path.dirname(__file__)

# ── 本地疗愈曲库映射 ──────────────────────────────────────────────
AUDIO_ROUTING_MAP = {
    "insomnia":   [f"rain_{i}.mp3" for i in range(1, 8)],
    "anxiety":    [f"handpan_relax_{i}.mp3" for i in range(1, 6)],
    "meditation": [f"singing_bowl_{i}.mp3" for i in range(1, 6)],
    "sad":        [f"ambient_piano_{i}.mp3" for i in range(1, 6)],
    "tired":      ["campfire_1.mp3", "campfire_2.mp3"],
    "relax":      [f"forest_stream_{i}.mp3" for i in range(1, 5)],
    "happy":      [f"happy_pop_{i}.mp3" for i in range(1, 4)],
    "ocean":      [f"ocean_waves_{i}.mp3" for i in range(1, 4)],
    "winter":     ["winter_wind_1.mp3", "winter_wind_2.mp3"],
}

MUSIC_KEYWORD_MAP = {
    "雨声": "insomnia", "下雨": "insomnia", "助眠": "insomnia",
    "手碟": "anxiety", "放松": "anxiety", "焦虑": "anxiety",
    "颂钵": "meditation", "冥想": "meditation", "禅": "meditation",
    "钢琴": "sad", "轻音乐": "sad",
    "篝火": "tired", "火焰": "tired",
    "森林": "relax", "溪流": "relax", "自然": "relax",
    "欢快": "happy", "开心": "happy", "流行": "happy",
    "海浪": "ocean", "海洋": "ocean", "大海": "ocean",
    "冬风": "winter", "寒风": "winter", "冬天": "winter",
}

SCENE_MAP = {
    "海边": {"audio_keyword": "ocean", "mist_level": 1, "mist_channel": "none"},
    "大海": {"audio_keyword": "ocean", "mist_level": 1, "mist_channel": "none"},
    "雪景": {"audio_keyword": "winter", "mist_level": 1, "mist_channel": "mint"},
    "森林": {"audio_keyword": "relax",  "mist_level": 2, "mist_channel": "mint"},
    "篝火": {"audio_keyword": "tired",  "mist_level": 1, "mist_channel": "rose"},
}

def _decode_legacy_music_tag(value) -> str:
    """Repair old GBK ID3 tags that FFmpeg exposes as Latin-1 text."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        repaired = text.encode("latin1").decode("gbk")
        if any("\u4e00" <= char <= "\u9fff" for char in repaired):
            return repaired
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return text

@lru_cache(maxsize=1)
def _music_catalog() -> list[dict]:
    music_dir = Path(ROOT_DIR).parent / "music"
    catalog = []
    for path in sorted(music_dir.glob("*.mp3")):
        metadata = {}
        try:
            import av
            container = av.open(str(path))
            metadata = {
                str(key).lower(): _decode_legacy_music_tag(value)
                for key, value in dict(container.metadata).items()
            }
            container.close()
        except Exception as exc:
            print(f"Music metadata unavailable for {path.name}: {exc}")

        catalog.append({
            "filename": path.name,
            "stem": path.stem,
            "title": metadata.get("title", ""),
            "artist": metadata.get("artist", ""),
            "album": metadata.get("album", ""),
        })
    return catalog

def _available_music_files() -> list[str]:
    return [item["filename"] for item in _music_catalog()]

def _select_music_file(query: str, user_text: str = "") -> str:
    """Resolve a model/user music request to one local MP3 file."""
    catalog = _music_catalog()
    if not catalog:
        return ""

    combined = f"{query or ''} {user_text or ''}".strip().lower()
    normalized = re.sub(r"\.(mp3|pcm)\b", "", combined)
    compact = re.sub(r"[\s_-]+", "", normalized)

    # Match filename, ID3 title, artist, and title+artist before categories.
    best_filename = ""
    best_score = 0
    for item in catalog:
        filename = item["filename"]
        stem = item["stem"].lower()
        title = item["title"].lower()
        artist = item["artist"].lower()
        score = 0
        if title and title in normalized:
            score += 100
        if artist and artist in normalized:
            score += 40
        if title and artist and title in normalized and artist in normalized:
            score += 30
        if stem in normalized or re.sub(r"[\s_-]+", "", stem) in compact:
            score += 80
        if score > best_score:
            best_score = score
            best_filename = filename
    if best_filename:
        return best_filename

    for keyword, category in MUSIC_KEYWORD_MAP.items():
        if keyword in combined:
            choices = [
                name for name in AUDIO_ROUTING_MAP.get(category, [])
                if any(item["filename"] == name for item in catalog)
            ]
            if choices:
                return random.choice(choices)

    for category, choices in AUDIO_ROUTING_MAP.items():
        if category in combined:
            existing = [
                name for name in choices
                if any(item["filename"] == name for item in catalog)
            ]
            if existing:
                return random.choice(existing)

    random_words = ("随机", "随便", "任意", "来一首", "放点音乐", "播放音乐")
    if any(word in combined for word in random_words):
        return random.choice([item["filename"] for item in catalog])

    # A specific title that is not in the library must not become a random song.
    return ""

# ── LLM / 模型配置 ───────────────────────────────────────────────
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "deepseek").strip().lower()
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.environ.get(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
).rstrip("/")
DEEPSEEK_MODEL = os.environ.get(
    "DEEPSEEK_MODEL", "deepseek-v4-flash"
).strip()
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
WHISPER_CPU_FALLBACK_MODEL = os.environ.get(
    "WHISPER_CPU_FALLBACK_MODEL", "medium"
)
WHISPER_CPU_THREADS = int(os.environ.get("WHISPER_CPU_THREADS", "4"))
WHISPER_BEAM_SIZE = int(os.environ.get("WHISPER_BEAM_SIZE", "5"))
WHISPER_INITIAL_PROMPT = os.environ.get(
    "WHISPER_INITIAL_PROMPT",
    "你好小智。电子相册控制：切换照片、上一张、下一张、随机照片、"
    "横屏、竖屏、狗子、琪露诺、我的世界小屋、落叶、播放音乐、"
    "播放雨声、播放钢琴、播放海浪、随机播放、停止音乐。",
).strip()
PIPER_MODEL = os.environ.get("PIPER_MODEL", "zh_CN-huayan-medium.onnx")
TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "aliyun").strip().lower()
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "").strip()
ALIYUN_TTS_MODEL = os.environ.get(
    "ALIYUN_TTS_MODEL", "cosyvoice-v3-flash"
).strip()
ALIYUN_TTS_VOICE = os.environ.get(
    "ALIYUN_TTS_VOICE", "longanhuan_v3"
).strip()
ALIYUN_TTS_VOLUME = int(os.environ.get("ALIYUN_TTS_VOLUME", "60"))
MUSIC_PLAYBACK_VOLUME = int(os.environ.get("MUSIC_PLAYBACK_VOLUME", "60"))
ALIYUN_TTS_SPEECH_RATE = float(
    os.environ.get("ALIYUN_TTS_SPEECH_RATE", "1.0")
)
# 静音阈值 (秒) — VAD 检测到持续静音即触发 ASR
SILENCE_TRIGGER_SEC = float(os.environ.get("SILENCE_TRIGGER", "0.6"))

# Flask server 内部回调地址（用于 voice_server 将 LLM 回复推送给 Web UI）
_FLASK_CALLBACK_URL = os.environ.get("FLASK_CALLBACK_URL", "http://127.0.0.1:8765/api/internal/llm_reply")
_FLASK_ASR_CALLBACK_URL = os.environ.get(
    "FLASK_ASR_CALLBACK_URL",
    "http://127.0.0.1:8765/api/internal/asr_text",
)

def _post_llm_reply_to_flask(reply_text: str):
    """通过 HTTP 将 LLM 回复内容回传给 Flask，再由 Flask 通过 SSE 推送给前端。"""
    if not reply_text:
        return
    try:
        import urllib.request
        payload = json.dumps({"reply": reply_text}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            _FLASK_CALLBACK_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"⚠️  [voice_server] 回调 Flask 失败: {e}")

def _post_asr_text_to_flask(user_text: str):
    """将物理麦克风的 ASR 结果写入网页历史并通过 SSE 推送。"""
    if not user_text:
        return
    try:
        import urllib.request
        payload = json.dumps(
            {"text": user_text}, ensure_ascii=False
        ).encode("utf-8")
        req = urllib.request.Request(
            _FLASK_ASR_CALLBACK_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"⚠️  [voice_server] ASR 回调 Flask 失败: {e}")

def _extract_tts_text(llm_text: str) -> str:
    if not llm_text:
        return ""
    try:
        parsed = json.loads(llm_text)
        return str((parsed.get("dialogue") or {}).get("tts_text") or llm_text)
    except Exception:
        pass
    try:
        match = re.search(
            r'"tts_text"\s*:\s*"((?:[^"\\]|\\.)*?)"', llm_text
        )
        if match:
            return json.loads('"' + match.group(1) + '"')
    except Exception:
        pass
    return llm_text

def _extract_photo_search_query(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    photo_words = ("照片", "图片", "相片", "相册")
    action_words = (
        "切换", "更换", "换成", "换到", "显示",
        "看看", "看一下", "找", "播放", "打开",
    )
    if not any(word in value for word in photo_words):
        return ""
    if not any(word in value for word in action_words):
        return ""

    query = value
    for phrase in (
        "并切换成竖屏", "并切换成横屏", "并改成竖屏", "并改成横屏",
        "同时切换成竖屏", "同时切换成横屏",
        "竖屏显示", "横屏显示", "竖屏", "横屏",
    ):
        query = query.replace(phrase, " ")
    for phrase in (
        "帮我切换成", "帮我切换到", "切换成", "切换到",
        "帮我更换成", "更换成",
        "帮我换成", "换成", "帮我换到", "换到",
        "帮我显示", "显示一下", "给我看看",
        "帮我找一张", "帮我找", "找一张", "打开", "播放",
        "切换", "更换", "显示", "看看", "看一下", "找",
    ):
        query = query.replace(phrase, " ")
    for word in (
        "这张", "那张", "对应的", "相关的", "照片", "图片", "相片", "相册",
        "帮我", "请你", "给我", "把",
    ):
        query = query.replace(word, " ")
    for word in ("随机切换", "随机更换", "随机播放", "随机", "随便", "任意"):
        query = query.replace(word, " ")
    query = re.sub(r"[，。！？,.!?]", " ", query)
    query = re.sub(r"\s+", " ", query).strip()
    query = re.sub(r"(?:的|一下|给我)$", "", query).strip()
    return query

def _is_random_photo_request(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return False
    has_random_word = any(word in value for word in ("随机", "随便", "任意"))
    has_photo_word = any(word in value for word in ("照片", "图片", "相片", "相册"))
    has_action_word = any(
        word in value for word in ("切换", "更换", "换", "显示", "看看", "播放")
    )
    return has_random_word and has_photo_word and has_action_word

def _semantic_photo_search(query: str) -> dict:
    import urllib.parse
    import urllib.request

    params = urllib.parse.urlencode({"q": query, "limit": 1})
    url = f"http://127.0.0.1:8765/api/photo/semantic-search?{params}"
    with urllib.request.urlopen(url, timeout=35) as response:
        result = json.loads(response.read().decode("utf-8"))
    matches = result.get("results") or []
    return matches[0] if matches else {}

def _extract_orientation_command(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    control_words = (
        "切换", "更换", "换成", "换到", "改成",
        "改为", "设置", "调整", "显示", "恢复",
    )
    if not any(word in value for word in control_words):
        return ""
    if any(word in value for word in ("竖屏", "竖向", "纵向", "竖着")):
        return "portrait"
    if any(word in value for word in ("横屏", "横向", "水平", "横着")):
        return "landscape"
    return ""

def _set_device_view(target_id: str = "", orientation: str = "keep") -> None:
    import urllib.request

    payload = json.dumps({
        "cmd": "set_view",
        "target_id": target_id,
        "orientation": orientation,
    }).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:8765/api/device/control",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError("Flask 未接受屏幕视图指令")

def _extract_fallback_photo_query(text: str, orientation: str = "") -> str:
    query = _extract_photo_search_query(text)
    if query or not orientation:
        return query

    value = (text or "").strip()
    for phrase in (
        "竖屏显示", "横屏显示", "竖屏", "横屏",
        "竖向", "横向", "纵向", "水平", "竖着", "横着",
        "帮我", "请你", "给我", "把", "同时", "一起", "并且", "然后",
        "切换成", "切换到", "切换", "更换成", "更换", "换成", "换到",
        "改成", "改为", "设置成", "设置", "调整为", "调整", "显示",
    ):
        value = value.replace(phrase, " ")
    value = re.sub(r"[，。！？,.!?]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value

def _load_system_prompt():
    prompt_path = os.path.join(ROOT_DIR, "voice_system_prompt.md")
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    return "You are a helpful assistant."


class VoiceServer:

    def __init__(self):
        self.output_dir = os.path.join(ROOT_DIR, "user_records")
        self.response_dir = os.path.join(ROOT_DIR, "response_records")
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.response_dir, exist_ok=True)

        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        self._model_lock = threading.Lock()

        # ── 多轮对话会话历史 ──
        self.sessions = {}          # {client_ip: [{"role":"user"|"assistant","content":...}, ...]}
        self.MAX_HISTORY_TURNS = 50 # 最多保留 50 轮对话（100 条消息）
        self.active_sessions = []   # 用于存储所有活跃连接的信息，以便内部 RPC 调用

        # ── LLM ──
        self.ollama_ok = (
            self._check_ollama()
            if LLM_PROVIDER == "ollama"
            else False
        )
        self.deepseek_ok = bool(DEEPSEEK_API_KEY)
        if LLM_PROVIDER == "deepseek" and self.deepseek_ok:
            print(f"✅ DeepSeek API: {DEEPSEEK_MODEL} @ {DEEPSEEK_BASE_URL}")
        elif LLM_PROVIDER == "deepseek":
            print("⚠️  DeepSeek API 不可用 — 请设置 DEEPSEEK_API_KEY")
        elif LLM_PROVIDER == "ollama" and self.ollama_ok:
            print(f"✅ Ollama: {OLLAMA_MODEL} @ {OLLAMA_HOST}")
        elif LLM_PROVIDER == "ollama":
            print(f"⚠️  Ollama 不可用 — 请确保已启动且 {OLLAMA_MODEL} 已拉取")
        else:
            print(
                f"⚠️  未知 LLM_PROVIDER={LLM_PROVIDER!r}，"
                "可选 deepseek 或 ollama"
            )

        # ── faster-whisper ──
        self._whisper_model = None
        try:
            import ctranslate2
            from faster_whisper import WhisperModel
            cuda_available = ctranslate2.get_cuda_device_count() > 0
            if cuda_available and os.name == "nt":
                try:
                    import ctypes
                    ctypes.WinDLL("cublas64_12.dll")
                    ctypes.WinDLL("cudnn64_9.dll")
                except OSError:
                    cuda_available = False
                    print(
                        "⚠️  检测到 NVIDIA GPU，但缺少 CUDA 12 cuBLAS/cuDNN 9；"
                        f"本次使用 CPU {WHISPER_CPU_FALLBACK_MODEL}"
                    )
            if cuda_available:
                try:
                    self._whisper_model = WhisperModel(
                        WHISPER_MODEL,
                        device="cuda",
                        compute_type="float16",
                    )
                    self._whisper_runtime = (
                        WHISPER_MODEL, "cuda", "float16"
                    )
                except Exception as cuda_error:
                    print(f"⚠️  Whisper CUDA 加载失败，回退 CPU: {cuda_error}")

            if self._whisper_model is None:
                self._whisper_model = WhisperModel(
                    WHISPER_CPU_FALLBACK_MODEL,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=WHISPER_CPU_THREADS,
                )
                self._whisper_runtime = (
                    WHISPER_CPU_FALLBACK_MODEL, "cpu", "int8"
                )
            self.whisper_ok = True
            runtime_model, runtime_device, runtime_compute = self._whisper_runtime
            print(
                f"✅ faster-whisper: {runtime_model} "
                f"({runtime_device}/{runtime_compute}, beam: {WHISPER_BEAM_SIZE})"
            )
        except ImportError:
            self.whisper_ok = False
            print("⚠️  faster-whisper 未安装")
        except Exception as e:
            self.whisper_ok = False
            print(f"⚠️  whisper 加载失败: {e}")

        # ── VAD ──
        self._vad = None
        self._vad_aggressiveness = int(os.environ.get("VAD_AGGRESSIVENESS", "2"))
        try:
            import webrtcvad
            self._vad = webrtcvad.Vad(self._vad_aggressiveness)
            self.vad_ok = True
            print(f"✅ VAD: webrtcvad (aggressiveness={self._vad_aggressiveness})")
        except ImportError:
            self.vad_ok = False
            print("⚠️  webrtcvad 未安装，使用能量检测回退")

        # ── TTS 优先级: 阿里云 CosyVoice → edge-tts → piper ──
        self.aliyun_tts_ok = self._check_aliyun_tts()
        self.edge_tts_ok = self._check_edge_tts()
        self.piper_ok = self._check_piper()
        if self.aliyun_tts_ok:
            print(
                f"✅ 阿里云 CosyVoice: {ALIYUN_TTS_MODEL} / "
                f"{ALIYUN_TTS_VOICE}"
            )
        elif TTS_PROVIDER == "aliyun":
            print("⚠️  阿里云 CosyVoice 不可用，将使用本地 TTS 兜底")
        if self.edge_tts_ok:
            print("✅ edge-tts: zh-CN-XiaoxiaoNeural (兜底)")
        if self.piper_ok:
            print(f"✅ piper-tts: {PIPER_MODEL} (兜底)")
        if not self.edge_tts_ok and not self.piper_ok:
            print("⚠️  没有可用的 TTS 方案")

    class _NoopWebSocket:
        async def send(self, _message):
            return None

    def _check_ollama(self):
        try:
            import urllib.request
            req = urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=5)
            req.read()
            return True
        except Exception:
            return False

    def _check_edge_tts(self):
        try:
            import edge_tts  # noqa: F401
            return True
        except Exception:
            return False

    def _check_aliyun_tts(self):
        if TTS_PROVIDER != "aliyun" or not DASHSCOPE_API_KEY:
            return False
        try:
            import dashscope  # noqa: F401
            from dashscope.audio.tts_v2 import SpeechSynthesizer  # noqa: F401
            return True
        except Exception as exc:
            print(f"⚠️  DashScope SDK 不可用: {exc}")
            return False

    def _check_piper(self):
        try:
            import piper  # noqa: F401
            model_path = os.path.join(ROOT_DIR, PIPER_MODEL)
            if os.path.exists(model_path):
                return True
            print(f"⚠️  piper 模型未找到: {model_path}")
            return False
        except Exception:
            return False

    # ════════════════════════════════════════════════════════════════
    # WebSocket 客户端处理（每个连接一个协程）
    # ════════════════════════════════════════════════════════════════

    async def handle_client(self, websocket, path=None):
        client_ip = websocket.remote_address[0]
        print(f"\n🔗 连接: {client_ip}")

        # 增大 Windows TCP 接收缓冲区，防止反压到 ESP32 的 SND_BUF
        try:
            sock = websocket.transport.get_extra_info('socket')
            if sock is not None:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 131072)
        except Exception:
            pass

        st = {
            "is_recording": False,
            "audio_buffer": bytearray(),
            "telemetry_data": {},
            "last_speech_time": 0.0,      # 上次检测到语音的时间
            "asr_running": False,          # ASR 是否正在后台运行
            "pipeline_running": False,     # 完整管线(ASR+LLM+TTS)是否在运行
            "llm_streaming": False,        # LLM 流式生成中
            "cancel_requested": False,     # 打断标志
        }

        session_info = {"websocket": websocket, "st": st, "client_ip": client_ip}
        if client_ip != "127.0.0.1":
            self.active_sessions.append(session_info)

        try:
            async for message in websocket:
                try:
                    if isinstance(message, bytes):
                        if st["is_recording"]:
                            st["audio_buffer"].extend(message)
                            # 如果是 Echo 模式，无需在 Python 端做 VAD，直接更新时间戳，隔离 C 库 webrtcvad 潜在的崩溃隐患
                            if ECHO_TEST_MODE:
                                st["last_speech_time"] = time.time()
                            else:
                                if self._has_speech(message):
                                    st["last_speech_time"] = time.time()
                            # 静音超过阈值 + 缓冲够长 + 管线空闲 → 触发流式识别
                            buf_sec = len(st["audio_buffer"]) / BYTES_PER_SAMPLE / SAMPLE_RATE
                            silence_dur = time.time() - st["last_speech_time"]
                            if (not st["pipeline_running"] and buf_sec > 1.5
                                    and silence_dur > SILENCE_TRIGGER_SEC):
                                st["pipeline_running"] = True
                                st["asr_running"] = True
                                audio_copy = bytes(st["audio_buffer"])
                                # 重置状态，防止紧接着的静音切片引发重复触发
                                st["audio_buffer"] = bytearray()
                                st["last_speech_time"] = time.time()
                                asyncio.create_task(
                                    self._streaming_pipeline(audio_copy, websocket, st, client_ip)
                                )
                        await asyncio.sleep(0)  # 每帧后让出事件循环，确保 Windows select() 立即注册下一轮 I/O
                        continue

                    data = json.loads(message)
                    event = data.get("event")

                    if event == "flask_rpc":
                        rpc_cmd = data.get("cmd")
                        rpc_text = data.get("text", "")
                        print(f"🤖 [RPC] 收到 Flask 内部调用: {rpc_cmd} -> {rpc_text}")
                        if rpc_cmd == "simulate_text":
                            if rpc_text == "[CANCEL]":
                                continue
                            rpc_st = {
                                "is_recording": False,
                                "audio_buffer": bytearray(),
                                "telemetry_data": {},
                                "last_speech_time": 0.0,
                                "asr_running": False,
                                "pipeline_running": True,
                                "llm_streaming": False,
                                "tts_generating": False,
                                "cancel_requested": False,
                                "is_web_simulated": True,
                            }
                            asyncio.create_task(
                                self._streaming_pipeline(
                                    None,
                                    self._NoopWebSocket(),
                                    rpc_st,
                                    "web",
                                    text_override=rpc_text,
                                )
                            )
                        continue

                    if event == "telemetry":
                        st["telemetry_data"] = data

                    elif event == "privacy_mode":
                        await websocket.send(json.dumps({
                            "action": {"mist": False, "brightness": 0}
                        }))

                    elif event == "privacy_mode_exit":
                        await websocket.send(json.dumps({
                            "action": {"brightness": 100}
                        }))
                        
                    elif event == "simulate_text":
                        sim_text = data.get("text", "")
                        print(f"🤖 [{client_ip}] 收到模拟文本输入: {sim_text}")
                        st["is_recording"] = False
                        st["audio_buffer"] = bytearray()
                        st["cancel_requested"] = True # 打断当前
                        st["pipeline_running"] = True
                        st["asr_running"] = False
                        st["is_web_simulated"] = True
                        asyncio.create_task(
                            self._streaming_pipeline(None, websocket, st, client_ip, text_override=sim_text)
                        )

                    elif event == "wake_word_detected":
                        print(f"🎉 [{client_ip}] 唤醒词")
                        st["cancel_requested"] = True # 💡 唤醒时立即标记打断上一轮
                        st["is_web_simulated"] = False
                        self.sessions.pop(client_ip, None)  # 新会话：清除历史

                    elif event == "recording_started":
                        print(f"🎤 [{client_ip}] 录音开始")
                        st["cancel_requested"] = True # 💡 强唤醒/录音开始双重打断保障
                        st["is_recording"] = True
                        st["audio_buffer"] = bytearray()
                        st["last_speech_time"] = time.time()
                        st["asr_running"] = False
                        st["pipeline_running"] = False

                    elif event == "recording_ended":
                        print(f"✅ [{client_ip}] 录音结束")
                        st["is_recording"] = False
                        if len(st["audio_buffer"]) == 0:
                            continue

                        buf_sec = len(st["audio_buffer"]) / BYTES_PER_SAMPLE / SAMPLE_RATE
                        print(f"📊 [{client_ip}] {len(st['audio_buffer'])} 字节 ({buf_sec:.2f}s)")

                        # 保存音频（后台线程）
                        audio_copy = bytes(st["audio_buffer"])
                        self._executor.submit(self._save_audio, audio_copy)

                        # 若管线已在运行（流式 ASR 已触发），不再重复处理
                        if st.get("pipeline_running") or st.get("llm_streaming") or st.get("tts_generating"):
                            continue

                        # 最终一次性处理（流式未触发时的回退路径）
                        await self._streaming_pipeline(audio_copy, websocket, st, client_ip)

                    elif event == "recording_cancelled":
                        print(f"⚠️ [{client_ip}] 录音取消")
                        st["is_recording"] = False
                        st["audio_buffer"] = bytearray()
                        st["cancel_requested"] = True

                    elif event == "session_end":
                        print(f"🔇 [{client_ip}] 会话结束")
                        self.sessions.pop(client_ip, None)

                except json.JSONDecodeError:
                    pass
                except Exception as e:
                    print(f"❌ [{client_ip}] 错误: {e}")
                    import traceback
                    traceback.print_exc()

        except websockets.exceptions.ConnectionClosed:
            print(f"🔌 [{client_ip}] 连接断开 (code={websocket.close_code}, reason={websocket.close_reason})")
        except Exception as e:
            print(f"❌ [{client_ip}] 异常: {e}")
        finally:
            if session_info in self.active_sessions:
                self.active_sessions.remove(session_info)
            st["is_recording"] = False
            st["pipeline_running"] = False
            st["asr_running"] = False
            st["llm_streaming"] = False
            st["tts_generating"] = False
            try:
                await websocket.close()
            except Exception:
                pass
            print(f"🔌 [{client_ip}] 资源已清理")

    def _append_history(self, client_ip, role, content):
        """追加一条消息到会话历史，限制最多 MAX_HISTORY_TURNS 轮。"""
        if not content:
            return
        sess = self.sessions.setdefault(client_ip, [])
        sess.append({"role": role, "content": content})
        if len(sess) > self.MAX_HISTORY_TURNS * 2:
            self.sessions[client_ip] = sess[-(self.MAX_HISTORY_TURNS * 2):]

    # ════════════════════════════════════════════════════════════════
    # 流式管线: ASR → LLM (stream) → TTS (piper) → ESP32
    # ════════════════════════════════════════════════════════════════

    async def _streaming_pipeline(self, pcm_data, websocket, st, client_ip, text_override=None):
        """在后台线程执行重计算，结果通过 WebSocket 流式推送。"""
        st["pipeline_running"] = True
        st["asr_running"] = True
        st["cancel_requested"] = False # 💡 开启新一轮 Pipeline，在此重置打断状态
        try:
            loop = asyncio.get_running_loop()

            if ECHO_TEST_MODE:
                print(f"🤖 [{client_ip}] Echo 模式触发：跳过 ASR/LLM/TTS，直接返回测试回复")
                # 1. 发送 ASR 文本
                await websocket.send(json.dumps({"event": "asr_final", "text": "Echo OK"}))
                
                # 2. 发送大模型 dialogue 控制帧
                dialogue_msg = {
                    "dialogue": {
                        "tts_text": "Echo OK",
                        "emotion": "neutral"
                    },
                    "action": {
                        "mist": {"command": "keep"},
                        "audio": {"command": "keep"},
                        "screen": {"command": "keep"}
                    }
                }
                await websocket.send(json.dumps(dialogue_msg, ensure_ascii=False))
                
                # 3. 发送 tts_start 音频头（16000Hz）
                await websocket.send(json.dumps({
                    "type": "tts_start",
                    "rate": 16000,
                    "format": "pcm"
                }))
                
                # 4. 读取并流式推送 test_16k_16b.pcm 音频文件
                pcm_path = os.path.join(ROOT_DIR, "test_16k_16b.pcm")
                
                # 若测试 pcm 文件缺失，自动生成一个 3 秒的 1kHz 纯正弦波测试 PCM 文件
                if not os.path.exists(pcm_path):
                    print(f"Creating fake test pcm: {pcm_path}")
                    import math, struct
                    with open(pcm_path, "wb") as pf:
                        for t in range(int(16000 * 3)): # 3秒长度
                            # 1000Hz正弦波，30%最大振幅
                            val = int(32767 * 0.3 * math.sin(2.0 * math.pi * 1000.0 * t / 16000))
                            pf.write(struct.pack("<h", val))
                            
                if os.path.exists(pcm_path):
                    with open(pcm_path, "rb") as f:
                        while chunk := f.read(3200):
                            if st.get("cancel_requested"):
                                print(f"⚠️ [{client_ip}] Echo 发送被打断，退出。")
                                break
                            await websocket.send(chunk)
                            await asyncio.sleep(0.09) # 稍微比 100ms 快一点点，防止 buffer 饿死
                else:
                    # 回退防挂起方案：若生成写入依旧受限，回退流式发送 1 秒的静音包
                    for _ in range(10):
                        await websocket.send(b'\x00' * 3200)
                        await asyncio.sleep(0.09)
                    
                # 5. 发送 ping 结束包
                await websocket.send("ping")
                print(f"✅ [{client_ip}] Echo 回复包推送完毕。")
                return

            # ── 阶段 1: ASR（线程池）──
            if text_override:
                transcribed = text_override
                st["asr_running"] = False
            else:
                transcribed = await asyncio.to_thread(self._transcribe, pcm_data)

            if not transcribed or st.get("cancel_requested"):
                print(f"⚠️ [{client_ip}] ASR 无结果")
                await websocket.send(json.dumps({"event": "asr_failed"}))
                return
            print(f"🗣️ [{client_ip}] ASR/文本: {transcribed}")
            await websocket.send(json.dumps({"event": "asr_final", "text": transcribed}))

            # ── 多轮对话：记录用户说的话 ──
            self._append_history(client_ip, "user", transcribed)
            if not text_override:
                await asyncio.to_thread(_post_asr_text_to_flask, transcribed)

            # ── 阶段 2: LLM streaming（线程池）与 TTS 并发 ──
            st["llm_streaming"] = True
            
            tts_queue = asyncio.Queue()
            pcm_queue = asyncio.Queue()
            tts_state = {"queued": 0}
            
            async def tts_consumer():
                st["tts_generating"] = True
                display_text = ""
                while True:
                    sentence = await tts_queue.get()
                    if sentence is None:
                        await pcm_queue.put(None)
                        break
                    if sentence.strip() and not st.get("cancel_requested"):
                        display_text += sentence
                        await websocket.send(json.dumps({
                            "event": "assistant_reply",
                            "text": display_text,
                            "emotion": "neutral",
                        }, ensure_ascii=False))
                        pcm_res = await asyncio.to_thread(
                            self._generate_tts_audio_only, sentence, client_ip
                        )
                        if pcm_res:
                            await pcm_queue.put(pcm_res)
                    tts_queue.task_done()
                st["tts_generating"] = False

            async def pcm_sender():
                while True:
                    pcm_res = await pcm_queue.get()
                    if pcm_res is None:
                        break
                    if not st.get("cancel_requested"):
                        await self._stream_pcm_data_async(
                            pcm_res, websocket, client_ip, st
                        )
                    pcm_queue.task_done()

            tts_task = asyncio.create_task(tts_consumer())
            pcm_task = asyncio.create_task(pcm_sender())

            full_text = await asyncio.to_thread(
                self._call_llm_stream, transcribed, st, websocket, loop,
                client_ip, tts_queue, tts_state
            )
            st["llm_streaming"] = False

            if full_text and tts_state["queued"] == 0:
                fallback_tts = _extract_tts_text(full_text).strip()
                if fallback_tts:
                    print(f"🔊 [{client_ip}] 流式断句未入队，使用完整 TTS 文本兜底")
                    await tts_queue.put(fallback_tts)
                    tts_state["queued"] += 1

            await tts_queue.put(None)
            await tts_task
            await pcm_task
            
            # 所有 TTS 断句全部发送完毕后，才发送 ping 通知设备可以结束播音并重启 VAD
            if not st.get("cancel_requested"):
                await websocket.send("ping")
            # ── 多轮对话：记录 AI 的回复 ──
            self._append_history(client_ip, "assistant", full_text)

            # 所有来源的 LLM 回复都回传给 Flask/SSE。
            if full_text:
                display_text = _extract_tts_text(full_text)
                asyncio.create_task(
                    asyncio.to_thread(_post_llm_reply_to_flask, display_text)
                )

            if st.get("cancel_requested"):
                return

            # ── 阶段 3: 解析 CJSON ──
            parsed = self._parse_llm_json(
                full_text or transcribed, client_ip, transcribed
            )
            cjson, _view_handled = parsed if parsed else (None, False)
            if cjson:
                print(f"   📤 CJSON: {json.dumps(cjson, ensure_ascii=False)}")
                await websocket.send(json.dumps(cjson, ensure_ascii=False))
            else:
                await asyncio.to_thread(
                    self._apply_keyword_view_fallback, transcribed, client_ip
                )
                control = self._parse_intent(full_text or transcribed, client_ip)
                if control:
                    await websocket.send(json.dumps(control))

        except Exception as e:
            print(f"❌ [{client_ip}] _streaming_pipeline 运行崩溃: {e}")
            import traceback
            traceback.print_exc()
        finally:
            st["pipeline_running"] = False
            st["asr_running"] = False
            st["llm_streaming"] = False
            st["tts_generating"] = False

    # ════════════════════════════════════════════════════════════════
    # VAD 语音活动检测
    # ════════════════════════════════════════════════════════════════

    def _has_speech(self, pcm_chunk):
        """检测 PCM 块是否含语音活动（webrtcvad 或能量回退）。"""
        has_voice = False
        if self._vad is not None:
            try:
                # webrtcvad 要求 16kHz 16bit mono PCM，帧长 10/20/30ms
                frame_ms = 30
                frame_bytes = int(SAMPLE_RATE * frame_ms / 1000) * BYTES_PER_SAMPLE
                for i in range(0, len(pcm_chunk) - frame_bytes + 1, frame_bytes):
                    if self._vad.is_speech(pcm_chunk[i:i + frame_bytes], SAMPLE_RATE):
                        has_voice = True
                        break
            except Exception:
                pass
        
        # 💡 逻辑规整：若 webrtcvad 未安装或报错，则走能量检测回退
        if not has_voice and len(pcm_chunk) >= 2:
            try:
                import struct
                samples = struct.unpack(f"<{len(pcm_chunk)//2}h", pcm_chunk)
                rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
                has_voice = rms > 500  # 静音阈值
            except Exception:
                pass

        # 💡 可视化控制台打印（同一行输出）
        if has_voice:
            print("█", end="", flush=True) # 有声音
        else:
            print("_", end="", flush=True) # 静音
            
        return has_voice

    # ════════════════════════════════════════════════════════════════
    # 阶段 1: faster-whisper ASR（同步 → 线程池调用）
    # ════════════════════════════════════════════════════════════════

    def _transcribe(self, pcm_data):
        if not self.whisper_ok or not self._whisper_model:
            return None
        try:
            import numpy as np
            audio_array = np.frombuffer(pcm_data, dtype=np.int16)
            if len(audio_array) > 0:
                max_val = np.max(np.abs(audio_array))
                print(f"📊 ASR 接收到音频大小: {len(pcm_data)} 字节, 最大振幅 (底噪/说话强度): {max_val}")
            else:
                print("📊 ASR 接收到音频大小为 0 字节")

            audio_float = audio_array.astype(np.float32) / 32768.0
            if audio_float.size:
                audio_float -= float(np.mean(audio_float))
                peak = float(np.max(np.abs(audio_float)))
                if 0.01 < peak < 0.85:
                    audio_float *= 0.85 / peak

            def run_model():
                segments, _ = self._whisper_model.transcribe(
                    audio_float,
                    language="zh",
                    beam_size=WHISPER_BEAM_SIZE,
                    initial_prompt=WHISPER_INITIAL_PROMPT,
                    condition_on_previous_text=False,
                    temperature=0.0,
                    no_speech_threshold=0.55,
                    log_prob_threshold=-1.0,
                    compression_ratio_threshold=2.4,
                    vad_filter=True,
                    vad_parameters=dict(
                        threshold=0.5,
                        min_speech_duration_ms=250,
                        min_silence_duration_ms=500,
                    ),
                )
                return " ".join(segment.text for segment in segments).strip()

            with self._model_lock:
                try:
                    text = run_model()
                except RuntimeError as runtime_error:
                    error_text = str(runtime_error).lower()
                    if (
                        self._whisper_runtime[1] != "cuda"
                        or ("cublas" not in error_text and "cudnn" not in error_text)
                    ):
                        raise
                    print(
                        "⚠️  CUDA Whisper 运行库不可用，"
                        f"切换 CPU {WHISPER_CPU_FALLBACK_MODEL}: {runtime_error}"
                    )
                    from faster_whisper import WhisperModel
                    self._whisper_model = WhisperModel(
                        WHISPER_CPU_FALLBACK_MODEL,
                        device="cpu",
                        compute_type="int8",
                        cpu_threads=WHISPER_CPU_THREADS,
                    )
                    self._whisper_runtime = (
                        WHISPER_CPU_FALLBACK_MODEL, "cpu", "int8"
                    )
                    text = run_model()
            return text if text else None
        except Exception as e:
            print(f"❌ ASR: {e}")
            return None

    # ════════════════════════════════════════════════════════════════
    # 阶段 2: LLM 流式生成（同步 → 线程池调用）
    # ════════════════════════════════════════════════════════════════

    def _call_llm_stream(self, user_text, st, websocket, loop, client_ip,
                         tts_queue=None, tts_state=None):
        """调用当前 LLM 提供商，token 通过 asyncio 跨线程推送到 ESP32。"""
        if LLM_PROVIDER == "deepseek":
            if not self.deepseek_ok:
                return None
        elif LLM_PROVIDER == "ollama":
            if not self.ollama_ok:
                return None
        else:
            return None

        system_prompt = _load_system_prompt()
        telemetry_str = ""
        if st.get("telemetry_data"):
            t = st["telemetry_data"]
            telemetry_str = f"温度: {t.get('temp')}℃, 湿度: {t.get('hum')}%"
        full_prompt = system_prompt + ("\n\n[传感器]\n" + telemetry_str if telemetry_str else "")

        # ── 多轮对话：注入历史 ──
        history = self.sessions.get(client_ip, [])
        messages = [{"role": "system", "content": full_prompt}]
        messages += history[-self.MAX_HISTORY_TURNS * 2:]  # 最近 N 轮（每轮 user+assistant）
        messages.append({"role": "user", "content": user_text})

        try:
            import urllib.request
            if LLM_PROVIDER == "deepseek":
                request_body = {
                    "model": DEEPSEEK_MODEL,
                    "messages": messages,
                    "stream": True,
                    "max_tokens": 1024,
                    "temperature": 0.3,
                    "response_format": {"type": "json_object"},
                    "thinking": {"type": "disabled"},
                }
                endpoint = f"{DEEPSEEK_BASE_URL}/chat/completions"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                }
            else:
                request_body = {
                    "model": OLLAMA_MODEL,
                    "messages": messages,
                    "stream": True,
                    "format": "json",
                    "options": {"temperature": 0.7, "num_predict": 1024},
                }
                endpoint = f"{OLLAMA_HOST}/api/chat"
                headers = {"Content-Type": "application/json"}

            payload = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=payload,
                headers=headers,
                method="POST",
            )

            full_text = []
            json_buffer = ""
            total_processed_text = ""
            current_sentence = ""
            tts_extraction_active = True

            with urllib.request.urlopen(req, timeout=120) as resp:
                for line in resp:
                    if st.get("cancel_requested"):
                        break
                    line = line.decode("utf-8", errors="ignore").strip()
                    if not line:
                        continue
                    if LLM_PROVIDER == "deepseek":
                        if not line.startswith("data:"):
                            continue
                        line = line[5:].strip()
                        if line == "[DONE]":
                            break
                    try:
                        chunk = json.loads(line)
                        if LLM_PROVIDER == "deepseek":
                            choices = chunk.get("choices") or []
                            token = (
                                choices[0].get("delta", {}).get("content") or ""
                                if choices else ""
                            )
                        else:
                            token = chunk.get("message", {}).get("content", "")
                        if token:
                            full_text.append(token)
                            json_buffer += token
                            # 跨线程推送 token 到 ESP32
                            asyncio.run_coroutine_threadsafe(
                                websocket.send(json.dumps(
                                    {"event": "llm_delta", "text": token}
                                )),
                                loop
                            )
                            
                            # 提取 TTS 文本并按标点推入队列
                            if tts_queue and tts_extraction_active:
                                match = re.search(r'"tts_text"\s*:\s*"((?:[^"\\]|\\.)*)', json_buffer)
                                if match:
                                    raw_val = match.group(1)
                                    try:
                                        # 正确的反转义方法：利用 json.loads 处理标准的 JSON 字符串
                                        # 这样既能保留原生的中文，又能正确解析 \n 和 \uXXXX
                                        decoded = json.loads('"' + raw_val + '"')
                                        new_chars = decoded[len(total_processed_text):]
                                        if new_chars:
                                            current_sentence += new_chars
                                            total_processed_text += new_chars
                                        
                                        puncs = ['。', '！', '？', '；', '!', '?', ';']
                                        last_punc_idx = -1
                                        for p in puncs:
                                            idx = current_sentence.rfind(p)
                                            if idx > last_punc_idx:
                                                last_punc_idx = idx
                                                
                                        if last_punc_idx != -1:
                                            to_send = current_sentence[:last_punc_idx+1].strip()
                                            current_sentence = current_sentence[last_punc_idx+1:]
                                            if to_send:
                                                asyncio.run_coroutine_threadsafe(tts_queue.put(to_send), loop)
                                                if tts_state is not None:
                                                    tts_state["queued"] += 1
                                    except Exception:
                                        pass

                                # 检查 "tts_text" 是否已经闭合结束
                                match_closed = re.search(r'"tts_text"\s*:\s*"((?:[^"\\]|\\.)*)"', json_buffer)
                                if match_closed:
                                    if current_sentence.strip():
                                        asyncio.run_coroutine_threadsafe(tts_queue.put(current_sentence.strip()), loop)
                                        if tts_state is not None:
                                            tts_state["queued"] += 1
                                        current_sentence = ""
                                    tts_extraction_active = False

                    except json.JSONDecodeError:
                        continue

            result = "".join(full_text).strip()
            print(f"🤖 [{client_ip}] LLM: {result[:80]}...")
            return result

        except Exception as e:
            provider_name = "DeepSeek API" if LLM_PROVIDER == "deepseek" else "Ollama"
            error_detail = ""
            if hasattr(e, "read"):
                try:
                    error_detail = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass
            print(f"❌ {provider_name}: {e} {error_detail}".rstrip())
            return None

    # ════════════════════════════════════════════════════════════════
    # 阶段 3: piper-tts 流式生成 PCM（同步 → 线程池调用）
    # ════════════════════════════════════════════════════════════════

    def _generate_tts_audio_only(self, text, client_ip):
        """TTS：仅生成音频 PCM 数据，与网络流式发送解耦，避免阻塞下一句生成。返回 (引擎名, pcm_data)。"""
        if not text:
            return None

        ffmpeg_bin = shutil.which("ffmpeg")
        import uuid
        uid = uuid.uuid4().hex[:8]
        tmp_pcm = os.path.join(self.response_dir, f"_tts_16k_{uid}.pcm")

        # ═════ 1) 首选阿里云 CosyVoice ═════
        if self.aliyun_tts_ok:
            try:
                import dashscope
                from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

                dashscope.api_key = DASHSCOPE_API_KEY
                dashscope.base_websocket_api_url = (
                    "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
                )
                synthesizer = SpeechSynthesizer(
                    model=ALIYUN_TTS_MODEL,
                    voice=ALIYUN_TTS_VOICE,
                    format=AudioFormat.PCM_16000HZ_MONO_16BIT,
                    volume=max(0, min(100, ALIYUN_TTS_VOLUME)),
                    speech_rate=max(0.5, min(2.0, ALIYUN_TTS_SPEECH_RATE)),
                )
                pcm_data = synthesizer.call(text)
                if pcm_data:
                    print(
                        f"🔊 [{client_ip}] CosyVoice 生成 "
                        f"{len(pcm_data)} 字节 PCM，首包 "
                        f"{synthesizer.get_first_package_delay()}ms"
                    )
                    return ("aliyun-cosyvoice", pcm_data)
                print(
                    "⚠️  CosyVoice 返回空音频: "
                    f"{synthesizer.get_response()}"
                )
            except Exception as exc:
                print(
                    f"⚠️  CosyVoice 失败，改用本地兜底: "
                    f"{type(exc).__name__}: {exc}"
                )

        # ═════ 2) edge-tts 兜底 ═════
        if self.edge_tts_ok and ffmpeg_bin:
            try:
                mp3_path = os.path.join(self.response_dir, f"_tts_edge_{uid}.mp3")
                voice = os.environ.get("TTS_VOICE", "zh-CN-XiaoxiaoNeural")
                subprocess.run(
                    [sys.executable, "-m", "edge_tts", "--text", text,
                     "--voice", voice, "--write-media", mp3_path],
                    capture_output=True, timeout=30, check=True
                )
                subprocess.run(
                    [ffmpeg_bin, "-y", "-i", mp3_path, "-f", "s16le", "-ar", "16000", "-ac", "1",
                     "-filter:a", "aresample=resampler=swr:precision=28,volume=1.0", tmp_pcm],
                    capture_output=True, timeout=30, check=True
                )
                try: os.remove(mp3_path)
                except: pass

                with open(tmp_pcm, "rb") as f:
                    pcm_data = f.read()
                try: os.remove(tmp_pcm)
                except: pass

                if len(pcm_data) > 0:
                    print(f"🔊 [{client_ip}] edge-tts 生成 {len(pcm_data)} 字节 PCM")
                    return ("edge-tts", pcm_data)
            except Exception as e:
                print(f"⚠️  edge-tts 失败 (本次跳过): {type(e).__name__}: {e}")
                # 不永久禁用 edge-tts，下次请求仍会重试
        elif self.edge_tts_ok and not ffmpeg_bin:
            print("⚠️  edge-tts 已安装但未找到 ffmpeg，改用 piper-tts")

        # ═════ 3) 离线 piper-tts 兜底 ═════
        if not self.piper_ok:
            return None

        model_path = os.path.join(ROOT_DIR, PIPER_MODEL)
        tmp_in = os.path.join(self.response_dir, f"_tts_in_{uid}.txt")
        tmp_raw = os.path.join(self.response_dir, f"_tts_piper_{uid}.raw")

        try:
            piper_env = os.environ.copy()
            espeak_path = r"C:\Program Files (x86)\eSpeak\command_line"
            if os.path.isdir(espeak_path):
                piper_env["PATH"] = espeak_path + os.pathsep + piper_env.get("PATH", "")

            with open(tmp_in, "w", encoding="utf-8") as f:
                f.write(text)

            with open(tmp_raw, "wb") as f_raw:
                result = subprocess.run(
                    [sys.executable, "-m", "piper", "--model", model_path,
                     "--output-raw", "-i", tmp_in],
                    stdout=f_raw, stderr=subprocess.PIPE, timeout=30, env=piper_env
                )
            if result.returncode != 0:
                detail = result.stderr.decode("utf-8", errors="ignore").strip()
                print(f"⚠️  piper-tts 生成失败: {detail or result.returncode}")
                return None

            import numpy as np
            raw = np.fromfile(tmp_raw, dtype="<i2")
            if raw.size == 0:
                print("⚠️  piper-tts 返回了空音频")
                return None
            output_count = max(1, int(round(raw.size * 16000 / 22050)))
            source_pos = np.arange(raw.size, dtype=np.float64)
            target_pos = np.linspace(
                0, raw.size - 1, output_count, dtype=np.float64
            )
            pcm_16k = np.interp(target_pos, source_pos, raw).astype("<i2")
            pcm_data = pcm_16k.tobytes()

            for f_path in [tmp_in, tmp_raw, tmp_pcm]:
                try: os.remove(f_path)
                except: pass

            if len(pcm_data) > 0:
                print(f"🔊 [{client_ip}] piper-tts 生成 {len(pcm_data)} 字节 PCM")
                return ("piper-tts", pcm_data)
            return None
        except Exception as e:
            print(f"⚠️  piper-tts 失败: {type(e).__name__}: {e}")
            return None

    async def _stream_pcm_data_async(self, source_and_pcm, websocket, client_ip, st):
        """将生成的 PCM 数据慢速流式发送给设备（全异步实现）。"""
        source, pcm_data = source_and_pcm
        total = len(pcm_data)
        if total == 0:
            return

        await websocket.send(json.dumps({
            "type": "tts_start", "format": "pcm",
            "rate": 16000, "channels": 1, "bits": 16,
            "simulated": st.get("is_web_simulated", False) if st else False
        }))

        CHUNK = 2048
        start_time = time.time()
        try:
            for offset in range(0, total, CHUNK):
                if st and st.get("cancel_requested"):
                    print(f"⚠️ [{client_ip}] TTS 播放被打断")
                    break
                await websocket.send(pcm_data[offset:offset + CHUNK])
                # 放宽速率，允许 ESP32 的环形缓冲区囤积数据，由硬件背压控制流速
                # 使用较小的 CHUNK 和匹配的 sleep，防止 ESP32 接收线程长时间阻塞锁死 websocket mutex
                await asyncio.sleep(0.05)
                
            # 等待实际音频播放完毕，再结束 pcm_task（防止 cjson 提前下发导致 MP3 与 TTS 重叠重放卡顿）
            audio_duration = total / 32000.0
            elapsed = time.time() - start_time
            if not (st and st.get("cancel_requested")) and elapsed < audio_duration:
                # 留 0.1 秒的提前量，让客户端有时间去请求网络 MP3，避免两句话之间停顿太久
                remaining = audio_duration - elapsed - 0.1
                if remaining > 0:
                    await asyncio.sleep(remaining)

            print(f"🔊 [{client_ip}] TTS ({source}): {total} 字节 (16kHz)")
        except websockets.exceptions.ConnectionClosed:
            print(f"⚠️ [{client_ip}] 发送音频途中设备断开连接。")
        except Exception as e:
            print(f"⚠️ [{client_ip}] 发送音频时出错: {e}")


    async def _generate_tts_fallback(self, text, websocket, loop, client_ip):
        """edge-tts 回退方案（piper 失败时调用，一般走不到）。"""
        try:
            mp3_path = os.path.join(self.response_dir, "_tts_fb.mp3")
            voice = os.environ.get("TTS_VOICE", "zh-CN-XiaoxiaoNeural")
            # edge-tts: 文本 → mp3（用 python -m 避免 PATH 问题）
            subprocess.run(
                ["python", "-m", "edge_tts", "--text", text,
                 "--voice", voice, "--write-media", mp3_path],
                capture_output=True, timeout=30, check=True
            )
            pcm_path = mp3_path.replace(".mp3", ".pcm")
            ffmpeg_bin = shutil.which("ffmpeg") or r"C:\Users\kotamen\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe"
            subprocess.run(
                [ffmpeg_bin, "-y", "-i", mp3_path,
                 "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1",
                 "-filter:a", "aresample=resampler=swr:precision=28,volume=0.4", pcm_path],
                capture_output=True, timeout=30, check=True
            )
            with open(pcm_path, "rb") as f:
                pcm = f.read()
            os.remove(mp3_path)
            os.remove(pcm_path)
            await websocket.send(pcm)
        except Exception as e:
            print(f"❌ TTS 回退失败: {e}")

    # ════════════════════════════════════════════════════════════════
    # 音频保存
    # ════════════════════════════════════════════════════════════════

    def _save_audio(self, audio_data):
        if not audio_data:
            return
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            wav_path = os.path.join(self.output_dir, f"rec_{ts}.wav")
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(BIT_DEPTH // 8)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio_data)
            print(f"💾 已保存: {wav_path}")
        except Exception as e:
            print(f"❌ 保存失败: {e}")

    # ════════════════════════════════════════════════════════════════
    # CJSON 解析 + 宏指令解析（不变）
    # ════════════════════════════════════════════════════════════════

    def _parse_llm_json(self, text, client_ip, user_text=""):
        if not text: return None
        cleaned = text.strip()
        
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if lines[0].startswith("```"): lines = lines[1:]
            if lines and lines[-1].strip() == "```": lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
            
        # 移除 LLM 生成内容中非法的真实换行符（将其替换为空格），避免 json.loads 直接崩溃
        cleaned = cleaned.replace("\n", " ").replace("\r", " ")
        
        try:
            raw = json.loads(cleaned)
        except json.JSONDecodeError:
            b1, b2 = cleaned.find("{"), cleaned.rfind("}")
            if b1 >= 0 and b2 > b1:
                try: raw = json.loads(cleaned[b1:b2 + 1])
                except json.JSONDecodeError: return None
            else: return None
        if not isinstance(raw, dict) or "dialogue" not in raw:
            return None
        self._complete_llm_view_intent(raw, user_text)
        return self._resolve_cjson(raw, client_ip)

    def _complete_llm_view_intent(self, raw, user_text):
        """补齐模型遗漏的明确视图字段，不替代模型对整句话的主判断。"""
        if not user_text:
            return

        action = raw.setdefault("action", {})
        audio = action.setdefault("audio", {"command": "keep"})
        music_words = (
            "音乐", "歌曲", "听歌", "放歌", "来一首", "来点音乐",
            "选歌", "换一首", "随机播放", "随机来一首",
        )
        stop_words = ("停止", "停下", "关掉", "关闭", "别放", "不要放")
        category_requested = any(
            keyword in user_text for keyword in MUSIC_KEYWORD_MAP
        )
        filename_requested = any(
            Path(filename).stem.lower() in user_text.lower()
            for filename in _available_music_files()
        )
        if (
            any(word in user_text for word in music_words)
            or category_requested
            or filename_requested
        ):
            if any(word in user_text for word in stop_words):
                audio["command"] = "stop"
                audio["url"] = ""
                print("🧩 [LLM校验] 补齐停止音乐指令")
            else:
                audio["command"] = "play"
                query = user_text
                audio["url"] = f"<search: {query}>"
                audio["loop"] = False
                audio.setdefault("volume", 60)
                print("🧩 [LLM校验] 补齐本地选歌请求（单曲播放）")

        screen = action.setdefault("screen", {"command": "keep"})
        brightness_words = (
            "亮度", "调亮", "调暗", "亮一点", "暗一点",
            "亮屏", "开屏", "黑屏", "关屏",
        )
        if not any(word in user_text for word in brightness_words):
            screen.pop("brightness", None)
        orientation = _extract_orientation_command(user_text)
        current_orientation = str(
            screen.get("orientation") or "keep"
        ).strip().lower()
        if orientation and current_orientation not in {"portrait", "landscape"}:
            screen["orientation"] = orientation
            print(f"🧩 [LLM校验] 补齐屏幕方向: {orientation}")

        photo_query = (
            _extract_fallback_photo_query(user_text, orientation)
            if orientation
            else _extract_photo_search_query(user_text)
        )
        if photo_query:
            screen["command"] = "show_specific"
            screen["url"] = f"<search: {photo_query}>"
            screen.setdefault("hold_mode", "until_midnight")
            print(f"🧩 [LLM校验] 补齐照片语义目标: {photo_query}")
            return

        if _is_random_photo_request(user_text):
            screen["command"] = "show_specific"
            screen["url"] = "<random>"
            screen.setdefault("hold_mode", "until_midnight")
            print("🧩 [LLM校验] 识别为随机照片请求")

    def _resolve_cjson(self, raw, client_ip):
        local_ip = self._get_best_local_ip(client_ip)
        action = raw.get("action", {})

        audio = action.get("audio", {})
        if audio.get("url", "").startswith("<search:"):
            kw = audio["url"][len("<search:"):].rstrip(">").strip()
            selected_music = _select_music_file(kw, user_text="")
            # Decode on the PC and stream 16 kHz mono PCM to the ESP32.
            # This keeps ESP32 memory usage constant for long tracks.
            if selected_music:
                pcm_name = Path(selected_music).with_suffix(".pcm").name
                audio["url"] = (
                    f"http://{local_ip}:8765/music-stream/{pcm_name}"
                )
                print(f"🎵 [{client_ip}] 本地选歌: {selected_music}")
            else:
                audio["command"] = "keep"
                audio["url"] = ""
                print(f"⚠️ [{client_ip}] 本地曲库为空")
        if audio.get("command") == "play":
            audio["loop"] = False
            audio["volume"] = max(20, min(60, MUSIC_PLAYBACK_VOLUME))

        screen = action.get("screen", {})
        scene_hit = None
        view_handled = False
        target_photo = None
        orientation = str(screen.get("orientation") or "keep").strip().lower()
        if orientation not in {"portrait", "landscape"}:
            orientation = "keep"
        if screen.get("url") == "<random>":
            target_photo = self._random_photo_result()
            if target_photo:
                screen["url"] = (
                    f"http://{local_ip}:8765/api/photo/{target_photo['id']}.jpg"
                )
        elif screen.get("url", "").startswith("<search:"):
            kw = screen["url"][len("<search:"):].rstrip(">").strip()
            target_photo = self._search_photo_result(kw)
            if target_photo:
                screen["url"] = (
                    f"http://{local_ip}:8765/api/photo/{target_photo['id']}.jpg"
                )
            for sk, sc in SCENE_MAP.items():
                if sk in kw: scene_hit = sc; break

        target_id = target_photo.get("id", "") if target_photo else ""
        if target_id or orientation != "keep":
            try:
                _set_device_view(target_id, orientation)
                view_handled = True
                print(
                    f"🖼️ [{client_ip}] 整句意图视图控制: "
                    f"target={target_id or 'keep'}, orientation={orientation}"
                )
                # 设备只执行 Flask 队列中的单条组合命令，避免 WebSocket 再执行一次。
                screen["command"] = "keep"
                screen["orientation"] = "keep"
            except Exception as exc:
                print(f"⚠️ [{client_ip}] 组合视图指令下发失败: {exc}")

        if scene_hit:
            if audio.get("command", "keep") == "keep":
                ak = scene_hit["audio_keyword"]
                if ak in AUDIO_ROUTING_MAP:
                    audio["command"] = "play"
                    audio["url"] = (
                        f"http://{local_ip}:8765/music-stream/"
                        f"{Path(random.choice(AUDIO_ROUTING_MAP[ak])).with_suffix('.pcm').name}"
                    )
                    audio["loop"] = False
                    audio["volume"] = max(20, min(60, MUSIC_PLAYBACK_VOLUME))
            mist = action.get("mist", {})
            if mist.get("command", "keep") == "keep":
                mist["command"] = "on"
                mist["channel"] = scene_hit.get("mist_channel", "none")
                mist["level"] = scene_hit.get("mist_level", 1)
        return raw, view_handled

    def _random_photo_result(self):
        try:
            import urllib.request

            with urllib.request.urlopen(
                "http://127.0.0.1:8765/api/device-library", timeout=5
            ) as response:
                result = json.loads(response.read().decode("utf-8"))
            photos = result.get("photos") or []
            if not photos:
                return {}

            current_id = ""
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:8765/api/device/status", timeout=2
                ) as response:
                    current_id = (
                        json.loads(response.read().decode("utf-8"))
                        .get("current_photo_id", "")
                    )
            except Exception:
                pass

            candidates = [
                photo for photo in photos
                if photo.get("id") and photo.get("id") != current_id
            ]
            return random.choice(candidates or photos)
        except Exception as exc:
            print(f"⚠️ 随机照片选择失败: {exc}")
            return {}

    def _search_photo_result(self, keyword):
        try:
            import urllib.parse
            import urllib.request
            query = urllib.parse.urlencode({"q": keyword})
            url = f"http://127.0.0.1:8765/api/photo/search?{query}"
            with urllib.request.urlopen(url, timeout=35) as response:
                result = json.loads(response.read().decode("utf-8"))
            if result.get("id"):
                return result
        except Exception as exc:
            print(f"[WARN] 照片语义检索失败: {exc}")
        return {}

    def _search_photo(self, keyword, local_ip):
        result = self._search_photo_result(keyword)
        photo_id = result.get("id")
        if photo_id:
            return f"http://{local_ip}:8765/api/photo/{photo_id}.jpg"
        return None

    def _apply_keyword_view_fallback(self, text, client_ip):
        """仅在 LLM JSON 解析失败时使用的快速兜底。"""
        orientation = _extract_orientation_command(text)
        photo_query = _extract_fallback_photo_query(text, orientation)
        photo = {}
        if photo_query:
            try:
                photo = _semantic_photo_search(photo_query)
            except Exception as exc:
                print(f"⚠️ [{client_ip}] 兜底照片检索失败: {exc}")
        elif _is_random_photo_request(text):
            photo = self._random_photo_result()
        target_id = photo.get("id", "")
        if not target_id and not orientation:
            return False
        try:
            _set_device_view(target_id, orientation or "keep")
        except Exception as exc:
            print(f"⚠️ [{client_ip}] 兜底视图指令失败: {exc}")
            return False
        print(
            f"⚡ [{client_ip}] 关键词兜底视图控制: "
            f"target={target_id or 'keep'}, orientation={orientation or 'keep'}"
        )
        return True

    def _parse_intent(self, text, client_ip):
        """旧版关键词回退。"""
        action, media = {}, {}
        tl = text.lower()
        if any(w in tl for w in ["加湿","香薰","喷雾","雾化"]):
            action["mist"] = not any(w in tl for w in ["关","停"])
            if action.get("mist"):
                ch = 0
                if "茉莉" in tl or "jasmine" in tl: ch = 1
                elif "蔷薇" in tl or "rose" in tl: ch = 2
                action["mist_channel"] = ch
                lv = 2
                if any(w in tl for w in ["低","弱","1"]): lv = 1
                elif any(w in tl for w in ["高","强","3"]): lv = 3
                action["mist_level"] = lv
        if any(w in tl for w in ["黑屏","关屏","睡觉"]): action["brightness"] = 0
        elif any(w in tl for w in ["亮屏","开屏"]): action["brightness"] = 100
        if any(w in tl for w in ["音乐","播放","听歌"]) and not any(w in tl for w in ["关","停"]):
            best_ip = self._get_best_local_ip(client_ip)
            media["url"] = (
                f"http://{best_ip}:8765/music-stream/"
                f"{Path(random.choice(_available_music_files())).with_suffix('.pcm').name}"
            )
        r = {}
        if action: r["action"] = action
        if media: r["media"] = media
        return r if r else None

    def _get_best_local_ip(self, client_ip):
        local_ips = self._get_local_ips()
        best_ip = local_ips[0] if local_ips else "127.0.0.1"
        if client_ip:
            ipv4 = client_ip.split(":")[-1]
            if "." in ipv4:
                prefix = ".".join(ipv4.split(".")[:3]) + "."
                for ip in local_ips:
                    if ip.startswith(prefix):
                        best_ip = ip
                        break
        return best_ip

    def _get_local_ips(self):
        ips = []
        try:
            # 优先使用 UDP 连接外部地址来获取真实的默认路由出网 IP（规避虚拟网卡和 TUN 接口）
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                if not ip.startswith("198.18.") and not ip.startswith("127."):
                    ips.append(ip)
            except: pass
            finally: s.close()

            for info in socket.getaddrinfo(socket.gethostname(), None):
                if info[0] == socket.AF_INET:
                    ip = info[4][0]
                    if ip not in ips and not ip.startswith("127.") and not ip.startswith("198.18."):
                        ips.append(ip)
            ips.append("127.0.0.1")
        except: ips = ["127.0.0.1"]
        return ips

    async def start(self):
        print("=" * 60)
        print("ESP32 语音助手 WebSocket (流式管线)")
        runtime_model, runtime_device, runtime_compute = getattr(
            self, "_whisper_runtime", (WHISPER_MODEL, "unknown", "unknown")
        )
        print(
            f"ASR: faster-whisper {runtime_model} "
            f"({runtime_device}/{runtime_compute}) + VAD"
        )
        if LLM_PROVIDER == "deepseek":
            print(f"LLM: DeepSeek API {DEEPSEEK_MODEL} (stream, non-thinking)")
        else:
            print(f"LLM: Ollama {OLLAMA_MODEL} (stream)")
        print(f"TTS: piper-tts {PIPER_MODEL}")
        for ip in self._get_local_ips():
            print(f"  ws://{ip}:{WS_PORT}")
        print("=" * 60)
        # close_timeout=2s: 旧连接快速释放，减少 opening handshake failed
        async with websockets.serve(self.handle_client, WS_HOST, WS_PORT,
                                   ping_interval=None,
                                   ping_timeout=None,
                                   close_timeout=2):
            await asyncio.Future()


def main():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    while True:
        try:
            asyncio.run(VoiceServer().start())
        except (KeyboardInterrupt, SystemExit):
            print("\n⚠️  已手动停止")
            break
        except BaseException as e:
            print(f"\n⚠️  服务器异常崩溃 ({e})，正在 3 秒后自动重启以保障高可用...")
            import time
            time.sleep(3)


if __name__ == "__main__":
    main()
