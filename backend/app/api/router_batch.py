"""批量后台任务接口：批量评估（全部 / 选中）与批量下载 PDF 全文。

从 ``router_projects`` 拆出来。三类职责的共同点是**都不在请求里做重活**：
提交给 ``broker`` 后台执行，返回 ``task_id`` 供前端轮询。
两个 ``_xxx_paper_ids`` 就是后台执行体，与提交它们的端点放在一起最直观。

URL 前缀与路径保持不变，前端无需配合改动。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.deps import get_orchestrator
from app.models.paper import Paper
from app.schemas.eval import EvaluateAllBody, EvaluateSelectedBody
from app.schemas.paper import DownloadPdfsBody
from app.services import evaluation_service, project_service
from app.services.tasks import broker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["batch"])


async def _evaluate_paper_ids(paper_ids: list[str], use_llm: bool) -> dict:
    """逐个评估（独立会话，串行）。evaluate-all / evaluate-selected 共用。"""
    orchestrator = get_orchestrator()
    results = []
    for pid in paper_ids:
        # 每次评估用独立会话，避免跨请求 session 复用
        with SessionLocal() as sess:
            try:
                await evaluation_service.evaluate(sess, pid, orchestrator, use_llm=use_llm)
                results.append({"paper_id": pid, "status": "done"})
            except Exception as e:
                # 失败终态由 evaluation_service 负责写入（status=failed + fetch_log），
                # 这里只把错误带给调用方看
                results.append({"paper_id": pid, "status": "failed", "error": str(e)})
        # LLM 深评时每篇间隔 1s，压低 Moonshot RPM 限流风险
        if use_llm:
            await asyncio.sleep(1)
    return {"results": results}


async def _download_paper_ids(paper_ids: list[str], progress) -> dict:
    """逐篇下载 PDF 全文（独立会话，串行）。成功写入 fulltext_path + status=text_ready。

    与 _evaluate_paper_ids 同模式：单篇失败只记 fetch_log + results，不阻塞整批。
    """
    from app.services.content import pdf_downloader, pdf_store

    settings = get_settings()
    pdf_dir = settings.data_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    email = settings.unpaywall_email or settings.fetcher_mailto
    proxy = settings.scihub_proxy

    # batch 级预检一次：Clash / WebBridge 不在线就整批降级跳过对应通道，不阻塞
    scihub_enabled = bool(proxy) and pdf_downloader.proxy_available(proxy)
    if proxy and not scihub_enabled:
        progress(f"代理不可用({proxy})，跳过 Sci-Hub 通道")
    use_browser = settings.use_webbridge and pdf_downloader.browser_available(
        settings.webbridge_url, settings.webbridge_session
    )
    if settings.use_webbridge and not use_browser:
        progress("WebBridge 离线，跳过浏览器通道")

    results = []
    total = len(paper_ids)
    for i, pid in enumerate(paper_ids, 1):
        with SessionLocal() as sess:
            paper = sess.get(Paper, pid)
            try:
                if paper is None:
                    results.append({"paper_id": pid, "status": "failed", "reason": "论文不存在"})
                    continue
                if paper.fulltext_path:
                    results.append({"paper_id": pid, "status": "skipped", "reason": "已有全文"})
                    continue
                if not paper.doi:
                    results.append({"paper_id": pid, "status": "failed", "reason": "无 DOI"})
                    continue
                # 按 DOI 命名 → 同一文献在不同项目里共用同一个文件（不重复落盘）
                dest = pdf_store.pdf_path(pdf_dir, paper.doi, pid)
                res = await asyncio.to_thread(
                    pdf_downloader.download_pdf,
                    paper.doi, dest,
                    email=email, proxy=proxy,
                    use_browser=use_browser, scihub_enabled=scihub_enabled,
                    webbridge_url=settings.webbridge_url,
                    webbridge_session=settings.webbridge_session,
                )
                if res.ok:
                    paper.fulltext_path = str(dest)
                    paper.status = "text_ready"
                    log = paper.fetch_log or []
                    log.append({"source": f"pdf:{res.channel}", "ok": True,
                                "error": None, "fields": ["fulltext_path"], "path": str(dest)})
                    paper.fetch_log = log
                    results.append({"paper_id": pid, "status": "done", "channel": res.channel})
                else:
                    log = paper.fetch_log or []
                    log.append({"source": "pdf", "ok": False, "error": res.reason, "fields": []})
                    paper.fetch_log = log
                    results.append({"paper_id": pid, "status": "failed", "reason": res.reason})
                sess.commit()
            except Exception as e:
                results.append({"paper_id": pid, "status": "failed",
                                "reason": f"{type(e).__name__}: {e}"})
        progress(f"下载中 {i}/{total}")
        await asyncio.sleep(0.5)  # 礼貌间隔，压低镜像限流
    done = sum(1 for r in results if r["status"] == "done")
    progress(f"完成:{done}/{total} 成功")
    return {"results": results}


def _project_paper_ids(db: Session, project_id: str, paper_ids: list[str]) -> list[str]:
    """把请求里的 paper_id 过滤成"确实属于该项目"的集合（越权保护）。"""
    rows = (
        db.query(Paper)
        .filter(Paper.project_id == project_id, Paper.id.in_(paper_ids))
        .all()
    )
    return [p.id for p in rows]


@router.post("/{project_id}/evaluate-all")
async def evaluate_all(
    project_id: str,
    body: EvaluateAllBody | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """批量评估全部：异步任务，返回 task_id 供轮询。默认 LLM 档（内容质量分以 LLM 为唯一来源）。"""
    project = project_service.get(db, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    paper_ids = [p.id for p in project.papers]
    use_llm = bool(body.llm) if body else True
    if body is not None and body.only_stale:
        from app.services import provenance

        paper_ids = [p.id for p in project.papers if provenance.is_actionable(p, p.evaluation)]
        if not paper_ids:
            return {
                "ok": True,
                "data": {"task_id": None, "skipped": True, "paper_count": 0},
                "warnings": ["没有需要重评的文献（数据未变、且无缺数据）"],
            }

    task_id = broker.submit_async(
        lambda: _evaluate_paper_ids(paper_ids, use_llm), name="evaluate-all"
    )
    return {"ok": True, "data": {"task_id": task_id}, "warnings": []}


@router.post("/{project_id}/evaluate-selected")
async def evaluate_selected(
    project_id: str,
    body: EvaluateSelectedBody,
    db: Session = Depends(get_db),
) -> dict:
    """对选中论文评估（默认 LLM 深评）。只接受属于该项目的 paper_id。"""
    project = project_service.get(db, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    paper_ids = _project_paper_ids(db, project_id, body.paper_ids)
    if not paper_ids:
        raise HTTPException(400, "未提供属于该项目的论文")

    task_id = broker.submit_async(
        lambda: _evaluate_paper_ids(paper_ids, bool(body.llm)), name="evaluate-selected"
    )
    return {"ok": True, "data": {"task_id": task_id}, "warnings": []}


@router.post("/{project_id}/download-pdfs")
async def download_pdfs(
    project_id: str,
    body: DownloadPdfsBody,
    db: Session = Depends(get_db),
) -> dict:
    """批量下载选中论文 PDF 全文：异步任务，返回 task_id 供轮询。

    PDF 直接写入 {data_dir}/pdfs/{paper_id}.pdf 并置 fulltext_path + status=text_ready，
    与手动上传共用同一槽位；随后批量评估自动读全文。
    """
    project = project_service.get(db, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    paper_ids = _project_paper_ids(db, project_id, body.paper_ids)
    if not paper_ids:
        raise HTTPException(400, "未提供属于该项目的论文")

    task_id: str | None = None

    def _progress(msg: str) -> None:
        if task_id:
            broker.set_progress(task_id, msg)

    task_id = broker.submit_async(
        lambda: _download_paper_ids(paper_ids, _progress), name="download-pdfs"
    )
    return {"ok": True, "data": {"task_id": task_id}, "warnings": []}
