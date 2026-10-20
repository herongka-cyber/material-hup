"""
缩略图生成服务。

职责：
- 图片：Pillow 生成 WebP 缩略图，最长边 320px，保持宽高比。
- 视频：ffmpeg 抽取中间帧生成 WebP；ffmpeg 不可用时返回 None。
- 音频（bgm/sfx）：Pillow 生成深色音符占位图，WebP。
- 输出统一存 data/thumbnails/{media_id}.webp。
- ensure_thumbnail(media_id) 幂等：文件已存在直接返回路径。

失败策略：
- 任何生成错误都以返回 None 表达（不抛异常），由调用方决定 404 或占位。
- 视频无 ffmpeg / ffprobe、抽帧失败，均返回 None。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from db import database

# 缩略图最长边
_THUMB_MAX_SIDE = 320

# WebP 质量
_WEBP_QUALITY = 80

# ffmpeg 路径探测（缓存结果）
_FFMPEG_PATH: Optional[str] = shutil.which("ffmpeg")

# ffmpeg 抽帧超时（秒）
_FFMPEG_TIMEOUT = 20


# ---------------------------------------------------------------------------
# 路径辅助
# ---------------------------------------------------------------------------

def _thumb_path(media_id: int) -> Path:
    """缩略图文件路径：data/thumbnails/{media_id}.webp"""
    return database.THUMBNAIL_DIR / f"{media_id}.webp"


def _fetch_media_row(media_id: int) -> Optional[dict]:
    """
    从数据库读取生成缩略图所需的字段。

    返回 None 表示素材不存在。
    """
    with database.get_cursor() as cur:
        cur.execute(
            "SELECT id, media_type, file_path, file_name, duration "
            "FROM media WHERE id = ?",
            (media_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return dict(row)


# ---------------------------------------------------------------------------
# 各类型缩略图生成
# ---------------------------------------------------------------------------

def _generate_image_thumbnail(src: Path, dest: Path) -> bool:
    """
    生成图片缩略图。返回是否成功。
    """
    try:
        from PIL import Image
    except Exception:
        return False

    try:
        with Image.open(str(src)) as img:
            # 逐帧图（GIF）取第一帧；Pillow 打开后默认即第一帧
            img.load()
            # 若为 RGBA/P 模式转换为 RGB 以便写 WebP（WebP 支持 alpha，但统一 RGB 更稳）
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            img.thumbnail((_THUMB_MAX_SIDE, _THUMB_MAX_SIDE))
            img.save(str(dest), "WEBP", quality=_WEBP_QUALITY, method=4)
        return True
    except Exception:
        # 清理可能生成的半成品
        try:
            if dest.exists():
                dest.unlink()
        except Exception:
            pass
        return False


def _generate_video_thumbnail(src: Path, dest: Path, duration: Optional[float]) -> bool:
    """
    使用 ffmpeg 抽取视频中间帧生成 WebP 缩略图。返回是否成功。

    ffmpeg 不可用时立即返回 False。
    """
    if _FFMPEG_PATH is None:
        return False

    # 决定抽帧时间点
    if isinstance(duration, (int, float)) and duration > 0:
        ts = max(0.0, float(duration) / 2.0)
    else:
        ts = 0.5  # 时长未知时退化为从 0.5 秒抽帧

    # 使用临时文件写入，成功后 rename，避免读到半成品
    tmp_fd, tmp_name = tempfile.mkstemp(
        suffix=".webp", dir=str(database.THUMBNAIL_DIR)
    )
    # 关闭文件描述符，让 ffmpeg 自己打开写
    try:
        import os
        os.close(tmp_fd)
    except Exception:
        pass
    tmp_path = Path(tmp_name)

    cmd = [
        _FFMPEG_PATH,
        "-y",
        "-ss", f"{ts:.3f}",
        "-i", str(src),
        "-frames:v", "1",
        # 最长边 320，等比缩放；不放大
        "-vf", f"scale='min({_THUMB_MAX_SIDE},iw)':'min({_THUMB_MAX_SIDE},ih)':force_original_aspect_ratio=decrease",
        "-c:v", "libwebp",
        "-quality", str(_WEBP_QUALITY),
        str(tmp_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_FFMPEG_TIMEOUT,
            check=False,
        )
    except Exception:
        try:
            tmp_path.unlink()
        except Exception:
            pass
        return False

    success = (
        proc.returncode == 0
        and tmp_path.exists()
        and tmp_path.stat().st_size > 0
    )

    if not success:
        try:
            tmp_path.unlink()
        except Exception:
            pass
        return False

    try:
        tmp_path.replace(dest)
        return True
    except Exception:
        try:
            tmp_path.unlink()
        except Exception:
            pass
        return False


def _generate_audio_placeholder(dest: Path, media_type: str) -> bool:
    """
    生成音频占位缩略图。

    深色底 + 简单几何音符轮廓，纯 Pillow 绘制，不依赖系统字体。
    bgm 与 sfx 略有区别：bgm 用蓝色调，sfx 用橙色调，便于视觉区分。
    返回是否成功。
    """
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return False

    size = 320
    try:
        img = Image.new("RGB", (size, size), color=(28, 28, 32))
        draw = ImageDraw.Draw(img)

        # 主题色
        if media_type == "bgm":
            accent = (86, 156, 214)   # 蓝
        else:
            accent = (214, 148, 86)   # 橙

        # 五线谱：五条水平细线
        line_color = (60, 60, 68)
        line_top = 120
        line_gap = 18
        for i in range(5):
            y = line_top + i * line_gap
            draw.line([(60, y), (260, y)], fill=line_color, width=2)

        # 八分音符：符头（椭圆） + 符干（竖线） + 符尾（短弧简化为折线）
        note_x = 150
        note_y = 168
        head_w, head_h = 34, 22
        draw.ellipse(
            [note_x - head_w // 2, note_y - head_h // 2,
             note_x + head_w // 2, note_y + head_h // 2],
            fill=accent,
        )
        stem_x = note_x + head_w // 2 - 4
        stem_top = note_y - 70
        draw.line([(stem_x, note_y), (stem_x, stem_top)], fill=accent, width=6)
        # 符尾：从符干顶部向右下一条弧（用两段直线近似）
        draw.line(
            [(stem_x, stem_top), (stem_x + 26, stem_top + 18)],
            fill=accent,
            width=6,
        )
        draw.line(
            [(stem_x + 26, stem_top + 18), (stem_x + 20, stem_top + 40)],
            fill=accent,
            width=6,
        )

        # 底部细横条作为装饰，区分 bgm/sfx
        draw.rectangle([60, 250, 260, 258], fill=accent)

        img.save(str(dest), "WEBP", quality=_WEBP_QUALITY, method=4)
        return True
    except Exception:
        try:
            if dest.exists():
                dest.unlink()
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

def ensure_thumbnail(media_id: int) -> Optional[Path]:
    """
    确保指定素材的缩略图存在。

    返回：
        Path     缩略图文件路径（已存在或刚生成成功）
        None     素材不存在，或生成失败（图片/视频读取失败、ffmpeg 不可用等）

    幂等：目标文件已存在则直接返回路径，不重复生成。
    """
    dest = _thumb_path(media_id)
    if dest.exists() and dest.stat().st_size > 0:
        return dest

    row = _fetch_media_row(media_id)
    if row is None:
        return None

    media_type = row["media_type"]
    file_path_str = row["file_path"]
    if not file_path_str:
        return None

    src = Path(file_path_str)
    if not src.is_file():
        return None

    # 确保缩略图目录存在（M1 已建，但防御性再建一次）
    database.THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)

    ok = False
    if media_type == "image":
        ok = _generate_image_thumbnail(src, dest)
    elif media_type == "video":
        ok = _generate_video_thumbnail(src, dest, row.get("duration"))
    elif media_type in ("bgm", "sfx"):
        ok = _generate_audio_placeholder(dest, media_type)
    else:
        return None

    if not ok:
        return None

    return dest


def is_video_thumbnailing_available() -> bool:
    """供健康检查或前端提示使用：视频缩略图能力是否可用。"""
    return _FFMPEG_PATH is not None