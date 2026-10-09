"""论文请求/响应模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PaperInput(BaseModel):
    """批量导入单个条目：DOI / 标题 / PMID（至少一项）。"""
    doi: str | None = None
    title: str | None = None
    pmid: str | None = None


class PaperBatchInput(BaseModel):
    """批量导入请求。"""
    inputs: list[PaperInput] = Field(..., min_length=1)


class DownloadPdfsBody(BaseModel):
    """批量下载选中论文 PDF 全文。"""
    paper_ids: list[str]


class PaperPatch(BaseModel):
    """人工补全缺失字段。"""
    impact_factor: float | None = None
    jcr_quartile: str | None = None
    cas_zone: int | None = None
    abstract: str | None = None
    title: str | None = None
    journal: str | None = None
    publication_year: int | None = None
    has_data_availability_stmt: bool | None = None
    has_code_repo: bool | None = None
    has_supplement: bool | None = None
    is_classic: bool | None = None
    is_retracted: bool | None = None


class PaperOut(BaseModel):
    """论文列表/详情视图（含评估简况）。"""
    id: str
    project_id: str
    doi: str | None
    title: str | None
    authors: list[dict[str, Any]] | None
    journal: str | None
    publication_year: int | None
    cited_by_count: int | None
    citation_percentile: float | None
    impact_factor: float | None
    jcr_quartile: str | None
    cas_zone: int | None
    journal_percentile: float | None
    field_of_study: str | None
    abstract: str | None
    keywords: list[str] | None
    status: str
    missing_fields: list[str]
    is_classic: bool
    created_at: datetime
    updated_at: datetime
    evaluation: dict[str, Any] | None = None  # 简况：composite/decision/radar 等

    model_config = {"from_attributes": True}
