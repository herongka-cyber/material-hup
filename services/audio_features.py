"""
BGM 音频特征分析服务（librosa）+ 特征补全任务管理。

职责：
- analyze_bgm_features(file_path)：分析单个音频文件，返回 bpm/musical_key/energy/features_json。
- 特征补全任务：遍历全部 BGM，为 bpm IS NULL 的记录调用分析并写回数据库。
- 任务状态存内存字典，结构与 services/scanner.py 平行。

关键设计：
- librosa 惰性导入（在函数内 import），避免 FastAPI 启动时加载耗时 3-10 秒。
- 只加载前 max_duration 秒（默认 180），不加载整首超长音频到内存。
- 分析失败不抛异常；加载失败返回空 dict。
- 同一时刻只允许一个补全任务运行（避免 libcoasa 计算与 SQLite 写竞争）。
- 每个文件分析后单独提交一次 UPDATE，进度实时可见。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from db import database

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 单曲最长分析时长（秒）。超出部分截断，避免内存与耗时爆炸
_MAX_ANALYZE_DURATION = 180.0

# librosa 加载采样率。22050 是 librosa 标准，特征计算快
_SAMPLE_RATE = 22050

# 合理 BPM 范围（含头尾）
_BPM_MIN = 40.0
_BPM_MAX = 220.0

# energy dB 映射范围
_ENERGY_DB_MIN = -40.0
_ENERGY_DB_MAX = 0.0

# 调性模板（Krumhansl-Kessler）
_MAJOR_PROFILE = (
    6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
    2.52, 5.19, 2.39, 3.66, 2.29, 2.88,
)
_MINOR_PROFILE = (
    6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
    2.54, 4.75, 3.98, 2.69, 3.34, 3.17,
)
_NOTE_NAMES = (
    "C", "C#", "D", "D#", "E", "F",
    "F#", "G", "G#", "A", "A#", "B",
)

# 错误信息保留上限
_MAX_ERRORS = 100

# ---------------------------------------------------------------------------
# 任务状态（内存）
# ---------------------------------------------------------------------------

_tasks: dict[str, dict[str, Any]] = {}
_tasks_lock = threading.Lock()

# 当前运行的补全任务 id（None 表示空闲）
_active_backfill_id: Optional[str] = None
_active_lock = threading.Lock()


def _create_task() -> dict[str, Any]:
    """创建并注册一个新补全任务。"""
    task_id = uuid.uuid4().hex
    task = {
        "task_id": task_id,
        "status": "running",
        "total": 0,
        "processed": 0,
        "skipped": 0,
        "analyzed": 0,
        "failed": 0,
        "current_file": "",
        "errors": [],
        "error_message": "",
        "started_at": time.time(),
        "finished_at": 0.0,
    }
    with _tasks_lock:
        _tasks[task_id] = task
    return task


def get_backfill_task(task_id: str) -> Optional[dict[str, Any]]:
    """按 task_id 获取任务快照（深拷贝 errors，避免外部修改）。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            return None
        snap = dict(task)
        snap["errors"] = list(task["errors"])
        return snap


def _update_task(task_id: str, **fields: Any) -> None:
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            return
        for k, v in fields.items():
            task[k] = v


def _append_error(task_id: str, message: str) -> None:
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            return
        errs = task["errors"]
        errs.append(message)
        if len(errs) > _MAX_ERRORS:
            del errs[: len(errs) - _MAX_ERRORS]


# ---------------------------------------------------------------------------
# 特征分析
# ---------------------------------------------------------------------------

def _load_audio(
    file_path: Path, max_duration: float = _MAX_ANALYZE_DURATION
) -> tuple[Any, Any]:
    """
    加载音频，返回 (y, sr)；失败返回 (None, None)。

    只加载前 max_duration 秒，避免整首超长音频驻留内存。
    惰性 import librosa，首次调用有 3-10 秒 JIT/缓存加载开销。
    """
    try:
        import librosa
    except Exception:
        return None, None

    try:
        y, sr = librosa.load(
            str(file_path),
            sr=_SAMPLE_RATE,
            mono=True,
            duration=max_duration,
        )
    except Exception:
        return None, None

    if y is None or getattr(y, "size", 0) == 0:
        return None, None
    return y, sr


def _sanitize_bpm(bpm: Optional[float]) -> Optional[float]:
    """
    将 bpm 规范到 [_BPM_MIN, _BPM_MAX] 区间。

    librosa 有时会把 80 判成 160（或 60 判成 120 判成 240），
    这里做倍频修正：若原值越界，尝试 ×2 或 ÷2 落入区间；
    仍不合法则返回 None。
    """
    if bpm is None or bpm <= 0:
        return None
    candidates = [float(bpm), float(bpm) / 2.0, float(bpm) * 2.0]
    for c in candidates:
        if _BPM_MIN <= c <= _BPM_MAX:
            return c
    return None


def _detect_bpm(y: Any, sr: int) -> Optional[float]:
    """librosa.beat.beat_track 估计全局 tempo。"""
    try:
        import numpy as np
        import librosa
    except Exception:
        return None

    try:
        tempo_raw, _ = librosa.beat.beat_track(y=y, sr=sr)
    except Exception:
        return None

    # 兼容 librosa 不同版本返回标量 / ndarray 的差异
    try:
        arr = np.atleast_1d(np.asarray(tempo_raw, dtype=float).ravel())
    except Exception:
        return None
    if arr.size == 0:
        return None
    tempo = float(arr[0])
    return _sanitize_bpm(tempo)


def _detect_key(y: Any, sr: int) -> Optional[str]:
    """
    基于 chroma_stft 均值与 Krumhansl-Kessler 模板旋转匹配，返回调性字符串。

    返回如 "C"、"Am"、"F#"、"Bbm"；计算失败返回 None。
    """
    try:
        import numpy as np
        import librosa
    except Exception:
        return None

    try:
        chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    except Exception:
        return None

    if chroma is None or chroma.size == 0:
        return None
    chroma_mean = chroma.mean(axis=1)  # 12 维
    if chroma_mean.size != 12:
        return None

    major = np.asarray(_MAJOR_PROFILE, dtype=float)
    minor = np.asarray(_MINOR_PROFILE, dtype=float)

    best_score = -1.0
    best_key: Optional[str] = None
    for i in range(12):
        rot_major = np.roll(major, i)
        s = float(np.dot(chroma_mean, rot_major))
        if s > best_score:
            best_score = s
            best_key = _NOTE_NAMES[i]

        rot_minor = np.roll(minor, i)
        s = float(np.dot(chroma_mean, rot_minor))
        if s > best_score:
            best_score = s
            best_key = _NOTE_NAMES[i] + "m"

    return best_key


def _compute_energy(y: Any) -> Optional[float]:
    """
    计算归一化能量 [0, 1]。

    使用 RMS 均值的 dB 值映射 [-40dB, 0dB] → [0, 1]。
    计算失败返回 None。
    """
    try:
        import numpy as np
        import librosa
    except Exception:
        return None

    try:
        rms = librosa.feature.rms(y=y)
    except Exception:
        return None
    if rms is None or rms.size == 0:
        return None

    mean_rms = float(np.mean(rms))
    if mean_rms <= 0.0:
        return 0.0

    db = 20.0 * np.log10(max(mean_rms, 1e-6))
    span = _ENERGY_DB_MAX - _ENERGY_DB_MIN
    energy = (float(db) - _ENERGY_DB_MIN) / span
    return float(np.clip(energy, 0.0, 1.0))


def _compute_extended_features(y: Any, sr: int) -> Optional[dict[str, float]]:
    """
    计算扩展特征：brightness_hz、onset_rate、zcr。

    返回 dict（与 M2 的 JSON 字段名对齐）；计算失败返回 None。
    部分失败时尽量返回能算的字段；三项全失败返回 None。
    """
    try:
        import numpy as np
        import librosa
    except Exception:
        return None

    result: dict[str, float] = {}

    # 亮度：spectral_centroid 均值
    try:
        centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
        if centroid is not None and centroid.size > 0:
            result["brightness_hz"] = float(np.mean(centroid))
    except Exception:
        pass

    # 过零率：zero_crossing_rate 均值
    try:
        zcr = librosa.feature.zero_crossing_rate(y=y)
        if zcr is not None and zcr.size > 0:
            result["zcr"] = float(np.mean(zcr))
    except Exception:
        pass

    # onset rate：检测到的 onset 数 / 时长（秒）
    try:
        onsets = librosa.onset.onset_detect(y=y, sr=sr)
        duration_sec = float(len(y)) / float(sr) if sr > 0 else 0.0
        if duration_sec > 0:
            result["onset_rate"] = float(len(onsets)) / duration_sec
    except Exception:
        pass

    return result if result else None


def analyze_bgm_features(file_path: Path | str) -> dict[str, Any]:
    """
    分析单个音频文件，返回 media 表可写入的字段 dict。

    返回键（均可缺省）：
        bpm             float
        musical_key     str，如 "C" / "Am"
        energy          float，0-1
        features_json   str（JSON 串，含 brightness_hz/onset_rate/zcr）

    加载失败返回空 dict，不抛异常；部分特征计算失败只返回能算出的字段。
    """
    path = Path(file_path)
    if not path.is_file():
        return {}

    y, sr = _load_audio(path)
    if y is None or sr is None:
        return {}

    result: dict[str, Any] = {}

    bpm = _detect_bpm(y, sr)
    if bpm is not None:
        result["bpm"] = bpm

    key = _detect_key(y, sr)
    if key is not None:
        result["musical_key"] = key

    energy = _compute_energy(y)
    if energy is not None:
        result["energy"] = energy

    ext = _compute_extended_features(y, sr)
    if ext is not None:
        result["features_json"] = json.dumps(ext, ensure_ascii=False)

    return result


# ---------------------------------------------------------------------------
# 写库辅助
# ---------------------------------------------------------------------------

def _update_media_features(media_id: int, features: dict[str, Any]) -> bool:
    """将分析结果写入 media 表。只更新出现的字段。返回是否写成功。"""
    writable = ("bpm", "energy", "musical_key", "features_json")
    set_parts: list[str] = []
    params: list[Any] = []
    for col in writable:
        if col in features and features[col] is not None:
            set_parts.append(f"{col} = ?")
            params.append(features[col])

    if not set_parts:
        return False

    try:
        with database.get_cursor() as cur:
            cur.execute(
                f"UPDATE media SET {', '.join(set_parts)} WHERE id = ?",
                params + [media_id],
            )
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 补全任务：主循环
# ---------------------------------------------------------------------------

def _run_backfill(task_id: str) -> None:
    """
    补全任务主循环。由后台线程调用。

    流程：
        1. 检查 librosa 是否可用（不可用则任务立即 error）
        2. 查询全部 bgm 记录的快照（id, file_path, bpm）
        3. 逐条：bpm 非空 → skipped+1；bpm 为空 → 分析 → 写库 / 记录失败
        4. 每处理一条更新一次任务进度（分析耗时数秒，实时更新有意义）
    """
    # 1) librosa 可用性检查
    try:
        import librosa  # noqa: F401
    except Exception as exc:
        _update_task(
            task_id,
            status="error",
            error_message=f"librosa 不可用: {exc}",
            finished_at=time.time(),
        )
        return

    # 2) 查询快照：所有 bgm 记录
    try:
        with database.get_cursor() as cur:
            cur.execute(
                "SELECT id, file_path, bpm FROM media "
                "WHERE media_type = 'bgm' "
                "ORDER BY id"
            )
            rows = cur.fetchall()
    except Exception as exc:
        _update_task(
            task_id,
            status="error",
            error_message=f"查询数据库失败: {exc}",
            finished_at=time.time(),
        )
        return

    total = len(rows)
    _update_task(task_id, total=total)

    if total == 0:
        _update_task(task_id, status="done", finished_at=time.time())
        return

    skipped = 0
    analyzed = 0
    failed = 0
    processed = 0

    for row in rows:
        processed += 1
        media_id = int(row["id"])
        file_path = row["file_path"] or ""
        existing_bpm = row["bpm"]

        _update_task(task_id, current_file=file_path)

        # 已有 bpm：跳过，不重复分析
        if existing_bpm is not None:
            skipped += 1
            _update_task(task_id, processed=processed, skipped=skipped)
            continue

        if not file_path:
            failed += 1
            _append_error(task_id, f"media_id={media_id} 文件路径为空")
            _update_task(task_id, processed=processed, failed=failed)
            continue

        path = Path(file_path)
        if not path.is_file():
            failed += 1
            _append_error(task_id, f"media_id={media_id} 文件不存在: {file_path}")
            _update_task(task_id, processed=processed, failed=failed)
            continue

        try:
            features = analyze_bgm_features(path)
        except Exception as exc:
            failed += 1
            _append_error(task_id, f"{file_path}: {exc}")
            _update_task(task_id, processed=processed, failed=failed)
            continue

        if not features:
            failed += 1
            _append_error(task_id, f"{file_path}: 分析结果为空")
            _update_task(task_id, processed=processed, failed=failed)
            continue

        if not _update_media_features(media_id, features):
            failed += 1
            _append_error(task_id, f"{file_path}: 写库失败")
            _update_task(task_id, processed=processed, failed=failed)
            continue

        analyzed += 1
        _update_task(task_id, processed=processed, analyzed=analyzed)

    _update_task(
        task_id,
        status="done",
        processed=processed,
        skipped=skipped,
        analyzed=analyzed,
        failed=failed,
        current_file="",
        finished_at=time.time(),
    )


def start_backfill_in_thread() -> tuple[str, Optional[str]]:
    """
    在后台线程中启动补全任务。

    返回 (task_id, error_message)：
        error_message 非 None 表示启动失败（已有任务在运行）。
    """
    global _active_backfill_id

    with _active_lock:
        if _active_backfill_id is not None:
            with _tasks_lock:
                existing = _tasks.get(_active_backfill_id)
            if existing is not None and existing.get("status") == "running":
                return "", f"已有补全任务正在运行: {_active_backfill_id}"
            # 已结束，清理
            _active_backfill_id = None

        task = _create_task()
        _active_backfill_id = task["task_id"]

    thread = threading.Thread(
        target=_run_backfill,
        args=(task["task_id"],),
        daemon=True,
        name=f"backfill-{task['task_id'][:8]}",
    )
    thread.start()
    return task["task_id"], None