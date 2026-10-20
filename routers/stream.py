"""
流式文件传输路由。

端点：
    GET /api/media/{id}/stream

支持 HTTP Range：
    bytes=0-1023       前 1024 字节
    bytes=1024-        从 1024 到文件尾
    bytes=-500         最后 500 字节

行为：
- 无 Range 头：200，返回全文件
- 有合法 Range：206，Content-Range: bytes start-end/total，Accept-Ranges: bytes
- Range 语法非法或 start>=文件大小：416，Content-Range: bytes */total

安全：
- 文件路径只从数据库取，不接受客户端传入路径，杜绝路径穿越。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from db import database

router = APIRouter(prefix="/api", tags=["stream"])

# 分块大小：64KB，兼顾吞吐与内存
_CHUNK_SIZE = 64 * 1024

# 扩展名 → Content-Type 映射
_EXT_CONTENT_TYPE: dict[str, str] = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "mkv": "video/x-matroska",
    "webm": "video/webm",
    "avi": "video/x-msvideo",
}

# Range 语法：只允许 "bytes=" 前缀 + 单段，不支持多段
_RANGE_RE = re.compile(r"^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$", re.IGNORECASE)


def _guess_content_type(file_ext: str) -> str:
    """按扩展名返回 Content-Type；未知时返回 application/octet-stream。"""
    return _EXT_CONTENT_TYPE.get(file_ext.lower(), "application/octet-stream")


def _fetch_media_row(media_id: int) -> Optional[dict]:
    """查询素材的 file_path/file_ext；不存在返回 None。"""
    with database.get_cursor() as cur:
        cur.execute(
            "SELECT file_path, file_ext FROM media WHERE id = ?",
            (media_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return dict(row)


def _iter_file(path: Path, start: int, length: int):
    """
    生成器：从 start 起读取 length 字节，分块 yield。

    不把整文件读进内存。
    """
    with open(path, "rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            chunk = f.read(min(_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _parse_range(range_header: str, file_size: int) -> Optional[tuple[int, int]]:
    """
    解析 Range 头，返回 (start, end) 闭区间。非法返回 None。

    支持三种形式：
        bytes=0-1023    → (0, 1023)
        bytes=1024-     → (1024, file_size-1)
        bytes=-500      → (file_size-500, file_size-1)

    边界处理：
        - start >= file_size：非法，返回 None
        - end >= file_size：裁剪到 file_size-1
        - start > end：非法，返回 None
        - 后缀长度 <= 0 或 > file_size：非法，返回 None
    """
    match = _RANGE_RE.match(range_header)
    if not match:
        return None
    start_str, end_str = match.group(1), match.group(2)

    # 两个都为空："bytes=-" 非法
    if not start_str and not end_str:
        return None

    # suffix：bytes=-N，最后 N 字节
    if not start_str and end_str:
        try:
            suffix_len = int(end_str)
        except ValueError:
            return None
        if suffix_len <= 0 or suffix_len > file_size:
            return None
        start = file_size - suffix_len
        end = file_size - 1
        return start, end

    # 普通形式：bytes=start-end 或 bytes=start-
    try:
        start = int(start_str)
    except ValueError:
        return None
    if start < 0 or start >= file_size:
        return None

    if end_str:
        try:
            end = int(end_str)
        except ValueError:
            return None
        if end < start:
            return None
        if end >= file_size:
            end = file_size - 1
    else:
        end = file_size - 1

    return start, end


@router.get("/media/{media_id}/stream", summary="流式获取素材文件")
def stream_media(media_id: int, request: Request) -> Response:
    """
    返回素材文件内容，支持 HTTP Range。

    - 无 Range 头：200 + 全文件
    - 合法 Range：206 + Content-Range
    - 非法 Range：416 + Content-Range: bytes */total
    """
    row = _fetch_media_row(media_id)
    if row is None:
        raise HTTPException(status_code=404, detail="素材不存在")

    file_path_str = row.get("file_path")
    if not file_path_str:
        raise HTTPException(status_code=404, detail="素材文件路径为空")

    file_path = Path(file_path_str)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="素材文件不存在于磁盘")

    file_size = file_path.stat().st_size
    content_type = _guess_content_type(row.get("file_ext") or file_path.suffix.lstrip("."))

    range_header = request.headers.get("range")

    # 无 Range：整文件
    if not range_header:
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        }
        return StreamingResponse(
            _iter_file(file_path, 0, file_size),
            status_code=200,
            media_type=content_type,
            headers=headers,
        )

    parsed = _parse_range(range_header, file_size)
    if parsed is None:
        # 416：Content-Range 用 "bytes */total"
        return Response(
            status_code=416,
            headers={
                "Content-Range": f"bytes */{file_size}",
                "Accept-Ranges": "bytes",
            },
        )

    start, end = parsed
    length = end - start + 1

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    return StreamingResponse(
        _iter_file(file_path, start, length),
        status_code=206,
        media_type=content_type,
        headers=headers,
    )