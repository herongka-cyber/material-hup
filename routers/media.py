"""
素材查询 API 路由。

端点：
    GET /api/media         素材列表（分页 + 多维筛选 + 排序）
    GET /api/media/{id}    素材详情
    GET /api/tags          标签列表（含关联计数）

设计要点：
- 手写 SQL，参数化查询；排序列、排序方向、媒体类型均用 Literal 白名单校验。
- 分页使用 COUNT(*) + LIMIT/OFFSET；COUNT 与 SELECT 复用同一份 WHERE 与参数。
- 标签过滤支持 match=any（默认，任一命中）与 match=all（全部命中）。
- LIKE 关键词对 % 和 _ 做转义。
- q 关键词按空白拆词，每词必须命中（AND），且匹配时把下划线/连字符视同空格。
- favorite=true 时只返回 is_favorite=1 的素材；不传或 false 时行为不变。
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import sqlite3
import tempfile
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Response

from db import database

router = APIRouter(prefix="/api", tags=["media"])

APP_VERSION = "v2.48"


# ---------------------------------------------------------------------------
# 排序字段白名单
# ---------------------------------------------------------------------------

_SORT_SQL: dict[str, str] = {
    "created_at": "media.created_at",
    "duration": "media.duration",
    "bpm": "media.bpm",
    "energy": "media.energy",
    "rating": "media.rating",
    "file_name": "media.file_name",
    "file_size": "media.file_size",
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _parse_tags(raw: Optional[str]) -> list[str]:
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
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _split_query_terms(q: Optional[str]) -> list[str]:
    """
    把关键词按空白拆成词列表；单词语义与旧实现一致（只有一个词时列表长度为 1）。
    过滤空串。
    """
    if not q:
        return []
    parts = re.split(r"\s+", q.strip())
    return [p for p in parts if p]


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
    favorite: Optional[bool] = None,
    untagged: Optional[bool] = None,
    unrated: Optional[bool] = None,
) -> tuple[str, list[Any]]:
    where_parts: list[str] = []
    params: list[Any] = []

    if media_type:
        where_parts.append("media.media_type = ?")
        params.append(media_type)

    if sfx_category:
        where_parts.append("LOWER(media.sfx_category) = LOWER(?)")
        params.append(sfx_category)

    if tag_list:
        placeholders = ",".join("?" for _ in tag_list)
        sub_sql = (
            "SELECT mt.media_id FROM media_tags mt "
            "JOIN tags t ON t.id = mt.tag_id "
            f"WHERE t.name COLLATE NOCASE IN ({placeholders})"
        )
        if match == "all":
            where_parts.append(
                "media.id IN ("
                f"{sub_sql} "
                "GROUP BY mt.media_id "
                "HAVING COUNT(DISTINCT LOWER(t.name)) = ?)"
            )
            params.extend(tag_list)
            params.append(len(tag_list))
        else:
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

    # 关键词：拆词 + AND + 下划线/连字符视同空格
    terms = _split_query_terms(q)
    if terms:
        for term in terms:
            pattern = f"%{_escape_like(term)}%"
            where_parts.append(
                "("
                "REPLACE(REPLACE(media.file_name, '_', ' '), '-', ' ') LIKE ? ESCAPE '\\' "
                "OR (media.title IS NOT NULL AND "
                "    REPLACE(REPLACE(media.title, '_', ' '), '-', ' ') LIKE ? ESCAPE '\\')"
                ")"
            )
            params.extend([pattern, pattern])

    # 收藏过滤：只有显式 true 时生效
    if favorite:
        where_parts.append("media.is_favorite = 1")

    # 整理状态过滤：未打标签 / 未评分，方便批量补整理
    if untagged:
        where_parts.append(
            "NOT EXISTS (SELECT 1 FROM media_tags mt WHERE mt.media_id = media.id)"
        )
    if unrated:
        where_parts.append("(media.rating IS NULL OR media.rating = 0)")

    where_sql = " WHERE " + " AND ".join(where_parts) if where_parts else ""
    return where_sql, params


def _fetch_tags_for(cur, media_ids: list[int]) -> dict[int, list[str]]:
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
        None,
        description="关键词；空格拆词后每词必须命中，匹配时下划线/连字符视同空格",
    ),
    favorite: Optional[bool] = Query(
        None,
        description="favorite=true 时只返回已收藏素材；不传或 false 时行为不变",
    ),
    untagged: Optional[bool] = Query(
        None,
        description="untagged=true 时只返回未打任何标签的素材",
    ),
    unrated: Optional[bool] = Query(
        None,
        description="unrated=true 时只返回未评分（rating 为 0）的素材",
    ),
    sort: Literal[
        "created_at", "duration", "bpm", "energy", "rating", "file_name", "file_size",
        "random",
    ] = Query("created_at", description="排序字段"),
    order: Literal["asc", "desc"] = Query("desc", description="排序方向"),
    seed: int = Query(1, ge=0, description="随机排序种子；同一种子分页顺序稳定、不重不漏"),
) -> dict:
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
        favorite=favorite,
        untagged=untagged,
        unrated=unrated,
    )

    # 随机排序不能用 ORDER BY RANDOM()：每页重洗会让分页重复/漏项。
    # 改用 id 的确定性伪随机散列（同种子同顺序），分页稳定、不重不漏。
    if sort == "random":
        order_sql = "ORDER BY ((media.id * 2654435761 + ? * 40503) % 1000003) ASC, media.id ASC"
        select_extra: list[Any] = [seed]
    else:
        order_sql = f"ORDER BY {_SORT_SQL[sort]} {order.upper()}"
        select_extra = []

    with database.get_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS c FROM media{where_sql}", params)
        total = int(cur.fetchone()["c"])

        offset = (page - 1) * page_size
        cur.execute(
            f"SELECT media.* FROM media{where_sql} {order_sql} LIMIT ? OFFSET ?",
            params + select_extra + [page_size, offset],
        )
        rows = cur.fetchall()
        items = [dict(r) for r in rows]

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
# 端点：失效素材（源文件已不存在）
# 注意：必须声明在 /media/{media_id} 之前，否则 "missing" 会被当作 id 捕获。
# ---------------------------------------------------------------------------

def _fetch_missing_media(cur) -> list[dict]:
    cur.execute("SELECT id, file_path, file_name, media_type FROM media ORDER BY id")
    rows = [dict(r) for r in cur.fetchall()]
    return [r for r in rows if not os.path.exists(r["file_path"])]


@router.get("/media/missing", summary="源文件已丢失的素材列表")
def list_missing_media() -> dict:
    with database.get_cursor() as cur:
        missing = _fetch_missing_media(cur)
    return {"total": len(missing), "items": missing}


@router.post("/media/missing/purge", summary="删除全部失效素材的记录")
def purge_missing_media() -> dict:
    """只删数据库记录（源文件本就不存在）；连带清歌单条目与标签关联。"""
    with database.get_cursor() as cur:
        ids = [r["id"] for r in _fetch_missing_media(cur)]
        if ids:
            ph = ",".join("?" for _ in ids)
            cur.execute(f"DELETE FROM playlist_items WHERE media_id IN ({ph})", ids)
            cur.execute(f"DELETE FROM media_tags WHERE media_id IN ({ph})", ids)
            cur.execute(f"DELETE FROM media WHERE id IN ({ph})", ids)
    return {"removed": len(ids)}


# ---------------------------------------------------------------------------
# 端点：导出素材清单（CSV）
# 注意：必须声明在 /media/{media_id} 之前，否则 "export.csv" 会被当作 id 捕获。
# ---------------------------------------------------------------------------

@router.get("/media/export.csv", summary="导出全部素材清单为 CSV")
def export_media_csv() -> Response:
    """全库清单一张表：给剪映/Excel 对照盘点用。带 BOM，Excel 打开不乱码。"""
    with database.get_cursor() as cur:
        cur.execute(
            "SELECT id, file_name, title, media_type, duration, file_size, bpm, "
            "musical_key, energy, rating, is_favorite, file_path, created_at "
            "FROM media ORDER BY id"
        )
        rows = [dict(r) for r in cur.fetchall()]
        tags_map = _fetch_tags_for(cur, [r["id"] for r in rows]) if rows else {}

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "文件名", "标题", "类型", "时长(秒)", "大小(字节)", "BPM",
                "调性", "能量", "评分", "收藏", "标签", "路径", "入库时间"])
    type_names = {"bgm": "BGM", "sfx": "音效", "image": "图片", "video": "视频"}
    for r in rows:
        w.writerow([
            r["id"], r["file_name"], r["title"] or "", type_names.get(r["media_type"], r["media_type"]),
            round(float(r["duration"]), 1) if r["duration"] is not None else "",
            r["file_size"] if r["file_size"] is not None else "",
            r["bpm"] if r["bpm"] is not None else "",
            r["musical_key"] or "", r["energy"] if r["energy"] is not None else "",
            r["rating"] or "", "是" if r["is_favorite"] else "",
            "、".join(tags_map.get(r["id"], [])), r["file_path"], r["created_at"] or "",
        ])
    data = "\ufeff" + buf.getvalue()
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="material_list.csv"'},
    )


# ---------------------------------------------------------------------------
# 端点：关于（版本与数据位置）
# ---------------------------------------------------------------------------

@router.get("/about", summary="关于本软件")
def get_about() -> dict:
    return {
        "app": "素材管家 Material Hub",
        "version": APP_VERSION,
        "db_path": str(database.DB_PATH),
        "data_dir": str(database.DATA_DIR),
    }


# ---------------------------------------------------------------------------
# 端点：数据库备份下载
# ---------------------------------------------------------------------------

@router.get("/backup.db", summary="下载数据库备份")
def download_backup() -> Response:
    """用 SQLite 在线备份接口拍一份一致性快照再下载，不直接拷正在写的文件。"""
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        src = sqlite3.connect(str(database.DB_PATH))
        dst = sqlite3.connect(tmp_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with open(tmp_path, "rb") as f:
            data = f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    fname = f"material_hub_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.db"
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ---------------------------------------------------------------------------
# 端点：重复素材检测
# 注意：必须声明在 /media/{media_id} 之前，否则 "duplicates" 会被当作 id 捕获。
# ---------------------------------------------------------------------------

_HASH_CACHE: dict = {}


def _file_content_hash(path: str, size: int) -> Optional[str]:
    """文件内容 SHA-256（按 路径+大小+修改时间 缓存，避免每次全库重算）。

    注意不能用「大小+时长」判重：恒定码率的 MP3 文件大小只由时长决定，
    不同的歌同长就会同大小，会误报。内容哈希才靠得住。
    文件不存在或读不了返回 None（失效素材归清理功能管，不进重复名单）。
    """
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _HASH_CACHE.get(path)
    if hit and hit[0] == size and hit[1] == mtime:
        return hit[2]
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
    except OSError:
        return None
    digest = h.hexdigest()
    _HASH_CACHE[path] = (size, mtime, digest)
    return digest


@router.get("/media/duplicates", summary="重复素材检测")
def list_duplicate_media() -> dict:
    """按文件内容哈希找真重复：同类型、字节数相同且内容逐字节一致才算一组。

    同一个文件被复制、改名、换文件夹再扫描进来都躲不过；
    内容不同的文件哪怕同名同大小也不会误报。本端点只读，
    删记录走 DELETE /api/media/{id}。
    """
    with database.get_cursor() as cur:
        cur.execute(
            "SELECT id, file_path, file_name, title, media_type, file_size, duration "
            "FROM media WHERE file_size IS NOT NULL AND file_size > 0 ORDER BY id"
        )
        rows = [dict(r) for r in cur.fetchall()]

    groups_map: dict = {}
    for r in rows:
        digest = _file_content_hash(r["file_path"], int(r["file_size"]))
        if digest is None:
            continue
        key = (r["media_type"], int(r["file_size"]), digest)
        groups_map.setdefault(key, []).append(r)

    groups = []
    total_items = 0
    extra_items = 0
    wasted_bytes = 0
    for (media_type, file_size, _digest), items in groups_map.items():
        if len(items) < 2:
            continue
        dur = items[0]["duration"]
        groups.append({
            "media_type": media_type,
            "file_size": file_size,
            "duration": round(float(dur), 1) if dur is not None else None,
            "count": len(items),
            "items": items,
        })
        total_items += len(items)
        extra_items += len(items) - 1
        wasted_bytes += file_size * (len(items) - 1)

    groups.sort(key=lambda g: (-g["count"], -g["file_size"]))
    return {
        "total_groups": len(groups),
        "total_items": total_items,
        "extra_items": extra_items,
        "wasted_bytes": wasted_bytes,
        "groups": groups,
    }


# ---------------------------------------------------------------------------
# 端点：素材库统计
# ---------------------------------------------------------------------------

@router.get("/stats", summary="素材库统计")
def get_stats() -> dict:
    with database.get_cursor() as cur:
        cur.execute(
            "SELECT media_type, COUNT(*) AS c, "
            "COALESCE(SUM(duration), 0) AS dur, "
            "COALESCE(SUM(file_size), 0) AS size "
            "FROM media GROUP BY media_type"
        )
        by_type = {}
        total = 0
        total_duration = 0.0
        total_size = 0
        for r in cur.fetchall():
            by_type[r["media_type"]] = {
                "count": int(r["c"]),
                "duration_sec": float(r["dur"]),
                "size_bytes": int(r["size"]),
            }
            total += int(r["c"])
            total_duration += float(r["dur"])
            total_size += int(r["size"])

        cur.execute("SELECT COUNT(*) AS c FROM media WHERE is_favorite = 1")
        favorites = int(cur.fetchone()["c"])

        cur.execute("SELECT COUNT(*) AS c FROM playlists")
        playlists = int(cur.fetchone()["c"])

        cur.execute("SELECT COUNT(*) AS c FROM playlist_items")
        playlist_items = int(cur.fetchone()["c"])

        cur.execute("SELECT AVG(rating) AS a FROM media WHERE rating > 0")
        avg_row = cur.fetchone()
        avg_rating = round(float(avg_row["a"]), 2) if avg_row and avg_row["a"] is not None else None

        cur.execute(
            "SELECT t.name AS name, COUNT(mt.media_id) AS c "
            "FROM tags t JOIN media_tags mt ON mt.tag_id = t.id "
            "GROUP BY t.id ORDER BY c DESC, t.name COLLATE NOCASE ASC LIMIT 5"
        )
        top_tags = [{"name": r["name"], "count": int(r["c"])} for r in cur.fetchall()]

        cur.execute("SELECT rating, COUNT(*) AS c FROM media GROUP BY rating")
        by_rating = {str(i): 0 for i in range(6)}
        for r in cur.fetchall():
            k = str(int(r["rating"] or 0))
            if k in by_rating:
                by_rating[k] = int(r["c"])

        cur.execute(
            "SELECT "
            "SUM(CASE WHEN duration IS NULL THEN 1 ELSE 0 END) AS nodur, "
            "SUM(CASE WHEN duration < 30 THEN 1 ELSE 0 END) AS lt30, "
            "SUM(CASE WHEN duration >= 30 AND duration < 120 THEN 1 ELSE 0 END) AS b30_120, "
            "SUM(CASE WHEN duration >= 120 AND duration < 300 THEN 1 ELSE 0 END) AS b120_300, "
            "SUM(CASE WHEN duration >= 300 THEN 1 ELSE 0 END) AS ge300 "
            "FROM media"
        )
        dr = cur.fetchone()
        by_duration = {
            "lt30": int(dr["lt30"] or 0),
            "b30_120": int(dr["b30_120"] or 0),
            "b120_300": int(dr["b120_300"] or 0),
            "ge300": int(dr["ge300"] or 0),
            "nodur": int(dr["nodur"] or 0),
        }

    return {
        "total": total,
        "by_type": by_type,
        "total_duration_sec": round(total_duration, 1),
        "total_size_bytes": total_size,
        "favorites": favorites,
        "playlists": playlists,
        "playlist_items": playlist_items,
        "avg_rating": avg_rating,
        "top_tags": top_tags,
        "by_rating": by_rating,
        "by_duration": by_duration,
    }


# ---------------------------------------------------------------------------
# 端点：素材详情
# ---------------------------------------------------------------------------

@router.get("/media/{media_id}", summary="素材详情")
def get_media(media_id: int) -> dict:
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
# 端点：相似素材
# ---------------------------------------------------------------------------

@router.get("/media/{media_id}/similar", summary="与该素材相似的其他素材")
def get_similar_media(
    media_id: int,
    limit: int = Query(6, ge=1, le=20, description="返回条数上限"),
) -> dict:
    """同类型里挑最像的几条：BGM 按 BPM 接近程度，音效优先同分类，其余按最新入库。

    找同风格配乐时不用回列表里翻。只读，不改任何数据。
    """
    with database.get_cursor() as cur:
        cur.execute("SELECT * FROM media WHERE id = ?", (media_id,))
        src = cur.fetchone()
        if src is None:
            raise HTTPException(status_code=404, detail="素材不存在")
        src = dict(src)

        fields = ("id, file_name, title, media_type, duration, bpm, musical_key, "
                  "sfx_category, file_size, rating, is_favorite")
        if src["media_type"] == "bgm" and src["bpm"] is not None:
            sql = (f"SELECT {fields} FROM media WHERE media_type = 'bgm' AND id != ? "
                   "ORDER BY (bpm IS NULL) ASC, ABS(bpm - ?) ASC, id ASC LIMIT ?")
            params: list[Any] = [media_id, float(src["bpm"]), limit]
        elif src["media_type"] == "sfx" and src["sfx_category"]:
            sql = (f"SELECT {fields} FROM media WHERE media_type = 'sfx' AND id != ? "
                   "ORDER BY (sfx_category = ?) DESC, id ASC LIMIT ?")
            params = [media_id, src["sfx_category"], limit]
        else:
            sql = (f"SELECT {fields} FROM media WHERE media_type = ? AND id != ? "
                   "ORDER BY created_at DESC, id DESC LIMIT ?")
            params = [src["media_type"], media_id, limit]
        cur.execute(sql, params)
        items = [dict(r) for r in cur.fetchall()]
        tag_map = _fetch_tags_for(cur, [it["id"] for it in items]) if items else {}

    for it in items:
        it["tags"] = tag_map.get(it["id"], [])
    return {"source_id": media_id, "items": items}


# ---------------------------------------------------------------------------
# 端点：标签列表
# ---------------------------------------------------------------------------

@router.get("/tags", summary="标签列表")
def list_tags(
    q: Optional[str] = Query(None, description="标签名模糊过滤"),
) -> list[dict]:
    where_sql = ""
    params: list[Any] = []
    if q:
        escaped = _escape_like(q)
        where_sql = " WHERE t.name LIKE ? ESCAPE '\\'"
        params.append(f"%{escaped}%")

    with database.get_cursor() as cur:
        cur.execute(
            f"SELECT t.id AS id, t.name AS name, COUNT(mt.media_id) AS cnt "
            f"FROM tags t "
            f"JOIN media_tags mt ON mt.tag_id = t.id "
            f"{where_sql} "
            f"GROUP BY t.id "
            f"ORDER BY cnt DESC, t.name COLLATE NOCASE ASC",
            params,
        )
        rows = cur.fetchall()

    return [{"id": int(r["id"]), "name": r["name"], "count": int(r["cnt"])} for r in rows]