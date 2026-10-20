"""
歌单/项目篮 API 路由。

端点：
    POST   /api/playlists                                      新建歌单
    GET    /api/playlists                                      歌单列表（含 item_count）
    GET    /api/playlists/{playlist_id}                        歌单详情（items 平铺 media 全字段 + tags）
    PATCH  /api/playlists/{playlist_id}                        修改歌单名/描述
    DELETE /api/playlists/{playlist_id}                        删除歌单（条目随 FK 级联删除）
    POST   /api/playlists/{playlist_id}/items                  加条目（幂等）
    DELETE /api/playlists/{playlist_id}/items/{media_id}       删条目
    PUT    /api/playlists/{playlist_id}/items/order            重排

设计要点：
- 加条目幂等：UNIQUE 冲突时返回现有条目，不覆盖 note、不改 sort_order。
- 重排严格校验：请求 order 集合与现有 media_id 集合完全一致，否则 422。
- 详情接口 items 平铺 media 全字段 + tags + sort_order + note；
  条目主键字段名用 item_id，避免与 media.id 冲突。
- 所有写操作显式事务，异常回滚。
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from db import database

router = APIRouter(prefix="/api", tags=["playlist"])


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class PlaylistCreate(BaseModel):
    """POST /api/playlists 请求体。"""
    name: str = Field(..., min_length=1, description="歌单名，非空")
    description: Optional[str] = Field(None, description="可选描述")

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name 不能为空白")
        return v.strip()


class PlaylistPatch(BaseModel):
    """PATCH /api/playlists/{id} 请求体。至少一个字段。"""
    name: Optional[str] = Field(None, description="新歌单名，非空字符串")
    description: Optional[str] = Field(None, description="新描述；null 表示清空")

    @model_validator(mode="after")
    def _check(self) -> "PlaylistPatch":
        if not self.model_fields_set:
            raise ValueError("请求体不能为空，至少提供 name 或 description 之一")
        if "name" in self.model_fields_set:
            if self.name is None:
                raise ValueError("name 不能为 null")
            stripped = self.name.strip()
            if not stripped:
                raise ValueError("name 不能为空白")
            # 归一化后回写，后续端点直接使用
            self.name = stripped
        return self


class PlaylistItemAdd(BaseModel):
    """POST /api/playlists/{id}/items 请求体。"""
    media_id: int = Field(..., description="要加入的素材 id")
    note: Optional[str] = Field(None, description="可选备注")


class PlaylistReorder(BaseModel):
    """PUT /api/playlists/{id}/items/order 请求体。"""
    order: list[int] = Field(
        ..., description="media_id 数组，按数组顺序重写 sort_order 为 0,1,2,..."
    )


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _ensure_playlist_exists(cur, playlist_id: int) -> None:
    """歌单不存在则 404。"""
    cur.execute("SELECT 1 FROM playlists WHERE id = ?", (playlist_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="歌单不存在")


def _ensure_media_exists(cur, media_id: int) -> None:
    """素材不存在则 404。"""
    cur.execute("SELECT 1 FROM media WHERE id = ?", (media_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="素材不存在")


def _fetch_tags_for_batch(cur, media_ids: list[int]) -> dict[int, list[str]]:
    """批量取标签，避免 N+1。返回 {media_id: [tag_name, ...]}。"""
    if not media_ids:
        return {}
    placeholders = ",".join("?" for _ in media_ids)
    cur.execute(
        f"SELECT mt.media_id AS media_id, t.name AS name "
        f"FROM media_tags mt "
        f"JOIN tags t ON t.id = mt.tag_id "
        f"WHERE mt.media_id IN ({placeholders}) "
        f"ORDER BY t.name COLLATE NOCASE",
        media_ids,
    )
    result: dict[int, list[str]] = {}
    for row in cur.fetchall():
        mid = int(row["media_id"])
        result.setdefault(mid, []).append(row["name"])
    return result


def _fetch_playlist_basic(cur, playlist_id: int) -> dict[str, Any]:
    """
    查询歌单主字段 + item_count。

    不存在时抛 404。
    """
    cur.execute(
        "SELECT p.id, p.name, p.description, p.created_at, "
        "(SELECT COUNT(*) FROM playlist_items pi WHERE pi.playlist_id = p.id) AS item_count "
        "FROM playlists p WHERE p.id = ?",
        (playlist_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="歌单不存在")
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "description": row["description"],
        "created_at": row["created_at"],
        "item_count": int(row["item_count"]),
    }


def _fetch_playlist_items(cur, playlist_id: int) -> list[dict[str, Any]]:
    """
    查询歌单的条目（平铺 media 全字段 + sort_order + note + tags）。

    字段冲突处理：
        playlist_items.id  → item_id
        media.id            → id
    """
    cur.execute(
        "SELECT pi.id AS item_id, pi.sort_order, pi.note, pi.media_id, m.* "
        "FROM playlist_items pi "
        "JOIN media m ON m.id = pi.media_id "
        "WHERE pi.playlist_id = ? "
        "ORDER BY pi.sort_order ASC, pi.id ASC",
        (playlist_id,),
    )
    rows = cur.fetchall()
    items = [dict(r) for r in rows]

    media_ids = [int(item["id"]) for item in items]
    tag_map = _fetch_tags_for_batch(cur, media_ids)
    for item in items:
        item["tags"] = tag_map.get(int(item["id"]), [])
    return items


def _fetch_playlist_detail(cur, playlist_id: int) -> dict[str, Any]:
    """详情 = 主字段 + items 数组。"""
    basic = _fetch_playlist_basic(cur, playlist_id)
    basic["items"] = _fetch_playlist_items(cur, playlist_id)
    return basic


def _fetch_single_item(cur, playlist_id: int, media_id: int) -> Optional[dict[str, Any]]:
    """
    查询歌单中的单条目（用于加条目幂等返回）。

    结构同 _fetch_playlist_items 的单项。
    """
    cur.execute(
        "SELECT pi.id AS item_id, pi.sort_order, pi.note, pi.media_id, m.* "
        "FROM playlist_items pi "
        "JOIN media m ON m.id = pi.media_id "
        "WHERE pi.playlist_id = ? AND pi.media_id = ?",
        (playlist_id, media_id),
    )
    row = cur.fetchone()
    if row is None:
        return None
    item = dict(row)
    tag_map = _fetch_tags_for_batch(cur, [int(item["id"])])
    item["tags"] = tag_map.get(int(item["id"]), [])
    return item


# ---------------------------------------------------------------------------
# 端点：歌单 CRUD
# ---------------------------------------------------------------------------

@router.post("/playlists", summary="新建歌单")
def create_playlist(payload: PlaylistCreate) -> dict:
    """新建歌单，返回歌单对象（含 item_count=0）。"""
    conn = database.get_conn()
    try:
        conn.execute("BEGIN;")
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO playlists(name, description) VALUES (?, ?)",
            (payload.name, payload.description),
        )
        playlist_id = int(cur.lastrowid)
        result = _fetch_playlist_basic(cur, playlist_id)
        conn.execute("COMMIT;")
        return result
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


@router.get("/playlists", summary="歌单列表")
def list_playlists() -> list[dict]:
    """歌单列表，按 created_at 倒序；每个歌单含 item_count。"""
    with database.get_cursor() as cur:
        cur.execute(
            "SELECT p.id, p.name, p.description, p.created_at, "
            "(SELECT COUNT(*) FROM playlist_items pi WHERE pi.playlist_id = p.id) AS item_count "
            "FROM playlists p "
            "ORDER BY p.created_at DESC, p.id DESC"
        )
        rows = cur.fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["name"],
            "description": r["description"],
            "created_at": r["created_at"],
            "item_count": int(r["item_count"]),
        }
        for r in rows
    ]


@router.get("/playlists/{playlist_id}", summary="歌单详情")
def get_playlist(playlist_id: int) -> dict:
    """
    歌单详情：主字段 + items 数组。

    每个 item 平铺 media 全字段 + tags + sort_order + note；
    playlist_item 主键字段名为 item_id。
    """
    with database.get_cursor() as cur:
        return _fetch_playlist_detail(cur, playlist_id)


@router.patch("/playlists/{playlist_id}", summary="修改歌单")
def patch_playlist(playlist_id: int, payload: PlaylistPatch) -> dict:
    """PATCH 语义：只更新请求体出现的字段。成功返回更新后的完整歌单。"""
    set_parts: list[str] = []
    params: list[Any] = []

    if "name" in payload.model_fields_set:
        set_parts.append("name = ?")
        params.append(payload.name)

    if "description" in payload.model_fields_set:
        set_parts.append("description = ?")
        params.append(payload.description)

    if not set_parts:
        # PlaylistPatch 已在 model_validator 层拦截，这里是双保险
        raise HTTPException(status_code=422, detail="没有可更新的字段")

    conn = database.get_conn()
    try:
        conn.execute("BEGIN;")
        cur = conn.cursor()
        _ensure_playlist_exists(cur, playlist_id)

        set_sql = ", ".join(set_parts)
        cur.execute(
            f"UPDATE playlists SET {set_sql} WHERE id = ?",
            params + [playlist_id],
        )

        result = _fetch_playlist_detail(cur, playlist_id)
        conn.execute("COMMIT;")
        return result
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


@router.delete("/playlists/{playlist_id}", summary="删除歌单")
def delete_playlist(playlist_id: int) -> dict:
    """
    删除歌单。条目随 FK ON DELETE CASCADE 自动清理。
    返回 {"deleted": true, "playlist_id": ...}。
    """
    conn = database.get_conn()
    try:
        conn.execute("BEGIN;")
        cur = conn.cursor()
        _ensure_playlist_exists(cur, playlist_id)
        cur.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        conn.execute("COMMIT;")
        return {"deleted": True, "playlist_id": playlist_id}
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
# 端点：条目管理
# ---------------------------------------------------------------------------

@router.post("/playlists/{playlist_id}/items", summary="加条目")
def add_item(playlist_id: int, payload: PlaylistItemAdd) -> dict:
    """
    向歌单追加条目。

    - 歌单不存在 404
    - media_id 不存在 404
    - UNIQUE 冲突（已在歌单中）：幂等返回现有条目，不覆盖 note、不改 sort_order
    - 正常追加：sort_order = MAX(existing)+1

    返回该条目（平铺 media 全字段 + tags + sort_order + note）。
    """
    conn = database.get_conn()
    try:
        conn.execute("BEGIN;")
        cur = conn.cursor()

        _ensure_playlist_exists(cur, playlist_id)
        _ensure_media_exists(cur, payload.media_id)

        # 幂等判断：已存在则直接返回现有条目
        existing = _fetch_single_item(cur, playlist_id, payload.media_id)
        if existing is not None:
            conn.execute("COMMIT;")
            return existing

        # 追加：MAX(sort_order)+1
        cur.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m "
            "FROM playlist_items WHERE playlist_id = ?",
            (playlist_id,),
        )
        next_sort = int(cur.fetchone()["m"]) + 1

        cur.execute(
            "INSERT INTO playlist_items(playlist_id, media_id, sort_order, note) "
            "VALUES (?, ?, ?, ?)",
            (playlist_id, payload.media_id, next_sort, payload.note),
        )

        item = _fetch_single_item(cur, playlist_id, payload.media_id)
        conn.execute("COMMIT;")
        # _fetch_single_item 在事务内查询到的是新插入的行，理论上不可能为 None
        if item is None:
            raise HTTPException(status_code=500, detail="条目写入后查询失败")
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


@router.delete(
    "/playlists/{playlist_id}/items/{media_id}", summary="删除条目"
)
def remove_item(playlist_id: int, media_id: int) -> dict:
    """
    从歌单中移除条目。

    歌单不存在 404；条目不存在（该歌单里没有这个 media_id）404。
    返回 {"deleted": true, "playlist_id": ..., "media_id": ...}。
    """
    conn = database.get_conn()
    try:
        conn.execute("BEGIN;")
        cur = conn.cursor()

        _ensure_playlist_exists(cur, playlist_id)

        cur.execute(
            "SELECT 1 FROM playlist_items WHERE playlist_id = ? AND media_id = ?",
            (playlist_id, media_id),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="条目不存在")

        cur.execute(
            "DELETE FROM playlist_items WHERE playlist_id = ? AND media_id = ?",
            (playlist_id, media_id),
        )
        conn.execute("COMMIT;")
        return {"deleted": True, "playlist_id": playlist_id, "media_id": media_id}
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


@router.put("/playlists/{playlist_id}/items/order", summary="重排条目")
def reorder_items(playlist_id: int, payload: PlaylistReorder) -> dict:
    """
    按请求 order 数组顺序重写 sort_order 为 0,1,2,...

    严格校验：order 集合必须与现有 media_id 集合完全一致。
    - 多传 / 少传 / 有重复 → 422
    - 歌单不存在 404

    返回重排后的歌单详情（含 items）。
    """
    conn = database.get_conn()
    try:
        conn.execute("BEGIN;")
        cur = conn.cursor()

        _ensure_playlist_exists(cur, playlist_id)

        # 取现有 media_id 列表
        cur.execute(
            "SELECT media_id FROM playlist_items WHERE playlist_id = ?",
            (playlist_id,),
        )
        existing_ids = sorted(int(r["media_id"]) for r in cur.fetchall())
        requested_ids = sorted(int(x) for x in payload.order)

        # 长度相等 + 排序后相等 ⇒ 集合完全相同（UNIQUE 保证 existing 无重复）
        # 若请求含重复，长度会先不等（因为现有无重复）
        if existing_ids != requested_ids:
            raise HTTPException(
                status_code=422,
                detail="order 数组必须与歌单现有条目集合完全一致"
                "（不允许新增、缺失或重复）",
            )

        for idx, mid in enumerate(payload.order):
            cur.execute(
                "UPDATE playlist_items SET sort_order = ? "
                "WHERE playlist_id = ? AND media_id = ?",
                (idx, playlist_id, int(mid)),
            )

        result = _fetch_playlist_detail(cur, playlist_id)
        conn.execute("COMMIT;")
        return result
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