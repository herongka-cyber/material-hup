"""
缩略图路由。

端点：
    GET /api/media/{id}/thumbnail

返回 WebP 图片（FileResponse）。
- 素材不存在：404
- 缩略图生成失败：404，detail 说明原因
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from db import database
from services import thumbnail as thumb_service

router = APIRouter(prefix="/api", tags=["thumbnail"])


@router.get("/media/{media_id}/thumbnail", summary="获取素材缩略图")
def get_thumbnail(media_id: int) -> FileResponse:
    """
    返回素材缩略图（image/webp）。

    若缩略图尚未生成，会按需生成后返回；生成失败返回 404 并说明原因。
    """
    # 先判断素材是否存在，与“生成失败”区分：素材不存在走 404 素材不存在，
    # 生成失败走 404 并附原因，两种 detail 不同便于前端区分。
    with database.get_cursor() as cur:
        cur.execute(
            "SELECT media_type FROM media WHERE id = ?",
            (media_id,),
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="素材不存在")

    media_type = row["media_type"]
    path = thumb_service.ensure_thumbnail(media_id)

    if path is None:
        # 按类型给出更具体的原因提示
        if media_type == "video" and not thumb_service.is_video_thumbnailing_available():
            reason = "未检测到 ffmpeg，无法生成视频缩略图，请安装 ffmpeg 后重试"
        elif media_type == "video":
            reason = "视频抽帧失败（文件可能损坏或编码不受支持）"
        elif media_type == "image":
            reason = "图片解码失败（文件可能损坏或格式不支持）"
        else:
            reason = "缩略图生成失败"
        raise HTTPException(status_code=404, detail=reason)

    return FileResponse(
        path=str(path),
        media_type="image/webp",
        filename=f"{media_id}.webp",
    )