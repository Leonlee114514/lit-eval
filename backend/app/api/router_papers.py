"""单篇论文接口：详情/状态/人工补全/重抓取/触发评估/PDF全文上传。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_orchestrator
from app.models.paper import Paper
from app.schemas.paper import PaperPatch
from app.services import evaluation_service, project_service
from app.services.content import pdf_store

router = APIRouter(prefix="/api/papers", tags=["papers"])


def _get_paper(db: Session, paper_id: str) -> Paper:
    paper = db.get(Paper, paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")
    return paper


@router.get("/{paper_id}")
def get_paper(paper_id: str, db: Session = Depends(get_db)) -> dict:
    paper = _get_paper(db, paper_id)
    data = project_service.paper_summary(paper)
    if paper.evaluation:
        ev = paper.evaluation
        data["evaluation"] = {
            "composite_score": ev.composite_score,
            "decision": ev.decision,
            "tier": ev.tier,
            "component_scores": ev.component_scores,
            "radar_scores": ev.radar_scores,
            "llm_assessment": ev.llm_assessment,
            "abstract_structure": ev.abstract_structure,
            "relevance": ev.relevance,
            "reproducibility": ev.reproducibility,
            "figure_analysis": ev.figure_analysis,
            "citation_intent": ev.citation_intent,
            "warnings": ev.warnings,
            "decision_reasons": ev.decision_reasons,
        }
    return {"ok": True, "data": data, "warnings": []}


@router.get("/{paper_id}/status")
def paper_status(paper_id: str, db: Session = Depends(get_db)) -> dict:
    paper = _get_paper(db, paper_id)
    return {
        "ok": True,
        "data": {
            "paper_id": paper_id,
            "status": paper.status,
            "missing_fields": paper.missing_fields or [],
            "has_evaluation": paper.evaluation is not None,
        },
        "warnings": [],
    }


@router.patch("/{paper_id}")
def patch_paper(paper_id: str, body: PaperPatch, db: Session = Depends(get_db)) -> dict:
    """人工补全缺失字段，补全后状态回退以便重评估。"""
    paper = _get_paper(db, paper_id)
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(paper, field, value)
    # 补全后清理 missing_fields
    if updates:
        paper.missing_fields = [
            f for f in (paper.missing_fields or []) if f not in _PATCHABLE_FIELDS
        ]
        paper.status = "partial" if paper.missing_fields else "metadata_ok"
    db.commit()
    db.refresh(paper)
    return {"ok": True, "data": project_service.paper_summary(paper), "warnings": []}


# 人工可补全的字段直接取自请求模型：这里过去有一份 12 个字的字面清单，
# 与 PaperPatch 一字不差地重复，改一处漏一处。
_PATCHABLE_FIELDS = frozenset(PaperPatch.model_fields)


@router.post("/{paper_id}/refetch")
async def refetch(paper_id: str, db: Session = Depends(get_db)) -> dict:
    paper = _get_paper(db, paper_id)
    if not paper.doi:
        raise HTTPException(400, "该论文无 DOI，无法重抓取")
    orchestrator = get_orchestrator()
    try:
        updated = await evaluation_service.fetch_paper(db, paper.project_id, paper.doi, orchestrator, force=True)
    except Exception as e:
        raise HTTPException(502, f"重抓取失败: {e}")
    return {"ok": True, "data": project_service.paper_summary(updated), "warnings": []}


@router.post("/{paper_id}/evaluate")
async def evaluate_paper(paper_id: str, db: Session = Depends(get_db)) -> dict:
    paper = _get_paper(db, paper_id)
    orchestrator = get_orchestrator()
    try:
        updated = await evaluation_service.evaluate(db, paper_id, orchestrator, use_llm=True)
    except Exception as e:
        raise HTTPException(502, f"评估失败: {e}")
    return {"ok": True, "data": project_service.paper_summary(updated), "warnings": []}


@router.post("/{paper_id}/fulltext")
async def upload_fulltext(
    paper_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    """上传 PDF 全文：解决源站无摘要导致内容分=0 的问题。上传后请重新评估。"""
    paper = _get_paper(db, paper_id)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持 PDF 文件")

    settings = get_settings()
    pdf_dir = settings.data_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    # 按 DOI 命名：同一文献在不同项目里共用同一个文件
    path = pdf_store.pdf_path(pdf_dir, paper.doi, paper_id)

    content = await file.read()
    if not content:
        raise HTTPException(400, "文件为空")
    path.write_bytes(content)
    paper.fulltext_path = str(path)
    paper.status = "text_ready"
    db.commit()
    db.refresh(paper)
    return {"ok": True, "data": project_service.paper_summary(paper), "warnings": []}
