"""引用脉络：被谁引用 + 它引用了谁。

- 被引（citing）：OpenAlex `filter=cites:{work_id}`，按被引降序取 top 8
- 引用（references）：取论文 referenced_works，批量查询标题/被引，按被引降序取 top 8
- 结果缓存 7 天（网络结果稳定）
"""
from __future__ import annotations

import logging

import httpx

from app.cache import cache
from app.config import get_settings
from app.models.paper import Paper

logger = logging.getLogger(__name__)

API = "https://api.openalex.org"
SELECT = "id,display_name,cited_by_count,publication_year"
BATCH = 50


async def get_citation_graph(
    paper: Paper, mailto: str = "", citing_limit: int = 8, reference_limit: int = 8
) -> dict | None:
    work_id = paper.openalex_work_id
    if not work_id:
        return None

    async def _load() -> dict:
        async with httpx.AsyncClient(
            timeout=30, follow_redirects=True, headers=get_settings().openalex_headers
        ) as client:
            citing = await _query_works(client, f"cites:{work_id}", mailto, per_page=citing_limit)
            references = await _query_references(client, work_id, mailto, limit=reference_limit)
        return {
            "root": {
                "id": paper.id,
                "title": paper.title,
                "cited_by_count": paper.cited_by_count,
                "publication_year": paper.publication_year,
            },
            "citing": citing,
            "references": references,
        }

    # 进程内缓存（异步闭包无法直接用 get_or_set，手动包一层）
    cache_key = f"cgraph:{work_id}:{citing_limit}:{reference_limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    result = await _load()
    cache.set(cache_key, result, ttl=7 * 24 * 3600)
    return result


async def _query_works(client: httpx.AsyncClient, filter_: str, mailto: str, per_page: int = 8) -> list[dict]:
    try:
        resp = await client.get(
            f"{API}/works",
            params={
                "filter": filter_,
                "sort": "cited_by_count:desc",
                "per-page": per_page,
                "select": SELECT,
                "mailto": mailto,
            },
        )
        resp.raise_for_status()
        return _nodes(resp.json().get("results", []))
    except httpx.HTTPError as e:
        logger.warning("引用查询失败 %s: %s", filter_, e)
        return []


async def _query_references(client: httpx.AsyncClient, work_id: str, mailto: str, limit: int = 8) -> list[dict]:
    try:
        resp = await client.get(
            f"{API}/works/{work_id}",
            params={"select": "referenced_works", "mailto": mailto},
        )
        resp.raise_for_status()
        ref_ids = resp.json().get("referenced_works", []) or []
    except httpx.HTTPError as e:
        logger.warning("取 references 失败 %s: %s", work_id, e)
        return []

    if not ref_ids:
        return []

    nodes: list[dict] = []
    for i in range(0, len(ref_ids), BATCH):
        chunk = ref_ids[i : i + BATCH]
        try:
            resp = await client.get(
                f"{API}/works",
                params={
                    "filter": f"openalex_id:{'|'.join(chunk)}",
                    "per-page": BATCH,
                    "select": SELECT,
                    "mailto": mailto,
                },
            )
            resp.raise_for_status()
            nodes.extend(_nodes(resp.json().get("results", [])))
        except httpx.HTTPError as e:
            logger.warning("批量查 references 失败: %s", e)

    nodes.sort(key=lambda n: n["cited_by_count"] or 0, reverse=True)
    return nodes[:limit]


def _nodes(results: list[dict]) -> list[dict]:
    return [
        {
            "id": w.get("id"),
            "title": w.get("display_name"),
            "cited_by_count": w.get("cited_by_count"),
            "publication_year": w.get("publication_year"),
        }
        for w in results
        if w.get("display_name")
    ]
