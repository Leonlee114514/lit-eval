"""CNKI 抓取存根：预留扩展位，MVP 不实现。

理由：CNKI 无公开 API、强反爬、需登录。后续如需覆盖中文学术文献，
建议走知网研学/第三方接口或人工录入。
"""
from __future__ import annotations

from app.services.fetchers.base import BaseFetcher, FetchResult


class CNKIFetcher(BaseFetcher):
    name = "cnki"

    async def fetch(self, doi: str) -> FetchResult:
        return FetchResult(
            source=self.name,
            ok=False,
            error="NotImplemented: CNKI scraping deferred (no public API / anti-bot).",
        )
