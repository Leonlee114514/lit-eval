"""Google Scholar 抓取存根：预留扩展位，MVP 不实现。

理由：GS 反爬严格且违反服务条款，可靠性低。后续如需实现，
建议用 SerpAPI/协作解析库，且只做"被引次数"单项补全。
"""
from __future__ import annotations

from app.services.fetchers.base import BaseFetcher, FetchResult


class GoogleScholarFetcher(BaseFetcher):
    name = "google_scholar"

    async def fetch(self, doi: str) -> FetchResult:
        return FetchResult(
            source=self.name,
            ok=False,
            error="NotImplemented: Google Scholar scraping deferred (ToS/anti-bot).",
        )
