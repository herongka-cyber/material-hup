"""
扫描 API 路由。

端点：
    POST /api/scan/start  启动扫描，返回 task_id
    GET  /api/scan/status/{task_id}  查询扫描状态
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from db import models
from services import scanner

router = APIRouter(prefix="/api/scan", tags=["scan"])


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class ScanEntry(BaseModel):
    """单个扫描目录条目。"""
    path: str = Field(..., description="目录路径（绝对路径或含 ~ 的路径）")
    media_type: str = Field(..., description="媒体类型，必须是四种枚举之一")

    @field_validator("media_type")
    @classmethod
    def _validate_media_type(cls, v: str) -> str:
        if v not in models.MEDIA_TYPES:
            raise ValueError(
                f"media_type 非法: {v!r}，允许值: {list(models.MEDIA_TYPES)}"
            )
        return v

    @field_validator("path")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("path 不能为空")
        return v.strip()


class ScanStartRequest(BaseModel):
    """扫描启动请求体。"""
    entries: list[ScanEntry] = Field(
        ..., min_length=1, description="扫描目录列表，至少一项"
    )
    feature_index: Optional[str] = Field(
        None,
        description="可选 BGM 特征 JSON 索引文件路径；提供时按文件名匹配写入特征",
    )


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------

@router.post("/start", summary="启动扫描")
def start_scan(payload: ScanStartRequest) -> dict:
    """
    启动异步扫描任务。

    请求体示例：
        {
          "entries": [
            {"path": "/素材/BGM", "media_type": "bgm"},
            {"path": "/素材/音效", "media_type": "sfx"}
          ],
          "feature_index": "/素材/bgm_features.json"
        }

    返回：{"task_id": "..."}
    """
    entries = [e.model_dump() for e in payload.entries]
    try:
        task_id = scanner.start_scan_in_thread(
            scan_entries=entries,
            feature_index_path=payload.feature_index,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"扫描启动失败: {exc}")
    return {"task_id": task_id}


@router.get("/status/{task_id}", summary="查询扫描状态")
def scan_status(task_id: str) -> dict:
    """
    查询扫描任务状态。

    返回：
        {
          "task_id": "...",
          "status": "running" | "done" | "error",
          "total": 1500,
          "processed": 320,
          "current_file": "/素材/BGM/xxx.mp3",
          "skipped": 0,
          "inserted": 320,
          "updated": 0,
          "errors": [...],
          "error_message": ""
        }
    """
    task = scanner.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task
