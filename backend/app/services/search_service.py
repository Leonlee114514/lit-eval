"""文献搜索编排：OpenAlex 关键词搜索 → 相关性重排 → 已导入标记。

- 缓存原始搜索结果 1 小时（oasearch: 前缀），避免重复请求。
- 带 project_id 时：用项目 research_topic/keywords 对每条结果预计算相关性分
  （复用 relevance.score，fastembed 语义向量已启用），并标记已导入的 DOI。
- 排序：带项目主题时按相关性降序；否则按 OpenAlex 相关分 → 被引量。
- 搜索池：per_page 默认拉到 OpenAlex 单页上限 200 条，返回后由前端在此池内
  本地分页（每页 50）。不走服务端 cursor 翻页——本地按相关性重排后，
  OpenAlex cursor 的"下一页"基于其自身相关序，翻页会与本地重排顺序错位
  （重复/漏项），故一次性取池再分页。

检索式构造（2026-09 新增，见 fetchers/query_builder.py）：
- 默认 SEARCH_QUERY_MODE=raw，用户输入原样透传，行为与引入前逐字一致。
- 设为 expanded 时按同义词表做 OR 扩展 + 短语引号；OpenAlex 会把未分隔的词
  按 AND 处理，所以扩展能显著改善召回（代价：search 请求按 $1/1000 计费）。
- 实际使用的检索式会写进日志并随响应返回（query_used），便于人工比对效果。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.content import relevance
from app.services.fetchers import query_builder
from app.services.fetchers.base import FetcherOrchestrator

logger = logging.getLogger(__name__)

# 透传给前端的字段白名单（NormalizedMeta 子集 + 标记字段）
_OUT_FIELDS = (
    "doi", "title", "journal", "publication_year", "publication_date", "authors",
    "abstract", "keywords", "cited_by_count", "citation_percentile",
    "field_of_study", "work_type", "openalex_work_id", "open_access",
)


def _log_plan(query: str, plan: query_builder.QueryPlan) -> None:
    """把实际检索式打进日志——调 SEARCH_QUERY_MODE 时靠它做人工比对。"""
    logger.info(
        "OpenAlex 检索 mode=%s variants=%d query=%r%s",
        plan.mode, len(plan.variants), query, f" note={plan.note}" if plan.note else "",
    )
    for i, v in enumerate(plan.variants, 1):
        logger.info("  检索式[%d/%d]: %s", i, len(plan.variants), v)


async def search_works(
    db: Session,
    orchestrator: FetcherOrchestrator,
    query: str,
    per_page: int = 200,
    year_from: int | None = None,
    year_to: int | None = None,
    work_type: str | None = None,
    project_id: str | None = None,
    query_mode: str | None = None,
) -> dict:
    """按关键词搜索候选论文。返回 {query, total, results, query_used, query_mode}。

    query_mode 缺省时用服务端 SEARCH_QUERY_MODE；前端会显式传 raw/expanded，
    以便用户在界面上按课题临时切换扩展开关。
    """
    from app.cache import cache

    settings = get_settings()
    effective_mode = query_mode or settings.search_query_mode
    plan = query_builder.build_query_plan(
        query,
        mode=effective_mode,
        extra_synonyms=settings.search_extra_synonyms,
        base_synonyms=relevance._SYNONYMS,
    )
    _log_plan(query, plan)

    # 缓存键带上 mode + 检索式指纹：切换 SEARCH_QUERY_MODE 或改同义词表后不会命中旧结果
    key = f"oasearch:{query}:{plan.mode}:{plan.digest}:{per_page}:{year_from}:{year_to}:{work_type}"
    raw = cache.get(key)
    if raw is None:
        openalex = orchestrator.sources["openalex"]
        kwargs = dict(
            per_page=per_page, year_from=year_from, year_to=year_to, work_type=work_type
        )
        if plan.is_multi:
            raw = await openalex.search_works_union(plan.variants, **kwargs)
        else:
            raw = await openalex.search_works(plan.variants[0] if plan.variants else query, **kwargs)
        cache.set(key, raw, ttl=3600)

    # 项目上下文：主题/关键词（相关性锚点） + 已导入 DOI 集合
    topic, keywords, imported = "", [], set()
    if project_id:
        from app.models.paper import Paper
        from app.models.project import Project

        project = db.get(Project, project_id)
        if project is not None:
            topic = project.research_topic or ""
            keywords = project.keywords or []
        imported = {
            d for (d,) in db.query(Paper.doi)
            .filter(Paper.project_id == project_id, Paper.doi.isnot(None)).all()
            if d
        }

    results = []
    for r in raw.get("results", []):
        row = {k: r.get(k) for k in _OUT_FIELDS}
        row["already_imported"] = bool(row.get("doi")) and row["doi"] in imported
        if project_id:
            # allow_load=False：请求路径绝不自己触发 fastembed 加载（首次加载可能是分钟级，
            # 会撞爆前端 120s 超时）。模型由启动时的 warm_up() 预热；未就绪则先按覆盖率打分。
            rel = relevance.score(
                topic, keywords, r.get("abstract"), r.get("title"), allow_load=False
            )
            row["relevance_score"] = rel["score"]
            row["relevance_method"] = rel["method"]
        else:
            row["relevance_score"] = None
            row["relevance_method"] = None
        row["openalex_score"] = r.get("openalex_score")
        results.append(row)

    # 排序：带项目主题时按相关性；否则按 OpenAlex 相关分 → 被引量
    if project_id and topic.strip():
        results.sort(key=lambda x: x["relevance_score"] or 0, reverse=True)
    else:
        results.sort(
            key=lambda x: (x["openalex_score"] or 0, x["cited_by_count"] or 0), reverse=True
        )

    # 多段并集（同义词扩展切块）时并集可能超过 per_page，排序后截断回原契约
    if len(results) > per_page:
        results = results[:per_page]

    return {
        "query": query,
        "query_used": list(plan.variants),
        "query_mode": plan.mode,
        "query_note": plan.note,
        "total": raw.get("total"),
        "total_estimated": bool(raw.get("total_estimated")),
        "results": results,
    }
