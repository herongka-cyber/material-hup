"""
素材写操作 API 路由。

端点：
    PATCH  /api/media/{id}          更新评分 / 收藏 / 标题（部分更新）
    POST   /api/media/batch         批量更新收藏状态/评分
    POST   /api/media/{id}/tags     添加标签
    DELETE /api/media/{id}/tags     删除标签关联

设计要点：
- 所有写操作走显式事务：get_conn() + BEGIN/COMMIT/ROLLBACK，保证原子性。
- 参数化查询；排序等无用户值场景除外。
- PATCH 语义：只更新请求体里出现的字段（model_fields_set 判定）。
- 至少一个字段出现在请求体，否则 422。
- 全字段都传 null 时显式 422（不做"null 视为未传"的隐式处理）。
- title 传非空字符串（去首尾空格后）→ 更新；传空串或纯空白 → 置 NULL（恢复派生名）。
- 标签增删只动 media_tags 关联表，不删除 tags 表中可能被其他素材复用的行。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from db import database

from routers.playlist import write_manifest_csv

router = APIRouter(prefix="/api", tags=["media-write"])


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class MediaPatchRequest(BaseModel):
    """
    PATCH /api/media/{id} 请求体。

    三个字段均可选，但至少传一个；全不传由 model_validator 抛错触发 422。
    """
    rating: Optional[int] = Field(
        None, ge=0, le=5, description="评分 0-5，整数"
    )
    is_favorite: Optional[bool] = Field(
        None, description="是否收藏"
    )
    title: Optional[str] = Field(
        None,
        description="自定义标题。非空字符串（去首尾空格后）→ 更新；"
                    "空字符串或纯空白 → 清除自定义标题（恢复按文件名派生显示）；"
                    "null → 422",
    )

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "MediaPatchRequest":
        if not self.model_fields_set:
            raise ValueError(
                "请求体不能为空，至少提供 rating / is_favorite / title 之一"
            )
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
    cur.execute("SELECT 1 FROM media WHERE id = ?", (media_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="素材不存在")


def _fetch_tags_for(cur, media_id: int) -> list[str]:
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
    cur.execute("SELECT * FROM media WHERE id = ?", (media_id,))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    item = dict(row)
    item["tags"] = _fetch_tags_for(cur, media_id)
    return item


def _upsert_tag(cur, name: str) -> int:
    cur.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,))
    row = cur.fetchone()
    if row is not None:
        return int(row["id"])
    cur.execute("INSERT INTO tags(name) VALUES (?)", (name,))
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# 端点：更新评分 / 收藏 / 标题
# ---------------------------------------------------------------------------

@router.patch("/media/{media_id}", summary="更新评分、收藏或标题")
def patch_media(media_id: int, payload: MediaPatchRequest) -> dict:
    """
    部分更新素材的 rating / is_favorite / title。

    只更新请求体里出现的字段。title 传空串或纯空白 → 清除自定义标题。
    成功返回更新后的完整记录（含 tags）。
    """
    set_parts: list[str] = []
    params: list[Any] = []

    if "rating" in payload.model_fields_set:
        if payload.rating is None:
            raise HTTPException(status_code=422, detail="rating 不能为 null")
        set_parts.append("rating = ?")
        params.append(int(payload.rating))

    if "is_favorite" in payload.model_fields_set:
        if payload.is_favorite is None:
            raise HTTPException(status_code=422, detail="is_favorite 不能为 null")
        set_parts.append("is_favorite = ?")
        params.append(1 if payload.is_favorite else 0)

    if "title" in payload.model_fields_set:
        if payload.title is None:
            raise HTTPException(status_code=422, detail="title 不能为 null")
        stripped = payload.title.strip()
        if stripped:
            set_parts.append("title = ?")
            params.append(stripped)
        else:
            # 空串或纯空白 → 清除自定义标题（置 NULL，前端派生名回落到文件名规则）
            set_parts.append("title = NULL")

    if not set_parts:
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
# 端点：删除单条素材记录
# ---------------------------------------------------------------------------

@router.delete("/media/{media_id}", summary="删除一条素材记录")
def delete_media(media_id: int) -> dict:
    """只删数据库记录与关联（歌单条目、标签关联），不删源文件。"""
    conn = database.get_conn()
    try:
        conn.execute("BEGIN;")
        cur = conn.cursor()
        cur.execute("SELECT id FROM media WHERE id = ?", (media_id,))
        if cur.fetchone() is None:
            conn.execute("ROLLBACK;")
            raise HTTPException(status_code=404, detail="素材不存在")
        cur.execute("DELETE FROM playlist_items WHERE media_id = ?", (media_id,))
        cur.execute("DELETE FROM media_tags WHERE media_id = ?", (media_id,))
        cur.execute("DELETE FROM media WHERE id = ?", (media_id,))
        conn.execute("COMMIT;")
        return {"deleted": media_id}
    except HTTPException:
        raise
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 端点：重命名源文件
# ---------------------------------------------------------------------------

class MediaRenameRequest(BaseModel):
    """PATCH /api/media/{id}/rename 请求体：新的文件名（可不带扩展名）。"""

    new_name: str = Field(..., min_length=1, max_length=200)


@router.patch("/media/{media_id}/rename", summary="重命名源文件")
def rename_media_file(media_id: int, payload: MediaRenameRequest) -> dict:
    """把磁盘上的源文件改名并同步数据库记录。

    规则：不能带路径分隔符；不写扩展名时自动沿用原扩展名；
    写了扩展名必须与原扩展名一致（防改完打不开）；同目录重名返回 409；
    源文件已不存在返回 409（那是失效素材，走清理）。
    """
    name = payload.new_name.strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise HTTPException(status_code=422, detail="文件名不合法：不能为空、不能包含路径分隔符")

    with database.get_cursor() as cur:
        cur.execute(
            "SELECT id, file_path, file_name, file_ext FROM media WHERE id = ?",
            (media_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    row = dict(row)

    ext = (row["file_ext"] or "").lstrip(".")
    if "." in name:
        base, _, e2 = name.rpartition(".")
        if not base:
            raise HTTPException(status_code=422, detail="文件名不合法")
        if ext and e2.lower() != ext.lower():
            raise HTTPException(status_code=422, detail="扩展名不能改（防止文件改完打不开）")
        final = base + "." + (ext or e2)
    else:
        final = name + ("." + ext if ext else "")

    old_path = row["file_path"]
    new_path = os.path.join(os.path.dirname(old_path), final)
    if new_path != old_path and os.path.exists(new_path):
        raise HTTPException(status_code=409, detail="同目录下已存在同名文件")
    if not os.path.exists(old_path):
        raise HTTPException(status_code=409, detail="源文件不存在，无法重命名（可先用失效清理处理）")

    if new_path != old_path:
        os.rename(old_path, new_path)
        try:
            with database.get_cursor() as cur:
                cur.execute(
                    "UPDATE media SET file_name = ?, file_path = ? WHERE id = ?",
                    (final, new_path, media_id),
                )
        except Exception:
            try:
                os.rename(new_path, old_path)
            except OSError:
                pass
            raise
    return {"id": media_id, "file_name": final, "file_path": new_path}


# ---------------------------------------------------------------------------
# 端点：批量更新（收藏/评分）
# ---------------------------------------------------------------------------

class MediaBatchRequest(BaseModel):
    """POST /api/media/batch 请求体：对一批素材统一设置收藏状态和/或评分。"""

    ids: list[int] = Field(..., min_length=1, max_length=500)
    is_favorite: Optional[bool] = Field(None, description="是否收藏；不传则不动该字段")
    rating: Optional[int] = Field(None, ge=0, le=5, description="评分 0-5；不传则不动该字段")

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "MediaBatchRequest":
        if self.is_favorite is None and self.rating is None:
            raise ValueError("请求体不能为空，至少提供 is_favorite / rating 之一")
        return self


@router.post("/media/batch", summary="批量更新素材收藏状态和/或评分")
def batch_update_media(payload: MediaBatchRequest) -> dict:
    ids = list(dict.fromkeys(payload.ids))  # 去重保序
    ph = ",".join("?" for _ in ids)
    set_parts: list[str] = []
    params: list = []
    if payload.is_favorite is not None:
        set_parts.append("is_favorite = ?")
        params.append(1 if payload.is_favorite else 0)
    if payload.rating is not None:
        set_parts.append("rating = ?")
        params.append(int(payload.rating))
    conn = database.get_conn()
    try:
        conn.execute("BEGIN;")
        cur = conn.cursor()
        cur.execute(
            f"UPDATE media SET {', '.join(set_parts)} WHERE id IN ({ph})",
            params + ids,
        )
        updated = cur.rowcount
        conn.execute("COMMIT;")
        out: dict = {"updated": updated}
        if payload.is_favorite is not None:
            out["is_favorite"] = payload.is_favorite
        if payload.rating is not None:
            out["rating"] = payload.rating
        return out
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 端点：标签改名 / 删除
# ---------------------------------------------------------------------------

class TagRenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="新标签名")


@router.patch("/tags/{tag_id}", summary="重命名标签")
def rename_tag(tag_id: int, payload: TagRenameRequest) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="标签名不能为空")
    conn = database.get_conn()
    try:
        conn.execute("BEGIN;")
        cur = conn.cursor()
        cur.execute("SELECT id FROM tags WHERE id = ?", (tag_id,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="标签不存在")
        cur.execute(
            "SELECT id FROM tags WHERE name = ? COLLATE NOCASE AND id <> ?",
            (name, tag_id),
        )
        if cur.fetchone() is not None:
            raise HTTPException(status_code=409, detail="已存在同名标签")
        cur.execute("UPDATE tags SET name = ? WHERE id = ?", (name, tag_id))
        cur.execute("SELECT COUNT(*) AS c FROM media_tags WHERE tag_id = ?", (tag_id,))
        count = int(cur.fetchone()["c"])
        conn.execute("COMMIT;")
        return {"id": tag_id, "name": name, "count": count}
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


@router.delete("/tags/{tag_id}", summary="删除标签")
def delete_tag(tag_id: int) -> dict:
    """删除标签及其全部素材关联（不影响素材本身）。"""
    conn = database.get_conn()
    try:
        conn.execute("BEGIN;")
        cur = conn.cursor()
        cur.execute("SELECT id FROM tags WHERE id = ?", (tag_id,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="标签不存在")
        cur.execute("DELETE FROM media_tags WHERE tag_id = ?", (tag_id,))
        cur.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        conn.execute("COMMIT;")
        return {"deleted": tag_id}
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
    tag_list = _normalize_tags(payload.tags)
    if not tag_list:
        raise HTTPException(status_code=422, detail="标签列表不能全为空白")

    conn = database.get_conn()
    try:
        conn.execute("BEGIN;")
        cur = conn.cursor()

        _ensure_media_exists(cur, media_id)

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

# ---------------------------------------------------------------------------
# 端点：导出选中的素材文件到目标目录
# ---------------------------------------------------------------------------

class MediaExportRequest(BaseModel):
    """POST /api/media/export 请求体：把选中的素材文件复制到目标目录。"""

    ids: list[int] = Field(..., min_length=1, max_length=500)
    dest_dir: str = Field(..., min_length=1, max_length=500, description="目标文件夹路径")
    with_manifest: bool = Field(False, description="是否在目标目录生成曲目清单 CSV")


def _resolve_unique_name_local(dest_dir: Path, file_name: str) -> Path:
    """目标目录内取不重名路径（同名加 _1、_2 递增），与歌单导出同规则。"""
    candidate = dest_dir / file_name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    ext = candidate.suffix  # 含点，如 ".mp3"
    idx = 1
    while True:
        candidate = dest_dir / f"{stem}_{idx}{ext}"
        if not candidate.exists():
            return candidate
        idx += 1


@router.post("/media/export", summary="导出选中的素材文件到目标目录")
def export_media(payload: MediaExportRequest) -> dict:
    """
    把 ids 指定的素材文件复制到 dest_dir。

    - 目标目录不存在则创建；存在但是文件 → 400。
    - 同名文件加 _1、_2 后缀递增，不覆盖（与歌单导出同规则）。
    - 源文件不存在时跳过并记入 skipped / errors。
    - 单文件复制失败不中断，记入 errors 后继续下一条。
    - 只读数据库，不修改数据库，不动源文件。
    """
    ids = list(dict.fromkeys(payload.ids))  # 去重保序
    dest_path = Path(payload.dest_dir).expanduser()
    if dest_path.exists() and not dest_path.is_dir():
        raise HTTPException(status_code=400, detail="目标路径已存在且不是文件夹")
    try:
        dest_path.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无法创建目标文件夹: {exc}")

    try:
        with database.get_cursor() as cur:
            ph = ",".join("?" for _ in ids)
            cur.execute(
                "SELECT id, file_name, file_path, title, media_type, duration, "
                "bpm, musical_key, energy, rating "
                f"FROM media WHERE id IN ({ph})",
                ids,
            )
            rows = {int(r["id"]): r for r in cur.fetchall()}
            # 标签一次查出
            cur.execute(
                "SELECT mt.media_id AS media_id, t.name AS name "
                "FROM media_tags mt JOIN tags t ON t.id = mt.tag_id "
                f"WHERE mt.media_id IN ({ph}) ORDER BY t.name COLLATE NOCASE",
                ids,
            )
            tags_map: dict[int, list[str]] = {}
            for tr in cur.fetchall():
                tags_map.setdefault(int(tr["media_id"]), []).append(tr["name"])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"查询素材失败: {exc}")

    copied = 0
    skipped: list[str] = []
    errors: list[str] = []
    manifest_rows: list[dict] = []
    seq = 0

    for mid in ids:
        row = rows.get(mid)
        file_name = (row["file_name"] if row else "") or ""
        file_path_str = (row["file_path"] if row else "") or ""
        if not file_path_str:
            skipped.append(file_name or f"(id {mid})")
            errors.append(f"{file_name or mid}: 数据库中没有文件路径")
            continue

        src = Path(file_path_str)
        if not src.is_file():
            skipped.append(file_name or src.name)
            errors.append(f"{file_name or src.name}: 源文件不存在")
            continue

        # 只取文件名部分，防止路径穿越
        safe_name = Path(file_name).name if file_name else src.name
        try:
            target = _resolve_unique_name_local(dest_path, safe_name)
            shutil.copy2(str(src), str(target))
            copied += 1
            seq += 1
            manifest_rows.append({
                "seq": seq,
                "file_name": safe_name,
                "title": (row["title"] if row else "") or "",
                "media_type": (row["media_type"] if row else "") or "",
                "duration": row["duration"] if row else None,
                "bpm": row["bpm"] if row else None,
                "musical_key": (row["musical_key"] if row else "") or "",
                "energy": row["energy"] if row else None,
                "rating": (row["rating"] if row else 0) or 0,
                "tags": tags_map.get(mid, []),
            })
        except Exception as exc:
            skipped.append(safe_name)
            errors.append(f"{safe_name}: {exc}")

    manifest_file = None
    if payload.with_manifest and manifest_rows:
        try:
            manifest_file = write_manifest_csv(dest_path, manifest_rows)
        except Exception as exc:
            errors.append(f"曲目清单生成失败: {exc}")

    return {
        "dest_dir": str(dest_path),
        "copied": copied,
        "skipped": skipped,
        "errors": errors,
        "manifest": manifest_file,
    }
