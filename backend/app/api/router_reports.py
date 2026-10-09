"""综合报告接口：JSON + Markdown 导出 + 引用脉络。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models.paper import Paper
from app.services import report

router = APIRouter(prefix="/api/papers", tags=["reports"])


def _paper_with_eval(db: Session, paper_id: str) -> Paper:
    paper = db.get(Paper, paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")
    if not paper.evaluation:
        raise HTTPException(409, "该论文尚未评估，请先触发评估")
    return paper


@router.get("/{paper_id}/report")
def get_report(paper_id: str, db: Session = Depends(get_db)) -> dict:
    paper = _paper_with_eval(db, paper_id)
    return {"ok": True, "data": report.generate(paper, paper.evaluation), "warnings": []}


@router.get("/{paper_id}/report/export")
def export_report(paper_id: str, format: str = "md", db: Session = Depends(get_db)):
    paper = _paper_with_eval(db, paper_id)
    if format == "md":
        content = report.to_markdown(report.generate(paper, paper.evaluation))
        safe_name = (paper.doi or paper.id).replace("/", "_")
        return PlainTextResponse(
            content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="report_{safe_name}.md"'},
        )
    if format == "json":
        return report.generate(paper, paper.evaluation)
    raise HTTPException(400, "仅支持 format=md 或 format=json")


@router.get("/{paper_id}/citation-graph")
async def citation_graph(paper_id: str, db: Session = Depends(get_db)) -> dict:
    """引用脉络：被谁引用 + 它引用了谁（替换占位引用树）。"""
    paper = db.get(Paper, paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")
    from app.services.citation_graph import get_citation_graph

    graph = await get_citation_graph(paper, mailto=get_settings().fetcher_mailto)
    if graph is None:
        raise HTTPException(409, "该论文无 OpenAlex 标识，无法构建引用树")
    return {"ok": True, "data": graph, "warnings": []}



@router.get("/{paper_id}/citation-network")
async def citation_network(paper_id: str, db: Session = Depends(get_db)) -> dict:
    """GNN 引用网络：PageRank / 社区发现 / 文献耦合 / 教科书式引用识别。"""
    paper = db.get(Paper, paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")
    from app.services.citation_network import get_citation_network

    network = await get_citation_network(paper, mailto=get_settings().fetcher_mailto)
    if network is None:
        raise HTTPException(409, "该论文无 OpenAlex 标识，无法构建引用网络")
    return {"ok": True, "data": network, "warnings": []}