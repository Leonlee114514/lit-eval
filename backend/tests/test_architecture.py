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
