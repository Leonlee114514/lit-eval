"""Semantic Scholar 抓取：TLDR/文章类型。自引率数据源。

注意：S2 在 2025+ 移除了 `selfCitationCount` 字段（请求该字段整请求报错），
因此不在此请求它——自引率改为可选：若未来某源提供 self_citation_count，
orchestrator 合并后仍会落库，decision 层的自引告警逻辑自动生效。
"""
from __future__ import annotations

from app.services.fetchers.base import BaseFetcher, FetchResult

S2_API = "https://api.semanticscholar.org/graph/v1/paper/DOI:"


class SemanticScholarFetcher(BaseFetcher):
    name = "s2"

    async def fetch(self, doi: str) -> FetchResult:
        try:
            raw = await self._get(
                S2_API + doi,
                params={
                    "fields": "title,year,abstract,externalIds,tldr,publicationTypes,isOpenAccess,citationCount,authors",
                },
            )
        except Exception as e:
            return FetchResult(source=self.name, ok=False, error=f"{type(e).__name__}: {e}")
        data = {
            "title": raw.get("title"),
            "publication_year": raw.get("year"),
            "abstract": raw.get("abstract"),
            "cited_by_count": raw.get("citationCount"),
            "work_type": _review_type(raw.get("publicationTypes")),
            # 独立信号：OpenAlex 常把综述误标 article，合并时按信任顺序会覆盖 work_type；
            # 保留 s2_work_type 走非优先级合并，供 review_detection 升格为 review
            "s2_work_type": _review_type(raw.get("publicationTypes")),
            "tldr": (raw.get("tldr") or {}).get("text"),
            "authors": [
                {"name": a.get("name")} for a in raw.get("authors", []) if a.get("name")
            ],
        }
        return FetchResult(source=self.name, ok=True, data=data)


def _review_type(types: list | None) -> str | None:
    if not types:
        return None
    lower = [t.lower() for t in types]
    if "review" in lower:
        return "review"
    if "letter" in lower:
        return "letter"
    return None
