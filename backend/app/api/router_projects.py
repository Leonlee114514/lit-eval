"""项目与批量导入接口。"""
from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_orchestrator
from app.models.paper import Paper
from app.schemas.eval import EvaluateAllBody, EvaluateSelectedBody
from app.schemas.paper import DownloadPdfsBody, PaperBatchInput
from app.schemas.project import ProjectCreate, ProjectOut, ProjectUpdate
from app.services import evaluation_service, project_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["projects"])

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s]+", re.IGNORECASE)


def _clean_doi(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    # 去掉 URL 前缀
    if "doi.org/" in raw:
        raw = raw.split("doi.org/", 1)[-1]
    m = DOI_RE.match(raw)
    return m.group(0) if m else None


@router.post("")
def create_project(body: ProjectCreate, db: Session = Depends(get_db)) -> dict:
    project = project_service.create(
        db, body.name, body.description, body.research_topic, body.keywords
    )
    return {"ok": True, "data": ProjectOut.model_validate(project), "warnings": []}


@router.get("")
def list_projects(db: Session = Depends(get_db)) -> dict:
    return {"ok": True, "data": project_service.list_all(db), "warnings": []}


@router.get("/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = project_service.get(db, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    data = ProjectOut.model_validate(project).model_dump()
    data["paper_count"] = len(project.papers)
    return {"ok": True, "data": data, "warnings": []}


@router.patch("/{project_id}")
def update_project(project_id: str, body: ProjectUpdate, db: Session = Depends(get_db)) -> dict:
    """部分更新项目（名称/描述/研究主题/关键词）。"""
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(400, "没有可更新的字段")
    project = project_service.update(db, project_id, fields)
    if not project:
        raise HTTPException(404, "项目不存在")
    return {"ok": True, "data": ProjectOut.model_validate(project), "warnings": []}


@router.delete("/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db)) -> dict:
    if not project_service.delete(db, project_id):
        raise HTTPException(404, "项目不存在")
    return {"ok": True, "data": {"deleted": project_id}, "warnings": []}


@router.get("/{project_id}/papers")
def list_papers(project_id: str, db: Session = Depends(get_db)) -> dict:
    """项目内论文列表（含评估简况），供列表页/散点矩阵。"""
    project = project_service.get(db, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    papers = [project_service.paper_summary(p) for p in project.papers]
    return {"ok": True, "data": papers, "warnings": []}



@router.get("/{project_id}/citation-network")
async def project_citation_network(project_id: str, db: Session = Depends(get_db)) -> dict:
    """项目级 GNN：共享参考文献 / 文献耦合 / 核心文献 / 教科书式引用。"""
    project = project_service.get(db, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    from app.config import get_settings
    from app.services.project_network import get_project_network

    network = await get_project_network(
        project_id, list(project.papers), mailto=get_settings().fetcher_mailto
    )
    if network is None:
        raise HTTPException(409, "该项目有效 OpenAlex 论文少于 2 篇或没有共享参考文献")
    return {"ok": True, "data": network, "warnings": []}

@router.post("/{project_id}/pmid-lookup")
async def pmid_lookup(project_id: str, body: dict, db: Session = Depends(get_db)) -> dict:
    """PMID → DOI 批量转换（NCBI E-utilities）。返回 {pmid: doi}。"""
    project = project_service.get(db, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    pmids = [str(p).strip() for p in (body.get("pmids") or []) if str(p).strip().isdigit()]
    if not pmids:
        return {"ok": True, "data": {}, "warnings": ["无有效 PMID"]}

    from app.config import get_settings
    from app.services.pubmed import batch_pmid_to_doi

    mapping = await batch_pmid_to_doi(pmids, mailto=get_settings().fetcher_mailto)
    return {"ok": True, "data": mapping, "warnings": []}


@router.get("/{project_id}/recheck")
async def recheck_status(project_id: str, db: Session = Depends(get_db)) -> dict:
    """数据溯源巡检：哪些文献的评估不可全信、分别为什么。

    - ``actionable``：重评可能改善（没评过 / 无指纹 / 数据已变）→ 可喂给 evaluate-all 的 only_stale
    - ``insufficient_data``：源头本来就缺数据（重跑也不会变）→ 应如实标"数据不足/无法核验"
    """
    project = project_service.get(db, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    from app.services import provenance

    counts: dict[str, int] = {}
    actionable: list[dict] = []
    insufficient: list[dict] = []
    for p in project.papers:
        reason = provenance.recheck_reason(p, p.evaluation)
        if not reason:
            continue
        head = reason.split(":", 1)[0]
        counts[head] = counts.get(head, 0) + 1
        ev = p.evaluation
        row = {
            "id": p.id,
            "doi": p.doi,
            "title": p.title,
            "reason": reason,
            "missing_dimensions": provenance.missing_dimensions(ev),
            "composite_score": ev.composite_score if ev else None,
            "tier": ev.tier if ev else None,
        }
        (actionable if reason in provenance.ACTIONABLE_REASONS else insufficient).append(row)

    return {
        "ok": True,
        "data": {
            "total": len(project.papers),
            "actionable_count": len(actionable),
            "insufficient_data_count": len(insufficient),
            "by_reason": counts,
            "actionable": actionable,
            "insufficient_data": insufficient,
        },
        "warnings": [],
    }


@router.post("/{project_id}/papers")
async def import_papers(
    project_id: str,
    body: PaperBatchInput,
    db: Session = Depends(get_db),
) -> dict:
    """批量导入 DOI / 标题 / PMID → 并发抓取 → 返回论文列表。

    标题走 OpenAlex 全文搜索兜底；PMID 走 PubMed 直接源（无 DOI 也入库），
    有 DOI 时自动叠加常规三源抓取。
    """
    project = project_service.get(db, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    tasks: list[tuple[str, str]] = []  # (kind, value) kind ∈ {"doi", "title", "pmid"}
    skipped: list[str] = []
    for item in body.inputs:
        if item.doi:
            doi = _clean_doi(item.doi)
            if not doi:
                skipped.append(item.doi)
                continue
            tasks.append(("doi", doi))
        elif item.title and item.title.strip():
            tasks.append(("title", item.title.strip()))
        elif item.pmid and item.pmid.strip().isdigit():
            tasks.append(("pmid", item.pmid.strip()))

    # 去重（DOI / 标题 / PMID 各自按原文去重）
    seen: set[str] = set()
    unique_tasks = []
    for kind, value in tasks:
        key = f"{kind}:{value.lower()}"
        if key not in seen:
            seen.add(key)
            unique_tasks.append((kind, value))

    if not unique_tasks:
        return {
            "ok": True,
            "data": {"papers": [], "skipped": skipped, "errors": []},
            "warnings": ["没有有效的 DOI、标题或 PMID 可导入"],
        }

    orchestrator = get_orchestrator()

    # 并发抓取时每个任务用独立会话（SQLAlchemy Session 非线程安全，不能跨 asyncio 任务共享）
    from app.db import SessionLocal

    async def _fetch(task: tuple[str, str]) -> dict:
        kind, value = task
        try:
            with SessionLocal() as sess:
                if kind == "doi":
                    paper = await evaluation_service.fetch_paper(sess, project_id, value, orchestrator)
                elif kind == "title":
                    paper = await evaluation_service.fetch_paper_by_title(sess, project_id, value, orchestrator)
                else:
                    paper = await evaluation_service.fetch_paper_by_pmid(sess, project_id, value, orchestrator)
                return {"paper": project_service.paper_summary(paper)}
        except Exception as e:
            return {"error": f"{kind}:{value} → {type(e).__name__}: {e}"}

    data = await asyncio.gather(*[_fetch(t) for t in unique_tasks])
    papers = [r["paper"] for r in data if "paper" in r]
    errors = [r["error"] for r in data if "error" in r]
    return {"ok": True, "data": {"papers": papers, "skipped": skipped, "errors": errors}, "warnings": []}


async def _evaluate_paper_ids(paper_ids: list[str], use_llm: bool) -> dict:
    """逐个评估（独立会话，串行）。evaluate-all / evaluate-selected 共用。"""
    orchestrator = get_orchestrator()
    # 每次评估用独立会话，避免跨请求 session 复用
    from app.db import SessionLocal

    results = []
    for pid in paper_ids:
        with SessionLocal() as sess:
            try:
                await evaluation_service.evaluate(sess, pid, orchestrator, use_llm=use_llm)
                results.append({"paper_id": pid, "status": "done"})
            except Exception as e:
                results.append({"paper_id": pid, "status": "failed", "error": str(e)})
        # LLM 深评时每篇间隔 1s，压低 Moonshot RPM 限流风险
        if use_llm:
            await asyncio.sleep(1)
    return {"results": results}


async def _download_paper_ids(paper_ids: list[str], progress) -> dict:
    """逐篇下载 PDF 全文（独立会话，串行）。成功写入 fulltext_path + status=text_ready。

    与 _evaluate_paper_ids 同模式：单篇失败只记 fetch_log + results，不阻塞整批。
    """
    from app.config import get_settings
    from app.services.content import pdf_downloader

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

    from app.db import SessionLocal

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
                dest = pdf_dir / f"{pid}.pdf"
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

        stale_ids = [
            p.id for p in project.papers if provenance.is_actionable(p, p.evaluation)
        ]
        if not stale_ids:
            return {
                "ok": True,
                "data": {"task_id": None, "skipped": True, "paper_count": 0},
                "warnings": ["没有需要重评的文献（数据未变、且无缺数据）"],
            }
        paper_ids = stale_ids

    from app.services.tasks import broker

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
    papers = (
        db.query(Paper)
        .filter(Paper.project_id == project_id, Paper.id.in_(body.paper_ids))
        .all()
    )
    paper_ids = [p.id for p in papers]
    if not paper_ids:
        raise HTTPException(400, "未提供属于该项目的论文")

    from app.services.tasks import broker

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
    papers = (
        db.query(Paper)
        .filter(Paper.project_id == project_id, Paper.id.in_(body.paper_ids))
        .all()
    )
    paper_ids = [p.id for p in papers]
    if not paper_ids:
        raise HTTPException(400, "未提供属于该项目的论文")

    from app.services.tasks import broker

    task_id: str | None = None

    def _progress(msg: str) -> None:
        if task_id:
            broker.set_progress(task_id, msg)

    task_id = broker.submit_async(
        lambda: _download_paper_ids(paper_ids, _progress), name="download-pdfs"
    )
    return {"ok": True, "data": {"task_id": task_id}, "warnings": []}
