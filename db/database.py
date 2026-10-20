"""
SQLite 连接管理与初始化。

设计要点：
- 每个请求/线程获取独立连接（SQLite 默认单连接多线程不安全）。
- 启用 WAL 模式，读写并发更友好（扫描写入 + 前端查询同时进行）。
- 启用外键约束（默认关闭）。
- 提供 get_cursor() 上下文管理器，自动提交/回滚/关闭。
- 数据库文件默认在项目根目录 data/material_hub.db，可用环境变量覆盖。
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from db import models

# 项目根目录 = 本文件所在目录的上一级
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 数据目录：可通过环境变量 MH_DATA_DIR 覆盖（便于测试隔离）
_DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR = Path(os.environ.get("MH_DATA_DIR", _DEFAULT_DATA_DIR)).resolve()
DB_PATH = DATA_DIR / "material_hub.db"

# 缩略图目录（M5 使用，本模块仅确保父目录存在）
THUMBNAIL_DIR = DATA_DIR / "thumbnails"


def _ensure_dirs() -> None:
    """确保数据目录存在。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)


def _configure_conn(conn: sqlite3.Connection) -> None:
    """统一设置连接级 PRAGMA。"""
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")


def get_conn() -> sqlite3.Connection:
    """获取一个新的数据库连接。调用方负责 close()。"""
    _ensure_dirs()
    conn = sqlite3.connect(str(DB_PATH), isolation_level=None, timeout=10.0)
    _configure_conn(conn)
    return conn


@contextmanager
def get_cursor() -> Iterator[sqlite3.Cursor]:
    """
    上下文管理器：获取游标并自动提交/回滚/关闭。

    用法：
        with get_cursor() as cur:
            cur.execute("SELECT ...")
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        yield cur
        if conn.in_transaction:
            conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """
    初始化数据库：建表、建索引、幂等执行。

    启动时调用。重复调用无副作用。
    """
    _ensure_dirs()
    conn = get_conn()
    try:
        conn.execute("BEGIN;")
        for ddl in models.ALL_TABLES_DDL:
            conn.execute(ddl)
        for ddl in models.DDL_INDEXES:
            conn.execute(ddl)
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()


def db_info() -> dict:
    """返回数据库基础信息，供 /api/health 或调试使用。"""
    conn = get_conn()
    try:
        tables = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        media_count = conn.execute("SELECT COUNT(*) AS c FROM media").fetchone()["c"]
        return {
            "db_path": str(DB_PATH),
            "tables": tables,
            "media_count": media_count,
            "thumbnail_dir": str(THUMBNAIL_DIR),
        }
    finally:
        conn.close()
