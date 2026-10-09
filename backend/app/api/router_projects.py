"""项目接口：CRUD / 论文列表 / 项目级引用网络 / 数据溯源巡检。

导入与批量任务已拆到 ``router_imports`` / ``router_batch`` ——
原先这三类职责挤在同一个 428 行文件里，任何一类改动都要动它。
URL 前缀与路径**保持不变**，前端无需配合改动。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.project import ProjectCreate, ProjectOut, ProjectUpdate
from app.services import project_service

router = APIRouter(prefix="/api/projects", tags=["projects"])


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
