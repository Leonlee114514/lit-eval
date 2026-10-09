"""评估状态机测试：失败必须落终态 ``failed``。

背景（2026-10 review 的 Critical）：``evaluate()`` 开头就把 ``status="evaluating"``
commit 了，中途失败若不写回终态，这篇论文会永远停在"评估中"——前端分不清"卡死"
与"进行中"，也没有任何字段说明为什么失败（09-16 那次 51 篇全空只能靠手工 force 捞回）。

覆盖四条：包装层写终态、原始异常不被吞、成功路径不被误伤、论文已消失时不二次崩。
"""
from __future__ import annotations

import asyncio

import pytest

from app.models.paper import Paper
from app.models.project import Project


@pytest.fixture
def db(test_app):
    """隔离库会话。

    必须依赖 ``test_app``：只有它会把 ``app.db`` 的 engine 换成 in-memory SQLite，
    否则这里会写到真实的 ``data/lit_eval.db``。
    """
    import app.db as dbmod

    session = dbmod.SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_paper(db, project_id: str) -> Paper:
    if db.get(Project, project_id) is None:
        db.add(Project(id=project_id, name="失败终态测试项目"))
        db.commit()
    paper = Paper(project_id=project_id, doi="10.0000/fail.test", status="metadata_ok")
    db.add(paper)
    db.commit()
    return paper


def test_pipeline_exception_writes_failed_terminal_state(db, monkeypatch):
    from app.services import evaluation_service

    paper = _make_paper(db, "p-fail-1")

    async def _boom(*args, **kwargs):
        raise RuntimeError("模拟 LLM 超时")

    monkeypatch.setattr(evaluation_service, "_run_evaluation", _boom)

    with pytest.raises(RuntimeError, match="模拟 LLM 超时"):
        asyncio.run(evaluation_service.evaluate(db, paper.id))

    db.expire_all()
    row = db.get(Paper, paper.id)
    assert row.status == "failed", "失败必须落终态，否则会永远停在 evaluating"
    errors = [entry.get("error", "") for entry in (row.fetch_log or [])]
    assert any(e.startswith("RuntimeError: 模拟 LLM 超时") for e in errors), errors


def test_pipeline_exception_is_not_swallowed(db, monkeypatch):
    """原始异常必须继续上抛——router 靠它把 502 文案交给前端。"""
    from app.services import evaluation_service

    paper = _make_paper(db, "p-fail-2")

    class _CustomError(Exception):
        pass

    async def _boom(*args, **kwargs):
        raise _CustomError("自定义失败")

    monkeypatch.setattr(evaluation_service, "_run_evaluation", _boom)

    with pytest.raises(_CustomError):
        asyncio.run(evaluation_service.evaluate(db, paper.id))


def test_success_path_is_not_marked_failed(db, monkeypatch):
    """成功路径不能被包装层误伤（try/except 的回归保护）。"""
    from app.services import evaluation_service

    paper = _make_paper(db, "p-fail-3")

    async def _ok(session, target, orchestrator, use_llm):
        target.status = "done"
        session.commit()
        return target

    monkeypatch.setattr(evaluation_service, "_run_evaluation", _ok)

    result = asyncio.run(evaluation_service.evaluate(db, paper.id))
    assert result.status == "done"


def test_mark_failed_tolerates_missing_paper(db):
    """论文已被并发删除时，落终态不能抛——否则会盖掉原始异常。"""
    from app.services import evaluation_service

    paper = _make_paper(db, "p-fail-4")
    paper_id = paper.id
    db.delete(paper)
    db.commit()

    evaluation_service._mark_failed(db, paper_id, RuntimeError("原始错误"))

    db.expire_all()
    assert db.get(Paper, paper_id) is None
