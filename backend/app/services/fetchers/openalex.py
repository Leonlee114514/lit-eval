"""OpenAlex 抓取：引用/主题/期刊指标主源（数据最全）。"""
from __future__ import annotations

from app.services.fetchers.base import BaseFetcher, FetchResult

OPENALEX_API = "https://api.openalex.org/works/https://doi.org/"


class OpenAlexFetcher(BaseFetcher):
    name = "openalex"

    def __init__(self, mailto: str = "", api_key: str = "", timeout: float = 20.0):
        """api_key 走 Authorization 头（官方推荐：省 query 空间、不进 URL 日志）。"""
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        super().__init__(mailto=mailto, timeout=timeout, extra_headers=headers)
        self.api_key = api_key

    async def fetch(self, doi: str) -> FetchResult:
        try:
            raw = await self._get(
                OPENALEX_API + doi,
                params={"mailto": self.mailto, "select": self._SELECT},
            )
        except Exception as e:
            return FetchResult(source=self.name, ok=False, error=f"{type(e).__name__}: {e}")
        data = self._normalize(raw)
        data = await self._enrich_author_h_index(data)
        return FetchResult(source=self.name, ok=True, data=data)

    async def fetch_by_title(self, title: str) -> FetchResult:
        try:
            raw = await self._get(
                "https://api.openalex.org/works",
                params={
                    "search": title,
                    "per-page": 1,
                    "select": self._SELECT,
                    "mailto": self.mailto,
                },
            )
        except Exception as e:
            return FetchResult(source=self.name, ok=False, error=f"{type(e).__name__}: {e}")
        results = raw.get("results", [])
        if not results:
            return FetchResult(source=self.name, ok=False, error="no_match")
        data = self._normalize(results[0])
        data = await self._enrich_author_h_index(data)
        data["title_matched"] = results[0].get("display_name")
        return FetchResult(source=self.name, ok=True, data=data)

    async def search_works(
        self,
        query: str,
        per_page: int = 50,
        year_from: int | None = None,
        year_to: int | None = None,
        work_type: str | None = None,
    ) -> dict:
        """单条检索式搜索（主题发现用），复用 _SELECT/_normalize。

        列表阶段不做作者 h-index 补查（省请求）；用户真正导入后走三源抓取才补。
        每条结果额外透出 OpenAlex 自带的搜索相关分（openalex_score）。
        """
        return await self._search_once(
            query, per_page=per_page, year_from=year_from, year_to=year_to, work_type=work_type
        )

    async def search_works_union(
        self,
        queries: list[str] | tuple[str, ...],
        per_page: int = 50,
        year_from: int | None = None,
        year_to: int | None = None,
        work_type: str | None = None,
        gap_seconds: float = 0.15,
    ) -> dict:
        """多条检索式串行请求后取并集（同义词扩展被 URL 长度切成多段时用）。

        两条诚实的边界（对应 query_builder 的等价性说明）：

        1. ``total`` 是各段 ``meta.count`` 之和，**不是并集真实命中数**（会重复计数），
           故返回 ``total_estimated=True``。
        2. 官方等价性依赖"取回全部命中"，而 ``per-page`` 会给每段封顶；单段命中数
           超过 per_page 时，并集是"封顶后的并集"，非严格等价。

        另外：不同段的 ``relevance_score`` 各自相对本段查询，**跨段不可比**，
        所以这里统一置 None（前端本就不展示），让上层排序自然退化为按被引量。
        """
        import asyncio

        merged: dict[str, dict] = {}
        totals: list[int] = []
        for i, q in enumerate(queries):
            if i:
                await asyncio.sleep(gap_seconds)  # 串行 + 间隔，避免突发限流
            one = await self._search_once(
                q, per_page=per_page, year_from=year_from, year_to=year_to, work_type=work_type
            )
            totals.append(int(one.get("total") or 0))
            for item in one["results"]:
                key = item.get("openalex_work_id") or item.get("doi") or item.get("title")
                if not key or key in merged:
                    continue
                item["openalex_score"] = None  # 跨段不可比，见 docstring
                merged[key] = item

        results = sorted(merged.values(), key=lambda x: (x.get("cited_by_count") or 0), reverse=True)
        return {
            "total": sum(totals),
            "total_estimated": True,
            "variants": list(queries),
            "results": results,
        }

    async def _search_once(
        self,
        query: str,
        per_page: int = 50,
        year_from: int | None = None,
        year_to: int | None = None,
        work_type: str | None = None,
    ) -> dict:
        """单条 ``search=`` 请求（不含并集逻辑）。"""
        params: dict[str, str] = {
            "search": query,
            "per-page": str(max(1, min(200, per_page))),
            "select": self._SELECT,
            "mailto": self.mailto,
        }
        filters: list[str] = []
        if year_from or year_to:
            filters.append(f"publication_year:{year_from or ''}-{year_to or ''}")
        if work_type:
            filters.append(f"type:{work_type}")
        if filters:
            params["filter"] = ",".join(filters)

        raw = await self._get("https://api.openalex.org/works", params=params)
        results = []
        for item in raw.get("results", []):
            norm = self._normalize(item)
            norm["openalex_score"] = item.get("relevance_score")
            results.append(norm)
        return {"total": (raw.get("meta") or {}).get("count"), "results": results}

    async def _enrich_author_h_index(self, data: dict) -> dict:
        """补查作者 h-index：works 端点不带 author.summary_stats（实测恒 None）。

        按论文作者 author.id 批量查 /authors 端点拿 summary_stats.h_index，
        结果按作者 ID 缓存 7 天（authorh:<id>）。查询失败降级——h_index 留
        None，author.py 回退中性 50，不阻断整体抓取。
        """
        authors = data.get("authors") or []
        need = [a for a in authors if a.get("author_id") and a.get("h_index") is None]
        if not need:
            return data

        from app.cache import cache

        hmap: dict[str, float] = {}
        for a in need:
            v = cache.get(f"authorh:{a['author_id']}")
            if v is not None:
                hmap[a["author_id"]] = v

        missing = [a["author_id"] for a in need if a["author_id"] not in hmap]
        for i in range(0, len(missing), 50):
            chunk = missing[i : i + 50]
            try:
                raw = await self._get(
                    "https://api.openalex.org/authors",
                    params={
                        "filter": f"openalex_id:{'|'.join(chunk)}",
                        "per-page": len(chunk),
                        "select": "id,summary_stats",
                        "mailto": self.mailto,
                    },
                )
            except Exception:
                continue
            for r in raw.get("results", []):
                h = (r.get("summary_stats") or {}).get("h_index")
                if h is not None:
                    hmap[r["id"]] = float(h)
                    cache.set(f"authorh:{r['id']}", float(h))

        for a in authors:
            if a.get("author_id") and a["author_id"] in hmap:
                a["h_index"] = hmap[a["author_id"]]
        data["authors"] = authors
        return data

    _SELECT = ",".join(
        [
            "id", "doi", "display_name", "publication_year", "publication_date",
            "authorships", "primary_location", "type", "cited_by_count",
            "cited_by_percentile_year", "abstract_inverted_index",
            "primary_topic", "keywords", "open_access", "biblio",
            "is_retracted", "relevance_score",
        ]
    )

    def _field_id(self, topic: dict) -> str | None:
        """从 primary_topic.field 提取领域编号（学科半衰期映射用）。"""
        field = topic.get("field") or {}
        fid = field.get("id")
        if not fid:
            return None
        try:
            return str(int(fid.rstrip("/").split("/")[-1]))
        except (ValueError, AttributeError, IndexError):
            return None

    def _normalize(self, raw: dict) -> dict:
        _doi = raw.get("doi")
        if _doi:
            _doi = _doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
        primary_location = raw.get("primary_location") or {}
        source = primary_location.get("source") or {}
        topic = raw.get("primary_topic") or {}
        subfield = topic.get("subfield") or {}
        authorships = raw.get("authorships") or []
        return {
            "field_id": self._field_id(topic),
            "doi": _doi,
            "title": raw.get("display_name"),
            "journal": source.get("display_name"),
            "issn": _first(source.get("issn")),
            "openalex_source_id": source.get("id"),
            "openalex_work_id": raw.get("id"),
            "publication_year": raw.get("publication_year"),
            "publication_date": raw.get("publication_date"),
            "abstract": self._restore_abstract(raw.get("abstract_inverted_index")),
            "authors": [
                {
                    "author_id": (a.get("author") or {}).get("id"),
                    "name": a.get("author", {}).get("display_name"),
                    "affiliation": _first_inst(a.get("institutions")),
                    "orcid": a.get("author", {}).get("orcid"),
                    "is_corresponding": a.get("is_corresponding"),
                    "h_index": (a.get("author") or {}).get("summary_stats", {}).get("h_index"),
                }
                for a in authorships
            ],
            "keywords": [k.get("display_name") for k in raw.get("keywords", [])[:10]],
            "cited_by_count": raw.get("cited_by_count"),
            "citation_percentile": _percentile(raw.get("cited_by_percentile_year")),
            "field_of_study": subfield.get("display_name"),
            "subfield_id": subfield.get("id"),
            "journal_2yr_mean": (source.get("summary_stats") or {}).get("2yr_mean_citedness"),
            "work_type": raw.get("type"),  # article/review 等
            "open_access": raw.get("open_access"),
            "volume": _first(_biblio(raw).get("volume")),
            "issue": _first(_biblio(raw).get("issue")),
            "pages": _pages(_biblio(raw)),
            "is_retracted": bool(raw.get("is_retracted")),
        }

    @staticmethod
    def _restore_abstract(inverted: dict | None) -> str | None:
        """OpenAlex 的 abstract_inverted_index 还原为正文。"""
        if not inverted:
            return None
        positions: dict[int, str] = {}
        for word, idxs in inverted.items():
            for i in idxs:
                positions[i] = word
        if not positions:
            return None
        words = [positions[i] for i in sorted(positions)]
        return " ".join(words)


def _first(v) -> str | None:
    if isinstance(v, list):
        return v[0] if v else None
    return v or None


def _biblio(raw: dict) -> dict:
    return raw.get("biblio") or {}


def _pages(b: dict) -> str | None:
    """页码：first_page-last_page，缺一段则单页。"""
    first = _first(b.get("first_page"))
    last = _first(b.get("last_page"))
    if not first and not last:
        return None
    if first and last and first != last:
        return f"{first}-{last}"
    return first or last


def _first_inst(institutions: list | None) -> str | None:
    if not institutions:
        return None
    return institutions[0].get("display_name")


def _percentile(pct: dict | None) -> float | None:
    """cited_by_percentile_year 实际格式为 {"min":98,"max":100}。

    取 max 作为同年引用百分位（0-100）。
    """
    if not pct:
        return None
    val = pct.get("max")
    if val is None:
        val = pct.get("percentile")  # 兼容标量旧格式
    return float(val) if val is not None else None
