"""
扫描服务。

职责：
- 接收 [{path, media_type}] 目录列表，遍历收集文件。
- 按显式 media_type 归类，不做路径关键词猜测。
- 增量判断：file_path 已存在且 file_size/file_mtime 未变则跳过。
- 逐文件提取元数据、推导音效分类、读取 BGM 特征 JSON。
- 每 50 个文件提交一次数据库事务并更新进度。
- 单文件异常跳过并记录，不中断整体。
- 扫描任务状态存内存字典，供 /api/scan/status 轮询。

任务状态结构（内存）：
    {
        "task_id": str,
        "status": "running" | "done" | "error",
        "total": int,
        "processed": int,
        "current_file": str,
        "skipped": int,
        "inserted": int,
        "updated": int,
        "errors": list[str],   # 最多保留 100 条错误信息
        "error_message": str,  # status=error 时的总体错误
        "started_at": float,
        "finished_at": float,
    }
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from db import database, models
from services import metadata

# 每处理多少个文件提交一次事务并更新进度
_BATCH_SIZE = 50

# 单任务错误信息保留上限
_MAX_ERRORS = 100

# 内存任务表：task_id → 状态 dict
_tasks: dict[str, dict[str, Any]] = {}
_tasks_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 任务状态管理
# ---------------------------------------------------------------------------

def create_task() -> dict[str, Any]:
    """创建并注册一个新扫描任务，返回状态 dict。"""
    task_id = uuid.uuid4().hex
    task = {
        "task_id": task_id,
        "status": "running",
        "total": 0,
        "processed": 0,
        "current_file": "",
        "skipped": 0,
        "inserted": 0,
        "updated": 0,
        "errors": [],
        "error_message": "",
        "started_at": time.time(),
        "finished_at": 0.0,
    }
    with _tasks_lock:
        _tasks[task_id] = task
    return task


def get_task(task_id: str) -> Optional[dict[str, Any]]:
    """按 task_id 获取任务状态快照（返回浅拷贝，避免外部修改）。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            return None
        snapshot = dict(task)
        # errors 列表复制一份
        snapshot["errors"] = list(task["errors"])
        return snapshot


def _update_task(task_id: str, **fields: Any) -> None:
    """线程安全地更新任务字段。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            return
        for k, v in fields.items():
            task[k] = v


def _append_error(task_id: str, message: str) -> None:
    """线程安全地追加错误信息，超出上限则丢弃最早的。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            return
        errors = task["errors"]
        errors.append(message)
        if len(errors) > _MAX_ERRORS:
            del errors[: len(errors) - _MAX_ERRORS]


# ---------------------------------------------------------------------------
# 文件收集
# ---------------------------------------------------------------------------

def _allowed_extensions(media_type: str) -> tuple[str, ...]:
    """按 media_type 返回允许的扩展名元组（小写）。"""
    if media_type == "bgm":
        return models.AUDIO_EXT_BGM
    if media_type == "sfx":
        return models.AUDIO_EXT_SFX
    if media_type == "image":
        return models.IMAGE_EXT
    if media_type == "video":
        return models.VIDEO_EXT
    return ()


def collect_files(scan_entry: dict[str, Any]) -> list[Path]:
    """
    遍历单个扫描目录，返回符合条件的文件路径列表。

    scan_entry: {"path": "...", "media_type": "bgm"}
    目录不存在或不可读时返回空列表。
    """
    root = Path(scan_entry["path"]).expanduser()
    media_type = scan_entry["media_type"]
    allowed = _allowed_extensions(media_type)

    if not root.is_dir():
        return []

    files: list[Path] = []
    try:
        for entry in root.rglob("*"):
            if not entry.is_file():
                continue
            ext = entry.suffix.lower().lstrip(".")
            if ext in allowed:
                files.append(entry)
    except Exception:
        # 遍历异常时返回已收集部分
        pass

    return files


# ---------------------------------------------------------------------------
# 数据库写入辅助
# ---------------------------------------------------------------------------

def _upsert_tag(cur, name: str) -> int:
    """插入或获取标签 id。返回 tag_id。"""
    cur.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,))
    row = cur.fetchone()
    if row is not None:
        return int(row["id"])
    cur.execute("INSERT INTO tags(name) VALUES (?)", (name,))
    return int(cur.lastrowid)


def _attach_tags(cur, media_id: int, tag_names: list[str]) -> None:
    """将标签关联到素材。先删旧关联再插新关联，保证幂等。"""
    if not tag_names:
        return
    cur.execute("DELETE FROM media_tags WHERE media_id = ?", (media_id,))
    for name in tag_names:
        tag_id = _upsert_tag(cur, name)
        cur.execute(
            "INSERT OR IGNORE INTO media_tags(media_id, tag_id) VALUES (?, ?)",
            (media_id, tag_id),
        )


def _find_existing(cur, file_path: str) -> Optional[dict[str, Any]]:
    """按 file_path 查询已有记录，返回 id/file_size/file_mtime；不存在返回 None。"""
    cur.execute(
        "SELECT id, file_size, file_mtime FROM media WHERE file_path = ?",
        (file_path,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "file_size": row["file_size"],
        "file_mtime": row["file_mtime"],
    }


# ---------------------------------------------------------------------------
# 单文件处理
# ---------------------------------------------------------------------------

def _process_one(
    cur,
    file_path: Path,
    media_type: str,
    scan_root: Path,
    bgm_index: dict[str, dict[str, Any]],
) -> str:
    """
    处理单个文件：判断增量、提取元数据、写入或更新。

    返回处理结果字符串：
        "skipped"  文件未变化，跳过
        "inserted" 新插入
        "updated"  已存在且变化，更新
    """
    stat = file_path.stat()
    file_size = int(stat.st_size)
    file_mtime = float(stat.st_mtime)
    file_path_str = str(file_path.resolve())
    file_name = file_path.name
    file_ext = file_path.suffix.lower().lstrip(".")

    existing = _find_existing(cur, file_path_str)
    if existing is not None:
        same_size = existing["file_size"] == file_size
        # mtime 用 float 直接比较可能因浮点误差失败，允许 1ms 容差
        same_mtime = (
            existing["file_mtime"] is not None
            and abs(float(existing["file_mtime"]) - file_mtime) < 0.001
        )
        if same_size and same_mtime:
            return "skipped"

    # 基础字段
    fields: dict[str, Any] = {
        "file_path": file_path_str,
        "file_name": file_name,
        "file_ext": file_ext,
        "file_size": file_size,
        "file_mtime": file_mtime,
        "media_type": media_type,
    }

    # 按类型提取元数据
    extra_tags: list[str] = []

    if media_type in ("bgm", "sfx"):
        fields.update(metadata.extract_audio_metadata(file_path))

    if media_type == "image":
        fields.update(metadata.extract_image_metadata(file_path))

    if media_type == "video":
        fields.update(metadata.extract_video_metadata(file_path))

    if media_type == "sfx":
        fields["sfx_category"] = metadata.derive_sfx_category(file_path, scan_root)

    if media_type == "bgm":
        record = bgm_index.get(file_name)
        if record is not None:
            features = metadata.extract_bgm_features_from_record(record)
            # JSON 的 duration_s 覆盖 mutagen 结果（若存在），保持索引一致性
            fields.update(features)
            extra_tags = metadata.get_record_tags(record)

    # 写入或更新
    if existing is None:
        columns = list(fields.keys())
        placeholders = ", ".join("?" for _ in columns)
        sql = (
            f"INSERT INTO media ({', '.join(columns)}) "
            f"VALUES ({placeholders})"
        )
        cur.execute(sql, [fields[c] for c in columns])
        media_id = int(cur.lastrowid)
        action = "inserted"
    else:
        media_id = existing["id"]
        columns = list(fields.keys())
        set_clause = ", ".join(f"{c} = ?" for c in columns)
        sql = f"UPDATE media SET {set_clause} WHERE id = ?"
        cur.execute(sql, [fields[c] for c in columns] + [media_id])
        action = "updated"

    # BGM 标签关联（仅在有 JSON 记录且带 tags 时）
    if media_type == "bgm" and extra_tags:
        _attach_tags(cur, media_id, extra_tags)

    return action


# ---------------------------------------------------------------------------
# 主扫描流程
# ---------------------------------------------------------------------------

def run_scan(
    task_id: str,
    scan_entries: list[dict[str, Any]],
    feature_index_path: Optional[str] = None,
) -> None:
    """
    扫描主流程。由后台线程调用。

    scan_entries: [{"path": "...", "media_type": "bgm"}, ...]
    feature_index_path: 可选 BGM 特征 JSON 文件路径。
    """
    # 加载 BGM 特征索引
    bgm_index: dict[str, dict[str, Any]] = {}
    if feature_index_path:
        index_path = Path(feature_index_path).expanduser()
        if index_path.is_file():
            bgm_index = metadata.load_feature_index(index_path)
        else:
            _append_error(task_id, f"特征索引文件不存在: {feature_index_path}")

    # 收集文件（保留每个文件对应的扫描根目录，供音效分类推导使用）
    # 列表元素：(Path, media_type, scan_root)
    all_files: list[tuple[Path, str, Path]] = []
    try:
        for entry in scan_entries:
            media_type = entry["media_type"]
            root = Path(entry["path"]).expanduser()
            files = collect_files(entry)
            for f in files:
                all_files.append((f, media_type, root))
    except Exception as exc:
        _update_task(
            task_id,
            status="error",
            error_message=f"目录收集失败: {exc}",
            finished_at=time.time(),
        )
        return

    total = len(all_files)
    _update_task(task_id, total=total)

    if total == 0:
        _update_task(task_id, status="done", finished_at=time.time())
        return

    # 主处理循环：每 _BATCH_SIZE 个提交一次
    processed = 0
    skipped = 0
    inserted = 0
    updated = 0

    cur = None
    conn = None
    try:
        conn = database.get_conn()
        conn.execute("BEGIN;")
        cur = conn.cursor()

        for idx, (file_path, media_type, scan_root) in enumerate(all_files, start=1):
            _update_task(task_id, current_file=str(file_path))
            try:
                action = _process_one(
                    cur,
                    file_path=file_path,
                    media_type=media_type,
                    scan_root=scan_root,
                    bgm_index=bgm_index,
                )
                if action == "skipped":
                    skipped += 1
                elif action == "inserted":
                    inserted += 1
                elif action == "updated":
                    updated += 1
            except Exception as exc:
                # 单文件异常：记录错误并继续；批量提交前的事务仍会成功提交已处理部分
                _append_error(task_id, f"{file_path}: {exc}")

            processed = idx
            _update_task(task_id, processed=processed)

            if processed % _BATCH_SIZE == 0:
                conn.execute("COMMIT;")
                conn.execute("BEGIN;")
                _update_task(
                    task_id,
                    skipped=skipped,
                    inserted=inserted,
                    updated=updated,
                )

        # 尾批提交
        if conn.in_transaction:
            conn.execute("COMMIT;")

        _update_task(
            task_id,
            status="done",
            processed=processed,
            skipped=skipped,
            inserted=inserted,
            updated=updated,
            current_file="",
            finished_at=time.time(),
        )
    except Exception as exc:
        if conn is not None and conn.in_transaction:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
        _update_task(
            task_id,
            status="error",
            error_message=str(exc),
            finished_at=time.time(),
        )
    finally:
        if conn is not None:
            conn.close()


def start_scan_in_thread(
    scan_entries: list[dict[str, Any]],
    feature_index_path: Optional[str] = None,
) -> str:
    """
    创建任务并在后台线程中启动扫描，返回 task_id。

    使用守护线程，进程退出时不会阻塞。
    """
    task = create_task()
    task_id = task["task_id"]
    thread = threading.Thread(
        target=run_scan,
        args=(task_id, scan_entries, feature_index_path),
        daemon=True,
        name=f"scan-{task_id[:8]}",
    )
    thread.start()
    return task_id
