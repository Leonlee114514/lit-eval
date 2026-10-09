"""进程级共享依赖：抓取编排器（orchestrator）单例。

独立成模块，而不是继续放在 ``app.main`` 里 —— 那是循环依赖的来源：
router 需要 orchestrator，而 ``main`` 需要 import 所有 router，两边互指就成环，
过去只能靠在函数体里写 ``__import__("app.main", fromlist=["get_orchestrator"])``
绕开 ImportError（静态工具看不见，改名/拆分时也不会报错）。

把单例搬到这里之后依赖方向恢复单向：

    router → deps
    main   → router
    main   → deps

``deps`` 不 import 任何 router，因此不会成环，函数级 import 也全部可以拿掉。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_orchestrator = None


def get_orchestrator():
    """懒加载单例：首次调用时按 settings 构造，之后复用同一组 HTTP 客户端（连接池）。

    不在这里做启动期初始化，是为了让 ``import app.main`` 不触发网络客户端构建
    （测试里替换 engine/fixture 时不会有副作用）。
    """
    global _orchestrator
    if _orchestrator is None:
        from app.config import get_settings
        from app.services.fetchers.base import FetcherOrchestrator

        settings = get_settings()
        _orchestrator = FetcherOrchestrator(
            mailto=settings.fetcher_mailto,
            openalex_api_key=settings.openalex_api_key,
        )
    return _orchestrator


async def close_orchestrator() -> None:
    """关停共享客户端并置空。幂等；lifespan 退出时调用。

    置空是必要的：测试里 lifespan 可能被多次进入/退出，
    不置空会让第二次拿到已关闭的客户端。
    """
    global _orchestrator
    if _orchestrator is not None:
        await _orchestrator.close()
        _orchestrator = None


def reset_orchestrator() -> None:
    """测试用：丢弃当前单例（不负责关闭，交由调用方）。"""
    global _orchestrator
    _orchestrator = None
