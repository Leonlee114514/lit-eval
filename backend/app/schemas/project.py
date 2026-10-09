"""项目请求/响应模型。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    research_topic: str = ""
    keywords: list[str] = []


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    research_topic: str | None = None
    keywords: list[str] | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str
    research_topic: str
    keywords: list[str]
    created_at: datetime
    paper_count: int = 0

    model_config = {"from_attributes": True}
