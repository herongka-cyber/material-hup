"""
元数据提取服务。

职责：
- 音频（mp3/wav）：mutagen 提取时长、码率、采样率。
- 图片：Pillow 提取宽高。
- 视频：ffprobe 提取时长、码率、宽高；未安装 ffmpeg/ffprobe 时降级返回空值，不抛异常。
- BGM 特征 JSON 索引：加载、按文件名建映射、解析 tags 字段。
- 扩展特征 JSON 串构造（brightness_hz/onset_rate/zcr）。

所有提取函数遵循同一约定：
- 输入为文件路径。
- 返回 dict，键为 media 表对应列名（可只含部分列）。
- 单文件提取失败不抛异常，返回空 dict 或部分字段，由调用方决定是否入库。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from db import models

# ffprobe 路径缓存：启动时探测一次，避免每文件都查 PATH
_FFPROBE_PATH: Optional[str] = shutil.which("ffprobe")

# ffprobe 单次调用超时（秒），防止损坏文件挂死
_FFPROBE_TIMEOUT = 15


# ---------------------------------------------------------------------------
# 音频元数据（mutagen）
# ---------------------------------------------------------------------------

def extract_audio_metadata(file_path: Path) -> dict[str, Any]:
    """
    使用 mutagen 提取音频元数据。

    返回键：duration（秒，float）、bitrate（bps，int）、sample_rate（Hz，int）。
    mutagen 无法解析时返回空 dict。
    """
    result: dict[str, Any] = {}
    try:
        from mutagen import File as MutagenFile
    except Exception:
        # mutagen 未安装或导入异常：返回空，不阻断扫描
        return result

    try:
        audio = MutagenFile(str(file_path))
    except Exception:
        return result

    if audio is None or audio.info is None:
        return result

    info = audio.info

    # 时长：不同格式属性名不同，优先 length
    duration = getattr(info, "length", None)
    if isinstance(duration, (int, float)) and duration > 0:
        result["duration"] = float(duration)

    # 码率：MP3 的 bitrate 单位为 bps；WAV 的 bitrate 属性可能不存在
    bitrate = getattr(info, "bitrate", None)
    if isinstance(bitrate, int) and bitrate > 0:
        result["bitrate"] = bitrate

    # 采样率
    sample_rate = getattr(info, "sample_rate", None)
    if isinstance(sample_rate, int) and sample_rate > 0:
        result["sample_rate"] = sample_rate

    return result


# ---------------------------------------------------------------------------
# 图片元数据（Pillow）
# ---------------------------------------------------------------------------

def extract_image_metadata(file_path: Path) -> dict[str, Any]:
    """
    使用 Pillow 提取图片宽高。

    返回键：width（int）、height（int）。
    打不开的图片返回空 dict。
    """
    result: dict[str, Any] = {}
    try:
        from PIL import Image
    except Exception:
        return result

    try:
        with Image.open(str(file_path)) as img:
            width, height = img.size
            if width > 0 and height > 0:
                result["width"] = int(width)
                result["height"] = int(height)
    except Exception:
        return result

    return result


# ---------------------------------------------------------------------------
# 视频元数据（ffprobe）
# ---------------------------------------------------------------------------

def is_ffprobe_available() -> bool:
    """返回 ffprobe 是否可用。"""
    return _FFPROBE_PATH is not None


def extract_video_metadata(file_path: Path) -> dict[str, Any]:
    """
    使用 ffprobe 提取视频元数据。

    返回键：duration（秒）、bitrate（bps）、width（int）、height（int）。
    ffprobe 未安装或解析失败时返回空 dict，不抛异常。
    """
    result: dict[str, Any] = {}
    if _FFPROBE_PATH is None:
        return result

    cmd = [
        _FFPROBE_PATH,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,bit_rate,duration",
        "-show_entries", "format=duration,bit_rate",
        "-of", "json",
        str(file_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_FFPROBE_TIMEOUT,
            check=False,
        )
    except Exception:
        return result

    if proc.returncode != 0 or not proc.stdout.strip():
        return result

    try:
        payload = json.loads(proc.stdout)
    except Exception:
        return result

    # 优先取 format 层级的 duration / bitrate（更准确）
    fmt = payload.get("format") or {}
    fmt_duration = fmt.get("duration")
    fmt_bitrate = fmt.get("bit_rate")
    if fmt_duration is not None:
        try:
            d = float(fmt_duration)
            if d > 0:
                result["duration"] = d
        except Exception:
            pass
    if fmt_bitrate is not None:
        try:
            b = int(fmt_bitrate)
            if b > 0:
                result["bitrate"] = b
        except Exception:
            pass

    # 宽高从第一路视频流取
    streams = payload.get("streams") or []
    if streams:
        stream = streams[0]
        w = stream.get("width")
        h = stream.get("height")
        if isinstance(w, int) and w > 0:
            result["width"] = w
        if isinstance(h, int) and h > 0:
            result["height"] = h

        # 若 format 层没有 duration，退而取 stream 层
        if "duration" not in result:
            s_dur = stream.get("duration")
            if s_dur is not None:
                try:
                    d = float(s_dur)
                    if d > 0:
                        result["duration"] = d
                except Exception:
                    pass

    return result


# ---------------------------------------------------------------------------
# BGM 特征 JSON 索引
# ---------------------------------------------------------------------------

def load_feature_index(json_path: Path) -> dict[str, dict[str, Any]]:
    """
    加载 BGM 特征索引 JSON，返回 {file_name: record} 映射。

    JSON 顶层为数组，每条记录至少含 file 字段。
    加载失败时返回空 dict（由调用方决定是否告警）。
    """
    index: dict[str, dict[str, Any]] = {}
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return index

    if not isinstance(payload, list):
        return index

    for record in payload:
        if not isinstance(record, dict):
            continue
        file_name = record.get("file")
        if isinstance(file_name, str) and file_name:
            index[file_name] = record

    return index


def extract_bgm_features_from_record(record: dict[str, Any]) -> dict[str, Any]:
    """
    将 BGM 特征 JSON 记录映射为 media 表列。

    映射：
        duration_s → duration
        bpm        → bpm
        key        → musical_key
        energy     → energy（已是 0-1，直接存）
        title      → title
        brightness_hz / onset_rate / zcr → features_json（JSON 串）

    tags 字段（逗号分隔）不在此函数处理，由 scanner 调用 get_record_tags 单独解析。
    返回 dict，仅包含成功解析的字段。
    """
    result: dict[str, Any] = {}

    duration_s = record.get("duration_s")
    if isinstance(duration_s, (int, float)) and duration_s > 0:
        result["duration"] = float(duration_s)

    bpm = record.get("bpm")
    if isinstance(bpm, (int, float)) and bpm > 0:
        result["bpm"] = float(bpm)

    key = record.get("key")
    if isinstance(key, str) and key.strip():
        result["musical_key"] = key.strip()

    energy = record.get("energy")
    if isinstance(energy, (int, float)):
        # 已经是 0-1，直接存；越界则裁剪
        e = float(energy)
        if e < 0.0:
            e = 0.0
        elif e > 1.0:
            e = 1.0
        result["energy"] = e

    title = record.get("title")
    if isinstance(title, str) and title.strip():
        result["title"] = title.strip()

    # 扩展特征
    ext: dict[str, Any] = {}
    for key_name in ("brightness_hz", "onset_rate", "zcr"):
        val = record.get(key_name)
        if isinstance(val, (int, float)):
            ext[key_name] = float(val)
    if ext:
        result["features_json"] = json.dumps(ext, ensure_ascii=False)

    return result


def get_record_tags(record: dict[str, Any]) -> list[str]:
    """
    解析 BGM 特征记录中的 tags 字段（逗号分隔字符串）。

    返回去空白、去重、保序的标签列表。字段缺失或非法时返回空列表。
    """
    raw = record.get("tags")
    if not isinstance(raw, str) or not raw.strip():
        return []

    seen: set[str] = set()
    tags: list[str] = []
    for piece in raw.split(","):
        name = piece.strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        tags.append(name)
    return tags


# ---------------------------------------------------------------------------
# 音效分类推导
# ---------------------------------------------------------------------------

def derive_sfx_category(file_path: Path, scan_root: Path) -> str:
    """
    根据音效文件相对扫描根目录的第一级子目录名推导分类。

    命中 SFX_CATEGORIES 枚举即用，否则返回 SFX_FALLBACK_CATEGORY。
    文件直接位于扫描根目录（无子目录）时同样返回兜底分类。
    """
    try:
        rel = file_path.resolve().relative_to(scan_root.resolve())
    except Exception:
        return models.SFX_FALLBACK_CATEGORY

    parts = rel.parts
    # parts[0] 是文件本身时说明没有子目录
    if len(parts) < 2:
        return models.SFX_FALLBACK_CATEGORY

    subdir = parts[0].strip().lower()
    if subdir in models.SFX_CATEGORIES:
        return subdir
    return models.SFX_FALLBACK_CATEGORY
