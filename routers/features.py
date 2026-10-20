"""
BGM 特征补全路由。

端点：
    POST /api/features/backfill             启动补全任务
    GET  /api/features/backfill/{task_id}   查询补全进度

设计：
- 与 /api/scan 平行，任务状态字段结构相同，前端渲染逻辑可复用。
- 已有任务运行中时 POST 返回 409，避免两个补全任务同时跑导致 CPU/IO 竞争。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services import audio_features

router = APIRouter(prefix="/api/features", tags=["features"])


@router.post("/backfill", summary="启动 BGM 特征补全")
def start_backfill() -> dict:
    """
    启动 BGM 特征补全任务。

    遍历所有 media_type='bgm' 的记录：
        - bpm 已有 → skipped+1（不重复分析）
        - bpm 缺失 → librosa 分析并写回 bpm/musical_key/energy/features_json

    返回：{"task_id": "..."}
    若已有任务在运行：409 Conflict
    """
    task_id, err = audio_features.start_backfill_in_thread()
    if err is not None:
        raise HTTPException(status_code=409, detail=err)
    return {"task_id": task_id}


@router.get("/backfill/{task_id}", summary="查询补全任务进度")
def get_backfill_status(task_id: str) -> dict:
    """
    查询补全任务状态。

    返回：
        {
          "task_id": "...",
          "status": "running" | "done" | "error",
          "total": 500,             # 全部 bgm 数量
          "processed": 120,         # 已处理（含跳过）
          "skipped": 80,            # 已有 bpm 跳过
          "analyzed": 38,           # 分析成功写库
          "failed": 2,              # 分析失败
          "current_file": "...",
          "errors": [...],
          "error_message": "",
          "started_at": ...,
          "finished_at": ...
        }
    """
    task = audio_features.get_backfill_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task