"""FastAPI 入口：CORS + 路由注册 + 生命周期初始化。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    router_evals,
    router_meta,
    router_papers,
    router_projects,
    router_reports,
    router_search,
)
from app.config import get_settings
from app.deps import close_orchestrator

logging.basicConfig(level=logging.INFO)


async def _warm_semantic_models() -> None:
    """后台预热语义模型：把 fastembed 的"首次加载"从请求路径挪到启动阶段。

    为什么需要：fastembed 首次加载若模型缺失会尝试下载并内部重试（实测累计约 2 分钟），
    原本发生在后端重启后的第一次搜索请求里，直接撞爆前端 axios 的 120s 超时——界面报
    timeout，而后端最终其实成功了。预热失败不影响功能（relevance 自动回退覆盖率），
    只记一条日志。
    """
    import asyncio
    import logging

    logger = logging.getLogger("app.warmup")
    if not get_settings().use_fastembed:
        return
    try:
        from app.services.content import relevance

        ok = await asyncio.to_thread(relevance.warm_up)
        logger.info("fastembed 预热%s", "完成" if ok else "失败（相关性将回退覆盖率）")
    except Exception as e:  # noqa: BLE001 — 预热失败绝不影响启动
        logger.warning("fastembed 预热异常（相关性将回退覆盖率）: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    from app.db import init_db

    settings = get_settings()
    settings.ensure_dirs()
    init_db()  # 建表（幂等）

    # 预热不阻塞启动：用户慢慢点进来时模型状态已定，第一次搜索不再等几分钟。
    warm_task = asyncio.create_task(_warm_semantic_models())

    yield

    warm_task.cancel()  # 关停时别让预热线程挂着
    await close_orchestrator()


app = FastAPI(title="文献评估网站", version="0.1.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request, exc: Exception):
    """兜底：未捕获异常返回结构化 JSON 而非裸 500 文本，便于前端/调试。"""
    import logging

    logger = logging.getLogger("uvicorn.error")
    logger.exception("Unhandled error at %s %s", request.method, request.url.path)
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=500,
        content={"ok": False, "data": None, "warnings": [f"服务器内部错误: {type(exc).__name__}: {exc}"]},
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发期全放开；生产应限定前端域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router_meta.router)
app.include_router(router_projects.router)
app.include_router(router_papers.router)
app.include_router(router_evals.router)
app.include_router(router_reports.router)
app.include_router(router_search.router)


@app.get("/api/health")
async def health():
    return {"ok": True, "status": "online", "app": app.title, "version": app.version}
