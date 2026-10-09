"""文献导入接口：DOI / 标题 / PMID 批量抓取落库，以及 PMID → DOI 转换。

从 ``router_projects`` 拆出来（原先一个文件住着 7 类职责，任何一类改动都要动同一个文件）。
URL 前缀与路径**保持不变**，前端无需配合改动。
"""
from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.deps import get_orchestrator
from app.schemas.paper import PaperBatchInput
from app.services import evaluation_service, project_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["imports"])

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


@router.post("/{project_id}/pmid-lookup")
async def pmid_lookup(project_id: str, body: dict, db: Session = Depends(get_db)) -> dict:
    """PMID → DOI 批量转换（NCBI E-utilities）。返回 {pmid: doi}。"""
    project = project_service.get(db, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    pmids = [str(p).strip() for p in (body.get("pmids") or []) if str(p).strip().isdigit()]
    if not pmids:
        return {"ok": True, "data": {}, "warnings": ["无有效 PMID"]}

    from app.services.pubmed import batch_pmid_to_doi

    mapping = await batch_pmid_to_doi(pmids, mailto=get_settings().fetcher_mailto)
    return {"ok": True, "data": mapping, "warnings": []}
