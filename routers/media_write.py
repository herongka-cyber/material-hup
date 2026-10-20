"""
素材写操作 API 路由。

端点：
    PATCH  /api/media/{id}          更新评分 / 收藏（部分更新）
    POST   /api/media/{id}/tags     添加标签
    DELETE /api/media/{id}/tags     删除标签关联

设计要点：
- 所有写操作走显式事务：get_conn() + BEGIN/COMMIT/ROLLBACK，保证原子性。
- 参数化查询，不使用字符串拼接传值（排序等无用户值的场景除外）。
- PATCH 采用“只更新请求体出现的字段”语义：Pydantic 的 model_fields_set
  用于判定请求中是否真的传了某字段（区分“显式传 null”与“未传”）。
- 全部字段都未传时通过 model_validator 主动抛错，触发 FastAPI 422。
- 标签增删只动 media_tags 关联表，不删除 tags 表中可能被其他素材复用的行。
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from db import database

router = APIRouter(prefix="/api", tags=["media-write"])


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class MediaPatchRequest(BaseModel):
    """
    PATCH /api/media/{id} 请求体。

    两个字段均可选，但至少传一个；都未传由 model_validator 抛错触发 422。
    rating 用 Field 约束 0-5 整数；is_favorite 用布尔类型。
    """
    rating: Optional[int] = Field(
        None, ge=0, le=5, description="评分 0-5，整数"
    )
    is_favorite: Optional[bool] = Field(
        None, description="是否收藏"
    )

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "MediaPatchRequest":
        # model_fields_set 只包含“请求体里实际出现的字段”
        # 因此 "{}" 会被拦下，而 {"rating": null} 视为已传但值非法交给 Field 校验
        if not self.model_fields_set:
            raise ValueError("请求体不能为空，至少提供 rating 或 is_favorite 之一")
        return self


class TagsAddRequest(BaseModel):
    """POST /api/media/{id}/tags 请求体。"""
    tags: list[str] = Field(..., min_length=1, description="要添加的标签列表")


class TagsRemoveRequest(BaseModel):
    """DELETE /api/media/{id}/tags 请求体。"""
    tags: list[str] = Field(..., min_length=1, description="要移除的标签列表")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _normalize_tags(raw: list[str]) -> list[str]:
    """
    标签归一化：去空白、去空串、大小写不敏感去重、保序。

    与 routers/media.py 中 _parse_tags 的语义一致，但输入是 list 而非逗号串。
    """
    seen: set[str] = set()
    result: list[str] = []
    for piece in raw:
        if not isinstance(piece, str):
            continue
        name = piece.strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


def _ensure_media_exists(cur, media_id: int) -> None:
    """素材不存在则抛 404。"""
    cur.execute("SELECT 1 FROM media WHERE id = ?", (media_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="素材不存在")


def _fetch_tags_for(cur, media_id: int) -> list[str]:
    """
    获取单个素材的标签列表（按名称升序，大小写不敏感）。

    这里只查一个 id，不复用 M3 的批量函数，避免跨模块导入带来的耦合。
    """
    cur.execute(
        "SELECT t.name AS name "
        "FROM media_tags mt "
        "JOIN tags t ON t.id = mt.tag_id "
        "WHERE mt.media_id = ? "
        "ORDER BY t.name COLLATE NOCASE",
        (media_id,),
    )
    return [row["name"] for row in cur.fetchall()]


def _fetch_media_full(cur, media_id: int) -> dict[str, Any]:
    """获取素材全字段 + tags；不存在抛 404。"""
    cur.execute("SELECT * FROM media WHERE id = ?", (media_id,))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    item = dict(row)
    item["tags"] = _fetch_tags_for(cur, media_id)
    return item


def _upsert_tag(cur, name: str) -> int:
    """
    插入或获取标签 id。返回 tag_id。

    使用 COLLATE NOCASE 查询，避免因大小写差异重复插入；
    tags.name 上有 UNIQUE COLLATE NOCASE 约束，并发下 INSERT 也不会重复。
    """
    cur.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,))
    row = cur.fetchone()
    if row is not None:
        return int(row["id"])
    cur.execute("INSERT INTO tags(name) VALUES (?)", (name,))
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# 端点：更新评分 / 收藏
# ---------------------------------------------------------------------------

@router.patch("/media/{media_id}", summary="更新评分或收藏")
def patch_media(media_id: int, payload: MediaPatchRequest) -> dict:
    """
    部分更新素材的 rating 和 is_favorite。

    只更新请求体里出现的字段（PATCH 语义）。
    成功返回更新后的完整记录（含 tags）。
    """
    # 构造需要更新的列；model_fields_set 保证只包含请求体出现的字段
    set_parts: list[str] = []
    params: list[Any] = []

    if "rating" in payload.model_fields_set:
        # Pydantic 已用 ge=0/le=5 校验；显式传 null 时 Field 约束会先失败，
        # 走到这里 payload.rating 一定是 0-5 的整数
        if payload.rating is None:
            raise HTTPException(status_code=422, detail="rating 不能为 null")
        set_parts.append("rating = ?")
        params.append(int(payload.rating))

    if "is_favorite" in payload.model_fields_set:
        if payload.is_favorite is None:
            raise HTTPException(status_code=422, detail="is_favorite 不能为 null")
        set_parts.append("is_favorite = ?")
        params.append(1 if payload.is_favorite else 0)

    if not set_parts:
        # 理论上 model_validator 已拦下，这里是双保险
        raise HTTPException(status_code=422, detail="没有可更新的字段")

    conn = database.get_conn()
    try:
        conn.execute("BEGIN;")
        cur = conn.cursor()

        _ensure_media_exists(cur, media_id)

        set_sql = ", ".join(set_parts)
        cur.execute(
            f"UPDATE media SET {set_sql} WHERE id = ?",
            params + [media_id],
        )

        item = _fetch_media_full(cur, media_id)
        conn.execute("COMMIT;")
        return item
    except HTTPException:
        if conn.in_transaction:
            conn.execute("ROLLBACK;")
        raise
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 端点：添加标签
# ---------------------------------------------------------------------------

@router.post("/media/{media_id}/tags", summary="为素材添加标签")
def add_tags(media_id: int, payload: TagsAddRequest) -> dict:
    """
    为素材添加标签。标签不存在则新建，已存在则复用。
    关联使用 INSERT OR IGNORE，重复添加不会报错。
    返回 {"media_id": ..., "tags": [最新标签列表]}。
    """
    tag_list = _normalize_tags(payload.tags)
    if not tag_list:
        raise HTTPException(status_code=422, detail="标签列表不能全为空白")

    conn = database.get_conn()
    try:
        conn.execute("BEGIN;")
        cur = conn.cursor()

        _ensure_media_exists(cur, media_id)

        for name in tag_list:
            tag_id = _upsert_tag(cur, name)
            cur.execute(
                "INSERT OR IGNORE INTO media_tags(media_id, tag_id) VALUES (?, ?)",
                (media_id, tag_id),
            )

        latest = _fetch_tags_for(cur, media_id)
        conn.execute("COMMIT;")
        return {"media_id": media_id, "tags": latest}
    except HTTPException:
        if conn.in_transaction:
            conn.execute("ROLLBACK;")
        raise
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 端点：删除标签
# ---------------------------------------------------------------------------

@router.delete("/media/{media_id}/tags", summary="移除素材的标签关联")
def remove_tags(media_id: int, payload: TagsRemoveRequest) -> dict:
    """
    移除素材上的标签关联。

    只删除 media_tags 关联行，不删除 tags 表本身——同名标签可能被其他素材复用。
    返回 {"media_id": ..., "tags": [最新标签列表]}。
    """
    tag_list = _normalize_tags(payload.tags)
    if not tag_list:
        raise HTTPException(status_code=422, detail="标签列表不能全为空白")

    conn = database.get_conn()
    try:
        conn.execute("BEGIN;")
        cur = conn.cursor()

        _ensure_media_exists(cur, media_id)

        # 逐个标签按名字（NOCASE）查出 tag_id 后删除关联；
        # 若标签在 tags 表中不存在，则跳过（无关联可删）
        placeholders = ",".join("?" for _ in tag_list)
        cur.execute(
            f"SELECT id FROM tags WHERE name COLLATE NOCASE IN ({placeholders})",
            tag_list,
        )
        tag_ids = [int(r["id"]) for r in cur.fetchall()]

        for tid in tag_ids:
            cur.execute(
                "DELETE FROM media_tags WHERE media_id = ? AND tag_id = ?",
                (media_id, tid),
            )

        latest = _fetch_tags_for(cur, media_id)
        conn.execute("COMMIT;")
        return {"media_id": media_id, "tags": latest}
    except HTTPException:
        if conn.in_transaction:
            conn.execute("ROLLBACK;")
        raise
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()