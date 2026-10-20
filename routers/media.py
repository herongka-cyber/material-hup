"""
素材查询 API 路由。

端点：
    GET /api/media         素材列表（分页 + 多维筛选 + 排序）
    GET /api/media/{id}    素材详情
    GET /api/tags          标签列表（含关联计数）

设计要点：
- 手写 SQL，参数化查询；排序列、排序方向、媒体类型均用 Literal 白名单校验，
  未知值触发 FastAPI 422。
- 分页使用 COUNT(*) + LIMIT/OFFSET；COUNT 与 SELECT 复用同一份 WHERE 与参数，
  避免重复构造条件引发的不一致。
- 标签过滤支持 match=any（默认，任一命中）与 match=all（全部命中）。
- 列表接口的标签通过一次查询批量获取（WHERE media_id IN (...)），
  避免逐条再查导致的 N+1。
- LIKE 关键词对 % 和 _ 做转义，防止用户输入被当成通配符。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query

from db import database

router = APIRouter(prefix="/api", tags=["media"])


# ---------------------------------------------------------------------------
# 排序字段白名单（与 media 表实际存在的列对应）
# ---------------------------------------------------------------------------

_SORT_SQL: dict[str, str] = {
    "created_at": "media.created_at",
    "duration": "media.duration",
    "bpm": "media.bpm",
    "energy": "media.energy",
    "rating": "media.rating",
    "file_name": "media.file_name",
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _parse_tags(raw: Optional[str]) -> list[str]:
    """
    解析逗号分隔的标签参数。

    去空白、去空串、按大小写不敏感去重、保持首次出现顺序。
    例如 "Calm, Piano,calm,,Warm" → ["Calm", "Piano", "Warm"]。
    """
    if not raw:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for piece in raw.split(","):
        name = piece.strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


def _escape_like(s: str) -> str:
    """转义 LIKE 通配符，使关键词按字面匹配。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_filter_sql(
    media_type: Optional[str],
    sfx_category: Optional[str],
    tag_list: list[str],
    match: str,
    min_rating: Optional[int],
    max_rating: Optional[int],
    min_bpm: Optional[float],
    max_bpm: Optional[float],
    min_energy: Optional[float],
    max_energy: Optional[float],
    min_duration: Optional[float],
    max_duration: Optional[float],
    q: Optional[str],
) -> tuple[str, list[Any]]:
    """
    构造 WHERE 子句及参数列表。

    返回 (" WHERE ..." 或 "", params)。COUNT 与 SELECT 共用，
    保证两处条件与参数完全一致。
    """
    where_parts: list[str] = []
    params: list[Any] = []

    if media_type:
        where_parts.append("media.media_type = ?")
        params.append(media_type)

    if sfx_category:
        # 大小写不敏感匹配；扫描时入库已统一小写，此处兼容调用方传大写
        where_parts.append("LOWER(media.sfx_category) = LOWER(?)")
        params.append(sfx_category)

    # 标签过滤：把命中逻辑放在子查询里，主表条件只用 IN
    if tag_list:
        placeholders = ",".join("?" for _ in tag_list)
        sub_sql = (
            "SELECT mt.media_id FROM media_tags mt "
            "JOIN tags t ON t.id = mt.tag_id "
            f"WHERE t.name COLLATE NOCASE IN ({placeholders})"
        )
        if match == "all":
            # 全部命中：按 media_id 分组后计数去重标签个数 = 请求标签个数
            where_parts.append(
                "media.id IN ("
                f"{sub_sql} "
                "GROUP BY mt.media_id "
                "HAVING COUNT(DISTINCT LOWER(t.name)) = ?)"
            )
            params.extend(tag_list)
            params.append(len(tag_list))
        else:
            # 任一命中
            where_parts.append(f"media.id IN ({sub_sql})")
            params.extend(tag_list)

    if min_rating is not None:
        where_parts.append("media.rating >= ?")
        params.append(min_rating)
    if max_rating is not None:
        where_parts.append("media.rating <= ?")
        params.append(max_rating)

    if min_bpm is not None:
        where_parts.append("media.bpm IS NOT NULL AND media.bpm >= ?")
        params.append(min_bpm)
    if max_bpm is not None:
        where_parts.append("media.bpm IS NOT NULL AND media.bpm <= ?")
        params.append(max_bpm)

    if min_energy is not None:
        where_parts.append("media.energy IS NOT NULL AND media.energy >= ?")
        params.append(min_energy)
    if max_energy is not None:
        where_parts.append("media.energy IS NOT NULL AND media.energy <= ?")
        params.append(max_energy)

    if min_duration is not None:
        where_parts.append("media.duration IS NOT NULL AND media.duration >= ?")
        params.append(min_duration)
    if max_duration is not None:
        where_parts.append("media.duration IS NOT NULL AND media.duration <= ?")
        params.append(max_duration)

    if q:
        # 对 title 和 file_name 都做模糊匹配；SQLite 默认 LIKE 对 ASCII 大小写不敏感
        escaped = _escape_like(q)
        pattern = f"%{escaped}%"
        where_parts.append(
            "(media.file_name LIKE ? ESCAPE '\\' "
            " OR (media.title IS NOT NULL AND media.title LIKE ? ESCAPE '\\'))"
        )
        params.extend([pattern, pattern])

    where_sql = " WHERE " + " AND ".join(where_parts) if where_parts else ""
    return where_sql, params


def _fetch_tags_for(cur, media_ids: list[int]) -> dict[int, list[str]]:
    """
    批量获取多个素材的标签，避免 N+1。

    返回 {media_id: [tag_name, ...]}；无标签的 media 不会出现在返回里。
    """
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


# ---------------------------------------------------------------------------
# 端点：素材列表
# ---------------------------------------------------------------------------

@router.get("/media", summary="素材列表")
def list_media(
    page: int = Query(1, ge=1, description="页码，最小 1"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量，1-100"),
    media_type: Optional[Literal["bgm", "sfx", "image", "video"]] = Query(
        None, description="媒体类型"
    ),
    sfx_category: Optional[str] = Query(None, description="音效分类"),
    tags: Optional[str] = Query(
        None, description="逗号分隔标签；配合 match=any/all 控制匹配方式"
    ),
    match: Literal["any", "all"] = Query(
        "any", description="标签匹配方式：any 任一命中，all 全部命中"
    ),
    min_rating: Optional[int] = Query(None, ge=0, le=5),
    max_rating: Optional[int] = Query(None, ge=0, le=5),
    min_bpm: Optional[float] = Query(None, ge=0),
    max_bpm: Optional[float] = Query(None, ge=0),
    min_energy: Optional[float] = Query(None, ge=0, le=1),
    max_energy: Optional[float] = Query(None, ge=0, le=1),
    min_duration: Optional[float] = Query(None, ge=0),
    max_duration: Optional[float] = Query(None, ge=0),
    q: Optional[str] = Query(
        None, description="关键词，对 file_name 与 title 做 LIKE 模糊匹配"
    ),
    sort: Literal[
        "created_at", "duration", "bpm", "energy", "rating", "file_name"
    ] = Query("created_at", description="排序字段"),
    order: Literal["asc", "desc"] = Query("desc", description="排序方向"),
) -> dict:
    """
    素材列表，支持分页、多维筛选与排序。

    返回：
        {
          "total": int,
          "page": int,
          "page_size": int,
          "items": [ {media 全字段..., "tags": [标签名...]} ]
        }
    """
    tag_list = _parse_tags(tags)

    where_sql, params = _build_filter_sql(
        media_type=media_type,
        sfx_category=sfx_category,
        tag_list=tag_list,
        match=match,
        min_rating=min_rating,
        max_rating=max_rating,
        min_bpm=min_bpm,
        max_bpm=max_bpm,
        min_energy=min_energy,
        max_energy=max_energy,
        min_duration=min_duration,
        max_duration=max_duration,
        q=q,
    )

    # sort / order 已由 Literal 白名单校验，拼接安全
    order_sql = f"ORDER BY {_SORT_SQL[sort]} {order.upper()}"

    with database.get_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS c FROM media{where_sql}", params)
        total = int(cur.fetchone()["c"])

        offset = (page - 1) * page_size
        cur.execute(
            f"SELECT media.* FROM media{where_sql} {order_sql} LIMIT ? OFFSET ?",
            params + [page_size, offset],
        )
        rows = cur.fetchall()
        items = [dict(r) for r in rows]

        # 一次查询批量取标签，组装到 items 里
        ids = [int(item["id"]) for item in items]
        tag_map = _fetch_tags_for(cur, ids)

    for item in items:
        item["tags"] = tag_map.get(int(item["id"]), [])

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }


# ---------------------------------------------------------------------------
# 端点：素材详情
# ---------------------------------------------------------------------------

@router.get("/media/{media_id}", summary="素材详情")
def get_media(media_id: int) -> dict:
    """
    按 id 查询单个素材的全字段 + tags。

    不存在返回 404。
    """
    with database.get_cursor() as cur:
        cur.execute("SELECT * FROM media WHERE id = ?", (media_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="素材不存在")

        item = dict(row)
        tag_map = _fetch_tags_for(cur, [media_id])

    item["tags"] = tag_map.get(media_id, [])
    return item


# ---------------------------------------------------------------------------
# 端点：标签列表
# ---------------------------------------------------------------------------

@router.get("/tags", summary="标签列表")
def list_tags(
    q: Optional[str] = Query(None, description="标签名模糊过滤"),
) -> list[dict]:
    """
    返回被至少一个素材关联的标签，按关联素材数降序。

    结构：[{"name": "Calm", "count": 5}, ...]
    """
    where_sql = ""
    params: list[Any] = []
    if q:
        escaped = _escape_like(q)
        where_sql = " WHERE t.name LIKE ? ESCAPE '\\'"
        params.append(f"%{escaped}%")

    with database.get_cursor() as cur:
        cur.execute(
            f"SELECT t.name AS name, COUNT(mt.media_id) AS cnt "
            f"FROM tags t "
            f"JOIN media_tags mt ON mt.tag_id = t.id "
            f"{where_sql} "
            f"GROUP BY t.id "
            f"ORDER BY cnt DESC, t.name COLLATE NOCASE ASC",
            params,
        )
        rows = cur.fetchall()

    return [{"name": r["name"], "count": int(r["cnt"])} for r in rows]