"""元信息接口：健康检查 + 评分规则下发。"""
from __future__ import annotations

from fastapi import APIRouter

from app.services.rules import get_rules

router = APIRouter(prefix="/api/meta", tags=["meta"])


@router.get("/rules")
async def get_rules_endpoint():
    return {"ok": True, "data": get_rules(), "warnings": []}
