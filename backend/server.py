#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file server.py
@brief 极客相册云端中控服务器与 RESTful API 提供端

@architecture
核心 Web 服务，由 Flask 驱动。
1. 状态管理：使用 SQLite3 数据库维护照片瀑布流、香薰开启状态和环境配置。
2. 设备心跳：响应设备的 HTTP 轮询（如 `/api/device/command`），实现云-边-端命令下发。
"""

from __future__ import annotations

from pathlib import Path
from flask import Flask, abort, send_file, Response, request, redirect, render_template, jsonify
import base64
import mimetypes
import sqlite3
import json
import html
import uuid
import time
import datetime as dt
import queue
import threading
import os
import re
import math
import hashlib
import shutil
import subprocess
from io import BytesIO
from PIL import Image, ImageOps, ImageFilter
import requests
import config as cfg
from websockets.sync.client import connect

ROOT_DIR = Path(__file__).resolve().parent

# --- 配置 ---
DOWNLOAD_KEY = str(getattr(cfg, "DOWNLOAD_KEY", "") or "").strip()
UPLOAD_PASSWORD = str(getattr(cfg, "UPLOAD_PASSWORD", "") or "").strip()
GEMINI_API_KEY = str(getattr(cfg, "GEMINI_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "") or "").strip()
GEMINI_VISION_MODEL = str(getattr(cfg, "GEMINI_VISION_MODEL", "") or os.environ.get("GEMINI_VISION_MODEL", "") or "gemini-2.5-flash-lite").strip()
GEMINI_EMBEDDING_MODEL = str(getattr(cfg, "GEMINI_EMBEDDING_MODEL", "") or os.environ.get("GEMINI_EMBEDDING_MODEL", "") or "gemini-embedding-2").strip()
ENABLE_GEMINI_CAPTION = bool(getattr(cfg, "ENABLE_GEMINI_CAPTION", True))
DISPLAY_ORIENTATION_DEFAULT = str(getattr(cfg, "DISPLAY_ORIENTATION_DEFAULT", "landscape") or "landscape").strip().lower()

DB_PATH = Path(str(getattr(cfg, "DB_PATH", "./photos.db") or "./photos.db")).expanduser()
if not DB_PATH.is_absolute():
    DB_PATH = (ROOT_DIR / DB_PATH).resolve()

IMAGE_DIR = Path(str(getattr(cfg, "IMAGE_DIR", "") or "")).expanduser()
if not IMAGE_DIR.is_absolute():
    IMAGE_DIR = (ROOT_DIR / IMAGE_DIR).resolve()

BIN_OUTPUT_DIR = Path(str(getattr(cfg, "BIN_OUTPUT_DIR", "./output") or "./output")).expanduser()
if not BIN_OUTPUT_DIR.is_absolute():
    BIN_OUTPUT_DIR = (ROOT_DIR / BIN_OUTPUT_DIR).resolve()
BIN_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_DB_PATH = Path(str(getattr(cfg, "UPLOAD_DB_PATH", "./upload.db") or "./upload.db")).expanduser()
if not UPLOAD_DB_PATH.is_absolute():
    UPLOAD_DB_PATH = (ROOT_DIR / UPLOAD_DB_PATH).resolve()

UPLOAD_DIR = Path(str(getattr(cfg, "UPLOAD_DIR", "./upload_photos") or "./upload_photos")).expanduser()
if not UPLOAD_DIR.is_absolute():
    UPLOAD_DIR = (ROOT_DIR / UPLOAD_DIR).resolve()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

FLASK_HOST = str(getattr(cfg, "FLASK_HOST", "0.0.0.0") or "0.0.0.0")
FLASK_PORT = int(getattr(cfg, "FLASK_PORT", 8765) or 8765)
ENABLE_REVIEW_WEBUI = bool(getattr(cfg, "ENABLE_REVIEW_WEBUI", True))
DAILY_PHOTO_QUANTITY = int(getattr(cfg, "DAILY_PHOTO_QUANTITY", 5) or 5)

# 强制模板重新加载，便于开发时修改 HTML 即时生效
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config['TEMPLATES_AUTO_RELOAD'] = True

# 设备状态全局缓存机制
DEVICE_STATE = {
    "last_seen": 0,
    "fps": 0.0,
    "free_mem": 0,
    "current_photo_id": "",
    "desired_orientation": "portrait" if DISPLAY_ORIENTATION_DEFAULT == "portrait" else "landscape",
    "reported_orientation": "portrait" if DISPLAY_ORIENTATION_DEFAULT == "portrait" else "landscape",
    "aroma_channels": [0, 0, 0],
    "pending_commands": []
}
_DEVICE_COMMAND_LOCK = threading.Lock()

def _queue_device_command(command: dict) -> None:
    with _DEVICE_COMMAND_LOCK:
        queue_items = DEVICE_STATE["pending_commands"]
        if command.get("cmd") in {"set_orientation", "switch_photo", "set_view"}:
            queue_items[:] = [
                item for item in queue_items
                if item.get("cmd") not in {"set_orientation", "switch_photo", "set_view"}
            ]
        queue_items.append(command)

# SSE 事件队列：voice_server 回调 → Web 前端实时推送
_SSE_SUBSCRIBERS: list[queue.Queue] = []
_SSE_LOCK = threading.Lock()

def _sse_push(event: dict):
    """向所有活跃 SSE 订阅者广播一条事件。"""
    with _SSE_LOCK:
        dead = []
        for q in _SSE_SUBSCRIBERS:
            try:
                q.put_nowait(event)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _SSE_SUBSCRIBERS.remove(q)

# DB 初始化与静默热迁移
def _init_databases():
    # 1. 初始化 upload.db，增加 dialog_history 表用于智能助手审计
    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS upload_photos (
            id TEXT PRIMARY KEY,
            original_name TEXT,
            rgb565_path TEXT,
            preview_path TEXT,
            message TEXT,
            uploader_name TEXT,
            target_date TEXT,
            created_at TEXT,
            downloaded INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dialog_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            content TEXT,
            aroma_change TEXT,
            photo_change_id TEXT,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS device_photos (
            id TEXT PRIMARY KEY,
            rgb565_path TEXT NOT NULL,
            preview_path TEXT,
            caption TEXT,
            uploader_name TEXT,
            source_type TEXT,
            source_ref TEXT,
            target_date TEXT,
            created_at TEXT,
            hidden INTEGER DEFAULT 0,
            tags TEXT DEFAULT '',
            taken_at TEXT DEFAULT '',
            city TEXT DEFAULT '',
            embedding_json TEXT DEFAULT '',
            embedding_hash TEXT DEFAULT '',
            embedding_model TEXT DEFAULT ''
        )
    """)
    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(device_photos)").fetchall()
    }
    for column, definition in {
        "tags": "TEXT DEFAULT ''",
        "taken_at": "TEXT DEFAULT ''",
        "city": "TEXT DEFAULT ''",
        "embedding_json": "TEXT DEFAULT ''",
        "embedding_hash": "TEXT DEFAULT ''",
        "embedding_model": "TEXT DEFAULT ''",
    }.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE device_photos ADD COLUMN {column} {definition}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_photos_hidden ON device_photos(hidden)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_photos_target_date ON device_photos(target_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_photos_source_type ON device_photos(source_type)"
    )
    conn.commit()
    conn.close()

    # 2. 静默热升级 photos.db，引入 tags 和 hidden
    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        try:
            c.execute("ALTER TABLE photo_scores ADD COLUMN tags TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE photo_scores ADD COLUMN hidden INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        conn.commit()
        conn.close()

_init_databases()

def _normalize_tags(value) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw_tags = [str(item) for item in value]
    else:
        raw_tags = re.split(r"[,，;；、\n]+", str(value or ""))

    result = []
    seen = set()
    for raw_tag in raw_tags:
        tag = re.sub(r"\s+", " ", raw_tag).strip()
        key = tag.casefold()
        if not tag or key in {"none", "null", "undefined"} or key in seen:
            continue
        seen.add(key)
        result.append(tag[:32])
        if len(result) >= 24:
            break
    return result

def _serialize_tags(value) -> str:
    return ",".join(_normalize_tags(value))

def _metadata_date(meta: dict, fallback: str = "") -> str:
    candidates = [str(meta.get("exif_datetime") or "")]
    try:
        exif_data = json.loads(meta.get("exif_json") or "{}")
        candidates.append(str(exif_data.get("datetime") or ""))
    except (TypeError, ValueError):
        pass
    for value in candidates:
        match = re.search(r"(\d{4})[:\-](\d{2})[:\-](\d{2})", value)
        if match:
            return "-".join(match.groups())
    return fallback

def _season_tag(date_value: str) -> str:
    match = re.search(r"\d{4}-(\d{2})-\d{2}", date_value or "")
    if not match:
        return ""
    month = int(match.group(1))
    if month in (3, 4, 5):
        return "春天"
    if month in (6, 7, 8):
        return "夏天"
    if month in (9, 10, 11):
        return "秋天"
    return "冬天"

def _build_photo_tags(
    *,
    explicit_tags="",
    date_value: str = "",
    city: str = "",
    source_type: str = "",
    photo_type: str = "",
) -> str:
    tags = _normalize_tags(explicit_tags)
    date_match = re.search(r"(\d{4})-(\d{2})-\d{2}", date_value or "")
    if date_match:
        year, month = date_match.groups()
        tags.extend([f"{year}年", f"{year}-{month}", f"{int(month)}月"])
        season = _season_tag(date_value)
        if season:
            tags.append(season)
    if city:
        tags.append(city)
    if photo_type:
        tags.append(photo_type)
    if source_type == "upload":
        tags.append("上传")
    elif source_type == "output":
        tags.append("本地图库")
    return _serialize_tags(tags)

def _strip_derived_tags(value, city: str = "") -> list[str]:
    derived = {"春天", "夏天", "秋天", "冬天", "上传", "本地图库"}
    if city:
        derived.add(city)
    result = []
    for tag in _normalize_tags(value):
        if tag in derived:
            continue
        if re.fullmatch(r"\d{4}年|\d{4}-\d{2}|\d{1,2}月", tag):
            continue
        result.append(tag)
    return result

def _photo_embedding_text(photo: dict) -> str:
    tags = "、".join(_normalize_tags(photo.get("tags")))
    parts = [
        f"照片标题：{photo.get('caption') or photo.get('id') or '未命名'}",
        f"标签：{tags or '无'}",
        f"拍摄日期：{photo.get('taken_at') or photo.get('target_date') or '未知'}",
        f"地点：{photo.get('city') or '未知'}",
        f"来源：{photo.get('source_type') or '未知'}",
    ]
    return "；".join(parts)

def _embedding_content_hash(photo: dict) -> str:
    return hashlib.sha256(_photo_embedding_text(photo).encode("utf-8")).hexdigest()

def _gemini_embedding(text: str, *, is_query: bool) -> list[float]:
    if not GEMINI_API_KEY:
        raise GeminiAnalysisError("Gemini API 未配置")
    prefix = (
        f"task: search result | query: {text}"
        if is_query
        else f"title: photo | text: {text}"
    )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_EMBEDDING_MODEL}:embedContent"
    )
    payload = {
        "content": {"parts": [{"text": prefix}]},
        "output_dimensionality": 768,
    }
    last_error = "Gemini 向量接口未返回有效结果"
    for attempt in range(2):
        try:
            resp = requests.post(
                url,
                params={"key": GEMINI_API_KEY},
                json=payload,
                timeout=30,
            )
            if resp.ok:
                values = (resp.json().get("embedding") or {}).get("values") or []
                if values:
                    return [float(value) for value in values]
                last_error = "Gemini 向量接口返回空向量"
            else:
                last_error = _gemini_error_message(resp)
        except requests.Timeout:
            last_error = "Gemini 向量请求超时"
        except requests.RequestException as exc:
            last_error = f"Gemini 向量网络请求失败: {exc}"
        if attempt == 0:
            time.sleep(1.2)
    raise GeminiAnalysisError(last_error)

def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)

def _refresh_photo_embedding(photo: dict, *, force: bool = False) -> bool:
    content_hash = _embedding_content_hash(photo)
    if (
        not force
        and photo.get("embedding_json")
        and photo.get("embedding_hash") == content_hash
        and photo.get("embedding_model") == GEMINI_EMBEDDING_MODEL
    ):
        return False
    vector = _gemini_embedding(_photo_embedding_text(photo), is_query=False)
    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    conn.execute(
        """
        UPDATE device_photos
        SET embedding_json = ?, embedding_hash = ?, embedding_model = ?
        WHERE id = ?
        """,
        (
            json.dumps(vector, separators=(",", ":")),
            content_hash,
            GEMINI_EMBEDDING_MODEL,
            photo["id"],
        ),
    )
    conn.commit()
    conn.close()
    return True

def _semantic_search_device_photos(
    query: str,
    limit: int = 5,
    min_score: float = 0.55,
    max_score_gap: float = 0.10,
    adjacent_score_gap: float = 0.06,
) -> list[dict]:
    query_vector = _gemini_embedding(query, is_query=True)
    rows = _load_device_photos(include_hidden=False, limit=None)
    matches = []
    for photo in rows:
        if not photo.get("embedding_json"):
            continue
        try:
            vector = json.loads(photo["embedding_json"])
        except (TypeError, ValueError):
            continue
        score = _cosine_similarity(query_vector, vector)
        matches.append({**photo, "semantic_score": round(score, 6)})
    matches.sort(key=lambda item: item["semantic_score"], reverse=True)
    if not matches:
        return []

    top_score = matches[0]["semantic_score"]
    adaptive_threshold = max(min_score, top_score - max_score_gap)
    relevant = []
    previous_score = None
    for photo in matches:
        score = photo["semantic_score"]
        if score < adaptive_threshold:
            break
        if previous_score is not None and previous_score - score >= adjacent_score_gap:
            break
        relevant.append(photo)
        previous_score = score
    return relevant[:max(1, min(int(limit), 20))]

def _photo_score_by_id(photo_id: str) -> dict:
    if not DB_PATH.exists():
        return {}
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT path, side_caption, caption, exif_json, exif_datetime,
               exif_city, tags, type, reason
        FROM photo_scores
        WHERE path LIKE ?
    """, (f"%{photo_id}%",)).fetchall()
    conn.close()
    if not rows:
        return {}
    row = next(
        (candidate for candidate in rows if Path(candidate["path"]).stem == photo_id),
        None,
    )
    return dict(row) if row else {}

def _sync_device_library_from_output() -> None:
    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    for rgb565_path in sorted(BIN_OUTPUT_DIR.glob("photo_*.rgb565")):
        photo_id = rgb565_path.stem.removeprefix("photo_")
        preview_path = BIN_OUTPUT_DIR / f"preview_{photo_id}.jpg"
        meta = _photo_score_by_id(photo_id)
        caption = meta.get("side_caption") or meta.get("caption") or photo_id
        date_str = _metadata_date(meta, dt.date.today().isoformat())
        city = str(meta.get("exif_city") or "").strip()
        tags = _build_photo_tags(
            explicit_tags=meta.get("tags"),
            date_value=date_str,
            city=city,
            source_type="output",
            photo_type=str(meta.get("type") or "").strip(),
        )
        conn.execute("""
            INSERT INTO device_photos
                (id, rgb565_path, preview_path, caption, uploader_name,
                 source_type, source_ref, target_date, created_at, hidden,
                 tags, taken_at, city)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'), 0, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                rgb565_path = excluded.rgb565_path,
                preview_path = excluded.preview_path,
                caption = COALESCE(NULLIF(device_photos.caption, ''), excluded.caption),
                target_date = COALESCE(NULLIF(device_photos.target_date, ''), excluded.target_date),
                tags = COALESCE(NULLIF(device_photos.tags, ''), excluded.tags),
                taken_at = COALESCE(NULLIF(device_photos.taken_at, ''), excluded.taken_at),
                city = COALESCE(NULLIF(device_photos.city, ''), excluded.city)
        """, (
            photo_id,
            str(rgb565_path),
            str(preview_path) if preview_path.exists() else "",
            caption,
            "",
            "output",
            photo_id,
            date_str,
            tags,
            date_str,
            city,
        ))
    conn.row_factory = sqlite3.Row
    upload_rows = conn.execute("""
        SELECT id, rgb565_path, message, uploader_name, target_date, created_at
        FROM upload_photos
    """).fetchall()
    for row in upload_rows:
        upload_id = row["id"]
        preview_path = UPLOAD_DIR / f"{upload_id}_preview.jpg"
        date_str = row["target_date"] or dt.date.today().isoformat()
        tags = _build_photo_tags(date_value=date_str, source_type="upload")
        conn.execute("""
            INSERT INTO device_photos
                (id, rgb565_path, preview_path, caption, uploader_name,
                 source_type, source_ref, target_date, created_at, hidden,
                 tags, taken_at, city)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, '')
            ON CONFLICT(id) DO UPDATE SET
                rgb565_path = excluded.rgb565_path,
                preview_path = excluded.preview_path,
                caption = COALESCE(NULLIF(device_photos.caption, ''), excluded.caption),
                uploader_name = COALESCE(NULLIF(device_photos.uploader_name, ''), excluded.uploader_name),
                target_date = COALESCE(NULLIF(device_photos.target_date, ''), excluded.target_date),
                tags = COALESCE(NULLIF(device_photos.tags, ''), excluded.tags),
                taken_at = COALESCE(NULLIF(device_photos.taken_at, ''), excluded.taken_at)
        """, (
            f"upload_{upload_id}",
            row["rgb565_path"],
            str(preview_path) if preview_path.exists() else "",
            row["message"] or upload_id,
            row["uploader_name"] or "上传",
            "upload",
            upload_id,
            date_str,
            row["created_at"] or dt.datetime.now().isoformat(sep=" ", timespec="seconds"),
            tags,
            date_str,
        ))
    conn.commit()
    conn.close()

_sync_device_library_from_output()

# 辅助函数
def _send_static_file(p: Path) -> Response:
    if not p.exists() or not p.is_file():
        abort(404)
    if p.suffix.lower() == ".rgb565":
        return send_file(p, mimetype="application/octet-stream", as_attachment=False)
    mt, _ = mimetypes.guess_type(str(p))
    if mt:
        return send_file(p, mimetype=mt, as_attachment=False)
    return send_file(p, as_attachment=False)

def extract_date_from_exif(exif_json: str | None) -> str:
    """
    从 EXIF JSON 字符串中提取拍摄日期。
    
    :param exif_json: 包含 EXIF 信息的 JSON 字符串
    :return: 格式化为 YYYY-MM-DD 的日期字符串，若提取失败则返回空字符串
    """
    if not exif_json:
        return ""
    try:
        data = json.loads(exif_json)
        dtv = data.get("datetime")
        if not dtv:
            return ""
        date_part = str(dtv).split()[0]
        parts = date_part.replace(":", "-").split("-")
        if len(parts) >= 3:
            return f"{parts[0]}-{parts[1]}-{parts[2]}"
    except Exception:
        pass
    return ""

# 照片查询逻辑
def load_rows(page: int = 1, page_size: int = 20, md: str = "", filter_date: str = "", tag: str = "", query: str = "", sort: str = "memory"):
    """
    从数据库加载并筛选照片记录。
    
    :param page: 当前页码
    :param page_size: 每页数量
    :param md: 按月日匹配 (例如 05-28)
    :param filter_date: 按完整日期匹配
    :param tag: 按标签匹配
    :param query: 全文模糊搜索关键字
    :param sort: 排序依据
    :return: 照片行字典列表与总记录数
    """
    if not DB_PATH.exists():
        return [], 0
    if page < 1: page = 1
    offset = (page - 1) * page_size

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    dt_expr = "json_extract(exif_json, '$.datetime')"
    where_clauses = ["IFNULL(hidden, 0) = 0"]
    params = []

    md = (md or "").strip()
    if md and len(md) == 5 and md[2] == "-":
        md_expr = f"(substr({dt_expr}, 6, 2) || '-' || substr({dt_expr}, 9, 2))"
        where_clauses.append(f"{dt_expr} IS NOT NULL AND {md_expr} = ?")
        params.append(md)
        
    filter_date = (filter_date or "").strip()
    if filter_date:
        search_date = filter_date.replace("-", ":")
        where_clauses.append(f"({dt_expr} LIKE ? OR exif_datetime LIKE ?)")
        params.append(search_date + "%")
        params.append(search_date + "%")
        
    tag = (tag or "").strip()
    if tag:
        where_clauses.append(f"IFNULL(tags, '') LIKE ?")
        params.append(f"%{tag}%")
        
    query = (query or "").strip()
    if query:
        where_clauses.append(f"(path LIKE ? OR caption LIKE ? OR side_caption LIKE ? OR reason LIKE ? OR IFNULL(tags, '') LIKE ?)")
        for _ in range(5):
            params.append(f"%{query}%")

    where_sql = "WHERE " + " AND ".join(where_clauses)
    total = c.execute(f"SELECT COUNT(1) FROM photo_scores {where_sql}", params).fetchone()[0]

    sort_map = {
        "beauty": "ORDER BY COALESCE(beauty_score, -1) DESC, path",
        "time_new": f"ORDER BY ({dt_expr} IS NULL) ASC, {dt_expr} DESC, path",
        "time_old": f"ORDER BY ({dt_expr} IS NULL) ASC, {dt_expr} ASC, path",
    }
    order_sql = sort_map.get(sort, "ORDER BY COALESCE(memory_score, -1) DESC, path")

    rows = c.execute(f"""
        SELECT path, caption, type, memory_score, beauty_score, reason,
               exif_json, width, height, orientation, used_at, side_caption,
               exif_datetime, exif_make, exif_model, exif_city, exif_gps_lat, exif_gps_lon, tags
        FROM photo_scores {where_sql} {order_sql} LIMIT ? OFFSET ?
    """, list(params) + [page_size, offset]).fetchall()
    
    result = [dict(r) for r in rows]
    conn.close()
    return result, int(total)

# API: 网页端照片管理交互
@app.route("/api/photos/hide", methods=["POST"])
def api_hide_photo():
    data = request.json or {}
    path = data.get("path")
    if not path:
        return jsonify({"ok": False, "error": "缺少 path 参数"}), 400
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE photo_scores SET hidden = 1 WHERE path = ?", (path,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.route("/api/photos/update_tags", methods=["POST"])
def api_update_tags():
    data = request.json or {}
    path = data.get("path")
    tags = data.get("tags", "")
    if not path:
        return jsonify({"ok": False, "error": "缺少 path 参数"}), 400
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE photo_scores SET tags = ? WHERE path = ?", (tags, path))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.route("/api/photos/update_side_caption", methods=["POST"])
def api_update_side_caption():
    data = request.json or {}
    path = data.get("path")
    side_caption = data.get("side_caption", "")
    if not path:
        return jsonify({"ok": False, "error": "缺少 path 参数"}), 400
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE photo_scores SET side_caption = ? WHERE path = ?", (side_caption, path))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

# API: 设备联络协议与遥控器
@app.route("/api/device/heartbeat", methods=["POST"])
def api_device_heartbeat():
    data = request.json or {}
    DEVICE_STATE["last_seen"] = time.time()
    if "fps" in data: DEVICE_STATE["fps"] = float(data["fps"])
    if "free_mem" in data: DEVICE_STATE["free_mem"] = int(data["free_mem"])
    if "current_photo_id" in data: DEVICE_STATE["current_photo_id"] = data["current_photo_id"]
    if "display_orientation" in data: DEVICE_STATE["reported_orientation"] = _normalize_orientation(data["display_orientation"])
    if "aroma_channels" in data: DEVICE_STATE["aroma_channels"] = data["aroma_channels"]
    return jsonify({"ok": True})

@app.route("/api/device/status", methods=["GET"])
def api_device_status():
    # 判断是否在线，30秒无心跳即掉线
    is_online = (time.time() - DEVICE_STATE["last_seen"]) < 30
    return jsonify({
        "online": is_online,
        "fps": DEVICE_STATE["fps"],
        "free_mem": DEVICE_STATE["free_mem"],
        "current_photo_id": DEVICE_STATE["current_photo_id"],
        "display_orientation": DEVICE_STATE["desired_orientation"],
        "desired_orientation": DEVICE_STATE["desired_orientation"],
        "reported_orientation": DEVICE_STATE["reported_orientation"],
        "orientation_synced": DEVICE_STATE["desired_orientation"] == DEVICE_STATE["reported_orientation"],
        "aroma_channels": DEVICE_STATE["aroma_channels"],
        "pending_command": (
            DEVICE_STATE["pending_commands"][0]
            if DEVICE_STATE["pending_commands"]
            else None
        ),
        "pending_command_count": len(DEVICE_STATE["pending_commands"]),
    })

@app.route("/api/device/control", methods=["POST"])
def api_device_control():
    data = request.json or {}
    cmd = data.get("cmd")
    if cmd == "switch_photo":
        _queue_device_command({
            "cmd": "switch_photo",
            "target_id": data.get("target_id", "next"),
        })
    elif cmd == "toggle_aroma":
        _queue_device_command({
            "cmd": "toggle_aroma",
            "channel": data.get("channel", 0),
            "state": data.get("state", 1),
        })
    elif cmd == "set_orientation":
        orientation = _normalize_orientation(data.get("orientation"))
        DEVICE_STATE["desired_orientation"] = orientation
        _queue_device_command({
            "cmd": "set_orientation",
            "orientation": orientation,
        })
    elif cmd == "set_view":
        target_id = str(data.get("target_id") or "").strip()[:63]
        orientation_value = str(data.get("orientation") or "keep").strip().lower()
        orientation = (
            "keep"
            if orientation_value == "keep"
            else _normalize_orientation(orientation_value)
        )
        if orientation != "keep":
            DEVICE_STATE["desired_orientation"] = orientation
        _queue_device_command({
            "cmd": "set_view",
            "target_id": target_id,
            "orientation": orientation,
        })
    else:
        return jsonify({"ok": False, "error": "unsupported command"}), 400
    return jsonify({"ok": True})

@app.route("/api/device/command", methods=["GET"])
def api_device_command():
    # 给 ESP32 用的接口，获取后自动清空
    with _DEVICE_COMMAND_LOCK:
        cmd = (
            DEVICE_STATE["pending_commands"].pop(0)
            if DEVICE_STATE["pending_commands"]
            else None
        )
    return jsonify({"command": cmd})

# API: 智能交互审计与情感联络
@app.route("/api/dialogs/send", methods=["POST"])
def api_dialogs_send():
    data = request.json or {}
    user_msg = data.get("message", "").strip()
    if not user_msg:
        return jsonify({"ok": False, "error": "空消息"})
    
    is_online = (time.time() - DEVICE_STATE["last_seen"]) < 30
    
    # 记录用户发言
    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    conn.execute("INSERT INTO dialog_history (sender, content, created_at) VALUES (?, ?, datetime('now', 'localtime'))", ("user", user_msg))
    conn.commit()
    conn.close()

    # 通过内部 RPC 立即下发指令给 voice_server.py（跳过 ESP32 的 5 秒长轮询）
    try:
        with connect("ws://127.0.0.1:8888") as ws:
            ws.send(json.dumps({
                "event": "flask_rpc",
                "cmd": "simulate_text",
                "text": user_msg
            }))
    except Exception as e:
        print(f"⚠️  [Flask] 内部转发请求给 voice_server 失败: {e}")

    return jsonify({"ok": True, "delivered": True, "device_online": is_online})

@app.route("/api/dialogs/cancel", methods=["POST"])
def api_dialogs_cancel():
    try:
        with connect("ws://127.0.0.1:8888") as ws:
            ws.send(json.dumps({
                "event": "flask_rpc",
                "cmd": "simulate_text",
                "text": "[CANCEL]"
            }))
    except Exception as e:
        print(f"⚠️  [Flask] 内部转发撤回请求失败: {e}")
    return jsonify({"ok": True})

@app.route("/api/dialogs/stream")
def api_dialogs_stream():
    """SSE 端点：向 Web 前端实时推送 AI 回复文本。"""
    def event_generator():
        q: queue.Queue = queue.Queue(maxsize=50)
        with _SSE_LOCK:
            _SSE_SUBSCRIBERS.append(q)
        try:
            while True:
                try:
                    event = q.get(timeout=25)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    # 心跳包，防止连接超时断开
                    yield "data: {\"type\": \"heartbeat\"}\n\n"
        finally:
            with _SSE_LOCK:
                if q in _SSE_SUBSCRIBERS:
                    _SSE_SUBSCRIBERS.remove(q)

    return Response(
        event_generator(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )

@app.route("/api/internal/llm_reply", methods=["POST"])
def api_internal_llm_reply():
    """内部回调接口：voice_server.py 在 LLM 回复完成后调用此接口推送文本给 Web UI。"""
    data = request.json or {}
    reply_text = data.get("reply", "").strip()
    if not reply_text:
        return jsonify({"ok": False, "error": "空回复"})
    
    # 存入对话历史
    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    conn.execute("INSERT INTO dialog_history (sender, content, created_at) VALUES (?, ?, datetime('now', 'localtime'))", ("ai", reply_text))
    conn.commit()
    conn.close()
    
    # SSE 广播
    _sse_push({"type": "ai_reply", "reply": reply_text})
    return jsonify({"ok": True})

@app.route("/api/internal/asr_text", methods=["POST"])
def api_internal_asr_text():
    """接收 ESP32 物理麦克风的 ASR 文本并同步到 Web UI。"""
    data = request.json or {}
    user_text = data.get("text", "").strip()
    if not user_text:
        return jsonify({"ok": False, "error": "空文本"})

    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    conn.execute(
        "INSERT INTO dialog_history (sender, content, created_at) "
        "VALUES (?, ?, datetime('now', 'localtime'))",
        ("user", user_text),
    )
    conn.commit()
    conn.close()

    _sse_push({"type": "user_message", "text": user_text})
    return jsonify({"ok": True})

@app.route("/api/dialogs/history", methods=["GET"])
def api_dialogs_history():
    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, sender, content, aroma_change, photo_change_id, created_at FROM dialog_history ORDER BY id ASC").fetchall()
    result = [dict(r) for r in rows]
    conn.close()
    response = jsonify({"history": result})
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route("/api/dialogs/clear", methods=["POST"])
def api_dialogs_clear():
    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    conn.execute("DELETE FROM dialog_history")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

# API: 惊喜队列与历史记录 (供前端请求)
@app.route("/api/upload/history", methods=["GET"])
def api_upload_history():
    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, original_name, preview_path, message, uploader_name, target_date, created_at, downloaded FROM upload_photos ORDER BY created_at DESC").fetchall()
    result = [dict(r) for r in rows]
    conn.close()
    return jsonify({"history": result})

@app.route("/api/upload/delete", methods=["POST"])
def api_upload_delete():
    data = request.json or {}
    uid = data.get("id")
    if not uid: return jsonify({"ok": False})
    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    conn.execute("DELETE FROM upload_photos WHERE id = ?", (uid,))
    conn.execute("DELETE FROM device_photos WHERE source_type = 'upload' AND source_ref = ?", (uid,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.route("/api/upload/clear", methods=["POST"])
def api_upload_clear():
    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    conn.execute("DELETE FROM upload_photos")
    conn.execute("DELETE FROM device_photos WHERE source_type = 'upload'")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.route("/api/baidu/sync", methods=["POST"])
def api_baidu_sync():
    """手动触发百度网盘增量同步任务"""
    import subprocess
    try:
        # 异步非阻塞执行同步脚本
        subprocess.Popen(["python", str(ROOT_DIR / "tasks" / "baidu_sync_task.py")])
        return jsonify({"ok": True, "message": "百度云端同步任务已在后台启动"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# 页面路由: 使用 Template 引擎渲染模块化 UI
@app.get("/")
def index():
    if ENABLE_REVIEW_WEBUI:
        return redirect("/review")
    return "InkTime Album Server — /review disabled, /api/* active"

@app.get("/review")
def review():
    if not ENABLE_REVIEW_WEBUI: abort(404)
    page = int(request.args.get("page", "1") or 1)
    md = (request.args.get("md", "") or "").strip()
    filter_date = (request.args.get("filter_date", "") or "").strip()
    tag = (request.args.get("tag", "") or "").strip()
    query = (request.args.get("query", "") or "").strip()
    sort = (request.args.get("sort", "") or "memory").strip()
    
    rows, total = load_rows(page=page, page_size=20, md=md, filter_date=filter_date, tag=tag, query=query, sort=sort)
    total_pages = (total + 19) // 20

    # 获取系统已有所有不重复的 年月 和 标签 供下拉筛选
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("SELECT DISTINCT substr(json_extract(exif_json, '$.datetime'), 1, 7) FROM photo_scores WHERE IFNULL(hidden, 0) = 0 AND json_extract(exif_json, '$.datetime') IS NOT NULL")
    date_options = sorted([r[0].replace(':', '-') for r in c.fetchall() if r[0]], reverse=True)
    c.execute("SELECT tags FROM photo_scores WHERE IFNULL(hidden, 0) = 0 AND tags IS NOT NULL AND tags != ''")
    all_tags = set()
    for (t_str,) in c.fetchall():
        for t in t_str.split(","):
            if t.strip(): all_tags.add(t.strip())
    conn.close()

    # 处理照片格式以适配前端渲染
    photos = []
    for r in rows:
        r_dict = dict(r)
        r_dict["date_str"] = extract_date_from_exif(r_dict.get("exif_json"))
        try:
            # 尝试计算出相对于 IMAGE_DIR 的子路径（例如 baidu_cloud/xxx.jpg）
            rel_path = Path(r_dict["path"]).relative_to(IMAGE_DIR)
            r_dict["img_uri"] = "/images/" + str(rel_path).replace("\\", "/")
        except ValueError:
            # 如果不在 IMAGE_DIR 内，降级使用 basename
            r_dict["img_uri"] = "/images/" + Path(r_dict["path"]).name
            
        photos.append(r_dict)

    return render_template("review.html", photos=photos, page=page, total_pages=total_pages, 
                           md=md, filter_date=filter_date, tag=tag, query=query, sort=sort,
                           date_options=date_options, tag_options=sorted(list(all_tags)))

@app.get("/upload")
def upload_page():
    password_required = bool(UPLOAD_PASSWORD)
    return render_template("upload.html", password_required=password_required)

@app.get("/device")
def device_page():
    return render_template("device.html")

@app.get("/dialogs")
def dialogs_page():
    return render_template("dialogs.html")

@app.get("/images/<path:subpath>")
def images(subpath: str):
    if not ENABLE_REVIEW_WEBUI: abort(404)
    # 直接使用传过来的带有文件夹层级的路径
    filepath = IMAGE_DIR / subpath
    if not filepath.is_file(): abort(404)
    return send_file(str(filepath))

@app.get("/music/<path:subpath>")
def music(subpath: str):
    # 提供音乐流媒体服务。PCM 缓存缺失时由同名 MP3 首次生成。
    # ESP32 串口按键 6 固定请求 test.mp3，将其映射到节奏清晰的功放测试曲。
    if subpath == "test.mp3":
        subpath = "happy_pop_1.mp3"
    filepath = ROOT_DIR / "music" / subpath
    if not filepath.is_file() and subpath.endswith("_16k_mono.pcm"):
        source_name = subpath.removesuffix("_16k_mono.pcm") + ".mp3"
        source_path = ROOT_DIR / "music" / source_name
        ffmpeg = shutil.which("ffmpeg")
        if source_path.is_file() and ffmpeg:
            temp_path = filepath.with_suffix(".pcm.tmp")
            try:
                subprocess.run(
                    [
                        ffmpeg, "-y", "-i", str(source_path),
                        "-f", "s16le", "-ar", "16000", "-ac", "1",
                        str(temp_path),
                    ],
                    capture_output=True,
                    timeout=120,
                    check=True,
                )
                temp_path.replace(filepath)
            except (OSError, subprocess.SubprocessError):
                temp_path.unlink(missing_ok=True)
    if not filepath.is_file(): abort(404)
    mimetype = (
        "application/octet-stream"
        if filepath.suffix.lower() == ".pcm"
        else None
    )
    return send_file(str(filepath), mimetype=mimetype)

@app.get("/music-stream/<path:subpath>")
def music_stream(subpath: str):
    """Transcode an MP3 to 16 kHz mono PCM while streaming it to the ESP32."""
    music_root = (ROOT_DIR / "music").resolve()
    if not subpath.lower().endswith(".pcm"):
        abort(404)
    source_name = subpath[:-4] + ".mp3"
    source_path = (music_root / source_name).resolve()
    try:
        source_path.relative_to(music_root)
    except ValueError:
        abort(404)
    if not source_path.is_file():
        abort(404)

    def generate_pcm():
        import av

        container = av.open(str(source_path))
        resampler = av.AudioResampler(
            format="s16",
            layout="mono",
            rate=16000,
        )
        pending = bytearray()

        def append_frames(frames):
            for output_frame in frames:
                pending.extend(
                    output_frame.to_ndarray()
                    .astype("<i2", copy=False)
                    .tobytes()
                )

        try:
            for frame in container.decode(audio=0):
                converted = resampler.resample(frame)
                if converted is None:
                    continue
                frames = converted if isinstance(converted, list) else [converted]
                append_frames(frames)
                while len(pending) >= 16384:
                    yield bytes(pending[:16384])
                    del pending[:16384]

            flushed = resampler.resample(None)
            if flushed:
                frames = flushed if isinstance(flushed, list) else [flushed]
                append_frames(frames)
            if pending:
                yield bytes(pending)
        finally:
            container.close()

    return Response(
        generate_pcm(),
        mimetype="application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Audio-Format": "s16le;rate=16000;channels=1",
        },
    )

# ESP32 核心依赖接口（保持原样兼容）
def _load_device_photos(
    include_hidden: bool = False,
    limit: int | None = None,
    query: str = "",
    tag: str = "",
    date_from: str = "",
    date_to: str = "",
    source_type: str = "",
) -> list[dict]:
    _sync_device_library_from_output()
    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    conn.row_factory = sqlite3.Row
    where_clauses = []
    params = []
    if not include_hidden:
        where_clauses.append("IFNULL(hidden, 0) = 0")

    for token in [item for item in re.split(r"\s+", query.strip()) if item]:
        search_value = f"%{token}%"
        where_clauses.append("""
            (
                id LIKE ? OR IFNULL(caption, '') LIKE ?
                OR IFNULL(uploader_name, '') LIKE ?
                OR IFNULL(tags, '') LIKE ?
                OR IFNULL(city, '') LIKE ?
                OR IFNULL(source_ref, '') LIKE ?
            )
        """)
        params.extend([search_value] * 6)

    normalized_tag = _normalize_tags(tag)
    if normalized_tag:
        where_clauses.append("(',' || IFNULL(tags, '') || ',') LIKE ?")
        params.append(f"%,{normalized_tag[0]},%")
    if date_from:
        where_clauses.append(
            "substr(COALESCE(NULLIF(taken_at, ''), target_date), 1, 10) >= ?"
        )
        params.append(date_from)
    if date_to:
        where_clauses.append(
            "substr(COALESCE(NULLIF(taken_at, ''), target_date), 1, 10) <= ?"
        )
        params.append(date_to)
    if source_type in {"upload", "output"}:
        where_clauses.append("source_type = ?")
        params.append(source_type)

    where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    limit_sql = "" if limit is None else "LIMIT ?"
    if limit is not None:
        params.append(limit)
    rows = conn.execute(f"""
        SELECT id, rgb565_path, preview_path, caption, uploader_name,
               source_type, source_ref, target_date, created_at, hidden,
               tags, taken_at, city, embedding_json, embedding_hash,
               embedding_model
        FROM device_photos
        {where_sql}
        ORDER BY COALESCE(NULLIF(taken_at, ''), target_date) DESC,
                 datetime(created_at) DESC, id
        {limit_sql}
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def _device_photo_tag_facets(include_hidden: bool = False) -> list[dict]:
    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    where_sql = "" if include_hidden else "WHERE IFNULL(hidden, 0) = 0"
    rows = conn.execute(f"SELECT tags FROM device_photos {where_sql}").fetchall()
    conn.close()
    counts = {}
    for (value,) in rows:
        for tag in _normalize_tags(value):
            counts[tag] = counts.get(tag, 0) + 1
    return [
        {"tag": tag, "count": count}
        for tag, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]

def _get_device_photo(photo_id: str, include_hidden: bool = False) -> dict | None:
    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    conn.row_factory = sqlite3.Row
    if include_hidden:
        row = conn.execute("SELECT * FROM device_photos WHERE id = ?", (photo_id,)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM device_photos WHERE id = ? AND IFNULL(hidden, 0) = 0",
            (photo_id,),
        ).fetchone()
    conn.close()
    return dict(row) if row else None

def _resolve_device_photo_source(photo: dict) -> Path | None:
    source_type = photo.get("source_type") or ""
    source_ref = photo.get("source_ref") or ""
    if source_type == "upload" and source_ref:
        conn = sqlite3.connect(str(UPLOAD_DB_PATH))
        row = conn.execute(
            "SELECT original_name FROM upload_photos WHERE id = ?",
            (source_ref,),
        ).fetchone()
        conn.close()
        if row:
            ext = Path(row[0] or "").suffix
            if ext:
                p = UPLOAD_DIR / f"{source_ref}{ext}"
                if p.exists():
                    return p

    if source_type == "output":
        meta = _photo_score_by_id(source_ref or photo.get("id", ""))
        if meta.get("path"):
            p = Path(meta["path"])
            if p.exists():
                return p

    for key in ("preview_path", "rgb565_path"):
        value = photo.get(key)
        if value and Path(value).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}:
            p = Path(value)
            if p.exists():
                return p
    return None

def _device_photo_variant_paths(photo: dict, orientation: str) -> tuple[Path, Path | None]:
    orientation = _normalize_orientation(orientation)
    if orientation == "landscape":
        preview_value = photo.get("preview_path") or ""
        return Path(photo["rgb565_path"]), Path(preview_value) if preview_value else None

    source_type = photo.get("source_type") or "library"
    source_ref = photo.get("source_ref") or photo.get("id") or uuid.uuid4().hex[:12]
    if source_type == "upload":
        return (
            UPLOAD_DIR / f"{source_ref}_portrait_fit.rgb565",
            UPLOAD_DIR / f"{source_ref}_portrait_fit_preview.jpg",
        )
    return (
        BIN_OUTPUT_DIR / f"portrait_fit_{source_ref}.rgb565",
        BIN_OUTPUT_DIR / f"portrait_fit_preview_{source_ref}.jpg",
    )

def _ensure_device_photo_variant(photo: dict, orientation: str) -> tuple[Path, Path | None]:
    orientation = _normalize_orientation(orientation)
    rgb565_path, preview_path = _device_photo_variant_paths(photo, orientation)
    if orientation == "landscape":
        return rgb565_path, preview_path if preview_path and preview_path.exists() else None

    source_path = _resolve_device_photo_source(photo)
    if (
        rgb565_path.exists()
        and preview_path is not None
        and preview_path.exists()
        and (not source_path or rgb565_path.stat().st_mtime >= source_path.stat().st_mtime)
    ):
        return rgb565_path, preview_path
    if source_path and source_path.exists():
        try:
            with Image.open(source_path) as img:
                if preview_path is None:
                    preview_path = rgb565_path.with_suffix(".jpg")
                _write_display_variant(img, orientation, rgb565_path, preview_path)
            return rgb565_path, preview_path
        except Exception as exc:
            print(f"[WARN] render {orientation} variant failed for {photo.get('id')}: {exc}")
    preview_value = photo.get("preview_path") or ""
    return Path(photo["rgb565_path"]), Path(preview_value) if preview_value else None

def _unlink_managed_file(path_value: str | None, roots: list[Path]) -> bool:
    if not path_value:
        return False
    try:
        p = Path(path_value)
        if not p.is_absolute():
            p = (ROOT_DIR / p).resolve()
        else:
            p = p.resolve()
        allowed_roots = [root.resolve() for root in roots]
        if not any(p == root or root in p.parents for root in allowed_roots):
            return False
        if p.exists() and p.is_file():
            p.unlink()
            return True
    except OSError:
        return False
    return False

def _fallback_upload_caption(filename: str | None) -> str:
    return "一张新上传的照片"

def _normalize_orientation(value: str | None) -> str:
    value = (value or "").strip().lower()
    return "portrait" if value in {"portrait", "vertical", "竖屏"} else "landscape"

def _display_size_for_orientation(orientation: str) -> tuple[int, int]:
    lcd_w = int(getattr(cfg, "LCD_WIDTH", 800) or 800)
    lcd_h = int(getattr(cfg, "LCD_HEIGHT", 480) or 480)
    if _normalize_orientation(orientation) == "portrait":
        return min(lcd_w, lcd_h), max(lcd_w, lcd_h)
    return max(lcd_w, lcd_h), min(lcd_w, lcd_h)

def _extract_embedded_portrait_foreground(src: Image.Image, target_aspect: float) -> Image.Image:
    img_w, img_h = src.size
    source_aspect = img_w / img_h
    if source_aspect <= target_aspect * 1.35:
        return src

    gray = src.convert("L")
    pixels = gray.load()
    scores: list[float] = []
    sample_step = 2
    for x in range(1, img_w - 1):
        score = 0
        samples = 0
        for y in range(1, img_h - 1, sample_step):
            score += abs(pixels[x + 1, y] - pixels[x - 1, y])
            score += abs(pixels[x, y + 1] - pixels[x, y - 1])
            samples += 1
        scores.append(score / max(1, samples))

    if not scores:
        return src

    window = max(5, img_w // 90)
    smooth: list[float] = []
    for i in range(len(scores)):
        left = max(0, i - window)
        right = min(len(scores), i + window + 1)
        smooth.append(sum(scores[left:right]) / (right - left))

    sorted_scores = sorted(smooth)
    median = sorted_scores[len(sorted_scores) // 2]
    threshold = max(median * 1.8, max(smooth) * 0.12)

    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, value in enumerate(smooth, start=1):
        if value > threshold and start is None:
            start = i
        if (value <= threshold or i == len(smooth)) and start is not None:
            end = i if value > threshold and i == len(smooth) else i - 1
            if end - start >= img_w * 0.12:
                runs.append((start, end))
            start = None

    if not runs:
        return src

    center_x = img_w / 2
    best = min(runs, key=lambda run: (abs(((run[0] + run[1]) / 2) - center_x), -(run[1] - run[0])))
    left, right = best
    crop_w = right - left
    crop_aspect = crop_w / img_h
    if not (target_aspect * 0.65 <= crop_aspect <= target_aspect * 1.6):
        return src
    if left < img_w * 0.08 or (img_w - right) < img_w * 0.08:
        return src
    if abs(((left + right) / 2) - center_x) > img_w * 0.22:
        return src

    pad = int(crop_w * 0.04)
    left = max(0, left - pad)
    right = min(img_w, right + pad)
    return src.crop((left, 0, right, img_h))

def _render_display_image(img: Image.Image, orientation: str) -> Image.Image:
    target_w, target_h = _display_size_for_orientation(orientation)
    orientation = _normalize_orientation(orientation)
    src = ImageOps.exif_transpose(img).convert("RGB")
    if orientation == "portrait":
        src = _extract_embedded_portrait_foreground(src, target_w / target_h)
    img_w, img_h = src.size
    target_aspect = target_w / target_h
    source_aspect = img_w / img_h

    canvas_img = Image.new("RGB", (target_w, target_h), (0, 0, 0))
    bg_scale = max(target_w / img_w, target_h / img_h)
    bg_w, bg_h = max(1, int(img_w * bg_scale)), max(1, int(img_h * bg_scale))
    img_bg_resized = src.resize((bg_w, bg_h), Image.LANCZOS)
    bg_left = (bg_w - target_w) // 2
    bg_top = (bg_h - target_h) // 2
    img_bg_cropped = img_bg_resized.crop((bg_left, bg_top, bg_left + target_w, bg_top + target_h))
    img_blurred = img_bg_cropped.filter(ImageFilter.GaussianBlur(radius=40))
    canvas_img.paste(img_blurred, (0, 0))

    portrait_like = (
        orientation == "portrait"
        and target_aspect * 0.65 <= source_aspect <= target_aspect * 1.35
    )

    if portrait_like:
        fg_scale = max(target_w / img_w, target_h / img_h)
        fg_w, fg_h = max(1, int(img_w * fg_scale)), max(1, int(img_h * fg_scale))
        img_fg_resized = src.resize((fg_w, fg_h), Image.LANCZOS)
        fg_left = (fg_w - target_w) // 2
        fg_top = (fg_h - target_h) // 2
        canvas_img.paste(
            img_fg_resized.crop((fg_left, fg_top, fg_left + target_w, fg_top + target_h)),
            (0, 0),
        )
    else:
        fg_scale = min(target_w / img_w, target_h / img_h)
        fg_w, fg_h = max(1, int(img_w * fg_scale)), max(1, int(img_h * fg_scale))
        img_fg_resized = src.resize((fg_w, fg_h), Image.LANCZOS)
        fg_left = (target_w - fg_w) // 2
        fg_top = (target_h - fg_h) // 2
        canvas_img.paste(img_fg_resized, (fg_left, fg_top))
    return canvas_img

def _image_to_rgb565_bytes(img: Image.Image) -> bytes:
    pixels = img.load()
    width, height = img.size
    rgb565_data = bytearray(width * height * 2)
    idx = 0
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            val = (((r >> 3) & 0x1F) << 11) | (((g >> 2) & 0x3F) << 5) | ((b >> 3) & 0x1F)
            rgb565_data[idx] = val & 0xFF
            rgb565_data[idx + 1] = (val >> 8) & 0xFF
            idx += 2
    return bytes(rgb565_data)

def _write_display_variant(src_img: Image.Image, orientation: str, rgb565_path: Path, preview_path: Path) -> None:
    rendered = _render_display_image(src_img, orientation)
    rgb565_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    rgb565_path.write_bytes(_image_to_rgb565_bytes(rendered))
    rendered.save(preview_path, "JPEG", quality=85)

def _extract_gemini_text(data: dict) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = (
        candidates[0]
        .get("content", {})
        .get("parts", [])
    )
    return "".join(part.get("text", "") for part in parts).strip()

def _parse_gemini_json(text: str) -> dict:
    value = (text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    try:
        result = json.loads(value)
    except (TypeError, ValueError):
        match = re.search(r"\{.*\}", value, flags=re.DOTALL)
        if not match:
            return {}
        try:
            result = json.loads(match.group(0))
        except (TypeError, ValueError):
            return {}
    return result if isinstance(result, dict) else {}

class GeminiAnalysisError(RuntimeError):
    pass

def _gemini_error_message(resp: requests.Response) -> str:
    try:
        payload = resp.json()
        message = str(payload.get("error", {}).get("message") or "").strip()
    except (TypeError, ValueError):
        message = ""
    if resp.status_code == 429:
        return message or "Gemini 请求过于频繁或免费额度暂时受限"
    if resp.status_code in {401, 403}:
        return message or "Gemini API 密钥无效或没有模型权限"
    if resp.status_code >= 500:
        return message or "Gemini 服务暂时不可用"
    return message or f"Gemini 请求失败（HTTP {resp.status_code}）"

def _analyze_image_with_gemini(
    img: Image.Image,
    *,
    raise_errors: bool = False,
) -> dict:
    if not ENABLE_GEMINI_CAPTION or not GEMINI_API_KEY:
        if raise_errors:
            raise GeminiAnalysisError("Gemini 识图未配置")
        return {}
    try:
        preview = ImageOps.exif_transpose(img).convert("RGB")
        preview.thumbnail((1024, 1024), Image.LANCZOS)
        buf = BytesIO()
        preview.save(buf, format="JPEG", quality=82, optimize=True)
        image_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        prompt = (
            "分析这张电子相册图片，只输出一个JSON对象，不要输出Markdown。"
            "格式为：{\"caption\":\"中文短文案\",\"tags\":[\"标签1\",\"标签2\"]}。"
            "caption要求20字以内、自然简洁，不要出现“这张图片/照片中”等说明腔。"
            "tags提供4到10个便于检索的中文标签，优先包含："
            "可确认的角色名、作品或游戏名、人物或动物、主要物体、场景、风格和主色。"
            "例如确实能确认是琪露诺或《我的世界》时，应分别包含“琪露诺”、"
            "“东方Project”或“我的世界”等具体标签。"
            "只有较有把握时才写具体IP、角色和作品名；无法确认时使用通用视觉标签。"
            "不要根据画面猜测拍摄地点、人物真实身份或具体事件。"
        )
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{GEMINI_VISION_MODEL}:generateContent"
        )
        payload = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                ],
            }],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 256,
                "responseMimeType": "application/json",
            },
        }
        last_error = "Gemini 未返回有效识别结果"
        for attempt in range(2):
            try:
                resp = requests.post(
                    url,
                    params={"key": GEMINI_API_KEY},
                    json=payload,
                    timeout=25,
                )
                if not resp.ok:
                    last_error = _gemini_error_message(resp)
                else:
                    text = _extract_gemini_text(resp.json())
                    result = _parse_gemini_json(text)
                    caption = str(result.get("caption") or "").strip(" \t\r\n\"'“”‘’")[:50]
                    tags = _normalize_tags(result.get("tags"))[:10]
                    if caption or tags:
                        return {"caption": caption, "tags": tags}
                    last_error = "Gemini 返回了空内容或无法解析的 JSON"
            except requests.Timeout:
                last_error = "Gemini 请求超时"
            except requests.RequestException as exc:
                last_error = f"Gemini 网络请求失败: {exc}"
            if attempt == 0:
                time.sleep(1.2)
        if raise_errors:
            raise GeminiAnalysisError(last_error)
        print(f"[WARN] Gemini image analysis failed: {last_error}")
        return {}
    except GeminiAnalysisError:
        raise
    except Exception as exc:
        if raise_errors:
            raise GeminiAnalysisError(f"Gemini 识图异常: {exc}") from exc
        print(f"[WARN] Gemini image analysis failed: {exc}")
        return {}

def _generate_upload_caption_with_gemini(img: Image.Image) -> str | None:
    return _analyze_image_with_gemini(img).get("caption") or None

@app.get("/library")
def device_library_page():
    return render_template("library.html")

@app.get("/api/device-library")
def api_device_library():
    include_hidden = request.args.get("include_hidden") == "1"
    rows = _load_device_photos(
        include_hidden=include_hidden,
        limit=None,
        query=request.args.get("q", ""),
        tag=request.args.get("tag", ""),
        date_from=request.args.get("date_from", ""),
        date_to=request.args.get("date_to", ""),
        source_type=request.args.get("source_type", ""),
    )
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "caption": r.get("caption") or "",
            "uploader_name": r.get("uploader_name") or "",
            "source_type": r.get("source_type") or "",
            "target_date": r.get("target_date") or "",
            "taken_at": r.get("taken_at") or r.get("target_date") or "",
            "city": r.get("city") or "",
            "tags": _normalize_tags(r.get("tags")),
            "tags_text": _serialize_tags(r.get("tags")),
            "semantic_indexed": bool(r.get("embedding_json")),
            "semantic_index_current": bool(
                r.get("embedding_json")
                and r.get("embedding_hash") == _embedding_content_hash(r)
                and r.get("embedding_model") == GEMINI_EMBEDDING_MODEL
            ),
            "created_at": r.get("created_at") or "",
            "hidden": bool(r.get("hidden")),
            "preview_url": f"/api/photo/{r['id']}.jpg",
            "preview_portrait_url": f"/api/photo/{r['id']}.jpg?orientation=portrait",
            "rgb565_url": f"/api/photo/{r['id']}.rgb565",
        })
    return jsonify({
        "photos": result,
        "count": len(result),
        "tag_facets": _device_photo_tag_facets(include_hidden=include_hidden),
        "semantic_indexed_count": sum(
            1 for photo in result if photo["semantic_index_current"]
        ),
    })

@app.post("/api/device-library/update")
def api_device_library_update():
    data = request.json or {}
    photo_id = (data.get("id") or "").strip()
    if not photo_id:
        return jsonify({"ok": False, "error": "missing id"}), 400
    photo = _get_device_photo(photo_id, include_hidden=True)
    if not photo:
        return jsonify({"ok": False, "error": "not found"}), 404

    taken_at = str(data.get("taken_at", photo.get("taken_at") or "") or "").strip()
    if taken_at and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", taken_at):
        return jsonify({"ok": False, "error": "invalid taken_at"}), 400
    city = str(data.get("city", photo.get("city") or "") or "").strip()[:64]

    fields = []
    params = []
    if "caption" in data:
        fields.append("caption = ?")
        params.append(str(data.get("caption") or "")[:256])
    if "city" in data:
        fields.append("city = ?")
        params.append(city)
    if "taken_at" in data:
        fields.append("taken_at = ?")
        params.append(taken_at)
    if any(key in data for key in ("tags", "city", "taken_at")):
        supplied_tags = data.get("tags", photo.get("tags") or "")
        manual_tags = _strip_derived_tags(supplied_tags, photo.get("city") or "")
        fields.append("tags = ?")
        params.append(_build_photo_tags(
            explicit_tags=manual_tags,
            date_value=taken_at,
            city=city,
            source_type=photo.get("source_type") or "",
        ))
    if "hidden" in data:
        fields.append("hidden = ?")
        params.append(1 if data.get("hidden") else 0)
    if not fields:
        return jsonify({"ok": False, "error": "no fields"}), 400
    if any(key in data for key in ("caption", "tags", "city", "taken_at")):
        fields.extend([
            "embedding_json = ''",
            "embedding_hash = ''",
            "embedding_model = ''",
        ])
    params.append(photo_id)
    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    cur = conn.execute(f"UPDATE device_photos SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return jsonify({"ok": cur.rowcount > 0})

@app.post("/api/device-library/analyze")
def api_device_library_analyze():
    data = request.json or {}
    photo_id = (data.get("id") or "").strip()
    if not photo_id:
        return jsonify({"ok": False, "error": "missing id"}), 400
    if not ENABLE_GEMINI_CAPTION or not GEMINI_API_KEY:
        return jsonify({"ok": False, "error": "Gemini 识图未配置"}), 503

    photo = _get_device_photo(photo_id, include_hidden=True)
    if not photo:
        return jsonify({"ok": False, "error": "not found"}), 404
    source_path = _resolve_device_photo_source(photo)
    if not source_path or not source_path.exists():
        return jsonify({"ok": False, "error": "找不到可识别的图片文件"}), 404
    try:
        with Image.open(source_path) as source_image:
            analysis = _analyze_image_with_gemini(source_image, raise_errors=True)
    except GeminiAnalysisError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    except Exception as exc:
        return jsonify({"ok": False, "error": f"无法读取图片: {exc}"}), 400
    if not analysis:
        return jsonify({"ok": False, "error": "Gemini 未返回有效识别结果"}), 502

    manual_tags = _strip_derived_tags(photo.get("tags"), photo.get("city") or "")
    ai_tags = _strip_derived_tags(analysis.get("tags", []))
    merged_tags = _normalize_tags(manual_tags + ai_tags)
    tags = _build_photo_tags(
        explicit_tags=merged_tags,
        date_value=photo.get("taken_at") or photo.get("target_date") or "",
        city=photo.get("city") or "",
        source_type=photo.get("source_type") or "",
    )
    fields = [
        "tags = ?",
        "embedding_json = ''",
        "embedding_hash = ''",
        "embedding_model = ''",
    ]
    params = [tags]
    replace_caption = bool(data.get("replace_caption"))
    suggested_caption = analysis.get("caption") or ""
    if replace_caption and suggested_caption:
        fields.append("caption = ?")
        params.append(suggested_caption)
    params.append(photo_id)

    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    cur = conn.execute(
        f"UPDATE device_photos SET {', '.join(fields)} WHERE id = ?",
        params,
    )
    conn.commit()
    conn.close()
    return jsonify({
        "ok": cur.rowcount > 0,
        "tags": _normalize_tags(tags),
        "tags_text": tags,
        "suggested_caption": suggested_caption,
    })

@app.post("/api/device-library/semantic-index")
def api_device_library_semantic_index():
    data = request.json or {}
    force = bool(data.get("force"))
    photos = _load_device_photos(include_hidden=False, limit=None)
    updated = 0
    skipped = 0
    errors = []
    for photo in photos:
        try:
            if _refresh_photo_embedding(photo, force=force):
                updated += 1
            else:
                skipped += 1
        except GeminiAnalysisError as exc:
            errors.append({"id": photo["id"], "error": str(exc)})
    status = 200 if not errors else (207 if updated or skipped else 502)
    return jsonify({
        "ok": not errors,
        "total": len(photos),
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "model": GEMINI_EMBEDDING_MODEL,
    }), status

@app.get("/api/photo/semantic-search")
def api_photo_semantic_search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"ok": False, "error": "missing q parameter"}), 400
    try:
        limit = max(1, min(int(request.args.get("limit", 5)), 20))
    except ValueError:
        return jsonify({"ok": False, "error": "invalid limit"}), 400
    try:
        min_score = max(0.0, min(float(request.args.get("min_score", 0.55)), 1.0))
        max_score_gap = max(0.02, min(float(request.args.get("max_score_gap", 0.10)), 0.30))
        adjacent_score_gap = max(
            0.02,
            min(float(request.args.get("adjacent_score_gap", 0.06)), 0.20),
        )
    except ValueError:
        return jsonify({"ok": False, "error": "invalid semantic threshold"}), 400

    photos = _load_device_photos(include_hidden=False, limit=None)
    indexed = 0
    for photo in photos:
        try:
            if (
                not photo.get("embedding_json")
                or photo.get("embedding_hash") != _embedding_content_hash(photo)
                or photo.get("embedding_model") != GEMINI_EMBEDDING_MODEL
            ):
                _refresh_photo_embedding(photo)
                indexed += 1
        except GeminiAnalysisError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 502

    try:
        matches = _semantic_search_device_photos(
            query,
            limit=limit,
            min_score=min_score,
            max_score_gap=max_score_gap,
            adjacent_score_gap=adjacent_score_gap,
        )
    except GeminiAnalysisError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    results = [{
        "id": photo["id"],
        "caption": photo.get("caption") or "",
        "tags": _normalize_tags(photo.get("tags")),
        "city": photo.get("city") or "",
        "date": photo.get("taken_at") or photo.get("target_date") or "",
        "score": photo["semantic_score"],
        "preview_url": f"/api/photo/{photo['id']}.jpg",
        "rgb565_url": f"/api/photo/{photo['id']}.rgb565",
    } for photo in matches]

    jumped_to = ""
    if request.args.get("jump") == "1" and results:
        jumped_to = results[0]["id"]
        _queue_device_command({
            "cmd": "switch_photo",
            "target_id": jumped_to,
        })
    return jsonify({
        "ok": True,
        "query": query,
        "count": len(results),
        "indexed": indexed,
        "jumped_to": jumped_to,
        "results": results,
        "model": GEMINI_EMBEDDING_MODEL,
        "min_score": min_score,
        "max_score_gap": max_score_gap,
        "adjacent_score_gap": adjacent_score_gap,
    })

@app.post("/api/device-library/delete")
def api_device_library_delete():
    data = request.json or {}
    photo_id = (data.get("id") or "").strip()
    if not photo_id:
        return jsonify({"ok": False, "error": "missing id"}), 400
    photo = _get_device_photo(photo_id, include_hidden=True)
    if not photo:
        return jsonify({"ok": False, "error": "not found"}), 404

    deleted_files = []
    managed_roots = [UPLOAD_DIR, BIN_OUTPUT_DIR]
    if _unlink_managed_file(photo.get("rgb565_path"), managed_roots):
        deleted_files.append("rgb565")
    if _unlink_managed_file(photo.get("preview_path"), managed_roots):
        deleted_files.append("preview")
    for orientation in ("portrait",):
        variant_rgb565, variant_preview = _device_photo_variant_paths(photo, orientation)
        if _unlink_managed_file(str(variant_rgb565), managed_roots):
            deleted_files.append(f"{orientation}_rgb565")
        if variant_preview and _unlink_managed_file(str(variant_preview), managed_roots):
            deleted_files.append(f"{orientation}_preview")

    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    if photo.get("source_type") == "upload" and photo.get("source_ref"):
        upload_id = photo["source_ref"]
        row = conn.execute(
            "SELECT original_name, rgb565_path FROM upload_photos WHERE id = ?",
            (upload_id,),
        ).fetchone()
        if row:
            original_name, rgb565_path = row
            ext = Path(original_name or "").suffix
            if ext:
                if _unlink_managed_file(str(UPLOAD_DIR / f"{upload_id}{ext}"), [UPLOAD_DIR]):
                    deleted_files.append("original")
            _unlink_managed_file(rgb565_path, [UPLOAD_DIR])
            _unlink_managed_file(str(UPLOAD_DIR / f"{upload_id}_preview.jpg"), [UPLOAD_DIR])
        conn.execute("DELETE FROM upload_photos WHERE id = ?", (upload_id,))

    cur = conn.execute("DELETE FROM device_photos WHERE id = ?", (photo_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": cur.rowcount > 0, "deleted_files": deleted_files})

@app.get("/api/today")
def api_today():
    """
    设备端调用：获取当日待展示的轮播照片列表。
    自动根据当天月日及照片打分进行智能推荐选取。
    """
    today = dt.date.today()
    target_md = f"{today.month:02d}-{today.day:02d}"
    library_has_rows = bool(_load_device_photos(include_hidden=True, limit=1))
    library_photos = _load_device_photos(include_hidden=False, limit=10)
    if library_has_rows:
        tag = (request.args.get("tag") or "").strip().lower()
        if tag:
            library_photos = [
                p for p in library_photos
                if tag in (p.get("caption") or "").lower()
                or tag in (p.get("uploader_name") or "").lower()
                or tag in (p.get("id") or "").lower()
                or tag in (p.get("tags") or "").lower()
                or tag in (p.get("city") or "").lower()
            ]
        return {
            "date": str(today),
            "target_md": target_md,
            "count": len(library_photos),
            "photos": [{
                "id": p["id"],
                "date": p.get("target_date") or str(today),
                "side": p.get("caption") or "",
                "caption": p.get("caption") or "",
                "memory": 100,
                "city": p.get("city") or p.get("uploader_name") or "",
                "lat": 0,
                "lon": 0,
                "tags": _normalize_tags(p.get("tags")),
            } for p in library_photos],
        }
    if not DB_PATH.exists():
        return {"photos": [], "date": str(today), "target_md": target_md}
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    rows = c.execute("""
        SELECT path, exif_json, side_caption, memory_score,
               exif_gps_lat, exif_gps_lon, exif_city, caption
        FROM photo_scores WHERE exif_json IS NOT NULL AND IFNULL(hidden, 0) = 0
    """).fetchall()
    conn.close()

    items = []
    for path, exif_json, side, mem, lat, lon, city, caption in rows:
        date_str = extract_date_from_exif(exif_json)
        if not date_str: continue
        try:
            y, m, d = map(int, date_str.split("-"))
            md = f"{m:02d}-{d:02d}"
            items.append({
                "id": Path(path).stem, "path": str(path), "date": date_str, "md": md,
                "side": side or "", "caption": side or caption or "", "memory": float(mem) if mem else 0,
                "lat": lat, "lon": lon, "city": city or ""
            })
        except Exception: pass

    tag = request.args.get("tag")
    if tag:
        tag_lower = tag.strip().lower()
        matched_items = []
        for it in items:
            c_str = (it["caption"] or "").lower()
            ct_str = (it["city"] or "").lower()
            s_str = (it["side"] or "").lower()
            if tag_lower in c_str or tag_lower in ct_str or tag_lower in s_str:
                matched_items.append(it)
        if matched_items:
            matched_items.sort(key=lambda x: x["memory"], reverse=True)
            chosen = matched_items[:DAILY_PHOTO_QUANTITY]
            return {
                "date": str(today), "target_md": target_md, "count": len(chosen),
                "photos": [{"id": p["id"], "date": p["date"], "side": p["side"], "caption": p["caption"],
                            "memory": p["memory"], "city": p["city"], "lat": p["lat"], "lon": p["lon"]} for p in chosen]
            }

    by_md = {}

    for it in items: by_md.setdefault(it["md"], []).append(it)
    for arr in by_md.values(): arr.sort(key=lambda x: x["memory"], reverse=True)

    def _md_to_doy(md_str):
        m, d = map(int, md_str.split("-"))
        days_before = [0, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
        return days_before[m] + d
    def _doy_to_md(doy):
        base = dt.date(2001, 1, 1) + dt.timedelta(days=doy - 1)
        return f"{base.month:02d}-{base.day:02d}"

    target_doy = _md_to_doy(target_md)
    threshold = float(getattr(cfg, "MEMORY_THRESHOLD", 70.0) or 70.0)
    count = DAILY_PHOTO_QUANTITY

    import random
    chosen = []
    for offset in range(365):
        doy = target_doy - offset
        if doy <= 0: doy += 365
        md = _doy_to_md(doy)
        arr = by_md.get(md, [])
        if not arr: continue
        candidates = [p for p in arr if p["memory"] > threshold]
        if not candidates: continue
        if len(candidates) >= count: chosen = random.sample(candidates, count)
        else: chosen = list(candidates)
        break

    if not chosen:
        sorted_all = sorted(items, key=lambda x: x["memory"], reverse=True)
        chosen = sorted_all[:count]

    return {
        "date": str(today), "target_md": target_md, "count": len(chosen),
        "photos": [{"id": p["id"], "date": p["date"], "side": p["side"], "caption": p["caption"],
                    "memory": p["memory"], "city": p["city"], "lat": p["lat"], "lon": p["lon"]} for p in chosen],
    }

@app.get("/api/photo/search")
def api_photo_search():
    """搜索照片数据库，返回最佳匹配的 JSON 元数据。
    由 voice_server.py 宏解析时调用。"""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "missing q parameter"}), 400

    device_rows = _load_device_photos(query=q, limit=1)
    if device_rows:
        photo = device_rows[0]
        photo_id = photo["id"]
        return jsonify({
            "id": photo_id,
            "caption": photo.get("caption") or "",
            "side_caption": photo.get("caption") or "",
            "date": photo.get("taken_at") or photo.get("target_date") or "",
            "city": photo.get("city") or "",
            "tags": _normalize_tags(photo.get("tags")),
            "source_type": photo.get("source_type") or "",
            "url": f"http://{request.host}/api/photo/{photo_id}.jpg",
            "rgb565_url": f"http://{request.host}/api/photo/{photo_id}.rgb565",
        })

    try:
        photos = _load_device_photos(include_hidden=False, limit=None)
        for photo in photos:
            if (
                not photo.get("embedding_json")
                or photo.get("embedding_hash") != _embedding_content_hash(photo)
                or photo.get("embedding_model") != GEMINI_EMBEDDING_MODEL
            ):
                _refresh_photo_embedding(photo)
        semantic_rows = _semantic_search_device_photos(q, limit=1)
    except GeminiAnalysisError:
        semantic_rows = []
    if semantic_rows:
        photo = semantic_rows[0]
        photo_id = photo["id"]
        return jsonify({
            "id": photo_id,
            "caption": photo.get("caption") or "",
            "side_caption": photo.get("caption") or "",
            "date": photo.get("taken_at") or photo.get("target_date") or "",
            "city": photo.get("city") or "",
            "tags": _normalize_tags(photo.get("tags")),
            "source_type": photo.get("source_type") or "",
            "semantic_score": photo["semantic_score"],
            "url": f"http://{request.host}/api/photo/{photo_id}.jpg",
            "rgb565_url": f"http://{request.host}/api/photo/{photo_id}.rgb565",
        })

    rows, total = load_rows(query=q, page_size=1, sort="memory")
    if rows:
        photo = rows[0]
        photo_id = Path(photo["path"]).stem
        photo["url"] = f"http://{request.host}/api/photo/{photo_id}.jpg"
        return jsonify(photo)
    return jsonify({"error": "no match"}), 404

@app.get("/api/photo/<photo_id>.rgb565")
def api_photo_rgb565(photo_id: str):
    """
    设备端调用：获取指定照片转换后的 RGB565 原始像素点阵文件。
    """
    safe_id = Path(photo_id).name
    orientation = _normalize_orientation(request.args.get("orientation") or DEVICE_STATE.get("desired_orientation"))
    lib_photo = _get_device_photo(safe_id)
    if lib_photo and lib_photo.get("rgb565_path"):
        rgb565_path, _ = _ensure_device_photo_variant(lib_photo, orientation)
        return _send_static_file(rgb565_path)
    p = BIN_OUTPUT_DIR / f"photo_{safe_id}.rgb565"
    if not p.exists(): p = BIN_OUTPUT_DIR / f"{safe_id}.rgb565"
    return _send_static_file(p)

@app.get("/api/photo/<photo_id>.jpg")
def api_photo_jpg(photo_id: str):
    safe_id = Path(photo_id).name
    orientation = _normalize_orientation(request.args.get("orientation") or "landscape")
    lib_photo = _get_device_photo(safe_id, include_hidden=True)
    if lib_photo and lib_photo.get("preview_path"):
        _, preview_path = _ensure_device_photo_variant(lib_photo, orientation)
        if preview_path:
            return _send_static_file(preview_path)
    p = BIN_OUTPUT_DIR / f"preview_{safe_id}.jpg"
    if not p.exists(): p = BIN_OUTPUT_DIR / f"{safe_id}.jpg"
    return _send_static_file(p)

@app.get("/api/upload/check")
def api_upload_check():
    today_str = dt.date.today().isoformat()
    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    row = conn.execute("""
        SELECT id, message, uploader_name, rgb565_path, downloaded
        FROM upload_photos WHERE target_date = ? ORDER BY created_at DESC LIMIT 1
    """, (today_str,)).fetchone()
    conn.close()
    if row is None: return {"has_upload": False}
    sid, message, uploader, rgb565_path, downloaded = row
    return {"has_upload": True, "id": sid, "message": message or "", "uploader_name": uploader or "神秘人", "downloaded": bool(downloaded)}

@app.get("/api/upload/<upload_id>.rgb565")
def api_upload_rgb565(upload_id: str):
    safe_id = Path(upload_id).name
    orientation = _normalize_orientation(request.args.get("orientation") or DEVICE_STATE.get("desired_orientation"))
    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    row = conn.execute("SELECT rgb565_path FROM upload_photos WHERE id = ?", (safe_id,)).fetchone()
    if row is None or not row[0]:
        conn.close()
        abort(404)
    p = Path(row[0])
    lib_photo = _get_device_photo(f"upload_{safe_id}", include_hidden=True)
    if lib_photo:
        p, _ = _ensure_device_photo_variant(lib_photo, orientation)
    conn.execute("UPDATE upload_photos SET downloaded = 1 WHERE id = ?", (safe_id,))
    conn.commit()
    conn.close()
    if not p.exists(): abort(404)
    return send_file(p, mimetype="application/octet-stream", as_attachment=False)

@app.post("/upload/send")
def phone_upload():
    """
    接收手机端或 Web 端的照片上传，进行缩放裁剪和边缘模糊等处理，
    最终生成设备支持的 RGB565 格式供云端下发。
    """
    if UPLOAD_PASSWORD:
        pw = request.form.get("password", "")
        if pw != UPLOAD_PASSWORD: return {"ok": False, "error": "密码错误"}, 403
    file = request.files.get("photo")
    if not file or not file.filename: return {"ok": False, "error": "未选择照片"}, 400
    ext = Path(file.filename).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}:
        return {"ok": False, "error": f"不支持的格式：{ext}"}, 400

    sid = uuid.uuid4().hex[:12]
    safe_name = f"{sid}{ext}"
    orig_path = UPLOAD_DIR / safe_name
    file.save(str(orig_path))

    try:
        img = Image.open(orig_path)
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception: return {"ok": False, "error": "无法解析图片文件"}, 400

    rgb565_path = UPLOAD_DIR / f"{sid}.rgb565"
    preview_path = UPLOAD_DIR / f"{sid}_preview.jpg"
    _write_display_variant(img, "landscape", rgb565_path, preview_path)

    image_analysis = _analyze_image_with_gemini(img)
    user_message = (request.form.get("message", "") or "").strip()[:50]
    message = (
        user_message
        or image_analysis.get("caption")
        or _fallback_upload_caption(file.filename)
    )
    uploader = (request.form.get("name", "") or "").strip()[:20]
    target_date = (request.form.get("target_date", "") or "").strip()
    if not target_date: target_date = dt.date.today().isoformat()

    conn = sqlite3.connect(str(UPLOAD_DB_PATH))
    conn.execute("""
        INSERT INTO upload_photos (id, original_name, rgb565_path, preview_path,
            message, uploader_name, target_date, created_at, downloaded)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'), 0)
    """, (sid, file.filename, str(rgb565_path), f"/api/upload/preview/{sid}_preview.jpg", message, uploader, target_date))
    conn.execute("""
        INSERT INTO device_photos
            (id, rgb565_path, preview_path, caption, uploader_name,
             source_type, source_ref, target_date, created_at, hidden,
             tags, taken_at, city)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'), 0, ?, ?, '')
    """, (
        f"upload_{sid}",
        str(rgb565_path),
        str(preview_path),
        message,
        uploader or "上传",
        "upload",
        sid,
        target_date,
        _build_photo_tags(
            explicit_tags=_strip_derived_tags(image_analysis.get("tags", [])),
            date_value=target_date,
            source_type="upload",
        ),
        target_date,
    ))
    conn.commit()
    conn.close()

    return {"ok": True, "id": sid, "target_date": target_date}

@app.get("/api/upload/preview/<filename>")
def api_upload_preview(filename: str):
    safe_name = Path(filename).name
    p = UPLOAD_DIR / safe_name
    if not p.exists(): abort(404)
    return _send_static_file(p)

# 启动
if __name__ == "__main__":
    print(f"=========================================")
    print(f" 云端电子相册 服务器启动")
    print(f" - WebUI 端口: {FLASK_PORT}")
    print(f" - 设备状态: DEVICE_STATE 已初始化")
    print(f" - DB 挂载: tags, hidden 字段以及 dialog_history 表已更新")
    print(f"=========================================")
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=True, use_reloader=False)
