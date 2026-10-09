"""期刊计量补充：从 OpenAlex /sources 端点获取 2yr_mean_citedness。

works 响应中的 source.summary_stats 恒为 None，需单独查 /sources。
结果按 source_id 缓存 7 天（热点数据）。
"""
from __future__ import annotations

import logging

from app.cache import cache
from app.config import get_settings

logger = logging.getLogger(__name__)

SOURCE_API = "https://api.openalex.org/sources/"


def get_2yr_mean(source_id: str | None, mailto: str = "") -> float | None:
    """返回期刊的 2yr_mean_citedness（伪 IF）。查询失败返回 None，不抛异常。"""
    if not source_id:
        return None

    def _query() -> float | None:
        import httpx

        try:
            resp = httpx.get(
                SOURCE_API + source_id,
                params={"select": "id,summary_stats", "mailto": mailto},
                headers=get_settings().openalex_headers,  # 必须带 key，否则走无 key 额度易 429
                timeout=20,
            )
            resp.raise_for_status()
            stats = resp.json().get("summary_stats") or {}
            val = stats.get("2yr_mean_citedness")
            return float(val) if val is not None else None
        except Exception as e:
            logger.warning("期刊计量查询失败 %s: %s", source_id, e)
            return None

    return cache.get_or_set(f"journal2yr:{source_id}", _query, ttl=7 * 24 * 3600)
