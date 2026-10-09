"""项目服务：CRUD + 项目内论文列表（含评估简况）。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.evaluation import Evaluation
from app.models.paper import Paper
from app.models.project import Project


def create(db: Session, name: str, description: str, research_topic: str, keywords: list[str]) -> Project:
    project = Project(
        name=name,
        description=description,
        research_topic=research_topic,
        keywords=keywords,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def get(db: Session, project_id: str) -> Project | None:
    return db.get(Project, project_id)


def list_all(db: Session) -> list[dict]:
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "research_topic": p.research_topic,
            "keywords": p.keywords,
            "created_at": p.created_at,
            "paper_count": len(p.papers),
        }
        for p in projects
    ]


def update(db: Session, project_id: str, fields: dict) -> Project | None:
    """部分字段更新（None 跳过）。返回 None 表示项目不存在。"""
    project = db.get(Project, project_id)
    if not project:
        return None
    for key, value in fields.items():
        if value is not None:
            setattr(project, key, value)
    db.commit()
    db.refresh(project)
    return project


def delete(db: Session, project_id: str) -> bool:
    project = db.get(Project, project_id)
    if not project:
        return False
    db.delete(project)
    db.commit()
    return True


def paper_summary(paper: Paper) -> dict:
    """论文列表行：元数据 + 评估简况 + 数据溯源标记。"""
    from app.services import provenance

    ev = paper.evaluation
    reason = provenance.recheck_reason(paper, ev) if ev else None
    return {
        "id": paper.id,
        "project_id": paper.project_id,
        "doi": paper.doi,
        "pmid": paper.pmid,
        "title": paper.title,
        "authors": paper.authors,
        "journal": paper.journal,
        "publication_year": paper.publication_year,
        "cited_by_count": paper.cited_by_count,
        "citation_percentile": paper.citation_percentile,
        "impact_factor": paper.impact_factor,
        "jcr_quartile": paper.jcr_quartile,
        "cas_zone": paper.cas_zone,
        "journal_percentile": paper.journal_percentile,
        "field_of_study": paper.field_of_study,
        "abstract": paper.abstract,
        "keywords": paper.keywords,
        "status": paper.status,
        "has_fulltext": bool(paper.fulltext_path),
        "missing_fields": paper.missing_fields or [],
        "is_classic": paper.is_classic,
        "is_retracted": paper.is_retracted,
        "created_at": paper.created_at,
        "updated_at": paper.updated_at,
        # 数据溯源：needs_recheck = 重跑可能改善；data_insufficient = 源头本来就缺数据
        # （重跑也不会变，应如实标"数据不足"，见 services/provenance.py）
        "recheck_reason": reason,
        "needs_recheck": reason in provenance.ACTIONABLE_REASONS,
        "missing_dimensions": provenance.missing_dimensions(ev) if ev else [],
        "evaluation": (
            {
                "composite_score": ev.composite_score,
                "decision": ev.decision,
                "tier": ev.tier,
                "radar_scores": ev.radar_scores,
                "relevance_score": (ev.relevance or {}).get("score"),
                "citation_intent": ev.citation_intent,
            }
            if ev
            else None
        ),
    }
