"""
素材管家 · FastAPI 入口。

启动时自动初始化数据库；/docs 可访问 OpenAPI 文档。
本模块只挂载健康检查与数据库信息接口，业务路由在后续模块追加。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from db import database
from routers import scan as scan_router
from routers import media as media_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时建库，关闭时无需清理。"""
    database.init_db()
    yield


app = FastAPI(
    title="素材管家 Material Hub",
    description="短视频创作者的本地素材管理软件（BGM / 音效 / 图片 / 视频）",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/api/health", summary="健康检查")
def health() -> JSONResponse:
    """返回服务与数据库状态，供前端与本地调试使用。"""
    return JSONResponse(
        {
            "status": "ok",
            "db": database.db_info(),
        }
    )


# 静态资源目录：M8 前端填充前先挂载（若目录为空不影响启动）
_dashboard_static = database.PROJECT_ROOT / "static"
_dashboard_static.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_dashboard_static)), name="static")


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 业务路由挂载（M2 起逐模块追加）
# ---------------------------------------------------------------------------
app.include_router(scan_router.router)
app.include_router(media_router.router)

# 后续模块挂载点（M4 起依次启用）
# from routers import playlist, stream, thumbnail
# app.include_router(playlist.router)
# app.include_router(stream.router)
# app.include_router(thumbnail.router)
# ---------------------------------------------------------------------------
# app.include_router(media.router)
# app.include_router(playlist.router)
# app.include_router(stream.router)
# app.include_router(thumbnail.router)
# ---------------------------------------------------------------------------
