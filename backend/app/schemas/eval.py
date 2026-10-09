"""评估结果响应模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class EvaluationOut(BaseModel):
    id: str
    paper_id: str
    composite_score: float | None
    weights: dict[str, float] | None
    component_scores: dict[str, float] | None
    radar_scores: dict[str, float] | None
    confidence: float | None
    abstract_structure: dict[str, Any] | None
    relevance: dict[str, Any] | None
    reproducibility: dict[str, Any] | None
    figure_analysis: dict[str, Any] | None
    llm_assessment: dict[str, Any] | None
    citation_intent: str | None
    decision: str | None
    tier: str | None
    decision_reasons: list[str]
    warnings: list[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EvaluateAllBody(BaseModel):
    """批量评估全部：默认 LLM 深评（内容质量分以 LLM 为唯一来源）；llm=False 走规则档。

    only_stale=True 时只重评"数据已变/没评过"的（见 services/provenance.py），
    源头本来就缺数据的不会被反复重跑。
    """
    llm: bool = True
    only_stale: bool = False


class EvaluateSelectedBody(BaseModel):
    """对选中论文评估：默认 LLM 深评（分档场景下用户主动升级）。"""
    paper_ids: list[str]
    llm: bool = True


class TaskOut(BaseModel):
    task_id: str
    status: str  # queued|running|done|failed
    message: str = ""
