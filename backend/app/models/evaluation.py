"""评估结果：1 Paper -> 1 Evaluation（重复评估覆盖）。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    paper_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("papers.id", ondelete="CASCADE"), unique=True, index=True
    )

    # ---- 综合分 ----
    composite_score: Mapped[float | None] = mapped_column(Float)  # 0-100
    weights: Mapped[dict | None] = mapped_column(JSON)
    component_scores: Mapped[dict | None] = mapped_column(JSON)  # 五路 0-100
    radar_scores: Mapped[dict | None] = mapped_column(JSON)  # 五维 0-100
    confidence: Mapped[float | None] = mapped_column(Float)  # 元数据完整率 0-1

    # ---- 内容评估（定量 + 规则化） ----
    abstract_structure: Mapped[dict | None] = mapped_column(JSON)
    relevance: Mapped[dict | None] = mapped_column(JSON)  # {score, method, user_topic}
    reproducibility: Mapped[dict | None] = mapped_column(JSON)
    figure_analysis: Mapped[dict | None] = mapped_column(JSON)  # 图表质量检测（PDF 全文）

    # ---- LLM 深度评估（可为空 → 前端显示规则化降级） ----
    llm_assessment: Mapped[dict | None] = mapped_column(JSON)
    citation_intent: Mapped[str | None] = mapped_column(String(20))  # background|method|compare|theory

    # ---- 决策 ----
    decision: Mapped[str | None] = mapped_column(String(20))  # deep_read|background_only|reject
    tier: Mapped[str | None] = mapped_column(String(20))  # 分数段推荐档：high_priority|recommended|conditional|not_recommended
    decision_reasons: Mapped[list | None] = mapped_column(JSON, default=list)
    warnings: Mapped[list | None] = mapped_column(JSON, default=list)

    # ---- 数据溯源（v9）：这份分数是基于什么数据/规则算出来的 ----
    # data_status：六维完整性（ok/missing…）。决策三态门用它判 unknown，此前只算不落库。
    data_status: Mapped[dict | None] = mapped_column(JSON)
    # data_fingerprint：打分输入（指标值 + 主题 + 正文摘要 + 规则版本）的 SHA-256。
    # 与当前 paper 状态重算比对 → 不相等即"数据已变，分数过时"。见 services/provenance.py
    data_fingerprint: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    paper: Mapped["Paper"] = relationship(back_populates="evaluation")  # noqa: F821
