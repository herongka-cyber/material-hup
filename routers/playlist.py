"""
歌单/项目篮 API 路由。

端点：
    POST   /api/playlists                                      新建歌单
    GET    /api/playlists                                      歌单列表（含 item_count）
    GET    /api/playlists/{playlist_id}                        歌单详情
    PATCH  /api/playlists/{playlist_id}                        修改歌单名/描述
    DELETE /api/playlists/{playlist_id}                        删除歌单
    POST   /api/playlists/{playlist_id}/items                  加条目（幂等）
    DELETE /api/playlists/{playlist_id}/items/{media_id}       删条目
    PUT    /api/playlists/{playlist_id}/items/order            重排
    POST   /api/playlists/{playlist_id}/export                 导出歌单内素材到目标目录

导出端点行为：
- 目标目录不存在时自动创建（含多级父目录）。
- 按 sort_order 顺序逐条把媒体文件复制到目标目录；同名文件加 _1、_2 后缀递增。
- 源文件在磁盘上找不到时跳过并记录；单个文件复制失败不中断整体。
- 只读数据库、不修改数据库，不动源文件。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Response
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


class PlaylistExportRequest(BaseModel):
    """POST /api/playlists/{id}/export 请求体。"""
    dest_dir: str = Field(..., min_length=1, description="目标文件夹路径")
    with_manifest: bool = Field(False, description="是否在目标目录生成曲目清单 CSV")

    @field_validator("dest_dir")
    @classmethod
    def _strip_dest(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("dest_dir 不能为空白")
        return stripped


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _ensure_playlist_exists(cur, playlist_id: int) -> None:
    cur.execute("SELECT 1 FROM playlists WHERE id = ?", (playlist_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="歌单不存在")


def _ensure_media_exists(cur, media_id: int) -> None:
    cur.execute("SELECT 1 FROM media WHERE id = ?", (media_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="素材不存在")


def _fetch_tags_for_batch(cur, media_ids: list[int]) -> dict[int, list[str]]:
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


_TYPE_NAMES = {"bgm": "BGM", "sfx": "音效", "image": "图片", "video": "视频"}


def write_manifest_csv(dest_dir: Path, rows: list[dict]) -> str:
    """在目标目录生成「曲目清单.csv」（带 BOM，Excel 打开不乱码），返回文件名。

    rows 每项：seq(序号)/file_name/title/media_type/duration(秒)/bpm/musical_key/
    energy/rating(0-5)/tags(list[str])。
    """
    import csv

    path = dest_dir / "曲目清单.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["序号", "文件名", "标题", "类型", "时长(秒)", "BPM", "调性", "能量", "评分", "标签"])
        for r in rows:
            w.writerow([
                r.get("seq", ""),
                r.get("file_name", ""),
                r.get("title", ""),
                _TYPE_NAMES.get(r.get("media_type", ""), r.get("media_type", "")),
                round(r["duration"], 1) if r.get("duration") is not None else "",
                round(r["bpm"], 1) if r.get("bpm") is not None else "",
                r.get("musical_key") or "",
                round(r["energy"], 2) if r.get("energy") is not None else "",
                (f'{r["rating"]} 星' if r.get("rating") else "未评分"),
                "、".join(r.get("tags") or []),
            ])
    return path.name


def _fetch_playlist_basic(cur, playlist_id: int) -> dict[str, Any]:
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
    basic = _fetch_playlist_basic(cur, playlist_id)
    basic["items"] = _fetch_playlist_items(cur, playlist_id)
    return basic


def _fetch_single_item(cur, playlist_id: int, media_id: int) -> Optional[dict[str, Any]]:
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
    with database.get_cursor() as cur:
        return _fetch_playlist_detail(cur, playlist_id)


@router.patch("/playlists/{playlist_id}", summary="修改歌单")
def patch_playlist(playlist_id: int, payload: PlaylistPatch) -> dict:
    set_parts: list[str] = []
    params: list[Any] = []

    if "name" in payload.model_fields_set:
        set_parts.append("name = ?")
        params.append(payload.name)

    if "description" in payload.model_fields_set:
        set_parts.append("description = ?")
        params.append(payload.description)

    if not set_parts:
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
    conn = database.get_conn()
    try:
        conn.execute("BEGIN;")
        cur = conn.cursor()

        _ensure_playlist_exists(cur, playlist_id)
        _ensure_media_exists(cur, payload.media_id)

        existing = _fetch_single_item(cur, playlist_id, payload.media_id)
        if existing is not None:
            conn.execute("COMMIT;")
            return existing

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
    conn = database.get_conn()
    try:
        conn.execute("BEGIN;")
        cur = conn.cursor()

        _ensure_playlist_exists(cur, playlist_id)

        cur.execute(
            "SELECT media_id FROM playlist_items WHERE playlist_id = ?",
            (playlist_id,),
        )
        existing_ids = sorted(int(r["media_id"]) for r in cur.fetchall())
        requested_ids = sorted(int(x) for x in payload.order)

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


# ---------------------------------------------------------------------------
# 端点：导出歌单为 M3U 播放列表
# ---------------------------------------------------------------------------

@router.get("/playlists/{playlist_id}/m3u", summary="导出歌单为 M3U")
def export_playlist_m3u(playlist_id: int) -> Response:
    """生成 .m3u8 文本：#EXTINF 时长+标题，下一行是本机绝对路径。

    在装有素材的这台电脑上，VLC、PotPlayer 等播放器可直接打开整单连播。
    """
    with database.get_cursor() as cur:
        detail = _fetch_playlist_detail(cur, playlist_id)
    lines = ["#EXTM3U"]
    for it in detail["items"]:
        dur = int(round(it["duration"])) if it.get("duration") is not None else -1
        label = it.get("title") or it.get("file_name") or ""
        lines.append(f"#EXTINF:{dur},{label}")
        lines.append(it["file_path"])
    body = "\n".join(lines) + "\n"
    return Response(
        content=body,
        media_type="audio/x-mpegurl; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="playlist_{playlist_id}.m3u8"'
        },
    )


# ---------------------------------------------------------------------------
# 端点：导出歌单素材
# ---------------------------------------------------------------------------

def _resolve_unique_name(dest_dir: Path, base_name: str) -> Path:
    """
    在目标目录里找不冲突的文件名。foo.mp3 冲突时返回 foo_1.mp3、foo_2.mp3 …；
    后缀保留原扩展名（大小写不敏感），路径以 dest_dir 为父。
    """
    candidate = dest_dir / base_name
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


@router.post("/playlists/{playlist_id}/export", summary="导出歌单素材到目标目录")
def export_playlist(playlist_id: int, payload: PlaylistExportRequest) -> dict:
    """
    把歌单内的素材文件按顺序复制到 dest_dir。

    - 目标目录不存在则创建。
    - 同名文件加 _1、_2 后缀递增，不覆盖。
    - 源文件不存在时跳过并记入 skipped / errors。
    - 单文件复制失败不中断，记入 errors 后继续下一条。
    - 只读数据库，不修改数据库，不动源文件。
    """
    dest_path = Path(payload.dest_dir).expanduser()
    # 存在但是文件 → 400；不存在或已是目录 → 后续 mkdir 处理
    if dest_path.exists() and not dest_path.is_dir():
        raise HTTPException(status_code=400, detail="目标路径已存在且不是文件夹")
    try:
        dest_path.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无法创建目标文件夹: {exc}")

    # 数据库只读，不开事务；直接把条目一次性读出来
    try:
        with database.get_cursor() as cur:
            _ensure_playlist_exists(cur, playlist_id)
            cur.execute(
                "SELECT pi.sort_order, pi.id AS item_id, m.id AS media_id, "
                "m.file_name, m.file_path, m.title, m.media_type, m.duration, "
                "m.bpm, m.musical_key, m.energy, m.rating "
                "FROM playlist_items pi "
                "JOIN media m ON m.id = pi.media_id "
                "WHERE pi.playlist_id = ? "
                "ORDER BY pi.sort_order ASC, pi.id ASC",
                (playlist_id,),
            )
            rows = cur.fetchall()
            tags_map = _fetch_tags_for_batch(cur, [int(r["media_id"]) for r in rows])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"查询歌单失败: {exc}")

    copied = 0
    skipped: list[str] = []
    errors: list[str] = []
    manifest_rows: list[dict] = []
    seq = 0

    for row in rows:
        file_name = row["file_name"] or ""
        file_path_str = row["file_path"] or ""
        if not file_path_str:
            skipped.append(file_name or "(未知文件)")
            errors.append(f"{file_name}: 数据库中没有文件路径")
            continue

        src = Path(file_path_str)
        if not src.is_file():
            skipped.append(file_name or src.name)
            errors.append(f"{file_name or src.name}: 源文件不存在")
            continue

        # 只取文件名部分，防止路径穿越
        safe_name = Path(file_name).name if file_name else src.name
        try:
            target = _resolve_unique_name(dest_path, safe_name)
            shutil.copy2(str(src), str(target))
            copied += 1
            seq += 1
            manifest_rows.append({
                "seq": seq,
                "file_name": safe_name,
                "title": row["title"] or "",
                "media_type": row["media_type"] or "",
                "duration": row["duration"],
                "bpm": row["bpm"],
                "musical_key": row["musical_key"],
                "energy": row["energy"],
                "rating": row["rating"] or 0,
                "tags": tags_map.get(int(row["media_id"]), []),
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