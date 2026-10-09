"""文献主表：元数据 + 指标 + 抓取状态。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Paper(Base):
    __tablename__ = "papers"
    # 同一文献可在不同项目出现；DOI 在同一项目内唯一
    __table_args__ = (
        UniqueConstraint("project_id", "doi", name="uq_project_doi"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )

    # ---- 元数据 ----
    doi: Mapped[str | None] = mapped_column(String(200), index=True)
    pmid: Mapped[str | None] = mapped_column(String(30), index=True)  # PubMed 直接源标识
    title: Mapped[str | None] = mapped_column(Text)
    authors: Mapped[list | None] = mapped_column(JSON)  # [{name, affiliation, orcid, h_index, is_corresponding}]
    corresponding_h_index: Mapped[float | None] = mapped_column(Float)
    journal: Mapped[str | None] = mapped_column(String(300))
    issn: Mapped[str | None] = mapped_column(String(50))
    openalex_source_id: Mapped[str | None] = mapped_column(String(50))  # 期刊 W/S 号
    openalex_work_id: Mapped[str | None] = mapped_column(String(50))
    publication_year: Mapped[int | None] = mapped_column()
    publication_date: Mapped[datetime | None] = mapped_column(Date)
    abstract: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[list | None] = mapped_column(JSON, default=list)
    fulltext_path: Mapped[str | None] = mapped_column(String(500))
    # 文章类型（Review/Original Research），供被引泡沫告警
    work_type: Mapped[str | None] = mapped_column(String(50))

    # ---- 引用指标（三源合并后的权威值） ----
    cited_by_count: Mapped[int | None] = mapped_column()
    cited_by_5yr: Mapped[int | None] = mapped_column()
    self_citation_count: Mapped[int | None] = mapped_column()  # 参考自引条数（Crossref 口径），供"自引偏高"告警
    reference_count: Mapped[int | None] = mapped_column()  # 参考文献总数（Crossref），自引率分母
    citation_percentile: Mapped[float | None] = mapped_column(Float)  # 0-100 同年百分位
    field_of_study: Mapped[str | None] = mapped_column(String(200))
    subfield_id: Mapped[str | None] = mapped_column(String(50))
    field_id: Mapped[str | None] = mapped_column(String(20))  # 学科半衰期用（如 "17"=CS）

    # ---- 期刊指标 ----
    impact_factor: Mapped[float | None] = mapped_column(Float)  # 手填或快照，仅展示
    jcr_quartile: Mapped[str | None] = mapped_column(String(5))  # Q1-Q4
    cas_zone: Mapped[int | None] = mapped_column()  # 中科院 1-4
    journal_2yr_mean: Mapped[float | None] = mapped_column(Float)  # OpenAlex 伪 IF
    journal_percentile: Mapped[float | None] = mapped_column(Float)  # 学科内百分位 0-100

    # ---- 书目信息（标准引用格式用） ----
    volume: Mapped[str | None] = mapped_column(String(50))
    issue: Mapped[str | None] = mapped_column(String(50))
    pages: Mapped[str | None] = mapped_column(String(50))

    # ---- 可重复性 ----
    has_data_availability_stmt: Mapped[bool | None] = mapped_column(Boolean)
    has_code_repo: Mapped[bool | None] = mapped_column(Boolean)
    has_supplement: Mapped[bool | None] = mapped_column(Boolean)
    is_retracted: Mapped[bool] = mapped_column(Boolean, default=False)  # 撤稿标记（人工/后续接口）

    # ---- 状态与人工补全 ----
    status: Mapped[str] = mapped_column(String(30), default="queued")  # queued|fetching|metadata_ok|partial|text_ready|evaluating|done|failed
    missing_fields: Mapped[list | None] = mapped_column(JSON, default=list)
    fetch_log: Mapped[list | None] = mapped_column(JSON, default=list)
    is_classic: Mapped[bool] = mapped_column(Boolean, default=False)  # 用户手动冻结时效

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    project: Mapped["Project"] = relationship(back_populates="papers")  # noqa: F821
    evaluation: Mapped["Evaluation | None"] = relationship(  # noqa: F821
        back_populates="paper", uselist=False, cascade="all, delete-orphan"
    )
