"""
音视频外部工具探测。

优先用系统 PATH 里的 ffmpeg / ffprobe；ffmpeg 找不到时回退到
imageio-ffmpeg 包内置的 ffmpeg 二进制（桌面打包版靠这个保证视频
抽帧可用——用户电脑上通常没有单独安装 ffmpeg）。

ffprobe 没有便携回退：视频元数据在 ffprobe 缺失时由 metadata 模块
改走 ffmpeg -i 的输出解析。
"""

from __future__ import annotations

import shutil
from typing import Optional

_FFMPEG_PATH: Optional[str] = None
_PROBED = False


def find_ffmpeg() -> Optional[str]:
    """返回可用的 ffmpeg 路径；都没有则返回 None。结果缓存。"""
    global _FFMPEG_PATH, _PROBED
    if _PROBED:
        return _FFMPEG_PATH
    _PROBED = True
    _FFMPEG_PATH = shutil.which("ffmpeg")
    if _FFMPEG_PATH is None:
        try:
            import imageio_ffmpeg

            candidate = imageio_ffmpeg.get_ffmpeg_exe()
            if candidate:
                _FFMPEG_PATH = str(candidate)
        except Exception:
            _FFMPEG_PATH = None
    return _FFMPEG_PATH


def find_ffprobe() -> Optional[str]:
    """返回系统 ffprobe 路径（无便携回退）。"""
    return shutil.which("ffprobe")
