"""综合报告响应模型（结构化 JSON）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ReportOut(BaseModel):
    paper_id: str
    title: str | None
    doi: str | None
    journal: str | None
    publication_year: int | None

    paper_summary: dict[str, Any]
    component_scores: dict[str, float]
    radar_scores: dict[str, float]
    composite_score: float | None
    confidence: float | None
    decision: str | None
    tier: str | None
    decision_reasons: list[str]
    content_eval: dict[str, Any]          # abstract_structure + relevance + reproducibility
    llm_assessment: dict[str, Any] | None
    citation_intent: str | None
    warnings: list[str]
    source_trace: list[dict[str, Any]]    # 各源抓取留痕
