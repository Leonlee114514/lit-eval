"""架构约束测试：依赖方向必须单向。

守护 2026-10 review 的 Critical（Dependency Disorder）：路由层过去用
``__import__("app.main", fromlist=["get_orchestrator"])`` 反向依赖 main，而 main 又
``include_router`` 了全部路由 —— 两边互指就成环，因此只能在函数体内动态导入绕开
ImportError（静态工具与 IDE 都看不见这条依赖）。

单例搬到 ``app.deps`` 后依赖方向恢复单向：

    router → deps
    main   → router
    main   → deps

这两条断言故意用子进程跑：同进程里别的测试可能已经 import 过 ``app.main``，
``sys.modules`` 就分辨不出「是谁拉进来的」。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
    )


def test_routers_do_not_pull_in_app_main():
    """路由层不得（直接或间接）依赖 app.main —— 那正是过去成环的成因。"""
    result = _run(
        "import sys, app.api.router_projects, app.api.router_papers, app.api.router_search;"
        "assert 'app.main' not in sys.modules, '路由层把 app.main 拉进来了，依赖又成环'"
    )
    assert result.returncode == 0, result.stderr


def test_deps_module_stays_a_leaf():
    """共享依赖模块不得 import 路由层，否则依赖方向又反了。"""
    result = _run(
        "import sys, app.deps;"
        "assert 'app.api' not in sys.modules, 'app.deps 拉进了路由层，依赖方向反了'"
    )
    assert result.returncode == 0, result.stderr


# 前端 frontend/src/api/client.ts 实际调用的端点（拆分 router_projects 前后必须一致）
_EXPECTED_ENDPOINTS = {
    ("get", "/api/health"),
    ("get", "/api/meta/rules"),
    ("get", "/api/projects"),
    ("post", "/api/projects"),
    ("get", "/api/projects/{project_id}"),
    ("patch", "/api/projects/{project_id}"),
    ("delete", "/api/projects/{project_id}"),
    ("get", "/api/projects/{project_id}/papers"),
    ("post", "/api/projects/{project_id}/papers"),
    ("post", "/api/projects/{project_id}/pmid-lookup"),
    ("get", "/api/projects/{project_id}/recheck"),
    ("get", "/api/projects/{project_id}/citation-network"),
    ("post", "/api/projects/{project_id}/evaluate-all"),
    ("post", "/api/projects/{project_id}/evaluate-selected"),
    ("post", "/api/projects/{project_id}/download-pdfs"),
    ("get", "/api/search/works"),
    ("get", "/api/papers/{paper_id}"),
    ("patch", "/api/papers/{paper_id}"),
    ("post", "/api/papers/{paper_id}/evaluate"),
    ("post", "/api/papers/{paper_id}/fulltext"),
    ("get", "/api/papers/{paper_id}/evaluation"),
    ("get", "/api/papers/{paper_id}/report"),
    ("get", "/api/tasks/{task_id}"),
}


def test_frontend_endpoints_all_exist(test_app):
    """路由拆分最容易出的错是"拆完忘了 include_router" —— 单测全绿，前端整片 404。

    这里对着 OpenAPI 清单核对前端真正会调用的每一个端点。
    """
    spec = test_app.openapi()
    actual = {
        (method, path)
        for path, ops in spec["paths"].items()
        for method in ops
        if method in ("get", "post", "patch", "delete")
    }
    missing = _EXPECTED_ENDPOINTS - actual
    assert not missing, f"拆分后丢失的端点: {sorted(missing)}"


def test_cors_allows_only_the_local_frontend(client):
    """跨域只放行本机前端来源。

    此前是 ``allow_origins=["*"]`` + ``allow_credentials=True`` —— 任何网页都能
    顶着用户身份调这套接口。前端默认走 Vite 代理（同源），正常使用不经过 CORS，
    所以收紧没有代价；这条测试防的是"哪天图方便又改回通配"。
    """
    preflight = {"Access-Control-Request-Method": "GET"}

    allowed = client.options(
        "/api/health", headers={"Origin": "http://localhost:5173", **preflight}
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"

    denied = client.options(
        "/api/health", headers={"Origin": "https://evil.example", **preflight}
    )
    assert "access-control-allow-origin" not in denied.headers, "通配来源被放行了"
