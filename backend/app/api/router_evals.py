"""评估结果与异步任务状态接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.paper import Paper
from app.schemas.eval import EvaluationOut

router = APIRouter(tags=["evals"])


@router.get("/api/papers/{paper_id}/evaluation")
def get_evaluation(paper_id: str, db: Session = Depends(get_db)) -> dict:
    paper = db.get(Paper, paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")
    if not paper.evaluation:
        return {"ok": True, "data": None, "warnings": ["尚未评估"]}
    return {"ok": True, "data": EvaluationOut.model_validate(paper.evaluation).model_dump(), "warnings": []}


@router.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    from app.services.tasks import broker

    return {"ok": True, "data": broker.status(task_id), "warnings": []}
