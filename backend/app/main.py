"""FastAPI entry point for the CUG course planner."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.router import api_router
from .config import settings
from .infrastructure.database import initialize_database


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    yield


def create_app(*, serve_frontend: bool = True) -> FastAPI:
    app = FastAPI(
        title="中国地质大学（武汉）2026 秋季智能排课助手",
        description=(
            "本地排课辅助工具；不会登录教务系统，也不会替学生提交选课。"
            "容量未知时不推断余量，旧快照独有课程默认需要再次确认。"
        ),
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.include_router(api_router)
    if serve_frontend and settings.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="frontend")
    return app


app = create_app()
