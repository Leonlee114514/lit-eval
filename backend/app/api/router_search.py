"""文献搜索接口：按主题/关键词返回 OpenAlex 候选论文列表。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_orchestrator
from app.models.project import Project
from app.services import search_service
from app.services.fetchers.base import SourceUnavailable

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("/works")
async def search_works(
    q: str = Query(..., min_length=1, max_length=500, description="搜索关键词/主题"),
    per_page: int = Query(200, ge=1, le=200, description="搜索池大小（默认拉到 OpenAlex 单页上限 200，前端在此池内分页）"),
    year_from: int | None = Query(None, ge=1900, le=2100),
    year_to: int | None = Query(None, ge=1900, le=2100),
    work_type: str | None = Query(None, description="article/review/preprint 等"),
    query_mode: str | None = Query(
        None,
        pattern="^(raw|expanded)$",
        description="检索式构造方式；缺省用服务端 SEARCH_QUERY_MODE。raw=原样透传，expanded=同义词 OR 扩展",
    ),
    project_id: str | None = Query(None, description="带上后预计算相关性分并标记已导入"),
    db: Session = Depends(get_db),
) -> dict:
    if year_from and year_to and year_from > year_to:
        raise HTTPException(400, "year_from 不能大于 year_to")
    if project_id and db.get(Project, project_id) is None:
        raise HTTPException(404, "项目不存在")

    orchestrator = get_orchestrator()
    try:
        data = await search_service.search_works(
            db, orchestrator, q.strip(), per_page=per_page,
            year_from=year_from, year_to=year_to, work_type=work_type,
            project_id=project_id, query_mode=query_mode,
        )
    except SourceUnavailable as e:
        # 限流/配额耗尽不是"服务器内部错误"，而是"上游暂时不可用"：
        # 503 + 人话（含恢复时间与配置 API key 的建议），前端直接显示 detail。
        raise HTTPException(status_code=503, detail=e.user_message()) from e
    return {"ok": True, "data": data, "warnings": []}
